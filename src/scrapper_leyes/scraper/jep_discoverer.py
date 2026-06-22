"""Discoverer para la JEP (Jurisdicción Especial para la Paz) vía Jurinfo.

Jurinfo (``jurinfo.jep.gov.co/normograma``) corre el motor "Avance Jurídico"
—el mismo que SUIN/DIAN— y expone la API ``Buscar.ashx`` declarada en
``configuracion.txt::direccionAPI``. Devuelve un JSON array con
``nombre/tipo/numero/year/link/entidad/epigrafe`` por documento; el texto vive
en ``compilacion/docs/<link>`` (HTML estático, 200/htm verificado).

Por qué NO usa el ``NormogramaDiscoverer`` genérico:
  - El campo ``tipo`` de Jurinfo es una **categoría orgánica** ("JEP - Salas de
    Justicia", "JEP - Tribunal para la Paz"), no el tipo documental → hay que
    derivar SENTENCIA/AUTO/RESOLUCION/ACUERDO/LEY del ``nombre``/``link``.
  - ``numero`` viene vacío para las providencias propias de la JEP (su "número"
    es un radicado tipo ``TP-SAR-001``, ``SRVR-009``), que se toma del ``link``.
  - El corpus de Jurinfo es **curado**: incluye espejos de control constitucional
    (Corte Constitucional/CSJ/CE) relevantes para la justicia transicional. Esos
    se **omiten** aquí (los aporta su propia fuente); esta fuente emite solo los
    documentos de **origen JEP** para no duplicar nodos del grafo.

La API tope ~3.000 ítems por consulta → se barren varias consultas semilla y se
dedup por ``link``. La relatoría completa (autos/sentencias por expediente) vive
además en la SPA ``relatoria.jep.gov.co`` (fase 2, fuera de este discoverer).
"""

from __future__ import annotations

import asyncio
import json as _json
import logging
import re
from datetime import date
from typing import Any, Iterator

import httpx

from scrapper_leyes.models import build_canonical_id
from scrapper_leyes.scraper.base import BaseDiscoverer, CatalogSeed

logger = logging.getLogger(__name__)

_API_URL = "https://jurinfo.jep.gov.co/normograma/buscador/Buscar.ashx"
_DOCS_BASE = "https://jurinfo.jep.gov.co/normograma/compilacion/docs/"

# Consultas semilla para barrer el corpus (la API topa ~3.000 ítems/consulta).
_SEED_QUERIES = ("paz", "jep", "amnistia", "victimas", "sala", "tribunal",
                 "auto", "sentencia", "resolucion", "acuerdo")

# Categoría orgánica (campo `tipo` de Jurinfo) → de qué entidad ES el documento.
# Solo emitimos los de origen JEP; los espejos de altas cortes se omiten.
_JEP_ORIGIN_HINTS = ("jep", "sala", "tribunal", "acuerdo", "resolucion",
                     "circular", "objecion", "documento", "comisión de género",
                     "comision de genero")
_MIRROR_HINTS = ("corte constitucional", "consejo de estado",
                 "corte suprema", "csj")


def _derive_tipo(nombre: str, link: str, categoria: str) -> str:
    """Deriva el tipo documental canónico del nombre/link (no de la categoría)."""
    blob = f"{nombre} {link}".lower()
    if blob.lstrip().startswith(("auto", "av ", "sv ", "svav")) or "_auto_" in blob or "auto_" in blob or " auto " in blob:
        # "AV/SV/SVAV …" son aclaraciones/salvamentos a un Auto.
        if "sentencia" in blob:
            return "SENTENCIA"
        return "AUTO"
    if "sentencia" in blob or "setencia" in blob:  # 'Setencia' = typo real en la fuente
        return "SENTENCIA"
    if "resoluci" in blob:
        return "RESOLUCION"
    if "acuerdo" in blob:
        return "ACUERDO"
    if blob.lstrip().startswith("ley") or "ley_" in blob:
        return "LEY"
    if "circular" in blob:
        return "CIRCULAR"
    if "objeci" in blob or blob.lstrip().startswith("obj"):
        return "OBJECION"
    if "documento" in blob or "doc_" in blob:
        return "DOCUMENTO"
    return "AUTO"  # la mayor masa de Jurinfo son autos de salas/tribunal


