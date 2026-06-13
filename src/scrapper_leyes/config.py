"""Configuration and settings for the pipeline."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    """Pipeline-wide settings, overridable via env vars."""

    # ── Socrata (datos.gov.co) ──────────────────────────────────────────
    socrata_base_url: str = "https://www.datos.gov.co/resource"
    socrata_dataset_id: str = "fiev-nid6"
    socrata_app_token: str | None = field(
        default_factory=lambda: os.environ.get("SOCRATA_APP_TOKEN")
    )
    socrata_page_size: int = 1000

    # ── SUIN-Juriscol ───────────────────────────────────────────────────
    suin_base_url: str = "https://www.suin-juriscol.gov.co"
    suin_rate_limit_rps: float = field(
        default_factory=lambda: float(os.environ.get("RATE_LIMIT_RPS", "1.0"))
    )
    suin_max_concurrent: int = 3
    suin_max_retries: int = 3
    suin_retry_base_delay: float = 2.0  # seconds; doubles each retry
    suin_user_agent: str = field(
        default_factory=lambda: (
            f"ScrapperLeyes/1.0 (investigacion academica; "
            f"contacto: {os.environ.get('USER_AGENT_CONTACT', 'no-reply@example.com')})"
        ),
    )

    # ── Storage ─────────────────────────────────────────────────────────
    data_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("DATA_DIR", "data"))
    )

    # ── Derived paths ───────────────────────────────────────────────────
    @property
    def catalog_db_path(self) -> Path:
        return self.data_dir / "catalog.db"

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    def raw_source_dir(self, source: str = "suin") -> Path:
        return self.raw_dir / source

    def raw_norm_dir(self, source: str, tipo: str, suin_id: str) -> Path:
        return self.raw_source_dir(source) / tipo / suin_id

    def ensure_dirs(self) -> None:
        """Create all required directories."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.raw_dir.mkdir(parents=True, exist_ok=True)

    # ── Socrata helpers ─────────────────────────────────────────────────
    @property
    def socrata_endpoint(self) -> str:
        return f"{self.socrata_base_url}/{self.socrata_dataset_id}.json"

    @property
    def socrata_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "application/json"}
        if self.socrata_app_token:
            headers["X-App-Token"] = self.socrata_app_token
        return headers


# ── Singleton (import this) ─────────────────────────────────────────────
settings = Settings()


# ── SUIN tipo → ruta mapping ────────────────────────────────────────────
TIPO_TO_RUTA: dict[str, str] = {
    "LEY": "Leyes",
    "DECRETO": "Decretos",
    "ACTO LEGISLATIVO": "Actos_Legislativos",
    "RESOLUCION": "Resoluciones",
    "CIRCULAR EXTERNA": "Circulares",
    "DIRECTIVA PRESIDENCIAL": "Directivas",
    "CONSTITUCION POLITICA": "Constituciones",
    "CODIGO": "Codigos",
    "CIRCULAR": "Circulares",
    "ACUERDO": "Acuerdos",
    "INSTRUCCION ADMINISTRATIVA CONJUNTA": "Instrucciones",
    "RESOLUCION EXTERNA": "Resoluciones",
    "CIRCULAR CONJUNTA": "Circulares",
    "INSTRUCCION": "Instrucciones",
    "DIRECTIVA VICEPRESIDENCIAL": "Directivas",
    "DIRECTIVA MINISTERIAL": "Directivas",
    "CIRCULAR VICEPRESIDENCIAL": "Circulares",
    "CARTA CIRCULAR": "Circulares",
}
