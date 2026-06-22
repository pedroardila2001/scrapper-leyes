"""Discoverer para EVA — Gestor Normativo de la Función Pública (DAFP).

EVA sirve cada norma como **PDF generado por PHP por id**:
``https://www.funcionpublica.gov.co/eva/gestornormativo/norma_pdf.php?i=<ID>``
(200 / ``application/pdf``) y su vista HTML equivalente ``norma.php?i=<ID>``.
El **índice** de normas vive en ``normasfp.php`` (y variantes paginadas /
filtradas por año), donde cada entrada es un enlace ``norma.php?i=<ID>`` cuyo
texto visible trae tipo + número + año (p.ej. *"Ley 1437 de 2011"*).

Estrategia de descubrimiento (cortés, sin ingesta):
- Cosechar el/los índices ``normasfp.php`` → extraer los ``i=<ID>`` con su texto
  (tipo/numero/anio) vía :meth:`_parse_index` (puro, testeable offline).
- Cada item → ``CatalogSeed`` con ``source="funcion_publica"`` y ``source_url``
  apuntando al PDF ``norma_pdf.php?i=<ID>`` (la ruta de descarga de texto).

El número de norma EVA puede traer guiones de serie (``1234-2020``) o ceros a la
izquierda; se preservan tal cual aparecen. El año, cuando el índice lo expone,
habilita el filtro ``desde``/``hasta``.

Limitación: ``norma.php?i=<ID>`` NO es secuencial-limpio (hay huecos y mezcla de
normas/conceptos), por eso se cosecha el índice en vez de barrer IDs a ciegas.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date
from html import unescape
from typing import Any, Iterator
from urllib.parse import urljoin

import httpx

from scrapper_leyes.models import build_canonical_id
from scrapper_leyes.scraper.base import BaseDiscoverer, CatalogSeed

logger = logging.getLogger(__name__)

_PDF_URL = "https://www.funcionpublica.gov.co/eva/gestornormativo/norma_pdf.php?i={i}"
_HTML_URL = "https://www.funcionpublica.gov.co/eva/gestornormativo/norma.php?i={i}"
_BASE = "https://www.funcionpublica.gov.co/eva/gestornormativo/"

# Enlace a una norma individual: norma.php?i=<ID> (capturamos el ID y el bloque
# completo de la etiqueta <a ...>texto</a> para extraer el texto visible).
_NORMA_LINK_RE = re.compile(
    r'<a\b[^>]*\bhref=["\']?[^"\'>]*norma\.php\?i=(\d+)[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)

# Enlace a otro índice normasfp.php (paginación / filtros por año) para cosechar.
_INDEX_LINK_RE = re.compile(
    r'href=["\']?([^"\'> ]*normasfp\.php[^"\'> ]*)', re.IGNORECASE
)

# Texto visible de una norma: "Ley 1437 de 2011", "Decreto Único 1083 de 2015",
# "Resolución 0312 de 2019", "Concepto 12345 de 2020". Capturamos tipo (palabras
# iniciales sin dígitos), número (alfa-numérico con guiones) y año (4 dígitos).
_NORMA_TEXT_RE = re.compile(
    r"^\s*"
    r"(?P<tipo>[A-Za-zÁÉÍÓÚÑáéíóúñ./ ]+?)\s+"
    r"(?:N[°ºo.]*\s*)?"  # 'No.', 'N°', 'Nº' opcional antes del número
    r"(?P<numero>[0-9][0-9A-Za-z\-/]*)\s+"
    r"(?:de|del|of)\s+"
    r"(?P<anio>\d{4})",
    re.IGNORECASE,
)

# Normalización de tipo visible → tipo canónico (mayúsculas estilo SUIN).
_TIPO_MAP = {
    "ley": "LEY",
    "ley estatutaria": "LEY",
    "decreto": "DECRETO",
    "decreto unico": "DECRETO",
    "decreto único": "DECRETO",
    "decreto ley": "DECRETO",
    "decreto reglamentario": "DECRETO",
    "resolucion": "RESOLUCION",
    "resolución": "RESOLUCION",
    "circular": "CIRCULAR",
    "circular externa": "CIRCULAR EXTERNA",
    "concepto": "CONCEPTO",
    "acuerdo": "ACUERDO",
    "directiva": "CIRCULAR",
    "acto legislativo": "ACTO LEGISLATIVO",
    "constitucion politica": "CONSTITUCION POLITICA",
    "constitución política": "CONSTITUCION POLITICA",
}

_TAGS_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _clean_text(html_fragment: str) -> str:
    """Quita etiquetas internas, des-escapa entidades y colapsa espacios."""
    txt = _TAGS_RE.sub(" ", html_fragment)
    txt = unescape(txt)
    return _WS_RE.sub(" ", txt).strip()


class EVADiscoverer(BaseDiscoverer):
    """Cosecha el índice ``normasfp.php`` de EVA → CatalogSeeds (source=funcion_publica).

    Args:
        max_pages: tope de páginas índice a visitar (cortesía con .gov).
        follow_index_links: si True, sigue enlaces ``normasfp.php`` (paginación /
            años) cosechados del índice raíz, hasta ``max_pages``.
    """

    SOURCE = "funcion_publica"

    def __init__(self, max_pages: int = 12, follow_index_links: bool = True):
        self.max_pages = max_pages
        self.follow_index_links = follow_index_links

    # ── parsing puro (offline) ──────────────────────────────────────────
    def _seed_from_link(self, norma_id: str, link_html: str) -> CatalogSeed | None:
        """Un enlace ``norma.php?i=<ID>`` + su texto visible → CatalogSeed."""
        texto = _clean_text(link_html)
        if not texto:
            return None
        m = _NORMA_TEXT_RE.match(texto)
        tipo_raw = numero = anio = None
        if m:
            tipo_raw = m.group("tipo").strip().lower()
            tipo_raw = _WS_RE.sub(" ", tipo_raw)
            numero = m.group("numero").strip()
            anio = m.group("anio")
            if not (1810 <= int(anio) <= 2035):
                anio = None
        tipo = _TIPO_MAP.get(tipo_raw or "", (tipo_raw or "NORMA").upper())
        if numero is None:
            # No pudimos parsear número del texto → seed mínima (solo id+url),
            # útil igual para crawl-by-id posterior.
            tipo = "NORMA"
        cid = None
        if numero and anio:
            try:
                cid = build_canonical_id(tipo, numero, anio)
            except Exception:
                cid = None
        return CatalogSeed(
            tipo=tipo,
            numero=numero or "",
            anio=anio,
            source=self.SOURCE,
            canonical_id=cid,
            external_id=str(norma_id),
            source_url=_PDF_URL.format(i=norma_id),
            extra={
                "entidad": "FUNCION_PUBLICA",
                "vista_html": _HTML_URL.format(i=norma_id),
                "titulo": texto,
            },
        )

    def _parse_index(self, html: str) -> list[CatalogSeed]:
        """Extrae todos los items de una página índice ``normasfp.php``.

        Dedup por ID dentro de la misma página. Puro / testeable sin red.
        """
        seeds: dict[str, CatalogSeed] = {}
        for m in _NORMA_LINK_RE.finditer(html):
            norma_id, link_html = m.group(1), m.group(2)
            if norma_id in seeds:
                continue
            seed = self._seed_from_link(norma_id, link_html)
            if seed:
                seeds[norma_id] = seed
        return list(seeds.values())

    def _index_links(self, html: str, base: str) -> list[str]:
        """Enlaces a otras páginas ``normasfp.php`` (paginación / años)."""
        out: list[str] = []
        seen: set[str] = set()
        for m in _INDEX_LINK_RE.finditer(html):
            url = urljoin(base, unescape(m.group(1)))
            if url not in seen:
                seen.add(url)
                out.append(url)
        return out

    # ── red (async) ─────────────────────────────────────────────────────
    async def _crawl(self) -> dict[str, CatalogSeed]:
        found: dict[str, CatalogSeed] = {}
        queue: list[str] = [urljoin(_BASE, "normasfp.php")]
        seen_idx: set[str] = set(queue)
        pages = 0

        async with httpx.AsyncClient(
            headers={"User-Agent": "ScrapperLeyes/1.0 (investigacion academica)"},
            timeout=60.0,
            follow_redirects=True,
            verify=False,
        ) as client:
            while queue and pages < self.max_pages:
                url = queue.pop(0)
                pages += 1
                try:
                    r = await client.get(url)
                    if r.status_code != 200:
                        logger.debug("eva idx %s → %s", url, r.status_code)
                        continue
                    html = r.text
                except Exception as e:  # pragma: no cover - red
                    logger.warning("eva idx fail %s: %s", url, e)
                    continue
                for seed in self._parse_index(html):
                    key = seed.external_id or seed.source_url
                    if key and key not in found:
                        found[key] = seed
                if self.follow_index_links:
                    for nxt in self._index_links(html, url):
                        if nxt not in seen_idx and len(seen_idx) < self.max_pages * 4:
                            seen_idx.add(nxt)
                            queue.append(nxt)
                # cortesía con .gov: pausa breve entre páginas índice.
                await asyncio.sleep(0.5)

        logger.info(
            "[%s] %d páginas índice, %d normas descubiertas",
            self.SOURCE,
            pages,
            len(found),
        )
        return found

    # ── API pública ─────────────────────────────────────────────────────
    def discover(
        self,
        *,
        desde: date | None = None,
        hasta: date | None = None,
        filtro: dict[str, Any] | None = None,
    ) -> Iterator[CatalogSeed]:
        found = asyncio.run(self._crawl())
        for seed in found.values():
            if seed.anio and seed.anio.isdigit():
                yr = int(seed.anio)
                if desde and yr < desde.year:
                    continue
                if hasta and yr > hasta.year:
                    continue
            yield seed
