#!/usr/bin/env python3
"""Scraper de jurisprudencia CSJ y Consejo de Estado usando CloakBrowser.

Aprovecha CloakBrowser (Chromium stealth) para navegar el buscador JSF de
WebRelatoria que no funciona con httpx (ViewState dinámico).

Estrategia:
1. Cargar la página de búsqueda
2. Rellenar el formulario de rango de fechas
3. Click en buscar
4. Esperar a que cargue la tabla de resultados
5. Parsear cada fila (número de radicado, fecha, tipo, ponente, etc.)
6. Sembrar en catalog.db
7. Paginar con el botón "Siguiente" del JSF
"""
import sys
import time
import json
import sqlite3
import random
import logging
from pathlib import Path
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

DB_PATH = str(Path(__file__).resolve().parent.parent / "data" / "catalog.db")

# Configuración por corte
CORTES = {
    "csj": {
        "url": "https://consultajurisprudencial.ramajudicial.gov.co/WebRelatoria/csj/index.xhtml",
        "corte": "CSJ",
        "source": "csj",
    },
    "consejo_estado": {
        "url": "https://jurisprudencia.ramajudicial.gov.co/WebRelatoria/ce/index.xhtml",
        "corte": "CE",
        "source": "consejo_estado",
    },
}


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=60000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def seed_norma(conn, source, corte, row_data):
    """Inserta una norma en el catálogo si no existe."""
    external_id = row_data.get("file_id") or row_data.get("radicado") or ""
    if not external_id:
        return False

    # Verificar si ya existe
    exists = conn.execute(
        "SELECT id FROM catalog WHERE source=? AND external_id=?",
        (source, external_id),
    ).fetchone()
    if exists:
        return False

    tipo = "SENTENCIA"
    numero = row_data.get("radicado", "")
    anio = row_data.get("anio", "")

    canonical_id = f"co:{tipo.lower()}:{numero}:{anio}" if numero and anio else None
    source_url = row_data.get("url", "")

    conn.execute(
        """INSERT OR IGNORE INTO catalog
           (tipo, numero, anio, source, external_id, source_url, canonical_id,
            corte, resolve_status, scrape_status, entidad, sector)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'resolved', 'pending', ?, ?)""",
        (tipo, numero, anio, source, external_id, source_url, canonical_id,
         corte, row_data.get("sala", ""), row_data.get("ponente", "")),
    )
    return True


