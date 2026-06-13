from __future__ import annotations

from typing import Any

from scrapper_leyes.config import Settings
from scrapper_leyes.storage.cache import ProvenanceCache
from scrapper_leyes.storage.database import Database
from scrapper_leyes.scraper.base import BaseDiscoverer, BaseIndexer, BaseScraper

class ScraperFactory:
    """Factory to get the right indexer, scraper and discoverer for a source."""

    # Registry of supported sources
    SOURCES = ["suin", "corte_constitucional", "csj", "consejo_estado"]

    # Sources whose documents are enumerated by crawling the source's own
    # buscador (no Socrata seed). Discoverers land in F1/F3.
    CRAWL_SOURCES = ["csj", "consejo_estado"]

    def __init__(self, settings: Settings, db: Database, cache: ProvenanceCache) -> None:
        self.settings = settings
        self.db = db
        self.cache = cache

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
        else:
            raise ValueError(f"Unknown source for indexer: {source}. Available: {self.SOURCES}")

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
        else:
            raise ValueError(f"Unknown source for scraper: {source}. Available: {self.SOURCES}")

    def get_discoverer(self, source: str) -> BaseDiscoverer:
        """Get the crawl-driven discoverer for a source.

        Discoverers enumerate documents from the source's own buscador (CSJ,
        Consejo de Estado, normogramas). They are implemented per source in
        F1/F3; until then this raises a clear, actionable error.
        """
        raise NotImplementedError(
            f"No hay discoverer para '{source}' todavía. "
            f"Las fuentes crawl-driven ({', '.join(self.CRAWL_SOURCES)}) "
            f"se implementan en las fases F1/F3 del plan de integración."
        )
