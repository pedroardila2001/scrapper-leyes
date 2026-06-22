"""Indexer + scraper genérico para fuentes *crawl-driven* basadas en URL.

Casi todos los discoverers (relatorías, normogramas, gacetas, …) ya colocan en
el catálogo un ``source_url`` **directo y fetcheable** (PDF o HTML del documento)
y un ``external_id``. Para esas fuentes no hace falta un scraper a medida por
cada portal: basta

  1. *resolver* (identidad: el ``external_id`` ya se conoce → se marca
     ``resolved`` y se espeja en ``suin_id`` para el seguimiento de estado), y
  2. *scrapear*: bajar ``source_url``, extraer el texto (PDF→docling, HTML→texto)
     y guardar un ``parsed.json`` plano con ``raw_text``.

El chunker indexa ese ``raw_text`` como grupo "Texto completo" (fallback), así
que el documento queda buscable aunque no tenga un parser estructural propio.
Las fuentes con parser rico (SUIN, Corte Constitucional) siguen su ruta a medida.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from io import BytesIO
from typing import Any

import httpx

from scrapper_leyes.config import Settings
from scrapper_leyes.scraper.base import BaseIndexer, BaseScraper
from scrapper_leyes.storage.cache import ProvenanceCache
from scrapper_leyes.storage.database import Database

logger = logging.getLogger(__name__)


class UrlIndexer(BaseIndexer):
    """Resolución identidad: el ``external_id`` del discoverer ya es el id final."""

    def __init__(self, settings: Settings, db: Database, source: str) -> None:
        self.settings = settings
        self.db = db
        self.source = source

    def resolve_id(self, catalog_row: dict[str, Any]) -> str | None:
        ext = catalog_row.get("external_id")
        return str(ext) if ext else None

    def resolve_batch(self, catalog_rows: list[dict[str, Any]]) -> dict[str, int]:
        stats = {"resolved": 0, "not_found": 0, "error": 0, "ambiguous": 0}
        for row in catalog_rows:
            cid = row["id"]
            ext = self.resolve_id(row)
            if ext:
                # Espeja external_id en suin_id para que update_scrape_status
                # (que filtra por suin_id) marque esta misma fila.
                self.db.update_resolve_status(
                    cid, suin_id=ext, status="resolved",
                    external_id=ext, source=row.get("source") or self.source,
                )
                stats["resolved"] += 1
            else:
                self.db.update_resolve_status(cid, None, "error", "sin external_id")
                stats["error"] += 1
        return stats


class UrlScraper(BaseScraper):
    """Baja ``source_url`` y guarda texto plano (PDF→docling / HTML→texto)."""

    def __init__(
        self, settings: Settings, db: Database, cache: ProvenanceCache, source: str
    ) -> None:
        self.settings = settings
        self.db = db
        self.cache = cache
        self.source = source
        self._concurrency = getattr(settings, "suin_max_concurrent", 5) or 5
        self._semaphore = asyncio.Semaphore(self._concurrency)
        self._rps: float | None = None
        self._converter = None  # docling, perezoso

    def reconfigure(self, workers: int | None = None, rps: float | None = None) -> None:
        if workers:
            self._concurrency = workers
            self._semaphore = asyncio.Semaphore(workers)
        if rps:
            self._rps = rps

    # ── extracción de texto ──────────────────────────────────────────────
    def _docling(self):
        if self._converter is None:
            from docling.document_converter import DocumentConverter
            self._converter = DocumentConverter()
        return self._converter

    def _extract(self, content: bytes, content_type: str, url: str) -> str:
        """Devuelve texto plano. PDF→docling (markdown); HTML→texto via bs4."""
        is_pdf = (
            content[:5] == b"%PDF-"
            or "application/pdf" in content_type
            or url.lower().split("?")[0].endswith(".pdf")
        )
        if is_pdf:
            from docling.datamodel.base_models import DocumentStream
            stream = DocumentStream(name="doc.pdf", stream=BytesIO(content))
            result = self._docling().convert(stream)
            return result.document.export_to_markdown()
        # HTML / texto: limpiar y extraer.
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(content, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        return soup.get_text("\n", strip=True)

    # ── scrape de un documento ───────────────────────────────────────────
    async def scrape_one(self, client: httpx.AsyncClient, row: dict[str, Any]) -> str:
        ext = row.get("suin_id") or row.get("external_id")
        if not ext:
            return "error"
        ext = str(ext)
        source = row.get("source") or self.source
        tipo = row["tipo"]
        url = row.get("source_url")
        if not url:
            self.db.update_scrape_status(ext, "error")
            return "error"

        if self.cache.has_content(source, tipo, ext):
            return "skipped_cached"

        try:
            async with self._semaphore:
                if self._rps:
                    await asyncio.sleep(1.0 / self._rps)
                resp = await client.get(url)
            if resp.status_code == 404:
                self.db.update_scrape_status(ext, "error")
                return "not_found"
            resp.raise_for_status()
        except Exception as e:
            logger.warning("fetch %s (%s): %s", ext, source, str(e)[:160])
            self.db.update_scrape_status(ext, "error")
            return "error"

        content = resp.content
        content_type = resp.headers.get("content-type", "")
        try:
            # docling/bs4 son bloqueantes → fuera del event loop.
            text = await asyncio.to_thread(self._extract, content, content_type, url)
        except Exception as e:
            logger.warning("parse %s (%s): %s", ext, source, str(e)[:160])
            self.db.update_scrape_status(ext, "error")
            return "error"

        if not text or not text.strip():
            self.db.update_scrape_status(ext, "error")
            return "empty"

        catalog_match = {
            "tipo": tipo,
            "numero": row.get("numero", ""),
            "anio": row.get("anio", ""),
            "corte": row.get("corte"),
            "magistrado_ponente": row.get("magistrado_ponente"),
        }
        self.cache.store_raw(
            source=source, tipo=tipo, suin_id=ext, content=content,
            source_url=url, http_status=resp.status_code, catalog_match=catalog_match,
        )
        parsed = {
            "suin_id": ext,
            "metadata": {k: v for k, v in catalog_match.items() if v},
            "articles": [],
            "modifications": [],
            "jurisprudence": [],
            "toc": [],
            "raw_text": text,
            "corte": row.get("corte"),
            "sala": row.get("sala"),
            "magistrado_ponente": row.get("magistrado_ponente"),
            "captured_at": datetime.now(timezone.utc).isoformat(),
        }
        self.cache.store_parsed(source, tipo, ext, parsed)
        self.db.update_scrape_status(ext, "done")
        return "done"

    async def scrape_batch(self, catalog_rows: list[dict[str, Any]]) -> dict[str, int]:
        stats: dict[str, int] = {}
        async with httpx.AsyncClient(
            timeout=90.0, follow_redirects=True, verify=False,
            headers={"User-Agent": "ScrapperLeyes/1.0 (investigacion academica)"},
        ) as client:
            tasks = [self.scrape_one(client, row) for row in catalog_rows]
            for fut in asyncio.as_completed(tasks):
                status = await fut
                stats[status] = stats.get(status, 0) + 1
        return stats
