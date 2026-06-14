"""Resolver de vigencia temporal.

Dada una norma/artículo (y opcionalmente una fecha), responde:
  * ¿Está vigente, derogado, modificado, inexequible o condicionado?
  * ¿Cuál es el texto operante (la versión que aplica a esa fecha)?
  * ¿Qué lo afectó (normas y sentencias), con qué tipo y desde cuándo?

Es la pieza de correctitud #1 para un sistema de IA jurídica: sin esto la IA
puede citar texto derogado o una norma declarada inexequible. Reutiliza los datos
ya parseados (`modifications`, `jurisprudence`, `previous_versions`) y es el núcleo
de la futura herramienta MCP ``texto_vigente(canonical_id, fecha)``.

Módulo puro (datetime + re + models), testeable sin dependencias pesadas.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from scrapper_leyes.models import AffectationType, normalize_article_number

# Estados de vigencia (orden de severidad para resolver conflictos).
ESTADO_INEXEQUIBLE = "inexequible"
ESTADO_DEROGADO = "derogado"
ESTADO_SUSPENDIDO = "suspendido"
ESTADO_CONDICIONADA = "exequible_condicionada"
ESTADO_MODIFICADO = "modificado"
ESTADO_VIGENTE = "vigente"
ESTADO_DESCONOCIDO = "desconocido"

_WHOLE_DOC_MARKERS = (
    "documento completo",
    "toda la norma",
    "la norma",
    "todo el documento",
)

_MODIFICA_TYPES = {
    AffectationType.MODIFICA.value,
    AffectationType.ADICIONA.value,
    AffectationType.SUSTITUYE.value,
    AffectationType.CORRIGE_YERRO.value,
    AffectationType.DEROGA_PARCIAL.value,
    AffectationType.COMPLEMENTA.value,
}


# ── Fechas ───────────────────────────────────────────────────────────────────

_DATE_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")


def parse_fecha(s: str | None) -> date | None:
    """Parsea una fecha DD/MM/YYYY (tolerante a espacios/texto alrededor)."""
    if not s:
        return None
    m = _DATE_RE.search(s)
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def parse_date_range(s: str | None) -> tuple[date | None, date | None]:
    """De 'Vigente desde: 03/10/2007 y hasta el: 05/01/2011' → (desde, hasta)."""
    if not s:
        return (None, None)
    fechas = [
        date(int(y), int(mo), int(d))
        for d, mo, y in _DATE_RE.findall(s)
        if _valid(int(y), int(mo), int(d))
    ]
    if not fechas:
        return (None, None)
    if len(fechas) == 1:
        return (fechas[0], None)
    return (fechas[0], fechas[-1])


def _valid(y: int, mo: int, d: int) -> bool:
    try:
        date(y, mo, d)
        return True
    except ValueError:
        return False


# ── Estructuras ──────────────────────────────────────────────────────────────


@dataclass
class Afectacion:
    tipo: str  # normalized_type
    raw: str
    fuente: str  # source_text
    fuente_id: str | None
    contexto: str | None
    ambito: str  # "documento" | "Artículo N"


@dataclass
class VersionTexto:
    texto: str
    desde: date | None
    hasta: date | None
    vigente: bool  # ¿es la versión actual?

    def to_dict(self) -> dict[str, Any]:
        return {
            "texto": self.texto,
            "desde": self.desde.isoformat() if self.desde else None,
            "hasta": self.hasta.isoformat() if self.hasta else None,
            "vigente": self.vigente,
        }


@dataclass
class VigenciaReport:
    canonical_id: str
    nivel: str  # "norma" | "articulo"
    estado: str
    vigente: bool
    motivo: str
    afectaciones: list[Afectacion] = field(default_factory=list)
    jurisprudencia: list[Afectacion] = field(default_factory=list)
    texto_aplicable: str | None = None
    texto_es_vigente: bool = True
    fecha_consulta: str | None = None
    versiones: list[VersionTexto] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_id": self.canonical_id,
            "nivel": self.nivel,
            "estado": self.estado,
            "vigente": self.vigente,
            "motivo": self.motivo,
            "fecha_consulta": self.fecha_consulta,
            "texto_aplicable": self.texto_aplicable,
            "texto_es_vigente": self.texto_es_vigente,
            "afectaciones": [a.__dict__ for a in self.afectaciones],
            "jurisprudencia": [j.__dict__ for j in self.jurisprudencia],
            "versiones": [v.to_dict() for v in self.versiones],
        }


# ── Matching de afectaciones por artículo ────────────────────────────────────


def _is_whole_doc(article_affected: str) -> bool:
    low = (article_affected or "").strip().lower()
    return not low or any(m in low for m in _WHOLE_DOC_MARKERS)


def _affects_article(article_affected: str, art_num_norm: str | None) -> bool:
    """¿La afectación recae sobre este artículo (o sobre todo el documento)?"""
    if _is_whole_doc(article_affected):
        return True
    if not art_num_norm:
        return False
    return normalize_article_number(article_affected) == art_num_norm


def _to_afectacion(raw: dict[str, Any]) -> Afectacion:
    affected = (raw.get("article_affected") or "").strip()
    return Afectacion(
        tipo=raw.get("normalized_type") or AffectationType.UNKNOWN.value,
        raw=raw.get("raw_type", ""),
        fuente=raw.get("source_text", ""),
        fuente_id=raw.get("source_suin_id"),
        contexto=raw.get("context"),
        ambito="documento" if _is_whole_doc(affected) else affected,
    )


# ── Clasificación de estado ──────────────────────────────────────────────────


def _classify(
    mod_types: set[str],
    jur: list[Afectacion],
    norm_vigencia: str | None,
) -> tuple[str, bool, str]:
    """Devuelve (estado, vigente, motivo) aplicando prioridad de severidad."""
    jur_types = {j.tipo for j in jur}
    nv = (norm_vigencia or "").strip().lower()

    def _src(t: str) -> str:
        for j in jur:
            if j.tipo == t:
                return j.fuente
        return ""

    # 1. Inexequibilidad: la norma sale del ordenamiento (cosa juzgada).
    if AffectationType.INEXEQUIBLE.value in jur_types:
        return (ESTADO_INEXEQUIBLE, False,
                f"Declarado inexequible por {_src(AffectationType.INEXEQUIBLE.value)}".strip())
    # 2. Derogatoria total (o norma derogada a nivel documento).
    if AffectationType.DEROGA_TOTAL.value in mod_types:
        return (ESTADO_DEROGADO, False, "Derogado totalmente")
    if nv.startswith("derogad"):
        return (ESTADO_DEROGADO, False, "Norma derogada")
    # 3. Suspensión.
    if AffectationType.SUSPENDE.value in mod_types:
        return (ESTADO_SUSPENDIDO, False, "Suspendido")
    # 4. Exequibilidad condicionada: vigente, pero solo bajo la interpretación
    #    fijada por la Corte.
    if AffectationType.EXEQUIBLE_CONDICIONADA.value in jur_types:
        return (ESTADO_CONDICIONADA, True,
                f"Exequible de forma condicionada por "
                f"{_src(AffectationType.EXEQUIBLE_CONDICIONADA.value)}".strip())
    # 5. Modificado / adicionado / derogado parcialmente: sigue vigente.
    if mod_types & _MODIFICA_TYPES:
        return (ESTADO_MODIFICADO, True, "Vigente con modificaciones")
    # 6. Vigente / desconocido según el catálogo.
    if nv.startswith("vigente"):
        return (ESTADO_VIGENTE, True, "Vigente")
    return (ESTADO_DESCONOCIDO, True, "Estado de vigencia no determinado")


# ── Versiones temporales ─────────────────────────────────────────────────────


def _build_versions(article: dict[str, Any]) -> list[VersionTexto]:
    """Versiones ordenadas cronológicamente (anteriores + la actual)."""
    versions: list[VersionTexto] = []
    for pv in article.get("previous_versions", []):
        desde, hasta = parse_date_range(pv.get("date_range"))
        versions.append(VersionTexto(texto=pv.get("text", ""), desde=desde, hasta=hasta, vigente=False))
    versions.sort(key=lambda v: (v.desde or date.min))
    # La versión actual arranca donde terminó la última anterior.
    inicio_actual = None
    if versions:
        ends = [v.hasta for v in versions if v.hasta]
        inicio_actual = max(ends) if ends else None
    versions.append(
        VersionTexto(texto=article.get("text", ""), desde=inicio_actual, hasta=None, vigente=True)
    )
    return versions


def _select_version(versions: list[VersionTexto], as_of: date | None) -> VersionTexto:
    if as_of is None:
        return versions[-1]  # la actual
    for v in versions:
        if (v.desde is None or as_of >= v.desde) and (v.hasta is None or as_of <= v.hasta):
            return v
    return versions[-1]


# ── API pública ──────────────────────────────────────────────────────────────


def resolve_article(
    article: dict[str, Any],
    modifications: list[dict[str, Any]],
    jurisprudence: list[dict[str, Any]],
    norm_vigencia: str | None,
    as_of: date | None = None,
) -> VigenciaReport:
    """Resuelve la vigencia de un artículo (estado + texto aplicable + historia)."""
    art_num = article.get("number_normalized")
    cid = article.get("canonical_id", "")

    afect = [_to_afectacion(m) for m in modifications if _affects_article(m.get("article_affected", ""), art_num)]
    # Las afectaciones salientes propias (lo que ESTE artículo deroga) no cambian
    # su vigencia; las entrantes (modifications) sí.
    jur = [_to_afectacion(j) for j in jurisprudence if _affects_article(j.get("article_affected", ""), art_num)]

    estado, vigente, motivo = _classify({a.tipo for a in afect}, jur, norm_vigencia)

    # Tener versiones anteriores implica que el texto fue modificado, aunque la
    # afectación estructurada no se haya cruzado por número.
    if estado in (ESTADO_VIGENTE, ESTADO_DESCONOCIDO) and article.get("previous_versions"):
        estado, motivo = ESTADO_MODIFICADO, "Vigente con modificaciones"

    versions = _build_versions(article)
    chosen = _select_version(versions, as_of)

    return VigenciaReport(
        canonical_id=cid,
        nivel="articulo",
        estado=estado,
        vigente=vigente,
        motivo=motivo,
        afectaciones=afect,
        jurisprudencia=jur,
        texto_aplicable=chosen.texto,
        texto_es_vigente=chosen.vigente,
        fecha_consulta=as_of.isoformat() if as_of else None,
        versiones=versions,
    )


def resolve_norm(
    parsed: dict[str, Any],
    catalog: dict[str, Any],
    as_of: date | None = None,
) -> VigenciaReport:
    """Resuelve la vigencia a nivel documento (norma completa)."""
    norm_vigencia = catalog.get("suin_vigencia") or catalog.get("vigencia")
    cid = catalog.get("canonical_id", "")

    # A nivel norma cuentan las afectaciones de documento completo.
    afect = [
        _to_afectacion(m)
        for m in parsed.get("modifications", [])
        if _is_whole_doc(m.get("article_affected", ""))
    ]
    jur = [
        _to_afectacion(j)
        for j in parsed.get("jurisprudence", [])
        if _is_whole_doc(j.get("article_affected", ""))
    ]
    estado, vigente, motivo = _classify({a.tipo for a in afect}, jur, norm_vigencia)

    return VigenciaReport(
        canonical_id=cid,
        nivel="norma",
        estado=estado,
        vigente=vigente,
        motivo=motivo,
        afectaciones=afect,
        jurisprudencia=jur,
        fecha_consulta=as_of.isoformat() if as_of else None,
    )


def resolve(
    parsed: dict[str, Any],
    catalog: dict[str, Any],
    art_ref: str | None = None,
    fecha: str | date | None = None,
) -> VigenciaReport | None:
    """Punto de entrada. ``art_ref`` = número normalizado de artículo (p.ej. '5',
    '5a', 'trans:1'); si es None, resuelve la norma completa. ``fecha`` puede ser
    'DD/MM/YYYY', 'YYYY-MM-DD' o un date.
    """
    as_of: date | None
    if isinstance(fecha, date):
        as_of = fecha
    elif isinstance(fecha, str):
        as_of = parse_fecha(fecha) or _parse_iso(fecha)
    else:
        as_of = None

    if art_ref is None:
        return resolve_norm(parsed, catalog, as_of)

    for art in parsed.get("articles", []):
        if art.get("number_normalized") == art_ref:
            return resolve_article(
                art,
                parsed.get("modifications", []),
                parsed.get("jurisprudence", []),
                catalog.get("suin_vigencia") or catalog.get("vigencia"),
                as_of,
            )
    return None


def _parse_iso(s: str) -> date | None:
    try:
        return datetime.strptime(s.strip(), "%Y-%m-%d").date()
    except (ValueError, AttributeError):
        return None
