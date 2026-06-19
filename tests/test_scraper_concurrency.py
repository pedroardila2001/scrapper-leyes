"""Tests del acelerador de scraping: rate limiter seguro + batch concurrente.

No tocan la red: el limiter se prueba con el reloj real (intervalos chicos) y el
batch concurrente con un ``scrape_norm`` stub.
"""

from __future__ import annotations

import asyncio
import time

import pytest

pytest.importorskip("httpx")
pytest.importorskip("rich")

from scrapper_leyes.config import Settings
from scrapper_leyes.scraper.suin_scraper import SuinScraper


def _scraper(workers: int, rps: float) -> SuinScraper:
    return SuinScraper(Settings(), db=None, cache=None, max_concurrent=workers, rps=rps)


def test_rate_limiter_espacia_bajo_concurrencia():
    """10 llamadas concurrentes a _rate_limit deben quedar espaciadas ~1/rps."""
    s = _scraper(workers=10, rps=20)  # interval = 0.05s

    async def run():
        async def one():
            await s._rate_limit()
            return time.monotonic()

        times = sorted(await asyncio.gather(*[one() for _ in range(10)]))
        gaps = [b - a for a, b in zip(times, times[1:])]
        # Sin ráfagas: cada par consecutivo separado ~>= interval (con tolerancia).
        assert all(g >= 0.04 for g in gaps), gaps

    asyncio.run(run())


def test_scrape_batch_es_concurrente(monkeypatch):
    """El batch debe correr varias normas a la vez (no el viejo for secuencial)."""
    s = _scraper(workers=4, rps=1000)
    active = 0
    peak = 0

    async def fake_scrape_norm(client, norm):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.02)
        active -= 1
        return "done"

    monkeypatch.setattr(s, "scrape_norm", fake_scrape_norm)
    rows = [{"suin_id": str(i), "tipo": "LEY"} for i in range(12)]
    stats = asyncio.run(s.scrape_batch(rows))

    assert stats.get("done") == 12
    assert peak >= 2, f"corrió secuencial (peak={peak})"


def test_slow_down_amplia_intervalo():
    """Ante 429/503, el intervalo crece (ritmo más lento) pero acotado."""
    s = _scraper(workers=5, rps=4)  # interval base = 0.25s
    base = s._base_interval
    s._slow_down()
    assert s._rate_interval == pytest.approx(base * 2)
    for _ in range(10):
        s._slow_down()
    assert s._rate_interval <= base * 8 + 1e-9  # tope 8×
