# Pipeline de Ingesta Normativa Colombiana

Catálogo de normas vía API Socrata (datos.gov.co) + scraper de texto completo y afectaciones desde SUIN-Juriscol, con cache de procedencia inmutable.

## Quick Start (Docker)

```bash
# Build
docker compose build

# Sync catalog (all types)
docker compose run pipeline catalog sync

# Sync only laws
docker compose run pipeline catalog sync --tipo LEY

# Resolve IDs and scrape 10 laws
docker compose run pipeline scrape run --tipo LEY --limit 10

# Check status
docker compose run pipeline scrape status

# Run tests inside container
docker compose run pipeline test
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SOCRATA_APP_TOKEN` | *(none)* | datos.gov.co app token (optional, avoids throttling) |
| `RATE_LIMIT_RPS` | `1.0` | Max requests/second to SUIN |
| `USER_AGENT_CONTACT` | `investigacion@example.com` | Contact email in User-Agent |
| `DATA_DIR` | `data` (local) / `/data` (Docker) | Data directory path |

## Gramática del Canonical ID

```
canonical_id = "co:" tipo_norm ":" numero ":" año [":art:" art_ref [":par:" par_num]]
art_ref      = digit+ [letter] | "trans:" digit+
```

### Ejemplos

| Canonical ID | Descripción |
|---|---|
| `co:ley:1712:2014` | Ley 1712 de 2014 (completa) |
| `co:ley:1712:2014:art:1` | Artículo 1° |
| `co:ley:1712:2014:art:5a` | Artículo 5A (adicionado) |
| `co:ley:1712:2014:art:trans:1` | Artículo Transitorio 1 |
| `co:ley:1581:2012:art:5:par:2` | Art. 5, Parágrafo 2 |
| `co:decreto:1494:2015:art:1` | Decreto 1494/2015, Art. 1 |

## Esquema de Tablas SQLite

### `catalog`
Catálogo sincronizado desde datos.gov.co (dataset `fiev-nid6`).

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `id` | INTEGER PK | Auto-increment |
| `tipo` | TEXT | LEY, DECRETO, RESOLUCION, etc. |
| `numero` | TEXT | Número de la norma |
| `anio` | TEXT | Año de expedición |
| `sector` | TEXT | Sector (Interior, Salud, etc.) |
| `subtipo` | TEXT | LEY ESTATUTARIA, DECRETO LEY, etc. |
| `vigencia` | TEXT | Estado según Socrata (snapshot) |
| `entidad` | TEXT | Entidad emisora |
| `materia` | TEXT | Materias (pipe-separated) |
| `suin_id` | TEXT | ID resuelto en SUIN |
| `resolve_status` | TEXT | pending/resolved/ambiguous/not_found/error |
| `scrape_status` | TEXT | pending/done/error/skipped/needs_ocr |
| `suin_vigencia` | TEXT | Estado según la página de SUIN |
| `vigencia_match` | INTEGER | 1=coincide, 0=discrepancia |

### `scrape_log`
Registro de cada descarga.

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `suin_id` | TEXT PK | SUIN document ID |
| `source` | TEXT | Fuente (suin, senado, relatoria) |
| `content_hash` | TEXT | SHA-256 del HTML |
| `capture_ts` | DATETIME | Timestamp de captura |
| `raw_path` | TEXT | Ruta relativa al archivo raw |
| `parse_status` | TEXT | pending/done/error/needs_ocr |

### `unmapped_affectations`
Tipos de afectación que no se mapearon al enum (backlog para ampliar vocabulario).

### `vigencia_discrepancies`
Normas donde `vigencia` (Socrata) ≠ `estado_documento` (SUIN).

## Layout del Cache

```
data/
├── catalog.db                          # SQLite (catálogo + logs)
└── raw/
    └── suin/                           # Fuente: SUIN-Juriscol
        ├── LEY/
        │   ├── 1687091/                # suin_id de Ley 1712/2014
        │   │   ├── 1687091_a1b2c3d4_20260612T030000Z.html
        │   │   ├── metadata.json       # Procedencia
        │   │   └── parsed.json         # Output del parser
        │   └── .../
        ├── DECRETO/
        └── .../
```

La **fuente de verdad** es el cache raw en filesystem. Todo lo demás (SQLite, parsed.json) se regenera desde ahí.

## Vocabulario de Afectaciones

| Enum | Raw strings que mapea |
|------|----------------------|
| `MODIFICA` | Modificado, Modificado parcialmente, Modifica |
| `DEROGA_TOTAL` | Derogado, Deroga, Derogado totalmente, Derogado tácitamente |
| `DEROGA_PARCIAL` | Derogado parcialmente, Deroga parcialmente |
| `ADICIONA` | Adicionado, Adiciona |
| `CORRIGE_YERRO` | Corregido yerro, Corrección de yerro |
| `EXEQUIBLE` | Declarado exequible, Exequible |
| `INEXEQUIBLE` | Declarado inexequible, Inexequible |
| `EXEQUIBLE_CONDICIONADA` | Declarado condicionalmente exequible, Exequible condicionado/a |
| `REGLAMENTA` | Reglamentado, Reglamentado parcialmente, Reglamenta |
| `COMPILA` | Compilado, Compila |
| `SUSTITUYE` | Sustituido, Sustituye |
| `SUSPENDE` | Suspendido, Suspende |
| `PRORROGA` | Prorrogado, Prorroga |
| `ACLARA` | Aclarado, Aclara |
| `UNKNOWN` | Cualquier string no mapeado → se loggea en `unmapped_affectations` |

## Decisiones de Diseño

- **Resolución de suin_id**: Se usa el índice CLP de SUIN (`/clp/contenidos.dll/{Tipo}`) que lista todas las normas con sus IDs internos. Esto es mucho más eficiente que buscar uno por uno y resuelve el problema de ambigüedad para LEYes (clave única por número+año).
- **Discrepancias de vigencia**: Se guardan ambos valores (catálogo Socrata = snapshot, SUIN = más actual). Ante discrepancia gana la página de SUIN. Las discrepancias se loggean para detectar rezago del catálogo.
- **Normas escaneadas**: Se guardan siempre (disco barato), con `parse_status=needs_ocr`. Son deuda procesable cuando se monte OCR en Fase 3.
- **Namespace de fuente**: `data/raw/suin/` permite agregar `data/raw/senado/` y `data/raw/relatoria/` sin migración.
