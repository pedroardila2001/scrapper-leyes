"""Discoverer para los Órganos de Control (`source="organos_control"`).

Cubre los TRES portales de doctrina/control disciplinario en un solo conector,
porque comparten un mismo destino conceptual (conceptos jurídicos + fallos
disciplinarios del Estado) aunque cada uno corra una plataforma distinta:

  * **Procuraduría — SIREL** (``apps.procuraduria.gov.co/relatoria``): app JSF
    cuyo buscador es un GET paginado. Cada documento expone PDF en
    ``/relatoria/media/file/<...>`` y/o HTML en ``/guia/.../docs/<rad>.html``.
    Mezcla conceptos y fallos disciplinarios. ~26.835 documentos.
  * **Contraloría — Azure Blob ``$web``**
    (``relatoria.blob.core.windows.net/$web/...``): repositorio estático con
    patrón DETERMINISTA. Conceptos de la Oficina Jurídica en
    ``files/conceptos-juridicos/CGR-OJ-<NNN>-<AAAA>.pdf`` (también
    ``resoluciones/REG-EJE-…`` y ``OGZ-…``). Verificado 206/PDF. ~2-5 mil.
  * **CNDJ — Comisión Nacional de Disciplina Judicial**
    (``relatoria.cndj.gov.co``): Liferay + relatoría; el PDF vive en
    ``docs_relatoria/<radicado+ADJUNTA+timestamp>.pdf``. Buscador vía XHR.
    Cientos-miles.

Diseño (igual que los otros discoverers del repo):
  * Un método async privado por portal (``_discover_<portal>``) que hace la red.
  * Métodos de parsing PUROS (``_parse_<portal>_*``) separados de la red y
    testeables offline con fixtures string reales.

Estado honesto (ver docstring de cada portal):
  * **Contraloría**: descubrimiento REAL por enumeración del patrón de blob
    (el patrón está verificado; HEAD por cada ``<NNN>`` x año, cortés).
  * **Procuraduría** y **CNDJ**: el patrón de descarga del PDF está confirmado,
    pero los nombres EXACTOS de parámetros del buscador no se pudieron confirmar
    en vivo en este spike (red restringida). El método de red intenta la vía
    documentada y, si la respuesta no encaja, devuelve ``[]`` con un log claro
    en vez de inventar datos. El parser puro SÍ está probado contra HTML/JSON
    de muestra reales. Quedan como **andamiaje con parser verificado**.
"""

from __future__ import annotations

import asyncio
import html as _html
import json as _json
import logging
import re
from datetime import date
from typing import Any, Iterator

import httpx

from scrapper_leyes.models import build_canonical_id
from scrapper_leyes.scraper.base import BaseDiscoverer, CatalogSeed

logger = logging.getLogger(__name__)

SOURCE = "organos_control"

_UA = "ScrapperLeyes/1.0 (investigacion academica)"
_ENTIDADES_VALIDAS = ("procuraduria", "contraloria", "cndj")


# ═══════════════════════════════════════════════════════════════════════════
# Procuraduría — SIREL
# ═══════════════════════════════════════════════════════════════════════════
_PROC_BASE = "https://apps.procuraduria.gov.co/relatoria"
# El buscador SIREL es un GET paginado con estos parámetros (confirmados en el
# spike de fuentes): ``first_result`` (offset), ``max_results`` (tamaño de
# página) y ``total_results`` reportado en la respuesta.
_PROC_PAGE_SIZE = 50

# Una fila de resultado SIREL trae un enlace al PDF (media/file) o al HTML
# (guia/.../docs/<rad>.html) y un bloque de metadatos. Capturamos ambos enlaces
# y el radicado del nombre de archivo.
_PROC_PDF_RE = re.compile(
    r'href=["\']([^"\']*?/relatoria/media/file/[^"\']+)["\']', re.IGNORECASE
)
_PROC_HTML_RE = re.compile(
    r'href=["\']([^"\']*?/guia/[^"\']*?/docs/([^"\'/]+?)\.html?)["\']', re.IGNORECASE
)
# total_results puede venir como input hidden o como texto "de N resultados".
_PROC_TOTAL_RE = re.compile(
    r'(?:total_results["\']?\s*[:=]\s*["\']?(\d+))'
    r'|(?:de\s+([\d.,]+)\s+resultados)',
    re.IGNORECASE,
)
# Tipo: SIREL marca "Concepto" vs "Fallo" / "Decisión disciplinaria".
_PROC_TIPO_RE = re.compile(
    r"\b(concepto|fallo|decisi[oó]n\s+disciplinaria|sentencia|auto)\b", re.IGNORECASE
)
# Radicado / año dentro del texto de la fila.
_PROC_ANIO_RE = re.compile(r"\b(19|20)\d{2}\b")


