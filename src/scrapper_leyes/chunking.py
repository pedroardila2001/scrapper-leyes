"""Coherent structural chunking for the legal corpus.

Single source of truth for turning a parsed norm / sentencia (parsed.json)
into retrieval chunks. Used by both the Qdrant exporter (`export_vector.py`)
and the dashboard `/api/norms/{id}/vectors` endpoint, so the agent retrieves
exactly what the dashboard shows.

Design goals (why this module exists):
  * Never truncate with ``text[:2000]`` — long articles are *split*, not cut.
  * Chunk on legal-structural boundaries (artículo → parágrafo/inciso), not
    arbitrary character windows, with overlap so context is not lost at seams.
  * Prepend a context header ("Ley 1712 de 2014 · Artículo 5 — Objeto:") to
    each chunk so the embedding carries provenance, not just bare body text.
  * Carry *vigencia* (estado de vigencia) into every chunk so a deep-agent can
    filter out derogated / superseded law — the single most important
    correctness signal in legal research.
  * Deterministic chunk IDs (uuid5 of canonical_id + index) so re-exports
    upsert in place instead of duplicating.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from scrapper_leyes.models import (
    AffectationType,
    TIPO_CANONICAL,
    build_canonical_id,
    normalize_article_number,
)

# ── Tunables (overridable by callers) ───────────────────────────────────────

# Target body size for a single chunk, in characters. ~4 chars/token, so
# 2400 chars ≈ 600 tokens — comfortably inside bge-m3's 8192-token window while
# keeping chunks focused enough for precise retrieval.
DEFAULT_MAX_CHARS = 2400
# Overlap carried from the tail of the previous chunk into the next one, so a
# clause split across a seam is still recoverable on both sides.
DEFAULT_OVERLAP_CHARS = 200

# Deterministic namespace for chunk UUIDs (frozen — do not change or IDs churn).
CHUNK_NAMESPACE = uuid.UUID("6f6c6579-6573-4c45-4759-455343485348")

# Affectation types that, when present on an article, mean it is no longer the
# operative text (fully repealed).
_DEROGA_TOTAL = {AffectationType.DEROGA_TOTAL}
_DEROGA_PARCIAL = {AffectationType.DEROGA_PARCIAL}
# Types that change the article's text but leave it (partly) in force.
_MODIFICA = {
    AffectationType.MODIFICA,
    AffectationType.ADICIONA,
    AffectationType.SUSTITUYE,
    AffectationType.CORRIGE_YERRO,
    AffectationType.COMPLEMENTA,
}

# article_affected strings that target the whole norm rather than one article.
_WHOLE_DOC_MARKERS = (
    "documento completo",
    "toda la norma",
    "la norma",
    "todo el documento",
)

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.;:])\s+")


# ── Chunk container ─────────────────────────────────────────────────────────


@dataclass
class Chunk:
    """One retrieval unit ready to embed and upsert."""

    uid: str  # deterministic UUID (string) — Qdrant point id
    canonical_id: str  # article-level when possible, else norm-level
    norm_canonical_id: str
    section: str  # human label, e.g. "Artículo 5°" or "Considerandos (2/3)"
    title: str | None
    text: str  # context header + body — THIS is what gets embedded
    body: str  # body only, for display
    chunk_index: int  # index within its parent unit
    n_chunks: int  # total chunks in the parent unit
    payload: dict[str, Any] = field(default_factory=dict)

    def to_api_dict(self, chunk_id: int) -> dict[str, Any]:
        """Backward-compatible shape for the dashboard /vectors endpoint."""
        p = self.payload
        return {
            "chunk_id": chunk_id,
            "uid": self.uid,
            "section": self.section,
            "canonical_id": self.canonical_id,
            "title": self.title or "",
            "text": self.body,
            "char_count": len(self.body),
            "chunk_index": self.chunk_index,
            "n_chunks": self.n_chunks,
            "tipo": p.get("tipo"),
            "numero": p.get("numero"),
            "anio": p.get("anio"),
            "corte": p.get("corte"),
            "magistrado": p.get("magistrado"),
            "estado_vigencia": p.get("estado_vigencia"),
            "derogado": p.get("derogado"),
            "modificado": p.get("modificado"),
            "afectaciones": p.get("afectaciones", []),
        }


# ── Text splitting ──────────────────────────────────────────────────────────


def estimate_tokens(text: str) -> int:
    """Cheap token estimate (~4 chars/token) — avoids a tokenizer dependency."""
    return max(1, len(text) // 4)


def _hard_window(text: str, max_chars: int, overlap: int) -> list[str]:
    """Last-resort splitter for a single oversized, unsplittable string."""
    out: list[str] = []
    start = 0
    n = len(text)
    step = max(1, max_chars - overlap)
    while start < n:
        out.append(text[start : start + max_chars].strip())
        start += step
    return [c for c in out if c]


def split_text(
    text: str,
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap: int = DEFAULT_OVERLAP_CHARS,
) -> list[str]:
    """Split ``text`` into coherent pieces, never mid-clause when avoidable.

    Strategy, in order of preference:
      1. Keep whole paragraphs (parsed.json joins incisos/parágrafos with
         ``\\n\\n``, so paragraphs already map to legal sub-units).
      2. Greedily pack paragraphs until ``max_chars`` would be exceeded.
      3. A paragraph that is itself too long is split on sentence boundaries.
      4. A single sentence longer than ``max_chars`` falls back to a char
         window. Consecutive chunks share ``overlap`` characters of tail.

    Returns ``[]`` for empty input and ``[text]`` when it already fits.
    """
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    # 1–2: explode into atomic units no larger than max_chars.
    units: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        if len(para) <= max_chars:
            units.append(para)
            continue
        # 3: sentence-level split for an oversized paragraph.
        buf = ""
        for sent in _SENTENCE_SPLIT_RE.split(para):
            sent = sent.strip()
            if not sent:
                continue
            if len(sent) > max_chars:
                if buf:
                    units.append(buf)
                    buf = ""
                units.extend(_hard_window(sent, max_chars, overlap))  # 4
                continue
            if buf and len(buf) + 1 + len(sent) > max_chars:
                units.append(buf)
                buf = sent
            else:
                buf = f"{buf} {sent}".strip()
        if buf:
            units.append(buf)

    # Greedy-pack units into chunks, carrying an overlap tail across seams.
    chunks: list[str] = []
    current = ""
    for unit in units:
        if current and len(current) + 2 + len(unit) > max_chars:
            chunks.append(current)
            tail = current[-overlap:] if overlap else ""
            # Start next chunk from a clean boundary inside the tail.
            if tail:
                cut = tail.find(" ")
                tail = tail[cut + 1 :] if cut != -1 else ""
            current = f"{tail}\n\n{unit}".strip() if tail else unit
        else:
            current = f"{current}\n\n{unit}".strip() if current else unit
    if current:
        chunks.append(current)
    return chunks


# ── Vigencia computation ────────────────────────────────────────────────────


def _affectation_index(modifications: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Map normalized article ref → affectations targeting it.

    Whole-document affectations are filed under the special key ``"*"``.
    """
    index: dict[str, list[dict[str, Any]]] = {}
    for mod in modifications or []:
        affected = (mod.get("article_affected") or "").strip()
        key: str | None
        low = affected.lower()
        if not affected or any(m in low for m in _WHOLE_DOC_MARKERS):
            key = "*"
        else:
            key = normalize_article_number(affected) or affected.lower()
        index.setdefault(key, []).append(mod)
    return index


