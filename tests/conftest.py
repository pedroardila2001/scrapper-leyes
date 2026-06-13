"""Shared fixtures for tests."""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def ley_1712_html() -> str:
    """Load the Ley 1712 de 2014 HTML fixture."""
    path = FIXTURES_DIR / "ley_1712_2014.html"
    return path.read_text(encoding="utf-8", errors="replace")


@pytest.fixture
def ley_1712_bytes() -> bytes:
    """Load the Ley 1712 de 2014 HTML fixture as bytes."""
    path = FIXTURES_DIR / "ley_1712_2014.html"
    return path.read_bytes()
