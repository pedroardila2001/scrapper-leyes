from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterator

from scrapper_leyes.models import ParsedNorm


@dataclass
class CatalogSeed:
    """A document discovered in a source, ready to seed the catalog.

    Produced by crawl-driven sources (relatorías, normogramas) that enumerate
    their own documents instead of being seeded from a Socrata dataset.
    """

    tipo: str
    numero: str
    source: str
    anio: str | None = None
    canonical_id: str | None = None
    external_id: str | None = None
    source_url: str | None = None
    corte: str | None = None
    magistrado_ponente: str | None = None
    subtipo: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_catalog_row(self) -> dict[str, Any]:
        """Shape for Database.upsert_catalog_seed."""
        return {
            "tipo": self.tipo,
            "numero": self.numero,
            "anio": self.anio,
            "subtipo": self.subtipo,
            "corte": self.corte,
            "magistrado_ponente": self.magistrado_ponente,
            "source": self.source,
            "external_id": self.external_id,
            "source_url": self.source_url,
            "canonical_id": self.canonical_id,
            **self.extra,
        }


class BaseDiscoverer(ABC):
    """Enumerates documents from a source that has no pre-seeded catalog.

    For relatorías / normogramas (CSJ, Consejo de Estado, DIAN, …) the source's
    own buscador is the index: ``discover`` yields CatalogSeeds that the
    orchestrator persists via ``Database.upsert_catalog_seed``, after which the
    normal resolve→scrape pipeline applies.
    """

    @abstractmethod
    def discover(
        self,
        *,
        desde: date | None = None,
        hasta: date | None = None,
        filtro: dict[str, Any] | None = None,
    ) -> Iterator[CatalogSeed]:
        """Yield CatalogSeeds for documents in the given window / filter."""
        raise NotImplementedError


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
