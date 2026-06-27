"""Legal Mapper: converts sentencias (HTML/PDF) to Markdown via Docling,
extracts heuristic sections, and applies Regex NER for legal citations.
"""

from __future__ import annotations

import logging
import tempfile
import os
import re
from typing import Any

from scrapper_leyes.models import ParsedSentencia
from scrapper_leyes.sentencia_decision import parse_operative_orders
from scrapper_leyes.sentencia_sections import derive_legacy_buckets, split_sections

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
# Regex NER Legal – Citation extraction patterns
# ═══════════════════════════════════════════════════════════════════════════

# Pattern: "Ley 100 de 1993", "Ley 1712 de 2014", etc.
_RE_LEY = re.compile(
    r"(?<!decreto\s)\bley\s+(\d+)\s+de\s+(\d{4})\b",
    re.IGNORECASE,
)

# Pattern: "Artículo 13 de la Ley 1527 de 2012"
_RE_ART_LEY = re.compile(
    r"\bart[ií]culo\s+(\d+[a-z]?)\s+(?:de\s+la\s+)?(?<!decreto\s)ley\s+(\d+)\s+de\s+(\d{4})\b",
    re.IGNORECASE,
)

# Pattern: "Decreto 1075 de 2015", "Decreto Ley 2811 de 1974"
_RE_DECRETO = re.compile(
    r"\bdecreto\s+(?:ley\s+)?(\d+)\s+de\s+(\d{4})\b",
    re.IGNORECASE,
)

# Pattern: "Artículo 23 del Decreto 1075 de 2015"
_RE_ART_DECRETO = re.compile(
    r"\bart[ií]culo\s+(\d+[a-z]?)\s+del\s+decreto\s+(?:ley\s+)?(\d+)\s+de\s+(\d{4})\b",
    re.IGNORECASE,
)

# Pattern: "Sentencia C-274 de 2013", "Sentencia T-025 de 2004", "Sentencia SU-062 de 2019"
_RE_SENTENCIA = re.compile(
    r"\bsentencia\s+([CTASU]{1,2})-(\d+)\s+de\s+(\d{4})\b",
    re.IGNORECASE,
)

# Pattern: "Resolución 1234 de 2020"
_RE_RESOLUCION = re.compile(
    r"\bresoluci[oó]n\s+(\d+)\s+de\s+(\d{4})\b",
    re.IGNORECASE,
)

# Pattern: "Acto Legislativo 01 de 2003"
_RE_ACTO_LEG = re.compile(
    r"\bacto\s+legislativo\s+(\d+)\s+de\s+(\d{4})\b",
    re.IGNORECASE,
)

# Pattern: "Constitución Política", "artículo X de la Constitución"
_RE_CONSTITUCION = re.compile(
    r"\bart[ií]culo\s+(\d+)\s+(?:de\s+la\s+)?constituci[oó]n",
    re.IGNORECASE,
)

# Pattern: "Código Civil", "Código Penal", "Código de Procedimiento..."
_RE_CODIGO = re.compile(
    r"\bc[oó]digo\s+(civil|penal|de\s+procedimiento\s+\w+|sustantivo\s+del\s+trabajo|de\s+comercio|contencioso\s+administrativo|general\s+del\s+proceso)",
    re.IGNORECASE,
)


