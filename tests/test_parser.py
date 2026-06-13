"""Tests for the SUIN HTML parser — runs against local fixture only."""

from __future__ import annotations

from scrapper_leyes.models import AffectationType, validate_canonical_id
from scrapper_leyes.scraper.html_parser import parse_suin_html


class TestParserMetadata:
    """Test metadata extraction from the Ley 1712 fixture."""

    def test_extracts_tipo(self, ley_1712_html: str) -> None:
        result = parse_suin_html(ley_1712_html, "1687091")
        assert result.metadata.get("tipo") == "LEY"

    def test_extracts_numero(self, ley_1712_html: str) -> None:
        result = parse_suin_html(ley_1712_html, "1687091")
        assert result.metadata.get("numero") == "1712"

    def test_extracts_anio(self, ley_1712_html: str) -> None:
        result = parse_suin_html(ley_1712_html, "1687091")
        assert result.metadata.get("anio") == "2014"

    def test_extracts_vigencia(self, ley_1712_html: str) -> None:
        result = parse_suin_html(ley_1712_html, "1687091")
        assert result.metadata.get("estado_documento") == "Vigente"

    def test_extracts_entidad(self, ley_1712_html: str) -> None:
        result = parse_suin_html(ley_1712_html, "1687091")
        entidad = result.metadata.get("entidad_emisora", "")
        assert "CONGRESO" in entidad.upper()

    def test_extracts_subtipo(self, ley_1712_html: str) -> None:
        result = parse_suin_html(ley_1712_html, "1687091")
        assert result.metadata.get("subtipo") == "LEY ESTATUTARIA"

    def test_extracts_epigrafe(self, ley_1712_html: str) -> None:
        result = parse_suin_html(ley_1712_html, "1687091")
        epi = result.metadata.get("epigrafe", "")
        assert "transparencia" in epi.lower() or "Transparencia" in epi


class TestParserArticles:
    """Test article extraction."""

    def test_extracts_at_least_30_articles(self, ley_1712_html: str) -> None:
        result = parse_suin_html(ley_1712_html, "1687091")
        assert len(result.articles) >= 30, (
            f"Expected ≥30 articles, got {len(result.articles)}"
        )

    def test_articles_have_valid_canonical_ids(self, ley_1712_html: str) -> None:
        result = parse_suin_html(ley_1712_html, "1687091")
        for art in result.articles:
            assert validate_canonical_id(art.canonical_id), (
                f"Invalid canonical_id: {art.canonical_id}"
            )

    def test_articles_canonical_ids_start_with_co_ley(
        self, ley_1712_html: str
    ) -> None:
        result = parse_suin_html(ley_1712_html, "1687091")
        for art in result.articles:
            assert art.canonical_id.startswith("co:ley:1712:2014:art:"), (
                f"Wrong prefix: {art.canonical_id}"
            )

    def test_article_1_has_correct_id(self, ley_1712_html: str) -> None:
        result = parse_suin_html(ley_1712_html, "1687091")
        art1_ids = [a.canonical_id for a in result.articles if a.number_normalized == "1"]
        assert "co:ley:1712:2014:art:1" in art1_ids

    def test_articles_have_text(self, ley_1712_html: str) -> None:
        result = parse_suin_html(ley_1712_html, "1687091")
        for art in result.articles:
            assert len(art.text) > 20, (
                f"Article {art.canonical_id} has too little text: {len(art.text)}"
            )

    def test_article_1_mentions_objeto(self, ley_1712_html: str) -> None:
        result = parse_suin_html(ley_1712_html, "1687091")
        art1 = next(
            (a for a in result.articles if a.number_normalized == "1"), None
        )
        assert art1 is not None
        assert art1.title is not None
        assert "Objeto" in art1.title or "objeto" in art1.text.lower()


class TestParserAffectations:
    """Test modification and jurisprudence extraction."""

    def test_has_at_least_one_modification(self, ley_1712_html: str) -> None:
        result = parse_suin_html(ley_1712_html, "1687091")
        assert len(result.modifications) >= 1, "Expected ≥1 modification"

    def test_decreto_1494_affectation(self, ley_1712_html: str) -> None:
        """Verify the known affectation: Decreto 1494 de 2015 on Art. 5."""
        result = parse_suin_html(ley_1712_html, "1687091")
        found = False
        for mod in result.modifications:
            if "1494" in mod.source_text and "2015" in mod.source_text:
                found = True
                assert mod.normalized_type == AffectationType.CORRIGE_YERRO
                assert mod.source_suin_id is not None
                break
        assert found, "Decreto 1494/2015 affectation not found"

    def test_has_at_least_one_jurisprudence(self, ley_1712_html: str) -> None:
        result = parse_suin_html(ley_1712_html, "1687091")
        assert len(result.jurisprudence) >= 1, "Expected ≥1 jurisprudence"

    def test_sentencia_c274_2013(self, ley_1712_html: str) -> None:
        """Verify the known jurisprudence: Sentencia C-274 de 2013."""
        result = parse_suin_html(ley_1712_html, "1687091")
        found = False
        for jur in result.jurisprudence:
            if "C-274" in jur.source_text or "274" in jur.source_text:
                found = True
                assert jur.normalized_type == AffectationType.EXEQUIBLE
                break
        assert found, "Sentencia C-274/2013 not found"

    def test_affectations_have_normalized_types(self, ley_1712_html: str) -> None:
        result = parse_suin_html(ley_1712_html, "1687091")
        all_affs = result.modifications + result.jurisprudence
        for aff in all_affs:
            assert isinstance(aff.normalized_type, AffectationType)


class TestParserToc:
    """Test table of contents extraction."""

    def test_has_toc_entries(self, ley_1712_html: str) -> None:
        result = parse_suin_html(ley_1712_html, "1687091")
        assert len(result.toc) >= 5, f"Expected ≥5 TOC entries, got {len(result.toc)}"


class TestParserSerialization:
    """Test that parsed output can be serialized to JSON."""

    def test_to_dict_is_serializable(self, ley_1712_html: str) -> None:
        import json

        result = parse_suin_html(ley_1712_html, "1687091")
        d = result.to_dict()
        json_str = json.dumps(d, ensure_ascii=False)
        assert len(json_str) > 1000
