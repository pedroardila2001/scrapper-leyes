"""Discoverer para conceptos institucionales del Ministerio del Trabajo.

Recon en vivo (2026-06-26): MinTrabajo **no** corre Normograma Avance Jurídico
(no hay ``normograma.mintrabajo.gov.co``). Su doctrina vive en el portal Liferay
``www.mintrabajo.gov.co`` como **PDFs estáticos** publicados en páginas de
contenido, accesibles 100 % por httpx (sin WAF, sin JS):

  - ``/web/guest/normatividad/conceptos-institucionales`` → ~140 conceptos
    (set curado de los más consultados; doctrina laboral).
  - ``/web/guest/normatividad/circulares`` y ``/circulares-generales`` (circulares).

Cada concepto es un ancla ``/documents/<g>/<f>/<RADICADO>+<descripcion>.pdf``. El
**número canónico es el radicado** (p.ej. ``11EE2020120300000005871``); el año va
codificado en el radicado (``\\d{2}[A-Z]{2}(20\\d{2})…``) → se extrae best-effort.
El texto se materializa bajando el PDF (docling) por ``source_url``.

LIMITACIÓN: la página lista solo el set publicado; el corpus completo de conceptos
del MinTrabajo (miles) no tiene buscador público enumerable → este discoverer
cosecha el set curado, que ya es doctrina laboral de alto valor.
"""

from __future__ import annotations

import asyncio
import html as _html
import logging
import re
from datetime import date
from typing import Any, Iterator
from urllib.parse import urljoin, unquote

import httpx

from scrapper_leyes.models import build_canonical_id
from scrapper_leyes.scraper.base import BaseDiscoverer, CatalogSeed

logger = logging.getLogger(__name__)

_BASE = "https://www.mintrabajo.gov.co"

_PAGES = {
    "conceptos": "/web/guest/normatividad/conceptos-institucionales",
    "circulares": "/web/guest/normatividad/circulares",
    "circulares_generales": "/web/guest/normatividad/circulares-generales",
}
_TIPO_PAGE = {
    "conceptos": "CONCEPTO",
    "circulares": "CIRCULAR",
    "circulares_generales": "CIRCULAR",
}

_PDF_RE = re.compile(r'href="(/documents/[^"]+?\.pdf)"', re.IGNORECASE)
# Radicado MinTrabajo: 2 dígitos + 2 letras + año (20\d{2}) + resto.
_RAD_RE = re.compile(r"^(\d{2}[A-Z]{2}(\d{4})\d+)", re.IGNORECASE)
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")


class MinTrabajoDiscoverer(BaseDiscoverer):
    """Cosecha los PDFs de conceptos/circulares del portal Liferay del MinTrabajo.

    Args:
        secciones: subconjunto de ("conceptos", "circulares", "circulares_generales").
    """

    def __init__(self, secciones: tuple[str, ...] = ("conceptos",)):
        unknown = set(secciones) - set(_PAGES)
        if unknown:
            raise ValueError(
                f"secciones no soportadas: {unknown}. Opciones: {tuple(_PAGES)}"
            )
        self.secciones = secciones

    def _seed_from_href(self, seccion: str, href: str) -> CatalogSeed | None:
        url = urljoin(_BASE, href)
        fname = unquote(href.rsplit("/", 1)[-1])  # RADICADO+descripcion.pdf
        fname = fname.replace("+", " ").strip()
        token = fname.split(" ", 1)[0]  # primer token = radicado (sin espacios)
        tipo = _TIPO_PAGE[seccion]
        m = _RAD_RE.match(token.replace(" ", ""))
        if m:
            numero = m.group(1)
            anio = m.group(2)
        else:
            numero = token or fname[:40]
            ym = _YEAR_RE.search(fname)
            anio = ym.group(0) if ym else None
        if anio and not (1900 <= int(anio) <= 2030):
            anio = None
        cid = None
        try:
            if anio:
                cid = build_canonical_id(tipo, numero, anio)
        except Exception:
            pass
        return CatalogSeed(
            tipo=tipo, numero=numero, anio=anio, source="mintrabajo",
            source_url=url, external_id=numero, canonical_id=cid,
            extra={
                "entidad": "MINISTERIO DEL TRABAJO", "ambito": "Nacional",
                "seccion": seccion, "titulo": fname[:-4].strip(),
            },
        )

    async def _crawl(self) -> list[CatalogSeed]:
        seeds: list[CatalogSeed] = []
        seen: set[str] = set()
        async with httpx.AsyncClient(
            headers={"User-Agent": "ScrapperLeyes/1.0 (investigacion academica)"},
            timeout=40.0, follow_redirects=True, verify=False,
        ) as client:
            for sec in self.secciones:
                try:
                    r = await client.get(_BASE + _PAGES[sec])
                except Exception as e:
                    logger.warning("[mintrabajo] %s error: %s", sec, e)
                    continue
                if r.status_code != 200:
                    logger.warning("[mintrabajo] %s HTTP %d", sec, r.status_code)
                    continue
                for m in _PDF_RE.finditer(_html.unescape(r.text)):
                    href = m.group(1)
                    if href in seen:
                        continue
                    seen.add(href)
                    seed = self._seed_from_href(sec, href)
                    if seed:
                        seeds.append(seed)
                logger.info("[mintrabajo] %s: %d acumulados", sec, len(seeds))
        logger.info("[mintrabajo] %d documentos descubiertos", len(seeds))
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
