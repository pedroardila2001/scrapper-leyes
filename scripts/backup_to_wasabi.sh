#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# backup_to_wasabi.sh — respaldo por tiers del knowledge a Wasabi (S3-compatible)
#
# Filosofía: catalog.db + clean (parsed.json) son la FUENTE DE VERDAD; el vector
# store (Qdrant) y el grafo (Neo4j) se reconstruyen de ellos. Por eso se respalda
# por valor:
#   Tier 1  catalog.db            (crítico, diminuto, diario)
#   Tier 2  raw/ + clean/         (el grueso, incremental ADITIVO — raw es inmutable)
#   Tier 3  snapshot de Qdrant    (caro de rehacer: re-embeber = horas → vale)
#   Tier 4  export del grafo Neo4j (opcional: se reconstruye con `export graph`)
#
# Requisitos en el HOST: docker compose corriendo, rclone configurado con un
# remote a Wasabi (ver scripts/README.md), gzip (o zstd). Idempotente y seguro de
# re-correr. NO borra raw/clean (usa copy, no sync).
#
# Uso:
#   ./scripts/backup_to_wasabi.sh                 # todos los tiers
#   ./scripts/backup_to_wasabi.sh --no-vectors    # salta Qdrant (p.ej. entre semana)
#   ./scripts/backup_to_wasabi.sh --no-graph      # salta Neo4j
#   ./scripts/backup_to_wasabi.sh --only-bulk     # solo raw/clean (Tier 2)
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Config (override por entorno o .env.backup) ──────────────────────────────
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[ -f "$HERE/.env.backup" ] && set -a && . "$HERE/.env.backup" && set +a

COMPOSE="${COMPOSE:-docker compose}"
DATA_DIR="${DATA_DIR:-$HERE/data}"
RCLONE_REMOTE="${RCLONE_REMOTE:-wasabi}"        # nombre del remote rclone
WASABI_BUCKET="${WASABI_BUCKET:?define WASABI_BUCKET (p.ej. leyes-co)}"
QDRANT_COLLECTION="${QDRANT_COLLECTION:-legal_corpus}"
QDRANT_URL="${QDRANT_URL:-http://localhost:6333}"
RETAIN_DAILY="${RETAIN_DAILY:-7}"               # snapshots fechados a conservar
DATE="$(date -u +%Y-%m-%d)"
DEST="$RCLONE_REMOTE:$WASABI_BUCKET"
BK="$DEST/backups/$DATE"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

WITH_VECTORS=1; WITH_GRAPH=1; ONLY_BULK=0
for a in "$@"; do case "$a" in
  --no-vectors) WITH_VECTORS=0 ;;
  --no-graph)   WITH_GRAPH=0 ;;
  --only-bulk)  ONLY_BULK=1 ;;
  *) echo "flag desconocido: $a" >&2; exit 2 ;;
esac; done

# Compresor: zstd si está, si no gzip (ambos universales en Linux server).
if command -v zstd >/dev/null 2>&1; then ZIP="zstd -q -19 -T0"; ZEXT="zst"
else ZIP="gzip -9"; ZEXT="gz"; fi

log() { printf '\033[1m[backup %s]\033[0m %s\n' "$(date -u +%H:%M:%S)" "$*"; }
need() { command -v "$1" >/dev/null 2>&1 || { echo "falta '$1' en el host" >&2; exit 1; }; }
need rclone

# ── Tier 1: catalog.db (copia CONSISTENTE vía sqlite backup, no copia en caliente) ──
backup_catalog() {
  log "Tier 1 · catalog.db → $BK/"
  # `.backup` produce una copia consistente aunque la BD esté en WAL/uso.
  $COMPOSE exec -T api python -c "import sqlite3; \
src=sqlite3.connect('/app/data/catalog.db'); dst=sqlite3.connect('/app/data/_catalog_backup.db'); \
src.backup(dst); dst.close(); src.close()"
  $ZIP -c "$DATA_DIR/_catalog_backup.db" > "$TMP/catalog.db.$ZEXT"
  rm -f "$DATA_DIR/_catalog_backup.db"
  rclone copyto "$TMP/catalog.db.$ZEXT" "$BK/catalog.db.$ZEXT"
}

