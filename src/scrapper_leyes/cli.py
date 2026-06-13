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
@click.pass_context
def sync(ctx: click.Context, tipo: str | None, limit: int | None) -> None:
    """Sincronizar catálogo desde datos.gov.co."""
    settings, db, _ = _get_deps(ctx.obj.get("data_dir"))

    from scrapper_leyes.catalog.socrata_client import fetch_catalog_count, sync_catalog

    # Show remote count first
    try:
        remote_count = fetch_catalog_count(settings, tipo=tipo)
        console.print(
            f"[bold]Registros en Socrata{f' (tipo={tipo})' if tipo else ''}: "
            f"{remote_count:,}[/bold]"
        )
    except Exception as e:
        console.print(f"[yellow]No se pudo obtener conteo remoto: {e}[/yellow]")

    total = sync_catalog(settings, db, tipo=tipo, limit=limit)
    local_count = db.get_catalog_count(tipo=tipo)

    console.print(f"\n[green]✓ Sincronizados {total:,} registros[/green]")
    console.print(f"[bold]Total en catálogo local: {local_count:,}[/bold]")

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
@click.pass_context
def run(ctx: click.Context, tipo: str | None, limit: int | None, source: str) -> None:
    """Ejecutar scraping de normas pendientes."""
    settings, db, cache = _get_deps(ctx.obj.get("data_dir"))

    from scrapper_leyes.scraper.factory import ScraperFactory
    import asyncio

    factory = ScraperFactory(settings, db, cache)
    indexer = factory.get_indexer(source)
    scraper = factory.get_scraper(source)

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

    html = raw.decode("utf-8", errors="replace")
    parsed_norm = parse_suin_html(html, suin_id)
    cache.store_parsed("suin", tipo, suin_id, parsed_norm.to_dict())

    console.print(f"[green]✓ Parseado: {len(parsed_norm.articles)} artículos, "
                  f"{len(parsed_norm.modifications)} modificaciones, "
                  f"{len(parsed_norm.jurisprudence)} sentencias[/green]")

    db.close()


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