def _vigencia_for(
    affectations: list[dict[str, Any]],
    norm_vigencia: str | None,
) -> dict[str, Any]:
    """Derive an article's vigencia state from the affectations touching it."""
    types: set[str] = set()
    compact: list[dict[str, str]] = []
    for a in affectations:
        t = a.get("normalized_type") or AffectationType.UNKNOWN.value
        types.add(t)
        compact.append(
            {
                "tipo": t,
                "raw": a.get("raw_type", ""),
                "fuente": a.get("source_text", ""),
                "fuente_id": a.get("source_suin_id") or "",
            }
        )

    derogado = AffectationType.DEROGA_TOTAL.value in types
    derogado_parcial = AffectationType.DEROGA_PARCIAL.value in types
    modificado = bool(types & {t.value for t in _MODIFICA})

    if derogado:
        estado = "derogado"
    elif (norm_vigencia or "").strip().lower().startswith("derogad"):
        # Norm-level repeal cascades to articles with no specific note.
        estado = "derogado"
        derogado = True
    elif derogado_parcial or modificado:
        estado = "modificado"
    elif norm_vigencia:
        estado = "vigente" if norm_vigencia.strip().lower().startswith("vigente") else "desconocido"
    else:
        estado = "desconocido"

    return {
        "estado_vigencia": estado,
        "derogado": derogado,
        "derogado_parcial": derogado_parcial,
        "modificado": modificado,
        "afectaciones": compact,
    }


