from __future__ import annotations

from scrapper_leyes.config import Settings
from scrapper_leyes.sources import SOURCE_REGISTRY, get_source
from scrapper_leyes.storage.cache import ProvenanceCache
from scrapper_leyes.storage.database import Database
from scrapper_leyes.scraper.base import BaseDiscoverer, BaseIndexer, BaseScraper

# Fuentes con módulo de scraper/indexer ya escrito (aunque algunas sean scaffold).
_SOURCES_CON_MODULO = {"suin", "corte_constitucional", "csj", "consejo_estado"}


class ScraperFactory:
    """Factory to get the right indexer, scraper and discoverer for a source.

    Las fuentes se declaran en :mod:`scrapper_leyes.sources` (registro central).
    Si una fuente está registrada pero aún no tiene conector, el factory lanza un
    error accionable con el *spike* a verificar — en vez de un ValueError opaco.
    """

    # Toda fuente conocida vive en el registro central.
    SOURCES = list(SOURCE_REGISTRY.keys())
    CRAWL_SOURCES = [k for k, s in SOURCE_REGISTRY.items() if s.modo == "crawl"]

    def __init__(self, settings: Settings, db: Database, cache: ProvenanceCache) -> None:
        self.settings = settings
        self.db = db
        self.cache = cache

    def _no_conector(self, source: str, kind: str) -> Exception:
        """Error accionable para una fuente registrada pero sin conector."""
        spec = get_source(source)
        if spec is None:
            return ValueError(
                f"Fuente desconocida: '{source}'. Registradas: {', '.join(self.SOURCES)}"
            )
        return NotImplementedError(
            f"No hay {kind} para '{spec.nombre}' todavía (estado: {spec.estado}, "
            f"capa {spec.capa}, modo {spec.modo}).\n"
            f"  Spike antes de implementar: {spec.spike or 'definir'}"
        )

    def get_indexer(self, source: str) -> BaseIndexer:
        """Get the indexer for a source.

        SUIN y Corte Constitucional tienen resolución a medida (necesitan
        construir/buscar un id interno). El resto de fuentes crawl ya traen el
        ``external_id`` y ``source_url`` del discoverer → resolución identidad
        con :class:`UrlIndexer`.
        """
        if source == "suin":
            from scrapper_leyes.scraper.suin_scraper import SuinIndexer
            return SuinIndexer(self.settings, self.db)
        elif source == "corte_constitucional":
            from scrapper_leyes.scraper.cc_scraper import CCIndexer
            return CCIndexer(self.settings, self.db)
        if get_source(source) is None:
            raise self._no_conector(source, "indexer")
        from scrapper_leyes.scraper.url_scraper import UrlIndexer
        return UrlIndexer(self.settings, self.db, source)

    def get_scraper(self, source: str) -> BaseScraper:
        """Get the scraper for a source.

        SUIN/CC tienen parser estructural propio. El resto usa el scraper
        genérico por URL (:class:`UrlScraper`): baja ``source_url`` y guarda el
        texto plano, que el chunker indexa como "Texto completo".
        """
        if source == "suin":
            from scrapper_leyes.scraper.suin_scraper import SuinScraper
            return SuinScraper(self.settings, self.db, self.cache)
        elif source == "corte_constitucional":
            from scrapper_leyes.scraper.cc_scraper import CCScraper
            return CCScraper(self.settings, self.db, self.cache)
        if source in ("csj", "consejo_estado"):
            # WebRelatoria no da el cuerpo por URL directa (el FileReferenceServlet
            # responde 404 y las CONSIDERACIONES solo salen por un flujo JSF con
            # estado no replicable a escala). El TEMA completo (tesis jurídicas +
            # fuente formal) SÍ viene en el buffer de búsqueda → WebRelatoriaScraper
            # lo materializa re-recorriendo por fecha, sin requests frágiles.
            from scrapper_leyes.scraper.webrelatoria_discoverer import WebRelatoriaScraper
            return WebRelatoriaScraper(self.settings, self.db, self.cache, source)
        if get_source(source) is None:
            raise self._no_conector(source, "scraper")
        from scrapper_leyes.scraper.url_scraper import UrlScraper
        return UrlScraper(self.settings, self.db, self.cache, source)

    def get_discoverer(self, source: str) -> BaseDiscoverer:
        """Get the crawl-driven discoverer for a source.

        Los discoverers enumeran documentos desde el buscador propio de la fuente
        (CSJ, Consejo de Estado, normogramas, relatorías internacionales y
        territoriales). Cada familia tiene su mecanismo (JSF, API Buscar.ashx,
        Drupal Views, blob, PDF determinístico…); aquí se despacha al adecuado.
        Una fuente registrada sin discoverer lanza un error accionable con su spike.
        """
        # Normogramas "Avance Jurídico" (mismo motor que SUIN): DIAN, CREG, CRC, CRA.
        from scrapper_leyes.scraper.normograma_discoverer import NORMOGRAMA_SOURCES
        if source in NORMOGRAMA_SOURCES:
            from scrapper_leyes.scraper.normograma_discoverer import NormogramaDiscoverer
            return NormogramaDiscoverer(source)
        # Altas cortes vía WebRelatoria (PrimeFaces/JSF).
        if source in ("csj", "consejo_estado"):
            from scrapper_leyes.scraper.webrelatoria_discoverer import WebRelatoriaDiscoverer
            # max_docs=None → sembrar TODO el total (cientos de miles); el tope
            # real se controla con `catalog discover --limit N`.
            return WebRelatoriaDiscoverer(source, max_docs=None)
        # Resto de fuentes: un discoverer por familia.
        if source == "jep":
            from scrapper_leyes.scraper.jep_discoverer import JEPDiscoverer
            return JEPDiscoverer()
        if source == "corte_idh":
            from scrapper_leyes.scraper.corteidh_discoverer import CorteIDHDiscoverer
            return CorteIDHDiscoverer()
        if source == "can":
            from scrapper_leyes.scraper.can_discoverer import CANDiscoverer
            return CANDiscoverer()
        if source == "funcion_publica":
            from scrapper_leyes.scraper.eva_discoverer import EVADiscoverer
            return EVADiscoverer()
        if source == "cne":
            from scrapper_leyes.scraper.cne_discoverer import CNEDiscoverer
            return CNEDiscoverer()
        if source == "organos_control":
            from scrapper_leyes.scraper.organos_control_discoverer import OrganosControlDiscoverer
            return OrganosControlDiscoverer()
        if source == "banco_republica":
            from scrapper_leyes.scraper.banco_republica_discoverer import BancoRepublicaDiscoverer
            return BancoRepublicaDiscoverer()
        if source == "diario_oficial":
            from scrapper_leyes.scraper.diario_oficial_discoverer import DiarioOficialDiscoverer
            return DiarioOficialDiscoverer()
        if source == "regimen_bogota":
            from scrapper_leyes.scraper.regimen_bogota_discoverer import RegimenBogotaDiscoverer
            return RegimenBogotaDiscoverer()
        if source == "gaceta_congreso":
            from scrapper_leyes.scraper.gaceta_congreso_discoverer import GacetaCongresoDiscoverer
            return GacetaCongresoDiscoverer()
        if source == "superintendencias":
            from scrapper_leyes.scraper.superintendencias_discoverer import SuperintendenciasDiscoverer
            return SuperintendenciasDiscoverer()
        if source == "cce":
            from scrapper_leyes.scraper.cce_discoverer import CCEDiscoverer
            return CCEDiscoverer()
        if source == "mintrabajo":
            from scrapper_leyes.scraper.mintrabajo_discoverer import MinTrabajoDiscoverer
            return MinTrabajoDiscoverer()
        raise self._no_conector(source, "discoverer")
