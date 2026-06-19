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
        capa=CAPA_LEGISLACION, modo=MODO_CRAWL, estado=EST_PENDIENTE, prioridad="media",
        tipos=("ley", "decreto", "resolucion"),
        rama="Rama Ejecutiva", cabeza="Imprenta Nacional",
        base_url="https://www.imprenta.gov.co",
        spike="Localizar patrón de PDF por edición/fecha; usar como fuente PRIMARIA de "
              "fecha de promulgación (clave para vigencia).",
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
        key="jep", nombre="Jurisdicción Especial para la Paz", capa=CAPA_JURISPRUDENCIA,
        modo=MODO_CRAWL, estado=EST_PENDIENTE, prioridad="media-baja",
        tipos=("sentencia", "auto"), corte="jep", rama="Rama Judicial", cabeza="Rama Judicial",
        base_url="https://www.jep.gov.co",
        spike="Portal JEP: localizar repositorio de decisiones/autos (justicia transicional, 2018+).",
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
        capa=CAPA_JURISPRUDENCIA, modo=MODO_CRAWL, estado=EST_PENDIENTE, prioridad="alta",
        tipos=("sentencia", "opinion_consultiva"), corte="idh",
        rama="Internacional", cabeza="Sistema Interamericano",
        base_url="https://www.corteidh.or.cr",
        spike="VERIFICADO acceso: PDF Serie C determinístico "
              "`/docs/casos/articulos/seriec_<N>_esp.pdf` (200/PDF) + fichas "
              "`ver_ficha_tecnica.cfm?nId_Ficha=<N>`. Crawl: iterar fichas (filtrar país=Colombia) "
              "→ bajar PDF Serie C. ~500-600 sentencias. Modelar CONTROL_CONVENCIONALIDAD en grafo.",
        notas="🔴 Jurisprudencia vinculante hoy invisible.",
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
        capa=CAPA_LEGISLACION, modo=MODO_CRAWL, estado=EST_PENDIENTE, prioridad="media",
        tipos=("resolucion", "circular"),
        rama="Órgano Autónomo", cabeza="Banco de la República",
        base_url="https://www.banrep.gov.co",
        spike="Resoluciones externas de la Junta (monetario/cambiario/crediticio). "
              "Localizar compilación normativa (DCIN, circular reglamentaria externa).",
    ),
    SourceSpec(
        key="organos_control", nombre="Órganos de Control (Procuraduría, Contraloría, CNDJ)",
        capa=CAPA_DOCTRINA, modo=MODO_CRAWL, estado=EST_PENDIENTE, prioridad="media",
        tipos=("concepto", "fallo_disciplinario", "circular"),
        rama="Organismos de Control", cabeza="Organismos de Control",
        base_url="https://www.procuraduria.gov.co",
        spike="Procuraduría (conceptos + fallos disciplinarios), Contraloría (responsabilidad "
              "fiscal), Comisión Nacional de Disciplina Judicial. Tres portales distintos.",
    ),
    SourceSpec(
        key="cne", nombre="Consejo Nacional Electoral", capa=CAPA_JURISPRUDENCIA,
        modo=MODO_CRAWL, estado=EST_PENDIENTE, prioridad="media-baja",
        tipos=("resolucion", "concepto"),
        rama="Órgano Electoral", cabeza="Organización Electoral",
        base_url="https://www.cne.gov.co",
        spike="Resoluciones y conceptos electorales; localizar repositorio normativo del CNE.",
    ),
    SourceSpec(
        key="can", nombre="Comunidad Andina (Decisiones + Tribunal de Justicia)",
        capa=CAPA_LEGISLACION, modo=MODO_CRAWL, estado=EST_PENDIENTE, prioridad="media-baja",
        tipos=("decision_can", "sentencia"), corte="can",
        rama="Internacional", cabeza="Comunidad Andina",
        base_url="https://www.comunidadandina.org",
        spike="Decisiones de la CAN (aplicación directa) + jurisprudencia del Tribunal de "
              "Justicia de la CAN. Gaceta Oficial del Acuerdo de Cartagena.",
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
        capa=CAPA_DOCTRINA, modo=MODO_CRAWL, estado=EST_PENDIENTE, prioridad="media",
        tipos=("circular_externa", "concepto"),
        rama="Rama Ejecutiva", cabeza="Superintendencias",
        spike="Un normograma por super; empezar por Financiera (circulares externas) y SIC.",
    ),
    SourceSpec(
        key="funcion_publica", nombre="Función Pública — Gestor Normativo (EVA)",
        capa=CAPA_DOCTRINA, modo=MODO_CRAWL, estado=EST_PENDIENTE, prioridad="media",
        tipos=("concepto", "decreto"),
        rama="Rama Ejecutiva", cabeza="Sector Función Pública",
        base_url="https://www.funcionpublica.gov.co/eva/gestornormativo",
        spike="Crawl by-id: norma.php?i=ID; barrer rango de ids; normas + conceptos.",
    ),

    # ── Capa D — Territorial + antecedentes ─────────────────────────────────
    SourceSpec(
        key="regimen_bogota", nombre="Régimen Legal de Bogotá (sisjur)", capa=CAPA_TERRITORIAL,
        modo=MODO_CRAWL, estado=EST_PENDIENTE, prioridad="media-baja",
        tipos=("decreto", "acuerdo", "resolucion"),
        rama="Rama Ejecutiva", cabeza="Distrito Capital",
        base_url="https://www.alcaldiabogota.gov.co/sisjur",
        spike="Normativa distrital consolidada (consulta avanzada). Modelo replicable a "
              "otras gobernaciones/alcaldías (ordenanzas, acuerdos).",
    ),
    SourceSpec(
        key="gaceta_congreso", nombre="Gaceta del Congreso", capa=CAPA_TERRITORIAL,
        modo=MODO_CRAWL, estado=EST_PENDIENTE, prioridad="media-baja",
        tipos=("proyecto_ley", "exposicion_motivos"),
        rama="Rama Legislativa", cabeza="Congreso de la República",
        base_url="https://svrpubindc.imprenta.gov.co",
        spike="Proyectos de ley, exposiciones de motivos, antecedentes (trazabilidad legislativa).",
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

# Volumen de documentos disponible por fuente, MEDIDO en los spikes (no es lo
# ingerido — es lo que existe para descubrir). None = aún sin medir.
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


def volumen_de(key: str) -> int | None:
    return VOLUMEN_MEDIDO.get(key)
