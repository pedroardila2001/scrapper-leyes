# Plan de Mapeo Completo — Jurisprudencia · Doctrina Administrativa · Territorial · Antecedentes

> Objetivo: completar el mapeo del ordenamiento jurídico colombiano en las capas que hoy
> faltan (B jurisprudencia, C doctrina administrativa, D territorial + antecedentes), dejar
> cada fuente **correcta y funcionando en orden**, y recién al final ejecutar la **indexación
> masiva** (catálogo → scrape → chunk → vector + grafo).
>
> Este documento es **plan ejecutable**, no implementación. Complementa
> `PLAN_INTEGRACION_FUENTES.md` (F0–F6) con el reconocimiento técnico verificado (2026-06).
> Fecha: 2026-06-22.

---

## 0. Principio rector (el hallazgo que simplifica todo)

El reconocimiento de fuentes reveló que **la gran mayoría del corpus faltante se descarga por
ID entero enumerable + GET directo**, no por buscadores complejos. Esto convierte "mapear todo"
en, sobre todo, **barridos de rangos de ID** + un crawler genérico de normogramas + semillas
Socrata. Los buscadores con estado (JSF/WebForms) quedan como minoría.

| Fuente | Mecanismo de descubrimiento | Dificultad |
|---|---|---|
| Función Pública EVA | `norma.php?i={INT}` secuencial 1..~261.000 | **1/5** |
| Régimen Bogotá (sisjur) | `Norma1.jsp?i={INT}` secuencial ~20k..188k | **1/5** |
| WebRelatoria CSJ/CE (texto) | `FileReferenceServlet?corp=&ext=&file={INT}` (GET sin sesión) | **1/5** |
| Secretaría Senado | patrón determinístico `ley_{NNNN}_{AAAA}.html` | **2/5** |
| Familia normograma (DIAN, Supersalud, SSPD…) | crawl del árbol `/docs/arbol/{id}.htm` | **2/5** |
| Socrata (CC + nichos CE, proyectos, tratados) | API JSON/CSV directa (sin scraping) | **1/5** |
| JSF/WebForms (Gaceta, Diario Oficial, SAMAI, búsqueda CSJ/CE) | POST + ViewState | **3/5** |
| Ordenanzas/acuerdos municipales | dispersos, sin consolidado nacional | **5/5** (postergar) |

**Implicación arquitectónica:** con **4 tipos de discoverer reutilizables** se cubre >90% del
volumen. El resto es parsing por fuente.

---

## 1. Estado actual (lo que ya existe)

- **F0 hecho**: `catalog` multi-fuente (`source`/`external_id`/`source_url`/`canonical_id`),
  `SocrataCatalogSource` genérico, contratos `BaseDiscoverer`/`CatalogSeed`/`BaseIndexer`/`BaseScraper`.
- **`factory.get_discoverer` lanza `NotImplementedError`** — no hay ningún discoverer construido.
  `CRAWL_SOURCES = [csj, consejo_estado]`.
- CSJ/CE: scrapers con `_build_url` (adivina URL determinística) pero **sin enumeración** → 0 datos
  (no saben *qué* documentos existen).
- CC: sembrable desde Socrata `v2k4-2t8s` (29.351 filas) + URL de relatoría; parser de sentencias
  (sectionizer + RESUELVE) ya **sólido** (hecho 2026-06-19).
- Vocabulario `TIPO_CANONICAL` ya incluye `concepto`, `tratado`, `auto`, `proyecto_ley`,
  `exposicion_motivos`, `decision_can`, `fallo_disciplinario`, `opinion_consultiva` → la gramática
  de `canonical_id` ya soporta las capas C/D.
- Cuello de botella real: scraper **1 req/s, secuencial**. Sin concurrencia + reanudabilidad,
  ninguna fase de cobertura escala.

---

## 2. Arquitectura faltante (transversal — habilita todas las fases)

Antes de las fuentes, hay que construir piezas reutilizables. **Estas son prerrequisito.**

