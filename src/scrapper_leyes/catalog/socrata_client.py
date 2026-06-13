"""Socrata API client for datos.gov.co catalog synchronization.

Generalized (F0) to seed the catalog from *any* Socrata dataset, not just the
legislation dataset. A ``SocrataCatalogSource`` bundles a dataset id, a field
map, fixed defaults (e.g. tipo/corte) and an optional per-row transform, so
adding a new catalog source (e.g. Corte Constitucional sentencias) is a few
lines of declaration instead of a new client.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn

from scrapper_leyes.config import Settings
from scrapper_leyes.models import build_canonical_id
from scrapper_leyes.storage.database import Database


# ═══════════════════════════════════════════════════════════════════════════
# Catalog source definition
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class SocrataCatalogSource:
    """A datos.gov.co dataset that seeds the catalog.

    Attributes:
        key: short registry name (e.g. "legislacion", "cc_sentencias").
        dataset_id: Socrata 4x4 id (e.g. "fiev-nid6").
        field_map: Socrata field name → our catalog column.
        source: value for the catalog ``source`` column.
        defaults: fixed column values applied to every row (e.g. tipo, corte).
        tipo_filterable: whether a ``tipo`` filter applies (legislation) or the
            dataset is single-typed (sentencias).
        row_transform: optional ``(cleaned, raw) -> cleaned`` hook to derive
            fields (numero/anio/external_id/source_url/canonical_id).
    """

    key: str
    dataset_id: str
    field_map: dict[str, str]
    source: str
    defaults: dict[str, Any] = field(default_factory=dict)
    tipo_filterable: bool = True
    row_transform: Callable[[dict[str, Any], dict[str, str]], dict[str, Any]] | None = None

    def endpoint(self, settings: Settings) -> str:
        return f"{settings.socrata_base_url}/{self.dataset_id}.json"

    def clean_row(self, raw: dict[str, str]) -> dict[str, Any]:
        """Map one Socrata record to our catalog schema (+ canonical_id)."""
        cleaned: dict[str, Any] = {}
        for socrata_key, our_key in self.field_map.items():
            val = raw.get(socrata_key)
            if val is None or val == "NULL" or str(val).strip() == "":
                cleaned[our_key] = None
            else:
                cleaned[our_key] = str(val).strip()

        for k, v in self.defaults.items():
            cleaned.setdefault(k, v)
        cleaned["source"] = self.source

        if self.row_transform:
            cleaned = self.row_transform(cleaned, raw)

        # Compute canonical_id if a transform didn't already.
        if not cleaned.get("canonical_id") and cleaned.get("numero"):
            try:
                cleaned["canonical_id"] = build_canonical_id(
                    cleaned.get("tipo", ""),
                    str(cleaned["numero"]),
                    str(cleaned.get("anio") or ""),
                    corte=cleaned.get("corte"),
                    sala=cleaned.get("_sala"),
                )
            except Exception:
                cleaned["canonical_id"] = None
        cleaned.pop("_sala", None)
        return cleaned


# ── Registered sources ──────────────────────────────────────────────────────

_LEGISLACION_FIELDS: dict[str, str] = {
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

_SALA_MAP = {
    "sala plena": "plena",
    "salas de revisión": "revision",
    "sala de revisión": "revision",
    "salas de revision": "revision",
    "sala de revision": "revision",
}


def _cc_sentencia_transform(cleaned: dict[str, Any], raw: dict[str, str]) -> dict[str, Any]:
    """Derive numero/anio/sala/external_id/source_url for Corte Const. sentencias.

    Socrata gives ``sentencia`` like 'T-012/92'; we split into numero='T-012'
    and take the year from ``fecha_sentencia`` (authoritative), then build the
    deterministic relatoría URL and canonical_id.
    """
    sent = (cleaned.pop("_sentencia", None) or "").strip()
    fecha = (cleaned.pop("_fecha", None) or "").strip()
    sala_raw = (cleaned.pop("_sala_raw", None) or "").strip().lower()
    subtipo = (cleaned.get("subtipo") or "").strip().upper()

    numero = sent.split("/")[0].strip() if sent else None
    anio = fecha[:4] if len(fecha) >= 4 and fecha[:4].isdigit() else None
    if not anio and "/" in sent:
        yy = sent.split("/")[-1].strip()[:2]
        if yy.isdigit():
            anio = ("19" if int(yy) >= 90 else "20") + yy

    # Sala: explicit mapping, else infer from sentencia type (C/SU plenary).
    sala = _SALA_MAP.get(sala_raw)
    if not sala:
        sala = "plena" if subtipo in {"C", "SU"} else "revision"

    cleaned["numero"] = numero
    cleaned["anio"] = anio
    cleaned["_sala"] = sala

    if numero and anio:
        year_short = anio[2:]
        cleaned["external_id"] = f"{numero}-{year_short}"
        cleaned["source_url"] = (
            f"https://www.corteconstitucional.gov.co/relatoria/{anio}/"
            f"{numero}-{year_short}.htm"
        )
    return cleaned


LEGISLACION = SocrataCatalogSource(
    key="legislacion",
    dataset_id="fiev-nid6",
    field_map=_LEGISLACION_FIELDS,
    source="suin",
    tipo_filterable=True,
)

CC_SENTENCIAS = SocrataCatalogSource(
    key="cc_sentencias",
    dataset_id="v2k4-2t8s",
    field_map={
        "sentencia": "_sentencia",
        "sentencia_tipo": "subtipo",
        "magistrado_a": "magistrado_ponente",
        "sala": "_sala_raw",
        "fecha_sentencia": "_fecha",
        "proceso": "materia",
    },
    source="corte_constitucional",
    defaults={"tipo": "SENTENCIA", "corte": "cc"},
    tipo_filterable=False,
    row_transform=_cc_sentencia_transform,
)

CATALOG_SOURCES: dict[str, SocrataCatalogSource] = {
    LEGISLACION.key: LEGISLACION,
    CC_SENTENCIAS.key: CC_SENTENCIAS,
}


# ═══════════════════════════════════════════════════════════════════════════
# Sync
# ═══════════════════════════════════════════════════════════════════════════


def sync_catalog(
    settings: Settings,
    db: Database,
    *,
    tipo: str | None = None,
    limit: int | None = None,
    catalog_source: SocrataCatalogSource = LEGISLACION,
) -> int:
    """Download a Socrata catalog dataset and upsert into SQLite.

    Args:
        tipo: Filter by norm type (only when the source is tipo-filterable).
        limit: Max total records to fetch (None = all).
        catalog_source: which registered dataset to sync (default: legislation).

    Returns:
        Total records fetched.
    """
    page_size = settings.socrata_page_size
    offset = 0
    total_fetched = 0

    base_params: dict[str, str] = {"$order": ":id DESC", "$limit": str(page_size)}
    if tipo and catalog_source.tipo_filterable:
        base_params["$where"] = f"tipo='{tipo}'"

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed} registros"),
    ) as progress:
        task = progress.add_task(
            f"Sincronizando catálogo ({catalog_source.key})...", total=None
        )

        with httpx.Client(headers=settings.socrata_headers, timeout=30.0) as client:
            while True:
                params = {**base_params, "$offset": str(offset)}
                resp = client.get(catalog_source.endpoint(settings), params=params)
                resp.raise_for_status()
                records = resp.json()
                if not records:
                    break

                cleaned = [catalog_source.clean_row(r) for r in records]
                db.upsert_catalog_seed(cleaned)

                batch_size = len(records)
                total_fetched += batch_size
                offset += batch_size
                progress.update(task, completed=total_fetched)

                if limit and total_fetched >= limit:
                    break
                if batch_size < page_size:
                    break

    return total_fetched


def fetch_catalog_count(
    settings: Settings,
    *,
    tipo: str | None = None,
    catalog_source: SocrataCatalogSource = LEGISLACION,
) -> int:
    """Query Socrata for total record count (without downloading all)."""
    params: dict[str, str] = {"$select": "count(*)"}
    if tipo and catalog_source.tipo_filterable:
        params["$where"] = f"tipo='{tipo}'"

    with httpx.Client(headers=settings.socrata_headers, timeout=30.0) as client:
        resp = client.get(catalog_source.endpoint(settings), params=params)
        resp.raise_for_status()
        data = resp.json()
        return int(data[0]["count"])
