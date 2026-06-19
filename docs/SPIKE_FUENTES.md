# Spike de acceso a fuentes — verificación de accesibilidad

Reconocimiento de las fuentes faltantes del sistema legal colombiano. Estado de
acceso **verificado por HTTP en vivo** (columna "HTTP" = código real observado el
2026-06-19). El registro declarativo vive en `src/scrapper_leyes/sources.py`;
este documento detalla el *cómo* de cada conector.

## Veredicto: TODAS las fuentes son accesibles

Ninguna está bloqueada por captcha/Cloudflare en sus rutas de documento. Los
patrones de descarga de texto están confirmados. Lo que varía es la dificultad
del **descubrimiento** (enumerar qué documentos existen).

## Hallazgo transversal clave

**DIAN, CREG, CRC, CRA y JEP-Jurinfo usan el MISMO motor que SUIN**: el
"Normograma / Gestor Normativo Alejandría" de Avance Jurídico. Documentos como
HTML estático con patrón `.../docs/<tipo>_<entidad>_<numero>_<año>.htm`. → **un
solo discoverer parametrizable por host cubre todas**, reutilizando el parser SUIN.

---

## Matriz de acceso (verificada)

| Fuente | HTTP | Mecanismo | Descarga de texto (patrón) | Vol. aprox. | Discoverer |
|---|---|---|---|---|---|
| **Tratados** | 200 JSON | Socrata `fdir-hk5z` | metadatos (1.261) | 1.261 | ✅ **catálogo (hecho)** |
| **DIAN** | 206 HTML | Normograma Avance Jurídico | `normograma.dian.gov.co/dian/compilacion/docs/<tipo>_dian_<n>_<año>.htm` | decenas de miles | Normograma genérico |
| **CREG** | 206 HTML | Normograma | `gestornormativo.creg.gov.co/gestor/entorno/docs/resolucion_creg_<serie>-<consec>_<año>.htm` | miles | Normograma genérico |
| **CRA** | 206 HTML | Normograma | `normas.cra.gov.co/gestor/docs/resolucion_cra_<n4>_<año>.htm` (pad 4) | ~1.000 | Normograma genérico |
| **CRC** | — | Normograma (NO tiene Socrata: dataset `nhnm-fedn` = 404) | `normograma.crcom.gov.co/crc/compilacion/docs/resolucion_crc_<n>_<año>.htm` | cientos | Normograma genérico |
| **CSJ** | 200 PDF | WebRelatoria JSF + servlet | `…/WebRelatoria/FileReferenceServlet?corp=csj&ext=pdf\|doc&file=<int>` | cientos de miles | servlet (texto) + JSF/Playwright (metadatos) |
| **Consejo de Estado** | 200 | WebRelatoria (<2021) + SAMAI (≥2021) | servlet `corp=ce` + SAMAI ASP.NET | decenas de miles | dual: servlet + SAMAI |
| **Corte IDH** | 206 PDF | HTML + PDF estático | `corteidh.or.cr/docs/casos/articulos/seriec_<N>_esp.pdf` + `ver_ficha_tecnica.cfm?nId_Ficha=<N>` | ~500-600 | crawl fichas → PDF |
| **CAN (Decisiones/Gacetas)** | 206 PDF | WordPress listado + PDF | `comunidadandina.org/DocOficialesFiles/decisiones/DECISION<N>.pdf` | ~920 dec + miles gacetas | listado HTML + fallback patrón |
| **CAN Tribunal (TJCAN)** | 206 | vía comunidadandina.org | `/DocOficialesFiles/Procesos/<cod>.pdf` | cientos | mismo crawl CAN |
| **Banco de la República** | 200 HTML | Drupal + CDN | `banrep.gov.co/es/…/boletin-<NN>-<AAAA>` + PDF `/sites/default/files/reglamentacion/archivos/` | ~1-2 mil | crawl Drupal (índices anuales) |
| **Procuraduría (SIREL)** | 200 HTML | JSF con buscador GET paginado | PDF `/relatoria/media/file/` + HTML `/guia/.../docs/<rad>.html` | ~26.835 | GET paginado (`first_result`/`max_results`/`total_results`) |
| **Contraloría** | 206 PDF | Azure Blob `$web` | `relatoria.blob.core.windows.net/$web/files/conceptos-juridicos/CGR-OJ-<NNN>-<AAAA>.PDF` | ~2-5 mil | sembrar microsite + enumerar patrón |
| **CNDJ** | 200 HTML | Liferay + relatoría | `relatoria.cndj.gov.co/docs_relatoria/<rad+timestamp>.pdf` | cientos-miles | buscador (XHR/Playwright) → PDF |
| **EVA (Función Pública)** | 200 PDF | PHP GET-by-id | `funcionpublica.gov.co/eva/gestornormativo/norma_pdf.php?i=<ID>` | miles | crawl-by-id + índice `normasfp.php` |
| **CNE** | 200 HTML | Joomla por año | `cne.gov.co/resoluciones-cne-<año>/<slug>` (slugs heterogéneos) | miles | crawl listado por año |
| **JEP** | 206 HTML | Jurinfo (Normograma HTML) + Relati (SPA) | `jurinfo.jep.gov.co/normograma/...` + `relatoria.jep.gov.co/documentos/providencias/.../*.pdf` | miles | Jurinfo (fase 1) + Relati (fase 2) |
| **Superintendencias** | — | Fin: buscador ABCD GET; SIC: Drupal facetas; Supersoc: Tesauro SPA | varios | miles c/u | uno por super |