### 2.1 Discoverers genéricos (4 clases cubren casi todo)
Implementar contra el contrato `BaseDiscoverer.discover() -> Iterator[CatalogSeed]`:

1. **`IDSweepDiscoverer`** — barre un rango de enteros contra una plantilla de URL, detecta
   404/redirect/"no existe", emite `CatalogSeed` por hit. Parametrizable: `url_template`,
   `id_range`, `not_found_detector`. Cubre **EVA, sisjur Bogotá, WebRelatoria (FileReferenceServlet)**.
2. **`NormogramaCrawler`** — recorre el árbol `/docs/arbol/{id}.htm` de la familia normograma y
   recolecta slugs de documento. Un solo crawler para **DIAN, Supersalud, SSPD (normograma.info), MinTIC**.
3. **`SocrataSeedDiscoverer`** — ya casi existe (`SocrataCatalogSource`); generalizar para registrar
   N datasets (CC + nichos). Sin scraping: descarga JSON/CSV.
4. **`JSFDiscoverer` / `WebFormsDiscoverer`** — emula el ciclo POST + `javax.faces.ViewState`
   (PrimeFaces) o `__VIEWSTATE`/`__EVENTVALIDATION` (ASP.NET) para paginar resultados de búsqueda.
   Cubre **Gaceta del Congreso, Diario Oficial, SAMAI, y la búsqueda de metadatos CSJ/CE**.
   Fallback a Playwright (ya disponible) solo si hay tokens generados por JS.

### 2.2 Concurrencia + reanudabilidad en el scraper (DESBLOQUEANTE)
- Pool asíncrono con límite de concurrencia respetando `RATE_LIMIT_RPS` por dominio.
- Reanudable vía `scrape_status` (ya existe) — reintentos con backoff, marca `error`/`not_found`.
- Sin esto, bajar cientos de miles de documentos es inviable.

### 2.3 Parsers por familia de documento
- **Sentencias** → `LegalMapper` + heurísticas de sección por corte (CC ya hecho; CSJ por sala;
  CE por sección). Extender el sectionizer (`sentencia_sections.py`) con encabezados por corte.
- **Conceptos/doctrina** (DIAN, supers, EVA) → parser ligero nuevo: encabezado, problema/tesis,
  fuente normativa, fecha. Estructura más simple que una sentencia.
- **Normas territoriales / Senado** → reusar el parser de normas (artículos).
- **Antecedentes** (Gaceta) → parser de PDF (proyecto, exposición de motivos, ponencia).

### 2.4 Deduplicación canónica (clave anti-ruido)
La misma Ley 1712/2014 vendrá de SUIN + Senado + Bogotá; la misma sentencia de Socrata-CC +
WebRelatoria + SUIN. Resolver `canonical_id` tras parsear y **unificar en un nodo lógico** con
múltiples `source_url` como procedencia. El índice `(canonical_id, source)` ya está; falta la
capa de merge en `export_*`.

### 2.5 Tipos y `source` nuevos en el registro
Registrar en `factory.SOURCES`/`CRAWL_SOURCES` las fuentes nuevas y asegurar que el catálogo
acepte los `tipo` de doctrina/antecedentes (vocabulario ya en `TIPO_CANONICAL`).

---

## 3. Fuentes por capa (acceso verificado + dificultad)

