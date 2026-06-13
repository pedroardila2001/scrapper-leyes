"""Socrata API client for datos.gov.co catalog synchronization."""

from __future__ import annotations

import httpx
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn

from scrapper_leyes.config import Settings
from scrapper_leyes.storage.database import Database


# Map Socrata field names (with encoding quirks) → clean names
_FIELD_MAP: dict[str, str] = {
    "tipo": "tipo",
    "n_mero": "numero",
    "a_o": "anio",
    "sector": "sector",
    "subtipo": "subtipo",
    "vigencia": "vigencia",
    "entidad": "entidad",
    "materia": "materia",
    "art_culos": "articulos",
}


def _clean_row(raw: dict[str, str]) -> dict[str, str | None]:
    """Normalize a Socrata record to our schema."""
    cleaned: dict[str, str | None] = {}
    for socrata_key, our_key in _FIELD_MAP.items():
        val = raw.get(socrata_key)
        if val is None or val == "NULL" or val.strip() == "":
            cleaned[our_key] = None
        else:
            cleaned[our_key] = val.strip()

    cleaned["corte"] = None
    cleaned["magistrado_ponente"] = None
    return cleaned


def sync_catalog(
    settings: Settings,
    db: Database,
    *,
    tipo: str | None = None,
    limit: int | None = None,
) -> int:
    """Download the full Socrata catalog and upsert into SQLite.

    Args:
        tipo: Filter by norm type (e.g. "LEY")
        limit: Max total records to fetch (None = all)

    Returns:
        Total records fetched.
    """
    page_size = settings.socrata_page_size
    offset = 0
    total_fetched = 0

    # Build base query params
    base_params: dict[str, str] = {
        "$order": ":id DESC",
        "$limit": str(page_size),
    }
    if tipo:
        base_params["$where"] = f"tipo='{tipo}'"

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed} registros"),
    ) as progress:
        task = progress.add_task("Sincronizando catálogo...", total=None)

        with httpx.Client(
            headers=settings.socrata_headers,
            timeout=30.0,
        ) as client:
            while True:
                params = {**base_params, "$offset": str(offset)}
                resp = client.get(settings.socrata_endpoint, params=params)
                resp.raise_for_status()
                records = resp.json()

                if not records:
                    break

                # Clean and insert
                cleaned = [_clean_row(r) for r in records]
                db.upsert_catalog_batch(cleaned)

                batch_size = len(records)
                total_fetched += batch_size
                offset += batch_size
                progress.update(task, completed=total_fetched)

                # Check limit
                if limit and total_fetched >= limit:
                    break

                # If we got fewer than page_size, we're done
                if batch_size < page_size:
                    break

    return total_fetched


def fetch_catalog_count(
    settings: Settings,
    *,
    tipo: str | None = None,
) -> int:
    """Query Socrata for total record count (without downloading all)."""
    params: dict[str, str] = {"$select": "count(*)"}
    if tipo:
        params["$where"] = f"tipo='{tipo}'"

    with httpx.Client(
        headers=settings.socrata_headers,
        timeout=30.0,
    ) as client:
        resp = client.get(settings.socrata_endpoint, params=params)
        resp.raise_for_status()
        data = resp.json()
        return int(data[0]["count"])
