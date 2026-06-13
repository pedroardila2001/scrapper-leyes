"""SUIN-Juriscol scraper: ID resolution, async download, and orchestration."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeRemainingColumn,
)

from scrapper_leyes.config import TIPO_TO_RUTA, Settings
from scrapper_leyes.scraper.base import BaseIndexer, BaseScraper
from scrapper_leyes.scraper.html_parser import detect_needs_ocr, parse_suin_html
from scrapper_leyes.storage.cache import ProvenanceCache
from scrapper_leyes.storage.database import Database

logger = logging.getLogger(__name__)

# CLP listing page pattern: entries like LEY_1712_2014_06/03/2014 with suin_id in URL
_CLP_ENTRY_RE = re.compile(
    r"/clp/contenidos\.dll/\w+/(\d+)\?fn=document-frame",
)
_CLP_LABEL_RE = re.compile(
    r"(?:LEY|DECRETO|RESOLUCION|ACTO[_ ]LEGISLATIVO)_(\d+)_(\d{4})_",
    re.IGNORECASE,
)


# ═══════════════════════════════════════════════════════════════════════════
# ID Resolution via CLP listing
# ═══════════════════════════════════════════════════════════════════════════


class SuinIndexer(BaseIndexer):
    """Indexer for SUIN-Juriscol using the CLP listing page."""

    def __init__(self, settings: Settings, db: Database) -> None:
        self.settings = settings
        self.db = db

    def resolve_id(self, catalog_row: dict[str, Any]) -> str | None:
        # We don't implement row-by-row for SUIN because it's inefficient.
        raise NotImplementedError("Use resolve_batch for SuinIndexer")

    def resolve_batch(self, catalog_rows: list[dict[str, Any]]) -> dict[str, int]:
        """
        Group rows by 'tipo' and resolve using the CLP listing page.
        """
        stats = {"resolved": 0, "ambiguous": 0, "not_found": 0, "error": 0}
        
        # Group by tipo
        rows_by_tipo: dict[str, list[dict[str, Any]]] = {}
        for row in catalog_rows:
            tipo = row["tipo"]
            if tipo not in rows_by_tipo:
                rows_by_tipo[tipo] = []
            rows_by_tipo[tipo].append(row)
            
        for tipo, rows in rows_by_tipo.items():
            tipo_stats = self._resolve_ids_from_clp_for_tipo(tipo, rows)
            for k, v in tipo_stats.items():
                stats[k] += v
                
        return stats

    def _resolve_ids_from_clp_for_tipo(self, tipo: str, rows: list[dict[str, Any]]) -> dict[str, int]:
        """Scrape the CLP listing to build (tipo, numero, anio) → suin_id mapping."""
        ruta = TIPO_TO_RUTA.get(tipo)
        if not ruta:
            logger.warning(f"No CLP ruta mapping for tipo={tipo}")
            return {"resolved": 0, "ambiguous": 0, "not_found": 0, "error": 0}

        stats = {"resolved": 0, "ambiguous": 0, "not_found": 0, "error": 0}
        clp_url = (
            f"{self.settings.suin_base_url}/clp/contenidos.dll/{ruta}"
            f"?f=templates$fn=contents-frame-h.htm$3.0"
            f"&sel=0&tf=main&tt=document-frameset.htm"
            f"&t=contents-frame-h.htm&och=onClick"
        )

        logger.info(f"Fetching CLP listing for {tipo} from {clp_url}")

        with httpx.Client(
            headers={"User-Agent": self.settings.suin_user_agent},
            timeout=60.0,
            follow_redirects=True,
            verify=False,
        ) as client:
            try:
                resp = client.get(clp_url)
                resp.raise_for_status()
            except httpx.HTTPError as e:
                logger.error(f"Failed to fetch CLP listing: {e}")
                stats["error"] = len(rows)
                return stats

        html = resp.text
        clp_index: dict[tuple[str, str], list[str]] = {}

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        for a_tag in soup.find_all("a", href=re.compile(r"/clp/contenidos\.dll/")):
            href = a_tag.get("href", "")
            label = a_tag.get_text(strip=True)

            m_id = re.search(rf"/clp/contenidos\.dll/{ruta}/(\d+)\?", href)
            if not m_id:
                continue
            suin_id = m_id.group(1)

            m_label = re.match(
                r"(?:LEY|DECRETO|RESOLUCION|ACTO[_ ]LEGISLATIVO|CIRCULAR|DIRECTIVA|"
                r"ACUERDO|INSTRUCCION|CODIGO|CONSTITUCION)[_ ]+"
                r"(\d+)[_ ]+(\d{4})",
                label,
                re.IGNORECASE,
            )
            if not m_label:
                m_label = re.match(r"[A-Z_]+_(\d+)_(\d{4})", label, re.IGNORECASE)
            if not m_label:
                continue

            numero = m_label.group(1)
            anio = m_label.group(2)
            key = (numero, anio)

            if key not in clp_index:
                clp_index[key] = []
            if suin_id not in clp_index[key]:
                clp_index[key].append(suin_id)

        logger.info(f"CLP listing parsed: {len(clp_index)} unique (numero, anio) entries")

        for row in rows:
            numero = row.get("numero")
            anio = row.get("anio")
            catalog_id = row["id"]

            if not numero or not anio:
                self.db.update_resolve_status(catalog_id, None, "error", "Missing numero or anio")
                stats["error"] += 1
                continue

            key = (numero, anio)
            matches = clp_index.get(key, [])

            if len(matches) == 1:
                self.db.update_resolve_status(catalog_id, matches[0], "resolved")
                stats["resolved"] += 1
            elif len(matches) > 1:
                note = f"Multiple suin_ids: {', '.join(matches)}"
                self.db.update_resolve_status(catalog_id, None, "ambiguous", note)
                stats["ambiguous"] += 1
            else:
                self.db.update_resolve_status(catalog_id, None, "not_found", f"Not in CLP listing for {tipo}")
                stats["not_found"] += 1

        return stats

# ═══════════════════════════════════════════════════════════════════════════
# Async Scraper
# ═══════════════════════════════════════════════════════════════════════════

class SuinScraper(BaseScraper):
    """Async scraper for SUIN viewDocument pages with rate limiting."""

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
        self._rate_interval = 1.0 / settings.suin_rate_limit_rps
        self._last_request_time = 0.0

    async def _rate_limit(self) -> None:
        """Enforce rate limiting between requests."""
        now = time.monotonic()
        elapsed = now - self._last_request_time
        if elapsed < self._rate_interval:
            await asyncio.sleep(self._rate_interval - elapsed)
        self._last_request_time = time.monotonic()

    async def _fetch_with_retry(
        self,
        client: httpx.AsyncClient,
        url: str,
    ) -> httpx.Response | None:
        """Fetch URL with exponential backoff retry."""
        delay = self.settings.suin_retry_base_delay
        for attempt in range(self.settings.suin_max_retries):
            try:
                async with self._semaphore:
                    await self._rate_limit()
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        return resp
                    elif resp.status_code == 404:
                        logger.warning(f"404 for {url}")
                        return resp
                    else:
                        logger.warning(
                            f"HTTP {resp.status_code} for {url}, "
                            f"attempt {attempt + 1}/{self.settings.suin_max_retries}"
                        )
            except (httpx.HTTPError, httpx.TimeoutException) as e:
                logger.warning(
                    f"Error fetching {url}: {e}, "
                    f"attempt {attempt + 1}/{self.settings.suin_max_retries}"
                )

            if attempt < self.settings.suin_max_retries - 1:
                await asyncio.sleep(delay)
                delay *= 2

        logger.error(f"All retries exhausted for {url}")
        return None

    async def scrape_norm(
        self,
        client: httpx.AsyncClient,
        norm: dict[str, Any],
    ) -> str:
        """Scrape a single norm. Returns status string."""
        suin_id = norm["suin_id"]
        tipo = norm["tipo"]
        source = "suin"

        # Check if already cached with same content
        if self.cache.has_content(source, tipo, suin_id):
            existing_log = self.db.get_scrape_log(suin_id)
            if existing_log and existing_log["parse_status"] in ("done", "needs_ocr"):
                logger.debug(f"Already cached: {suin_id}")
                return "skipped_cached"

        # Build URL
        url = f"{self.settings.suin_base_url}/viewDocument.asp?id={suin_id}"

        # Fetch
        resp = await self._fetch_with_retry(client, url)
        if resp is None:
            self.db.update_scrape_status(suin_id, "error")
            return "error"

        if resp.status_code == 404:
            self.db.update_scrape_status(suin_id, "error")
            return "not_found"

        content = resp.content
        content_hash = ProvenanceCache.compute_hash(content)

        # Check if content hasn't changed
        if self.cache.content_hash_matches(source, tipo, suin_id, content_hash):
            logger.debug(f"Content unchanged: {suin_id}")
            return "unchanged"

        # Store raw
        catalog_match = {
            "tipo": tipo,
            "numero": norm.get("numero", ""),
            "anio": norm.get("anio", ""),
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

        # Detect OCR need
        html_text = content.decode("utf-8", errors="replace")
        needs_ocr = detect_needs_ocr(html_text)

        if needs_ocr:
            parse_status = "needs_ocr"
            parsed = None
        else:
            # Parse
            try:
                parsed_norm = parse_suin_html(html_text, suin_id)
                parsed = parsed_norm.to_dict()
                parse_status = "done"

                # Log unmapped affectations
                for aff in parsed_norm.modifications + parsed_norm.jurisprudence:
                    if not aff.mapped:
                        self.db.log_unmapped_affectation(
                            suin_id=suin_id,
                            raw_type=aff.raw_type,
                            article_affected=aff.article_affected,
                            source_text=aff.source_text,
                            context=aff.context,
                        )
            except Exception as e:
                logger.error(f"Parse error for {suin_id}: {e}")
                parse_status = "error"
                parsed = None

        # Store parsed output
        if parsed is not None:
            self.cache.store_parsed(source, tipo, suin_id, parsed)

        # Update scrape log
        raw_path = self.cache.get_raw_relative_path(source, tipo, suin_id) or ""
        log_entry: dict[str, Any] = {
            "suin_id": suin_id,
            "source": source,
            "source_url": url,
            "content_hash": content_hash,
            "capture_ts": datetime.now(timezone.utc).isoformat(),
            "http_status": resp.status_code,
            "raw_path": raw_path,
            "parse_status": parse_status,
            "parse_error": None,
            "articles_count": len(parsed_norm.articles) if parsed and not needs_ocr else None,
            "modifications_count": (
                len(parsed_norm.modifications) if parsed and not needs_ocr else None
            ),
            "jurisprudence_count": (
                len(parsed_norm.jurisprudence) if parsed and not needs_ocr else None
            ),
            "scraper_version": "1.0.0",
        }
        self.db.insert_scrape_log(log_entry)

        # Update catalog
        suin_vigencia = None
        if parsed and not needs_ocr:
            suin_vigencia = parsed_norm.metadata.get("estado_documento")
        self.db.update_scrape_status(suin_id, parse_status, suin_vigencia)

        return parse_status

    async def scrape_batch(
        self,
        catalog_rows: list[dict[str, Any]],
    ) -> dict[str, int]:
        """Scrape a batch of norms concurrently (within rate limits).

        Returns stats dict.
        """
        stats: dict[str, int] = {}

        async with httpx.AsyncClient(
            headers={"User-Agent": self.settings.suin_user_agent},
            timeout=60.0,
            follow_redirects=True,
            verify=False,
        ) as client:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TextColumn("{task.completed}/{task.total}"),
                TimeRemainingColumn(),
            ) as progress:
                task = progress.add_task(
                    "Scrapeando normas...", total=len(catalog_rows)
                )

                for norm in catalog_rows:
                    status = await self.scrape_norm(client, norm)
                    stats[status] = stats.get(status, 0) + 1
                    progress.advance(task)

        return stats


def run_scrape(
    settings: Settings,
    db: Database,
    cache: ProvenanceCache,
    *,
    tipo: str | None = None,
    limit: int | None = None,
) -> dict[str, int]:
    """Run the scraper synchronously (wraps async)."""
    norms = db.get_pending_norms(tipo=tipo, limit=limit)
    if not norms:
        logger.info("No pending norms to scrape")
        return {}

    scraper = SuinScraper(settings, db, cache)
    return asyncio.run(scraper.scrape_batch(norms))
