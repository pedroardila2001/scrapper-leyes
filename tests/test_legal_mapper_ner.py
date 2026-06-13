import pytest
from scrapper_leyes.scraper.legal_mapper import extract_legal_citations, citations_to_strings

def test_extract_ley_and_articles():
    text = "De acuerdo con el Artículo 13 de la Ley 100 de 1993, y la Ley 1712 de 2014."
    citations = extract_legal_citations(text)
    
    assert len(citations) == 2
    
    ley_100 = next(c for c in citations if c.get("numero") == "100")
    assert ley_100["type"] == "ley"
    assert ley_100["anio"] == "1993"
    assert ley_100["articulo"] == "13"
    
    ley_1712 = next(c for c in citations if c.get("numero") == "1712")
    assert ley_1712["type"] == "ley"
    assert ley_1712["anio"] == "2014"
    assert "articulo" not in ley_1712

def test_extract_decretos():
    text = "Según el Decreto 1075 de 2015 y el Artículo 23 del Decreto Ley 2811 de 1974."
    citations = extract_legal_citations(text)
    
    assert len(citations) == 2
    
    d1075 = next(c for c in citations if c.get("numero") == "1075")
    assert d1075["type"] == "decreto"
    assert d1075["anio"] == "2015"
    
    d2811 = next(c for c in citations if c.get("numero") == "2811")
    assert d2811["type"] == "decreto"
    assert d2811["anio"] == "1974"
    assert d2811["articulo"] == "23"

def test_extract_sentencias():
    text = "Como lo establece la Sentencia C-274 de 2013 y la Sentencia SU-062 de 2019."
    citations = extract_legal_citations(text)
    
    assert len(citations) == 2
    
    c274 = next(c for c in citations if c.get("numero") == "C-274")
    assert c274["type"] == "sentencia"
    assert c274["anio"] == "2013"
    
    su062 = next(c for c in citations if c.get("numero") == "SU-062")
    assert su062["type"] == "sentencia"
    assert su062["anio"] == "2019"

def test_extract_constitucion_and_codigos():
    text = "El artículo 29 de la Constitución Política garantiza el debido proceso, en concordancia con el Código Penal."
    citations = extract_legal_citations(text)
    
    assert len(citations) == 2
    
    const = next(c for c in citations if c["type"] == "constitucion")
    assert const["articulo"] == "29"
    
    cod = next(c for c in citations if c["type"] == "codigo")
    assert cod["nombre"] == "penal"

def test_citations_to_strings():
    citations = [
        {"type": "ley", "numero": "100", "anio": "1993", "articulo": "13"},
        {"type": "sentencia", "numero": "C-274", "anio": "2013"}
    ]
    strings = citations_to_strings(citations)
    assert len(strings) == 2
    assert strings[0] == "Artículo 13 de la Ley 100 de 1993"
    assert strings[1] == "Sentencia C-274 de 2013"