### Capa B — JURISPRUDENCIA
| Fuente | Acceso verificado | Texto | Metadatos | Dif. |
|---|---|---|---|---|
| **Corte Constitucional** | Socrata `v2k4-2t8s` (29.351 filas, campos `sentencia/sentencia_tipo/magistrado_a/sala/fecha_sentencia/sv_spv/av_apv`) + relatoría URL determinística | GET relatoría | Socrata JSON | **1–2/5** |
| **CSJ (Civil/Penal/Laboral/Tutelas)** | WebRelatoria JSF. **Texto: `FileReferenceServlet?corp=csj&ext=pdf&file={INT}`** (IDs ~344k–937k). Metadatos: POST+ViewState | GET por file ID | JSF | **1/5** texto · **3/5** metadatos |
| **Consejo de Estado** | WebRelatoria JSF (`corp=ce`, file ~230k–2.19M) para histórico; **SAMAI** (ASP.NET, desde 01-dic-2021) para reciente | GET por file ID | JSF + WebForms | **1/5** texto · **3/5** metadatos |
| **Nichos Socrata CE** | `shrb-iwqu` (jurisp. indígena CE, 1920+), `ukfp-srim`/`njuz-uxyd` (acciones populares), `3ipn-fy7x` (Marco Jurídico Paz), `9wd9-se7y` (DNDA) | — | Socrata JSON | **1/5** |
| **SUIN jurisprudencia de control** | ya en el scraper SUIN (incl. ex-Sala Const. CSJ 1910-1991, único) | reusar SUIN | — | **2/5** |
| **JEP** | portal propio | crawl | — | **4/5** (nicho) |

### Capa C — DOCTRINA ADMINISTRATIVA
| Fuente | Acceso verificado | Dif. |
|---|---|---|
| **Función Pública EVA** ★ empezar aquí | `norma.php?i={INT}` / `norma_pdf.php?i={INT}`, secuencial 1..~261k, httpx. Incluye normas + conceptos + jurisprudencia | **1/5** |
| **DIAN normograma** | crawl árbol → `oficio_dian_{n}_{año}.htm` (ID NO secuencial). Doctrina tributaria/aduanera/cambiaria | **2/5** |
| **SSPD (normograma.info/ssppdd)** | crawl `/docs/arbol/{id}.htm` → `concepto_superservicios_{7dig}_{año}.htm` | **2/5** |
| **Supersalud** | normograma `.htm` clásico, crawl índice | **2/5** |
| **SIC** | `buscadorconceptos.sic.gov.co` + `relatoria.sic.gov.co` (API JSON probable, investigar con Playwright); portal `?page=N` GET | **3/5** |
| **Supersociedades** | `tesauro.supersociedades.gov.co/jsonviewer/{id}` (API JSON potencial) + portal ASPX | **3/5** |
| **SFC (Financiera)** | listados por año + plataforma interactiva de circulares (headless) | **3/5** |
| **Mintrabajo / Minsalud / Banrep / CGN** | normogramas/listados dispersos | **3/5** |

> Atajo: **`normograma.info` es plataforma compartida** (varias entidades bajo namespaces). Un solo
> `NormogramaCrawler` desbloquea DIAN, Supersalud, SSPD, MinTIC, SENA con el mismo parser.

### Capa D — TERRITORIAL + ANTECEDENTES
| Fuente | Acceso verificado | Dif. |
|---|---|---|
| **Régimen Bogotá (sisjur)** ★ | `Norma1.jsp?i={INT}` secuencial ~20k–188k, **httpx verificado** (HTML server-rendered con metadatos) | **1/5** |
| **Secretaría del Senado** | `secretariasenado.gov.co/senado/basedoc/ley_{NNNN}_{AAAA}.html`, determinístico, httpx. Texto consolidado con concordancias (validación/enriquecimiento) | **2/5** |
| **Gaceta del Congreso** | JSF `svrpubindc.imprenta.gov.co/gacetas/`; doc por GET/PDF. Proyectos, exposiciones de motivos, ponencias | **3/5** |
| **Diario Oficial** | `imprenta.gov.co/diario-oficial`, buscable por nº/ley/fecha → PDF. **Clave para fecha oficial de promulgación** (alimenta vigencia temporal) | **3/5** (4/5 históricos) |
| **Socrata antecedentes** | `feim-cysj` (proyectos de ley Senado), `fdir-hk5z` (tratados) | **1/5** |
| **Ordenanzas / acuerdos municipales** | dispersos, sin consolidado nacional | **5/5** (postergar/muestreo) |

---

## 4. Fases ordenadas (con dependencias)

