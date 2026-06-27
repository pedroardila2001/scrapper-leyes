"""Discoverer para normativa de Superintendencias (Financiera, SIC, …).

Cada Superintendencia tiene su propio buscador/normograma; este discoverer se
parametriza por entidad. Cubre dos por ahora:

  - **Superfinanciera** (``financiera``): portal JSF Oracle
    ``superfinanciera.gov.co/jsp/loader.jsf?lServicio=Publicaciones&...&id=<ID>``.
    Tipos: circulares externas, cartas circulares, resoluciones, conceptos.
  - **SIC** (``sic``): repositorio **Drupal** con facetas
    ``sic.gov.co/repositorio-de-normatividad?field_tipo_de_norma_value=<N>``
    (5 = circulares externas). Cada fila enlaza el documento/PDF.

Recon en vivo (2026-06-19) — caveats importantes:
  - **Superfinanciera está protegida por WAF** (fingerprint ``uzdbm`` /
    "Unauthorized Request Blocked"): un ``fetch``/httpx simple es bloqueado aunque la
    URL ``loader.jsf?...&id=`` responda 200 (devuelve la página de bloqueo). Requiere
    sesión de navegador real (cookies del challenge) → en httpx puro hay que sembrar
    cookies o usar Playwright. El parser (``_parse_financiera``) está listo para el
    HTML del listado de publicaciones; el acceso es el problema, no el parsing.
  - **SIC**: el listado Drupal no se capturó limpio en el recon mínimo (redirecciones
    del entorno), pero su estructura (tabla de resultados con enlaces a documento) es
    estándar Drupal Views → ``_parse_sic`` la parsea.

Estado: **ANDAMIAJE**. Ambos parsers (``_parse_financiera``, ``_parse_sic``) son
puros y testeados offline con el formato de cada portal. La red queda best-effort:
loguea y devuelve lo posible; Superfinanciera necesita resolver el WAF antes de
cosechar en volumen.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date
from typing import Any, Iterator
from urllib.parse import urljoin, urlparse

import httpx

from scrapper_leyes.models import build_canonical_id
from scrapper_leyes.scraper.base import BaseDiscoverer, CatalogSeed
from scrapper_leyes.scraper.normograma_discoverer import (
    _DOC_RE as _NG_DOC_RE,
    _DOCNAME_RE as _NG_DOCNAME_RE,
    _IDX_RE as _NG_IDX_RE,
    _TIPO_MAP as _NG_TIPO_MAP,
)

logger = logging.getLogger(__name__)

_FIN_BASE = "https://www.superfinanciera.gov.co"
_SIC_BASE = "https://www.sic.gov.co"
# SSPD (Superservicios) corre Normograma Avance Jurídico (mismo motor que SUIN/DIAN):
# documentos estáticos docs/<tipo>_superservicios_<num>_<año>.htm descubiertos por
# BFS sobre los índices cronológicos. Verificado en vivo 2026-06-26 (439 conceptos
# en el índice cronológico de conceptos).
_SSPD_BASE = "https://normograma.info/sspd2024/compilacion/"
_SSPD_SEEDS = [
    "c-dcsdspd_superintendencia_servicios_publicos_domiciliarios_conceptos_orden_cronologico.html",
    "c-dcsdspd_superintendencia_servicios_publicos_domiciliarios_resoluciones_orden_cronologico.html",
    "c-dcsdspd_superintendencia_servicios_publicos_domiciliarios_circulares_orden_cronologico.html",
]
# Facetas del repositorio SIC (field_tipo_de_norma_value): 5 = circulares externas.
_SIC_REPO = _SIC_BASE + "/repositorio-de-normatividad?field_tipo_de_norma_value=5"
_FIN_LOADER = (
    _FIN_BASE + "/jsp/loader.jsf?lServicio=Publicaciones&lTipo=publicaciones"
    "&lFuncion=loadContenidoPublicacion&id={id}"
)

# Texto "Circular Externa 029 de 2014" / "Carta Circular 12 de 2020" / "Concepto …".
_DOC_RE = re.compile(
    r"(?P<tipo>Circular\s+Externa|Carta\s+Circular|Concepto|Resoluci[oó]n)\s+"
    r"(?:N[°ºo.]*\s*)?(?P<numero>[0-9][0-9A-Za-z\-]*)\s+de\s+(?P<anio>\d{4})",
    re.IGNORECASE,
)

_TIPO_MAP = {
    "circular externa": "CIRCULAR EXTERNA",
    "carta circular": "CIRCULAR EXTERNA",
    "concepto": "CONCEPTO",
    "resolucion": "RESOLUCION",
    "resolución": "RESOLUCION",
}

_ENTIDAD_LABEL = {
    "financiera": "SUPERFINANCIERA", "sic": "SIC", "sspd": "SSPD",
}


def _norm_tipo(raw: str) -> str:
    key = re.sub(r"\s+", " ", raw.strip().lower())
    return _TIPO_MAP.get(key, key.upper())


class SuperintendenciasDiscoverer(BaseDiscoverer):
    """Descubre circulares/conceptos de Superintendencias → CatalogSeeds.

    Args:
        entidades: subconjunto de ("financiera", "sic").
    """

    def __init__(self, entidades: tuple[str, ...] = ("sspd", "sic", "financiera")):
        unknown = set(entidades) - set(_ENTIDAD_LABEL)
        if unknown:
            raise ValueError(
                f"Superintendencias no cubiertas: {unknown}. "
                f"Opciones: {tuple(_ENTIDAD_LABEL)}"
            )
        self.entidades = entidades

    # ── parsing puro: Superfinanciera ───────────────────────────────────────
    def _parse_financiera(self, html: str) -> list[CatalogSeed]:
        """Parsea un listado de publicaciones de Superfinanciera.

        Cada publicación enlaza ``loader.jsf?...&id=<ID>`` y su texto trae
        "Circular Externa <N> de <AÑO>". Empareja id↔texto por orden de aparición.
        """
        seeds: list[CatalogSeed] = []
        seen: set[str] = set()
        # Recorre cada <a> con su href y texto.
        for m in re.finditer(
            r'<a[^>]+href="([^"]*loadContenidoPublicacion[^"]*id=(\d+)[^"]*)"[^>]*>'
            r"(.*?)</a>",
            html,
            re.IGNORECASE | re.DOTALL,
        ):
            href, pub_id, text = m.group(1), m.group(2), m.group(3)
            dm = _DOC_RE.search(re.sub(r"<[^>]+>", " ", text))
            if not dm:
                continue
            if pub_id in seen:
                continue
            seen.add(pub_id)
            seeds.append(
                self._make_seed(
                    "financiera",
                    _norm_tipo(dm.group("tipo")),
                    dm.group("numero"),
                    dm.group("anio"),
                    source_url=_FIN_LOADER.format(id=pub_id),
                    external_id=pub_id,
                )
            )
        return seeds

    # ── parsing puro: SIC (Drupal) ──────────────────────────────────────────
    def _parse_sic(self, html: str) -> list[CatalogSeed]:
        """Parsea el repositorio Drupal de la SIC (filas con enlace a documento)."""
        seeds: list[CatalogSeed] = []
        seen: set[tuple[str, str]] = set()
        for m in re.finditer(
            r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
            html,
            re.IGNORECASE | re.DOTALL,
        ):
            href, text = m.group(1), m.group(2)
            clean = re.sub(r"<[^>]+>", " ", text)
            dm = _DOC_RE.search(clean)
            if not dm:
                continue
            tipo = _norm_tipo(dm.group("tipo"))
            numero, anio = dm.group("numero"), dm.group("anio")
            key = (numero, anio)
            if key in seen:
                continue
            seen.add(key)
            url = href if href.startswith("http") else _SIC_BASE + href
            seeds.append(
                self._make_seed("sic", tipo, numero, anio, source_url=url)
            )
        return seeds

    # ── SSPD (Superservicios): BFS Normograma estático ──────────────────────
    def _seed_from_sspd_doc(self, url: str) -> CatalogSeed | None:
        name = url.rsplit("/", 1)[-1]
        m = _NG_DOCNAME_RE.search(name)
        if not m:
            return None
        tipo_raw, _ent, numero, anio = m.group(1).lower(), m.group(2), m.group(3), m.group(4)
        if not (1900 <= int(anio) <= 2030):
            return None
        tipo = _NG_TIPO_MAP.get(tipo_raw, tipo_raw.upper().replace("_", " "))
        # external_id = slug de la URL (host+path con "/"→"_"), igual que el
        # NormogramaDiscoverer de creg/cra/dian; sin esto UrlIndexer marca
        # "error: sin external_id" y la fila nunca se baja (url_scraper.py:62).
        return self._make_seed(
            "sspd", tipo, numero, anio, source_url=url,
            external_id=url.split("://", 1)[-1].replace("/", "_"),
        )

    async def _crawl_sspd(self, client: httpx.AsyncClient, max_pages: int = 60) -> list[CatalogSeed]:
        """BFS acotado sobre el Normograma de la SSPD (mismo motor que SUIN/DIAN)."""
        base_host = urlparse(_SSPD_BASE).netloc
        seen_idx: set[str] = set()
        found: dict[str, CatalogSeed] = {}
        queue: list[str] = []
        for s in _SSPD_SEEDS:
            u = urljoin(_SSPD_BASE, s)
            seen_idx.add(u)
            queue.append(u)
        pages = 0
        while queue and pages < max_pages:
            url = queue.pop(0)
            pages += 1
            try:
                r = await client.get(url)
                if r.status_code != 200:
                    continue
                html = r.text
            except Exception as e:
                logger.debug("[superintendencias] SSPD idx fail %s: %s", url, e)
                continue
            for m in _NG_DOC_RE.finditer(html):
                doc = urljoin(url, m.group(1))
                if doc in found:
                    continue
                seed = self._seed_from_sspd_doc(doc)
                if seed:
                    found[doc] = seed
            for m in _NG_IDX_RE.finditer(html):
                nxt = urljoin(url, m.group(1))
                if (
                    nxt not in seen_idx
                    and urlparse(nxt).netloc == base_host
                    and nxt.startswith(_SSPD_BASE)
                    and "docs/" not in nxt
                ):
                    seen_idx.add(nxt)
                    queue.append(nxt)
        logger.info(
            "[superintendencias] SSPD: %d índices, %d documentos", pages, len(found)
        )
        return list(found.values())

    # ── helper ───────────────────────────────────────────────────────────────
    def _make_seed(
        self,
        entidad: str,
        tipo: str,
        numero: str,
        anio: str,
        *,
        source_url: str,
        external_id: str | None = None,
    ) -> CatalogSeed:
        cid = None
        try:
            cid = build_canonical_id(tipo, numero, anio)
        except Exception:
            pass
        return CatalogSeed(
            tipo=tipo,
            numero=numero,
            anio=anio,
            source="superintendencias",
            source_url=source_url,
            external_id=external_id,
            canonical_id=cid,
            extra={"entidad": _ENTIDAD_LABEL[entidad], "ambito": "Nacional"},
        )

    # ── red (best-effort; WAF en Superfinanciera) ───────────────────────────
    async def _crawl(self) -> list[CatalogSeed]:
        found: list[CatalogSeed] = []
        async with httpx.AsyncClient(
            headers={"User-Agent": "ScrapperLeyes/1.0 (investigacion academica)"},
            timeout=40.0,
            follow_redirects=True,
            verify=False,
        ) as client:
            if "sspd" in self.entidades:
                try:
                    found.extend(await self._crawl_sspd(client))
                except Exception as e:
                    logger.warning("[superintendencias] SSPD no accesible: %s", e)
            if "sic" in self.entidades:
                try:
                    r = await client.get(_SIC_REPO)
                    if r.status_code == 200:
                        found.extend(self._parse_sic(r.text))
                except Exception as e:
                    logger.warning("[superintendencias] SIC no accesible: %s", e)
            if "financiera" in self.entidades:
                try:
                    r = await client.get(_FIN_BASE + "/inicio/normativa")
                    if r.status_code == 200 and "Unauthorized Request" not in r.text:
                        found.extend(self._parse_financiera(r.text))
                    else:
                        logger.warning(
                            "[superintendencias] Superfinanciera bloqueada por WAF "
                            "(uzdbm); requiere sesión de navegador / cookies del challenge."
                        )
                except Exception as e:
                    logger.warning("[superintendencias] Financiera no accesible: %s", e)
        logger.info("[superintendencias] %d documentos descubiertos", len(found))
        return found

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