def _radicado_from_link(link: str) -> str:
    """Extrae un radicado legible del nombre de archivo (sin extensión)."""
    stem = re.sub(r"\.html?$", "", link, flags=re.IGNORECASE)
    # Quitar prefijos de aclaración/salvamento de voto y datos del magistrado.
    stem = re.sub(r"^(AV|SV|SVAV)_Dr[ae]?-[^_]+_", "", stem)
    stem = re.sub(r"^(Auto|Sentencia|Setencia|Resoluci[oó]n)_", "", stem, flags=re.IGNORECASE)
    return stem.strip("_-") or link


class JEPDiscoverer(BaseDiscoverer):
    """Descubre documentos de origen JEP desde la API Jurinfo."""

    SOURCE = "jep"

    def __init__(self, queries: tuple[str, ...] = _SEED_QUERIES,
                 incluir_espejos: bool = False):
        self.queries = queries
        self.incluir_espejos = incluir_espejos

    # ── parsing puro (testeable sin red) ─────────────────────────────────
    def _is_jep_origin(self, categoria: str, entidad: str) -> bool:
        blob = f"{categoria} {entidad}".lower()
        if any(h in blob for h in _MIRROR_HINTS):
            return False
        return any(h in blob for h in _JEP_ORIGIN_HINTS)

    def _seed_from_item(self, it: dict[str, Any]) -> CatalogSeed | None:
        link = (it.get("link") or "").strip()
        if not link:
            return None
        categoria = (it.get("tipo") or "").strip()
        entidad = (it.get("entidad") or "").strip()
        if not self.incluir_espejos and not self._is_jep_origin(categoria, entidad):
            return None
        nombre = (it.get("nombre") or "").strip()
        anio = (it.get("year") or "").strip() or None
        if anio and not (anio.isdigit() and 1990 <= int(anio) <= 2030):
            anio = None
        tipo = _derive_tipo(nombre, link, categoria)
        numero = (it.get("numero") or "").strip() or _radicado_from_link(link)
        corte = "jep" if tipo in ("SENTENCIA", "AUTO") else None
        cid = None
        if anio and numero:
            try:
                cid = build_canonical_id(tipo, numero, anio, corte=corte)
            except Exception:
                cid = None
        return CatalogSeed(
            tipo=tipo, numero=numero, anio=anio, source=self.SOURCE,
            corte=corte, canonical_id=cid, external_id=link,
            source_url=_DOCS_BASE + link,
            extra={"entidad": entidad, "categoria": categoria, "titulo": nombre},
        )

    def _seeds_from_payload(self, items: list[dict[str, Any]]) -> dict[str, CatalogSeed]:
        out: dict[str, CatalogSeed] = {}
        for it in items:
            seed = self._seed_from_item(it)
            if seed and seed.external_id not in out:
                out[seed.external_id] = seed
        return out

    # ── red ──────────────────────────────────────────────────────────────
    async def _crawl(self) -> dict[str, CatalogSeed]:
        found: dict[str, CatalogSeed] = {}
        async with httpx.AsyncClient(
            headers={"User-Agent": "ScrapperLeyes/1.0 (investigacion academica)"},
            timeout=180.0, follow_redirects=True, verify=False,
        ) as client:
            for q in self.queries:
                try:
                    r = await client.get(_API_URL, params={"texto": q})
                    if r.status_code != 200:
                        continue
                    items = _json.loads(r.text)
                    if not isinstance(items, list):
                        continue
                except Exception as e:  # respuesta de error del servidor, timeout, etc.
                    logger.warning("[jep] consulta '%s' falló: %s", q, e)
                    continue
                before = len(found)
                for ext_id, seed in self._seeds_from_payload(items).items():
                    found.setdefault(ext_id, seed)
                logger.info("[jep] '%s': +%d (%d acumulados)",
                            q, len(found) - before, len(found))
                await asyncio.sleep(1.0)  # cortesía con el servidor
        return found

    def discover(
        self,
        *,
        desde: date | None = None,
        hasta: date | None = None,
        filtro: dict[str, Any] | None = None,
    ) -> Iterator[CatalogSeed]:
        found = asyncio.run(self._crawl())
        logger.info("[jep] %d documentos de origen JEP descubiertos", len(found))
        for seed in found.values():
            if desde and seed.anio and seed.anio.isdigit() and int(seed.anio) < desde.year:
                continue
            if hasta and seed.anio and seed.anio.isdigit() and int(seed.anio) > hasta.year:
                continue
            yield seed
