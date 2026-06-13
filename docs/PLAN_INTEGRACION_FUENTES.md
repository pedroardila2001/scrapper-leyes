# Plan de Integración de Fuentes — Sistema Legal Colombiano

> Objetivo: mapear **lo más completo posible** el ordenamiento jurídico colombiano
> (legislación + jurisprudencia de todas las cortes + doctrina administrativa) para
> alimentar las 2 herramientas del deep-agent (búsqueda vectorial + grafo de conocimiento).
>
> Estado de este documento: **plan** (no implementación). Fecha: 2026-06-13.

---

## 1. Estado actual (lo que ya mapeamos)

| Componente | Estado | Detalle |
|---|---|---|
| Catálogo de normas | ✅ | Socrata `fiev-nid6` (datos.gov.co) → tabla `catalog` (~15.726 normas). |
| Scraper SUIN | ✅ completo | Índice CLP → `viewDocument.asp?id=` → `html_parser.py`. Legislación (LEY, DECRETO, RESOLUCIÓN, ACTO LEGISLATIVO, etc.). ~33 normas bajadas. |
| Corte Constitucional | 🟡 scaffold | `cc_scraper.py`: URL determinística a relatoría, **sin** indexación remota. 11 sentencias bajadas. Parser = `LegalMapper` (Docling). |
| Corte Suprema (CSJ) | 🟡 scaffold | `csj_scraper.py`: URL por sala/año, **sin** índice remoto. **0 datos**. |
| Consejo de Estado (CE) | 🟡 scaffold | `ce_scraper.py`: URL por año, **sin** índice remoto. **0 datos**. |
| Chunking + vectores | ✅ (fase previa) | `chunking.py` + `export_vector.py` (bge-m3 + BM25, vigencia). |
| Grafo (Neo4j) | ✅ parcial | `export_neo4j.py` (normas, artículos, sentencias, citaciones). |

**Deuda arquitectónica que bloquea multi-fuente** (ver §5):
1. `catalog` es **SUIN-céntrico**: `suin_id` es el único ID externo; `source` vive en `scrape_log`, no en `catalog`.
2. El pipeline solo soporta el modo **catalog-driven** (Socrata siembra → resolver → scrapear). Las relatorías de las cortes necesitan modo **crawl-driven** (el propio buscador enumera los documentos).
3. `socrata_client.py` tiene **hardcodeado** el dataset `fiev-nid6` y su mapeo de campos.
4. `LegalMapper` (parser de sentencias) usa heurísticas genéricas; cada corte estructura distinto.

---

## 2. Modelo del ordenamiento jurídico colombiano

Para "mapearlo todo" hay que cubrir **4 capas**. El producto hoy cubre bien la capa A y arranca la B.

```
A. LEGISLACIÓN          Constitución · actos legislativos · leyes · decretos ·
   (normas generales)   resoluciones · circulares · directivas · códigos
                        Fuente primaria: Diario Oficial. Consolidado: SUIN.

B. JURISPRUDENCIA       Corte Constitucional · Corte Suprema (Civil/Penal/Laboral) ·
   (precedente)         Consejo de Estado (Secc. 1-5 + Consulta) · JEP ·
                        ex-Sala Constitucional CSJ (1910-1991) · Tribunales

C. DOCTRINA ADMIN.      Conceptos y circulares de entidades: DIAN, Superintendencias
   (interpretación)     (Financiera, Sociedades, SIC, Salud, SSPD), Función Pública…

D. TERRITORIAL +        Ordenanzas, acuerdos, decretos distritales (Régimen Bogotá) ·
   ANTECEDENTES         Gaceta del Congreso / proyectos de ley (trazabilidad)
```

---

## 3. Inventario de fuentes (matriz priorizada)

**Modo**: `catálogo` = un dataset/índice siembra metadatos y luego se baja el texto · `crawl` = hay que recorrer un buscador/relatoría para descubrir documentos.

