"""Discoverer para WebRelatoria (Corte Suprema de Justicia + Consejo de Estado).

WebRelatoria es una app PrimeFaces/JSF: la búsqueda es un POST AJAX con
``javax.faces.ViewState`` que devuelve un ``<partial-response>`` con la tabla de
resultados (``resultForm:jurisTable``). Cada fila trae metadatos completos —
incluido el **ID** del documento, que es el ``file`` de ``FileReferenceServlet``
para descargar el texto (PDF/DOC).

Flujo (replicado con httpx, sin navegador):
  1. GET index.xhtml → cookie JSESSIONID + ViewState inicial.
  2. POST búsqueda (payload capturado del buscador real) → filas + total + nuevo
     ViewState.
  3. Paginación del datatable (POST ``jurisTable_pagination``) hasta ``max_docs``.

Texto: ``FileReferenceServlet?corp={csj|ce}&ext=pdf&file=<ID>`` (verificado 200/PDF).
Nota: el CE ≥2021-12 vive en SAMAI (ASP.NET), no en WebRelatoria → fuente aparte.
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

_VIEWSTATE_RE = re.compile(
    r'name="javax\.faces\.ViewState"[^>]*value="([^"]+)"'
)
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


# Etiquetas de campo que aparecen en cada fila de resultado (en cualquier orden).
_LABELS = [
    "NÚMERO DE PROCESO", "NÚMERO DE PROVIDENCIA", "CLASE DE ACTUACIÓN",
    "TIPO DE PROVIDENCIA", "FECHA", "PONENTE", "TEMA", "ID",
]


def _parse_segment(seg: str) -> dict[str, str]:
    """Extrae cada campo cortando entre una etiqueta y la siguiente (cualquiera)."""
    found: list[tuple[int, int, str]] = []
    for lbl in _LABELS:
        m = re.search(re.escape(lbl) + r"\s*:", seg)
        if m:
            found.append((m.start(), m.end(), lbl))
    found.sort()
    out: dict[str, str] = {}
    for i, (lstart, vstart, lbl) in enumerate(found):
        vend = found[i + 1][0] if i + 1 < len(found) else len(seg)
        out[lbl] = seg[vstart:vend].strip()
    return out


_PORTAL = {
    "csj": "https://consultajurisprudencial.ramajudicial.gov.co/WebRelatoria/csj/index.xhtml",
    "consejo_estado": "https://jurisprudencia.ramajudicial.gov.co/WebRelatoria/ce/index.xhtml",
}
_CORTE = {"csj": "csj", "consejo_estado": "ce"}


class WebRelatoriaDiscoverer(BaseDiscoverer):
    """Descubre providencias de CSJ / Consejo de Estado vía el buscador JSF."""

    PAGE_ROWS = 50

    def __init__(self, source: str, query: str = "derecho", max_docs: int | None = 200):
        if source not in _PORTAL:
            raise ValueError(f"WebRelatoria no cubre '{source}'. Opciones: {list(_PORTAL)}")
        self.source = source
        self.corte = _CORTE[source]
        self.url = _PORTAL[source]
        self.query = query
        self.max_docs = max_docs
        self.servlet = self.url.replace("/index.xhtml", "/FileReferenceServlet")

    # ── parsing de filas ─────────────────────────────────────────────────
    def _parse_rows(self, table_html: str) -> list[CatalogSeed]:
        import html as _html

        text = _html.unescape(re.sub(r"<[^>]+>", " ", table_html))
        text = re.sub(r"\s+", " ", text)
        seeds: list[CatalogSeed] = []
        # La sala precede al "ID:" → emparejar sala↔ID sobre el texto completo.
        sala_by_id: dict[str, str] = {}
        for m in re.finditer(
            r"(SALA\s+DE\s+[A-ZÁÉÍÓÚÑ ]+?)\s+(?:TUTELA\s+|ASUNTO\s+)?ID:\s*(\d+)", text
        ):
            sala_by_id[m.group(2)] = m.group(1).strip().title()
        # Cada fila empieza en "ID: <n>"; partimos por esas marcas.
        parts = re.split(r"(?=ID:\s*\d+)", text)
        for seg in parts:
            mid = re.search(r"ID:\s*(\d+)", seg)
            if not mid:
                continue
            doc_id = mid.group(1)
            f = _parse_segment(seg)
            providencia = f.get("NÚMERO DE PROVIDENCIA")
            proceso = f.get("NÚMERO DE PROCESO")
            fecha = f.get("FECHA")
            ponente = f.get("PONENTE")
            tipo_prov = f.get("TIPO DE PROVIDENCIA")
            sala = sala_by_id.get(doc_id)
            anio = None
            mf = re.search(r"/(\d{4})|\b(\d{4})\b", fecha or "")
            if mf:
                anio = mf.group(1) or mf.group(2)
            numero = providencia or doc_id
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

    # ── flujo JSF ────────────────────────────────────────────────────────
    def _search_payload(self, viewstate: str) -> dict[str, str]:
        collapsed = [
            "fulltxt", "ponente", "fecha", "radicado", "providencia", "id", "tipo",
            "clase", "fuente", "juris", "procedencia", "delitos", "sujetos",
            "servidor", "categoria",
        ]
        p = {
            "javax.faces.partial.ajax": "true",
            "javax.faces.source": "searchForm:searchButton",
            "javax.faces.partial.execute": "@all",
            "javax.faces.partial.render": "resultForm:jurisTable resultForm:pagText2 resultForm:selectAllButton",
            "searchForm:searchButton": "searchForm:searchButton",
            "searchForm": "searchForm",
            "searchForm:temaInput": self.query,
            "searchForm:scivil_focus": "", "searchForm:slaboral_focus": "",
            "searchForm:spenal_focus": "", "searchForm:splena_focus": "",
            "searchForm:relevanteselect": "", "searchForm:options1": "0",
            "searchForm:fechaIniCal": "", "searchForm:fechaFinCal": "",
            "javax.faces.ViewState": viewstate,
        }
        for c in collapsed:
            p[f"searchForm:{c}Input"] = ""
            p[f"searchForm:set-{c}_collapsed"] = "true"
        return p

    def _page_payload(self, viewstate: str, first: int) -> dict[str, str]:
        return {
            "javax.faces.partial.ajax": "true",
            "javax.faces.source": "resultForm:jurisTable",
            "javax.faces.partial.execute": "resultForm:jurisTable",
            "javax.faces.partial.render": "resultForm:jurisTable",
            "resultForm:jurisTable": "resultForm:jurisTable",
            "resultForm:jurisTable_pagination": "true",
            "resultForm:jurisTable_first": str(first),
            "resultForm:jurisTable_rows": str(self.PAGE_ROWS),
            "resultForm:jurisTable_encodeFeature": "true",
            "resultForm:jurisTable_skipChildren": "true",
            "resultForm": "resultForm",
            "javax.faces.ViewState": viewstate,
        }

    async def _run(self) -> tuple[list[CatalogSeed], int]:
        headers = {
            "User-Agent": "ScrapperLeyes/1.0 (investigacion academica)",
            "Faces-Request": "partial/ajax",
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        }
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True, verify=False) as c:
            r = await c.get(self.url, headers={"User-Agent": headers["User-Agent"]})
            m = _VIEWSTATE_RE.search(r.text)
            if not m:
                raise RuntimeError("No se obtuvo ViewState inicial de WebRelatoria")
            viewstate = m.group(1)

            r = await c.post(self.url, data=self._search_payload(viewstate), headers=headers)
            total = 0
            mp = _PAG_RE.search(r.text)
            if mp:
                mt = _TOTAL_RE.search(mp.group(1))
                if mt:
                    total = int(mt.group(1).replace(".", "").replace(",", ""))
            vm = _VIEWSTATE_PR_RE.search(r.text)
            if vm:
                viewstate = vm.group(1)

            seeds: dict[str, CatalogSeed] = {}
            mt = _TABLE_RE.search(r.text)
            if mt:
                for s in self._parse_rows(mt.group(1)):
                    seeds[s.external_id] = s

            # Paginación.
            first = self.PAGE_ROWS
            limit = self.max_docs if self.max_docs else total
            while len(seeds) < min(limit, total) and first < total:
                rp = await c.post(self.url, data=self._page_payload(viewstate, first), headers=headers)
                vm = _VIEWSTATE_PR_RE.search(rp.text)
                if vm:
                    viewstate = vm.group(1)
                mt = _TABLE_RE.search(rp.text)
                new = 0
                if mt:
                    for s in self._parse_rows(mt.group(1)):
                        if s.external_id not in seeds:
                            seeds[s.external_id] = s
                            new += 1
                if not new:
                    break
                first += self.PAGE_ROWS
            return list(seeds.values()), total

    def discover(
        self,
        *,
        desde: date | None = None,
        hasta: date | None = None,
        filtro: dict[str, Any] | None = None,
    ) -> Iterator[CatalogSeed]:
        seeds, total = asyncio.run(self._run())
        logger.info("[%s] %d providencias de %d totales (query='%s')",
                    self.source, len(seeds), total, self.query)
        for s in seeds:
            yield s