# ═══════════════════════════════════════════════════════════════════════════
# Contraloría — Azure Blob $web (patrón determinista, verificado 206/PDF)
# ═══════════════════════════════════════════════════════════════════════════
_CGR_BLOB = "https://relatoria.blob.core.windows.net/$web"
# Familias de documentos observadas en el blob:
#   files/conceptos-juridicos/CGR-OJ-<NNN>-<AAAA>.pdf   → CONCEPTO (Oficina Jurídica)
#   files/resoluciones/REG-EJE-<NNN>-<AAAA>.pdf         → RESOLUCION (reglamentaria)
#   files/resoluciones/OGZ-<NNN>-<AAAA>.pdf             → RESOLUCION (organizacional)
# El padding del consecutivo VARÍA (001, 0005, 144, 155) → al enumerar probamos
# variantes de padding y ambas extensiones (.pdf / .PDF).
_CGR_PREFIX = {
    "CONCEPTO": ("conceptos-juridicos", "CGR-OJ"),
    "RESOLUCION_REG": ("resoluciones", "REG-EJE"),
    "RESOLUCION_OGZ": ("resoluciones", "OGZ"),
}
# Parser de una URL de blob ya conocida → (prefijo, numero, anio).
_CGR_URL_RE = re.compile(
    r"/files/(?P<folder>[^/]+)/(?P<prefix>CGR-OJ|CGR%E2%80%93OJ|REG-EJE|OGZ)-?\s*"
    r"(?P<numero>\d+)-(?P<anio>\d{4})\.pdf",
    re.IGNORECASE,
)
# Un índice/microsite que enumere los conceptos suele listar los blobs como
# enlaces directos; este RE cosecha cualquier URL de blob de un HTML índice.
_CGR_LINK_RE = re.compile(
    r'(https://relatoria\.blob\.core\.windows\.net/\$web/files/[^"\'\s>]+?\.pdf)',
    re.IGNORECASE,
)


# ═══════════════════════════════════════════════════════════════════════════
# CNDJ — relatoría (PDF en docs_relatoria/<rad+ADJUNTA+timestamp>.pdf)
# ═══════════════════════════════════════════════════════════════════════════
_CNDJ_BASE = "https://relatoria.cndj.gov.co"
_CNDJ_DOCS = f"{_CNDJ_BASE}/docs_relatoria"
# El nombre de archivo es el radicado (23 dígitos) + "ADJUNTA" + timestamp (14
# dígitos) + .pdf. Ejemplos reales:
#   F52001250200020230046801ADJUNTA20240207110359.pdf
#   F11001110200020200085701ADJUNTA20231026141206.pdf
_CNDJ_DOCNAME_RE = re.compile(
    r"(?P<rad>[A-Z]?\d{20,24})ADJUNTA(?P<ts>\d{14})\.pdf", re.IGNORECASE
)
# Enlace a un PDF de relatoría dentro de cualquier HTML/JSON de resultados.
_CNDJ_LINK_RE = re.compile(
    r'(https?://relatoria\.cndj\.gov\.co/docs_relatoria/[^"\'\s>]+?\.pdf)',
    re.IGNORECASE,
)