---

## Volumen medido (discoverer Normograma, BFS real 2026-06-19)

| Fuente | Documentos descubiertos | Composición principal | Años |
|---|---|---|---|
| **CREG** | **4.888** | 3.948 resoluciones · 603 conceptos · 149 acuerdos · 151 circulares | 1960–2026 |
| **CRA** | **3.323** | 1.654 resoluciones · 1.249 conceptos · 370 circulares | 1986–2026 |
| **CRC** | **2.170** | 1.081 resoluciones · 587 conceptos · 274 circulares | 1995–2026 |
| **DIAN** | **~26.171** | API Buscar.ashx (Avance Jurídico) — conceptos/oficios/decretos | — |
| **Tratados** | 1.261 (Socrata) | metadatos | — |

→ Comisiones + DIAN = **~36.500 documentos**. **Hallazgo clave**: el motor Avance
Jurídico expone una API de búsqueda `Buscar.ashx` (declarada en
`configuracion.txt::direccionAPI`) que devuelve metadatos completos
(nombre/tipo/año/número/link) — vía superior al BFS donde el índice es JS (DIAN,
y disponible también en CRA). Endpoint DIAN: `normograma.info/prueba-dian/buscador/Buscar.ashx?texto=<q>`. Notas: hay
cross-listing entre gestores (un doc CRA puede aparecer en el de CREG); el
discoverer dedup por URL pero el conteo puede incluir normas espejadas de otras
entidades. El resto del universo (cortes, doctrina, control) está pendiente de
correr su discoverer una vez implementado.

## Estrategia de implementación (orden de ROI)

1. **Tratados** — ✅ hecho (catálogo Socrata, `catalog sync --dataset tratados`).
2. **Discoverer "Normograma" genérico** (Avance Jurídico) → cubre **DIAN, CREG, CRA, CRC** de un golpe (mismo motor que SUIN; sembrar desde índices `*_por_orden_cronologico.html` / `novedades.html` y resolver `docs/*.htm`). **Mayor ROI.**
3. **FileReferenceServlet (CSJ + CE histórico)** → texto por `file=<int>` (GET, sin JSF). Metadatos en fase 2 con Playwright sobre el `p:dataTable` PrimeFaces.
4. **CAN** → listado WordPress + patrón `DECISION<N>.pdf` (numeración 1..922).
5. **Corte IDH** → iterar fichas `nId_Ficha`, filtrar país=Colombia, bajar `seriec_<N>_esp.pdf`.
6. **EVA** → crawl-by-id `norma_pdf.php?i=` sembrado desde `normasfp.php`.
7. **Banco República, Procuraduría, Contraloría, CNDJ, CNE, Superintendencias, JEP-Relati** → cada uno su crawler (patrones arriba). SAMAI para CE ≥2021.

## Notas de robustez
- Todos `.gov.co` son lentos → throttling cortés + backoff (el acelerador de SUIN ya lo tiene).
- `.doc/.docx` (CE antiguo, algunas Decisiones CAN) requieren conversión a texto (docling ya está en la imagen de ingesta).
- IDs no secuenciales (JEP, CNDJ, CNE) → obligatorio cosechar el índice, no se puede adivinar la URL.
- Pendiente cuando se implemente cada uno: confirmar `robots.txt` y throttling tolerado por host.
