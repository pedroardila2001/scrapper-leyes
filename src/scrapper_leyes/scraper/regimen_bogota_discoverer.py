"""Discoverer para el Régimen Legal de Bogotá D.C. (sisjur).

sisjur (Secretaría Jurídica Distrital) consolida la normativa distrital —decretos,
acuerdos del Concejo, resoluciones— en ``https://www.alcaldiabogota.gov.co/sisjur``.
Cada norma es una **ficha estática** servida por un portal Oracle PL/SQL en la URL
canónica ``normas/Norma1.jsp?i=<ID>``, donde ``<ID>`` es el identificador interno
sisjur de la norma (no es el número de la norma; es un autoincremental del portal).

Recon en vivo (2026-06-19, httpx-equivalente vía navegador):
  - ``Norma1.jsp?i=1``   → ficha vacía (stub), confirma el *template* de la ficha.
  - ``Norma1.jsp?i=13935`` → "Decreto 190 de 2004 Alcaldía Mayor de Bogotá, D.C."
  - ``Norma1.jsp?i=4125``  → "Constitución Política 1 de 1991 …"
  El ``<title>`` y el ``<h2 class="h2">`` llevan SIEMPRE el patrón
  ``<TIPO> <NÚMERO> de <AÑO> <Entidad emisora>``; la ficha tiene divisiones con
  etiquetas en negrita: *Fecha de Expedición*, *Fecha de Entrada en Vigencia*,
  *Medio de Publicación*, y un enlace ``Norma_temas.jsp?i=<ID>``. La página se sirve
  en ``Windows-1252``.

Estado: **PARCIAL**. El parsing de la ficha (``_parse_ficha``) está confirmado con
fixture real y es el corazón testeable. El *descubrimiento* del universo de IDs vía
la búsqueda avanzada (``consulta_avanzada.jsp``) es un portal Oracle (``p_arg_names``
+ ``tipodoc`` numérico, p.ej. Decreto=11, Acuerdo=3) cuyo emparejamiento de campos
no se logró replicar en el recon mínimo; por eso el discoverer enumera por **barrido
acotado de IDs** (``Norma1.jsp?i=N``) — robusto porque los IDs son densos/secuenciales
en el portal — y descarta las fichas vacías. Si más adelante se confirma la query
GET de la búsqueda, ``_search_url`` y ``_parse_index`` ya están listos para usarla.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date
from typing import Any, Iterator
from urllib.parse import urljoin

import httpx

from scrapper_leyes.models import build_canonical_id
from scrapper_leyes.scraper.base import BaseDiscoverer, CatalogSeed

logger = logging.getLogger(__name__)

_BASE = "https://www.alcaldiabogota.gov.co/sisjur/"
_NORMA_URL = _BASE + "normas/Norma1.jsp?i={i}"

# El título / h2 de la ficha: "<TIPO> <NÚMERO> de <AÑO> <Entidad emisora>".
# El tipo puede ser de dos palabras ("Constitución Política", "Acto Legislativo").
_TITLE_RE = re.compile(
    r"^\s*(?P<tipo>[A-Za-zÁÉÍÓÚÑáéíóúñ.\s]+?)\s+"
    r"(?P<numero>[0-9][0-9A-Za-z\-]*)\s+de\s+(?P<anio>\d{4})\b"
    r"(?:\s+(?P<entidad>.*))?$"
)

# Etiqueta -> valor en la ficha (div en negrita seguido del div con el valor).
# Entre la etiqueta (truncada, p.ej. "Fecha de Expedici") y el cierre del div hay
# texto residual ("&oacute;n:") que toleramos con [^<]* (cualquier cosa salvo '<').
_FICHA_FIELD_RE = (
    r"{label}[^<]*</div>\s*"
    r'<div class="col-lg-12">\s*(?P<val>.*?)\s*</div>'
)

# Tipos distritales que nos interesan (mapeo a tipo canónico en mayúsculas).
_TIPO_MAP = {
    "decreto": "DECRETO",
    "acuerdo": "ACUERDO",
    "resolucion": "RESOLUCION",
    "resolución": "RESOLUCION",
    "circular": "CIRCULAR",
    "directiva": "CIRCULAR",
    "constitucion politica": "CONSTITUCION POLITICA",
    "constitución política": "CONSTITUCION POLITICA",
    "acto legislativo": "ACTO LEGISLATIVO",
    "ley": "LEY",
}

# Códigos de tipodoc en la búsqueda avanzada (capturados del <select> real).
TIPODOC_CODES = {"DECRETO": "11", "ACUERDO": "3"}


def _strip_tags(s: str) -> str:
    import html as _html

    s = re.sub(r"<[^>]+>", " ", s)
    s = _html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


class RegimenBogotaDiscoverer(BaseDiscoverer):
    """Descubre normativa distrital de Bogotá (sisjur) → CatalogSeeds.

    Args:
        id_inicio: primer ID sisjur del barrido.
        id_fin: último ID (exclusivo) del barrido.
        concurrency: peticiones concurrentes (cortés con .gov.co).
        tipos: filtra a estos tipos canónicos; por defecto los tres distritales.
    """

    def __init__(
        self,
        id_inicio: int = 1,
        id_fin: int = 189000,
        concurrency: int = 4,
        tipos: tuple[str, ...] = ("DECRETO", "ACUERDO", "RESOLUCION"),
    ):
        self.id_inicio = id_inicio
        self.id_fin = id_fin
        self.concurrency = concurrency
        self.tipos = set(tipos) if tipos else None

    # ── parsing puro (testeable offline) ────────────────────────────────────
    def _parse_ficha(self, html: str, sisjur_id: str) -> CatalogSeed | None:
        """Parsea una ficha ``Norma1.jsp?i=<id>`` → CatalogSeed (o None si vacía)."""
        title = None
        mt = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        if mt and mt.group(1).strip():
            title = _strip_tags(mt.group(1))
        if not title:
            mh = re.search(
                r'<h2 class="h2">(.*?)</h2>', html, re.IGNORECASE | re.DOTALL
            )
            if mh:
                title = _strip_tags(mh.group(1))
        if not title:
            return None  # ficha vacía / stub

        m = _TITLE_RE.match(title)
        if not m:
            return None
        tipo_raw = m.group("tipo").strip().lower()
        tipo = _TIPO_MAP.get(tipo_raw, tipo_raw.upper())
        if self.tipos and tipo not in self.tipos:
            return None
        numero = m.group("numero")
        anio = m.group("anio")
        entidad = (m.group("entidad") or "").strip() or None

        # Campos de la ficha (fecha de expedición, medio de publicación…).
        extra: dict[str, Any] = {"ambito": "Distrital"}
        if entidad:
            extra["entidad_emisora"] = entidad
        for label, key in (
            ("Fecha de Expedici", "fecha_expedicion"),
            ("Fecha de Entrada en Vigencia", "fecha_vigencia"),
            ("Medio de Publicaci", "medio_publicacion"),
        ):
            mm = re.search(
                _FICHA_FIELD_RE.format(label=re.escape(label)),
                html,
                re.IGNORECASE | re.DOTALL,
            )
            if mm:
                val = _strip_tags(mm.group("val"))
                if val:
                    extra[key] = val

        cid = None
        try:
            cid = build_canonical_id(tipo, numero, anio)
        except Exception:
            pass

        return CatalogSeed(
            tipo=tipo,
            numero=numero,
            anio=anio,
            source="regimen_bogota",
            source_url=_NORMA_URL.format(i=sisjur_id),
            external_id=sisjur_id,
            canonical_id=cid,
            extra=extra,
        )

    def _parse_index(self, html: str) -> list[CatalogSeed]:
        """Parsea una página de resultados de la búsqueda avanzada → seeds parciales.

        sisjur enlaza cada resultado con ``normas/Norma1.jsp?i=<ID>``. Esta función
        cosecha esos IDs y el texto del enlace (tipo/número/año) cuando está presente.
        Pensada para cuando se confirme la query GET de ``consulta_avanzada.jsp``;
        hoy el discoverer enumera por barrido de IDs (ver ``_crawl``).
        """
        seeds: list[CatalogSeed] = []
        seen: set[str] = set()
        for m in re.finditer(
            r'<a[^>]+href="[^"]*Norma1\.jsp\?i=(\d+)"[^>]*>(.*?)</a>',
            html,
            re.IGNORECASE | re.DOTALL,
        ):
            sisjur_id = m.group(1)
            if sisjur_id in seen:
                continue
            seen.add(sisjur_id)
            link_text = _strip_tags(m.group(2))
            tm = _TITLE_RE.match(link_text)
            if not tm:
                continue
            tipo_raw = tm.group("tipo").strip().lower()
            tipo = _TIPO_MAP.get(tipo_raw, tipo_raw.upper())
            if self.tipos and tipo not in self.tipos:
                continue
            cid = None
            try:
                cid = build_canonical_id(tipo, tm.group("numero"), tm.group("anio"))
            except Exception:
                pass
            seeds.append(
                CatalogSeed(
                    tipo=tipo,
                    numero=tm.group("numero"),
                    anio=tm.group("anio"),
                    source="regimen_bogota",
                    source_url=_NORMA_URL.format(i=sisjur_id),
                    external_id=sisjur_id,
                    canonical_id=cid,
                    extra={"ambito": "Distrital"},
                )
            )
        return seeds

    @staticmethod
    def _search_url(tipo: str, anio: int) -> str:
        """URL GET (best-effort) de la búsqueda avanzada por tipo+año.

        NO confirmada en el recon (el portal Oracle exige un emparejamiento exacto
        de ``p_arg_names``). Se deja como referencia del shape observado.
        """
        code = TIPODOC_CODES.get(tipo, " ")
        return (
            f"{_BASE}consulta_avanzada.jsp?dS=N"
            f"&p_arg_names=vnorm_tipn_nombre&tipodoc={code}"
            f"&p_arg_names=vnorm_ano_ini&ano1={anio}"
            f"&p_arg_names=vnorm_ano_fin&ano2={anio}"
            f"&buscarFrase=1&Consultar=Consultar"
        )

    # ── red ─────────────────────────────────────────────────────────────────
    async def _fetch_ficha(
        self, client: httpx.AsyncClient, sisjur_id: int
    ) -> CatalogSeed | None:
        url = _NORMA_URL.format(i=sisjur_id)
        try:
            r = await client.get(url)
            if r.status_code != 200:
                return None
            # sisjur se sirve en Windows-1252.
            html = r.content.decode("windows-1252", errors="replace")
        except Exception as e:
            logger.debug("[regimen_bogota] fallo ficha i=%s: %s", sisjur_id, e)
            return None
        return self._parse_ficha(html, str(sisjur_id))

    async def _crawl(self) -> list[CatalogSeed]:
        sem = asyncio.Semaphore(self.concurrency)
        found: list[CatalogSeed] = []

        async with httpx.AsyncClient(
            headers={"User-Agent": "ScrapperLeyes/1.0 (investigacion academica)"},
            timeout=40.0,
            follow_redirects=True,
            verify=False,
        ) as client:

            async def one(i: int):
                async with sem:
                    seed = await self._fetch_ficha(client, i)
                if seed:
                    found.append(seed)

            await asyncio.gather(
                *(one(i) for i in range(self.id_inicio, self.id_fin))
            )

        logger.info(
            "[regimen_bogota] %d normas descubiertas (IDs %d..%d)",
            len(found),
            self.id_inicio,
            self.id_fin,
        )
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
