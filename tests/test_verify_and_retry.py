"""Tests del retry entre corridas y de la lógica de veredicto de verify (sin red)."""

from __future__ import annotations

from pathlib import Path

import pytest

from scrapper_leyes.storage.database import Database
from scrapper_leyes.verify import FAIL, INFO, PASS, WARN, CheckResult, _worst


def _mk_db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "t.db")
    rows = [
        ("LEY", "1", "2020", "error", 0),
        ("LEY", "2", "2020", "error", 3),   # agotó intentos (tope 3)
        ("LEY", "3", "2020", "error", 5),   # por encima del tope
        ("LEY", "4", "2020", "done", 0),
        ("LEY", "5", "2020", "pending", 0),
    ]
    for tipo, numero, anio, st, att in rows:
        db.conn.execute(
            "INSERT INTO catalog (tipo, numero, anio, scrape_status, scrape_attempts) "
            "VALUES (?,?,?,?,?)", (tipo, numero, anio, st, att),
        )
    db.conn.commit()
    return db


def test_reset_errors_respeta_tope(tmp_path):
    db = _mk_db(tmp_path)
    # Tope 3 → solo la fila con attempts<3 (la #1) se re-encola.
    n = db.reset_errors_to_pending(max_attempts=3)
    assert n == 1
    pend = {r["numero"] for r in db.conn.execute(
        "SELECT numero FROM catalog WHERE scrape_status='pending'")}
    assert pend == {"1", "5"}  # la #1 re-encolada + la #5 que ya estaba pending
    # Las #2 y #3 siguen en error (agotaron intentos).
    err = {r["numero"] for r in db.conn.execute(
        "SELECT numero FROM catalog WHERE scrape_status='error'")}
    assert err == {"2", "3"}
    db.close()


def test_reset_tope_alto_reencola_todos(tmp_path):
    db = _mk_db(tmp_path)
    n = db.reset_errors_to_pending(max_attempts=99)
    assert n == 3  # las 3 en error
    db.close()


def test_update_status_incrementa_intentos_solo_en_error(tmp_path):
    db = _mk_db(tmp_path)
    db.conn.execute("UPDATE catalog SET suin_id='X' WHERE numero='5'")
    db.conn.commit()
    db.update_scrape_status("X", "error")
    db.update_scrape_status("X", "error")
    db.update_scrape_status("X", "done")  # no incrementa
    att = db.conn.execute(
        "SELECT scrape_attempts FROM catalog WHERE suin_id='X'").fetchone()[0]
    assert att == 2
    db.close()


def test_worst_verdict():
    assert _worst([PASS, PASS]) == PASS
    assert _worst([PASS, WARN]) == WARN
    assert _worst([WARN, FAIL]) == FAIL
    assert _worst([INFO, INFO]) == INFO
    assert _worst([INFO, PASS]) == PASS  # INFO no degrada si hay PASS
    assert _worst([INFO, WARN]) == WARN


def test_checkresult_shape():
    r = CheckResult("X", FAIL, "roto", {"a": 1})
    assert r.status == FAIL and r.details["a"] == 1
