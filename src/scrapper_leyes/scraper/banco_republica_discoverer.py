"""Discoverer para la compilación normativa del Banco de la República.

El Banco publica su reglamentación (resoluciones externas de la Junta Directiva,
circulares reglamentarias externas — DODM/DCIN/DCIP/DGD) en un índice **Drupal
Views** paginado:

    https://www.banrep.gov.co/es/reglamentacion-temas/2153?page=<N>   (page 0-based)

Cada documento es un ``<tr>`` dentro de ``<table class="cols-0">`` con dos
anclas:

  1. **Título** — ``<td class="views-field views-field-title"><b><a href="/es/..."
     hreflang="es">TEXTO</a></b>`` — el TEXTO trae tipo + número + año
     (p.ej. ``Resolución Externa No. 10 de 2014 del 26 de Septiembre de 2014 "..."``
     o ``Circular Reglamentaria Externa DODM-139``).
  2. **PDF** — ``<span class="file file--mime-application-pdf ..."><a
     href="/sites/default/files/reglamentacion/archivos/<archivo>.pdf" ...>`` — el
     nombre de archivo codifica el boletín (``bjd_<num>_<año>.pdf`` =
     Boletín de la Junta Directiva núm. <num> de <año>).

El número/año de la NORMA viven en el texto del título; el número/año del BOLETÍN
viven en el nombre del PDF. Cuando el texto no da número/año explícito, se cae al
boletín como respaldo.

Verificado en vivo 2026-06-19: índice 2153 HTTP 200, paginado ``?page=N`` (Drupal
Views, 0-based, con elipsis), última página real ≈ 55–65, ~900 documentos en el
tema. La página embebe un gadget que auto-redirige a terceros (Diario Oficial,
Alcaldía de Bogotá) y dispara descargas: hay que tomar el HTML crudo (GET) y
parsearlo, **sin** ejecutar el JS de la página.

Estado: andamiaje/parcial. El parser de índice está implementado contra el HTML
real capturado; la parte de red camina la paginación y loguea. Falta confirmar la
última página exacta (se acota con ``max_pages``) y el resto de ``temas`` (este
discoverer cubre el tema 2153 = cambiario/monetario por defecto).
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date
from html import unescape
from typing import Any, Iterator
from urllib.parse import urljoin, urlparse

import httpx

from scrapper_leyes.models import build_canonical_id
from scrapper_leyes.scraper.base import BaseDiscoverer, CatalogSeed

logger = logging.getLogger(__name__)

_UA = "ScrapperLeyes/1.0 (investigacion academica)"

# Una fila de resultado: capturamos la celda del título completa (con su ancla de
# título y, si está, el bloque de descarga del PDF) hasta el cierre del <td>.
_ROW_RE = re.compile(
    r'<td[^>]*class="[^"]*views-field-title[^"]*"[^>]*>(.*?)</td>',
    re.IGNORECASE | re.DOTALL,
)
# Ancla de título: primer <a href=...>TEXTO</a> dentro de la celda (lleva hreflang).
_TITLE_A_RE = re.compile(
    r'<a\s+href="([^"]+)"[^>]*hreflang="[^"]*"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
# Ancla del PDF de reglamentación.
_PDF_A_RE = re.compile(
    r'<a\s+href="([^"]*reglamentacion/archivos/[^"]+\.pdf)"',
    re.IGNORECASE,
)
# Tag stripper para quedarnos con el texto visible del título.
_TAG_RE = re.compile(r"<[^>]+>")

# ── Reconocimiento de tipo + número + año dentro del texto del título ──────────
# "Resolución Externa No. 10 de 2014" / "Resolución Externa 8 de 2000".
_RES_RE = re.compile(
    r"resoluci[oó]n\s+externa\s+(?:n[ouº°.\s]*)?(\d+)\s+de\s+(\d{4})",
    re.IGNORECASE,
)
# "Circular Reglamentaria Externa DODM-139" / "... DCIN-83" (dependencia-num).
_CIR_RE = re.compile(
    r"circular\s+reglamentaria\s+externa\s+([a-z]{2,5})[\s\-]*([0-9a-z\-]+)",
    re.IGNORECASE,
)
# Año "de 2015" / "del ... de 2015" en cualquier parte del texto (respaldo).
_YEAR_IN_TEXT_RE = re.compile(r"\bde\s+(\d{4})\b", re.IGNORECASE)
# Nombre de PDF de boletín: bjd_<num>_<año>.pdf (Boletín Junta Directiva).
_BOLETIN_FILE_RE = re.compile(r"bjd_([0-9a-z\-]+)_(\d{4})\.pdf$", re.IGNORECASE)


class BancoRepublicaDiscoverer(BaseDiscoverer):
    """Camina el índice Drupal Views de reglamentación del Banco → CatalogSeeds.

    Args:
        tema: id del tema de reglamentación (``2153`` = cambiario/monetario, el
            que lista resoluciones externas y circulares reglamentarias).
        max_pages: tope de páginas ``?page=N`` a recorrer (cortesía + el pager
            de Drupal clampa pero devuelve filas vacías al pasarse).
        concurrency: peticiones concurrentes.
    """

    SOURCE = "banco_republica"
    BASE = "https://www.banrep.gov.co"
    INDEX_PATH = "/es/reglamentacion-temas/{tema}"

    def __init__(self, tema: str = "2153", max_pages: int = 70, concurrency: int = 4):
        self.tema = str(tema)
        self.max_pages = max_pages
        self.concurrency = concurrency

    # ── parsing (puro, testeable offline) ─────────────────────────────────
    def _parse_index(self, html: str) -> list[CatalogSeed]:
        """Parsea una página del índice Views → lista de CatalogSeed.

        Método puro: recibe el HTML crudo de ``reglamentacion-temas/<tema>`` y
        devuelve un seed por fila con ancla de PDF reconocible.
        """
        seeds: list[CatalogSeed] = []
        for cell in _ROW_RE.findall(html):
            tm = _TITLE_A_RE.search(cell)
            pm = _PDF_A_RE.search(cell)
            if not pm:
                # Sin PDF de reglamentación no hay documento descargable; saltar.
                continue
            pdf_href = pm.group(1)
            pdf_url = urljoin(self.BASE + "/", pdf_href)
            title_href = tm.group(1) if tm else None
            title_text = unescape(_TAG_RE.sub(" ", tm.group(2))).strip() if tm else ""
            title_text = re.sub(r"\s+", " ", title_text)
            seed = self._seed_from_row(title_text, title_href, pdf_url)
            if seed:
                seeds.append(seed)
        return seeds

    def _seed_from_row(
        self, title_text: str, title_href: str | None, pdf_url: str
    ) -> CatalogSeed | None:
        tipo: str | None = None
        numero: str | None = None
        anio: str | None = None
        subtipo: str | None = None
        dependencia: str | None = None

        rm = _RES_RE.search(title_text)
        cm = _CIR_RE.search(title_text)
        if rm:
            tipo = "RESOLUCION"
            subtipo = "RESOLUCION EXTERNA"
            numero, anio = rm.group(1), rm.group(2)
        elif cm:
            tipo = "CIRCULAR"
            subtipo = "CIRCULAR REGLAMENTARIA EXTERNA"
            dependencia = cm.group(1).upper()
            numero = cm.group(2).upper()  # p.ej. "139", "83", "DODM-139" ya partido
            ym = _YEAR_IN_TEXT_RE.search(title_text)
            anio = ym.group(1) if ym else None
        else:
            # Tipo no reconocido en el texto; intentar derivar del boletín del PDF.
            pass

        # Respaldo desde el nombre del PDF de boletín (bjd_<num>_<año>.pdf).
        fname = pdf_url.rsplit("/", 1)[-1]
        bm = _BOLETIN_FILE_RE.search(fname)
        boletin_num = boletin_anio = None
        if bm:
            boletin_num = bm.group(1)
            boletin_anio = bm.group(2)

        if anio is None:
            anio = boletin_anio
        if tipo is None:
            # No pudimos clasificar por texto. Modelamos como BOLETIN para no perder
            # el documento (honesto: es el contenedor, no la norma individual).
            if boletin_num and boletin_anio:
                tipo = "CIRCULAR"
                subtipo = "BOLETIN JUNTA DIRECTIVA"
                numero = boletin_num
                anio = boletin_anio
            else:
                return None

        if not numero:
            numero = boletin_num
        if not numero or not anio:
            return None
        if not (anio.isdigit() and 1900 <= int(anio) <= 2030):
            return None

        cid = None
        try:
            cid = build_canonical_id(tipo, numero, anio)
        except Exception:
            pass

        extra: dict[str, Any] = {"entidad": "BANCO DE LA REPUBLICA"}
        if title_text:
            extra["titulo"] = title_text
        if dependencia:
            extra["dependencia"] = dependencia
        if boletin_num:
            extra["boletin"] = boletin_num
        if boletin_anio:
            extra["boletin_anio"] = boletin_anio

        return CatalogSeed(
            tipo=tipo,
            numero=numero,
            anio=anio,
            source=self.SOURCE,
            source_url=pdf_url,
            external_id=urljoin(self.BASE + "/", title_href) if title_href else None,
            canonical_id=cid,
            subtipo=subtipo,
            extra=extra,
        )

    # ── red (async) ───────────────────────────────────────────────────────
    async def _crawl(self) -> dict[str, CatalogSeed]:
        found: dict[str, CatalogSeed] = {}
        base_path = self.INDEX_PATH.format(tema=self.tema)
        sem = asyncio.Semaphore(self.concurrency)
        empty_streak = 0

        async with httpx.AsyncClient(
            headers={"User-Agent": _UA},
            timeout=40.0,
            follow_redirects=True,
            verify=False,
        ) as client:
            page = 0
            while page < self.max_pages:
                url = urljoin(self.BASE, base_path) + f"?page={page}"
                try:
                    async with sem:
                        r = await client.get(url)
                    if r.status_code != 200:
                        logger.debug("[banco_republica] page %d → HTTP %d", page, r.status_code)
                        break
                    page_seeds = self._parse_index(r.text)
                except Exception as e:  # noqa: BLE001
                    logger.warning("[banco_republica] page %d falló: %s", page, e)
                    break

                if not page_seeds:
                    empty_streak += 1
                    # Drupal devuelve la plantilla sin filas más allá del final.
                    if empty_streak >= 2:
                        break
                else:
                    empty_streak = 0
                    for s in page_seeds:
                        found.setdefault(s.source_url or s.canonical_id or repr(s), s)
                page += 1

        logger.info(
            "[banco_republica] tema %s: %d páginas, %d documentos descubiertos",
            self.tema, page, len(found),
        )
        return found

    def discover(
        self,
        *,
        desde: date | None = None,
        hasta: date | None = None,
        filtro: dict[str, Any] | None = None,
    ) -> Iterator[CatalogSeed]:
        found = asyncio.run(self._crawl())
        for seed in found.values():
            if desde and seed.anio and seed.anio.isdigit() and int(seed.anio) < desde.year:
                continue
            if hasta and seed.anio and seed.anio.isdigit() and int(seed.anio) > hasta.year:
                continue
            yield seed
