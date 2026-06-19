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
        """Get the indexer for a source."""
        if source == "suin":
            from scrapper_leyes.scraper.suin_scraper import SuinIndexer
            return SuinIndexer(self.settings, self.db)
        elif source == "corte_constitucional":
            from scrapper_leyes.scraper.cc_scraper import CCIndexer
            return CCIndexer(self.settings, self.db)
        elif source == "csj":
            from scrapper_leyes.scraper.csj_scraper import CSJIndexer
            return CSJIndexer(self.settings, self.db)
        elif source == "consejo_estado":
            from scrapper_leyes.scraper.ce_scraper import CEIndexer
            return CEIndexer(self.settings, self.db)
        raise self._no_conector(source, "indexer")

    def get_scraper(self, source: str) -> BaseScraper:
        """Get the scraper for a source."""
        if source == "suin":
            from scrapper_leyes.scraper.suin_scraper import SuinScraper
            return SuinScraper(self.settings, self.db, self.cache)
        elif source == "corte_constitucional":
            from scrapper_leyes.scraper.cc_scraper import CCScraper
            return CCScraper(self.settings, self.db, self.cache)
        elif source == "csj":
            from scrapper_leyes.scraper.csj_scraper import CSJScraper
            return CSJScraper(self.settings, self.db, self.cache)
        elif source == "consejo_estado":
            from scrapper_leyes.scraper.ce_scraper import CEScraper
            return CEScraper(self.settings, self.db, self.cache)
        raise self._no_conector(source, "scraper")

    def get_discoverer(self, source: str) -> BaseDiscoverer:
        """Get the crawl-driven discoverer for a source.

        Los discoverers enumeran documentos desde el buscador propio de la fuente
        (CSJ, Consejo de Estado, normogramas, relatorías internacionales). Se
        implementan por fuente; hasta entonces esto lanza un error con el spike.
        """
        from scrapper_leyes.scraper.normograma_discoverer import NORMOGRAMA_SOURCES
        if source in NORMOGRAMA_SOURCES:
            from scrapper_leyes.scraper.normograma_discoverer import NormogramaDiscoverer
            return NormogramaDiscoverer(source)
        raise self._no_conector(source, "discoverer")
