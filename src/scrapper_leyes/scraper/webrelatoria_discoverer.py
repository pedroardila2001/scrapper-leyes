"""Discoverer para WebRelatoria (Corte Suprema de Justicia + Consejo de Estado).

WebRelatoria es una app PrimeFaces/JSF. La búsqueda es un POST AJAX con
``javax.faces.ViewState`` que devuelve un ``<partial-response>`` con la tabla de
resultados (``resultForm:jurisTable``). Cada fila trae metadatos completos —
incluido el **ID** del documento (``data-rk``), que es el ``file`` de
``FileReferenceServlet`` para descargar el texto (PDF/DOC).

ENUMERACIÓN (clave). El datatable NO es paginable de forma libre: el backend es
un ``LazyDataModel`` con una **ventana fija de 100 registros**. La búsqueda carga
el bloque ``[0..99]``; un POST de paginación ``first=0..50`` lee dentro de ese
bloque, pero ``first>=100`` devuelve **0 filas** (el bloque no se recargó). Los
botones de paginación del portal (primero/anterior/siguiente/último) mueven un
cursor de **a un documento**, así que no sirven para saltar bloques. Verificado
en vivo (2026-06-22) con Playwright sobre el portal real.

Por eso enumeramos por **bisección recursiva de rango de fechas**: se parte el
intervalo a la mitad hasta que un sub-rango tenga ``<= 100`` resultados, y ahí se
lee el bloque completo. Para días densos (``> 100`` en un único día) se refina
por ejes secundarios disjuntos (CSJ: tutela/asuntos; CE: tipo de providencia).
Las particiones son disjuntas y completas (verificado: TUTELA+ASUNTOS = total).

Texto: ``FileReferenceServlet?corp={csj|ce}&ext=pdf&file=<ID>`` (verificado 200/PDF).
Nota: el CE >=2021-12 vive además en SAMAI (ASP.NET) → fuente aparte.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field as dc_field
from datetime import date, timedelta
from typing import Any, Iterator

import httpx

from scrapper_leyes.models import build_canonical_id
from scrapper_leyes.scraper.base import BaseDiscoverer, CatalogSeed

logger = logging.getLogger(__name__)

_VIEWSTATE_RE = re.compile(r'name="javax\.faces\.ViewState"[^>]*value="([^"]+)"')
_VIEWSTATE_PR_RE = re.compile(
    r'<update id="[^"]*ViewState[^"]*"><!\[CDATA\[([^\]]+)\]\]>'
)
_TABLE_RE = re.compile(
    r'<update id="resultForm:jurisTable"><!\[CDATA\[(.*?)\]\]></update>', re.S
)
_PAG_RE = re.compile(
    r'<update id="resultForm:pagText2"><!\[CDATA\[(.*?)\]\]></update>', re.S
)
_TOTAL_RE = re.compile(r"/\s*([\d.,]+)")
# id del botón de búsqueda dentro de searchForm (CSJ: searchButton; CE: j_idtNN).
_SEARCH_BTN_RE = re.compile(
    r'id="(searchForm:[A-Za-z0-9_]+)"[^>]*class="[^"]*searchButton[^"]*"'
)
_VIEW_EXPIRED_RE = re.compile(r"ViewExpired|view.{0,3}could not be restored", re.I)


@dataclass
class _Axis:
    """Eje secundario que parte un día denso en sub-conjuntos.

    ``parts`` es una lista de dicts de filtros JSF (cada dict define un bucket).
    Los buckets deberían ser disjuntos y completos, pero aunque se solapen la
    deduplicación por ``external_id`` mantiene la corrección (solo cuesta
    requests redundantes). ``name`` es para logging.
    """

    name: str
    parts: list[dict[str, str]] = dc_field(default_factory=list)


# Salas de la CSJ (cada providencia pertenece a una; verificado que las cuatro
# principales suman el total de un día denso → partición completa). Se incluyen
# todas las variantes para cubrir también asuntos de sala además de tutelas.
_CSJ_SALAS = [
    {"searchForm:scivil": "SALA DE CASACIÓN CIVIL"},
    {"searchForm:slaboral": "SALA DE CASACIÓN LABORAL"},
    {"searchForm:slaboral": "SALA DE DESCONGESTIÓN LABORAL "},
    {"searchForm:spenal": "Sala de Casación Penal"},
    {"searchForm:spenal": "Sala Especial de Instrucción"},
    {"searchForm:spenal": "Sala Especial de Primera Instancia"},
    {"searchForm:splena": "SALA PLENA"},
]

_PORTAL = {
    "csj": {
        "url": "https://consultajurisprudencial.ramajudicial.gov.co/WebRelatoria/csj/index.xhtml",
        "corte": "csj",
        # CSJ usa inputs de fecha con máscara (sin sufijo _input).
        "date_suffix": "",
        # Día denso → tutela/asuntos (2) y luego sala (7). Cumulativo.
        "axes": [
            _Axis("tutela", [{"searchForm:tutelaselect": "ASUNTOS DE SALA"},
                             {"searchForm:tutelaselect": "TUTELA"}]),
            _Axis("sala", _CSJ_SALAS),
        ],
    },
    "consejo_estado": {
        "url": "https://jurisprudencia.ramajudicial.gov.co/WebRelatoria/ce/index.xhtml",
        "corte": "ce",
        # CE usa p:calendar → el value real va en el campo con sufijo _input.
        "date_suffix": "_input",
        # Día denso → tipo de providencia (3) y luego sección (selectOneMenu).
        # Las secciones suman el total del bucket (verificado: partición completa).
        # Bastan las modernas: las salas legacy guardan docs antiguos de días poco
        # densos que no llegan a activar este eje.
        "axes": [
            _Axis("tipo", [{"searchForm:j_idt58": "AUTO"},
                           {"searchForm:j_idt58": "CONCEPTO"},
                           {"searchForm:j_idt58": "SENTENCIA"}]),
            _Axis("seccion", [
                {"searchForm:j_idt71_input": s} for s in (
                    "SECCION PRIMERA", "SECCION SEGUNDA", "SECCION TERCERA",
                    "SECCION CUARTA", "SECCION QUINTA", "SALA PLENA",
                    "SALA DE CONSULTA Y SERVICIO CIVIL",
                )
            ]),
        ],
    },
}

# Tamaño de la ventana server-side del LazyDataModel (verificado: 100).
_WINDOW = 100


class WebRelatoriaDiscoverer(BaseDiscoverer):
    """Descubre providencias de CSJ / Consejo de Estado vía el buscador JSF.

    Enumera por bisección recursiva de fechas: cada sub-rango con ``<= _WINDOW``
    resultados se lee completo; los más grandes se parten por la mitad (o por eje
    secundario en días densos).
    """

    def __init__(self, source: str, query: str = "", max_docs: int | None = None):
        if source not in _PORTAL:
            raise ValueError(
                f"WebRelatoria no cubre '{source}'. Opciones: {list(_PORTAL)}"
            )
        cfg = _PORTAL[source]
        self.source = source
        self.corte = cfg["corte"]
        self.url = cfg["url"]
        self.date_suffix = cfg["date_suffix"]
        self.axes: list[_Axis] = cfg["axes"]
        # query vacío = TODO el corpus del rango (no solo docs con un término).
        self.query = query
        self.max_docs = max_docs
        self.servlet = self.url.replace("/index.xhtml", "/FileReferenceServlet")
        self._search_src = "searchForm:searchButton"  # se resuelve en bootstrap

    # ── parsing de filas ─────────────────────────────────────────────────
    # Etiqueta → valor hasta la siguiente etiqueta conocida (sirve a CSJ y CE).
    _STOP = (
        r"NÚMERO DE PROCESO|NÚMERO DE PROVIDENCIA|CLASE DE ACTUACIÓN|"
        r"TIPO DE PROVIDENCIA|FECHA|PONENTE|TEMA|FUENTE FORMAL|SALVAMENTO|"
        r"ACTOR|DEMANDAD[OA]|DECISION|DECISIÓN|SUSTENTO NORMATIVO|"
        r"NORMA DEMANDADA|SECCI[OÓ]N|NR|ID|$"
    )

    @classmethod
    def _field(cls, text: str, label: str) -> str | None:
        m = re.search(label + r"\s*:?\s*(.*?)\s*(?:" + cls._STOP + r")\s*:", text)
        if m and m.group(1).strip():
            return m.group(1).strip()
        return None

    def _parse_rows(self, table_html: str) -> list[CatalogSeed]:
        """Parsea filas del datatable. Unificado CSJ/CE: cada fila empieza en
        ``data-rk="<id>"`` (presente en ambos portales); el id es el ``file`` del
        FileReferenceServlet. Los metadatos se extraen del texto con etiquetas
        tolerantes a los dos formatos (CSJ usa "ID:"/"SALA DE …"; CE usa
        "NR:"/"SECCION :")."""
        import html as _html

        seeds: list[CatalogSeed] = []
        # Bloques por fila: desde un data-rk hasta el siguiente.
        blocks = re.findall(r'data-rk="(\d+)"(.*?)(?=data-rk="\d+"|\Z)', table_html, re.S)
        for doc_id, raw in blocks:
            text = _html.unescape(re.sub(r"<[^>]+>", " ", raw))
            text = re.sub(r"\s+", " ", text).strip()

            mf = re.search(r"FECHA\s*:?\s*(\d{2})/(\d{2})/(\d{4})", text)
            anio = mf.group(3) if mf else None
            ponente = self._field(text, "PONENTE")
            tipo_prov = self._field(text, "TIPO DE PROVIDENCIA")
            # Radicado/proceso: CSJ lo etiqueta ("NÚMERO DE PROCESO:"); CE lo deja
            # suelto tras el NR → se reconoce por su forma XXXXX-XX-XX-…
            proceso = self._field(text, "NÚMERO DE PROCESO")
            if not proceso:
                mr = re.search(r"\b([A-Z]?\s?\d{4,5}-?\d{2}-?\d{2}-?\d{3}[\d-]{6,})\b", text)
                proceso = mr.group(1).strip() if mr else None
            providencia = self._field(text, "NÚMERO DE PROVIDENCIA")
            # Sala (CSJ, inline) o Sección (CE, etiquetada).
            sala = None
            ms = re.search(r"(SALA\s+(?:DE\s+)?[A-ZÁÉÍÓÚÑ ]+?)\s+(?:TUTELA|ASUNTO|NR|ID|N[ÚU]MERO)", text)
            if ms:
                sala = ms.group(1).strip().title()
            else:
                seccion = self._field(text, "SECCI[OÓ]N")
                if seccion:
                    sala = seccion.title()
            if tipo_prov is None:
                # CE: el tipo aparece tras el radicado, sin etiqueta; se corta
                # antes de la siguiente sección ("SUSTENTO", "FECHA", …).
                mt = re.search(
                    r"\b(AUTO|SENTENCIA|CONCEPTO)((?:\s+[A-ZÁÉÍÓÚÑ]+)*?)"
                    r"(?=\s+(?:SUSTENTO|NORMA|FECHA|SECCI|PONENTE|ACTOR|TEMA|DECISI))",
                    text,
                )
                if mt:
                    tipo_prov = (mt.group(1) + mt.group(2)).strip().title()

            numero = providencia or proceso or doc_id
            sala_l = (sala or "").lower()
            sala_code = next(
                (c for c in ("laboral", "penal", "civil", "plena", "constitucional")
                 if c in sala_l), "plena",
            )
            cid = None
            if anio:
                try:
                    cid = build_canonical_id("SENTENCIA", numero, anio, corte=self.corte,
                                             sala=sala_code)
                except Exception:
                    pass
            seeds.append(CatalogSeed(
                tipo="SENTENCIA", numero=numero, anio=anio, source=self.source,
                corte=self.corte, magistrado_ponente=ponente, canonical_id=cid,
                external_id=doc_id,
                source_url=f"{self.servlet}?corp={self.corte}&ext=pdf&file={doc_id}",
                subtipo=tipo_prov,
                extra={"radicado": proceso, "sala": sala, "providencia": providencia},
            ))
        return seeds

    # ── payloads JSF ─────────────────────────────────────────────────────
    def _fecha_field(self, which: str) -> str:
        return f"searchForm:fecha{which}Cal{self.date_suffix}"

    def _search_payload(
        self, viewstate: str, desde: date, hasta: date, extra: dict[str, str]
    ) -> dict[str, str]:
        collapsed = [
            "fulltxt", "ponente", "fecha", "radicado", "providencia", "id", "tipo",
            "clase", "fuente", "juris", "procedencia", "delitos", "sujetos",
            "servidor", "categoria",
        ]
        p = {
            "javax.faces.partial.ajax": "true",
            "javax.faces.source": self._search_src,
            "javax.faces.partial.execute": "@all",
            "javax.faces.partial.render": "resultForm:jurisTable resultForm:pagText2 resultForm:selectAllButton",
            self._search_src: self._search_src,
            "searchForm": "searchForm",
            "searchForm:temaInput": self.query,
            "searchForm:relevanteselect": "", "searchForm:options1": "0",
            self._fecha_field("Ini"): desde.strftime("%d/%m/%Y"),
            self._fecha_field("Fin"): hasta.strftime("%d/%m/%Y"),
            "javax.faces.ViewState": viewstate,
        }
        for c in collapsed:
            p[f"searchForm:{c}Input"] = ""
            p[f"searchForm:set-{c}_collapsed"] = "true"
        p.update(extra)
        return p

    def _buffer_payload(self, viewstate: str) -> dict[str, str]:
        """Lee el bloque completo (hasta _WINDOW filas) de la búsqueda actual."""
        return {
            "javax.faces.partial.ajax": "true",
            "javax.faces.source": "resultForm:jurisTable",
            "javax.faces.partial.execute": "resultForm:jurisTable",
            "javax.faces.partial.render": "resultForm:jurisTable",
            "resultForm:jurisTable": "resultForm:jurisTable",
            "resultForm:jurisTable_pagination": "true",
            "resultForm:jurisTable_first": "0",
            "resultForm:jurisTable_rows": str(_WINDOW),
            "resultForm:jurisTable_skipChildren": "true",
            "resultForm:jurisTable_encodeFeature": "true",
            "resultForm": "resultForm",
            "javax.faces.ViewState": viewstate,
        }

    # ── flujo HTTP ───────────────────────────────────────────────────────
    async def _post(self, c: httpx.AsyncClient, data: dict[str, str]) -> str:
        headers = {
            "User-Agent": "ScrapperLeyes/1.0 (investigacion academica)",
            "Faces-Request": "partial/ajax",
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        }
        last_exc: Exception | None = None
        for attempt in range(4):
            try:
                r = await c.post(self.url, data=data, headers=headers)
                return r.text
            except (httpx.ReadTimeout, httpx.ConnectError, httpx.RemoteProtocolError) as e:
                last_exc = e
                await asyncio.sleep(1.5 * (attempt + 1))
        raise last_exc  # type: ignore[misc]

    async def _bootstrap(self, c: httpx.AsyncClient) -> str:
        """GET index → ViewState inicial + resuelve el id del botón de búsqueda."""
        r = await c.get(self.url, headers={"User-Agent": "ScrapperLeyes/1.0"})
        m = _VIEWSTATE_RE.search(r.text)
        if not m:
            raise RuntimeError("No se obtuvo ViewState inicial de WebRelatoria")
        btn = _SEARCH_BTN_RE.search(r.text)
        if btn:
            self._search_src = btn.group(1)
        logger.debug("[%s] search button = %s", self.source, self._search_src)
        return m.group(1)

    @staticmethod
    def _parse_total(resp: str) -> int:
        mp = _PAG_RE.search(resp)
        if mp:
            mt = _TOTAL_RE.search(mp.group(1))
            if mt:
                return int(mt.group(1).replace(".", "").replace(",", ""))
        return 0

    async def _count(
        self, c: httpx.AsyncClient, vs: str, desde: date, hasta: date,
        extra: dict[str, str],
    ) -> tuple[int, str]:
        """Ejecuta la búsqueda y devuelve (total, viewstate_actualizado)."""
        resp = await self._post(c, self._search_payload(vs, desde, hasta, extra))
        if _VIEW_EXPIRED_RE.search(resp):
            vs = await self._bootstrap(c)
            resp = await self._post(c, self._search_payload(vs, desde, hasta, extra))
        vm = _VIEWSTATE_PR_RE.search(resp)
        if vm:
            vs = vm.group(1)
        return self._parse_total(resp), vs

    async def _read_buffer(self, c: httpx.AsyncClient, vs: str) -> tuple[list[CatalogSeed], str]:
        """Lee el bloque (<= _WINDOW) de la búsqueda recién ejecutada."""
        resp = await self._post(c, self._buffer_payload(vs))
        vm = _VIEWSTATE_PR_RE.search(resp)
        if vm:
            vs = vm.group(1)
        mt = _TABLE_RE.search(resp)
        rows = self._parse_rows(mt.group(1)) if mt else []
        return rows, vs

    async def _harvest(
        self, c: httpx.AsyncClient, vs: str, desde: date, hasta: date,
        extra: dict[str, str], axis_idx: int, sink: dict[str, CatalogSeed],
    ) -> str:
        """Recolecta recursivamente todas las filas de [desde, hasta] + filtros."""
        if self.max_docs and len(sink) >= self.max_docs:
            return vs
        total, vs = await self._count(c, vs, desde, hasta, extra)
        if total == 0:
            return vs

        if total <= _WINDOW:
            rows, vs = await self._read_buffer(c, vs)
            for s in rows:
                sink.setdefault(s.external_id, s)
            return vs

        if desde < hasta:
            # Bisección de fechas (rangos disjuntos: [desde, mid] y [mid+1, hasta]).
            mid = desde + (hasta - desde) // 2
            vs = await self._harvest(c, vs, desde, mid, extra, axis_idx, sink)
            vs = await self._harvest(c, vs, mid + timedelta(days=1), hasta, extra, axis_idx, sink)
            return vs

        # Día único con > _WINDOW: refinar por el siguiente eje secundario.
        if axis_idx < len(self.axes):
            axis = self.axes[axis_idx]
            for part in axis.parts:
                sub = dict(extra)
                sub.update(part)
                vs = await self._harvest(c, vs, desde, hasta, sub, axis_idx + 1, sink)
            return vs

        # Ejes agotados: leemos lo que podamos y reportamos la cola perdida.
        rows, vs = await self._read_buffer(c, vs)
        for s in rows:
            sink.setdefault(s.external_id, s)
        logger.warning(
            "[%s] %s: %d resultados con ejes agotados; recuperados <=%d, "
            "cola de ~%d sin capturar (filtros=%s)",
            self.source, desde.isoformat(), total, _WINDOW, total - _WINDOW, extra,
        )
        return vs

    async def _run(self, desde: date, hasta: date) -> dict[str, CatalogSeed]:
        """Recolecta [desde, hasta] (típicamente un año) y devuelve los seeds."""
        sink: dict[str, CatalogSeed] = {}
        async with httpx.AsyncClient(
            timeout=120.0, follow_redirects=True, verify=False
        ) as c:
            vs = await self._bootstrap(c)
            total, vs = await self._count(c, vs, desde, hasta, {})
            logger.info("[%s] enumerando %s..%s (total declarado=%d)",
                        self.source, desde.isoformat(), hasta.isoformat(), total)
            await self._harvest(c, vs, desde, hasta, {}, 0, sink)
        return sink

    def discover(
        self,
        *,
        desde: date | None = None,
        hasta: date | None = None,
        filtro: dict[str, Any] | None = None,
    ) -> Iterator[CatalogSeed]:
        """Enumera el corpus emitiendo año por año.

        Procesar por año acota la memoria (~un año de providencias) y da puntos
        de control naturales: si el crawl se corta, basta re-correr — el upsert
        del catálogo es idempotente (por ``external_id``/``canonical_id``).
        """
        # Rango por defecto: desde 1900 (cubre Gaceta Judicial histórica) hasta hoy.
        d0 = desde or date(1900, 1, 1)
        d1 = hasta or date.today()
        emitted = 0
        # De más reciente a más antiguo: la jurisprudencia útil suele ser reciente.
        for year in range(d1.year, d0.year - 1, -1):
            y_start = max(d0, date(year, 1, 1))
            y_end = min(d1, date(year, 12, 31))
            sink = asyncio.run(self._run(y_start, y_end))
            for s in sink.values():
                yield s
                emitted += 1
                if self.max_docs and emitted >= self.max_docs:
                    logger.info("[%s] tope max_docs=%d alcanzado", self.source, self.max_docs)
                    return
            logger.info("[%s] año %d: %d seeds (acumulado %d)",
                        self.source, year, len(sink), emitted)
