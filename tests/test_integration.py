"""Integration tests that hit the network. Run with: pytest -m integration"""

from __future__ import annotations

import pytest


@pytest.mark.integration
class TestSocrataIntegration:
    """Tests that hit the real Socrata API."""

    def test_fetch_5_records(self) -> None:
        """Verify the Socrata API returns expected fields."""
        import httpx

        resp = httpx.get(
            "https://www.datos.gov.co/resource/fiev-nid6.json",
            params={"$limit": "5"},
            timeout=30.0,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 5

        # Check expected fields exist
        expected_fields = {"tipo", "n_mero", "vigencia", "entidad"}
        for record in data:
            assert expected_fields.issubset(record.keys()), (
                f"Missing fields: {expected_fields - record.keys()}"
            )

    def test_count_endpoint(self) -> None:
        """Verify the count endpoint works."""
        import httpx

        resp = httpx.get(
            "https://www.datos.gov.co/resource/fiev-nid6.json",
            params={"$select": "count(*)"},
            timeout=30.0,
        )
        assert resp.status_code == 200
        data = resp.json()
        count = int(data[0]["count"])
        assert count > 80000  # Should be ~88k


@pytest.mark.integration
class TestSuinIntegration:
    """Tests that hit the real SUIN website."""

    def test_fetch_ley_1712(self) -> None:
        """Download Ley 1712/2014 and verify basic parsing."""
        import httpx

        from scrapper_leyes.scraper.html_parser import parse_suin_html

        resp = httpx.get(
            "https://www.suin-juriscol.gov.co/viewDocument.asp?id=1687091",
            timeout=60.0,
            follow_redirects=True,
        )
        assert resp.status_code == 200

        result = parse_suin_html(resp.text, "1687091")
        assert result.metadata.get("tipo") == "LEY"
        assert result.metadata.get("numero") == "1712"
        assert len(result.articles) >= 30
