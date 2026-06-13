"""Tests for the canonical ID grammar and affectation normalization."""

from __future__ import annotations

import pytest

from scrapper_leyes.models import (
    AffectationType,
    build_canonical_id,
    normalize_affectation_type,
    normalize_article_number,
    parse_canonical_id,
    validate_canonical_id,
)


class TestCanonicalIdGrammar:
    """Test the canonical ID grammar regex and builder."""

    def test_basic_norm_id(self) -> None:
        cid = build_canonical_id("LEY", "1712", "2014")
        assert cid == "co:ley:1712:2014"
        assert validate_canonical_id(cid)

    def test_norm_with_article(self) -> None:
        cid = build_canonical_id("LEY", "1712", "2014", art="1")
        assert cid == "co:ley:1712:2014:art:1"
        assert validate_canonical_id(cid)

    def test_article_with_letter(self) -> None:
        cid = build_canonical_id("LEY", "1712", "2014", art="5a")
        assert cid == "co:ley:1712:2014:art:5a"
        assert validate_canonical_id(cid)

    def test_transitory_article(self) -> None:
        cid = build_canonical_id("LEY", "1712", "2014", art="trans:1")
        assert cid == "co:ley:1712:2014:art:trans:1"
        assert validate_canonical_id(cid)

    def test_article_with_paragraph(self) -> None:
        cid = build_canonical_id("LEY", "1581", "2012", art="5", par="2")
        assert cid == "co:ley:1581:2012:art:5:par:2"
        assert validate_canonical_id(cid)

    def test_decreto(self) -> None:
        cid = build_canonical_id("DECRETO", "1494", "2015", art="1")
        assert cid == "co:decreto:1494:2015:art:1"
        assert validate_canonical_id(cid)

    def test_acto_legislativo(self) -> None:
        cid = build_canonical_id("ACTO LEGISLATIVO", "3", "2011")
        assert cid == "co:acto_legislativo:3:2011"
        assert validate_canonical_id(cid)

    def test_sentencia(self) -> None:
        cid = build_canonical_id("SENTENCIA", "C-001", "2020")
        assert cid == "co:sentencia:c-001:2020"
        assert validate_canonical_id(cid)

    def test_invalid_ids(self) -> None:
        assert not validate_canonical_id("")
        assert not validate_canonical_id("ley:1712:2014")  # missing co: prefix
        assert not validate_canonical_id("co:ley:@bc:2014")  # invalid chars in numero
        assert not validate_canonical_id("co:ley:1712:14")  # 2-digit year
        assert not validate_canonical_id("co:ley:1712:2014:art")  # missing article number

    def test_parse_canonical_id(self) -> None:
        result = parse_canonical_id("co:ley:1712:2014:art:5:par:2")
        assert result is not None
        assert result["tipo"] == "ley"
        assert result.get("corte") is None
        assert result.get("sala") is None
        assert result["numero"] == "1712"
        assert result["anio"] == "2014"
        assert result["art"] == "5"
        assert result["par"] == "2"

    def test_parse_sentencia_id(self) -> None:
        result = parse_canonical_id("co:sentencia:cc:plena:c-274:2013")
        assert result is not None
        assert result["tipo"] == "sentencia"
        assert result["corte"] == "cc"
        assert result["sala"] == "plena"
        assert result["numero"] == "c-274"
        assert result["anio"] == "2013"
        assert result["art"] is None

    def test_parse_transitory(self) -> None:
        result = parse_canonical_id("co:ley:1712:2014:art:trans:1")
        assert result is not None
        assert result["art"] == "trans:1"

    def test_parse_invalid_returns_none(self) -> None:
        assert parse_canonical_id("not:valid") is None


class TestArticleNumberNormalization:
    """Test extraction of article numbers from raw text."""

    def test_standard_article(self) -> None:
        assert normalize_article_number("Artículo 5°.") == "5"

    def test_article_with_letter(self) -> None:
        assert normalize_article_number("Artículo 5A.") == "5a"

    def test_transitory_article(self) -> None:
        assert normalize_article_number("Artículo Transitorio 1.") == "trans:1"

    def test_article_with_accent(self) -> None:
        assert normalize_article_number("Artículo 10.") == "10"

    def test_article_without_degree(self) -> None:
        assert normalize_article_number("Artículo 33.") == "33"

    def test_no_article_returns_none(self) -> None:
        assert normalize_article_number("TÍTULO I") is None

    def test_article_with_ordinal_o(self) -> None:
        assert normalize_article_number("Artículo 1o.") == "1"


class TestAffectationNormalization:
    """Test raw affectation string → enum mapping."""

    def test_modificado(self) -> None:
        atype, mapped = normalize_affectation_type("Modificado")
        assert atype == AffectationType.MODIFICA
        assert mapped is True

    def test_corregido_yerro(self) -> None:
        atype, mapped = normalize_affectation_type("Corregido yerro")
        assert atype == AffectationType.CORRIGE_YERRO
        assert mapped is True

    def test_declarado_exequible(self) -> None:
        atype, mapped = normalize_affectation_type("Declarado exequible")
        assert atype == AffectationType.EXEQUIBLE
        assert mapped is True

    def test_exequible_condicionada(self) -> None:
        atype, mapped = normalize_affectation_type(
            "Declarado condicionalmente exequible"
        )
        assert atype == AffectationType.EXEQUIBLE_CONDICIONADA
        assert mapped is True

    def test_derogado_parcialmente(self) -> None:
        atype, mapped = normalize_affectation_type("Derogado parcialmente")
        assert atype == AffectationType.DEROGA_PARCIAL
        assert mapped is True

    def test_reglamentado(self) -> None:
        atype, mapped = normalize_affectation_type("Reglamentado parcialmente")
        assert atype == AffectationType.REGLAMENTA
        assert mapped is True

    def test_unknown_returns_unknown_and_not_mapped(self) -> None:
        atype, mapped = normalize_affectation_type("Algo desconocido XYZ")
        assert atype == AffectationType.UNKNOWN
        assert mapped is False

    def test_case_insensitive(self) -> None:
        atype, mapped = normalize_affectation_type("MODIFICADO")
        assert atype == AffectationType.MODIFICA
        assert mapped is True

    def test_whitespace_handling(self) -> None:
        atype, mapped = normalize_affectation_type("  Corregido yerro  ")
        assert atype == AffectationType.CORRIGE_YERRO
        assert mapped is True
