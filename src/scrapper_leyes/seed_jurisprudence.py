import json
import re
from pathlib import Path

from scrapper_leyes.config import settings
from scrapper_leyes.storage.database import Database
from scrapper_leyes.models import build_canonical_id, TIPO_CANONICAL

# Matches "Sentencia C-274 de 2013", "Sentencia de la Corte Constitucional C-122 de 2020", etc.
SENTENCIA_RE = re.compile(
    r"Sentencia(?:.*?)"
    r"\b(?P<sala>C|SU|T|A)\s*-\s*"
    r"(?P<numero>\d+)\s*"
    r"(?:de\s+|/|-)(?P<anio>\d{4})",
    re.IGNORECASE
)

def main():
    db = Database(settings.catalog_db_path)
    
    parsed_files = list(settings.raw_dir.rglob("parsed.json"))
    print(f"Buscando en {len(parsed_files)} normas analizadas...")
    
    sentencias_encontradas = set()
    
    for pf in parsed_files:
        try:
            with open(pf, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
            
        for aff in data.get("jurisprudence", []) + data.get("modifications", []):
            text = aff.get("source_text", "")
            if not text:
                continue
                
            for m in SENTENCIA_RE.finditer(text):
                sala = m.group("sala").upper()
                numero = m.group("numero")
                anio = m.group("anio")
                
                # Para la corte constitucional, la sala C, SU, T
                if sala in ["C", "SU", "T", "A"]:
                    corte = "cc"
                    if sala == "C": sala_code = "plena"
                    elif sala == "SU": sala_code = "plena" # Unificación
                    elif sala == "T": sala_code = "revision"
                    else: sala_code = "auto"
                    
                    radicado = f"{sala}-{numero}"
                    sentencias_encontradas.add((radicado, anio, corte, sala_code))
    
    print(f"Se encontraron {len(sentencias_encontradas)} sentencias únicas.")
    
    # Insert in catalog
    rows_to_insert = []
    for radicado, anio, corte, sala_code in sentencias_encontradas:
        rows_to_insert.append({
            "tipo": "SENTENCIA",
            "numero": radicado,
            "anio": anio,
            "sector": None,
            "subtipo": "JURISPRUDENCIA",
            "vigencia": "Vigente",
            "entidad": "Corte Constitucional",
            "materia": None,
            "articulos": None,
            "corte": corte,
            "magistrado_ponente": None
        })
        
    if rows_to_insert:
        db.upsert_catalog_batch(rows_to_insert)
        print(f"Guardadas {len(rows_to_insert)} sentencias en el catálogo.")

if __name__ == "__main__":
    main()