def _anio_from_rad_ts(rad: str, ts: str) -> str | None:
    """El año va embebido en el radicado de 23 dígitos en posición FIJA.

    Formato de radicación colombiano: ``DDDDD AA EE OOO YYYY NNNNN II`` → el año
    son los dígitos [12:16] tras quitar la letra de tipo (F/A). Un ``finditer``
    de ``(19|20)\\d{2}`` falla porque agarra el "2000" del bloque de despacho;
    por eso se extrae posicionalmente. Fallback: los 4 primeros del timestamp."""
    digits = re.sub(r"\D", "", rad)
    if len(digits) >= 16:
        y = digits[12:16]
        if y.isdigit() and 1990 <= int(y) <= date.today().year + 1:
            return y
    if ts and len(ts) >= 4:
        y = ts[:4]
        if y.isdigit() and 1990 <= int(y) <= date.today().year + 1:
            return y
    return None


class OrganosControlDiscoverer(BaseDiscoverer):
    """Descubre conceptos/fallos de Procuraduría, Contraloría y CNDJ.

    Args:
        entidades: subconjunto de portales a recorrer. Por defecto los tres.
        max_docs: tope blando de documentos por entidad (cortesía con servidores).
    """

    def __init__(
        self,
        entidades: tuple[str, ...] = _ENTIDADES_VALIDAS,
        *,
        max_docs: int = 500,
    ):
        bad = [e for e in entidades if e not in _ENTIDADES_VALIDAS]
        if bad:
            raise ValueError(
                f"Entidades desconocidas {bad}. Opciones: {list(_ENTIDADES_VALIDAS)}"
            )
        if not entidades:
            raise ValueError("Debe pasar al menos una entidad.")
        self.entidades = tuple(dict.fromkeys(entidades))  # dedup, conserva orden
        self.max_docs = max_docs

    # ════════════════════════════════════════════════════════════════════
    # PROCURADURÍA — parsing puro
    # ════════════════════════════════════════════════════════════════════
    def _parse_procuraduria_results(self, html: str) -> list[CatalogSeed]:
        """Extrae CatalogSeeds de una página de resultados SIREL.

        Toma el HTML crudo de la lista de resultados y, por cada documento,
        captura el enlace al PDF (``media/file``) o al HTML (``docs/<rad>.html``),
        el radicado, el tipo (concepto vs fallo) y el año.
        """
        seeds: list[CatalogSeed] = []
        seen: set[str] = set()

        # Cada resultado suele estar envuelto en un <tr>/<li>/<div class="result">.
        # Partimos por las marcas de enlace PDF/HTML para aislar el contexto de
        # cada documento sin depender de una estructura exacta de contenedor.
        pdf_hits = list(_PROC_PDF_RE.finditer(html))
        html_hits = list(_PROC_HTML_RE.finditer(html))

        # Indexar HTML-doc por su radicado (nombre de archivo) para emparejar.
        html_by_rad: dict[str, str] = {}
        for m in html_hits:
            html_by_rad[m.group(2)] = m.group(1)

        # Límite inferior del contexto de cada enlace = fin del enlace anterior
        # (PDF o HTML), para no mezclar metadatos entre resultados contiguos.
        boundaries = sorted(h.end() for h in pdf_hits + html_hits)

        def _ctx(pos: int, span: int = 600) -> str:
            # No retroceder más allá del enlace previo.
            lo = max(0, pos - span)
            for b in boundaries:
                if b <= pos:
                    lo = max(lo, b)
            seg = html[lo: pos + span]
            return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", seg)))

        def _tipo_from(text: str) -> str:
            m = _PROC_TIPO_RE.search(text or "")
            if not m:
                return "CONCEPTO"
            kind = m.group(1).lower()
            if kind.startswith("concepto"):
                return "CONCEPTO"
            return "FALLO DISCIPLINARIO"

        # 1) Documentos con enlace PDF.
        for m in pdf_hits:
            pdf = _html.unescape(m.group(1))
            if not pdf.startswith("http"):
                pdf = _PROC_BASE.rsplit("/relatoria", 1)[0] + pdf if pdf.startswith("/") else pdf
            if pdf in seen:
                continue
            seen.add(pdf)
            ctx = _ctx(m.start())
            rad = pdf.rstrip("/").rsplit("/", 1)[-1].split(".")[0]
            anio_m = _PROC_ANIO_RE.search(ctx)
            anio = anio_m.group(0) if anio_m else None
            tipo = _tipo_from(ctx)
            seeds.append(self._proc_seed(tipo, rad, anio, pdf, ctx))

        # 2) Documentos que solo exponen HTML (sin PDF directo).
        for rad, href in html_by_rad.items():
            url = _html.unescape(href)
            if not url.startswith("http"):
                url = _PROC_BASE.rsplit("/relatoria", 1)[0] + url if url.startswith("/") else url
            if url in seen:
                continue
            seen.add(url)
            # ¿ya cubierto por un PDF con el mismo radicado?
            if any(rad in s.external_id for s in seeds if s.external_id):
                continue
            seeds.append(self._proc_seed("CONCEPTO", rad, None, url, rad))
        return seeds

    def _proc_seed(
        self, tipo: str, rad: str, anio: str | None, url: str, ctx: str
    ) -> CatalogSeed:
        numero = rad
        cid = None
        if anio and numero:
            try:
                cid = build_canonical_id(tipo, numero, anio)
            except Exception:
                pass
        return CatalogSeed(
            tipo=tipo,
            numero=numero,
            anio=anio,
            source=SOURCE,
            external_id=rad,
            source_url=url,
            canonical_id=cid,
            extra={"entidad": "PROCURADURIA", "radicado": rad},
        )

    def _parse_procuraduria_total(self, html: str) -> int:
        m = _PROC_TOTAL_RE.search(html)
        if not m:
            return 0
        raw = m.group(1) or m.group(2) or "0"
        return int(raw.replace(".", "").replace(",", ""))

    # ════════════════════════════════════════════════════════════════════
    # CONTRALORÍA — parsing puro
    # ════════════════════════════════════════════════════════════════════
    def _seed_from_cgr_url(self, url: str) -> CatalogSeed | None:
        """Convierte una URL de blob CGR conocida en CatalogSeed."""
        m = _CGR_URL_RE.search(url)
        if not m:
            return None
        prefix = m.group("prefix").upper().replace("%E2%80%93", "-")
        numero = m.group("numero").lstrip("0") or m.group("numero")
        anio = m.group("anio")
        if prefix.startswith("CGR"):
            tipo, ext_prefix = "CONCEPTO", "CGR-OJ"
        else:
            tipo, ext_prefix = "RESOLUCION", prefix  # REG-EJE / OGZ
        external_id = f"{ext_prefix}-{m.group('numero')}-{anio}"
        cid = None
        try:
            cid = build_canonical_id(tipo, numero, anio)
        except Exception:
            pass
        return CatalogSeed(
            tipo=tipo,
            numero=numero,
            anio=anio,
            source=SOURCE,
            external_id=external_id,
            source_url=url,
            canonical_id=cid,
            extra={"entidad": "CONTRALORIA", "serie": ext_prefix},
        )

    def _parse_contraloria_index(self, html: str) -> list[CatalogSeed]:
        """Cosecha todos los enlaces a blobs CGR de una página índice/microsite."""
        seeds: list[CatalogSeed] = []
        seen: set[str] = set()
        for m in _CGR_LINK_RE.finditer(html):
            url = m.group(1)
            if url in seen:
                continue
            seen.add(url)
            s = self._seed_from_cgr_url(url)
            if s:
                seeds.append(s)
        return seeds

    @staticmethod
    def _cgr_candidate_urls(prefix_key: str, numero: int, anio: int) -> list[str]:
        """URLs candidatas (variantes de padding y extensión) para un consecutivo."""
        folder, ext_prefix = _CGR_PREFIX[prefix_key]
        urls: list[str] = []
        for width in (3, 4):  # 001 y 0001 observados
            nnn = str(numero).zfill(width)
            for ext in ("PDF", "pdf"):
                urls.append(f"{_CGR_BLOB}/files/{folder}/{ext_prefix}-{nnn}-{anio}.{ext}")
        return urls

    # ════════════════════════════════════════════════════════════════════
    # CNDJ — parsing puro
    # ════════════════════════════════════════════════════════════════════
    def _seed_from_cndj_url(self, url: str) -> CatalogSeed | None:
        name = url.rstrip("/").rsplit("/", 1)[-1]
        m = _CNDJ_DOCNAME_RE.search(name)
        if not m:
            return None
        rad, ts = m.group("rad"), m.group("ts")
        anio = _anio_from_rad_ts(rad, ts)
        # El prefijo del radicado ('A'=auto, 'F'=fallo/providencia de fondo) da una
        # pista de tipo; por defecto FALLO DISCIPLINARIO (jurisdicción disciplinaria).
        tipo = "FALLO DISCIPLINARIO"
        cid = None
        if anio:
            try:
                cid = build_canonical_id(tipo, rad, anio)
            except Exception:
                pass
        return CatalogSeed(
            tipo=tipo,
            numero=rad,
            anio=anio,
            source=SOURCE,
            external_id=name.rsplit(".", 1)[0],
            source_url=url if url.startswith("http") else f"{_CNDJ_DOCS}/{name}",
            canonical_id=cid,
            extra={"entidad": "CNDJ", "radicado": rad, "timestamp": ts},
        )

    def _parse_cndj_results(self, payload: str) -> list[CatalogSeed]:
        """Extrae CatalogSeeds de la respuesta del buscador CNDJ (HTML o JSON).

        El buscador devuelve filas que enlazan al PDF en ``docs_relatoria/``.
        Aceptamos tanto HTML como JSON: cosechamos cualquier URL de PDF de
        relatoría presente y, si el JSON trae nombres de archivo sueltos, los
        resolvemos al directorio de docs.
        """
        seeds: list[CatalogSeed] = []
        seen: set[str] = set()

        # a) Enlaces absolutos a PDF de relatoría.
        for m in _CNDJ_LINK_RE.finditer(payload):
            url = m.group(1)
            if url in seen:
                continue
            seen.add(url)
            s = self._seed_from_cndj_url(url)
            if s:
                seeds.append(s)

        # b) Nombres de archivo sueltos (JSON con "archivo"/"documento": "<...>.pdf").
        for m in _CNDJ_DOCNAME_RE.finditer(payload):
            name = m.group(0)
            url = f"{_CNDJ_DOCS}/{name}"
            if url in seen:
                continue
            seen.add(url)
            s = self._seed_from_cndj_url(url)
            if s:
                seeds.append(s)
        return seeds

    # ════════════════════════════════════════════════════════════════════
    # Métodos de red (async) — uno por portal
    # ════════════════════════════════════════════════════════════════════
    async def _discover_procuraduria(self) -> list[CatalogSeed]:
        """SIREL: GET paginado (first_result/max_results). Andamiaje: si la
        respuesta no encaja con el parser, devuelve [] con log claro."""
        seeds: dict[str, CatalogSeed] = {}
        url = f"{_PROC_BASE}/consulta_sirel.page"
        async with httpx.AsyncClient(
            headers={"User-Agent": _UA}, timeout=60.0,
            follow_redirects=True, verify=False,
        ) as c:
            first = 0
            total = 0
            while len(seeds) < self.max_docs:
                params = {
                    "first_result": str(first),
                    "max_results": str(_PROC_PAGE_SIZE),
                }
                try:
                    r = await c.get(url, params=params)
                except Exception as e:
                    logger.warning("[organos_control/procuraduria] red falló: %s", e)
                    break
                if r.status_code != 200:
                    logger.warning(
                        "[organos_control/procuraduria] HTTP %s en %s",
                        r.status_code, r.url,
                    )
                    break
                if total == 0:
                    total = self._parse_procuraduria_total(r.text)
                page = self._parse_procuraduria_results(r.text)
                if not page:
                    logger.info(
                        "[organos_control/procuraduria] sin resultados parseables en "
                        "first_result=%s (el HTML del buscador puede diferir del "
                        "esperado; revisar selectores)", first,
                    )
                    break
                new = 0
                for s in page:
                    key = s.source_url or s.external_id
                    if key not in seeds:
                        seeds[key] = s
                        new += 1
                if not new:
                    break
                first += _PROC_PAGE_SIZE
                if total and first >= total:
                    break
        logger.info(
            "[organos_control/procuraduria] %d documentos (total reportado=%s)",
            len(seeds), total or "?",
        )
        return list(seeds.values())

    async def _discover_contraloria(self) -> list[CatalogSeed]:
        """Contraloría: enumeración DETERMINISTA del patrón de blob.

        El patrón ``files/conceptos-juridicos/CGR-OJ-<NNN>-<AAAA>.pdf`` está
        verificado (206/PDF). Barremos consecutivos por año haciendo HEAD
        (cortés); paramos tras una racha de faltantes por año.
        """
        seeds: dict[str, CatalogSeed] = {}
        this_year = date.today().year
        async with httpx.AsyncClient(
            headers={"User-Agent": _UA}, timeout=30.0,
            follow_redirects=True, verify=False,
        ) as c:
            for anio in range(this_year, 2018, -1):
                misses = 0
                for numero in range(1, 400):
                    if len(seeds) >= self.max_docs:
                        break
                    found = False
                    for url in self._cgr_candidate_urls("CONCEPTO", numero, anio):
                        try:
                            r = await c.head(url)
                        except Exception:
                            continue
                        if r.status_code in (200, 206):
                            s = self._seed_from_cgr_url(url)
                            if s:
                                seeds[s.external_id] = s
                                found = True
                            break
                    if found:
                        misses = 0
                    else:
                        misses += 1
                        if misses >= 12:  # racha de faltantes → fin del año
                            break
                if len(seeds) >= self.max_docs:
                    break
        logger.info("[organos_control/contraloria] %d conceptos enumerados", len(seeds))
        return list(seeds.values())

    async def _discover_cndj(self) -> list[CatalogSeed]:
        """CNDJ: buscador vía XHR. Andamiaje: el patrón de PDF está confirmado,
        pero el endpoint exacto del buscador no se pudo verificar en vivo. Se
        intenta la home + un endpoint de búsqueda plausible; si nada encaja,
        devuelve [] con log claro (NO inventa)."""
        seeds: dict[str, CatalogSeed] = {}
        candidates = [
            f"{_CNDJ_BASE}/",
            f"{_CNDJ_BASE}/buscador",
            f"{_CNDJ_BASE}/api/buscar",
        ]
        async with httpx.AsyncClient(
            headers={"User-Agent": _UA, "X-Requested-With": "XMLHttpRequest"},
            timeout=60.0, follow_redirects=True, verify=False,
        ) as c:
            for url in candidates:
                if len(seeds) >= self.max_docs:
                    break
                try:
                    r = await c.get(url)
                except Exception as e:
                    logger.debug("[organos_control/cndj] %s falló: %s", url, e)
                    continue
                if r.status_code != 200:
                    continue
                for s in self._parse_cndj_results(r.text):
                    seeds.setdefault(s.source_url or s.external_id, s)
        if not seeds:
            logger.info(
                "[organos_control/cndj] el buscador XHR no devolvió PDFs "
                "parseables desde los endpoints probados; queda como andamiaje "
                "(patrón de PDF confirmado, falta endpoint exacto del buscador)."
            )
        else:
            logger.info("[organos_control/cndj] %d providencias descubiertas", len(seeds))
        return list(seeds.values())

    # ════════════════════════════════════════════════════════════════════
    # discover()
    # ════════════════════════════════════════════════════════════════════
    async def _run(self) -> list[CatalogSeed]:
        dispatch = {
            "procuraduria": self._discover_procuraduria,
            "contraloria": self._discover_contraloria,
            "cndj": self._discover_cndj,
        }
        out: list[CatalogSeed] = []
        for ent in self.entidades:
            try:
                out.extend(await dispatch[ent]())
            except Exception as e:
                logger.warning("[organos_control/%s] discover falló: %s", ent, e)
        return out

    def discover(
        self,
        *,
        desde: date | None = None,
        hasta: date | None = None,
        filtro: dict[str, Any] | None = None,
    ) -> Iterator[CatalogSeed]:
        seeds = asyncio.run(self._run())
        for s in seeds:
            if desde and s.anio and s.anio.isdigit() and int(s.anio) < desde.year:
                continue
            if hasta and s.anio and s.anio.isdigit() and int(s.anio) > hasta.year:
                continue
            yield s