# ── Tier 2: raw + clean (incremental, ADITIVO — raw es inmutable) ─────────────
backup_bulk() {
  log "Tier 2 · clean/ (parsed.json + metadata) → $DEST/clean/"
  rclone copy "$DATA_DIR/raw" "$DEST/clean" \
    --include "**/parsed.json" --include "**/metadata.json" \
    --transfers 8 --checkers 16 --fast-list
  log "Tier 2 · raw/ (PDF/HTML originales) → $DEST/raw/"
  rclone copy "$DATA_DIR/raw" "$DEST/raw" \
    --exclude "**/parsed.json" --exclude "**/metadata.json" \
    --transfers 8 --checkers 16 --fast-list
}

# ── Tier 3: snapshot de Qdrant (caro de rehacer) ─────────────────────────────
backup_qdrant() {
  log "Tier 3 · snapshot de Qdrant ($QDRANT_COLLECTION)"
  local name
  name="$(curl -fsS -X POST "$QDRANT_URL/collections/$QDRANT_COLLECTION/snapshots" \
          | python3 -c 'import sys,json;print(json.load(sys.stdin)["result"]["name"])')"
  curl -fsS "$QDRANT_URL/collections/$QDRANT_COLLECTION/snapshots/$name" -o "$TMP/qdrant.snapshot"
  # Limpia el snapshot del volumen de Qdrant para no llenarlo.
  curl -fsS -X DELETE "$QDRANT_URL/collections/$QDRANT_COLLECTION/snapshots/$name" >/dev/null
  $ZIP -c "$TMP/qdrant.snapshot" > "$TMP/qdrant-$QDRANT_COLLECTION.snapshot.$ZEXT"
  rclone copyto "$TMP/qdrant-$QDRANT_COLLECTION.snapshot.$ZEXT" \
    "$BK/qdrant-$QDRANT_COLLECTION.snapshot.$ZEXT"
}

# ── Tier 4: grafo Neo4j (best-effort vía APOC; se reconstruye con export graph) ──
backup_graph() {
  log "Tier 4 · export del grafo Neo4j (APOC, best-effort)"
  # APOC escribe en el import dir del contenedor; requiere apoc.export.file.enabled=true.
  if $COMPOSE exec -T neo4j cypher-shell -u "${NEO4J_USER:-neo4j}" -p "${NEO4J_PASSWORD:-password}" \
       "CALL apoc.export.cypher.all('graph.cypher', {format:'cypher-shell'})" >/dev/null 2>&1; then
    $COMPOSE exec -T neo4j sh -c 'cat /var/lib/neo4j/import/graph.cypher' > "$TMP/graph.cypher" 2>/dev/null \
      || $COMPOSE cp neo4j:/var/lib/neo4j/import/graph.cypher "$TMP/graph.cypher"
    $ZIP -c "$TMP/graph.cypher" > "$TMP/neo4j-graph.cypher.$ZEXT"
    rclone copyto "$TMP/neo4j-graph.cypher.$ZEXT" "$BK/neo4j-graph.cypher.$ZEXT"
  else
    log "  ! APOC export no disponible (apoc.export.file.enabled?). Se omite —"
    log "    el grafo se reconstruye con 'scrapper-leyes export graph' desde el catálogo."
  fi
}

# ── Retención: conserva los últimos N backups fechados ───────────────────────
prune_old() {
  log "Retención · conservando los últimos $RETAIN_DAILY backups fechados"
  mapfile -t dirs < <(rclone lsf "$DEST/backups/" --dirs-only 2>/dev/null | sed 's#/$##' | sort)
  local n=${#dirs[@]}
  if (( n > RETAIN_DAILY )); then
    for d in "${dirs[@]:0:$((n - RETAIN_DAILY))}"; do
      log "  purga backups/$d"
      rclone purge "$DEST/backups/$d" || true
    done
  fi
}

# ── Orquestación ─────────────────────────────────────────────────────────────
log "Destino: $DEST  | compresor: $ZIP"
if (( ONLY_BULK )); then
  backup_bulk
else
  backup_catalog
  backup_bulk
  (( WITH_VECTORS )) && backup_qdrant || log "Tier 3 · Qdrant OMITIDO (--no-vectors)"
  (( WITH_GRAPH ))   && backup_graph  || log "Tier 4 · Neo4j OMITIDO (--no-graph)"
  prune_old
fi
log "Backup completo ✓"
