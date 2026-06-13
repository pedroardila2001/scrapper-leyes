"""Tests for the F0 multi-source generalization (catalog schema + Socrata sources)."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from scrapper_leyes.storage.database import Database, source_for


# ── helpers ──────────────────────────────────────────────────────────────────


def _fresh_db() -> tuple[Database, Path]:
    tmp = Path(tempfile.mkdtemp()) / "catalog.db"
    return Database(tmp), tmp


def _legacy_db_without_new_columns() -> Path:
    """Create a pre-F0 catalog (old schema, no source/external_id/...) with rows."""
    tmp = Path(tempfile.mkdtemp()) / "legacy.db"
    conn = sqlite3.connect(str(tmp))
    conn.execute(
        """CREATE TABLE catalog (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tipo TEXT NOT NULL, numero TEXT NOT NULL, anio TEXT,
            sector TEXT, subtipo TEXT, vigencia TEXT, entidad TEXT, materia TEXT,
            articulos TEXT, corte TEXT, magistrado_ponente TEXT,
            suin_id TEXT, resolve_status TEXT DEFAULT 'pending', resolve_note TEXT,
            scrape_status TEXT DEFAULT 'pending', suin_vigencia TEXT, vigencia_match INTEGER,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    conn.executemany(
        "INSERT INTO catalog (tipo, numero, anio, suin_id, corte) VALUES (?,?,?,?,?)",
        [
            ("LEY", "1712", "2014", "1687091", None),
            ("DECRETO", "1494", "2015", "30019945", None),
            ("SENTENCIA", "C-274", "2013", None, "cc"),
            ("SENTENCIA", "SL-123", "2020", None, "csj"),
        ],
    )
    conn.commit()
    conn.close()
    return tmp


# ── source_for ───────────────────────────────────────────────────────────────


def test_source_for_maps_correctly():
    assert source_for("LEY", None) == "suin"
    assert source_for("DECRETO", None) == "suin"
    assert source_for("SENTENCIA", "cc") == "corte_constitucional"
    assert source_for("SENTENCIA", "csj") == "csj"
    assert source_for("SENTENCIA", "ce") == "consejo_estado"
    assert source_for("SENTENCIA", None) == "corte_constitucional"


# ── migration ────────────────────────────────────────────────────────────────


def test_migration_adds_columns_and_backfills_legacy_db():
    legacy = _legacy_db_without_new_columns()
    db = Database(legacy)  # opening triggers migration + backfill
    cols = {r[1] for r in db.conn.execute("PRAGMA table_info(catalog)")}
    assert {"source", "external_id", "source_url", "canonical_id"} <= cols

    rows = {r["numero"]: dict(r) for r in db.conn.execute("SELECT * FROM catalog")}
    assert rows["1712"]["source"] == "suin"
    assert rows["1712"]["canonical_id"] == "co:ley:1712:2014"
    assert rows["1712"]["external_id"] == "1687091"  # mirrored from suin_id
    assert rows["C-274"]["source"] == "corte_constitucional"
    assert rows["SL-123"]["source"] == "csj"
    db.close()


def test_migration_is_idempotent():
    legacy = _legacy_db_without_new_columns()
    Database(legacy).close()
    db = Database(legacy)  # second open must not duplicate or error
    assert db.get_catalog_count() == 4
    db.close()


# ── multi-source seeding & dedup ─────────────────────────────────────────────


def test_seed_dedups_sentencias_by_canonical_and_source():
    db, _ = _fresh_db()
    seed = {
        "tipo": "SENTENCIA",
        "numero": "T-012",
        "anio": "1992",
        "corte": "cc",
        "source": "corte_constitucional",
        "canonical_id": "co:sentencia:cc:revision:t-012:1992",
        "external_id": "T-012-92",
    }
    db.upsert_catalog_seed([seed])
    db.upsert_catalog_seed([dict(seed)])  # same again
    assert db.get_catalog_count(tipo="SENTENCIA") == 1
    db.close()


def test_seed_keeps_legislation_with_same_number_diff_entidad():
    db, _ = _fresh_db()
    base = {
        "tipo": "RESOLUCION",
        "numero": "1",
        "anio": "2020",
        "source": "suin",
        "canonical_id": "co:resolucion:1:2020",
    }
    db.upsert_catalog_seed([{**base, "entidad": "MinSalud"}])
    db.upsert_catalog_seed([{**base, "entidad": "MinTrabajo"}])
    # Same canonical_id but different entidad → both must survive (not collapsed).
    assert db.get_catalog_count(tipo="RESOLUCION") == 2
    db.close()


def test_get_pending_norms_uses_external_id_and_source_filter():
    db, _ = _fresh_db()
    db.upsert_catalog_seed([
        {"tipo": "LEY", "numero": "1", "anio": "2020", "source": "suin",
         "canonical_id": "co:ley:1:2020"},
    ])
    row = db.conn.execute("SELECT id FROM catalog").fetchone()
    db.update_resolve_status(row["id"], suin_id="999", status="resolved")
    pend = db.get_pending_norms(source="suin")
    assert len(pend) == 1 and pend[0]["external_id"] == "999"
    assert db.get_pending_norms(source="csj") == []
    db.close()


# ── Socrata CC transform (needs httpx; skipped in bare env) ───────────────────


def test_cc_sentencia_transform():
    try:
        from scrapper_leyes.catalog.socrata_client import CC_SENTENCIAS
    except ImportError:
        return  # httpx/rich not installed in this environment — skip
    raw = {
        "sentencia": "T-012/92",
        "sentencia_tipo": "T",
        "magistrado_a": "José Gregorio Hernández",
        "sala": "Salas de Revisión",
        "fecha_sentencia": "1992-02-25T00:00:00.000",
        "proceso": "Tutela",
    }
    row = CC_SENTENCIAS.clean_row(raw)
    assert row["tipo"] == "SENTENCIA"
    assert row["corte"] == "cc"
    assert row["source"] == "corte_constitucional"
    assert row["numero"] == "T-012"
    assert row["anio"] == "1992"
    assert row["subtipo"] == "T"
    assert row["external_id"] == "T-012-92"
    assert row["source_url"].endswith("/relatoria/1992/T-012-92.htm")
    assert row["canonical_id"] == "co:sentencia:cc:revision:t-012:1992"
    assert "_sala" not in row and "_sentencia" not in row


def test_legislacion_clean_row_maps_encoded_fields():
    try:
        from scrapper_leyes.catalog.socrata_client import LEGISLACION
    except ImportError:
        return
    raw = {"tipo": "LEY", "n_mero": "1712", "a_o": "2014", "entidad": "Congreso"}
    row = LEGISLACION.clean_row(raw)
    assert row["tipo"] == "LEY"
    assert row["numero"] == "1712"
    assert row["anio"] == "2014"
    assert row["source"] == "suin"
    assert row["canonical_id"] == "co:ley:1712:2014"
