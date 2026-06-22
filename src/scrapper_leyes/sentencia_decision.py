"""Parse a sentencia's operative part (RESUELVE) into typed orders.

Why this module exists
----------------------
The decision (parte resolutiva) is the highest-value, most structured piece of
a ruling: each numeral ("Primero.- Declarar EXEQUIBLE el artículo 18…") is a
discrete order with a *decision type* and one or more *targets* (norms it
controls). Today that text is stored as one opaque blob.

Turning each numeral into an :class:`OrderDecision` lets us:
  * label resuelve chunks with ``decision_type`` / ``order_number`` instead of
    a generic "Resuelve (3/58)";
  * emit typed graph edges (DECLARA_EXEQUIBLE / DECLARA_INEXEQUIBLE / …) from
    the sentencia to the disposición it controls — the OUTGOING direction that
    SUIN's jurisprudence backlinks do not cover for relatoría-scraped rulings;
  * feed the vigencia engine directly from the ruling that produced the effect.

Pure function, regex-based, no heavy deps — cheap to unit-test.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

# ── Decision types (stable vocabulary) ───────────────────────────────────────
# Constitutionality control (these feed the vigencia engine):
EXEQUIBLE = "EXEQUIBLE"
EXEQUIBLE_CONDICIONADA = "EXEQUIBLE_CONDICIONADA"
INEXEQUIBLE = "INEXEQUIBLE"
INHIBIDA = "INHIBIDA"
ESTARSE_A_LO_RESUELTO = "ESTARSE_A_LO_RESUELTO"
NULIDAD = "NULIDAD"
# Tutela / amparo:
CONCEDE_TUTELA = "CONCEDE_TUTELA"
NIEGA_TUTELA = "NIEGA_TUTELA"
CONFIRMA = "CONFIRMA"
REVOCA = "REVOCA"
MODIFICA = "MODIFICA"
OTRO = "OTRO"

# Spanish ordinal words → number, for "Primero", "Segundo", … (decisions rarely
# exceed ~20 numerals). Accents are stripped before lookup.
_ORDINALS = {
    "primero": 1, "segundo": 2, "tercero": 3, "cuarto": 4, "quinto": 5,
    "sexto": 6, "septimo": 7, "octavo": 8, "noveno": 9, "decimo": 10,
    "undecimo": 11, "duodecimo": 12, "decimoprimero": 11, "decimosegundo": 12,
    "decimotercero": 13, "decimocuarto": 14, "decimoquinto": 15,
    "decimosexto": 16, "decimoseptimo": 17, "decimoctavo": 18,
    "decimonoveno": 19, "vigesimo": 20,
}

# Numeral start: line beginning with an ordinal word OR a number, optionally
# bold-wrapped, followed by a separator (".", ".-", ":", ")"). The DOTALL body
# is sliced between consecutive matches by the caller.
_ORDINAL_ALT = "|".join(sorted(_ORDINALS, key=len, reverse=True))
_NUMERAL_RE = re.compile(
    r"(?im)^\s*\**\s*(" + _ORDINAL_ALT + r"|[0-9]{1,2})\s*\**\s*[\.\)\:\-]",
)

# Decision-type rules, evaluated in order (most specific first). Patterns run
# against the accent-stripped, lowercased order text.
_DECISION_RULES: list[tuple[str, re.Pattern[str]]] = [
    (ESTARSE_A_LO_RESUELTO, re.compile(r"est[ae]rse?\s+a\s+lo\s+resuelto|estese\s+a\s+lo\s+resuelto")),
    (INEXEQUIBLE, re.compile(r"inexequib")),
    (EXEQUIBLE_CONDICIONADA, re.compile(r"exequib\w*\s+condicional|condicionalmente\s+exequib|exequib.*(?:en\s+el\s+entendido|bajo\s+el\s+entendido|siempre\s+y\s+cuando|siempre\s+que|en\s+el\s+sentido\s+de)")),
    (EXEQUIBLE, re.compile(r"exequib")),
    (INHIBIDA, re.compile(r"inhib")),
    (NULIDAD, re.compile(r"\bnulidad\b|declar\w*\s+(?:la\s+)?nul")),
    (CONCEDE_TUTELA, re.compile(r"\b(conceder|tutelar|amparar|proteger)\b")),
    (NIEGA_TUTELA, re.compile(r"\b(negar|denegar)\b")),
    (REVOCA, re.compile(r"\brevoc")),
    (CONFIRMA, re.compile(r"\bconfirm")),
    (MODIFICA, re.compile(r"\bmodific")),
]

# Scope of res judicata ("por los cargos analizados" limits the cosa juzgada).
_SCOPE_RE = re.compile(
    r"por\s+(?:el\s+cargo|los\s+cargos)\s+(?:analizad\w+|examinad\w+|estudiad\w+)"
    r"|en\s+relaci[oó]n\s+con\s+(?:el\s+cargo|los\s+cargos)",
    re.IGNORECASE,
)
# Conditioning clause for EXEQUIBLE_CONDICIONADA.
_CONDICION_RE = re.compile(
    r"(en\s+el\s+entendido\s+(?:de\s+)?que|bajo\s+el\s+entendido\s+(?:de\s+)?que|"
    r"siempre\s+y\s+cuando|siempre\s+que)\s+(.+?)(?:\.\s|$)",
    re.IGNORECASE | re.DOTALL,
)


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


@dataclass
class OrderDecision:
    """One numeral of the parte resolutiva."""

    order_number: int | None  # 1, 2, 3 … (from the ordinal word/number)
    ordinal_label: str  # "Primero", "Segundo", "1" …
    decision_type: str  # EXEQUIBLE / INEXEQUIBLE / …
    text: str  # full text of the numeral
    scope: str | None = None  # "por los cargos analizados"
    condicion: str | None = None  # conditioning clause for EXEQUIBLE_CONDICIONADA
    targets: list[dict[str, Any]] = field(default_factory=list)  # structured citations

    def to_dict(self) -> dict[str, Any]:
        return {
            "order_number": self.order_number,
            "ordinal_label": self.ordinal_label,
            "decision_type": self.decision_type,
            "text": self.text,
            "scope": self.scope,
            "condicion": self.condicion,
            "targets": self.targets,
        }


def classify_decision(text: str) -> str:
    """Return the decision type for a single order's text."""
    norm = _strip_accents(text or "").lower()
    for label, pattern in _DECISION_RULES:
        if pattern.search(norm):
            return label
    return OTRO


