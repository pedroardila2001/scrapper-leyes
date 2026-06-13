"""SQLite database management for catalog and scrape log."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


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
    -- Resolved SUIN mapping
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

    def _init_schema(self) -> None:
        self.conn.executescript(SCHEMA_SQL)
        self.conn.commit()

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
    ) -> list[dict[str, Any]]:
        """Get catalog rows ready for scraping (resolved + pending scrape)."""
        sql = """SELECT * FROM catalog
                 WHERE resolve_status = 'resolved'
                   AND scrape_status = 'pending'
                   AND suin_id IS NOT NULL"""
        params: list[Any] = []
        if tipo:
            sql += " AND tipo = ?"
            params.append(tipo)
        sql += " ORDER BY anio DESC, numero"
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def get_unresolved_norms(
        self,
        tipo: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Get catalog rows needing suin_id resolution."""
        sql = "SELECT * FROM catalog WHERE resolve_status = 'pending'"
        params: list[Any] = []
        if tipo:
            sql += " AND tipo = ?"
            params.append(tipo)
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
    ) -> None:
        self.conn.execute(
            """UPDATE catalog
               SET suin_id = ?, resolve_status = ?, resolve_note = ?,
                   updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (suin_id, status, note, catalog_id),
        )
        self.conn.commit()

    def update_scrape_status(
        self, suin_id: str, status: str, suin_vigencia: str | None = None
    ) -> None:
        self.conn.execute(
            """UPDATE catalog
               SET scrape_status = ?, suin_vigencia = ?,
                   updated_at = CURRENT_TIMESTAMP
               WHERE suin_id = ?""",
            (status, suin_vigencia, suin_id),
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
