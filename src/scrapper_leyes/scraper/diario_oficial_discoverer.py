"""Discoverer para las ediciones del Diario Oficial (Imprenta Nacional).

El Diario Oficial es la fuente **PRIMARIA de la fecha de promulgación/publicación**
de leyes, decretos y resoluciones — el dato valioso para la vigencia temporal.

El buscador vive en una app **JSF / PrimeFaces sobre GlassFish** (no hay API JSON):

    https://svrpubindc.imprenta.gov.co/diario/   (resuelve a index.xhtml)

Una sola GET de ``/diario/`` ya renderiza, server-side, las ~10 ediciones más
recientes en la tabla PrimeFaces ``dtbDiariosOficiales`` (con
``dtbDiariosOficiales_rppDD`` se sube el tamaño de página a 50). Cada edición es
un ``<tr data-rk="53.526">`` con tres ``<label>``:

  * ``...:N:numeroDiario`` → número de edición (formato con punto de miles, "53.526")
  * ``...:N:tipoEdicion``  → Ordinaria | Extraordinaria | Especial | Oficio Tributario
  * ``...:N:fechaDiario``  → fecha de publicación ``dd/mm/aaaa``  ← dato valioso

Las ediciones se enumeran por un **entero monótono** (~1 por día hábil; tope
≈ 53.526 al 2026-06-18) → ``numero`` de la CatalogSeed = número de edición.

**Caveat de descarga (verificado en vivo 2026-06-19):** el PDF NO tiene URL
estática enumerable. El botón "Ver Diario" es un submit JSF
(``dtbDiariosOficiales:N:j_idt38``) que hace POST del formulario (con el índice de
fila, ``javax.faces.ViewState`` y la cookie ``JSESSIONID``) y navega a
``/diario/view/diarioficial/detallesPdf.xhtml``, desde donde el PDF se sirve
*session-bound* (StreamedContent). Para descargarlo el scraper de texto debe
**replicar el flujo JSF** (GET → extraer ViewState → POST → seguir a detallesPdf),
no construir una URL. Por eso ``source_url`` apunta a la página de detalle, no a
un ``.pdf`` directo, y se deja constancia en ``extra``.

Estado: andamiaje/parcial. El parser de la tabla de resultados está implementado
contra el HTML real capturado y es testeable offline. La parte de red hace la GET
inicial y parsea la primera página; la búsqueda por rango (POST JSF con ViewState)
queda como TODO porque requiere cookie jar + token, fuera del alcance del recon.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date
from html import unescape
from typing import Any, Iterator
from urllib.parse import urljoin

import httpx

from scrapper_leyes.scraper.base import BaseDiscoverer, CatalogSeed

logger = logging.getLogger(__name__)

_UA = "ScrapperLeyes/1.0 (investigacion academica)"

# Fila de edición en la tabla PrimeFaces: <tr ... data-rk="53.526"> ... </tr>.
_ROW_RE = re.compile(
    r'<tr\b[^>]*\bdata-rk="([^"]+)"[^>]*>(.*?)</tr>',
    re.IGNORECASE | re.DOTALL,
)
# Labels por sufijo de id de PrimeFaces (numeroDiario / tipoEdicion / fechaDiario).
_LABEL_RE = re.compile(
    r'<label[^>]*id="[^"]*:(numeroDiario|tipoEdicion|fechaDiario)"[^>]*>(.*?)</label>',
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")
_FECHA_RE = re.compile(r"(\d{2})/(\d{2})/(\d{4})")

# Tipo de edición (texto PrimeFaces) → etiqueta canónica para extra.
_TIPO_EDICION = {
    "ordinaria": "ORDINARIA",
    "extraordinaria": "EXTRAORDINARIA",
    "especial": "ESPECIAL",
    "oficio tributario": "OFICIO TRIBUTARIO",
}

# Página de detalle (session-bound) desde donde se sirve el PDF de la edición.
DETALLE_PATH = "/diario/view/diarioficial/detallesPdf.xhtml"


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", unescape(_TAG_RE.sub(" ", s))).strip()


class DiarioOficialDiscoverer(BaseDiscoverer):
    """Lista ediciones del Diario Oficial desde el buscador JSF → CatalogSeeds.

    Args:
        base: host del buscador (svrpubindc.imprenta.gov.co).
    """

    SOURCE = "diario_oficial"
    BASE = "https://svrpubindc.imprenta.gov.co"
    INDEX_PATH = "/diario/index.xhtml"

    def __init__(self, base: str | None = None):
        self.base = (base or self.BASE).rstrip("/")

    # ── parsing (puro, testeable offline) ─────────────────────────────────
    def _parse_resultados(self, html: str) -> list[CatalogSeed]:
        """Parsea la tabla de resultados PrimeFaces → lista de CatalogSeed.

        Método puro: recibe el HTML (cuerpo de ``dtbDiariosOficiales_data`` o la
        página completa) y devuelve un seed por fila ``data-rk``.
        """
        seeds: list[CatalogSeed] = []
        for rk, body in _ROW_RE.findall(html):
            labels = {k.lower(): _clean(v) for k, v in _LABEL_RE.findall(body)}
            num_raw = labels.get("numerodiario") or rk
            seed = self._seed_from_row(
                numero_diario=num_raw,
                tipo_edicion=labels.get("tipoedicion", ""),
                fecha=labels.get("fechadiario", ""),
            )
            if seed:
                seeds.append(seed)
        return seeds

    def _seed_from_row(
        self, *, numero_diario: str, tipo_edicion: str, fecha: str
    ) -> CatalogSeed | None:
        # "53.526" → "53526" (quitar punto de miles, conservar como número de edición).
        numero = numero_diario.replace(".", "").replace(" ", "").strip()
        if not numero.isdigit():
            return None

        anio: str | None = None
        fecha_iso: str | None = None
        fm = _FECHA_RE.search(fecha or "")
        if fm:
            dd, mm, yyyy = fm.group(1), fm.group(2), fm.group(3)
            anio = yyyy
            fecha_iso = f"{yyyy}-{mm}-{dd}"  # fecha de promulgación/publicación

        tipo_ed = _TIPO_EDICION.get(
            (tipo_edicion or "").strip().lower(), (tipo_edicion or "").strip().upper() or None
        )

        # La unidad direccionable es la EDICIÓN. source_url → página de detalle
        # (session-bound); el PDF se obtiene replicando el flujo JSF.
        detalle = urljoin(self.base + "/", DETALLE_PATH.lstrip("/"))

        extra: dict[str, Any] = {
            "edicion": numero_diario.strip(),
            "unidad": "EDICION_DIARIO_OFICIAL",
            "descarga": "jsf_session_bound",  # no enumerable: POST ViewState+JSESSIONID
        }
        if fecha_iso:
            extra["fecha"] = fecha_iso
        if tipo_ed:
            extra["tipo_edicion"] = tipo_ed

        return CatalogSeed(
            tipo="DIARIO OFICIAL",
            numero=numero,
            anio=anio,
            source=self.SOURCE,
            source_url=detalle,
            external_id=numero,
            canonical_id=None,  # una edición no es una norma → sin canonical_id
            extra=extra,
        )

    # ── red (async) ───────────────────────────────────────────────────────
    async def _crawl(self) -> dict[str, CatalogSeed]:
        """GET inicial del buscador → parsea las ediciones recientes renderizadas.

        La búsqueda por rango de fechas/número exige un POST JSF con
        ``javax.faces.ViewState`` + cookie ``JSESSIONID`` (stateful); no se
        implementa aquí. Esta vía cubre el polling de las ediciones más recientes
        (la GET inicial ya trae las ~10 últimas server-rendered).
        """
        found: dict[str, CatalogSeed] = {}
        url = urljoin(self.base, self.INDEX_PATH)
        async with httpx.AsyncClient(
            headers={"User-Agent": _UA},
            timeout=40.0,
            follow_redirects=True,
            verify=False,
        ) as client:
            try:
                r = await client.get(url)
                if r.status_code == 200:
                    for s in self._parse_resultados(r.text):
                        found.setdefault(s.numero, s)
                else:
                    logger.warning("[diario_oficial] GET %s → HTTP %d", url, r.status_code)
            except Exception as e:  # noqa: BLE001
                logger.warning("[diario_oficial] GET %s falló: %s", url, e)

        logger.info(
            "[diario_oficial] %d ediciones descubiertas (GET inicial; "
            "búsqueda por rango requiere POST JSF, pendiente)",
            len(found),
        )
        return found

    def discover(
        self,
        *,
        desde: date | None = None,
        hasta: date | None = None,
        filtro: dict[str, Any] | None = None,
    ) -> Iterator[CatalogSeed]:
        found = asyncio.run(self._crawl())
        for seed in found.values():
            if desde and seed.anio and seed.anio.isdigit() and int(seed.anio) < desde.year:
                continue
            if hasta and seed.anio and seed.anio.isdigit() and int(seed.anio) > hasta.year:
                continue
            yield seed
