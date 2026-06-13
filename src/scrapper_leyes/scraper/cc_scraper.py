import asyncio
import logging
from typing import Any
import httpx

from scrapper_leyes.config import Settings
from scrapper_leyes.storage.cache import ProvenanceCache
from scrapper_leyes.storage.database import Database
from scrapper_leyes.scraper.base import BaseIndexer, BaseScraper

logger = logging.getLogger(__name__)

class CCIndexer(BaseIndexer):
    """Indexer for Corte Constitucional."""

    def __init__(self, settings: Settings, db: Database) -> None:
        self.settings = settings
        self.db = db

    def resolve_id(self, catalog_row: dict[str, Any]) -> str | None:
        """Abstract method implementation."""
        numero = catalog_row.get("numero")
        anio = catalog_row.get("anio")
        if not numero or not anio:
            return None
        
        anio_short = str(anio)[-2:]
        # Return ID rather than URL so Windows paths don't break
        return f"{numero}-{anio_short}"

    def resolve_batch(self, catalog_rows: list[dict[str, Any]]) -> dict[str, int]:
        """
        Resolves the radicado (e.g., 'C-274') and year into the CC internal URL/ID.
        """
        stats = {"resolved": 0, "not_found": 0, "error": 0, "ambiguous": 0}
        for row in catalog_rows:
            catalog_id = row["id"]
            url = self.resolve_id(row)
            if not url:
                self.db.update_resolve_status(catalog_id, None, "error", "Missing numero or anio")
                stats["error"] += 1
                continue
            
            # For CC, the "suin_id" or internal ID will just be the direct URL.
            self.db.update_resolve_status(catalog_id, url, "resolved")
            stats["resolved"] += 1
            
        return stats


class CCScraper(BaseScraper):
    """Scraper for Corte Constitucional providencias."""

    def __init__(self, settings: Settings, db: Database, cache: ProvenanceCache) -> None:
        self.settings = settings
        self.db = db
        self.cache = cache

    async def scrape_batch(self, catalog_rows: list[dict[str, Any]]) -> dict[str, int]:
        """
        Downloads and parses sentencias from Corte Constitucional.
        """
        stats = {"done": 0, "error": 0, "skipped_cached": 0, "needs_ocr": 0, "not_found": 0}
        
        async with httpx.AsyncClient(
            timeout=60.0,
            follow_redirects=True,
            verify=False,
        ) as client:
            for row in catalog_rows:
                status = await self.scrape_norm(client, row)
                stats[status] = stats.get(status, 0) + 1
            
        return stats

    async def scrape_norm(self, client: httpx.AsyncClient, row: dict[str, Any]) -> str:
        suin_id = row.get("suin_id") # For CC this is the ID e.g. C-162-21
        if not suin_id:
            return "error"
        
        anio = row.get("anio")
        url = f"https://www.corteconstitucional.gov.co/relatoria/{anio}/{suin_id}.htm"
        
        # In a real system, we'd use rate limiting, but for testing we fetch directly.
        try:
            resp = await client.get(url)
            if resp.status_code == 404:
                self.db.update_scrape_status(suin_id, "error")
                return "not_found"
            resp.raise_for_status()
        except Exception as e:
            logger.error(f"Error fetching {suin_id}: {e}")
            self.db.update_scrape_status(suin_id, "error")
            return "error"

        content = resp.content
        source = "corte_constitucional"
        tipo = row["tipo"]
        catalog_match = {"tipo": tipo, "numero": row.get("numero"), "anio": row.get("anio"), "corte": row.get("corte"), "magistrado_ponente": row.get("magistrado_ponente")}
        
        self.cache.store_raw(
            source=source,
            tipo=tipo,
            suin_id=suin_id,
            content=content,
            source_url=url,
            http_status=resp.status_code,
            catalog_match=catalog_match
        )

        from scrapper_leyes.scraper.legal_mapper import LegalMapper
        mapper = LegalMapper()
        parsed_sentencia = mapper.process_html(content, suin_id, catalog_match)

        if parsed_sentencia:
            self.cache.store_parsed(source, tipo, suin_id, parsed_sentencia.to_dict())
            self.db.update_scrape_status(suin_id, "done", None)
            return "done"
        else:
            self.db.update_scrape_status(suin_id, "error", None)
            return "error"