def extract_legal_citations(text: str) -> list[dict[str, str]]:
    """Extract structured legal citations from text using Regex NER.

    Returns a list of citation dicts with keys:
        - type: "ley", "decreto", "sentencia", "resolucion", "acto_legislativo",
                "constitucion", "codigo"
        - raw: the full matched text
        - numero: norm number
        - anio: year (if available)
        - articulo: article number (if available)
    """
    citations: list[dict[str, str]] = []
    seen: set[str] = set()
    covered_spans: list[tuple[int, int]] = []

    def is_covered(start: int, end: int) -> bool:
        for s, e in covered_spans:
            if start < e and end > s:
                return True
        return False

    def add_citation(m: re.Match, key: str, citation_dict: dict[str, str]):
        start, end = m.span()
        if not is_covered(start, end):
            covered_spans.append((start, end))
            if key not in seen:
                seen.add(key)
                citations.append(citation_dict)

    # Article + Ley citations (most specific first)
    for m in _RE_ART_LEY.finditer(text):
        add_citation(
            m,
            f"ley:{m.group(2)}:{m.group(3)}:art:{m.group(1)}",
            {
                "type": "ley",
                "raw": m.group(0),
                "numero": m.group(2),
                "anio": m.group(3),
                "articulo": m.group(1),
            }
        )

    # Standalone Ley citations
    for m in _RE_LEY.finditer(text):
        add_citation(
            m,
            f"ley:{m.group(1)}:{m.group(2)}",
            {
                "type": "ley",
                "raw": m.group(0),
                "numero": m.group(1),
                "anio": m.group(2),
            }
        )

    # Article + Decreto citations
    for m in _RE_ART_DECRETO.finditer(text):
        add_citation(
            m,
            f"decreto:{m.group(2)}:{m.group(3)}:art:{m.group(1)}",
            {
                "type": "decreto",
                "raw": m.group(0),
                "numero": m.group(2),
                "anio": m.group(3),
                "articulo": m.group(1),
            }
        )

    # Standalone Decreto citations
    for m in _RE_DECRETO.finditer(text):
        add_citation(
            m,
            f"decreto:{m.group(1)}:{m.group(2)}",
            {
                "type": "decreto",
                "raw": m.group(0),
                "numero": m.group(1),
                "anio": m.group(2),
            }
        )

    # Sentencia citations
    for m in _RE_SENTENCIA.finditer(text):
        prefix = m.group(1).upper()
        add_citation(
            m,
            f"sentencia:{prefix}-{m.group(2)}:{m.group(3)}",
            {
                "type": "sentencia",
                "raw": m.group(0),
                "numero": f"{prefix}-{m.group(2)}",
                "anio": m.group(3),
            }
        )

    # Resolución citations
    for m in _RE_RESOLUCION.finditer(text):
        add_citation(
            m,
            f"resolucion:{m.group(1)}:{m.group(2)}",
            {
                "type": "resolucion",
                "raw": m.group(0),
                "numero": m.group(1),
                "anio": m.group(2),
            }
        )

    # Acto Legislativo citations
    for m in _RE_ACTO_LEG.finditer(text):
        add_citation(
            m,
            f"acto_legislativo:{m.group(1)}:{m.group(2)}",
            {
                "type": "acto_legislativo",
                "raw": m.group(0),
                "numero": m.group(1),
                "anio": m.group(2),
            }
        )

    # Constitución citations (article-level)
    for m in _RE_CONSTITUCION.finditer(text):
        add_citation(
            m,
            f"constitucion:art:{m.group(1)}",
            {
                "type": "constitucion",
                "raw": m.group(0),
                "articulo": m.group(1),
            }
        )

    # Código citations
    for m in _RE_CODIGO.finditer(text):
        code_name = m.group(1).strip().lower()
        add_citation(
            m,
            f"codigo:{code_name}",
            {
                "type": "codigo",
                "raw": m.group(0),
                "nombre": code_name,
            }
        )

    return citations


def citations_to_strings(citations: list[dict[str, str]]) -> list[str]:
    """Convert structured citations to readable string list for backward compatibility."""
    result: list[str] = []
    for c in citations:
        if c["type"] == "ley":
            s = f"Ley {c['numero']} de {c.get('anio', '?')}"
            if "articulo" in c:
                s = f"Artículo {c['articulo']} de la {s}"
            result.append(s)
        elif c["type"] == "decreto":
            s = f"Decreto {c['numero']} de {c.get('anio', '?')}"
            if "articulo" in c:
                s = f"Artículo {c['articulo']} del {s}"
            result.append(s)
        elif c["type"] == "sentencia":
            result.append(f"Sentencia {c['numero']} de {c.get('anio', '?')}")
        elif c["type"] == "resolucion":
            result.append(f"Resolución {c['numero']} de {c.get('anio', '?')}")
        elif c["type"] == "acto_legislativo":
            result.append(f"Acto Legislativo {c['numero']} de {c.get('anio', '?')}")
        elif c["type"] == "constitucion":
            result.append(f"Artículo {c.get('articulo', '?')} de la Constitución")
        elif c["type"] == "codigo":
            result.append(f"Código {c.get('nombre', '?').title()}")
        else:
            result.append(c.get("raw", str(c)))
    return result


