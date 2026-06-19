"""Discoverer genérico para normogramas del motor "Avance Jurídico / Alejandría".

DIAN, CREG, CRC, CRA (y JEP-Jurinfo, Cancillería…) corren el mismo motor que SUIN:
documentos como HTML estático en ``.../docs/<tipo>_<entidad>_<num>_<año>.htm``,
descubiertos navegando un árbol de páginas índice ``.html``.

Este discoverer hace un **BFS acotado** sobre ese árbol: parte de unas páginas
semilla, sigue los enlaces a otros índices ``.html`` dentro del mismo gestor, y
cosecha los enlaces a documentos ``docs/*.htm``, parseando tipo/número/año del
nombre de archivo → ``CatalogSeed``. Un solo crawler cubre las 4 comisiones/DIAN.

Limitación: algunas instancias sirven su índice raíz por JS (p.ej. el cronológico
de CREG) → ahí el BFS cosecha menos; se compensa con páginas semilla estáticas.
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

logger = logging.getLogger(__name__)

# Enlaces a documentos y a sub-índices dentro de un gestor Normograma.
_DOC_RE = re.compile(r'href=["\']?([^"\' >]*docs/[a-z0-9_.\-]+\.html?)', re.IGNORECASE)
_IDX_RE = re.compile(r'href=["\']?([a-z0-9_.\-]+\.html?)["\' >]', re.IGNORECASE)
# Nombre de documento: <tipo>_<entidad>_<numero>_<año>.htm  (num puede ser 501-64)
_DOCNAME_RE = re.compile(
    r"([a-z_]+)_([a-z]+)_([0-9a-z\-]+)_(\d{4})\.html?$", re.IGNORECASE
)

# tipo de archivo Normograma → tipo canónico (mayúsculas estilo SUIN).
_TIPO_MAP = {
    "resolucion": "RESOLUCION", "oficio": "CONCEPTO", "concepto": "CONCEPTO",
    "concepto_tributario": "CONCEPTO", "circular": "CIRCULAR", "decreto": "DECRETO",
    "memorando": "CIRCULAR", "decision": "DECISION CAN",
}

# tipo plural que devuelve la API Buscar.ashx → tipo canónico.
_API_TIPO_MAP = {
    "resoluciones": "RESOLUCION", "oficios": "CONCEPTO", "conceptos": "CONCEPTO",
    "circulares": "CIRCULAR", "decretos": "DECRETO", "leyes": "LEY",
    "memorandos": "CIRCULAR", "sentencias": "SENTENCIA", "autos": "AUTO",
    "decisiones": "DECISION CAN", "directivas": "CIRCULAR", "acuerdos": "ACUERDO",
    "codigos": "CODIGO", "estatutos": "CODIGO", "constitucion": "CONSTITUCION",
}


class NormogramaConfig:
    """Config por fuente: base del gestor + semillas + prefijo de entidad.

    Si la instancia expone la API de búsqueda de Avance Jurídico (``Buscar.ashx``,
    declarada en ``configuracion.txt::direccionAPI``), se usa esa vía (metadatos
    completos, una sola consulta) en vez del BFS — necesario donde el índice es
    JS (DIAN).
    """

    def __init__(
        self, source: str, base: str, seeds: list[str], entidad: str,
        api_url: str | None = None, api_queries: tuple[str, ...] = ("de",),
        docs_base: str | None = None,
    ):
        self.source = source
        self.base = base.rstrip("/") + "/"
        self.seeds = seeds
        self.entidad = entidad  # prefijo esperado en el nombre de archivo (creg, cra…)
        self.api_url = api_url
        self.api_queries = api_queries
        self.docs_base = (docs_base or self.base + "docs/").rstrip("/") + "/"


NORMOGRAMA_SOURCES: dict[str, NormogramaConfig] = {
    "cra": NormogramaConfig(
        "cra", "https://normas.cra.gov.co/gestor/",
        ["m0_todas_resoluciones_por_orden_cronologico.html",
         "resoluciones_expedidas_por_cra.html",
         "m1nelreplcr_comision_regulacion_agua_potable_saneamiento_basico_cra.html",
         "m1_novedades_regulacion_expedida_por_cra.html"],
        "cra",
    ),
    "dian": NormogramaConfig(
        "dian", "https://normograma.dian.gov.co/dian/compilacion/",
        [],  # índice JS → BFS no aplica; se descubre por la API Buscar.ashx.
        "dian",
        api_url="https://normograma.info/prueba-dian/buscador/Buscar.ashx",
        api_queries=("de", "y"),
        docs_base="https://normograma.dian.gov.co/dian/compilacion/docs/",
    ),
    "creg": NormogramaConfig(
        "creg", "https://gestornormativo.creg.gov.co/gestor/entorno/",
        ["resoluciones_por_orden_cronologico.html",
         "novedades_proyectos_resolucion.html", "novedades_circulares.html"],
        "creg",
    ),
    "crc": NormogramaConfig(
        "crc", "https://normograma.crcom.gov.co/crc/compilacion/",
        ["ndstr_crc_comision_regulacion_comunicaciones.html"],
        "crc",
    ),
}


class NormogramaDiscoverer(BaseDiscoverer):
    """BFS acotado sobre un gestor Normograma → CatalogSeeds."""

    def __init__(self, source: str, max_pages: int = 1500, concurrency: int = 6):
        cfg = NORMOGRAMA_SOURCES.get(source)
        if cfg is None:
            raise ValueError(
                f"'{source}' no es un normograma conocido. "
                f"Opciones: {', '.join(NORMOGRAMA_SOURCES)}"
            )
        self.cfg = cfg
        self.max_pages = max_pages
        self.concurrency = concurrency

    # ── parsing ─────────────────────────────────────────────────────────
    def _seed_from_docurl(self, url: str) -> CatalogSeed | None:
        name = url.rsplit("/", 1)[-1]
        m = _DOCNAME_RE.search(name)
        if not m:
            return None
        tipo_raw, _ent, numero, anio = m.group(1).lower(), m.group(2), m.group(3), m.group(4)
        # El último grupo de 4 dígitos debe ser un año plausible; si no, el nombre
        # no encaja en el patrón <tipo>_<ent>_<num>_<año> (es ruido) → descartar.
        if not (1900 <= int(anio) <= 2030):
            return None
        tipo = _TIPO_MAP.get(tipo_raw, tipo_raw.upper().replace("_", " "))
        cid = None
        try:
            cid = build_canonical_id(tipo, numero, anio)
        except Exception:
            pass
        return CatalogSeed(
            tipo=tipo, numero=numero, anio=anio, source=self.cfg.source,
            source_url=url, canonical_id=cid,
            extra={"entidad": self.cfg.entidad.upper()},
        )

    def _seed_from_api_item(self, it: dict[str, Any]) -> CatalogSeed | None:
        """Convierte un item de Buscar.ashx en CatalogSeed."""
        link = (it.get("link") or "").strip()
        if not link:
            return None
        tipo = _API_TIPO_MAP.get((it.get("tipo") or "").strip().lower())
        if not tipo:
            # Derivar del nombre de archivo como respaldo.
            seed = self._seed_from_docurl(self.cfg.docs_base + link)
            return seed
        numero = (it.get("numero") or "").strip() or None
        anio = (it.get("year") or "").strip() or None
        if anio and not (anio.isdigit() and 1900 <= int(anio) <= 2030):
            anio = None
        cid = None
        if numero and anio:
            try:
                cid = build_canonical_id(tipo, numero, anio)
            except Exception:
                pass
        return CatalogSeed(
            tipo=tipo, numero=numero, anio=anio, source=self.cfg.source,
            source_url=self.cfg.docs_base + link, canonical_id=cid,
            extra={"entidad": (it.get("entidad") or self.cfg.entidad).strip()},
        )

    async def _crawl_api(self) -> dict[str, CatalogSeed]:
        """Descubre vía la API Buscar.ashx (metadatos completos, sin BFS)."""
        import json as _json

        found: dict[str, CatalogSeed] = {}
        async with httpx.AsyncClient(
            headers={"User-Agent": "ScrapperLeyes/1.0 (investigacion academica)"},
            timeout=180.0, follow_redirects=True, verify=False,
        ) as client:
            for q in self.cfg.api_queries:
                try:
                    r = await client.get(self.cfg.api_url, params={"texto": q})
                    if r.status_code != 200:
                        continue
                    items = _json.loads(r.text)
                except Exception as e:
                    logger.warning("[%s] API query '%s' falló: %s", self.cfg.source, q, e)
                    continue
                for it in items:
                    link = (it.get("link") or "").strip()
                    if not link or link in found:
                        continue
                    seed = self._seed_from_api_item(it)
                    if seed:
                        found[link] = seed
                logger.info(
                    "[%s] API '%s': %d documentos acumulados", self.cfg.source, q, len(found)
                )
        return found

    # ── crawl ───────────────────────────────────────────────────────────
    async def _crawl(self) -> dict[str, CatalogSeed]:
        base = self.cfg.base
        base_host = urlparse(base).netloc
        seen_idx: set[str] = set()
        found: dict[str, CatalogSeed] = {}
        queue: asyncio.Queue = asyncio.Queue()
        for s in self.cfg.seeds:
            u = urljoin(base, s)
            seen_idx.add(u)
            queue.put_nowait(u)

        sem = asyncio.Semaphore(self.concurrency)
        pages = 0

        async with httpx.AsyncClient(
            headers={"User-Agent": "ScrapperLeyes/1.0 (investigacion academica)"},
            timeout=40.0, follow_redirects=True, verify=False,
        ) as client:
            async def worker():
                nonlocal pages
                while True:
                    try:
                        url = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        return
                    if pages >= self.max_pages:
                        return
                    pages += 1
                    try:
                        async with sem:
                            r = await client.get(url)
                        if r.status_code != 200:
                            continue
                        html = r.text
                    except Exception as e:
                        logger.debug("idx fail %s: %s", url, e)
                        continue
                    # Documentos.
                    for m in _DOC_RE.finditer(html):
                        doc = urljoin(url, m.group(1))
                        if doc in found:
                            continue
                        seed = self._seed_from_docurl(doc)
                        if seed:
                            found[doc] = seed
                    # Sub-índices (mismo host y bajo la base del gestor).
                    for m in _IDX_RE.finditer(html):
                        nxt = urljoin(url, m.group(1))
                        if (
                            nxt not in seen_idx
                            and urlparse(nxt).netloc == base_host
                            and nxt.startswith(base)
                            and "docs/" not in nxt
                            and len(seen_idx) < self.max_pages * 3
                        ):
                            seen_idx.add(nxt)
                            queue.put_nowait(nxt)

            workers = [asyncio.create_task(worker()) for _ in range(self.concurrency)]
            await asyncio.gather(*workers)

        logger.info(
            "[%s] %d páginas índice visitadas, %d documentos descubiertos",
            self.cfg.source, pages, len(found),
        )
        return found

    def discover(
        self,
        *,
        desde: date | None = None,
        hasta: date | None = None,
        filtro: dict[str, Any] | None = None,
    ) -> Iterator[CatalogSeed]:
        found = asyncio.run(self._crawl_api() if self.cfg.api_url else self._crawl())
        for seed in found.values():
            if desde and seed.anio and seed.anio.isdigit() and int(seed.anio) < desde.year:
                continue
            if hasta and seed.anio and seed.anio.isdigit() and int(seed.anio) > hasta.year:
                continue
            yield seed
