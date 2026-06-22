#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# daily_update.sh — ciclo de actualización del knowledge (cron/systemd diario)
#
# Discover deltas → scrape (con retry de errores) → export vector/graph →
# VERIFY (compuerta) → backup a Wasabi. Todo idempotente: re-correr solo agrega
# lo nuevo. Si `verify` falla, el script sale con código ≠ 0 (para que el cron
# alerte) pero AÚN respalda la fuente de verdad (catalog + raw/clean); solo se
# salta el snapshot del vector/grafo si quedaron inconsistentes.
#
# Config por entorno o .env.backup:
#   DAILY_SOURCES   fuentes a refrescar (default: las de mayor cambio)
#   SCRAPE_RPS / SCRAPE_WORKERS   ritmo cortés
#   SKIP_BACKUP=1   no respaldar (p.ej. en pruebas)
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail   # NO -e: queremos controlar el flujo aunque un paso falle

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[ -f "$HERE/.env.backup" ] && set -a && . "$HERE/.env.backup" && set +a

COMPOSE="${COMPOSE:-docker compose}"
PIPE="$COMPOSE run --rm -T pipeline"          # CLI con docling+fastembed
DAILY_SOURCES="${DAILY_SOURCES:-corte_constitucional csj consejo_estado dian creg crc cra jep}"
SOCRATA_DATASETS="${SOCRATA_DATASETS:-legislacion cc_sentencias tratados}"
SCRAPE_RPS="${SCRAPE_RPS:-3}"
SCRAPE_WORKERS="${SCRAPE_WORKERS:-5}"

log() { printf '\033[1m[daily %s]\033[0m %s\n' "$(date -u +%H:%M:%S)" "$*"; }
run() { log "» $*"; "$@"; }

log "═══ Inicio ciclo de actualización ($(date -u +%FT%TZ)) ═══"

# 1) Descubrir deltas (catálogo Socrata idempotente + discoverers crawl) ──────
for ds in $SOCRATA_DATASETS; do
  run $PIPE catalog sync --dataset "$ds" || log "! sync $ds falló (sigo)"
done
for src in $DAILY_SOURCES; do
  run $PIPE catalog discover --source "$src" || log "! discover $src falló (sigo)"
done

# 2) Scrapear pendientes + RE-ENCOLAR errores (retry entre corridas) ──────────
for src in $DAILY_SOURCES suin; do
  run $PIPE scrape run --source "$src" --retry-errors \
      --workers "$SCRAPE_WORKERS" --rps "$SCRAPE_RPS" || log "! scrape $src falló (sigo)"
done

# 3) Materializar: vector + grafo (idempotentes) ─────────────────────────────
run $PIPE export vector || log "! export vector falló"
run $PIPE export graph  || log "! export graph falló"

# 4) VERIFY — compuerta de calidad ───────────────────────────────────────────
log "── Verificando integridad del knowledge ──"
$PIPE verify
VERIFY_RC=$?
if [ "$VERIFY_RC" -ne 0 ]; then
  log "✗ VERIFY FALLÓ (rc=$VERIFY_RC) — el knowledge quedó inconsistente."
fi

# 5) Backup a Wasabi ─────────────────────────────────────────────────────────
if [ "${SKIP_BACKUP:-0}" != "1" ]; then
  if [ "$VERIFY_RC" -eq 0 ]; then
    log "── Backup completo (verify OK) ──"
    "$HERE/scripts/backup_to_wasabi.sh"
  else
    # Verify falló: respalda solo la fuente de verdad; NO snapshotees un vector
    # store inconsistente.
    log "── Backup parcial: catálogo + raw/clean (verify falló, se omite vector/grafo) ──"
    "$HERE/scripts/backup_to_wasabi.sh" --no-vectors --no-graph
  fi
else
  log "Backup OMITIDO (SKIP_BACKUP=1)"
fi

log "═══ Fin ciclo (verify rc=$VERIFY_RC) ═══"
exit "$VERIFY_RC"
