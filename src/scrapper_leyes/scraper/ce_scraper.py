"""Consejo de Estado (CE) scraper: indexer and async scraper."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import httpx

from scrapper_leyes.config import Settings
from scrapper_leyes.scraper.base import BaseIndexer, BaseScraper
from scrapper_leyes.scraper.legal_mapper import LegalMapper
from scrapper_leyes.storage.cache import ProvenanceCache
from scrapper_leyes.storage.database import Database

logger = logging.getLogger(__name__)

# Consejo de Estado relatoria base URL
CE_BASE_URL = "https://www.consejodeestado.gov.co"
CE_RELATORIA_URL = f"{CE_BASE_URL}/relatoria"


class CEIndexer(BaseIndexer):
    """Indexer for Consejo de Estado.

    CE sentencias are identified by radicado number and year.
    Sections: Sección Primera through Quinta, Sala Plena, Sala Consulta.
    """

    def __init__(self, settings: Settings, db: Database) -> None:
        self.settings = settings
        self.db = db

    def resolve_id(self, catalog_row: dict[str, Any]) -> str | None:
        """Build a CE-style internal ID from catalog data."""
        numero = catalog_row.get("numero", "")
        anio = catalog_row.get("anio", "")
        if not numero or not anio:
            return None

        numero_clean = re.sub(r"\s+", "", numero.strip().upper())
        return f"CE-{numero_clean}-{anio}"

    def resolve_batch(self, catalog_rows: list[dict[str, Any]]) -> dict[str, int]:
        """Resolve CE internal IDs for a batch of catalog rows."""
        stats = {"resolved": 0, "not_found": 0, "error": 0, "ambiguous": 0}

        for row in catalog_rows:
            catalog_id = row["id"]
            try:
                resolved_id = self.resolve_id(row)
                if resolved_id:
                    self.db.update_resolve_status(catalog_id, resolved_id, "resolved")
                    stats["resolved"] += 1
                else:
                    self.db.update_resolve_status(
                        catalog_id, None, "error", "Missing numero or anio for CE"
                    )
                    stats["error"] += 1
            except Exception as e:
                self.db.update_resolve_status(
                    catalog_id, None, "error", str(e)[:200]
                )
                stats["error"] += 1

        return stats


class CEScraper(BaseScraper):
    """Async scraper for Consejo de Estado providencias."""

    def __init__(
        self,
        settings: Settings,
        db: Database,
        cache: ProvenanceCache,
    ) -> None:
        self.settings = settings
        self.db = db
        self.cache = cache
        self._semaphore = asyncio.Semaphore(settings.suin_max_concurrent)

    def _build_url(self, row: dict[str, Any]) -> str:
        """Build the CE relatoria search URL.

        CE organizes providencias in their relatoria system accessible
        via year-based directories.
        """
        anio = row.get("anio", "")
        numero = row.get("numero", "").strip()
        return f"{CE_RELATORIA_URL}/{anio}/{numero}.htm"

    async def scrape_norm(
        self, client: httpx.AsyncClient, row: dict[str, Any]
    ) -> str:
        """Scrape a single CE sentencia."""
        suin_id = row.get("suin_id")
        if not suin_id:
            return "error"

        url = self._build_url(row)
        source = "consejo_estado"
        tipo = row["tipo"]

        # Check cache
        if self.cache.has_content(source, tipo, suin_id):
            return "skipped_cached"

        try:
            async with self._semaphore:
                resp = await client.get(url)

            if resp.status_code == 404:
                alt_url = url.replace(".htm", ".html")
                async with self._semaphore:
                    resp = await client.get(alt_url)

            if resp.status_code == 404:
                self.db.update_scrape_status(suin_id, "error")
                return "not_found"

            resp.raise_for_status()
        except Exception as e:
            logger.error(f"CE fetch error for {suin_id}: {e}")
            self.db.update_scrape_status(suin_id, "error")
            return "error"

        content = resp.content
        catalog_match = {
            "tipo": tipo,
            "numero": row.get("numero", ""),
            "anio": row.get("anio", ""),
            "corte": "ce",
            "magistrado_ponente": row.get("magistrado_ponente"),
        }

        self.cache.store_raw(
            source=source,
            tipo=tipo,
            suin_id=suin_id,
            content=content,
            source_url=url,
            http_status=resp.status_code,
            catalog_match=catalog_match,
        )

        # Process with LegalMapper
        try:
            mapper = LegalMapper()
            parsed_sentencia = mapper.process_html(content, suin_id, catalog_match)

            if parsed_sentencia:
                self.cache.store_parsed(source, tipo, suin_id, parsed_sentencia.to_dict())
                self.db.update_scrape_status(suin_id, "done", None)
                return "done"
            else:
                self.db.update_scrape_status(suin_id, "error", None)
                return "error"
        except Exception as e:
            logger.error(f"CE parse error for {suin_id}: {e}")
            self.db.update_scrape_status(suin_id, "error", None)
            return "error"

    async def scrape_batch(
        self, catalog_rows: list[dict[str, Any]]
    ) -> dict[str, int]:
        """Scrape a batch of CE sentencias."""
        stats: dict[str, int] = {}

        async with httpx.AsyncClient(
            timeout=60.0,
            follow_redirects=True,
            verify=False,
        ) as client:
            for row in catalog_rows:
                status = await self.scrape_norm(client, row)
                stats[status] = stats.get(status, 0) + 1

        return stats