| # | Fuente | Capa | Qué aporta | Cobertura | Acceso | Vol. aprox. | Modo | Prio |
|---|--------|------|-----------|-----------|--------|------------|------|------|
| 1 | **Socrata `fiev-nid6`** (datos.gov.co) | A | Catálogo de normas (metadatos) | — | API Socrata JSON | ~16k+ | catálogo | ✅ hecho |
| 2 | **SUIN-Juriscol** | A+B | Texto de normas + afectaciones + **jurisprudencia de control** (CC, CE, ex-Sala Const. CSJ 1910-1991) | 1864→hoy | HTML (`viewDocument.asp`), índice CLP | **85.000+** disposiciones | catálogo | ✅ scraper |
| 3 | **Socrata `v2k4-2t8s`** (Corte Const.) | B | Catálogo de **todas** las sentencias CC (metadatos: tipo, nº, MP, sala, fecha) | **1992→hoy** | API Socrata JSON (mensual) | **29.310** | catálogo | 🔴 ALTA |
| 4 | **Relatoría Corte Constitucional** | B | Texto completo de sentencias (C/T/SU/A) | 1992→hoy | HTML `relatoria/{año}/{sent}.htm` + `buscador_new` (export Excel, máx 5.000/consulta) | 29k | catálogo→scrape | 🔴 ALTA |
| 5 | **WebRelatoria CSJ** (ramajudicial) | B | Sentencias Corte Suprema: Salas Civil, Penal, Laboral, Plena/Tutelas | histórico→hoy | App JSF `consultajurisprudencial.ramajudicial.gov.co/WebRelatoria/csj` | decenas de miles | crawl | 🔴 ALTA |
| 6 | **WebRelatoria CE** (ramajudicial) | B | Consejo de Estado: Secciones 1-5 + Sala de Consulta + unificación | histórico→hoy | `jurisprudencia.ramajudicial.gov.co/WebRelatoria/ce` + **Mi Relatoría/SAMAI** (2021-12+) | decenas de miles | crawl | 🔴 ALTA |
| 7 | **DIAN Normograma** | C | Conceptos/oficios tributarios, aduaneros, cambiarios | amplio | HTML `normograma.dian.gov.co/.../docs/` | miles | crawl/catálogo | 🟠 MEDIA |
| 8 | **Función Pública – Gestor Normativo (EVA)** | A+C | Normas + conceptos + jurisprudencia sector público | amplio | HTML `gestornormativo/norma.php?i=ID` | miles | crawl by-id | 🟠 MEDIA |
| 9 | **Superintendencias** (Financiera, Sociedades, SIC, Salud, SSPD) | C | Circulares externas + conceptos por sector | por entidad | normogramas HTML por super | miles | crawl | 🟠 MEDIA |
| 10 | **Régimen Legal de Bogotá** (sisjur) | A+D | Normativa distrital **consolidada** + doctrina + jurisprudencia asociada | Distrito Capital | `alcaldiabogota.gov.co/sisjur` (consulta avanzada) | miles | crawl | 🟡 MEDIA-BAJA |
| 11 | **Secretaría del Senado** | A | Leyes y **códigos consolidados con concordancias** (texto de alta calidad) | leyes/códigos | HTML `secretariasenado.gov.co/leyes-de-la-republica` | ~miles | crawl | 🟡 (validación de texto) |
| 12 | **Diario Oficial** (Imprenta Nacional) | A | Fuente **primaria** de promulgación (fecha oficial, texto original) | 1864→hoy | portal Imprenta / PDF | enorme | crawl/PDF | 🟡 (fecha vigencia) |
| 13 | **JEP** | B | Jurisprudencia justicia transicional | 2018→hoy | portal JEP | cientos | crawl | 🟢 NICHO |
| 14 | **Gaceta del Congreso** (Imprenta) | D | Proyectos de ley, exposiciones de motivos, antecedentes | histórico→hoy | `svrpubindc.imprenta.gov.co` | enorme | crawl | 🟢 NICHO |
| 15 | **Tribunales Superiores/Admin.** | B | Jurisprudencia de segunda instancia | — | WebRelatoria / CPNU | masivo | crawl | 🟢 (escala futura) |

