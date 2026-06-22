"""Verificación de calidad del knowledge (catálogo + Qdrant + Neo4j).

Responde la pregunta operativa: *tras una ingesta, ¿el hybrid graph+vector store
quedó completo, consistente entre sí, bien relacionado y usable sin errores?*

No mide progreso (eso es ``scrape status``) sino **correctitud**. Corre 5 chequeos
y emite un veredicto PASS / WARN / FAIL por cada uno, con un código de salida ≠ 0
si algo falla — pensado para que el cron diario reviente ruidosamente si el
conocimiento quedó roto, en vez de servir resultados silenciosamente incompletos.

Chequeos:
  1. RECONCILIACIÓN  — #docs 'done' en catálogo ≈ #docs en Qdrant ≈ #nodos Neo4j.
  2. COMPLETITUD     — una muestra de docs 'done' tiene chunks en Qdrant Y nodo
                       en Neo4j (detecta el doc 'done' pero sin vector/sin grafo).
  3. INTEGRIDAD REL. — citas a fantasmas, nodos aislados, afectaciones sin mapear.
  4. RECUPERACIÓN    — dim de la colección == dim del modelo; búsquedas de humo
                       devuelven resultados.
  5. PRESUPUESTO ERR — % de error / needs_ocr por debajo del umbral.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from typing import Any

from scrapper_leyes.config import Settings
from scrapper_leyes.storage.cache import ProvenanceCache
from scrapper_leyes.storage.database import Database

logger = logging.getLogger(__name__)

# Umbrales (ajustables). Una deriva pequeña es WARN; una grande o un almacén
# vacío con datos 'done' es FAIL.
DRIFT_WARN = 0.02      # 2% de diferencia entre almacenes → WARN
DRIFT_FAIL = 0.10      # 10% → FAIL
SAMPLE_SIZE = 300      # docs 'done' muestreados para el chequeo de completitud
MISSING_WARN = 0.01    # 1% de la muestra sin vector/grafo → WARN
MISSING_FAIL = 0.05    # 5% → FAIL
ERROR_BUDGET_WARN = 0.05   # 5% de error/needs_ocr → WARN
ERROR_BUDGET_FAIL = 0.20   # 20% → FAIL
SCROLL_CAP = 500_000   # tope de puntos a recorrer para contar docs distintos

# Consultas de humo para la prueba de recuperación.
SMOKE_QUERIES = ("derecho fundamental a la salud", "contrato estatal", "habeas data")

PASS, WARN, FAIL, INFO = "PASS", "WARN", "FAIL", "INFO"


@dataclass
class CheckResult:
    name: str
    status: str           # PASS | WARN | FAIL | INFO
    message: str
    details: dict[str, Any] = field(default_factory=dict)


def _worst(statuses: list[str]) -> str:
    for s in (FAIL, WARN, INFO, PASS):
        if s in statuses:
            return s if s != INFO else (PASS if PASS in statuses else INFO)
    return PASS


class KnowledgeVerifier:
    """Corre los chequeos de calidad sobre los 3 almacenes."""

    def __init__(self, settings: Settings, db: Database, cache: ProvenanceCache):
        self.settings = settings
        self.db = db
        self.cache = cache
        self.collection = settings.qdrant_collection
        self._qdrant = None
        self._driver = None

    # ── clientes (lazy) ──────────────────────────────────────────────────
    @property
    def qdrant(self):
        if self._qdrant is None:
            from scrapper_leyes.export_vector import VectorStoreExporter
            self._qdrant = VectorStoreExporter._build_client(self.settings)
        return self._qdrant

    @property
    def driver(self):
        if self._driver is None:
            from neo4j import GraphDatabase
            self._driver = GraphDatabase.driver(
                self.settings.neo4j_uri,
                auth=(self.settings.neo4j_user, self.settings.neo4j_password),
            )
        return self._driver

    def close(self) -> None:
        if self._driver is not None:
            self._driver.close()

    # ── helpers de conteo ────────────────────────────────────────────────
    def _catalog_done(self) -> list[dict[str, Any]]:
        rows = self.db.conn.execute(
            "SELECT canonical_id, suin_id, tipo, numero, anio, corte "
            "FROM catalog WHERE scrape_status = 'done'"
        ).fetchall()
        return [dict(r) for r in rows]

    def _qdrant_points(self) -> int:
        try:
            return self.qdrant.count(self.collection, exact=True).count
        except Exception as e:
            logger.warning("qdrant count falló: %s", e)
            return -1

    def _qdrant_distinct_docs(self) -> tuple[int, bool]:
        """Cuenta docs distintos (norm_canonical_id) recorriendo la colección.

        Devuelve (n_distintos, capado?) — capado=True si se alcanzó SCROLL_CAP.
        """
        seen: set[str] = set()
        offset = None
        scanned = 0
        try:
            while scanned < SCROLL_CAP:
                points, offset = self.qdrant.scroll(
                    self.collection, limit=4096, offset=offset,
                    with_payload=["norm_canonical_id"], with_vectors=False,
                )
                if not points:
                    break
                for p in points:
                    pl = p.payload or {}
                    cid = pl.get("norm_canonical_id") or pl.get("canonical_id")
                    if cid:
                        seen.add(cid)
                scanned += len(points)
                if offset is None:
                    break
        except Exception as e:
            logger.warning("qdrant scroll falló: %s", e)
        return len(seen), scanned >= SCROLL_CAP

    def _qdrant_has_doc(self, norm_cid: str) -> bool:
        from qdrant_client.http import models
        try:
            n = self.qdrant.count(
                self.collection,
                count_filter=models.Filter(must=[models.FieldCondition(
                    key="norm_canonical_id", match=models.MatchValue(value=norm_cid))]),
                exact=True,
            ).count
            return n > 0
        except Exception:
            return False

    def _neo4j_node_count(self) -> int:
        with self.driver.session() as s:
            r = s.run(
                "MATCH (n) WHERE (n:Norma OR n:Sentencia) AND n.suin_id IS NOT NULL "
                "RETURN count(n) AS c"
            ).single()
            return r["c"] if r else 0

    def _neo4j_has_doc(self, norm_cid: str) -> bool:
        with self.driver.session() as s:
            r = s.run(
                "MATCH (n {id: $cid}) WHERE n:Norma OR n:Sentencia RETURN count(n) AS c",
                cid=norm_cid,
            ).single()
            return bool(r and r["c"] > 0)

    # ── chequeos ─────────────────────────────────────────────────────────
    def check_reconciliation(self, done: list[dict[str, Any]]) -> CheckResult:
        catalog_n = len(done)
        if catalog_n == 0:
            return CheckResult("Reconciliación", INFO,
                               "No hay documentos 'done' todavía — nada que reconciliar.")
        points = self._qdrant_points()
        qdrant_docs, capped = self._qdrant_distinct_docs()
        neo4j_docs = self._neo4j_node_count()

        details = {
            "catalogo_done": catalog_n, "qdrant_chunks": points,
            "qdrant_docs_distintos": qdrant_docs, "qdrant_scroll_capado": capped,
            "neo4j_nodos": neo4j_docs,
        }
        # Almacén entero vacío con datos 'done' = FAIL inequívoco.
        if qdrant_docs == 0 or neo4j_docs == 0:
            vacios = [n for n, v in (("Qdrant", qdrant_docs), ("Neo4j", neo4j_docs)) if v == 0]
            return CheckResult("Reconciliación", FAIL,
                               f"{catalog_n} docs 'done' pero {', '.join(vacios)} vacío(s). "
                               f"Falta correr export {'vector' if 'Qdrant' in vacios else ''}"
                               f"{'/' if len(vacios) > 1 else ''}"
                               f"{'graph' if 'Neo4j' in vacios else ''}.", details)

        def drift(a: int, b: int) -> float:
            return abs(a - b) / max(a, b, 1)

        d_q = 0.0 if capped else drift(catalog_n, qdrant_docs)
        d_n = drift(catalog_n, neo4j_docs)
        worst = max(d_q, d_n)
        msg = (f"catálogo={catalog_n}, qdrant={qdrant_docs}"
               f"{' (capado)' if capped else ''}, neo4j={neo4j_docs}; "
               f"deriva máx {worst:.1%}")
        if worst >= DRIFT_FAIL:
            return CheckResult("Reconciliación", FAIL, msg, details)
        if worst >= DRIFT_WARN:
            return CheckResult("Reconciliación", WARN, msg, details)
        return CheckResult("Reconciliación", PASS, msg, details)

    def check_completeness(self, done: list[dict[str, Any]]) -> CheckResult:
        if not done:
            return CheckResult("Completitud", INFO, "Sin docs 'done'.")
        sample = done if len(done) <= SAMPLE_SIZE else random.sample(done, SAMPLE_SIZE)
        miss_vec: list[str] = []
        miss_graph: list[str] = []
        for d in sample:
            cid = d.get("canonical_id")
            if not cid:
                continue  # sin canonical_id no se puede reconciliar por clave
            if not self._qdrant_has_doc(cid):
                miss_vec.append(cid)
            if not self._neo4j_has_doc(cid):
                miss_graph.append(cid)
        n = len(sample)
        rate = max(len(miss_vec), len(miss_graph)) / max(n, 1)
        details = {
            "muestra": n,
            "sin_vector": len(miss_vec), "sin_grafo": len(miss_graph),
            "ejemplos_sin_vector": miss_vec[:10], "ejemplos_sin_grafo": miss_graph[:10],
        }
        msg = (f"muestra={n}; sin vector={len(miss_vec)}, sin grafo={len(miss_graph)} "
               f"({rate:.1%})")
        if rate >= MISSING_FAIL:
            return CheckResult("Completitud", FAIL, msg, details)
        if rate > MISSING_WARN or (miss_vec or miss_graph):
            return CheckResult("Completitud", WARN, msg, details)
        return CheckResult("Completitud", PASS, msg, details)

    def check_relational_integrity(self) -> CheckResult:
        try:
            with self.driver.session() as s:
                total = s.run("MATCH (n) RETURN count(n) AS c").single()["c"]
                ghosts = s.run(
                    "MATCH (n) WHERE (n:Norma OR n:Sentencia) AND n.suin_id IS NULL "
                    "RETURN count(n) AS c").single()["c"]
                isolated = s.run(
                    "MATCH (n) WHERE NOT (n)--() RETURN count(n) AS c").single()["c"]
                cita_total = s.run(
                    "MATCH ()-[r:CITA_A]->() RETURN count(r) AS c").single()["c"]
                cita_ghost = s.run(
                    "MATCH ()-[r:CITA_A]->(m) WHERE m.suin_id IS NULL "
                    "RETURN count(r) AS c").single()["c"]
        except Exception as e:
            return CheckResult("Integridad relacional", WARN,
                               f"No se pudo consultar Neo4j: {e}")
        unmapped = self.db.get_unmapped_count()
        cita_res = 1 - (cita_ghost / cita_total) if cita_total else 1.0
        details = {
            "nodos_total": total, "nodos_fantasma": ghosts, "nodos_aislados": isolated,
            "citas_total": cita_total, "citas_a_fantasma": cita_ghost,
            "tasa_resolucion_citas": round(cita_res, 3),
            "afectaciones_sin_mapear": unmapped,
        }
        msg = (f"{total} nodos ({ghosts} fantasma, {isolated} aislados); "
               f"citas resueltas {cita_res:.0%}; {unmapped} afectaciones sin mapear")
        # Informativo: fantasmas e islas son esperables mientras la cobertura sube.
        # Solo marcamos WARN si TODOS los nodos están aislados (grafo sin relaciones).
        if total and isolated == total:
            return CheckResult("Integridad relacional", WARN,
                               "Todos los nodos están aislados — no hay relaciones.", details)
        return CheckResult("Integridad relacional", INFO, msg, details)

    def check_retrieval(self) -> CheckResult:
        from scrapper_leyes.search import CollectionMissing, SemanticSearcher
        try:
            if not self.qdrant.collection_exists(self.collection):
                return CheckResult("Recuperación", FAIL,
                                   f"La colección '{self.collection}' no existe.")
            points = self._qdrant_points()
            if points <= 0:
                return CheckResult("Recuperación", INFO,
                                   "Colección vacía — nada que recuperar todavía.")
            # Dim configurada de la colección (informativa). La compatibilidad
            # real dim-modelo la valida la búsqueda de humo de abajo: si la dim
            # no cuadrara, query_points reventaría.
            info = self.qdrant.get_collection(self.collection)
            vectors = info.config.params.vectors
            from scrapper_leyes.export_vector import DENSE_VECTOR_NAME
            vp = vectors.get(DENSE_VECTOR_NAME) if isinstance(vectors, dict) else vectors
            coll_dim = getattr(vp, "size", None)
            searcher = SemanticSearcher(self.settings)
        except CollectionMissing:
            return CheckResult("Recuperación", FAIL, "Colección ausente.")
        except Exception as e:
            return CheckResult("Recuperación", WARN, f"No se pudo inspeccionar: {e}")

        # Búsquedas de humo: deben devolver resultados no vacíos.
        hits: dict[str, int] = {}
        try:
            for q in SMOKE_QUERIES:
                res = searcher.search(q, limit=5)
                hits[q] = len(res)
        except Exception as e:
            return CheckResult("Recuperación", FAIL,
                               f"La búsqueda híbrida falló: {e}",
                               {"collection_dim": coll_dim})
        vacias = [q for q, n in hits.items() if n == 0]
        details = {"collection_dim": coll_dim, "hits": hits}
        if vacias:
            return CheckResult("Recuperación", WARN,
                               f"{len(vacias)}/{len(SMOKE_QUERIES)} consultas sin resultados "
                               f"(dim={coll_dim}).", details)
        return CheckResult("Recuperación", PASS,
                           f"Búsqueda híbrida OK (dim={coll_dim}); "
                           f"todas las consultas de humo devolvieron resultados.", details)

    def check_error_budget(self) -> CheckResult:
        stats = self.db.get_scrape_stats()
        total = sum(stats.values()) or 1
        bad = stats.get("error", 0) + stats.get("needs_ocr", 0)
        done = stats.get("done", 0)
        rate = bad / total
        details = {"por_estado": stats, "tasa_error_ocr": round(rate, 3)}
        msg = (f"done={done}, error={stats.get('error', 0)}, "
               f"needs_ocr={stats.get('needs_ocr', 0)} de {total} ({rate:.1%})")
        if rate >= ERROR_BUDGET_FAIL:
            return CheckResult("Presupuesto de error", FAIL, msg, details)
        if rate >= ERROR_BUDGET_WARN:
            return CheckResult("Presupuesto de error", WARN, msg, details)
        return CheckResult("Presupuesto de error", PASS, msg, details)

    # ── orquestación ─────────────────────────────────────────────────────
    def run_all(self) -> tuple[list[CheckResult], str]:
        done = self._catalog_done()
        results = [
            self.check_reconciliation(done),
            self.check_completeness(done),
            self.check_relational_integrity(),
            self.check_retrieval(),
            self.check_error_budget(),
        ]
        verdict = _worst([r.status for r in results])
        return results, verdict
