"""Barrido de IDs para EVA (Función Pública) - siembra masiva al catálogo.

EVA tiene ~261k IDs secuenciales. Este script los barre en paralelo,
detecta los que tienen contenido real, y siembra CatalogSeeds.
"""
import asyncio
import logging
import re
import sqlite3
import sys
from pathlib import Path
from html import unescape
import threading

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)

DB_PATH = "/app/data/catalog.db"
_BASE = "https://www.funcionpublica.gov.co/eva/gestornormativo/"
_PDF_URL = "https://www.funcionpublica.gov.co/eva/gestornormativo/norma_pdf.php?i={i}"
_HTML_URL = "https://www.funcionpublica.gov.co/eva/gestornormativo/norma.php?i={i}"

_TAGS_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

_NORMA_TEXT_RE = re.compile(
    r"^\s*"
    r"(?P<tipo>[A-Za-zÁÉÍÓÚÑáéíóúñ./ ]+?)\s+"
    r"(?:N[°ºo.]*\s*)?"
    r"(?P<numero>[0-9][0-9A-Za-z\-/]*)\s+"
    r"(?:de|del|of)\s+"
    r"(?P<anio>\d{4})",
    re.IGNORECASE,
)

_TIPO_MAP = {
    "ley": "LEY", "decreto": "DECRETO", "resolucion": "RESOLUCION", "resolución": "RESOLUCION",
    "concepto": "CONCEPTO", "acuerdo": "ACUERDO", "circular": "CIRCULAR",
    "directiva": "DIRECTIVA", "acto legislativo": "ACTO LEGISLATIVO",
}

# IDs vacíos (template de 13066 bytes) vs con contenido
MIN_CONTENT_LEN = 15000


def _clean_text(html_fragment: str) -> str:
    txt = _TAGS_RE.sub(" ", html_fragment)
    txt = unescape(txt)
    return _WS_RE.sub(" ", txt).strip()


def _extract_norma_info(html: str, eva_id: str):
    """Extrae tipo/numero/año del HTML de EVA."""
    # Buscar el título de la norma en la página
    # EVA usa <td class="cuerpo"> o tablas con el texto
    text = _clean_text(html)
    
    # Buscar patrón "Ley N de AÑO" / "Decreto N de AÑO"
    m = _NORMA_TEXT_RE.search(text[:2000])
    if m:
        tipo_raw = m.group("tipo").strip().lower()
        tipo_raw = _WS_RE.sub(" ", tipo_raw)
        tipo = _TIPO_MAP.get(tipo_raw, tipo_raw.upper())
        numero = m.group("numero").strip()
        anio = m.group("anio")
        return tipo, numero, anio
    return "NORMA", eva_id, None


async def fetch_and_seed(client, eva_id, db_lock, conn):
    """Fetch un ID de EVA y siembra en el catálogo si tiene contenido."""
    try:
        url = _HTML_URL.format(i=eva_id)
        r = await client.get(url, timeout=15)
        if r.status_code != 200:
            return False
        if len(r.text) < MIN_CONTENT_LEN:
            return False  # template vacío
        
        tipo, numero, anio = _extract_norma_info(r.text, str(eva_id))
        source_url = _PDF_URL.format(i=eva_id)
        external_id = f"eva_{eva_id}"
        
        with db_lock:
            # Check si ya existe
            existing = conn.execute(
                "SELECT id FROM catalog WHERE source='funcion_publica' AND external_id=?",
                (external_id,)
            ).fetchone()
            if existing:
                return False
            
            # Insertar
            conn.execute(
                """INSERT OR IGNORE INTO catalog 
                   (tipo, numero, anio, source, source_url, external_id, suin_id, 
                    resolve_status, scrape_status, entidad)
                   VALUES (?,?,?,?,?,?,?,?,?, 'Función Pública')""",
                (tipo, numero, anio, "funcion_publica", source_url, external_id, external_id,
                 "resolved", "pending")
            )
        return True
    except Exception as e:
        logger.debug(f"EVA {eva_id} error: {e}")
        return False


async def sweep_range(start, end, concurrency=20, db_lock=None, conn=None):
    """Barre un rango de IDs de EVA."""
    sem = asyncio.Semaphore(concurrency)
    found = 0
    
    async with httpx.AsyncClient(
        headers={"User-Agent": "ScrapperLeyes/1.0 (investigacion academica)"},
        timeout=httpx.Timeout(15.0),
        verify=False,
        follow_redirects=True,
    ) as client:
        async def _fetch(i):
            nonlocal found
            async with sem:
                if await fetch_and_seed(client, i, db_lock, conn):
                    found += 1
                    if found % 100 == 0:
                        with db_lock:
                            conn.commit()
                        logger.info(f"[{start}-{end}] {found} normas sembradas ({i}/{end})")
        
        tasks = [_fetch(i) for i in range(start, end + 1)]
        await asyncio.gather(*tasks)
    
    return found


async def main():
    # EVA: IDs 1..261000. Dividir en rangos para paralelizar.
    # Los IDs bajos (<20k) suelen estar vacíos. Empezar desde 20k.
    ID_START = int(sys.argv[1]) if len(sys.argv) > 1 else 20000
    ID_END = int(sys.argv[2]) if len(sys.argv) > 2 else 261000
    CONCURRENCY = int(sys.argv[3]) if len(sys.argv) > 3 else 20
    BATCH_SIZE = 500  # commit cada 500 IDs
    
    logger.info(f"EVA sweep: IDs {ID_START}..{ID_END}, concurrency={CONCURRENCY}")
    
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    db_lock = threading.Lock()
    
    total_found = 0
    # Procesar en batches para no saturar
    batch_start = ID_START
    while batch_start < ID_END:
        batch_end = min(batch_start + BATCH_SIZE, ID_END)
        found = await sweep_range(batch_start, batch_end, CONCURRENCY, db_lock, conn)
        total_found += found
        with db_lock:
            conn.commit()
        logger.info(f"Batch {batch_start}-{batch_end}: +{found} (total: {total_found})")
        batch_start = batch_end
    
    conn.close()
    logger.info(f"COMPLETO: {total_found} normas EVA sembradas en rango {ID_START}-{ID_END}")


if __name__ == "__main__":
    asyncio.run(main())
