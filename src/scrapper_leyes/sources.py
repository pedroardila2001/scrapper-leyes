"""Registro central de fuentes del ordenamiento jurídico colombiano.

Única fuente de verdad del "universo" a mapear: cada familia de documentos
(legislación, jurisprudencia, doctrina administrativa, supranacional…) es un
``SourceSpec`` declarativo con su capa, modo de ingesta, tipos canónicos,
ubicación en la taxonomía, estado de implementación y el *spike* a verificar
antes de construir su conector.

El `factory`, el CLI y la documentación leen de aquí, de modo que **introducir
una fuente nueva = añadir/llenar un SourceSpec + su discoverer/scraper**, sin
tocar if/elif dispersos.

NOTA: registrar una fuente aquí NO la implementa. ``estado`` indica si el
conector existe; las fuentes en 'pendiente'/'andamiaje' lanzan un error
accionable (con su spike) al pedir su scraper/discoverer.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ── Vocabularios ─────────────────────────────────────────────────────────────

# Capas del ordenamiento (ver docs/PLAN_INTEGRACION_FUENTES.md §2).
CAPA_LEGISLACION = "A"      # normas generales
CAPA_JURISPRUDENCIA = "B"   # precedente
CAPA_DOCTRINA = "C"         # doctrina administrativa (conceptos)
CAPA_TERRITORIAL = "D"      # territorial + antecedentes

# Modo de ingesta.
MODO_CATALOGO = "catalogo"  # un dataset/índice siembra metadatos y se baja texto
MODO_CRAWL = "crawl"        # hay que recorrer un buscador/relatoría

# Estado del conector.
EST_OPERATIVO = "operativo"     # funciona end-to-end
EST_PARCIAL = "parcial"         # cableado pero incompleto
EST_ANDAMIAJE = "andamiaje"     # hay scaffold sin indexador remoto
EST_PENDIENTE = "pendiente"     # solo registrado; falta construir el conector


@dataclass(frozen=True)
class SourceSpec:
    """Una familia de documentos del sistema legal y cómo ingerirla."""

    key: str                      # identificador interno (factory/source column)
    nombre: str
    capa: str                     # CAPA_*
    modo: str                     # MODO_*
    estado: str                   # EST_*
    prioridad: str                # "alta" | "media" | "media-baja" | "—"
    tipos: tuple[str, ...]        # tipos canónicos que emite (lowercase)
    # Ubicación en la taxonomía (rama/cabeza display); para jurisprudencia, corte.
    rama: str | None = None
    cabeza: str | None = None
    corte: str | None = None      # código de corte para jurisprudencia (cc, csj, idh…)
    base_url: str | None = None
    dataset_id: str | None = None  # Socrata 4x4 si modo catálogo
    spike: str = ""               # qué verificar antes de implementar el conector
    notas: str = ""

    @property
    def implementado(self) -> bool:
        return self.estado in (EST_OPERATIVO, EST_PARCIAL)


# ── Registro ─────────────────────────────────────────────────────────────────
# Orden = prioridad de mapeo. Mantener alineado con docs/PLAN_INTEGRACION_FUENTES.md.

_SPECS: list[SourceSpec] = [
    # ── Capa A — Legislación ────────────────────────────────────────────────
    SourceSpec(
        key="suin", nombre="SUIN-Juriscol", capa=CAPA_LEGISLACION, modo=MODO_CATALOGO,
        estado=EST_OPERATIVO, prioridad="alta",
        tipos=("ley", "decreto", "acto_legislativo", "resolucion", "codigo", "circular",
               "constitucion"),
        base_url="https://www.suin-juriscol.gov.co",
        notas="Texto de normas + afectaciones + jurisprudencia de control (CC, CE, "
              "ex-Sala Const. CSJ 1910-1991). Catálogo sembrado por Socrata fiev-nid6.",
    ),
    SourceSpec(
        key="diario_oficial", nombre="Diario Oficial (Imprenta Nacional)",
        capa=CAPA_LEGISLACION, modo=MODO_CRAWL, estado=EST_ANDAMIAJE, prioridad="media",
        tipos=("ley", "decreto", "resolucion"),
        rama="Rama Ejecutiva", cabeza="Imprenta Nacional",
        base_url="https://www.imprenta.gov.co",
        spike="Discoverer escrito (DiarioOficialDiscoverer). App JSF/PrimeFaces sin API: "
              "la edición es la unidad (la nº 53.526 al 2026-06-18). El PDF es session-bound "
              "(submit JSF → detallesPdf.xhtml), no enumerable por URL estática → el scraper "
              "debe replicar el flujo JSF con cookie jar. Fecha de promulgación en extra.fecha "
              "(dato PRIMARIO para vigencia).",
    ),

    # ── Capa B — Jurisprudencia ─────────────────────────────────────────────
    SourceSpec(
        key="corte_constitucional", nombre="Corte Constitucional", capa=CAPA_JURISPRUDENCIA,
        modo=MODO_CATALOGO, estado=EST_PARCIAL, prioridad="alta",
        tipos=("sentencia",), corte="cc", rama="Rama Judicial", cabeza="Corte Constitucional",
        dataset_id="v2k4-2t8s",
        base_url="https://www.corteconstitucional.gov.co/relatoria",
        spike="Catálogo (29.310) sembrado; falta completar el descargador de relatoría "
              "(11/1.000 con texto) y confirmar patrón de URL por tipo/año.",
    ),
    SourceSpec(
        key="csj", nombre="Corte Suprema de Justicia (WebRelatoria)", capa=CAPA_JURISPRUDENCIA,
        modo=MODO_CRAWL, estado=EST_PARCIAL, prioridad="alta",
        tipos=("sentencia",), corte="csj", rama="Rama Judicial", cabeza="Corte Suprema de Justicia",
        base_url="https://consultajurisprudencial.ramajudicial.gov.co/WebRelatoria/csj",
        spike="MECANISMO VERIFICADO: PrimeFaces POST a index.xhtml;jsessionid con ViewState; el "
              "total sale en 'Resultado: X / N'. **~321.880 providencias** (búsqueda 'derecho'). "
              "Texto por FileReferenceServlet?corp=csj&ext=pdf&file=<int> (200/PDF). Discoverer: "
              "drive JSF (Playwright o replay httpx de ViewState) + paginar + extraer file IDs.",
        notas="La fuente más grande del sistema. Salas: Civil, Penal, Laboral, Plena, Constitucional.",
    ),
    SourceSpec(
        key="consejo_estado", nombre="Consejo de Estado (WebRelatoria)", capa=CAPA_JURISPRUDENCIA,
        modo=MODO_CRAWL, estado=EST_PARCIAL, prioridad="alta",
        tipos=("sentencia",), corte="ce", rama="Rama Judicial", cabeza="Consejo de Estado",
        base_url="https://jurisprudencia.ramajudicial.gov.co/WebRelatoria/ce",
        spike="MECANISMO VERIFICADO: misma plataforma JSF que CSJ. **~103.351 providencias** "
              "(búsqueda 'derecho', WebRelatoria <2021). CE ≥2021-12 vive en SAMAI (ASP.NET) → "
              "pipeline aparte. Texto por FileReferenceServlet?corp=ce. Secciones 1-5 + Consulta.",
    ),
    SourceSpec(
        key="jep", nombre="Jurisdicción Especial para la Paz (Jurinfo)", capa=CAPA_JURISPRUDENCIA,
        modo=MODO_CRAWL, estado=EST_PARCIAL, prioridad="media-baja",
        tipos=("sentencia", "auto", "acuerdo", "resolucion"), corte="jep",
        rama="Rama Judicial", cabeza="Rama Judicial",
        base_url="https://jurinfo.jep.gov.co/normograma",
        spike="VERIFICADO en vivo: Jurinfo corre el motor Avance Jurídico con API Buscar.ashx "
              "(buscador/Buscar.ashx?texto=<q>, JSON con nombre/tipo/numero/year/link). "
              "JEPDiscoverer barre consultas semilla y dedup; emite SOLO documentos de origen "
              "JEP (omite espejos de CC/CSJ/CE que ya tienen su fuente). Texto en "
              "compilacion/docs/<link>.htm. La relatoría por expediente vive además en la SPA "
              "relatoria.jep.gov.co (fase 2).",
    ),

    # ── Fuentes que faltaban (tabla del usuario) ────────────────────────────
    SourceSpec(
        key="tratados", nombre="Tratados internacionales de Colombia (bloque de constitucionalidad)",
        capa=CAPA_LEGISLACION, modo=MODO_CATALOGO, estado=EST_PARCIAL, prioridad="alta",
        tipos=("tratado",), rama="Internacional", cabeza="Tratados y derecho internacional",
        dataset_id="fdir-hk5z",
        base_url="https://www.datos.gov.co/resource/fdir-hk5z.json",
        spike="VERIFICADO: 1.261 registros, campos vigente/numeroleyaprobatoria/sentencianumero "
              "→ catálogo Socrata (`catalog sync --dataset tratados`). Falta el TEXTO del "
              "instrumento: crawl complementario a SISMRE Cancillería (2.437 instrumentos).",
        notas="Metadatos ya cableados; cada tratado enlaza su ley aprobatoria + sentencia de "
              "control → base del grafo de bloque de constitucionalidad.",
    ),
    SourceSpec(
        key="corte_idh", nombre="Corte Interamericana de DD.HH.",
        capa=CAPA_JURISPRUDENCIA, modo=MODO_CRAWL, estado=EST_PARCIAL, prioridad="alta",
        tipos=("sentencia", "opinion_consultiva"), corte="idh",
        rama="Internacional", cabeza="Sistema Interamericano",
        base_url="https://www.corteidh.or.cr",
        spike="VERIFICADO Y FUNCIONANDO en vivo: el listado útil es "
              "`casos_en_supervision_por_pais.cfm` (172 fichas con nId_Ficha; "
              "`casos_sentencias.cfm` es JS y da 0). La ficha lista el Nº de Serie C POR FASE "
              "('Sentencia de Fondo: 55', 'Excepciones Preliminares: 18') → una seed por fase, "
              "PDF `seriec_<N>_esp.pdf` (206/PDF). Filtra país=Colombia en la ficha. ~25 casos × "
              "~2-3 fases ≈ 60 sentencias. Pendiente: completar casos archivados (no en supervisión).",
        notas="🔴 Jurisprudencia vinculante; modelar CONTROL_CONVENCIONALIDAD en el grafo.",
    ),
    # Comisiones de Regulación — mismo motor Normograma que SUIN (discoverer hecho).
    SourceSpec(
        key="creg", nombre="CREG — Comisión de Regulación de Energía y Gas",
        capa=CAPA_LEGISLACION, modo=MODO_CRAWL, estado=EST_PARCIAL, prioridad="alta",
        tipos=("resolucion", "circular"), rama="Rama Ejecutiva", cabeza="Comisiones de Regulación",
        base_url="https://gestornormativo.creg.gov.co/gestor/entorno/",
        spike="Normograma Avance Jurídico. Discoverer hecho (BFS). Índice cronológico es JS → "
              "cosecha parcial; falta scraper de texto del .htm (reutilizable del de SUIN).",
        notas="🔴 Regulación sectorial con fuerza normativa; ≠ Superintendencias.",
    ),
    SourceSpec(
        key="crc", nombre="CRC — Comisión de Regulación de Comunicaciones",
        capa=CAPA_LEGISLACION, modo=MODO_CRAWL, estado=EST_PARCIAL, prioridad="alta",
        tipos=("resolucion", "circular"), rama="Rama Ejecutiva", cabeza="Comisiones de Regulación",
        base_url="https://normograma.crcom.gov.co/crc/compilacion/",
        spike="Normograma Avance Jurídico (NO tiene Socrata). Discoverer hecho. La Res. CRC "
              "5050/2016 es la compilatoria maestra. Falta scraper de texto.",
    ),
    SourceSpec(
        key="cra", nombre="CRA — Comisión de Regulación de Agua Potable",
        capa=CAPA_LEGISLACION, modo=MODO_CRAWL, estado=EST_PARCIAL, prioridad="alta",
        tipos=("resolucion", "circular"), rama="Rama Ejecutiva", cabeza="Comisiones de Regulación",
        base_url="https://normas.cra.gov.co/gestor/",
        spike="Normograma Avance Jurídico. Discoverer hecho. Padding a 4 dígitos en el número. "
              "Falta scraper de texto.",
    ),
    SourceSpec(
        key="banco_republica", nombre="Banco de la República — Junta Directiva",
        capa=CAPA_LEGISLACION, modo=MODO_CRAWL, estado=EST_PARCIAL, prioridad="media",
        tipos=("resolucion", "circular"),
        rama="Órgano Autónomo", cabeza="Banco de la República",
        base_url="https://www.banrep.gov.co",
        spike="Discoverer escrito (BancoRepublicaDiscoverer) sobre el índice Drupal Views "
              "/es/reglamentacion-temas/<tema>?page=N (tema 2153 cambiario/monetario). "
              "Tipo+número+año salen del TEXTO del ancla ('Resolución Externa No. 10 de 2014'), "
              "no del href; el PDF (bjd_<n>_<año>.pdf) codifica el boletín → en extra.boletin. "
              "Falta extender a otros temas/<id>.",
    ),
    SourceSpec(
        key="organos_control", nombre="Órganos de Control (Procuraduría, Contraloría, CNDJ)",
        capa=CAPA_DOCTRINA, modo=MODO_CRAWL, estado=EST_PARCIAL, prioridad="media",
        tipos=("concepto", "fallo_disciplinario", "circular"),
        rama="Organismos de Control", cabeza="Organismos de Control",
        base_url="https://www.procuraduria.gov.co",
        spike="OrganosControlDiscoverer cubre tres portales: Contraloría descubre por patrón "
              "determinístico de Azure Blob (CGR-OJ-<NNN>-<AAAA>.pdf); Procuraduría (SIREL, "
              "first_result/max_results, ~26.835) y CNDJ (docs_relatoria/<rad>ADJUNTA<ts>.pdf) "
              "tienen parser verificado pero falta confirmar el endpoint del buscador en vivo.",
    ),
    SourceSpec(
        key="cne", nombre="Consejo Nacional Electoral", capa=CAPA_JURISPRUDENCIA,
        modo=MODO_CRAWL, estado=EST_PARCIAL, prioridad="media-baja",
        tipos=("resolucion", "concepto"),
        rama="Órgano Electoral", cabeza="Organización Electoral",
        base_url="https://www.cne.gov.co",
        spike="VERIFICADO Y FUNCIONANDO: la URL real es `/index.php/resoluciones-cne-<año>` "
              "(el /index.php/ es obligatorio; sin él da 404). Los PDFs viven en SharePoint y el "
              "ancla dice 'Documento' → el número/año se extraen del NOMBRE DE ARCHIVO de la URL "
              "('Res. 06772 de 2024.pdf'). Pendiente: paginación dentro de cada año (la página "
              "muestra un subconjunto) y resolver el token efímero de SharePoint al descargar.",
    ),
    SourceSpec(
        key="can", nombre="Comunidad Andina (Decisiones + Tribunal de Justicia)",
        capa=CAPA_LEGISLACION, modo=MODO_CRAWL, estado=EST_PARCIAL, prioridad="media-baja",
        tipos=("decision_can", "sentencia"), corte="can",
        rama="Internacional", cabeza="Comunidad Andina",
        base_url="https://www.comunidadandina.org",
        spike="CANDiscoverer: cosecha el listado WordPress + fallback DETERMINÍSTICO por patrón "
              "DECISION<N>.pdf (N=1..922) tras muestreo cortés. TJCAN por Procesos/<cod>.pdf. "
              "Algunas Decisiones antiguas son .doc (docling ya está en la imagen).",
    ),

    # ── Capa C — Doctrina administrativa ────────────────────────────────────
    SourceSpec(
        key="dian", nombre="DIAN — Normograma", capa=CAPA_DOCTRINA, modo=MODO_CRAWL,
        estado=EST_PARCIAL, prioridad="alta",
        tipos=("concepto", "resolucion", "circular"),
        rama="Rama Ejecutiva", cabeza="Sector Hacienda",
        base_url="https://normograma.dian.gov.co/dian/compilacion/",
        spike="RESUELTO el descubrimiento: la API Buscar.ashx (Avance Jurídico, declarada en "
              "configuracion.txt::direccionAPI) devuelve metadatos completos. Discoverer usa "
              "esa vía. **~26.171 documentos** (verificado). Falta el scraper de texto del .htm.",
        notas="Mismo motor que SUIN; el .htm reutiliza el parser SUIN.",
    ),
    SourceSpec(
        key="superintendencias", nombre="Superintendencias (Financiera, Sociedades, SIC, Salud, SSPD)",
        capa=CAPA_DOCTRINA, modo=MODO_CRAWL, estado=EST_ANDAMIAJE, prioridad="media",
        tipos=("circular_externa", "concepto"),
        rama="Rama Ejecutiva", cabeza="Superintendencias",
        spike="ANDAMIAJE (la genuinamente difícil). Recon en vivo 2026-06-19: SIC "
              "(repositorio-de-normatividad) es Drupal con Views por AJAX → los documentos NO "
              "están en el HTML estático (un GET da solo la landing); hay que llamar al endpoint "
              "Views/facetas (field_tipo_de_norma_value). Superfinanciera (loader.jsf) responde "
              "302/redirección de sesión + WAF en rutas de documento → requiere Playwright con "
              "sesión. Los parsers _parse_sic/_parse_financiera están listos para el fragmento "
              "correcto; falta el acceso al endpoint real. Prioridad media (circulares externas).",
    ),
    SourceSpec(
        key="funcion_publica", nombre="Función Pública — Gestor Normativo (EVA)",
        capa=CAPA_DOCTRINA, modo=MODO_CRAWL, estado=EST_PARCIAL, prioridad="media",
        tipos=("concepto", "decreto"),
        rama="Rama Ejecutiva", cabeza="Sector Función Pública",
        base_url="https://www.funcionpublica.gov.co/eva/gestornormativo",
        spike="VERIFICADO Y FUNCIONANDO: normasfp.php trae ~108 enlaces `norma.php?i=<ID>` con "
              "`<h4>Ley 1474 de 2011</h4>` → EVADiscoverer extrae 101 seeds reales con tipo/num/"
              "año/canonical_id. Texto en PDF `norma_pdf.php?i=<ID>` (docling). LIMITACIÓN: "
              "normasfp.php es solo los más consultados; el corpus completo (miles) requiere "
              "crawl-by-id (IDs no secuenciales, con huecos) — modo opcional a habilitar.",
    ),

    # ── Capa D — Territorial + antecedentes ─────────────────────────────────
    SourceSpec(
        key="regimen_bogota", nombre="Régimen Legal de Bogotá (sisjur)", capa=CAPA_TERRITORIAL,
        modo=MODO_CRAWL, estado=EST_PARCIAL, prioridad="media-baja",
        tipos=("decreto", "acuerdo", "resolucion"),
        rama="Rama Ejecutiva", cabeza="Distrito Capital",
        base_url="https://www.alcaldiabogota.gov.co/sisjur",
        spike="RegimenBogotaDiscoverer: barrido por id sobre Norma1.jsp?i=<ID> (portal Oracle, "
              "IDs densos/secuenciales; ficha en Windows-1252) con _parse_ficha verificado. "
              "La consulta avanzada PL/SQL no se replicó → se enumera por id. Modelo replicable "
              "a otras alcaldías/gobernaciones.",
    ),
    SourceSpec(
        key="gaceta_congreso", nombre="Gaceta del Congreso", capa=CAPA_TERRITORIAL,
        modo=MODO_CRAWL, estado=EST_ANDAMIAJE, prioridad="media-baja",
        tipos=("proyecto_ley", "exposicion_motivos"),
        rama="Rama Legislativa", cabeza="Congreso de la República",
        base_url="https://svrpubindc.imprenta.gov.co",
        spike="ANDAMIAJE. Recon 2026-06-19: la descarga `index2.xhtml?ent=Senado|Camara&"
              "fec=<D-M-AAAA>&num=<N>` responde 200 PERO requiere la FECHA, que no es derivable "
              "del número → la enumeración pura no sirve; hay que capturar el POST del buscador "
              "JSF/PrimeFaces (como WebRelatoria) para obtener el mapeo num→fecha. El parser de "
              "resultados y la siembra directa filtro={'gacetas':[{ent,fec,num}]} ya están.",
    ),
]

SOURCE_REGISTRY: dict[str, SourceSpec] = {s.key: s for s in _SPECS}


# ── Consultas ────────────────────────────────────────────────────────────────

def get_source(key: str) -> SourceSpec | None:
    return SOURCE_REGISTRY.get(key)


def all_sources() -> list[SourceSpec]:
    return list(_SPECS)


def sources_by_estado(estado: str) -> list[SourceSpec]:
    return [s for s in _SPECS if s.estado == estado]


def sources_by_capa(capa: str) -> list[SourceSpec]:
    return [s for s in _SPECS if s.capa == capa]


def pending_sources() -> list[SourceSpec]:
    """Fuentes que aún no tienen conector (andamiaje o pendiente)."""
    return [s for s in _SPECS if not s.implementado]


CAPA_LABEL = {
    CAPA_LEGISLACION: "A · Legislación",
    CAPA_JURISPRUDENCIA: "B · Jurisprudencia",
    CAPA_DOCTRINA: "C · Doctrina administrativa",
    CAPA_TERRITORIAL: "D · Territorial + antecedentes",
}

# Volumen de documentos disponible por fuente, MEDIDO contando contra la fuente
# real (catálogo Socrata, API Buscar.ashx, total JSF, BFS). No es lo ingerido:
# es lo que existe para descubrir. None = aún sin medir.
VOLUMEN_MEDIDO: dict[str, int] = {
    "suin": 15715,             # catálogo SUIN sembrado
    "corte_constitucional": 29310,  # catálogo Socrata v2k4-2t8s
    "tratados": 1261,          # Socrata fdir-hk5z
    "csj": 321880,             # WebRelatoria (búsqueda 'derecho')
    "consejo_estado": 103351,  # WebRelatoria <2021
    "dian": 26171,             # API Buscar.ashx
    "creg": 4888,              # BFS Normograma
    "cra": 3323,
    "crc": 2170,
}

# Volumen ESTIMADO por fuente cuyo discoverer existe pero aún no se ha corrido un
# conteo completo (estado parcial/andamiaje). Cifras del spike (docs/SPIKE_FUENTES.md)
# o de muestreo en vivo — orden de magnitud, NO conteo definitivo. Sirve para que el
# dashboard refleje el "universo mapeado" completo, marcado como estimado.
VOLUMEN_ESTIMADO: dict[str, int] = {
    "jep": 5000,               # Jurinfo Buscar.ashx (origen JEP, multi-consulta)
    "corte_idh": 60,           # casos contenciosos de Colombia + opiniones consultivas
    "can": 1200,               # ~922 Decisiones + TJCAN
    "funcion_publica": 8000,   # EVA normas + conceptos
    "cne": 3000,               # resoluciones/conceptos electorales por año
    "organos_control": 31835,  # Procuraduría 26.835 + Contraloría ~3.500 + CNDJ ~1.500
    "banco_republica": 1500,   # Junta Directiva (resoluciones + circulares)
    "diario_oficial": 53526,   # ediciones (unidad = edición; la nº 53.526 al 2026-06)
    "regimen_bogota": 30000,   # normativa distrital consolidada (orden de magnitud)
    "gaceta_congreso": 8000,   # gacetas/proyectos por legislatura
    "superintendencias": 10000,  # circulares + conceptos por super
}


def volumen_de(key: str) -> int | None:
    """Volumen medido (o None). Para medido+estimado usar :func:`volumen_total_de`."""
    return VOLUMEN_MEDIDO.get(key)


def volumen_total_de(key: str) -> tuple[int | None, str]:
    """Devuelve (volumen, calidad) donde calidad ∈ {'medido','estimado','sin_medir'}.

    Prioriza el conteo medido sobre el estimado para una misma fuente.
    """
    if key in VOLUMEN_MEDIDO:
        return VOLUMEN_MEDIDO[key], "medido"
    if key in VOLUMEN_ESTIMADO:
        return VOLUMEN_ESTIMADO[key], "estimado"
    return None, "sin_medir"
