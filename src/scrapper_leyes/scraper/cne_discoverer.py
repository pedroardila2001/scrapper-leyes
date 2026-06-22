"""Discoverer para el CNE — Consejo Nacional Electoral (Joomla).

El CNE publica sus resoluciones agrupadas por año en páginas Joomla tipo
``https://www.cne.gov.co/index.php/resoluciones-cne-<año>`` (verificado en vivo
2026-06-19; el ``/index.php/`` es obligatorio). Los PDFs viven en **SharePoint**
(``cnegovco-my.sharepoint.com/.../Documents/Attachments/Res. 06772 de 2024.pdf``)
y el texto del ancla suele ser genérico ("Documento") → el número y el año se
extraen del **nombre de archivo de la URL de SharePoint**, con respaldo al texto
del ancla cuando éste sí trae "Resolución No. X de AAAA".

Estrategia (cortés, sin ingesta):
- Para cada año en un rango fijo, GET la página del año (``/index.php/...``).
- :meth:`_parse_year_page` (puro, offline) cosecha cada enlace de documento →
  ``CatalogSeed(tipo="RESOLUCION", source="cne", corte="cne", ...)``.

Nota: los enlaces de SharePoint llevan un token efímero ``?e=...``; el discoverer
guarda la URL tal cual (el scraper resolverá la descarga). ``external_id`` =
``<numero>-<anio>`` para dedup estable.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date
from html import unescape
from typing import Any, Iterator
from urllib.parse import unquote, urljoin

import httpx

from scrapper_leyes.models import build_canonical_id
from scrapper_leyes.scraper.base import BaseDiscoverer, CatalogSeed

logger = logging.getLogger(__name__)

_BASE = "https://www.cne.gov.co/"
_YEAR_URL = "https://www.cne.gov.co/index.php/resoluciones-cne-{anio}"

# Año por defecto del barrido. FIJO a propósito: el código de test NO debe
# depender de datetime.now(). El llamador puede pasar otro rango por constructor.
DEFAULT_DESDE = 2010
DEFAULT_HASTA = 2026

# Enlace + texto visible. Capturamos href y el contenido interno del <a>.
_LINK_RE = re.compile(
    r'<a\b[^>]*\bhref=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)

# Texto de una resolución/concepto: "Resolución No. 1234 de 2023",
# "RESOLUCION 0456 DE 2023", "Concepto 12 de 2023".
_DOC_TEXT_RE = re.compile(
    r"(?P<tipo>Resoluci[oó]n|Concepto|Acuerdo)\s+"
    r"(?:N[°ºo.]*\s*)?"
    r"(?P<numero>[0-9][0-9A-Za-z\-/]*)\s+"
    r"(?:de|del)\s+"
    r"(?P<anio>\d{4})",
    re.IGNORECASE,
)

# Número/año desde el NOMBRE DE ARCHIVO de la URL de SharePoint, p.ej.
# ".../Attachments/Res. 06772 de 2024.pdf" o ".../RES 06623 DE 2024 1.pdf".
_URL_RES_RE = re.compile(
    r"(?:Res|Resoluci[oó]n)[.\s]*\s*(?P<numero>\d{2,6})\s+(?:de|del)\s+(?P<anio>20\d\d|19\d\d)",
    re.IGNORECASE,
)

_TIPO_MAP = {
    "resolucion": "RESOLUCION",
    "resolución": "RESOLUCION",
    "concepto": "CONCEPTO",
    "acuerdo": "ACUERDO",
}

_TAGS_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

# Extensiones de documento real (descarga directa de texto).
_DOC_EXT_RE = re.compile(r"\.(pdf|docx?|html?)(\?|#|$)", re.IGNORECASE)


def _clean_text(html_fragment: str) -> str:
    txt = _TAGS_RE.sub(" ", html_fragment)
    txt = unescape(txt)
    return _WS_RE.sub(" ", txt).strip()


class CNEDiscoverer(BaseDiscoverer):
    """Cosecha las páginas por año del CNE → CatalogSeeds (source=cne, corte=cne).

    Args:
        desde: primer año a barrer (inclusive). Default :data:`DEFAULT_DESDE`.
        hasta: último año a barrer (inclusive). Default :data:`DEFAULT_HASTA`.
    """

    SOURCE = "cne"
    CORTE = "cne"

    def __init__(self, desde: int = DEFAULT_DESDE, hasta: int = DEFAULT_HASTA):
        self.desde = desde
        self.hasta = hasta

    # ── parsing puro (offline) ──────────────────────────────────────────
    def _seed_from_link(
        self, href: str, link_html: str, anio: int, base: str
    ) -> CatalogSeed | None:
        url = urljoin(base, unescape(href))
        texto = _clean_text(link_html)
        url_dec = unquote(href)

        tipo = "RESOLUCION"
        numero = anio_doc = None
        # 1) Texto del ancla ("Resolución No. 1234 de 2023") cuando lo trae.
        mt = _DOC_TEXT_RE.search(texto)
        if mt:
            tipo = _TIPO_MAP.get(mt.group("tipo").lower(), "RESOLUCION")
            numero = mt.group("numero").strip()
            anio_doc = mt.group("anio")
        else:
            # 2) Nombre de archivo de la URL de SharePoint ("Res. 06772 de 2024").
            mu = _URL_RES_RE.search(url_dec)
            if not mu:
                return None
            numero = mu.group("numero").strip()
            anio_doc = mu.group("anio")

        if not (numero and anio_doc and 1990 <= int(anio_doc) <= 2035):
            return None

        cid = None
        try:
            if tipo == "RESOLUCION":
                cid = build_canonical_id(tipo, numero, anio_doc, corte="cne", sala="plena")
            else:
                cid = build_canonical_id(tipo, numero, anio_doc)
        except Exception:
            cid = None
        return CatalogSeed(
            tipo=tipo,
            numero=numero,
            anio=anio_doc,
            source=self.SOURCE,
            corte=self.CORTE,
            canonical_id=cid,
            external_id=f"{numero}-{anio_doc}",
            source_url=url,
            extra={
                "entidad": "CNE",
                "titulo": texto or f"Resolución {numero} de {anio_doc}",
                "anio_pagina": str(anio),
                "es_documento": bool(_DOC_EXT_RE.search(url_dec)) or "sharepoint" in url.lower(),
            },
        )

    def _parse_year_page(self, html: str, anio: int) -> list[CatalogSeed]:
        """Cosecha los enlaces a resoluciones/conceptos de una página de año.

        Dedup por (tipo, numero, anio) dentro de la página. Puro / sin red.
        """
        base = _YEAR_URL.format(anio=anio)
        seeds: dict[tuple[str, str, str], CatalogSeed] = {}
        for m in _LINK_RE.finditer(html):
            href, link_html = m.group(1), m.group(2)
            if href.strip().startswith("#") or href.strip().lower().startswith(
                "javascript:"
            ):
                continue
            seed = self._seed_from_link(href, link_html, anio, base)
            if not seed:
                continue
            key = (seed.tipo, seed.numero, seed.anio or "")
            if key not in seeds:
                seeds[key] = seed
        return list(seeds.values())

    # ── red (async) ─────────────────────────────────────────────────────
    async def _crawl(self, desde: int, hasta: int) -> dict[str, CatalogSeed]:
        found: dict[str, CatalogSeed] = {}
        async with httpx.AsyncClient(
            headers={"User-Agent": "ScrapperLeyes/1.0 (investigacion academica)"},
            timeout=60.0,
            follow_redirects=True,
            verify=False,
        ) as client:
            for anio in range(desde, hasta + 1):
                url = _YEAR_URL.format(anio=anio)
                try:
                    r = await client.get(url)
                    if r.status_code != 200:
                        logger.debug("cne año %s → %s", anio, r.status_code)
                        continue
                    html = r.text
                except Exception as e:  # pragma: no cover - red
                    logger.warning("cne año %s fail: %s", anio, e)
                    continue
                seeds = self._parse_year_page(html, anio)
                for seed in seeds:
                    key = seed.canonical_id or seed.source_url or f"{seed.tipo}:{seed.numero}:{seed.anio}"
                    if key not in found:
                        found[key] = seed
                logger.info("[cne] año %s: %d resoluciones", anio, len(seeds))
                await asyncio.sleep(0.7)  # cortesía con .gov
        logger.info("[%s] %d documentos descubiertos", self.SOURCE, len(found))
        return found

    # ── API pública ─────────────────────────────────────────────────────
    def discover(
        self,
        *,
        desde: date | None = None,
        hasta: date | None = None,
        filtro: dict[str, Any] | None = None,
    ) -> Iterator[CatalogSeed]:
        d = desde.year if desde else self.desde
        h = hasta.year if hasta else self.hasta
        found = asyncio.run(self._crawl(d, h))
        yield from found.values()