def scrape_corte(corte_key, fecha_desde, fecha_hasta, max_pages=50):
    """Scrapea una corte por rango de fechas."""
    cfg = CORTES[corte_key]
    from cloakbrowser import launch

    log.info(f"=== Scrapeando {cfg['corte']} ({fecha_desde} a {fecha_hasta}) ===")

    browser = launch(headless=True, humanize=True)
    page = browser.new_page()

    try:
        # 1. Cargar página
        log.info(f"Cargando {cfg['url']}")
        page.goto(cfg["url"], timeout=30000)
        time.sleep(3)

        # 2. Forzar visibilidad de campos de fecha con JavaScript
        page.evaluate("""
            // Mostrar campos de fecha ocultos por JSF
            document.querySelectorAll('input[id*=\"fechaIniCal\"], input[id*=\"fechaFinCal\"]').forEach(el => {
                el.style.display = 'inline-block';
                el.style.visibility = 'visible';
                el.style.opacity = '1';
                el.removeAttribute('disabled');
            });
            // Expandir el fieldset si está colapsado
            var set = document.querySelector('[id*="set-fecha"]');
            if (set && set.getAttribute('data-collapsed') !== 'false') {
                set.click();
            }
        """)
        time.sleep(1)

        # Llenar fechas via JavaScript (más confiable que fill)
        page.evaluate(f"""
            var ini = document.querySelector('input[id*="fechaIniCal"]');
            var fin = document.querySelector('input[id*="fechaFinCal"]');
            if (ini) {{ ini.value = '{fecha_desde}'; ini.dispatchEvent(new Event('input', {{bubbles:true}})); ini.dispatchEvent(new Event('change', {{bubbles:true}})); }}
            if (fin) {{ fin.value = '{fecha_hasta}'; fin.dispatchEvent(new Event('input', {{bubbles:true}})); fin.dispatchEvent(new Event('change', {{bubbles:true}})); }}
        """)
        time.sleep(1)
        log.info(f"Fechas establecidas: {fecha_desde} a {fecha_hasta}")

        # 3. Click en botón de búsqueda - selector específico de WebRelatoria
        btn = page.query_selector("#searchForm\\:searchButton")
        if not btn:
            btn = page.query_selector("button[id*='searchButton']")
        if btn:
            log.info("Click en searchForm:searchButton")
            btn.click()
            clicked = True
        else:
            log.error("No se encontró botón de búsqueda")
            return 0

        # 4. Esperar el primer resultado
        log.info("Esperando resultados...")
        time.sleep(8)

        conn = get_db()
        total_seeded = 0
        current_idx = 1
        max_results = max_pages * 50  # max_results en vez de páginas

        while current_idx <= max_results:
            # Extraer el resultado actual
            text = page.query_selector("body").inner_text()

            # Buscar el contador "Resultado: X / Y"
            import re as _re
            counter_m = _re.search(r"Resultado:\s*(\d+)\s*/\s*(\d+)", text)
            if counter_m:
                current_idx = int(counter_m.group(1))
                total_count = int(counter_m.group(2))
                if current_idx == 1:
                    log.info(f"Total resultados: {total_count}")
            else:
                log.warning("No se encontró contador de resultados, terminando")
                break

            # Extraer data-rk del resultado actual
            rk_el = page.query_selector("tr[data-rk]")
            if rk_el:
                file_id = rk_el.get_attribute("data-rk") or ""
                row_text = rk_el.inner_text()

                # Extraer campos
                mf = _re.search(r"(\d{2})/(\d{2})/(\d{4})", row_text)
                anio = mf.group(3) if mf else ""

                radicado = ""
                mrad = _re.search(r"(?:NÚMERO DE PROCESO|NÚMERO DE PROVIDENCIA)\s*:?\s*([0-9-]+)", row_text, _re.I)
                if mrad:
                    radicado = mrad.group(1)
                mrad2 = _re.search(r"(?:NR|ID)\s*:?\s*(\d+)", row_text)
                if not radicado and mrad2:
                    radicado = mrad2.group(1)

                ponente = ""
                mpon = _re.search(r"PONENTE\s*:?\s*(.*?)(?:TEMA|FUENTE|SECCION|NR|$)", row_text, _re.I)
                if mpon:
                    ponente = mpon.group(1).strip()[:100]

                sala = ""
                msala = _re.search(r"(?:SALA|SECCION)\s*:?\s*(.*?)(?:NR|FECHA|PONENTE|NÚMERO|$)", row_text, _re.I)
                if msala:
                    sala = msala.group(1).strip()[:100]

                tipo_prov = ""
                mtipo = _re.search(r"TIPO DE PROVIDENCIA\s*:?\s*(\w+)", row_text, _re.I)
                if mtipo:
                    tipo_prov = mtipo.group(1)

                seeded = seed_norma(conn, cfg["source"], cfg["corte"], {
                    "file_id": file_id,
                    "radicado": radicado or file_id,
                    "anio": anio,
                    "url": f"{cfg['url']}#{file_id}",
                    "sala": sala,
                    "ponente": ponente,
                })
                if seeded:
                    total_seeded += 1
                conn.commit()
            else:
                log.warning(f"Resultado {current_idx}: no se encontró data-rk")

            if current_idx % 100 == 0:
                log.info(f"Procesado resultado {current_idx}/{total_count} (sembrados: {total_seeded})")

            # Click en "siguiente" - los botones JSF j_idt256-259 son navegación
            # j_idt259 suele ser "siguiente/último"
            next_clicked = False
            for btn_id in ["resultForm:j_idt259", "resultForm:j_idt258", "resultForm:j_idt257"]:
                btn = page.query_selector(f"#{btn_id.replace(':', chr(92) + ':')}")
                if btn:
                    try:
                        btn.click()
                        next_clicked = True
                        break
                    except:
                        continue

            if not next_clicked:
                log.info("No hay botón siguiente disponible")
                break

            time.sleep(2)
            current_idx += 1

        conn.close()
        log.info(f"=== {cfg['corte']}: {total_seeded} providencias sembradas ===")
        return total_seeded

    except Exception as e:
        log.error(f"Error scrapeando {corte_key}: {e}")
        return 0
    finally:
        browser.close()


def main():
    corte = sys.argv[1] if len(sys.argv) > 1 else "csj"
    desde = sys.argv[2] if len(sys.argv) > 2 else "01/01/2023"
    hasta = sys.argv[3] if len(sys.argv) > 3 else "31/12/2023"
    max_pages = int(sys.argv[4]) if len(sys.argv) > 4 else 50

    if corte not in CORTES:
        print(f"Corte inválido. Opciones: {list(CORTES.keys())}")
        sys.exit(1)

    total = scrape_corte(corte, desde, hasta, max_pages)
    print(f"\nTotal sembrado: {total}")


if __name__ == "__main__":
    main()