> Cada fase: construir → **spike de verificación** (§5) → parser → probar en muestra (10–50 docs)
> → validar dedup/grafo/vigencia → recién entonces marcar lista. La indexación masiva NO ocurre
> hasta §6.

| Fase | Entrega | Depende de | Esfuerzo |
|---|---|---|---|
| **M0** | Arquitectura transversal: `IDSweepDiscoverer`, `NormogramaCrawler`, scraper concurrente + reanudable, dedup canónica, parser de conceptos | — | **L** (desbloqueante) |
| **M1** | **CC completa** (Socrata `v2k4-2t8s` + nichos + relatoría) | M0 | S |
| **M2** | **Doctrina C — vía ID/normograma**: EVA (IDSweep) → DIAN/SSPD/Supersalud (NormogramaCrawler) | M0 + parser conceptos | M |
| **M3** | **Territorial + Senado**: sisjur Bogotá (IDSweep) + Senado (determinístico) | M0 | M |
| **M4** | **WebRelatoria CSJ + CE**: texto por `FileReferenceServlet` (IDSweep), metadatos por `JSFDiscoverer`; SAMAI (WebForms) para CE reciente | M0 + parsers por corte | **L** |
| **M5** | **Antecedentes + Diario Oficial**: Socrata `feim-cysj` + Gaceta (JSF) + Diario Oficial (fecha promulgación → vigencia) | M0 + parser PDF | M |
| **M6** | **Doctrina C — buscadores**: SIC, Supersociedades, SFC (APIs JSON/Playwright) | M0 + Playwright | M |
| **M7** | **Nicho/escala**: JEP, ordenanzas/acuerdos (muestreo), tribunales | M0 | L |

**Orden recomendado por valor/esfuerzo:** M0 → M1 → M2 → M3 → M4 → M5 → M6 → M7.
M1+M2+M3 dan el mayor salto con discoverers triviales (IDs enumerables). M4 es el grande de
jurisprudencia. M5 cierra la vigencia temporal real (Diario Oficial).

---

## 5. Spikes de verificación (correr ANTES de codear cada fuente)

WebFetch estuvo bloqueado durante el reconocimiento; estos GET confirman estático-vs-JS, rango de
IDs y esquemas antes de invertir en cada scraper:

```bash
# Socrata: contar filas y columnas reales (M1, M5)
curl -s "https://api.us.socrata.com/api/catalog/v1?domains=www.datos.gov.co&categories=Justicia%20y%20Derecho&limit=200"
curl -s "https://www.datos.gov.co/resource/v2k4-2t8s.json?\$select=count(1)"
curl -s "https://www.datos.gov.co/api/views/shrb-iwqu.json" | jq '.columns[].fieldName'

# EVA: confirmar estático + rango/densidad de IDs (M2)
curl -s "https://www.funcionpublica.gov.co/eva/gestornormativo/norma.php?i=238096" | head -c 2000

# DIAN / SSPD: árbol del normograma + un documento (M2)
curl -s "https://normograma.info/ssppdd/docs/arbol/54832.htm" | head -c 2000
curl -s "https://normograma.dian.gov.co/dian/compilacion/docs/oficio_dian_1734_2019.htm" | head -c 2000

# sisjur Bogotá: estático + metadatos (M3)
curl -s "https://www.alcaldiabogota.gov.co/sisjur/normas/Norma1.jsp?i=188557" | head -c 2000

# WebRelatoria: descarga directa por file ID (M4)
curl -sI "https://consultajurisprudencial.ramajudicial.gov.co/WebRelatoria/FileReferenceServlet?corp=csj&ext=pdf&file=937015"

# Senado: patrón determinístico (M3)
curl -sI "http://www.secretariasenado.gov.co/senado/basedoc/ley_1712_2014.html"
```
Además: para CSJ/CE y SAMAI/Gaceta, capturar **una sesión real con Playwright** para extraer los
nombres de campos del formulario y el flujo de paginación (lo único no determinable sin HTML crudo).