# ── Context headers & labels ────────────────────────────────────────────────


def _tipo_label(tipo: str) -> str:
    """Human label for a norm type ('LEY' → 'Ley')."""
    if not tipo:
        return "Norma"
    return tipo.capitalize() if tipo.isupper() else tipo


def _norm_label(catalog: dict[str, Any]) -> str:
    tipo = _tipo_label(catalog.get("tipo", ""))
    numero = catalog.get("numero", "")
    anio = catalog.get("anio", "")
    if catalog.get("tipo") == "SENTENCIA":
        corte = (catalog.get("corte") or "").upper()
        base = f"Sentencia {numero} de {anio}"
        return f"{base} ({corte})" if corte else base
    return f"{tipo} {numero} de {anio}".strip()


def _display_num(number: str, num_norm: str | None) -> str:
    """Clean article label: 'Artículo 1°.' / 'trans:1' → '1' / 'Transitorio 1'."""
    if num_norm:
        if num_norm.startswith("trans:"):
            return f"Transitorio {num_norm.split(':', 1)[1]}"
        return num_norm.upper() if num_norm[-1].isalpha() else num_norm
    # Fall back to stripping the "Artículo" prefix and ordinal punctuation.
    cleaned = re.sub(r"^\s*art[ií]culo\s+", "", number or "", flags=re.IGNORECASE)
    return cleaned.strip().rstrip("°ºo.").strip() or (number or "?")


def _article_header(catalog: dict[str, Any], num_display: str, title: str | None) -> str:
    label = _norm_label(catalog)
    head = f"{label} · Artículo {num_display}".rstrip()
    if title:
        head += f" — {title}"
    return head + ":"


# ── Public chunking API ─────────────────────────────────────────────────────


def chunk_document(
    parsed: dict[str, Any],
    catalog: dict[str, Any],
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap: int = DEFAULT_OVERLAP_CHARS,
) -> list[Chunk]:
    """Dispatch on norm type and return retrieval chunks."""
    if catalog.get("tipo") == "SENTENCIA":
        return chunk_sentencia(parsed, catalog, max_chars=max_chars, overlap=overlap)
    return chunk_norm(parsed, catalog, max_chars=max_chars, overlap=overlap)


def chunk_norm(
    parsed: dict[str, Any],
    catalog: dict[str, Any],
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap: int = DEFAULT_OVERLAP_CHARS,
) -> list[Chunk]:
    """Chunk a law/decree: one+ chunk per article, vigencia-annotated."""
    tipo = catalog.get("tipo", "")
    numero = catalog.get("numero", "")
    anio = catalog.get("anio", "")
    norm_cid = build_canonical_id(tipo, str(numero), str(anio))
    norm_vigencia = catalog.get("suin_vigencia") or catalog.get("vigencia")

    aff_index = _affectation_index(parsed.get("modifications", []))
    whole_doc_aff = aff_index.get("*", [])

    base_payload = {
        "tipo": tipo,
        "numero": numero,
        "anio": anio,
        "sector": catalog.get("sector"),
        "entidad": catalog.get("entidad"),
        "materia": catalog.get("materia"),
        "suin_id": catalog.get("suin_id") or parsed.get("suin_id"),
        "norm_canonical_id": norm_cid,
        "norm_vigencia": norm_vigencia,
        "corte": None,
        "magistrado": None,
    }

    chunks: list[Chunk] = []
    seen_cids: set[str] = set()
    for art in parsed.get("articles", []):
        body = (art.get("text") or "").strip()
        if not body:
            continue
        # Defensive dedup: SUIN anchors some articles twice → the parser may
        # emit the same article (same canonical_id) more than once. Keep first.
        _cid_check = art.get("canonical_id") or art.get("art_id") or art.get("number")
        if _cid_check in seen_cids:
            continue
        seen_cids.add(_cid_check)
        number = art.get("number", "?")
        num_norm = art.get("number_normalized")
        art_id = art.get("art_id") or ""
        title = (art.get("title") or "").strip() or None
        art_cid = art.get("canonical_id") or (
            build_canonical_id(tipo, str(numero), str(anio), art=num_norm)
            if num_norm
            else f"{norm_cid}:art:{number}"
        )
        num_display = _display_num(str(number), num_norm)

        affectations = list(whole_doc_aff)
        if num_norm:
            affectations += aff_index.get(num_norm, [])
        vig = _vigencia_for(affectations, norm_vigencia)

        header = _article_header(catalog, num_display, title)
        pieces = split_text(body, max_chars=max_chars, overlap=overlap)
        n = len(pieces)
        for i, piece in enumerate(pieces):
            section = f"Artículo {num_display}" + (f" ({i + 1}/{n})" if n > 1 else "")
            payload = {
                **base_payload,
                "canonical_id": art_cid,
                "titulo": title,
                "section": section,
                "numero_articulo": num_norm,
                "tiene_notas": bool(art.get("notes")),
                "tiene_version_anterior": bool(art.get("previous_versions")),
                **vig,
            }
            payload["text"] = piece
            # Seed the uid with art_id when present so duplicate-canonical-id
            # articles (SUIN sometimes emits two) don't collide on upsert.
            uid_seed = f"{art_cid}#{art_id}" if art_id else art_cid
            chunks.append(
                Chunk(
                    uid=_uid(uid_seed, i),
                    canonical_id=art_cid,
                    norm_canonical_id=norm_cid,
                    section=section,
                    title=title,
                    text=f"{header}\n\n{piece}",
                    body=piece,
                    chunk_index=i,
                    n_chunks=n,
                    payload=payload,
                )
            )
    return chunks