def _resolve_targets(text: str) -> list[dict[str, Any]]:
    """Structured citations (norms/sentencias) named inside the order text."""
    # Lazy import: legal_mapper's heavy deps (docling) are imported lazily
    # inside its methods, so importing the citation helper is cheap.
    from scrapper_leyes.scraper.legal_mapper import extract_legal_citations

    return extract_legal_citations(text or "")


def parse_operative_orders(resuelve_text: str) -> list[OrderDecision]:
    """Split the RESUELVE text into ordered, typed :class:`OrderDecision`s.

    Returns ``[]`` if no numerals are found (e.g. an empty or malformed
    resuelve). Falls back to a single OTRO order covering the whole text only
    when there is clearly decision content but no recognizable numbering is a
    deliberate non-goal — callers can inspect ``[]`` and keep the raw text.
    """
    text = (resuelve_text or "").strip()
    if not text:
        return []

    matches = list(_NUMERAL_RE.finditer(text))
    if not matches:
        return []

    orders: list[OrderDecision] = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        raw_label = m.group(1)
        key = _strip_accents(raw_label).lower()
        if key in _ORDINALS:
            number = _ORDINALS[key]
            ordinal_label = raw_label.strip().capitalize()
        elif key.isdigit():
            number = int(key)
            ordinal_label = key
        else:
            number = None
            ordinal_label = raw_label.strip()

        decision_type = classify_decision(body)
        scope_m = _SCOPE_RE.search(body)
        scope = scope_m.group(0).strip() if scope_m else None
        condicion = None
        if decision_type == EXEQUIBLE_CONDICIONADA:
            cond_m = _CONDICION_RE.search(body)
            if cond_m:
                condicion = cond_m.group(2).strip()[:500]

        orders.append(
            OrderDecision(
                order_number=number,
                ordinal_label=ordinal_label,
                decision_type=decision_type,
                text=body,
                scope=scope,
                condicion=condicion,
                targets=_resolve_targets(body),
            )
        )
    return orders