> Nota de cobertura: SUIN (#2) **ya incluye** jurisprudencia de control constitucional, lo que da un atajo histórico (incluida la ex-Sala Constitucional de la CSJ 1910-1991, que ninguna otra fuente expone fácilmente).

---

## 4. Estrategia: dos atajos de alto impacto

1. **Sembrar catálogos desde Socrata, no rastrear.** Igual que `fiev-nid6` siembra normas,
   `v2k4-2t8s` siembra **las 29.310 sentencias de la Corte Constitucional** con metadatos limpios
   (tipo, número, magistrado, sala, fecha). Solo falta bajar el texto de la relatoría con URL
   determinística. Esto convierte la CC de "scaffold" a "completo" con poco esfuerzo.
2. **Una integración cubre dos cortes.** CSJ y CE comparten la plataforma **WebRelatoria** de la
   Rama Judicial. Un solo cliente `WebRelatoria` (parametrizado por corte) cubre #5 y #6, y deja
   la puerta abierta a tribunales (#15).

---

## 5. Patrón de integración (cómo encaja en el código)

### 5.1 Generalizar el catálogo a multi-fuente

`catalog` debe dejar de asumir SUIN. Cambios propuestos (migración aditiva, no destructiva):

```sql
ALTER TABLE catalog ADD COLUMN source TEXT;          -- 'suin' | 'corte_constitucional' | 'csj' | 'ce' | 'dian' | ...
ALTER TABLE catalog ADD COLUMN external_id TEXT;     -- id en la fuente (generaliza suin_id)
ALTER TABLE catalog ADD COLUMN source_url TEXT;      -- URL canónica del documento
ALTER TABLE catalog ADD COLUMN canonical_id TEXT;    -- co:... (clave de dedup entre fuentes)
-- Unicidad real pasa a ser (canonical_id, source); la UNIQUE(tipo,numero,anio,entidad)
-- actual no distingue sentencias por corte/sala.
```

`suin_id` se mantiene por compatibilidad pero `external_id`+`source` es el par genérico.
`canonical_id` (ya definido en `models.py`) es la **clave de deduplicación**: la misma Ley 1712/2014
puede venir de SUIN, Senado y Régimen Bogotá → un nodo lógico, varias procedencias.

### 5.2 Dos modos de ingesta (hoy solo existe el primero)

```
Modo A — catalog-driven (SUIN, CC vía Socrata):
  Socrata/índice → siembra catalog → Indexer.resolve_batch → Scraper.scrape_batch → cache → chunk

Modo B — crawl-driven (CSJ, CE, DIAN, supers):
  Discoverer.crawl (recorre buscador/relatoría) → siembra catalog (con external_id+source_url)
                                                 → Scraper.scrape_batch → cache → chunk
```

Hay que añadir un tercer rol al contrato de `base.py`, junto a `BaseIndexer`/`BaseScraper`:

```python
class BaseDiscoverer:
    """Enumera documentos de una fuente sin catálogo previo (relatorías, normogramas)."""
    def discover(self, *, desde: date | None, hasta: date | None,
                 filtro: dict | None) -> Iterator[CatalogSeed]: ...
```

`ScraperFactory` (factory.py:14) gana las nuevas `source` y opcionalmente un `get_discoverer(source)`.
El layout de cache `data/raw/{source}/{tipo}/{id}/` **ya soporta** fuentes nuevas sin migración.

### 5.3 Generalizar `socrata_client.py`

Extraer un `SocrataCatalogSource(dataset_id, field_map, tipo_fijo=None)` para registrar N datasets:
- `fiev-nid6` → normas (ya).
- `v2k4-2t8s` → sentencias CC (`sentencia`→numero, `magistrado_a`→magistrado_ponente, `sala`, `fecha_sentencia`→anio, `corte='cc'`).
- (verificar si existen datasets análogos para CE/CSJ; si no, esas van por modo B).

### 5.4 Parsers por fuente

- Normas SUIN → `html_parser.py` (sirve tal cual).
- Sentencias → `LegalMapper` (Docling) + **heurísticas de sección por corte** (CC: ANTECEDENTES/CONSIDERACIONES/RESUELVE; CSJ: distinto por sala; CE: por sección). Extender `_map_sections`.
- Conceptos (DIAN/supers) → parser ligero nuevo (encabezado, problema jurídico, tesis, fuente).

### 5.5 Resolución canónica / deduplicación

Tras parsear, resolver `canonical_id` y unificar procedencias. La misma sentencia puede aparecer en
Socrata-CC, SUIN y WebRelatoria → guardar todas las `source_url` como procedencia, un solo nodo en el grafo.

---

## 6. Roadmap por fases

| Fase | Entrega | Fuentes | Esfuerzo | Resultado |
|------|---------|---------|----------|-----------|
| **F0** ✅ | Refactor multi-fuente | — | M | **HECHO.** `source`/`external_id`/`source_url`/`canonical_id` en catalog (migración + backfill); `SocrataCatalogSource` genérico + dataset `cc_sentencias` (v2k4-2t8s); `BaseDiscoverer`/`CatalogSeed`; `factory.get_discoverer`. **Habilita todo lo demás.** |
| **F1** | Corte Constitucional completa | #3, #4 | S | Siembra 29.310 sentencias desde `v2k4-2t8s`; scrape relatoría; parser CC. **Gran salto de cobertura jurisprudencial con poco esfuerzo.** |
| **F2** | Jurisprudencia histórica vía SUIN | #2 (jurisp.) | S | Activar la rama de jurisprudencia de SUIN (CC, CE, ex-Sala Const. CSJ 1910-1991). Reutiliza scraper SUIN. |
| **F3** | WebRelatoria (CSJ + CE) | #5, #6 | L | Cliente `WebRelatoria` parametrizable + discoverer crawl + parsers por sala/sección + Mi Relatoría/SAMAI para CE reciente. |
| **F4** | Doctrina administrativa | #7, #8, #9 | M | DIAN normograma + Función Pública EVA + supers. Alto valor tributario/regulatorio. |
| **F5** | Consolidados y territorial | #10, #11, #12 | M | Régimen Bogotá, Senado (validación de texto), Diario Oficial (fecha de promulgación → alimenta vigencia temporal). |
| **F6** | Nicho y escala | #13, #14, #15 | L | JEP, Gaceta del Congreso, tribunales. |
| **Transversal** | Calidad | todas | — | Dedup canónica · OCR (normas escaneadas, `needs_ocr`) · reranker · **resolver de vigencia temporal** (usa Diario Oficial + afectaciones). |

**Orden recomendado:** F0 → F1 → F2 → F3 → F4 → F5 → F6.
F1+F2 dan el mayor salto de cobertura jurisprudencial por unidad de esfuerzo (catalog-driven, reusa lo existente). F3 es el grande (CSJ/CE reales).

---

## 7. Riesgos y consideraciones

- **Legal/ético:** todas son fuentes **públicas oficiales** de difusión del ordenamiento; uso para investigación. Respetar `robots.txt`, rate-limit (`RATE_LIMIT_RPS`, hoy 1.0) y `User-Agent` con contacto. No re-publicar como oficial (SUIN advierte que su copia es informativa).
- **SSL de SUIN:** el sitio tiene **cadena de certificados incompleta** → el cliente HTTP debe manejarlo explícitamente (verificación custom), no desactivar TLS a ciegas.
- **Apps JSF (WebRelatoria):** mantienen estado de sesión/ViewState → el discoverer debe emular el flujo (POST con tokens), no solo GET. Posible necesidad de navegador headless (Playwright) si hay JS pesado.
- **Escala:** 85k normas (SUIN) + 29k sentencias CC + decenas de miles CSJ/CE → cientos de miles de documentos. El scraper secuencial a 1 rps no escala; F0 debería introducir **concurrencia respetando rate-limit** y reanudabilidad (ya hay `scrape_status`).
- **Heterogeneidad de parsers:** cada corte/entidad estructura distinto → presupuestar iteración de heurísticas + casos de prueba por fuente.
- **Volumen vs. señal:** tribunales (#15) son masivos y de menor valor de precedente → dejar para el final / muestreo.

---

## 8. Spikes de verificación (antes de implementar cada fase)

1. **F1:** confirmar patrón de URL de relatoría CC para todos los tipos (C/T/SU/A) y años; medir tasa de 404 vs. `v2k4-2t8s`. Verificar si `buscador_new` expone JSON (no solo Excel).
2. **F3:** capturar el flujo real de `WebRelatoria` (parámetros de búsqueda, paginación, ViewState) para CSJ y CE; evaluar si `Mi Relatoría`/SAMAI tiene endpoint consultable.
3. **F2:** localizar el índice de jurisprudencia dentro de SUIN (¿CLP análogo al de normas?) y su mapeo a `tipo=SENTENCIA`.
4. **F4:** patrón de id en `normograma.dian.gov.co` y en `gestornormativo/norma.php?i=` (rango de ids, sitemap).
5. **Datos abiertos:** barrer datos.gov.co por categoría "Justicia y Derecho" buscando datasets-semilla análogos a `v2k4-2t8s` para CE/CSJ.

---

## Fuentes consultadas

- SUIN-Juriscol — alcance (85.000+ disposiciones, 1864→, jurisprudencia de control): https://www.suin-juriscol.gov.co/suinjuriscol.html
- Dataset Socrata sentencias Corte Constitucional `v2k4-2t8s` (29.310 filas, 1992→2026, metadatos): https://www.datos.gov.co/Justicia-y-Derecho/Sentencias-proferidas-por-la-Corte-Constitucional/v2k4-2t8s
- Buscador de jurisprudencia Corte Constitucional: https://www.corteconstitucional.gov.co/relatoria/buscador_new/
- Consulta de Jurisprudencia CSJ (WebRelatoria): https://consultajurisprudencial.ramajudicial.gov.co/WebRelatoria/csj/index.xhtml
- Consulta de Jurisprudencia Consejo de Estado (WebRelatoria): https://jurisprudencia.ramajudicial.gov.co/WebRelatoria/ce/index.xhtml
- Consejo de Estado — buscador / Mi Relatoría (SAMAI): https://www.consejodeestado.gov.co/buscador-de-jurisprudencia2/
- Función Pública — Gestor Normativo (EVA): https://www.funcionpublica.gov.co/eva_/gestor-normativo
- DIAN — Compilación Jurídica (normograma): https://normograma.dian.gov.co/dian/compilacion/
- Secretaría del Senado — Leyes de la República: http://www.secretariasenado.gov.co/leyes-de-la-republica
- Régimen Legal de Bogotá (sisjur): https://www.alcaldiabogota.gov.co/sisjur/consulta_avanzada.jsp
- Rama Judicial — datos abiertos / consulta de procesos: https://www.ramajudicial.gov.co/