def chunk_sentencia(
    parsed: dict[str, Any],
    catalog: dict[str, Any],
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap: int = DEFAULT_OVERLAP_CHARS,
) -> list[Chunk]:
    """Chunk a sentencia by section (hechos, consideraciones, resuelve)."""
    numero = catalog.get("numero", "")
    anio = catalog.get("anio", "")
    corte = catalog.get("corte") or parsed.get("corte")
    sala = parsed.get("sala")
    magistrado = catalog.get("magistrado_ponente") or parsed.get("magistrado_ponente")
    norm_cid = catalog.get("canonical_id") or build_canonical_id(
        "SENTENCIA", str(numero), str(anio), corte=(corte or "cc"), sala=(sala or "plena")
    )
    label = _norm_label(catalog)

    base_payload = {
        "tipo": "SENTENCIA",
        "numero": numero,
        "anio": anio,
        "corte": corte,
        "sala": sala,
        "magistrado": magistrado,
        "suin_id": catalog.get("suin_id") or parsed.get("suin_id"),
        "norm_canonical_id": norm_cid,
        "norm_vigencia": catalog.get("suin_vigencia") or catalog.get("vigencia"),
        "estado_vigencia": "vigente",
        "derogado": False,
        "modificado": False,
        "afectaciones": [],
    }

    section_labels = {
        "hechos": "Hechos",
        "consideraciones": "Consideraciones",
        "resuelve": "Resuelve",
    }

    chunks: list[Chunk] = []
    for key, nice in section_labels.items():
        body = (parsed.get(key) or "").strip()
        if not body:
            continue
        sec_cid = f"{norm_cid}:{key}"
        pieces = split_text(body, max_chars=max_chars, overlap=overlap)
        n = len(pieces)
        for i, piece in enumerate(pieces):
            section = nice + (f" ({i + 1}/{n})" if n > 1 else "")
            header = f"{label} · {section}:"
            payload = {
                **base_payload,
                "canonical_id": sec_cid,
                "titulo": nice,
                "section": section,
                "text": piece,
            }
            chunks.append(
                Chunk(
                    uid=_uid(sec_cid, i),
                    canonical_id=sec_cid,
                    norm_canonical_id=norm_cid,
                    section=section,
                    title=nice,
                    text=f"{header}\n\n{piece}",
                    body=piece,
                    chunk_index=i,
                    n_chunks=n,
                    payload=payload,
                )
            )
    return chunks


def _uid(canonical_id: str, index: int) -> str:
    """Deterministic point id so re-exports upsert instead of duplicating."""
    return str(uuid.uuid5(CHUNK_NAMESPACE, f"{canonical_id}#chunk{index}"))
