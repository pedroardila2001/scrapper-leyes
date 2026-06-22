"""Discoverer para la Comunidad Andina de Naciones (CAN).

Dos cuerpos normativos, ambos en ``comunidadandina.org`` como PDF estático:

  * **Decisiones** (legislación andina, vinculante para los países miembros):
    ``https://www.comunidadandina.org/DocOficialesFiles/decisiones/DECISION<N>.pdf``
    Numeración correlativa 1..~922.
  * **Sentencias / Procesos del Tribunal de Justicia (TJCAN)**:
    ``https://www.comunidadandina.org/DocOficialesFiles/Procesos/<cod>.pdf``

El sitio es WordPress: hay una página de **listado de Decisiones** que enlaza cada
``DECISION<N>.pdf`` junto a su título y (a veces) año. Estrategia:

  1. Cosechar el/los listado(s) HTML → números + títulos + años cuando estén.
  2. **Fallback determinístico**: si el listado no se obtiene o cubre poco,
     generar seeds por patrón para ``DECISION<N>.pdf`` en un rango. NO se descarga
     cada PDF aquí (el scraper de texto vendrá después); se hace solo un muestreo
     opcional para confirmar que el patrón responde.

El parsing del HTML del listado vive en un método puro testeable offline,
separado de la parte de red.
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

_BASE = "https://www.comunidadandina.org"
_DECISION_PDF = _BASE + "/DocOficialesFiles/decisiones/DECISION{n}.pdf"
_PROCESO_PDF = _BASE + "/DocOficialesFiles/Procesos/{cod}.pdf"

# Páginas de listado de Decisiones (WordPress). Punto de partida del cosechado.
_SEED_LISTINGS = (
    _BASE + "/documentos-oficiales/decisiones/",
    _BASE + "/decisiones/",
)

# Enlace a un PDF de Decisión dentro del listado: captura N (del nombre del
# archivo) y, de forma laxa, el texto del ancla — hasta </a> — que suele traer
# "Decisión N - Título ... (año)" (posiblemente con tags internos).
_DECISION_LINK_RE = re.compile(
    r'href=["\']([^"\']*DocOficialesFiles/decisiones/DECISION(\d+)\.pdf)["\']'
    r'[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
# Enlace a un proceso/sentencia del Tribunal.
_PROCESO_LINK_RE = re.compile(
    r'href=["\']([^"\']*DocOficialesFiles/Procesos/([^"\'./]+)\.pdf)["\']',
    re.IGNORECASE,
)
# Año dentro del texto del ancla / fila.
_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")


class CANDiscoverer(BaseDiscoverer):
    """Descubre Decisiones (y opcionalmente sentencias TJCAN) de la CAN."""

    def __init__(
        self,
        source: str = "can",
        *,
        decision_range: tuple[int, int] = (1, 922),
        incluir_tribunal: bool = True,
        usar_fallback: bool = True,
        request_delay: float = 0.4,
    ):
        if source != "can":
            raise ValueError(f"CANDiscoverer solo cubre 'can', no '{source}'.")
        self.source = source
        self.decision_range = decision_range
        self.incluir_tribunal = incluir_tribunal
        self.usar_fallback = usar_fallback
        self.request_delay = request_delay

    # ── parsing puro (offline) ───────────────────────────────────────────
    def _seed_from_decision(
        self, numero: int | str, *, titulo: str | None = None,
        anio: str | None = None, url: str | None = None,
    ) -> CatalogSeed:
        """Construye una seed de Decisión CAN por número."""
        n = str(numero)
        cid = None
        if anio:
            try:
                cid = build_canonical_id("DECISION CAN", n, anio, corte="can")
            except Exception:
                pass
        extra: dict[str, Any] = {"organismo": "Comunidad Andina"}
        if titulo:
            extra["titulo"] = titulo
        return CatalogSeed(
            tipo="DECISION CAN", numero=n, anio=anio, source=self.source,
            corte="can", external_id=n, canonical_id=cid,
            source_url=url or _DECISION_PDF.format(n=n), extra=extra,
        )

    def _seed_from_proceso(
        self, cod: str, *, titulo: str | None = None, anio: str | None = None,
        url: str | None = None,
    ) -> CatalogSeed:
        """Construye una seed de sentencia/proceso del Tribunal (TJCAN)."""
        cid = None
        if anio:
            try:
                cid = build_canonical_id("SENTENCIA", cod, anio, corte="can")
            except Exception:
                pass
        extra: dict[str, Any] = {"organismo": "Tribunal de Justicia CAN"}
        if titulo:
            extra["titulo"] = titulo
        return CatalogSeed(
            tipo="SENTENCIA", numero=cod, anio=anio, source=self.source,
            corte="can", external_id=cod, canonical_id=cid,
            source_url=url or _PROCESO_PDF.format(cod=cod), extra=extra,
        )

    def _parse_listing(self, html: str) -> list[CatalogSeed]:
        """De una página de listado HTML → CatalogSeeds (decisiones + procesos).

        Dedup por número de Decisión / código de proceso.
        """
        seeds: dict[str, CatalogSeed] = {}
        for m in _DECISION_LINK_RE.finditer(html):
            url, num = m.group(1), m.group(2)
            anchor = m.group(3) or ""
            titulo = re.sub(r"<[^>]+>", " ", anchor)
            titulo = re.sub(r"\s+", " ", titulo).strip() or None
            my = _YEAR_RE.search(titulo or "")
            anio = my.group(1) if my else None
            if url.startswith("/"):
                url = _BASE + url
            key = f"d:{num}"
            if key not in seeds:
                seeds[key] = self._seed_from_decision(
                    num, titulo=titulo, anio=anio,
                    url=url if url.lower().startswith("http") else None,
                )
        if self.incluir_tribunal:
            for m in _PROCESO_LINK_RE.finditer(html):
                url, cod = m.group(1), m.group(2)
                if url.startswith("/"):
                    url = _BASE + url
                key = f"p:{cod}"
                if key not in seeds:
                    seeds[key] = self._seed_from_proceso(
                        cod, url=url if url.lower().startswith("http") else None,
                    )
        return list(seeds.values())

    # ── red ──────────────────────────────────────────────────────────────
    async def _crawl(self) -> dict[str, CatalogSeed]:
        found: dict[str, CatalogSeed] = {}
        async with httpx.AsyncClient(
            headers={"User-Agent": "ScrapperLeyes/1.0 (investigacion academica)"},
            timeout=60.0, follow_redirects=True, verify=False,
        ) as client:
            # 1) Cosechar el listado WordPress.
            for listing in _SEED_LISTINGS:
                try:
                    r = await client.get(listing)
                    if r.status_code == 200:
                        for seed in self._parse_listing(r.text):
                            found[seed.source_url] = seed
                except Exception as e:
                    logger.warning("[can] listado %s falló: %s", listing, e)
                await asyncio.sleep(self.request_delay)

            logger.info("[can] %d documentos del listado WordPress", len(found))

            # 2) Fallback determinístico por patrón DECISION<N>.pdf.
            if self.usar_fallback:
                lo, hi = self.decision_range
                # Muestreo cortés (~10 HEAD) para confirmar que el patrón
                # responde, sin descargar los ~922 PDFs.
                ok = await self._sample_pattern(client, lo, hi)
                if ok:
                    for n in range(lo, hi + 1):
                        seed = self._seed_from_decision(n)
                        found.setdefault(seed.source_url, seed)
                    logger.info(
                        "[can] fallback de patrón aplicado (%d..%d)", lo, hi
                    )
                else:
                    logger.warning(
                        "[can] muestreo del patrón DECISION<N>.pdf no respondió; "
                        "se omite el fallback"
                    )

        logger.info("[can] %d documentos descubiertos en total", len(found))
        return found

    async def _sample_pattern(
        self, client: httpx.AsyncClient, lo: int, hi: int, n_samples: int = 10
    ) -> bool:
        """HEAD/GET de ~10 números del rango para confirmar que responden."""
        step = max(1, (hi - lo) // n_samples)
        hits = 0
        tried = 0
        for n in range(lo, hi + 1, step):
            url = _DECISION_PDF.format(n=n)
            try:
                r = await client.head(url)
                if r.status_code in (200, 206):
                    hits += 1
                elif r.status_code == 405:  # HEAD no permitido → probar GET
                    r = await client.get(url, headers={"Range": "bytes=0-0"})
                    if r.status_code in (200, 206):
                        hits += 1
            except Exception:
                pass
            tried += 1
            await asyncio.sleep(self.request_delay)
            if tried >= n_samples:
                break
        logger.info("[can] muestreo patrón: %d/%d respondieron", hits, tried)
        return hits >= max(1, tried // 2)

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
