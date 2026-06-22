"""Discoverer para la Corte Interamericana de Derechos Humanos (Corte IDH).

La jurisprudencia de la Corte IDH se publica como **PDF estático** en una
numeración por *serie*:

  * **Serie C** (casos contenciosos → sentencias):
    ``https://www.corteidh.or.cr/docs/casos/articulos/seriec_<N>_esp.pdf``
  * **Serie A** (opiniones consultivas):
    ``https://www.corteidh.or.cr/docs/opiniones/seriea_<N>_esp.pdf``

Cada caso contencioso tiene además una **ficha técnica** HTML accesible por un
id interno ``nId_Ficha``:

  ``https://www.corteidh.or.cr/ver_ficha_tecnica.cfm?nId_Ficha=<N>&lang=es``

La ficha trae el nombre del caso, el **"Estado demandado"** (país) y el número de
**Serie C** de la sentencia de fondo — los tres datos que necesitamos para sembrar
una ``CatalogSeed`` apuntando al PDF correcto.

Estrategia de descubrimiento (cortés, mínima):
  1. Cosechar las páginas de **listado de casos** (``casos_sentencias.cfm`` /
     ``casos_en_etapa_de_supervision`` y el buscador por país) para obtener los
     ``nId_Ficha`` candidatos. Se parsea el HTML del listado → pares
     (nId_Ficha, nombre).
  2. Para cada ficha candidata, leer la ficha técnica y **filtrar por
     ``Estado demandado: Colombia``**, extrayendo el número de Serie C.
  3. Emitir ``CatalogSeed`` (tipo="SENTENCIA") apuntando al
     ``seriec_<N>_esp.pdf``.

Las opiniones consultivas (Serie A) son pocas (~30 en total, ninguna "de" un país)
y baratas de incluir por patrón → se emiten como ``tipo="OPINION_CONSULTIVA"`` si
``incluir_opiniones=True``.

El parsing del HTML (listado y ficha) vive en métodos puros, testeables offline,
separados de la parte de red.
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

_BASE = "https://www.corteidh.or.cr"
_SERIEC_PDF = _BASE + "/docs/casos/articulos/seriec_{n}_esp.pdf"
_SERIEA_PDF = _BASE + "/docs/opiniones/seriea_{n}_esp.pdf"
_FICHA_URL = _BASE + "/ver_ficha_tecnica.cfm?nId_Ficha={n}&lang=es"

# Páginas de listado donde la Corte enumera sus casos contenciosos. Cada caso
# enlaza a su ficha técnica vía ?nId_Ficha=<N>.
_SEED_LISTINGS = (
    _BASE + "/casos_sentencias.cfm?lang=es",
    _BASE + "/casos_en_supervision_por_pais.cfm?lang=es",
)

# Enlace a una ficha técnica dentro de un listado: captura el nId_Ficha y, de
# forma laxa, el texto del ancla (nombre del caso).
_FICHA_LINK_RE = re.compile(
    r'ver_ficha_tecnica\.cfm\?nId_Ficha=(\d+)[^>]*>(?:\s*<[^>]+>)*\s*([^<]+)',
    re.IGNORECASE,
)
# Variante mínima: solo el id (cuando el ancla no trae texto legible).
_FICHA_ID_RE = re.compile(r'nId_Ficha=(\d+)', re.IGNORECASE)

# Campos de la ficha técnica. Se aplican sobre el TEXTO de la ficha (sin tags,
# ver _strip_html) → la etiqueta queda pegada a su valor.
_PAIS_RE = re.compile(
    r"Estado\s+[Dd]emandado\s*:?\s*([A-Za-zÁÉÍÓÚÑáéíóúñ .]+)", re.IGNORECASE
)
_NOMBRE_RE = re.compile(
    r"Nombre\s+del\s+caso\s*:?\s*([^\n]+?)\s+(?:Estado\s+[Dd]emandado|Sumilla|Serie\s+C|V[íi]ctima)",
    re.IGNORECASE,
)
# Año de la sentencia de fondo (en la sumilla: "sentencia de fondo del 8 de
# diciembre de 1995").
_FONDO_FECHA_RE = re.compile(
    r"[Ss]entencia\s+de\s+fondo\s+del?\s+\d{1,2}\s+de\s+\w+\s+de\s+(\d{4})"
)
_ANY_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")

# La ficha lista el Nº de Serie C POR FASE como "<etiqueta>: <N>" (cada fase es
# un PDF seriec_<N>_esp.pdf independiente). Orden = más específica primero para
# que "Fondo" no robe el match de "Sentencia de Fondo".
_PHASE_LABELS: tuple[tuple[str, str], ...] = (
    ("Excepciones Preliminares, Fondo, Reparaciones y Costas", "fondo"),
    ("Excepción Preliminar, Fondo, Reparaciones y Costas", "fondo"),
    ("Fondo, Reparaciones y Costas", "fondo"),
    ("Sentencia de Fondo", "fondo"),
    ("Reparaciones y Costas", "reparaciones"),
    ("Excepciones Preliminares", "excepciones_preliminares"),
    ("Excepción Preliminar", "excepciones_preliminares"),
    ("Interpretación de la Sentencia", "interpretacion"),
    ("Interpretación", "interpretacion"),
    ("Fondo", "fondo"),
)


class CorteIDHDiscoverer(BaseDiscoverer):
    """Descubre sentencias (Serie C) de la Corte IDH para un país dado.

    Por defecto filtra ``pais="Colombia"``. Las opiniones consultivas (Serie A)
    se incluyen opcionalmente por patrón.
    """

    def __init__(
        self,
        source: str = "corte_idh",
        *,
        pais: str = "Colombia",
        incluir_opiniones: bool = True,
        max_fichas: int = 800,
        opiniones_range: tuple[int, int] = (1, 30),
        request_delay: float = 0.4,
    ):
        if source != "corte_idh":
            raise ValueError(
                f"CorteIDHDiscoverer solo cubre 'corte_idh', no '{source}'."
            )
        self.source = source
        self.pais = pais
        self.pais_norm = _norm_pais(pais)
        self.incluir_opiniones = incluir_opiniones
        self.max_fichas = max_fichas
        self.opiniones_range = opiniones_range
        self.request_delay = request_delay

    # ── parsing puro (offline) ───────────────────────────────────────────
    def _parse_listing(self, html: str) -> list[tuple[str, str | None]]:
        """De una página de listado → lista de (nId_Ficha, nombre|None).

        Dedup por id, preservando el primer nombre legible encontrado.
        """
        out: dict[str, str | None] = {}
        for m in _FICHA_LINK_RE.finditer(html):
            fid = m.group(1)
            nombre = re.sub(r"\s+", " ", (m.group(2) or "")).strip() or None
            if fid not in out or (out[fid] is None and nombre):
                out[fid] = nombre
        # Recoger ids que no encajaron en el patrón con nombre.
        for m in _FICHA_ID_RE.finditer(html):
            out.setdefault(m.group(1), None)
        return list(out.items())

    def _seeds_from_ficha(
        self, ficha_html: str, nid: str, nombre: str | None = None
    ) -> list[CatalogSeed]:
        """De la ficha técnica HTML → una CatalogSeed POR FASE (si el país coincide).

        Cada caso ante la Corte IDH puede tener varias sentencias (excepciones
        preliminares, fondo, reparaciones, interpretación), cada una su propio
        PDF ``seriec_<N>_esp.pdf``. La ficha las lista como "<etiqueta>: <N>".
        Devuelve ``[]`` si la ficha no es del país objetivo o no expone fases.
        """
        text = _strip_html(ficha_html)

        mp = _PAIS_RE.search(text)
        pais = None
        if mp:
            raw = re.sub(r"\s+", " ", mp.group(1)).strip()
            pais = _leading_pais(raw)
        if pais and _norm_pais(pais) != self.pais_norm:
            return []
        if not pais and self.pais_norm not in _norm_pais(text):
            return []

        if not nombre:
            mn = _NOMBRE_RE.search(text)
            nombre = re.sub(r"\s+", " ", mn.group(1)).strip() if mn else None

        mf = _FONDO_FECHA_RE.search(text)
        anio_fondo = mf.group(1) if mf else None
        if not anio_fondo:
            my = _ANY_YEAR_RE.search(text)
            anio_fondo = my.group(1) if my else None

        # Extraer "<fase>: <N>" para cada fase conocida; primera etiqueta gana,
        # dedup por número de Serie C.
        por_serie: dict[str, str] = {}
        for label, subtipo in _PHASE_LABELS:
            for m in re.finditer(re.escape(label) + r"\s*:?\s*(\d{1,4})\b", text):
                serie_c = m.group(1)
                por_serie.setdefault(serie_c, subtipo)

        seeds: list[CatalogSeed] = []
        for serie_c, subtipo in por_serie.items():
            # El año de fondo aplica a la fase de fondo; las demás quedan sin año
            # confirmado (lo completará el scraper del PDF).
            anio = anio_fondo if subtipo == "fondo" else None
            seeds.append(self._build_seed(
                tipo="SENTENCIA", numero=serie_c, anio=anio, serie="C",
                external_id=f"{nid}-{serie_c}", nombre=nombre, subtipo=subtipo,
            ))
        return seeds

    def _seed_opinion(self, serie_a: str, anio: str | None = None,
                      nombre: str | None = None) -> CatalogSeed:
        """Construye una seed de opinión consultiva (Serie A) por patrón."""
        return self._build_seed(
            tipo="OPINION_CONSULTIVA", numero=serie_a, anio=anio, serie="A",
            external_id=None, nombre=nombre,
        )

    def _build_seed(
        self, *, tipo: str, numero: str, anio: str | None, serie: str,
        external_id: str | None, nombre: str | None, subtipo: str | None = None,
    ) -> CatalogSeed:
        if serie == "C":
            url = _SERIEC_PDF.format(n=numero)
        else:
            url = _SERIEA_PDF.format(n=numero)
        cid = None
        if anio:
            try:
                # corte+sala califican el id → co:sentencia:idh:<fase>:<N>:<año>
                # (evita colisión con una sentencia nacional del mismo número).
                cid = build_canonical_id(
                    tipo, numero, anio, corte="idh", sala=subtipo or "fondo"
                )
            except Exception:
                pass
        extra: dict[str, Any] = {"serie": serie}
        if tipo == "SENTENCIA":
            extra["pais"] = self.pais
        if nombre:
            extra["caso"] = nombre
        return CatalogSeed(
            tipo=tipo, numero=numero, anio=anio, source=self.source,
            corte="idh", canonical_id=cid, external_id=external_id,
            source_url=url, subtipo=subtipo, extra=extra,
        )

    # ── red ──────────────────────────────────────────────────────────────
    async def _crawl(self) -> dict[str, CatalogSeed]:
        found: dict[str, CatalogSeed] = {}
        async with httpx.AsyncClient(
            headers={"User-Agent": "ScrapperLeyes/1.0 (investigacion academica)"},
            timeout=60.0, follow_redirects=True, verify=False,
        ) as client:
            # 1) Cosechar listados → candidatos (nId_Ficha, nombre).
            candidates: dict[str, str | None] = {}
            for listing in _SEED_LISTINGS:
                try:
                    r = await client.get(listing)
                    if r.status_code == 200:
                        for fid, nombre in self._parse_listing(r.text):
                            if fid not in candidates or (
                                candidates[fid] is None and nombre
                            ):
                                candidates[fid] = nombre
                except Exception as e:
                    logger.warning("[corte_idh] listado %s falló: %s", listing, e)
                await asyncio.sleep(self.request_delay)

            logger.info("[corte_idh] %d fichas candidatas en los listados",
                        len(candidates))

            # 2) Leer cada ficha y filtrar por país (una seed por fase/sentencia).
            for i, (fid, nombre) in enumerate(candidates.items()):
                if i >= self.max_fichas:
                    break
                try:
                    r = await client.get(_FICHA_URL.format(n=fid))
                    if r.status_code != 200:
                        continue
                    for seed in self._seeds_from_ficha(r.text, fid, nombre):
                        found[seed.source_url] = seed
                except Exception as e:
                    logger.debug("[corte_idh] ficha %s falló: %s", fid, e)
                await asyncio.sleep(self.request_delay)

            # 3) Opiniones consultivas (Serie A) por patrón, si se piden.
            if self.incluir_opiniones:
                lo, hi = self.opiniones_range
                for n in range(lo, hi + 1):
                    seed = self._seed_opinion(str(n))
                    found.setdefault(seed.source_url, seed)

        logger.info("[corte_idh] %d documentos descubiertos (país=%s)",
                    len(found), self.pais)
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


# Países americanos sujetos a la jurisdicción de la Corte IDH (para recortar el
# valor "Estado demandado" cuando arrastra texto de la siguiente etiqueta).
_PAISES = (
    "Argentina", "Bolivia", "Brasil", "Chile", "Colombia", "Costa Rica",
    "Ecuador", "El Salvador", "Guatemala", "Haití", "Honduras", "México",
    "Nicaragua", "Panamá", "Paraguay", "Perú", "República Dominicana",
    "Suriname", "Trinidad y Tobago", "Uruguay", "Venezuela",
)


def _strip_html(html: str) -> str:
    """Quita tags y colapsa espacios (mismo enfoque que WebRelatoria)."""
    import html as _html

    text = _html.unescape(re.sub(r"<[^>]+>", " ", html))
    return re.sub(r"\s+", " ", text).strip()


def _leading_pais(raw: str) -> str:
    """De un valor de 'Estado demandado' (que puede arrastrar texto) → el país.

    Empareja el prefijo contra la lista conocida de países; si no encaja, toma
    la primera palabra capitalizada como respaldo.
    """
    raw_n = _norm_pais(raw)
    best = ""
    for p in _PAISES:
        if raw_n.startswith(_norm_pais(p)) and len(p) > len(best):
            best = p
    if best:
        return best
    return raw.split(" ")[0] if raw else raw


def _norm_pais(s: str) -> str:
    """Normaliza un país/texto para comparación: minúsculas, sin acentos."""
    s = s.lower()
    for a, b in (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u"), ("ñ", "n")):
        s = s.replace(a, b)
    return s
