from abc import ABC, abstractmethod
from typing import Any

from scrapper_leyes.models import ParsedNorm

class BaseIndexer(ABC):
    """
    Interface for resolving an external system's internal document ID.
    e.g., getting a SUIN id from the `ley 1712 de 2014` or getting a Corte Constitucional ID
    from a `Sentencia C-274 de 2013`.
    """
    
    @abstractmethod
    def resolve_id(self, catalog_row: dict[str, Any]) -> str | None:
        """
        Takes a catalog row dict (from the database) and attempts to find its internal ID
        in the target system.
        Returns the internal ID as a string, or None if not found.
        """
        pass

    def resolve_batch(self, catalog_rows: list[dict[str, Any]]) -> dict[str, int]:
        """
        Takes a list of catalog rows and resolves them. This is useful for systems
        like SUIN where fetching an index page yields many IDs at once.
        Returns stats: {"resolved": X, "not_found": Y, "error": Z}.
        
        The default implementation calls resolve_id iteratively and must be updated
        to reflect the resolution state into the DB (or the caller handles the DB update,
        but for SUIN the indexer handles the DB update). 
        To maintain interface consistency, it's recommended that the indexer updates the DB
        or yields results.
        """
        # Default naive implementation. Subclasses can override for bulk efficiency.
        stats = {"resolved": 0, "not_found": 0, "error": 0, "ambiguous": 0}
        for row in catalog_rows:
            try:
                res = self.resolve_id(row)
                if res:
                    stats["resolved"] += 1
                else:
                    stats["not_found"] += 1
            except Exception:
                stats["error"] += 1
        return stats

class BaseScraper(ABC):
    """
    Interface for scraping and parsing the content of a document once its internal ID is known.
    """
    
    @abstractmethod
    async def scrape_batch(self, catalog_rows: list[dict[str, Any]]) -> dict[str, int]:
        """
        Takes a list of resolved catalog rows (with source-specific IDs like suin_id)
        and asynchronously scrapes, parses, and saves them.
        Returns stats: {"done": X, "error": Y, "skipped_cached": Z}.
        """
        pass
