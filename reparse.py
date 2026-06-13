"""Reparse all cached sentencias with the improved LegalMapper (NER citations)."""

import json
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Add project to path
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from scrapper_leyes.config import Settings
from scrapper_leyes.storage.database import Database
from scrapper_leyes.storage.cache import ProvenanceCache
from scrapper_leyes.scraper.legal_mapper import LegalMapper


def main():
    settings = Settings()
    db = Database(settings.catalog_db_path)
    cache = ProvenanceCache(settings)
    mapper = LegalMapper()

    rows = db.conn.execute(
        "SELECT * FROM catalog WHERE tipo = 'SENTENCIA' AND scrape_status = 'done'"
    ).fetchall()
    
    logger.info(f"Found {len(rows)} sentencias to reparse")
    
    for row in rows:
        suin_id = row["suin_id"]
        raw = cache.load_raw("corte_constitucional", "SENTENCIA", suin_id)
        if not raw:
            logger.warning(f"No raw HTML for {suin_id}, skipping")
            continue

        catalog_match = {
            "tipo": row["tipo"],
            "numero": row["numero"],
            "anio": row["anio"],
            "corte": row["corte"],
            "magistrado_ponente": row["magistrado_ponente"],
        }

        parsed = mapper.process_html(raw, suin_id, catalog_match)
        if parsed:
            cache.store_parsed("corte_constitucional", "SENTENCIA", suin_id, parsed.to_dict())
            logger.info(
                f"  ✓ {suin_id}: {len(parsed.citaciones)} citaciones, "
                f"sala={parsed.sala}, MP={parsed.magistrado_ponente}"
            )
        else:
            logger.error(f"  ✗ {suin_id}: parse failed")

    db.close()
    logger.info("Done reparsing all sentencias.")


if __name__ == "__main__":
    main()
