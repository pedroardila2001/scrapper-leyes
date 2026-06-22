"""Discoverer para la Gaceta del Congreso (Imprenta Nacional).

La Gaceta del Congreso publica proyectos de ley, exposiciones de motivos, ponencias
y demás antecedentes legislativos (art. 157 C.P.). La Imprenta Nacional la sirve en
``https://svrpubindc.imprenta.gov.co/senado/``.

Recon en vivo (2026-06-19) + documentación verificada:
  - Cada Gaceta se descarga por URL directa
    ``senado/index2.xhtml?ent={Senado|Camara}&fec={D-M-AAAA}&num={NUM}`` →
    devuelve el **PDF** de esa Gaceta (verificado: ``num=399`` descargó
    ``gaceta_399.pdf``; título de página "Gaceta Congreso").
  - La búsqueda/listado es una app JSF (PrimeFaces) en el mismo host; en el recon
    mínimo no se logró capturar la página de resultados (SPA + interstitial), por lo
    que el *descubrimiento del índice* queda como andamiaje honesto (``_parse_index``
    parsea el formato de filas que sí se observó: enlaces ``index2.xhtml?...num=``).

Identidad de cada Gaceta: la tupla ``(entidad, fecha, número)``. El ``num`` es el
**número de Gaceta** (no el número del proyecto de ley; un proyecto puede aparecer
en varias gacetas). Por eso el ``CatalogSeed`` usa ``tipo="GACETA"`` por defecto, con
``external_id=<num>`` y la fecha en ``extra``.

Estado: **ANDAMIAJE** (parcial en el constructor de URL, que sí está confirmado). El
constructor de URL de descarga (``gaceta_url``) y el parser de listado
(``_parse_index``) están listos y testeados offline; falta confirmar la query del
buscador JSF para enumerar qué gacetas existen por rango de fechas/legislatura.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date
from typing import Any, Iterator

import httpx

from scrapper_leyes.scraper.base import BaseDiscoverer, CatalogSeed

logger = logging.getLogger(__name__)

_BASE = "https://svrpubindc.imprenta.gov.co/senado/"
_GACETA_URL = _BASE + "index2.xhtml?ent={ent}&fec={fec}&num={num}"

# Enlace a una gaceta en un listado: index2.xhtml?ent=Senado&fec=27-4-2023&num=399
_LINK_RE = re.compile(
    r"index2\.xhtml\?ent=(?P<ent>Senado|Camara)"
    r"&(?:amp;)?fec=(?P<fec>\d{1,2}-\d{1,2}-\d{4})"
    r"&(?:amp;)?num=(?P<num>\d+)",
    re.IGNORECASE,
)

_ENTIDADES = ("Senado", "Camara")


def gaceta_url(ent: str, fec: str, num: int | str) -> str:
    """URL de descarga directa del PDF de una Gaceta (patrón confirmado en vivo).

    Args:
        ent: "Senado" o "Camara".
        fec: fecha de la gaceta como ``D-M-AAAA`` (p.ej. ``27-4-2023``).
        num: número de la gaceta (p.ej. 399).
    """
    return _GACETA_URL.format(ent=ent, fec=fec, num=num)


class GacetaCongresoDiscoverer(BaseDiscoverer):
    """Descubre Gacetas del Congreso → CatalogSeeds.

    Args:
        entidades: ("Senado", "Camara").
        tipo: tipo canónico a asignar ("GACETA" o "PROYECTO_LEY").
    """

    def __init__(
        self,
        entidades: tuple[str, ...] = _ENTIDADES,
        tipo: str = "GACETA",
    ):
        self.entidades = entidades
        self.tipo = tipo

    # ── parsing puro (testeable offline) ────────────────────────────────────
    def _seed_from_link(self, ent: str, fec: str, num: str) -> CatalogSeed:
        anio = None
        mf = re.search(r"-(\d{4})$", fec)
        if mf:
            anio = mf.group(1)
        return CatalogSeed(
            tipo=self.tipo,
            numero=num,
            anio=anio,
            source="gaceta_congreso",
            source_url=gaceta_url(ent, fec, num),
            external_id=num,
            extra={"entidad": ent.upper(), "fecha": fec, "ambito": "Nacional"},
        )

    def _parse_index(self, html: str) -> list[CatalogSeed]:
        """Cosecha gacetas de una página de listado/resultados del buscador JSF.

        Cada gaceta enlaza con ``index2.xhtml?ent=...&fec=...&num=...``. Dedup por
        (entidad, número, fecha).
        """
        seeds: list[CatalogSeed] = []
        seen: set[tuple[str, str, str]] = set()
        for m in _LINK_RE.finditer(html):
            ent = m.group("ent").capitalize()
            fec = m.group("fec")
            num = m.group("num")
            key = (ent, num, fec)
            if key in seen:
                continue
            seen.add(key)
            seeds.append(self._seed_from_link(ent, fec, num))
        return seeds

    # ── red (andamiaje honesto) ─────────────────────────────────────────────
    async def _crawl(self, filtro: dict[str, Any] | None) -> list[CatalogSeed]:
        """Best-effort: intenta el buscador JSF y parsea lo que devuelva.

        El recon no confirmó la query exacta del buscador (SPA PrimeFaces). Si se
        pasa ``filtro={"entidad","fec","num"}`` se construyen seeds directos por la
        URL confirmada (no requiere el índice). Si no, se loguea y se devuelve vacío.
        """
        # Caso directo: gacetas explícitas por (entidad, fecha, número).
        explicit = (filtro or {}).get("gacetas")
        if explicit:
            return [
                self._seed_from_link(g["ent"], g["fec"], str(g["num"]))
                for g in explicit
            ]

        found: dict[tuple[str, str, str], CatalogSeed] = {}
        async with httpx.AsyncClient(
            headers={"User-Agent": "ScrapperLeyes/1.0 (investigacion academica)"},
            timeout=40.0,
            follow_redirects=True,
            verify=False,
        ) as client:
            try:
                r = await client.get(_BASE + "index.xhtml")
                if r.status_code == 200:
                    for s in self._parse_index(r.text):
                        found[(s.extra["entidad"], s.numero, s.extra["fecha"])] = s
            except Exception as e:
                logger.warning("[gaceta_congreso] buscador no accesible: %s", e)
        if not found:
            logger.info(
                "[gaceta_congreso] sin índice cosechado (buscador JSF pendiente de "
                "confirmar); usa filtro={'gacetas':[{'ent','fec','num'}]} para sembrar."
            )
        return list(found.values())

    def discover(
        self,
        *,
        desde: date | None = None,
        hasta: date | None = None,
        filtro: dict[str, Any] | None = None,
    ) -> Iterator[CatalogSeed]:
        for seed in asyncio.run(self._crawl(filtro)):
            if desde and seed.anio and seed.anio.isdigit() and int(seed.anio) < desde.year:
                continue
            if hasta and seed.anio and seed.anio.isdigit() and int(seed.anio) > hasta.year:
                continue
            yield seed
