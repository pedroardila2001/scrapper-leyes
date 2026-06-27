"""Discoverer para la Relatoría de la ANCP-CCE (Colombia Compra Eficiente).

Recon en vivo (2026-06-26): ``relatoria.colombiacompra.gov.co`` corre **WordPress**
y expone su REST API pública (``/wp-json/wp/v2/<post_type>``). Cuatro custom post
types relevantes para contratación estatal:

  - ``conceptos``     → 7.064  (doctrina CCE; el EJE de este discoverer)
  - ``providencias``  → 3.117  (providencias del Consejo de Estado relatadas)
  - ``normativa``     →   286  (leyes/decretos contractuales)
  - ``laudo``         →    52  (laudos arbitrales)

Cada item trae ``slug``/``title`` con el patrón ``<TIPO> <numero> de <año>``
(p.ej. concepto ``C-819 de 2026``, normativa ``Decreto 287 de 2026``,
``Ley 2306 de 2023``) y el **texto completo embebido** en ``content.rendered`` →
no hay que bajar PDF: el ``source_url`` (``link``) sirve, y el scraper genérico
de URL materializa el cuerpo.

Acceso 100 % httpx (sin WAF, sin JS): paginación estándar WordPress
``?per_page=100&page=N`` con cabeceras ``X-WP-Total`` / ``X-WP-TotalPages``.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date
from typing import Any, Iterator

import httpx

from scrapper_leyes.models import build_canonical_id
from scrapper_leyes.scraper.base import BaseDiscoverer, CatalogSeed

logger = logging.getLogger(__name__)

_BASE = "https://relatoria.colombiacompra.gov.co/wp-json/wp/v2"

# post_type → tipo canónico por defecto (conceptos/laudos; normativa se deriva del título).
_POSTTYPE_TIPO = {
    "conceptos": "CONCEPTO",
    "providencias": "SENTENCIA",
    "laudo": "LAUDO ARBITRAL",
    "normativa": None,  # se deriva del título (Ley/Decreto/…)
}

# Forma común: "<cabeza> de <año>".  cabeza = número solo ("C-819", radicado largo)
# o "<Tipo> <número>" para normativa ("Decreto 287", "Ley 2306").
_TITLE_RE = re.compile(r"^\s*(?P<head>.+?)\s+de\s+(?P<anio>\d{4})\b", re.IGNORECASE)
# Para normativa: separa el tipo (palabra) del número en la cabeza.
_NORMATIVA_HEAD_RE = re.compile(
    r"^(?P<tipo>[A-Za-zÁÉÍÓÚÑáéíóúñ\.]+)\s+(?P<numero>[0-9][0-9A-Za-z\-]*)$"
)

_NORMATIVA_TIPO = {
    "ley": "LEY", "decreto": "DECRETO", "resolucion": "RESOLUCION",
    "resolución": "RESOLUCION", "circular": "CIRCULAR", "acuerdo": "ACUERDO",
}


class CCEDiscoverer(BaseDiscoverer):
    """Enumera la Relatoría ANCP-CCE vía WordPress REST → CatalogSeeds.

    Args:
        post_types: subconjunto de ("conceptos", "providencias", "normativa", "laudo").
            Por defecto solo ``conceptos`` (el eje doctrina de contratación).
        max_per_type: tope de items por post_type (None = todos).
    """

    def __init__(
        self,
        post_types: tuple[str, ...] = ("conceptos",),
        max_per_type: int | None = None,
    ):
        unknown = set(post_types) - set(_POSTTYPE_TIPO)
        if unknown:
            raise ValueError(
                f"post_types no soportados: {unknown}. Opciones: {tuple(_POSTTYPE_TIPO)}"
            )
        self.post_types = post_types
        self.max_per_type = max_per_type

    def _seed_from_item(self, pt: str, it: dict[str, Any]) -> CatalogSeed | None:
        title = (it.get("title") or {}).get("rendered") or it.get("slug") or ""
        link = (it.get("link") or "").strip()
        if not link:
            return None
        m = _TITLE_RE.match(title)
        tipo = _POSTTYPE_TIPO.get(pt)
        numero: str | None = None
        anio: str | None = None
        if m:
            anio = m.group("anio")
            head = m.group("head").strip()
            hm = _NORMATIVA_HEAD_RE.match(head)
            if hm and (pt == "normativa" or hm.group("tipo").lower() in _NORMATIVA_TIPO):
                # "Decreto 287" / "Ley 2306" → tipo + número.
                tipo = _NORMATIVA_TIPO.get(hm.group("tipo").lower(), hm.group("tipo").upper())
                numero = hm.group("numero")
            else:
                # Conceptos ("C-819"), providencias (radicado largo): la cabeza ES el número.
                numero = head
        if not anio:
            # Fallback: año de publicación (campo date).
            d = (it.get("date") or "")[:4]
            anio = d if d.isdigit() else None
        cid = None
        if tipo and numero and anio:
            try:
                cid = build_canonical_id(tipo, numero, anio)
            except Exception:
                pass
        return CatalogSeed(
            tipo=tipo or "DOCUMENTO",
            numero=numero or (it.get("slug") or str(it.get("id") or "")),
            anio=anio,
            source="cce",
            source_url=link,
            external_id=str(it.get("id") or "") or None,
            canonical_id=cid,
            extra={
                "entidad": "COLOMBIA COMPRA EFICIENTE",
                "ambito": "Nacional",
                "post_type": pt,
                "titulo": title,
            },
        )

    async def _crawl(self) -> list[CatalogSeed]:
        seeds: list[CatalogSeed] = []
        async with httpx.AsyncClient(
            headers={"User-Agent": "ScrapperLeyes/1.0 (investigacion academica)"},
            timeout=60.0, follow_redirects=True, verify=False,
        ) as client:
            for pt in self.post_types:
                got = 0
                page = 1
                while True:
                    params = {
                        "per_page": 100, "page": page,
                        "_fields": "id,slug,title,date,link",
                    }
                    try:
                        r = await client.get(f"{_BASE}/{pt}", params=params)
                    except Exception as e:
                        logger.warning("[cce] %s page %d error: %s", pt, page, e)
                        break
                    if r.status_code != 200:
                        # WordPress devuelve 400 al pasar la última página.
                        break
                    items = r.json()
                    if not items:
                        break
                    for it in items:
                        seed = self._seed_from_item(pt, it)
                        if seed:
                            seeds.append(seed)
                            got += 1
                            if self.max_per_type and got >= self.max_per_type:
                                break
                    total_pages = int(r.headers.get("X-WP-TotalPages", "0") or 0)
                    logger.info("[cce] %s: %d/%d páginas, %d seeds", pt, page, total_pages, got)
                    if self.max_per_type and got >= self.max_per_type:
                        break
                    if total_pages and page >= total_pages:
                        break
                    page += 1
        logger.info("[cce] %d documentos descubiertos", len(seeds))
        return seeds

    def discover(
        self,
        *,
        desde: date | None = None,
        hasta: date | None = None,
        filtro: dict[str, Any] | None = None,
    ) -> Iterator[CatalogSeed]:
        for seed in asyncio.run(self._crawl()):
            if desde and seed.anio and seed.anio.isdigit() and int(seed.anio) < desde.year:
                continue
            if hasta and seed.anio and seed.anio.isdigit() and int(seed.anio) > hasta.year:
                continue
            yield seed
