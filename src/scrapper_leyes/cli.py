"""CLI entry point for the normative ingestion pipeline."""

from __future__ import annotations

import logging
import sys

import click
from rich.console import Console
from rich.table import Table

from scrapper_leyes.config import Settings
from scrapper_leyes.storage.cache import ProvenanceCache
from scrapper_leyes.storage.database import Database

console = Console()


def _get_deps(
    data_dir: str | None = None,
) -> tuple[Settings, Database, ProvenanceCache]:
    """Initialize shared dependencies."""
    if data_dir:
        import os
        os.environ["DATA_DIR"] = data_dir
    settings = Settings()
    settings.ensure_dirs()
    db = Database(settings.catalog_db_path)
    cache = ProvenanceCache(settings)
    return settings, db, cache


@click.group()
@click.option("--data-dir", default=None, help="Override data directory")
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging")
@click.pass_context
def main(ctx: click.Context, data_dir: str | None, verbose: bool) -> None:
    """Pipeline de ingesta normativa colombiana."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    ctx.ensure_object(dict)
    ctx.obj["data_dir"] = data_dir


# ═══════════════════════════════════════════════════════════════════════════
# Catalog commands
# ═══════════════════════════════════════════════════════════════════════════


@main.group()
def catalog() -> None:
    """Gestión del catálogo Socrata (datos.gov.co)."""


@catalog.command()
@click.option("--tipo", default=None, help="Filter by norm type (e.g. LEY)")
@click.option("--limit", default=None, type=int, help="Max records to fetch")
@click.option(
    "--dataset",
    default="legislacion",
    help="Dataset Socrata a sincronizar: legislacion | cc_sentencias",
)
@click.pass_context
def sync(ctx: click.Context, tipo: str | None, limit: int | None, dataset: str) -> None:
    """Sincronizar catálogo desde datos.gov.co."""
    settings, db, _ = _get_deps(ctx.obj.get("data_dir"))

    from scrapper_leyes.catalog.socrata_client import (
        CATALOG_SOURCES,
        fetch_catalog_count,
        sync_catalog,
    )

    catalog_source = CATALOG_SOURCES.get(dataset)
    if catalog_source is None:
        console.print(
            f"[red]Dataset desconocido: {dataset}. "
            f"Opciones: {', '.join(CATALOG_SOURCES)}[/red]"
        )
        db.close()
        sys.exit(1)

    # Show remote count first
    try:
        remote_count = fetch_catalog_count(settings, tipo=tipo, catalog_source=catalog_source)
        console.print(
            f"[bold]Registros en Socrata [{dataset}]{f' (tipo={tipo})' if tipo else ''}: "
            f"{remote_count:,}[/bold]"
        )
    except Exception as e:
        console.print(f"[yellow]No se pudo obtener conteo remoto: {e}[/yellow]")

    total = sync_catalog(settings, db, tipo=tipo, limit=limit, catalog_source=catalog_source)
    local_count = db.get_catalog_count(tipo=tipo)

    console.print(f"\n[green]✓ Sincronizados {total:,} registros[/green]")
    console.print(f"[bold]Total en catálogo local: {local_count:,}[/bold]")

    db.close()


@catalog.command()
@click.option("--source", required=True, help="Fuente crawl-driven (ver `sources list`)")
@click.option("--desde", default=None, help="Descubrir desde fecha YYYY-MM-DD")
@click.option("--hasta", default=None, help="Descubrir hasta fecha YYYY-MM-DD")
@click.option("--limit", default=None, type=int, help="Máx documentos a descubrir")
@click.pass_context
def discover(
    ctx: click.Context,
    source: str,
    desde: str | None,
    hasta: str | None,
    limit: int | None,
) -> None:
    """Descubrir documentos de una fuente crawl-driven y sembrar el catálogo.

    Ejecuta el discoverer de la fuente (relatoría/normograma propio) y persiste
    los CatalogSeed; después aplica el pipeline normal resolve→scrape→export.
    """
    from datetime import date

    settings, db, cache = _get_deps(ctx.obj.get("data_dir"))
    from scrapper_leyes.scraper.factory import ScraperFactory

    factory = ScraperFactory(settings, db, cache)
    try:
        discoverer = factory.get_discoverer(source)
    except NotImplementedError as e:
        console.print(f"[yellow]{e}[/yellow]")
        db.close()
        sys.exit(1)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        db.close()
        sys.exit(1)

    def _d(s: str | None) -> date | None:
        return date.fromisoformat(s) if s else None

    seeds: list[dict] = []
    for seed in discoverer.discover(desde=_d(desde), hasta=_d(hasta)):
        seeds.append(seed.to_catalog_row())
        if limit and len(seeds) >= limit:
            break

    inserted = db.upsert_catalog_seed(seeds) if seeds else 0
    console.print(
        f"\n[green]✓ Descubiertos {len(seeds)} documentos; "
        f"nuevos en catálogo: {inserted}[/green]"
    )
    db.close()


@catalog.command()
@click.option("--tipo", default=None, help="Filter by norm type")
@click.pass_context
def stats(ctx: click.Context, tipo: str | None) -> None:
    """Mostrar estadísticas del catálogo."""
    settings, db, _ = _get_deps(ctx.obj.get("data_dir"))

    table = Table(title="Catálogo por tipo")
    table.add_column("Tipo", style="cyan")
    table.add_column("Resolve", style="yellow")
    table.add_column("Scrape", style="green")
    table.add_column("Count", justify="right")

    for row in db.get_catalog_stats():
        if tipo and row["tipo"] != tipo:
            continue
        table.add_row(
            row["tipo"],
            row["resolve_status"],
            row["scrape_status"],
            str(row["cnt"]),
        )

    console.print(table)
    console.print(f"\nTotal: {db.get_catalog_count(tipo=tipo):,}")
    db.close()


# ═══════════════════════════════════════════════════════════════════════════
# Scrape commands
# ═══════════════════════════════════════════════════════════════════════════


@main.group()
def scrape() -> None:
    """Scraping de normas desde SUIN-Juriscol."""


@scrape.command()
@click.option("--tipo", default=None, help="Filter by norm type (e.g. LEY)")
@click.option("--limit", default=None, type=int, help="Max norms to scrape")
@click.option("--source", default="suin", help="Data source to scrape (suin, corte_constitucional)")
@click.option("--workers", default=None, type=int, help="Requests concurrentes (default: 5)")
@click.option("--rps", default=None, type=float, help="Requests por segundo, ritmo global (default: 3)")
@click.option("--retry-errors", is_flag=True,
              help="Re-encola las normas en 'error' (bajo el tope de intentos) antes de scrapear")
@click.option("--max-attempts", default=3, type=int,
              help="Tope de intentos por norma al re-encolar errores (default: 3)")
@click.pass_context
def run(
    ctx: click.Context,
    tipo: str | None,
    limit: int | None,
    source: str,
    workers: int | None,
    rps: float | None,
    retry_errors: bool,
    max_attempts: int,
) -> None:
    """Ejecutar scraping de normas pendientes."""
    settings, db, cache = _get_deps(ctx.obj.get("data_dir"))

    # Retry entre corridas: re-encola 'error' → 'pending' (con tope de intentos)
    # para que get_pending_norms vuelva a tomarlas. Sin esto, un fallo de red
    # deja la norma abandonada para siempre.
    if retry_errors:
        requeued = db.reset_errors_to_pending(
            tipo=tipo, source=source if source != "suin" else None,
            max_attempts=max_attempts,
        )
        console.print(
            f"[yellow]↻ Re-encoladas {requeued} normas en 'error' "
            f"(intentos < {max_attempts}).[/yellow]"
        )

    from scrapper_leyes.scraper.factory import ScraperFactory
    import asyncio

    factory = ScraperFactory(settings, db, cache)
    indexer = factory.get_indexer(source)
    scraper = factory.get_scraper(source)
    # Ritmo configurable por corrida (concurrencia / rps).
    if (workers or rps) and hasattr(scraper, "reconfigure"):
        scraper.reconfigure(workers=workers, rps=rps)

    # Step 1: Resolve IDs if there are unresolved norms
    unresolved = db.get_unresolved_norms(tipo=tipo)
    # Filter unresolved by entity/source mapping if necessary. For now, assume if we are 
    # running --source corte_constitucional, we only want to resolve sentencias.
    if source == "corte_constitucional":
        unresolved = [r for r in unresolved if r["tipo"] == "SENTENCIA"]
    elif source == "suin":
        unresolved = [r for r in unresolved if r["tipo"] != "SENTENCIA"]

    if unresolved:
        console.print(
            f"\n[bold]Paso 1: Resolviendo {len(unresolved)} IDs de {source}...[/bold]"
        )
        resolve_stats = indexer.resolve_batch(unresolved)

        console.print(f"  Resueltos: {resolve_stats.get('resolved', 0)}")
        console.print(f"  Ambiguos: {resolve_stats.get('ambiguous', 0)}")
        console.print(f"  No encontrados: {resolve_stats.get('not_found', 0)}")
        console.print(f"  Errores: {resolve_stats.get('error', 0)}")

    # Step 2: Scrape resolved+pending norms
    pending = db.get_pending_norms(tipo=tipo, limit=limit)
    if source == "corte_constitucional":
        pending = [r for r in pending if r["tipo"] == "SENTENCIA"]
    elif source == "suin":
        pending = [r for r in pending if r["tipo"] != "SENTENCIA"]

    if not pending:
        console.print("\n[green]No hay normas pendientes de scraping.[/green]")
        db.close()
        return

    console.print(f"\n[bold]Paso 2: Scrapeando {len(pending)} normas desde {source}...[/bold]")
    scrape_stats = asyncio.run(scraper.scrape_batch(pending))

    # Show results
    table = Table(title="Resultados del scraping")
    table.add_column("Estado", style="cyan")
    table.add_column("Count", justify="right")
    for status, count in sorted(scrape_stats.items()):
        table.add_row(status, str(count))
    console.print(table)

    # Show unmapped affectations if any
    unmapped = db.get_unmapped_count()
    if unmapped:
        console.print(
            f"\n[yellow]! {unmapped} afectaciones sin mapear "
            f"(ver tabla unmapped_affectations)[/yellow]"
        )

    # Show vigencia discrepancies
    discrepancies = db.get_discrepancy_count()
    if discrepancies:
        console.print(
            f"[yellow]! {discrepancies} discrepancias de vigencia "
            f"catálogo vs SUIN[/yellow]"
        )

    db.close()


@scrape.command()
@click.option("--tipo", default=None, help="Filter by norm type")
@click.pass_context
def status(ctx: click.Context, tipo: str | None) -> None:
    """Mostrar estado del scraping con tasa de resolución."""
    settings, db, _ = _get_deps(ctx.obj.get("data_dir"))

    # Resolution stats
    resolve = db.get_resolve_stats(tipo=tipo)
    console.print("\n[bold]═══ Resolución de IDs ═══[/bold]")
    r_table = Table()
    r_table.add_column("Estado", style="cyan")
    r_table.add_column("Count", justify="right")
    r_table.add_column("%", justify="right")
    total_resolve = sum(resolve.values()) or 1
    for status_name, count in sorted(resolve.items()):
        pct = f"{count / total_resolve * 100:.1f}%"
        r_table.add_row(status_name, str(count), pct)
    r_table.add_row("[bold]TOTAL[/bold]", f"[bold]{total_resolve}[/bold]", "100%")
    console.print(r_table)

    # Scrape stats
    scrape_s = db.get_scrape_stats(tipo=tipo)
    console.print("\n[bold]═══ Estado del Scraping ═══[/bold]")
    s_table = Table()
    s_table.add_column("Estado", style="cyan")
    s_table.add_column("Count", justify="right")
    s_table.add_column("%", justify="right")
    total_scrape = sum(scrape_s.values()) or 1
    for status_name, count in sorted(scrape_s.items()):
        pct = f"{count / total_scrape * 100:.1f}%"
        s_table.add_row(status_name, str(count), pct)
    s_table.add_row("[bold]TOTAL[/bold]", f"[bold]{total_scrape}[/bold]", "100%")
    console.print(s_table)

    # Extra metrics
    unmapped = db.get_unmapped_count()
    discrepancies = db.get_discrepancy_count()
    console.print(f"\nAfectaciones sin mapear: {unmapped}")
    console.print(f"Discrepancias de vigencia: {discrepancies}")

    db.close()


@scrape.command()
@click.option("--suin-id", required=True, help="SUIN ID to parse")
@click.pass_context
def parse(ctx: click.Context, suin_id: str) -> None:
    """Re-parsear un documento ya descargado."""
    settings, db, cache = _get_deps(ctx.obj.get("data_dir"))

    # Find the tipo from catalog
    row = db.conn.execute(
        "SELECT * FROM catalog WHERE suin_id = ?", (suin_id,)
    ).fetchone()
    if not row:
        console.print(f"[red]suin_id {suin_id} no encontrado en catálogo[/red]")
        db.close()
        sys.exit(1)

    tipo = row["tipo"]
    raw = cache.load_raw("suin", tipo, suin_id)
    if not raw:
        console.print(f"[red]No hay HTML en cache para {suin_id}[/red]")
        db.close()
        sys.exit(1)

    from scrapper_leyes.scraper.html_parser import parse_suin_html

    html = raw.decode("utf-8", errors="replace")
    parsed_norm = parse_suin_html(html, suin_id)
    cache.store_parsed("suin", tipo, suin_id, parsed_norm.to_dict())

    console.print(f"[green]✓ Parseado: {len(parsed_norm.articles)} artículos, "
                  f"{len(parsed_norm.modifications)} modificaciones, "
                  f"{len(parsed_norm.jurisprudence)} sentencias[/green]")

    db.close()


@scrape.command()
@click.option("--tipo", default=None, help="Filter by norm type (e.g. LEY)")
@click.pass_context
def reparse(ctx: click.Context, tipo: str | None) -> None:
    """Re-parsear todas las normas SUIN descargadas (regenera parsed.json).

    Refresca con el parser actual: texto limpio (sin ruido de UI) y afectaciones
    salientes (qué deroga/modifica cada artículo de otras normas).
    """
    settings, db, cache = _get_deps(ctx.obj.get("data_dir"))

    from scrapper_leyes.scraper.html_parser import parse_suin_html

    sql = "SELECT * FROM catalog WHERE scrape_status = 'done' AND tipo != 'SENTENCIA'"
    params: list[str] = []
    if tipo:
        sql += " AND tipo = ?"
        params.append(tipo)
    rows = db.conn.execute(sql, params).fetchall()

    ok = 0
    affects_total = 0
    for row in rows:
        suin_id = row["suin_id"]
        if not suin_id:
            continue
        raw = cache.load_raw("suin", row["tipo"], suin_id)
        if not raw:
            continue
        try:
            parsed = parse_suin_html(raw.decode("utf-8", errors="replace"), suin_id)
        except Exception as e:
            console.print(f"[yellow]✗ {suin_id}: {e}[/yellow]")
            continue
        cache.store_parsed("suin", row["tipo"], suin_id, parsed.to_dict())
        affects_total += sum(len(a.affects) for a in parsed.articles)
        ok += 1

    console.print(
        f"\n[green]✓ Re-parseadas {ok} normas[/green] "
        f"({affects_total} afectaciones salientes capturadas)"
    )
    db.close()


# Corte code → cache source folder (mirrors the scrapers).
_CORTE_SOURCE = {
    "cc": "corte_constitucional",
    "csj": "csj",
    "ce": "consejo_estado",
}


@scrape.command(name="reparse-sentencias")
@click.option("--corte", default=None, help="Filtrar por corte (cc, csj, ce)")
@click.pass_context
def reparse_sentencias(ctx: click.Context, corte: str | None) -> None:
    """Re-parsear sentencias descargadas (regenera parsed.json).

    Aplica el sectionizer guiado por encabezados (secciones normalizadas) y el
    parser de la parte resolutiva (órdenes tipadas: EXEQUIBLE / INEXEQUIBLE /
    …). Usa Docling si está instalado (mejor markdown); si no, cae al fallback
    de BeautifulSoup. Tras correrlo, re-exporta: ``export graph`` y
    ``export vector``.
    """
    settings, db, cache = _get_deps(ctx.obj.get("data_dir"))

    from scrapper_leyes.scraper.legal_mapper import LegalMapper

    sql = "SELECT * FROM catalog WHERE scrape_status = 'done' AND tipo = 'SENTENCIA'"
    params: list[str] = []
    if corte:
        sql += " AND corte = ?"
        params.append(corte)
    rows = db.conn.execute(sql, params).fetchall()

    mapper = LegalMapper()
    ok = 0
    sections_total = 0
    orders_total = 0
    for row in rows:
        suin_id = row["suin_id"]
        if not suin_id:
            continue
        source = _CORTE_SOURCE.get((row["corte"] or "cc").lower(), "corte_constitucional")
        raw = cache.load_raw(source, row["tipo"], suin_id)
        if not raw:
            console.print(f"[yellow]✗ {suin_id}: sin HTML en caché ({source})[/yellow]")
            continue
        catalog_match = {
            "tipo": row["tipo"],
            "numero": row["numero"],
            "anio": row["anio"],
            "corte": row["corte"],
            "magistrado_ponente": row["magistrado_ponente"],
        }
        try:
            parsed = mapper.process_html(raw, suin_id, catalog_match)
        except Exception as e:
            console.print(f"[yellow]✗ {suin_id}: {e}[/yellow]")
            continue
        if not parsed:
            console.print(f"[yellow]✗ {suin_id}: parseo vacío[/yellow]")
            continue
        d = parsed.to_dict()
        cache.store_parsed(source, row["tipo"], suin_id, d)
        sections_total += len(d.get("sections", []))
        orders_total += len(d.get("orders", []))
        ok += 1

    console.print(
        f"\n[green]✓ Re-parseadas {ok} sentencias[/green] "
        f"({sections_total} secciones, {orders_total} órdenes resolutivas)"
    )
    db.close()


# ═══════════════════════════════════════════════════════════════════════════
# Export commands
# ═══════════════════════════════════════════════════════════════════════════


@main.group()
def export() -> None:
    """Exportar el corpus a stores de recuperación (vector, grafo)."""


@export.command()
@click.option("--tipo", default=None, help="Filter by norm type (e.g. LEY)")
@click.option(
    "--recreate",
    is_flag=True,
    help="Drop and rebuild the collection (default: incremental upsert)",
)
@click.pass_context
def vector(ctx: click.Context, tipo: str | None, recreate: bool) -> None:
    """Embeber y subir chunks a Qdrant (hybrid dense+sparse, bge-m3 + BM25)."""
    settings, db, cache = _get_deps(ctx.obj.get("data_dir"))

    from scrapper_leyes.export_vector import VectorStoreExporter

    console.print(
        f"[bold]Exportando a Qdrant[/bold] "
        f"(collection={settings.qdrant_collection}, "
        f"dense={settings.embedding_model_dense}, sparse={settings.embedding_model_sparse})"
    )
    try:
        exporter = VectorStoreExporter(settings, db, cache)
        total = exporter.export_all(tipo=tipo, recreate=recreate)
        console.print(f"\n[green]✓ {total:,} chunks indexados en Qdrant[/green]")
    finally:
        db.close()


@export.command()
@click.pass_context
def graph(ctx: click.Context) -> None:
    """Exportar normas, artículos, sentencias y citaciones al grafo Neo4j."""
    settings, db, cache = _get_deps(ctx.obj.get("data_dir"))

    from scrapper_leyes.export_neo4j import Neo4jExporter

    console.print(f"[bold]Exportando a Neo4j[/bold] ({settings.neo4j_uri})")
    exporter = Neo4jExporter(
        settings, db, cache,
        uri=settings.neo4j_uri,
        user=settings.neo4j_user,
        password=settings.neo4j_password,
    )
    try:
        st = exporter.export_all()
        console.print(
            f"\n[green]✓ Grafo exportado:[/green] {st['norms']:,} normas, "
            f"{st['sentencias']:,} sentencias."
        )
        if st["failed"]:
            console.print(
                f"[yellow]! {st['failed']} documentos fallaron al exportar "
                f"(ver logs); el resto del grafo se construyó igual.[/yellow]"
            )
    finally:
        exporter.close()
        db.close()


# ═══════════════════════════════════════════════════════════════════════════
# Sources registry
# ═══════════════════════════════════════════════════════════════════════════


@main.group()
def sources() -> None:
    """Registro de fuentes del sistema legal colombiano."""


@sources.command(name="list")
@click.option("--capa", default=None, help="Filtrar por capa (A/B/C/D)")
@click.option("--pendientes", is_flag=True, help="Solo fuentes sin conector")
def sources_list(capa: str | None, pendientes: bool) -> None:
    """Listar todas las fuentes registradas y su estado."""
    from scrapper_leyes.sources import CAPA_LABEL, all_sources

    specs = all_sources()
    if capa:
        specs = [s for s in specs if s.capa == capa.upper()]
    if pendientes:
        specs = [s for s in specs if not s.implementado]

    table = Table(title="Fuentes del ordenamiento jurídico colombiano")
    table.add_column("Fuente", style="bold")
    table.add_column("Capa")
    table.add_column("Modo")
    table.add_column("Prio")
    table.add_column("Estado")
    _color = {
        "operativo": "green", "parcial": "yellow",
        "andamiaje": "orange3", "pendiente": "red",
    }
    for s in specs:
        table.add_row(
            s.nombre,
            CAPA_LABEL.get(s.capa, s.capa).split(" · ")[0],
            s.modo,
            s.prioridad,
            f"[{_color.get(s.estado, 'white')}]{s.estado}[/]",
        )
    console.print(table)
    total = len(all_sources())
    operativas = sum(1 for s in all_sources() if s.implementado)
    console.print(
        f"\n{operativas}/{total} fuentes con conector. "
        f"`scrapper-leyes sources list --pendientes` para ver lo que falta."
    )


# ═══════════════════════════════════════════════════════════════════════════
# Verify — calidad/consistencia del knowledge tras la ingesta
# ═══════════════════════════════════════════════════════════════════════════


@main.command()
@click.option("--strict", is_flag=True,
              help="Salir con código ≠ 0 también ante WARN (no solo FAIL)")
@click.pass_context
def verify(ctx: click.Context, strict: bool) -> None:
    """Verificar que el knowledge (catálogo+Qdrant+Neo4j) quedó íntegro y usable.

    Corre 5 chequeos (reconciliación, completitud, integridad relacional,
    recuperación, presupuesto de error) y sale con código ≠ 0 si algo falla —
    pensado para que el cron diario reviente si la ingesta dejó algo roto.
    """
    from scrapper_leyes.verify import FAIL, INFO, PASS, WARN, KnowledgeVerifier

    settings, db, cache = _get_deps(ctx.obj.get("data_dir"))
    verifier = KnowledgeVerifier(settings, db, cache)
    style = {PASS: "green", WARN: "yellow", FAIL: "red", INFO: "cyan"}
    try:
        results, verdict = verifier.run_all()
    finally:
        verifier.close()
        db.close()

    table = Table(title="Verificación del knowledge")
    table.add_column("Chequeo", style="bold")
    table.add_column("Estado")
    table.add_column("Detalle")
    for r in results:
        table.add_row(r.name, f"[{style[r.status]}]{r.status}[/{style[r.status]}]", r.message)
    console.print(table)
    console.print(
        f"\nVeredicto: [{style[verdict]}]{verdict}[/{style[verdict]}]"
    )
    if verdict == FAIL or (strict and verdict == WARN):
        sys.exit(1)


# ═══════════════════════════════════════════════════════════════════════════
# MCP — servidor de tools para LLMs
# ═══════════════════════════════════════════════════════════════════════════


@main.command()
@click.option("--transport", default="stdio",
              type=click.Choice(["stdio", "sse", "streamable-http"]),
              help="Transporte MCP (stdio local; sse/streamable-http remoto)")
@click.option("--host", default="0.0.0.0", help="Host para sse/streamable-http")
@click.option("--port", default=8765, type=int, help="Puerto para sse/streamable-http")
def mcp(transport: str, host: str, port: int) -> None:
    """Arrancar el servidor MCP (buscar_normas, texto_vigente, consulta_grafo)."""
    from scrapper_leyes.mcp_server import run
    run(transport=transport, host=host, port=port)


# ═══════════════════════════════════════════════════════════════════════════
# Test command (for Docker)
# ═══════════════════════════════════════════════════════════════════════════


@main.command()
def test() -> None:
    """Run pytest unit tests (for Docker container)."""
    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-v", "-m", "not integration"],
        cwd="/app" if sys.platform != "win32" else ".",
    )
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
