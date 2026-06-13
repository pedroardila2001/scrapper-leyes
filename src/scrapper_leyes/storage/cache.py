"""Immutable provenance cache for raw scraped documents."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scrapper_leyes.config import Settings


class ProvenanceCache:
    """Manages the raw document cache with immutable provenance metadata.

    Layout:
        data/raw/{source}/{TIPO}/{suin_id}/
            {suin_id}_{sha256_8}_{timestamp}.html   — raw HTML
            metadata.json                            — provenance
            parsed.json                              — parser output (regenerable)
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _norm_dir(self, source: str, tipo: str, suin_id: str) -> Path:
        return self.settings.raw_norm_dir(source, tipo, suin_id)

    @staticmethod
    def compute_hash(content: bytes) -> str:
        """SHA-256 hex digest of raw content."""
        return hashlib.sha256(content).hexdigest()

    def has_content(self, source: str, tipo: str, suin_id: str) -> bool:
        """Check if we already have raw content for this norm."""
        d = self._norm_dir(source, tipo, suin_id)
        return d.exists() and (d / "metadata.json").exists()

    def content_hash_matches(
        self, source: str, tipo: str, suin_id: str, content_hash: str
    ) -> bool:
        """Check if cached content has the same hash (skip re-download)."""
        meta = self.load_metadata(source, tipo, suin_id)
        if meta is None:
            return False
        return meta.get("content_hash_sha256") == content_hash

    def store_raw(
        self,
        source: str,
        tipo: str,
        suin_id: str,
        content: bytes,
        source_url: str,
        http_status: int,
        catalog_match: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Store raw HTML content with provenance metadata.

        Returns the metadata dict (also saved to disk).
        """
        d = self._norm_dir(source, tipo, suin_id)
        d.mkdir(parents=True, exist_ok=True)

        content_hash = self.compute_hash(content)
        ts = datetime.now(timezone.utc)
        ts_str = ts.strftime("%Y%m%dT%H%M%SZ")
        short_hash = content_hash[:8]

        # Save raw HTML
        raw_filename = f"{suin_id}_{short_hash}_{ts_str}.html"
        raw_path = d / raw_filename
        raw_path.write_bytes(content)

        # Build metadata
        metadata: dict[str, Any] = {
            "suin_id": suin_id,
            "source": source,
            "source_url": source_url,
            "content_hash_sha256": content_hash,
            "capture_timestamp": ts.isoformat(),
            "http_status": http_status,
            "content_length": len(content),
            "raw_filename": raw_filename,
            "scraper_version": "1.0.0",
        }
        if catalog_match:
            metadata["catalog_match"] = catalog_match

        # Save metadata (overwrite — latest capture wins)
        meta_path = d / "metadata.json"
        meta_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")

        return metadata

    def store_parsed(
        self, source: str, tipo: str, suin_id: str, parsed: dict[str, Any]
    ) -> Path:
        """Store parsed output (regenerable from raw)."""
        d = self._norm_dir(source, tipo, suin_id)
        d.mkdir(parents=True, exist_ok=True)
        path = d / "parsed.json"
        path.write_text(json.dumps(parsed, indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    def load_metadata(
        self, source: str, tipo: str, suin_id: str
    ) -> dict[str, Any] | None:
        """Load provenance metadata, or None if not cached."""
        meta_path = self._norm_dir(source, tipo, suin_id) / "metadata.json"
        if not meta_path.exists():
            return None
        return json.loads(meta_path.read_text(encoding="utf-8"))

    def load_raw(self, source: str, tipo: str, suin_id: str) -> bytes | None:
        """Load the latest raw HTML from cache."""
        meta = self.load_metadata(source, tipo, suin_id)
        if meta is None:
            return None
        raw_path = self._norm_dir(source, tipo, suin_id) / meta["raw_filename"]
        if not raw_path.exists():
            return None
        return raw_path.read_bytes()

    def load_parsed(
        self, source: str, tipo: str, suin_id: str
    ) -> dict[str, Any] | None:
        """Load parsed output, or None."""
        path = self._norm_dir(source, tipo, suin_id) / "parsed.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def get_raw_relative_path(
        self, source: str, tipo: str, suin_id: str
    ) -> str | None:
        """Get the relative path to raw file (for scrape_log)."""
        meta = self.load_metadata(source, tipo, suin_id)
        if meta is None:
            return None
        return str(
            Path(source) / tipo / suin_id / meta["raw_filename"]
        )
