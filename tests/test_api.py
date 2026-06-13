from fastapi.testclient import TestClient
from scrapper_leyes.api.main import app
import pytest
from unittest.mock import patch, MagicMock

client = TestClient(app)

def test_health_endpoint():
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

@patch("scrapper_leyes.api.main._get_conn")
def test_stats_endpoint(mock_get_conn):
    mock_conn = MagicMock()
    mock_get_conn.return_value = mock_conn
    
    # Mock all the execute calls
    def mock_execute_side_effect(query, *args, **kwargs):
        mock_cursor = MagicMock()
        if "COUNT(*) FROM catalog" in query and "tipo" not in query and "status" not in query:
            mock_cursor.fetchone.return_value = [100]
        elif "GROUP BY tipo" in query:
            mock_cursor.fetchall.return_value = [{"tipo": "LEY", "count": 50}, {"tipo": "DECRETO", "count": 50}]
        elif "GROUP BY scrape_status" in query:
            mock_cursor.fetchall.return_value = [{"scrape_status": "done", "count": 80}, {"scrape_status": "error", "count": 20}]
        elif "GROUP BY resolve_status" in query:
            mock_cursor.fetchall.return_value = []
        elif "tipo='SENTENCIA'" in query:
            mock_cursor.fetchone.return_value = [10]
        elif "tipo='LEY'" in query:
            mock_cursor.fetchone.return_value = [50]
        elif "tipo='DECRETO'" in query:
            mock_cursor.fetchone.return_value = [40]
        elif "scrape_status='done'" in query:
            mock_cursor.fetchone.return_value = [80]
        elif "scrape_status='error'" in query:
            mock_cursor.fetchone.return_value = [20]
        else:
            mock_cursor.fetchone.return_value = [0]
        return mock_cursor
        
    mock_conn.execute.side_effect = mock_execute_side_effect
    
    response = client.get("/api/stats")
    assert response.status_code == 200
    data = response.json()
    assert data["total_norms"] == 100
    assert data["leyes"] == 50
    assert data["scraped_done"] == 80
    assert len(data["by_tipo"]) == 2

@patch("scrapper_leyes.api.main._get_conn")
def test_catalog_endpoint(mock_get_conn):
    mock_conn = MagicMock()
    mock_get_conn.return_value = mock_conn
    
    mock_cursor = MagicMock()
    # First query is COUNT(*)
    # Second query is SELECT *
    def mock_execute_side_effect(query, *args, **kwargs):
        m = MagicMock()
        if "COUNT(*)" in query:
            m.fetchone.return_value = [1]
        else:
            m.fetchall.return_value = [
                {"id": 1, "suin_id": "123", "tipo": "LEY", "numero": "100", "anio": "1993"}
            ]
        return m
        
    mock_conn.execute.side_effect = mock_execute_side_effect
    
    response = client.get("/api/catalog?limit=10")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["numero"] == "100"

@patch("scrapper_leyes.api.main._get_conn")
@patch("scrapper_leyes.api.main._find_parsed")
def test_norms_text_endpoint(mock_find_parsed, mock_get_conn):
    mock_conn = MagicMock()
    mock_get_conn.return_value = mock_conn
    
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = {"id": 1, "suin_id": "123", "tipo": "LEY"}
    mock_conn.execute.return_value = mock_cursor
    
    mock_find_parsed.return_value = {
        "articles": [{"number": "1", "text": "Texto del articulo"}],
        "raw_text": "Texto completo"
    }
    
    response = client.get("/api/norms/123/text")
    assert response.status_code == 200
    data = response.json()
    assert "articles" in data
    assert data["_catalog"]["suin_id"] == "123"
