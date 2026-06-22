"""Resolver de vigencia **respaldado por el grafo**.

A diferencia de :mod:`scrapper_leyes.vigencia` (que lee un único ``parsed.json``),
este resolver consulta Neo4j: toma las afectaciones **entrantes** de un
artículo/norma —vengan del documento que vengan— y las versiones de texto
guardadas en el nodo ``Articulo``. Así el grafo es la fuente única de verdad y la
resolución es completa cross-documento.

Reutiliza la lógica pura (clasificación por severidad, versiones temporales) de
``vigencia.py``; aquí solo cambia *de dónde salen los datos*.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from scrapper_leyes.vigencia import (
    ESTADO_DESCONOCIDO,
    ESTADO_MODIFICADO,
    ESTADO_VIGENTE,
    Afectacion,
    VigenciaReport,
    _build_versions,
    _classify,
    _is_whole_doc,
    _select_version,
    parse_fecha,
    _parse_iso,
)

# Affectation edge provenance considered authoritative for vigencia:
#   suin           — SUIN's recorded modifications (other norms → this one)
#   jurisprudencia — SUIN's jurisprudence backlinks (control constitucional)
#   resuelve       — parsed directly from a sentencia's parte resolutiva
#                    (DECLARA_INEXEQUIBLE/…); same effect class as jurisprudencia.
_SOURCES = ["suin", "jurisprudencia", "resuelve"]
_JUR_SOURCES = {"jurisprudencia", "resuelve"}


def _coerce_fecha(fecha: str | date | None) -> date | None:
    if isinstance(fecha, date):
        return fecha
    if isinstance(fecha, str):
        return parse_fecha(fecha) or _parse_iso(fecha)
    return None


def _row_to_afectacion(row: dict[str, Any]) -> Afectacion:
    return Afectacion(
        tipo=row.get("tipo") or "UNKNOWN",
        raw="",
        fuente=row.get("texto") or "",
        fuente_id=None,
        contexto=row.get("anio"),
        ambito=(row.get("articulo") or "documento") if (row.get("articulo") or "").strip() else "documento",
    )


def _split(rows: list[dict[str, Any]]) -> tuple[list[Afectacion], list[Afectacion]]:
    """Separa afectaciones normativas (suin) de jurisprudencia (control const.)."""
    afect, jur = [], []
    for r in rows:
        af = _row_to_afectacion(r)
        if r.get("source") in _JUR_SOURCES:
            jur.append(af)
        else:
            afect.append(af)
    return afect, jur


def resolve_graph(
    driver,
    *,
    norm_cid: str,
    art_cid: str | None,
    norm_vigencia: str | None,
    fecha: str | date | None = None,
) -> VigenciaReport | None:
    """Resuelve vigencia consultando el grafo. Devuelve ``None`` si el nodo no
    existe aún en Neo4j (el llamador puede caer al resolver basado en JSON)."""
    as_of = _coerce_fecha(fecha)

    with driver.session() as s:
        if art_cid:
            art = s.run(
                "MATCH (a:Articulo {id: $id}) RETURN a.texto AS texto, a.prev_versions AS prev",
                id=art_cid,
            ).single()
            if art is None:
                return None
            texto = art["texto"] or ""
            prev = json.loads(art["prev"] or "[]")

            # Afectaciones entrantes al artículo + las de documento completo a la norma.
            art_rows = s.run(
                "MATCH (src)-[r]->(a:Articulo {id: $id}) "
                "WHERE r.source IN $srcs "
                "RETURN coalesce(r.tipo, type(r)) AS tipo, r.source AS source, r.texto AS texto, "
                "       r.anio AS anio, r.articulo AS articulo",
                id=art_cid, srcs=_SOURCES,
            ).data()
            norm_rows = s.run(
                "MATCH (src)-[r]->(n:Norma {id: $id}) "
                "WHERE r.source IN $srcs "
                "RETURN coalesce(r.tipo, type(r)) AS tipo, r.source AS source, r.texto AS texto, "
                "       r.anio AS anio, r.articulo AS articulo",
                id=norm_cid, srcs=_SOURCES,
            ).data()
            # Solo cascadean las afectaciones de documento completo.
            cascada = [r for r in norm_rows if _is_whole_doc(r.get("articulo") or "")]
            rows = art_rows + cascada

            afect, jur = _split(rows)
            estado, vigente, motivo = _classify({a.tipo for a in afect}, jur, norm_vigencia)
            if estado in (ESTADO_VIGENTE, ESTADO_DESCONOCIDO) and prev:
                estado, motivo = ESTADO_MODIFICADO, "Vigente con modificaciones"

            versions = _build_versions({"text": texto, "previous_versions": prev})
            chosen = _select_version(versions, as_of)
            return VigenciaReport(
                canonical_id=art_cid,
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

        # Nivel norma.
        norm = s.run("MATCH (n:Norma {id: $id}) RETURN n.id AS id", id=norm_cid).single()
        if norm is None:
            return None
        norm_rows = s.run(
            "MATCH (src)-[r]->(n:Norma {id: $id}) "
            "WHERE r.source IN $srcs "
            "RETURN type(r) AS tipo, r.source AS source, r.texto AS texto, "
            "       r.anio AS anio, r.articulo AS articulo",
            id=norm_cid, srcs=_SOURCES,
        ).data()
        rows = [r for r in norm_rows if _is_whole_doc(r.get("articulo") or "")]
        afect, jur = _split(rows)
        estado, vigente, motivo = _classify({a.tipo for a in afect}, jur, norm_vigencia)
        return VigenciaReport(
            canonical_id=norm_cid,
            nivel="norma",
            estado=estado,
            vigente=vigente,
            motivo=motivo,
            afectaciones=afect,
            jurisprudencia=jur,
            fecha_consulta=as_of.isoformat() if as_of else None,
        )