---

## 6. Calidad transversal (debe quedar correcto ANTES de la indexación masiva)

1. **Dedup canónica activa** en `export_neo4j`/`export_vector`: un `canonical_id` = un nodo,
   N procedencias. Probar con una ley presente en SUIN + Senado + Bogotá.
2. **Diario Oficial → fecha de promulgación** conectada al resolver de vigencia temporal
   (`vigencia.py`/`vigencia_graph.py`): hoy se infiere de `previous_versions`; con la fecha oficial
   se ancla de verdad.
3. **OCR** para normas/sentencias escaneadas (`needs_ocr`): docling ya está en la imagen `pipeline`.
4. **Parsers por corte** validados con muestra (CSJ por sala, CE por sección) — no asumir el formato CC.
5. **Eval mínima de recuperación** sobre cada fuente nueva (¿el chunk correcto sale top-k?).

---

## 7. Indexación masiva final (el cierre — recién cuando §2–6 estén verdes)

Secuencia de extremo a extremo, por fuente, vía el servicio `pipeline` (ya con docling):

```bash
# 1) Catálogo (siembra metadatos)
docker compose run --rm pipeline catalog sync --dataset cc_sentencias          # M1
docker compose run --rm pipeline catalog discover --source funcion_publica     # M2 (IDSweep)
docker compose run --rm pipeline catalog discover --source sisjur_bogota        # M3
docker compose run --rm pipeline catalog discover --source csj --via filerefs   # M4
# … una por fuente

# 2) Scrape concurrente + reanudable (baja el texto)
docker compose run --rm pipeline scrape run --source <fuente> --concurrency N

# 3) Parse (sentencias/conceptos/normas) — incremental
docker compose run --rm pipeline scrape reparse-sentencias                       # ya existe
docker compose run --rm pipeline scrape reparse --source <fuente>

# 4) Exportar a los stores (chunk → vector + grafo), con dedup canónica
docker compose run --rm pipeline export vector
docker compose run --rm pipeline export graph
```

**Regla de oro:** indexar **fuente por fuente**, validando cobertura/dedup/grafo tras cada una,
no todo de golpe. El scraper a escala (cientos de miles de docs) corre como job de larga duración
con reanudabilidad.

---

## 8. Riesgos y mitigaciones

- **Legal/ético:** todas son fuentes públicas oficiales; respetar `robots.txt`, rate-limit y
  `User-Agent` con contacto. No re-publicar como oficial.
- **JSF/WebForms con ViewState:** emular el flujo POST; Playwright como fallback solo si hay tokens
  por JS. Confirmar en el spike.
- **IDs con huecos:** EVA/sisjur/FileReferenceServlet tienen IDs borrados → esperar 404 en una
  fracción; el barrido debe tolerarlo sin abortar.
- **Escala:** cientos de miles de documentos → scraper concurrente + reanudable es prerrequisito (M0).
- **Heterogeneidad de parsers:** presupuestar iteración de heurísticas + casos de prueba por fuente.
- **Diario Oficial históricos:** pueden requerir sesión/pago → cubrir lo accesible, marcar el resto.

---

## Apéndice — Datasets Socrata verificados (datos.gov.co)

| Dataset | Contenido | Capa |
|---|---|---|
| `v2k4-2t8s` | Sentencias Corte Constitucional (29.351 filas) | B |
| `ujr7-jwzm` | Sentencias CC (variante con texto/extracto) | B |
| `shrb-iwqu` | Jurisprudencia indígena Consejo de Estado (1920+) | B |
| `9wd9-se7y` | Histórico sentencias DNDA (derechos de autor) | B |
| `ukfp-srim` / `njuz-uxyd` | Acciones populares/grupo — sentencias CE | B |
| `3ipn-fy7x` | Sentencias Marco Jurídico para la Paz / Fast Track | B |
| `feim-cysj` | Proyectos de ley del Senado | D |
| `fdir-hk5z` | Tratados internacionales de Colombia | A |
