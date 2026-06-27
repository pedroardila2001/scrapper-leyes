"""SQLite database management for catalog and scrape log."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from scrapper_leyes.models import build_canonical_id


SCHEMA_SQL = """
-- ── Catalog (from Socrata) ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS catalog (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo            TEXT NOT NULL,
    numero          TEXT NOT NULL,
    anio            TEXT,            -- may be NULL in the dataset
    sector          TEXT,
    subtipo         TEXT,
    vigencia        TEXT,            -- from Socrata snapshot
    entidad         TEXT,
    materia         TEXT,
    articulos       TEXT,
    corte           TEXT,            -- e.g. "cc", "csj", "ce"
    magistrado_ponente TEXT,
    -- Multi-source generalization (F0)
    source          TEXT,            -- 'suin' | 'corte_constitucional' | 'csj' | 'consejo_estado' | ...
    external_id     TEXT,            -- id in the source system (generalizes suin_id)
    source_url      TEXT,            -- canonical document URL in the source
    canonical_id    TEXT,            -- co:... dedup key across sources
    -- Resolved SUIN mapping (kept for backward compat; mirror of external_id for SUIN)
    suin_id         TEXT,
    resolve_status  TEXT NOT NULL DEFAULT 'pending',
        -- pending | resolved | ambiguous | not_found | error
    resolve_note    TEXT,            -- disambiguation info / error message
    -- Scrape orchestration
    scrape_status   TEXT NOT NULL DEFAULT 'pending',
        -- pending | done | error | skipped | needs_ocr
    -- Vigencia from SUIN page (may differ from Socrata)
    suin_vigencia   TEXT,            -- estado_documento from SUIN HTML
    vigencia_match  INTEGER,         -- 1=match, 0=discrepancy, NULL=not checked
    -- Timestamps
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_catalog_tipo ON catalog(tipo);
CREATE INDEX IF NOT EXISTS idx_catalog_tipo_num_anio ON catalog(tipo, numero, anio);
CREATE INDEX IF NOT EXISTS idx_catalog_scrape_status ON catalog(scrape_status);
CREATE INDEX IF NOT EXISTS idx_catalog_resolve_status ON catalog(resolve_status);
CREATE INDEX IF NOT EXISTS idx_catalog_suin_id ON catalog(suin_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_catalog_unique_norm
    ON catalog(tipo, numero, anio, entidad);

-- ── Scrape Log ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS scrape_log (
    suin_id         TEXT PRIMARY KEY,
    source          TEXT NOT NULL DEFAULT 'suin',
    source_url      TEXT NOT NULL,
    content_hash    TEXT NOT NULL,       -- SHA-256 of raw HTML
    capture_ts      DATETIME NOT NULL,
    http_status     INTEGER NOT NULL,
    raw_path        TEXT NOT NULL,       -- relative path to raw file
    parse_status    TEXT NOT NULL DEFAULT 'pending',
        -- pending | done | error | needs_ocr
    parse_error     TEXT,
    articles_count  INTEGER,
    modifications_count  INTEGER,
    jurisprudence_count  INTEGER,
    scraper_version TEXT NOT NULL DEFAULT '1.0.0',
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ── Unmapped Affectations (backlog, not error) ──────────────────────────
CREATE TABLE IF NOT EXISTS unmapped_affectations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    suin_id         TEXT NOT NULL,
    raw_type        TEXT NOT NULL,
    article_affected TEXT,
    source_text     TEXT,
    context         TEXT,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ── Vigencia Discrepancies ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS vigencia_discrepancies (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    suin_id         TEXT NOT NULL,
    catalog_vigencia TEXT,
    suin_vigencia   TEXT,
    detected_at     DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""


def source_for(tipo: str | None, corte: str | None) -> str:
    """Map a norm type + corte to its canonical data source.

    Legislation lives in SUIN; sentencias map to their issuing court.
    """
    if (tipo or "").upper() == "SENTENCIA":
        if corte == "csj":
            return "csj"
        if corte == "ce":
            return "consejo_estado"
        return "corte_constitucional"
    return "suin"


class Database:
    """SQLite database wrapper with schema auto-creation."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    # Columns added after the original schema; ALTERed in on existing DBs.
    _NEW_COLUMNS = (
        ("source", "TEXT"),
        ("external_id", "TEXT"),
        ("source_url", "TEXT"),
        ("canonical_id", "TEXT"),
        # Biblioteca / entity taxonomy
        ("rama", "TEXT"),
        ("cabeza", "TEXT"),
        ("entidad_norm", "TEXT"),
        # Retry: cuántas veces se intentó scrapear (cap para no reintentar para
        # siempre lo genuinamente roto, p.ej. 404).
        ("scrape_attempts", "INTEGER NOT NULL DEFAULT 0"),
    )

    # Indexes that depend on the new columns — created only after migration.
    _MIGRATION_INDEXES = """
    CREATE INDEX IF NOT EXISTS idx_catalog_source ON catalog(source);
    CREATE INDEX IF NOT EXISTS idx_catalog_canonical ON catalog(canonical_id);
    CREATE INDEX IF NOT EXISTS idx_catalog_rama ON catalog(rama);
    CREATE INDEX IF NOT EXISTS idx_catalog_entidad_norm ON catalog(entidad_norm);
    -- Dedup sentencias by (canonical_id, source). Restricted to SENTENCIA so it
    -- never collapses legislation rows where numero repeats across entidades
    -- (e.g. two Resolución 1 de 2020 from different entities).
    CREATE UNIQUE INDEX IF NOT EXISTS idx_catalog_sentencia_canon
        ON catalog(canonical_id, source)
        WHERE canonical_id IS NOT NULL AND tipo = 'SENTENCIA';
    """

    def _init_schema(self) -> None:
        self.conn.executescript(SCHEMA_SQL)
        self.conn.commit()
        self._migrate()

    def _migrate(self) -> None:
        """Idempotent, additive migration for the multi-source generalization."""
        existing = {r[1] for r in self.conn.execute("PRAGMA table_info(catalog)")}
        for name, decl in self._NEW_COLUMNS:
            if name not in existing:
                self.conn.execute(f"ALTER TABLE catalog ADD COLUMN {name} {decl}")
        self.conn.executescript(self._MIGRATION_INDEXES)
        self.conn.commit()
        self._backfill_source_columns()
        self._backfill_taxonomy()

    def _backfill_source_columns(self) -> None:
        """Populate source/external_id/canonical_id for pre-F0 rows."""
        rows = self.conn.execute(
            "SELECT id, tipo, numero, anio, suin_id, corte "
            "FROM catalog WHERE source IS NULL"
        ).fetchall()
        if not rows:
            return
        updates: list[tuple[str, str | None, str, int]] = []
        for r in rows:
            src = source_for(r["tipo"], r["corte"])
            try:
                cid = build_canonical_id(r["tipo"], str(r["numero"]), str(r["anio"] or ""))
            except Exception:
                cid = None
            updates.append((src, r["suin_id"], cid, r["id"]))
        self.conn.executemany(
            "UPDATE catalog SET source = ?, external_id = COALESCE(external_id, ?), "
            "canonical_id = COALESCE(canonical_id, ?) WHERE id = ?",
            updates,
        )
        self.conn.commit()

    def _backfill_taxonomy(self, force: bool = False) -> int:
        """Classify rows into rama/cabeza/entidad_norm for the biblioteca.

        One-time by default (only rows where rama IS NULL); ``force=True`` to
        reclassify everything after improving the taxonomy.
        """
        from scrapper_leyes.taxonomia import classify, entidad_key

        where = "" if force else " WHERE rama IS NULL"
        rows = self.conn.execute(
            f"SELECT id, tipo, sector, entidad, corte FROM catalog{where}"
        ).fetchall()
        if not rows:
            return 0
        updates: list[tuple[str, str, str, int]] = []
        for r in rows:
            rama, cabeza, _ = classify(r["tipo"], r["sector"], r["entidad"], r["corte"])
            updates.append((rama, cabeza, entidad_key(r["entidad"]), r["id"]))
        self.conn.executemany(
            "UPDATE catalog SET rama = ?, cabeza = ?, entidad_norm = ? WHERE id = ?",
            updates,
        )
        self.conn.commit()
        return len(updates)

    def reclassify_entities(self) -> int:
        """Force-recompute the biblioteca classification for all rows."""
        return self._backfill_taxonomy(force=True)

    def close(self) -> None:
        self.conn.close()

    # ── Catalog operations ──────────────────────────────────────────────

    def upsert_catalog_row(self, row: dict[str, Any]) -> None:
        """Insert or ignore a catalog row (dedup by tipo+numero+anio+entidad)."""
        self.conn.execute(
            """INSERT OR IGNORE INTO catalog
               (tipo, numero, anio, sector, subtipo, vigencia, entidad, materia, articulos, corte, magistrado_ponente)
               VALUES (:tipo, :numero, :anio, :sector, :subtipo, :vigencia,
                       :entidad, :materia, :articulos, :corte, :magistrado_ponente)""",
            row,
        )

    def upsert_catalog_batch(self, rows: list[dict[str, Any]]) -> int:
        """Batch insert catalog rows. Returns count of new rows."""
        cursor = self.conn.executemany(
            """INSERT OR IGNORE INTO catalog
               (tipo, numero, anio, sector, subtipo, vigencia, entidad, materia, articulos, corte, magistrado_ponente)
               VALUES (:tipo, :numero, :anio, :sector, :subtipo, :vigencia,
                       :entidad, :materia, :articulos, :corte, :magistrado_ponente)""",
            rows,
        )
        self.conn.commit()
        return cursor.rowcount

    # ── Multi-source seeding (F0) ───────────────────────────────────────

    _SEED_COLUMNS = (
        "tipo", "numero", "anio", "sector", "subtipo", "vigencia", "entidad",
        "materia", "articulos", "corte", "magistrado_ponente",
        "source", "external_id", "source_url", "canonical_id",
    )

    def upsert_catalog_seed(self, rows: list[dict[str, Any]]) -> int:
        """Insert catalog rows from any source (Socrata dataset or discoverer).

        Fills the multi-source columns and dedups via the existing unique
        indexes (legislation by tipo+numero+anio+entidad; sentencias by
        canonical_id+source). Returns count of newly inserted rows.
        """
        cols = ", ".join(self._SEED_COLUMNS)
        placeholders = ", ".join(f":{c}" for c in self._SEED_COLUMNS)
        normalized = [{c: row.get(c) for c in self._SEED_COLUMNS} for row in rows]
        cursor = self.conn.executemany(
            f"INSERT OR IGNORE INTO catalog ({cols}) VALUES ({placeholders})",
            normalized,
        )
        self.conn.commit()
        return cursor.rowcount

    def get_catalog_count(self, tipo: str | None = None) -> int:
        if tipo:
            row = self.conn.execute(
                "SELECT COUNT(*) FROM catalog WHERE tipo = ?", (tipo,)
            ).fetchone()
        else:
            row = self.conn.execute("SELECT COUNT(*) FROM catalog").fetchone()
        return row[0]

    def get_catalog_stats(self) -> list[dict[str, Any]]:
        """Return counts by tipo and scrape_status."""
        rows = self.conn.execute(
            """SELECT tipo, scrape_status, resolve_status, COUNT(*) as cnt
               FROM catalog GROUP BY tipo, scrape_status, resolve_status
               ORDER BY tipo, scrape_status"""
        ).fetchall()
        return [dict(r) for r in rows]

    def get_pending_norms(
        self,
        tipo: str | None = None,
        limit: int | None = None,
        source: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get catalog rows ready for scraping (resolved + pending scrape).

        Readiness requires a resolved external id (external_id or its SUIN
        mirror suin_id). Optionally scope to a single source.
        """
        sql = """SELECT * FROM catalog
                 WHERE resolve_status = 'resolved'
                   AND scrape_status = 'pending'
                   AND COALESCE(external_id, suin_id) IS NOT NULL"""
        params: list[Any] = []
        if tipo:
            sql += " AND tipo = ?"
            params.append(tipo)
        if source:
            sql += " AND source = ?"
            params.append(source)
        sql += " ORDER BY anio DESC, numero"
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def get_unresolved_norms(
        self,
        tipo: str | None = None,
        limit: int | None = None,
        source: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get catalog rows needing external-id resolution."""
        sql = "SELECT * FROM catalog WHERE resolve_status = 'pending'"
        params: list[Any] = []
        if tipo:
            sql += " AND tipo = ?"
            params.append(tipo)
        if source:
            sql += " AND source = ?"
            params.append(source)
        sql += " ORDER BY anio DESC, numero"
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def update_resolve_status(
        self,
        catalog_id: int,
        suin_id: str | None,
        status: str,
        note: str | None = None,
        *,
        external_id: str | None = None,
        source: str | None = None,
    ) -> None:
        """Mark a catalog row resolved.

        ``suin_id`` is kept for backward compatibility (SUIN indexer) and is
        mirrored into ``external_id``; new sources pass ``external_id``/``source``.
        """
        ext = external_id if external_id is not None else suin_id
        self.conn.execute(
            """UPDATE catalog
               SET suin_id = ?, external_id = COALESCE(?, external_id),
                   source = COALESCE(?, source),
                   resolve_status = ?, resolve_note = ?,
                   updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (suin_id, ext, source, status, note, catalog_id),
        )
        self.conn.commit()

    def update_scrape_status(
        self,
        suin_id: str,
        status: str,
        suin_vigencia: str | None = None,
        note: str | None = None,
    ) -> None:
        # Cada fallo incrementa scrape_attempts → permite reintentar con tope.
        # ``note`` registra la CAUSA del fallo (http_404, parse_error, empty_text…)
        # en resolve_note para que la taxonomía de errores sea consultable por SQL
        # sin re-probar las URLs. Solo se escribe si se provee.
        self.conn.execute(
            """UPDATE catalog
               SET scrape_status = ?, suin_vigencia = ?,
                   resolve_note = COALESCE(?, resolve_note),
                   scrape_attempts = scrape_attempts + (CASE WHEN ? = 'error' THEN 1 ELSE 0 END),
                   updated_at = CURRENT_TIMESTAMP
               WHERE suin_id = ?""",
            (status, suin_vigencia, note, status, suin_id),
        )
        # Check vigencia discrepancy
        if suin_vigencia:
            row = self.conn.execute(
                "SELECT vigencia FROM catalog WHERE suin_id = ?", (suin_id,)
            ).fetchone()
            if row and row["vigencia"]:
                cat_vig = row["vigencia"].strip().lower()
                suin_vig = suin_vigencia.strip().lower()
                match = 1 if cat_vig == suin_vig else 0
                self.conn.execute(
                    "UPDATE catalog SET vigencia_match = ? WHERE suin_id = ?",
                    (match, suin_id),
                )
                if not match:
                    self.conn.execute(
                        """INSERT INTO vigencia_discrepancies
                           (suin_id, catalog_vigencia, suin_vigencia)
                           VALUES (?, ?, ?)""",
                        (suin_id, row["vigencia"], suin_vigencia),
                    )
        self.conn.commit()

    def reset_errors_to_pending(
        self,
        *,
        tipo: str | None = None,
        source: str | None = None,
        max_attempts: int = 3,
    ) -> int:
        """Re-encola para scraping las filas en 'error' que aún no agotaron el
        tope de intentos. Devuelve cuántas se re-encolaron.

        Esto cierra el hueco de retry ENTRE corridas: ``get_pending_norms`` solo
        toma 'pending', así que sin esto los 'error' quedaban abandonados. El
        tope ``max_attempts`` evita reintentar para siempre lo genuinamente roto
        (404, normas sin texto)."""
        sql = """UPDATE catalog
                 SET scrape_status = 'pending', updated_at = CURRENT_TIMESTAMP
                 WHERE scrape_status = 'error' AND scrape_attempts < ?"""
        params: list[Any] = [max_attempts]
        if tipo:
            sql += " AND tipo = ?"
            params.append(tipo)
        if source:
            sql += " AND source = ?"
            params.append(source)
        cur = self.conn.execute(sql, params)
        self.conn.commit()
        return cur.rowcount

    # ── Scrape log ──────────────────────────────────────────────────────

    def insert_scrape_log(self, log: dict[str, Any]) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO scrape_log
               (suin_id, source, source_url, content_hash, capture_ts,
                http_status, raw_path, parse_status, parse_error,
                articles_count, modifications_count, jurisprudence_count,
                scraper_version)
               VALUES (:suin_id, :source, :source_url, :content_hash,
                       :capture_ts, :http_status, :raw_path, :parse_status,
                       :parse_error, :articles_count, :modifications_count,
                       :jurisprudence_count, :scraper_version)""",
            log,
        )
        self.conn.commit()

    def get_scrape_log(self, suin_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM scrape_log WHERE suin_id = ?", (suin_id,)
        ).fetchone()
        return dict(row) if row else None

    # ── Unmapped affectations ───────────────────────────────────────────

    def log_unmapped_affectation(
        self,
        suin_id: str,
        raw_type: str,
        article_affected: str | None = None,
        source_text: str | None = None,
        context: str | None = None,
    ) -> None:
        self.conn.execute(
            """INSERT INTO unmapped_affectations
               (suin_id, raw_type, article_affected, source_text, context)
               VALUES (?, ?, ?, ?, ?)""",
            (suin_id, raw_type, article_affected, source_text, context),
        )
        self.conn.commit()

    # ── Status / reporting ──────────────────────────────────────────────

    def get_resolve_stats(self, tipo: str | None = None) -> dict[str, int]:
        """Return counts by resolve_status."""
        sql = "SELECT resolve_status, COUNT(*) as cnt FROM catalog"
        params: list[Any] = []
        if tipo:
            sql += " WHERE tipo = ?"
            params.append(tipo)
        sql += " GROUP BY resolve_status"
        rows = self.conn.execute(sql, params).fetchall()
        return {r["resolve_status"]: r["cnt"] for r in rows}

    def get_scrape_stats(self, tipo: str | None = None) -> dict[str, int]:
        """Return counts by scrape_status."""
        sql = "SELECT scrape_status, COUNT(*) as cnt FROM catalog"
        params: list[Any] = []
        if tipo:
            sql += " WHERE tipo = ?"
            params.append(tipo)
        sql += " GROUP BY scrape_status"
        rows = self.conn.execute(sql, params).fetchall()
        return {r["scrape_status"]: r["cnt"] for r in rows}

    def get_unmapped_count(self) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM unmapped_affectations"
        ).fetchone()
        return row[0]

    def get_discrepancy_count(self) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM vigencia_discrepancies"
        ).fetchone()
        return row[0]
