# Operación: actualización diaria + respaldo a Wasabi

Dos scripts para correr en el **servidor** (Contabo), con `docker compose` arriba:

| Script | Qué hace |
|---|---|
| `daily_update.sh` | discover deltas → scrape (con retry) → export vector/grafo → **verify** (compuerta) → backup |
| `backup_to_wasabi.sh` | respaldo por tiers a Wasabi (catálogo, raw+clean, snapshot Qdrant, grafo Neo4j) |

## Filosofía de respaldo (por valor)

`catalog.db` + `clean/` (parsed.json) son la **fuente de verdad**: de ellos se
reconstruyen el vector store (`export vector`) y el grafo (`export graph`).

| Tier | Qué | Cadencia sugerida |
|---|---|---|
| 1 | `catalog.db` (comprimido) | diario |
| 2 | `raw/` (PDF/HTML) + `clean/` (parsed.json) — incremental, **aditivo** | diario |
| 3 | snapshot de Qdrant (re-embeber = horas → vale respaldar) | semanal / post-ingesta |
| 4 | grafo Neo4j (APOC) — se reconstruye rápido, **opcional** | semanal |

Layout en el bucket:

```
s3://leyes-co/
  raw/<source>/<tipo>/<id>/<doc>.(pdf|html)     # originales (inmutables)
  clean/<source>/<tipo>/<id>/parsed.json        # texto limpio + metadata.json
  backups/<YYYY-MM-DD>/
      catalog.db.zst
      qdrant-legal_corpus.snapshot.zst
      neo4j-graph.cypher.zst
```

## Setup en el servidor

### 1. Instalar herramientas

```bash
sudo apt-get install -y rclone zstd        # zstd opcional (si no, usa gzip)
```

### 2. Configurar el remote de Wasabi (`~/.config/rclone/rclone.conf`)

```ini
[wasabi]
type = s3
provider = Wasabi
access_key_id = TU_ACCESS_KEY
secret_access_key = TU_SECRET_KEY
endpoint = s3.us-east-1.wasabisys.com        # ← usa el endpoint de TU región
```

(Crea las llaves en la consola de Wasabi → Access Keys. Crea el bucket `leyes-co`.)

Prueba: `rclone lsd wasabi:` debe listar tu bucket.

### 3. Config local

```bash
cp .env.backup.example .env.backup    # edita WASABI_BUCKET, NEO4J_PASSWORD, etc.
chmod +x scripts/*.sh
```

### 4. (Opcional) Habilitar el export de grafo por APOC

Para el Tier 4 (Neo4j), el contenedor neo4j necesita:

```
NEO4J_apoc_export_file_enabled=true     # en environment del servicio neo4j
```

Si no se habilita, el backup lo omite con un aviso (el grafo se reconstruye con
`scrapper-leyes export graph`).

## Correr

```bash
./scripts/daily_update.sh                  # ciclo completo
./scripts/backup_to_wasabi.sh              # solo backup, todos los tiers
./scripts/backup_to_wasabi.sh --no-vectors # backup sin snapshot de Qdrant
./scripts/backup_to_wasabi.sh --only-bulk  # solo raw/clean
```

### Programar (systemd timer, recomendado sobre cron)

`/etc/systemd/system/leyes-daily.service`:

```ini
[Unit]
Description=Actualización diaria del knowledge legal
After=docker.service

[Service]
Type=oneshot
WorkingDirectory=/opt/scrapper-leyes
ExecStart=/opt/scrapper-leyes/scripts/daily_update.sh
```

`/etc/systemd/system/leyes-daily.timer`:

```ini
[Unit]
Description=Corre la actualización diaria de madrugada

[Timer]
OnCalendar=*-*-* 03:30:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
sudo systemctl enable --now leyes-daily.timer
systemctl list-timers leyes-daily.timer      # ver próxima ejecución
journalctl -u leyes-daily.service -f         # ver logs
```

El servicio sale con código ≠ 0 si `verify` falla → systemd lo marca `failed`
(configurable `OnFailure=` para alertar por email/webhook).

## Restaurar

```bash
# Catálogo
rclone copyto wasabi:leyes-co/backups/2026-06-21/catalog.db.zst ./catalog.db.zst
zstd -d catalog.db.zst -o data/catalog.db

# Texto limpio + originales
rclone copy wasabi:leyes-co/clean data/raw
rclone copy wasabi:leyes-co/raw   data/raw

# Vector store (rápido) — restaurar snapshot de Qdrant
rclone copyto wasabi:leyes-co/backups/2026-06-21/qdrant-legal_corpus.snapshot.zst ./q.zst
zstd -d q.zst -o q.snapshot
curl -X POST "http://localhost:6333/collections/legal_corpus/snapshots/upload" \
  -H "Content-Type: multipart/form-data" -F "snapshot=@q.snapshot"

# Grafo — reconstruir desde el catálogo (no requiere backup):
scrapper-leyes export graph
# (o restaurar el .cypher con cypher-shell si se respaldó)
```

> **Nota Wasabi:** mínimo de 90 días de almacenamiento por objeto + mínimo
> mensual. Por eso el grueso (raw/clean) se sube **incremental** (solo deltas) y
> los backups fechados rotan con `RETAIN_DAILY`. Sin cargos de egreso al restaurar.