class LegalMapper:
    """
    Ingesta jurídica: convierte sentencias (HTML/PDF) a Markdown vía Docling,
    y extrae heurísticamente las partes clave de la providencia.
    Aplica Regex NER Legal para identificar citaciones cruzadas.
    """

    def __init__(self):
        self._converter = None

    @property
    def converter(self):
        """Lazy-load the Docling converter (heavy import)."""
        if self._converter is None:
            try:
                from docling.document_converter import DocumentConverter
                self._converter = DocumentConverter()
            except ImportError:
                logger.warning(
                    "Docling not installed – falling back to basic HTML extraction. "
                    "Install with: pip install docling"
                )
                self._converter = None
        return self._converter

    def process_html(self, html_content: bytes, source_id: str, catalog_match: dict[str, Any]) -> ParsedSentencia | None:
        """
        1. Uses Docling to convert HTML to Markdown (or falls back to basic extraction).
        2. Applies heuristics to map sections.
        3. Extracts legal citations via Regex NER.
        """
        md_text = self._convert_to_markdown(html_content, source_id)
        if md_text is None:
            return None

        # Parse sections and extract citations
        return self._map_sections(md_text, source_id, catalog_match)

    def _convert_to_markdown(self, html_content: bytes, source_id: str) -> str | None:
        """Convert HTML to Markdown, using Docling if available, fallback otherwise."""
        if self.converter is not None:
            return self._convert_with_docling(html_content, source_id)
        return self._convert_fallback(html_content, source_id)

    def _convert_with_docling(self, html_content: bytes, source_id: str) -> str | None:
        """Convert HTML to Markdown using Docling."""
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as tmp:
            tmp.write(html_content)
            tmp_path = tmp.name

        try:
            result = self.converter.convert(tmp_path)
            md = result.document.export_to_markdown()
            # Si Docling devuelve texto muy corto, probablemente falló silenciosamente
            # (p.ej. sentencias viejas con encoding latin-1 o HTML malformado).
            # Caer al fallback que es más robusto para estos casos.
            if not md or len(md.strip()) < 200:
                logger.warning(
                    "Docling returned thin content for %s (%d chars), using fallback",
                    source_id, len(md or ""),
                )
                return self._convert_fallback(html_content, source_id)
            return md
        except Exception as e:
            logger.error(f"Docling conversion failed for {source_id}: {e}")
            return self._convert_fallback(html_content, source_id)
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def _convert_fallback(self, html_content: bytes, source_id: str) -> str | None:
        """Basic HTML to text extraction when Docling is not available."""
        try:
            from bs4 import BeautifulSoup

            # Detectar encoding: intentamos utf-8 primero, luego latin-1
            # (sentencias viejas de la CC usan latin-1/iso-8859-1).
            text = None
            for enc in ("utf-8", "latin-1", "iso-8859-1", "cp1252"):
                try:
                    text = html_content.decode(enc)
                    # Si decode no falla pero hay muchos reemplazos, probar otro
                    if "\ufffd" not in text[:2000] or enc == "cp1252":
                        break
                except (UnicodeDecodeError, ValueError):
                    continue
            if text is None:
                text = html_content.decode("utf-8", errors="replace")

            soup = BeautifulSoup(text, "html.parser")

            # Remove scripts, styles, y elementos de navegación
            for tag in soup.find_all(["script", "style", "nav", "noscript"]):
                tag.decompose()

            # Get text with some structure preserved
            lines = []
            # Patrón para detectar headings que NO están en <h*> tags: numeración
            # romana o numeral seguido de título en mayúsculas
            # (típico de sentencias viejas: "VI. CONSIDERACIONES Y FUNDAMENTOS")
            _HEADING_LIKE_RE = re.compile(
                r"^(?:#{1,6}\s+)?"  # opcional markdown heading
                r"(?:[IVXLCDM]+|[0-9]+(?:\.[0-9]+)*|[A-Z][\)\.])"  # numeración
                r"[\.\)\-º°]*\s*"  # separador opcional (. ) - ) espacio
                r"([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ\s]{4,})",  # título en mayúsculas
                re.UNICODE,
            )
            for elem in soup.find_all(["h1", "h2", "h3", "h4", "h5", "p", "li", "td", "div"]):
                t = elem.get_text(strip=True)
                if t and len(t) > 2:
                    if elem.name and elem.name.startswith("h"):
                        level = int(elem.name[1])
                        lines.append(f"{'#' * level} {t}")
                    elif elem.name != "div":
                        # Detectar patrones tipo "VI. CONSIDERACIONES" o "1. ANTECEDENTES"
                        # que deberían ser headings aunque estén en <p>
                        m = _HEADING_LIKE_RE.match(t)
                        if m and len(t) < 80:
                            lines.append(f"## {t}")
                        else:
                            lines.append(t)

            result = "\n\n".join(lines) if lines else soup.get_text(separator="\n", strip=True)
            # Si después de todo no tenemos texto sustancial, devolver el texto crudo
            if len(result.strip()) < 50:
                result = soup.get_text(separator="\n", strip=True)
            return result if result.strip() else None
        except Exception as e:
            logger.error(f"Fallback HTML extraction failed for {source_id}: {e}")
            return None

    def _map_sections(self, md_text: str, source_id: str, catalog_match: dict[str, Any]) -> ParsedSentencia:
        """
        Heuristic extraction of Sentencia parts + Regex NER Legal citations.
        """
        # Defaults
        corte = catalog_match.get("corte", "Corte Constitucional")
        sala = "Plena"  # Default or regex extract
        magistrado_ponente = catalog_match.get("magistrado_ponente")

        # ── Extract Sala ───────────────────────────────────────────────────
        sala_match = re.search(
            r"(?:Sala\s+(?:de\s+)?(?:Casaci[oó]n\s+)?)"
            r"(Plena|Civil|Penal|Laboral|Primera|Segunda|Tercera|Cuarta|Quinta|Revisi[oó]n|Consulta)",
            md_text, re.IGNORECASE
        )
        if sala_match:
            sala = sala_match.group(1).strip()

        # ── Extract Magistrado Ponente ─────────────────────────────────────
        if not magistrado_ponente:
            m = re.search(
                r"(?:Magistrad[oa]\s+Ponente|M\.P\.|Consejero\s+Ponente)\s*[:\-]?\s*([^\n]+)",
                md_text, re.IGNORECASE,
            )
            if m:
                magistrado_ponente = m.group(1).replace("*", "").replace("_", "").strip()

        # ── Heading-driven sectionizer (replaces the old fragile regex) ────
        # Walks the markdown headings ("# VII. CONSIDERACIONES", "# VIII.
        # DECISIÓN") and normalizes each to a stable section type. The old
        # regex required the keyword immediately after the '#', so numbered
        # headings like "# VIII. DECISIÓN" never matched and the whole ruling
        # was dumped into `consideraciones`. The sections list is the new
        # source of truth; hechos/consideraciones/resuelve are derived from it.
        sections = split_sections(md_text)
        buckets = derive_legacy_buckets(sections)
        hechos = buckets["hechos"]
        consideraciones = buckets["consideraciones"]
        resuelve = buckets["resuelve"]

        # Parse the operative part into typed orders (EXEQUIBLE / INEXEQUIBLE /
        # …) with their controlled targets — drives typed graph edges & vigencia.
        orders = [o.to_dict() for o in parse_operative_orders(resuelve)]

        # ── Extract citations using Regex NER Legal ────────────────────────
        structured_citations = extract_legal_citations(md_text)
        citaciones_str = citations_to_strings(structured_citations)

        # Build ParsedSentencia
        sentencia = ParsedSentencia(
            suin_id=source_id,
            metadata={
                "tipo": catalog_match.get("tipo", "SENTENCIA"),
                "numero": catalog_match.get("numero", ""),
                "anio": catalog_match.get("anio", ""),
            },
            articles=[],
            modifications=[],
            jurisprudence=[],
            toc=[],
            corte=corte,
            sala=sala,
            magistrado_ponente=magistrado_ponente,
            hechos=hechos,
            consideraciones=consideraciones,
            resuelve=resuelve,
            sections=[s.to_dict() for s in sections],
            orders=orders,
            citaciones=citaciones_str,
            # El texto íntegro vive en hechos/consideraciones/resuelve; raw_text
            # es solo un respaldo. No lo recortamos a 5000 (eso hacía que el front
            # mostrara las sentencias "cortadas") — guardamos el markdown completo.
            raw_text=md_text,
        )
        return sentencia
