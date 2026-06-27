"""Parser for SUIN-Juriscol HTML documents.

Extracts metadata, articles, affectations, and jurisprudence from the
SUIN viewDocument.asp HTML structure.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

import warnings

from bs4 import BeautifulSoup, Tag, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

from scrapper_leyes.models import (
    Affectation,
    AffectationType,
    ParsedArticle,
    ParsedNorm,
    TocEntry,
    build_canonical_id,
    normalize_affectation_type,
    normalize_article_number,
    strip_suin_ui_noise,
)


def parse_suin_html(html: str, suin_id: str) -> ParsedNorm:
    """Parse a complete SUIN viewDocument.asp HTML page.

    Args:
        html: Raw HTML content.
        suin_id: The SUIN document ID.

    Returns:
        ParsedNorm with all extracted data.
    """
    soup = BeautifulSoup(html, "html.parser")

    metadata = _extract_metadata(soup)
    toc = _extract_toc(soup, suin_id)
    modifications = _extract_affectations(soup, suin_id, section="NotasVigencia")
    jurisprudence = _extract_affectations(
        soup, suin_id, section="AfectacionesJurisp"
    )
    articles = _extract_articles(soup, metadata)

    return ParsedNorm(
        suin_id=suin_id,
        metadata=metadata,
        articles=articles,
        modifications=modifications,
        jurisprudence=jurisprudence,
        toc=toc,
    )


# ─── Metadata ───────────────────────────────────────────────────────────


def _extract_metadata(soup: BeautifulSoup) -> dict[str, str]:
    """Extract all <span field="..."> metadata from hidden div."""
    metadata: dict[str, str] = {}
    for span in soup.find_all("span", attrs={"field": True}):
        field_name = span["field"]
        text = span.get_text(strip=True)
        if text and text != "NULL":
            metadata[field_name] = text
    return metadata


# ─── Table of Contents ──────────────────────────────────────────────────


def _extract_toc(soup: BeautifulSoup, suin_id: str) -> list[TocEntry]:
    """Extract table of contents entries."""
    toc: list[TocEntry] = []
    toc_div = soup.find("div", id=f"{suin_id}cuerpo-toc")
    if not toc_div:
        return toc

    for li in toc_div.find_all("li", class_="vinieta-toc"):
        a_tag = li.find("a")
        if not a_tag:
            continue

        href = a_tag.get("href", "")
        anchor = None
        if "#ver_" in href:
            anchor = href.split("#ver_")[-1]

        span = li.find("span")
        text = span.get_text(strip=True) if span else ""
        if not text:
            continue

        # Determine level
        cls = span.get("class", []) if span else []
        if "toctext" in cls:
            level = "division"
        elif "toctextart" in cls:
            level = "articulo"
        else:
            level = "unknown"

        toc.append(TocEntry(level=level, text=text, anchor=anchor))

    return toc


# ─── Affectations ───────────────────────────────────────────────────────

_SUIN_ID_FROM_HREF = re.compile(r"id=(\d+)")
_ANCHOR_FROM_HREF = re.compile(r"#ver_(\d+)")


def _extract_affectations(
    soup: BeautifulSoup,
    suin_id: str,
    section: str,
) -> list[Affectation]:
    """Extract modification or jurisprudence affectations.

    Args:
        section: 'NotasVigencia' or 'AfectacionesJurisp'
    """
    affectations: list[Affectation] = []
    div_id = f"{suin_id}Resumen{section}"
    container = soup.find("div", id=div_id)
    if not container:
        return affectations

    # Structure: <div> → <ul class="resumenvigencias"> → <li class="resumenvigencias">
    top_ul = container.find("ul", class_="resumenvigencias")
    if not top_ul:
        return affectations

    for group_li in top_ul.find_all(
        "li", class_="resumenvigencias", recursive=False
    ):
        # Article affected is in the <b> tag
        b_tag = group_li.find("b")
        article_affected = ""
        if b_tag:
            raw_art = b_tag.get_text(strip=True).rstrip(":")
            article_affected = raw_art

        # Each nested <li class="referencia"> is one affectation
        for ref_li in group_li.find_all("li", class_="referencia"):
            # The type is in the <span> child
            span = ref_li.find("span")
            raw_type = span.get_text(strip=True) if span else ""

            # The source is in the <a> tag
            a_tag = ref_li.find("a")
            source_text = a_tag.get_text(strip=True) if a_tag else ""
            source_suin_id = None
            source_anchor = None

            if a_tag and a_tag.get("href"):
                href = a_tag["href"]
                m = _SUIN_ID_FROM_HREF.search(href)
                if m:
                    source_suin_id = m.group(1)
                m2 = _ANCHOR_FROM_HREF.search(href)
                if m2:
                    source_anchor = f"ver_{m2.group(1)}"

            # Any extra text (context in parens)
            full_text = ref_li.get_text(strip=True)
            context = None
            if "(" in full_text and ")" in full_text:
                paren_start = full_text.index("(")
                paren_end = full_text.rindex(")") + 1
                context = full_text[paren_start:paren_end]

            # Normalize the type
            normalized_type, mapped = normalize_affectation_type(raw_type)

            affectations.append(
                Affectation(
                    article_affected=article_affected,
                    raw_type=raw_type,
                    normalized_type=normalized_type,
                    mapped=mapped,
                    source_text=source_text,
                    source_suin_id=source_suin_id,
                    source_anchor=source_anchor,
                    context=context,
                )
            )

    return affectations


# ─── Articles ───────────────────────────────────────────────────────────

# Pattern for toggle divs that contain articles
_TOGGLE_ID_RE = re.compile(r"toggle_(\d+)")

# Pattern to extract article title from <em> after <strong>
_ART_TITLE_RE = re.compile(
    r"Art[ií]culo\s+(?:Transitorio\s+)?\d+[A-Za-z]?[°ºo.]?\s*\.?\s*",
    re.IGNORECASE,
)


def _extract_articles(
    soup: BeautifulSoup,
    metadata: dict[str, str],
) -> list[ParsedArticle]:
    """Extract individual articles from the document body."""
    articles: list[ParsedArticle] = []
    tipo = metadata.get("tipo", "")
    numero = metadata.get("numero", "")
    anio = metadata.get("anio", "")

    seen_art_ids: set[str] = set()
    seen_numbers: set[str] = set()
    # Map EVERY anchor's art_id → article number (incl. duplicate anchors), so
    # we can attribute toggle data (NotasOrigen) attached to a non-kept anchor.
    art_id_to_number: dict[str, str] = {}
    by_number: dict[str, ParsedArticle] = {}

    # Find all <a name="ver_XXXXX"> anchors that precede article divs
    for anchor in soup.find_all("a", attrs={"name": re.compile(r"^ver_\d+")}):
        art_id = anchor["name"].replace("ver_", "")

        # Skip duplicates (SUIN sometimes has multiple anchors)
        if art_id in seen_art_ids:
            continue

        # Find the toggle div that follows this anchor
        toggle_div = anchor.find_next(
            "div", id=_TOGGLE_ID_RE
        )
        if not toggle_div:
            continue

        # Check this toggle div is actually associated with our anchor
        # (it should be a sibling or very close)
        text_content = toggle_div.get_text(strip=False)
        if not text_content or len(text_content.strip()) < 10:
            continue

        # Extract article number from the <strong> tag (formato moderno SUIN)
        # o fallback: buscar "Artículo N" en el texto del toggle (formato viejo).
        strong = toggle_div.find("strong")
        strong_text = strong.get_text(strip=True) if strong else ""

        # Only process actual articles (not titles/divisions)
        art_num_norm = normalize_article_number(strong_text)
        if art_num_norm is None:
            # Fallback: parsear el número del texto del toggle div.
            # Formato viejo SUIN: "Artículo 1. ..." o "Artículo 1º. ..." en <p>.
            full_text = toggle_div.get_text(separator=" ", strip=True)
            m_art = re.match(
                r"^\s*Art[ií]culo\s+(?:Transitorio\s+)?(\d+|[IVXLCDM]+)",
                full_text,
                re.IGNORECASE,
            )
            if m_art:
                art_num_raw = m_art.group(1)
                if not art_num_raw.isdigit():
                    from scrapper_leyes.models import _roman_to_int
                    ri = _roman_to_int(art_num_raw)
                    art_num_norm = str(ri) if ri is not None else None
                else:
                    art_num_norm = art_num_raw
                if art_num_norm is None:
                    continue
            else:
                continue

        # Record the art_id → number mapping for EVERY anchor (kept or not).
        art_id_to_number[art_id] = art_num_norm

        # Dedup by article number: SUIN anchors some articles twice (original +
        # modified-version anchor) with identical text. Keep the first.
        if art_num_norm in seen_numbers:
            continue
        seen_numbers.add(art_num_norm)

        seen_art_ids.add(art_id)

        # Extract title from <em> following the article number
        em = toggle_div.find("em")
        title = None
        if em:
            em_text = em.get_text(strip=True)
            # Clean up: remove the article number prefix if present
            if em_text and not em_text[0].isdigit():
                title = em_text.rstrip(".")

        # Get full text of the article
        article_text = _clean_article_text(toggle_div)

        # Build canonical ID
        canonical_id = build_canonical_id(tipo, numero, anio, art=art_num_norm)

        # Extract vigencia notes + previous versions for this article
        notes = _extract_article_notes(soup, art_id)
        prev_versions = _extract_previous_versions(soup, art_id)

        article = ParsedArticle(
            art_id=art_id,
            number=strong_text,
            number_normalized=art_num_norm,
            title=title,
            text=article_text,
            canonical_id=canonical_id,
            notes=notes,
            previous_versions=prev_versions,
        )
        articles.append(article)
        by_number[art_num_norm] = article

    # Second pass: attribute OUTGOING affectations (NotasOrigen). The toggle is
    # often attached to a non-kept (modified-version) anchor of the article, so
    # we resolve it by number rather than by the kept article's art_id.
    for div in soup.find_all("div", id=re.compile(r"NotasOrigen\d+")):
        m = re.search(r"NotasOrigen(\d+)", div.get("id", ""))
        if not m:
            continue
        number = art_id_to_number.get(m.group(1))
        article = by_number.get(number) if number else None
        if article is None:
            continue
        seen = {f"{a['normalized_type']}|{a['target_text']}" for a in article.affects}
        for aff in _extract_origin_affectations_from_div(div):
            key = f"{aff['normalized_type']}|{aff['target_text']}"
            if key not in seen:
                seen.add(key)
                article.affects.append(aff)

    return articles


def _clean_article_text(div: Tag) -> str:
    """Extract clean text from an article div, removing nested metadata."""
    # Clone to avoid modifying the tree
    text_parts: list[str] = []
    for p in div.find_all("p"):
        # Skip hidden divs and metadata
        parent = p.parent
        if parent and parent.get("style", "").startswith("display: none"):
            continue
        t = p.get_text(strip=True)
        if t:
            text_parts.append(t)
    raw = "\n\n".join(text_parts) if text_parts else div.get_text(strip=True)
    return strip_suin_ui_noise(raw)


def _extract_article_notes(soup: BeautifulSoup, art_id: str) -> list[str]:
    """Extract vigencia notes for a specific article."""
    notes: list[str] = []
    # Notes are in divs like: {suin_id}NotasDestino{art_id}
    for div in soup.find_all("div", id=re.compile(rf"NotasDestino{art_id}$")):
        for li in div.find_all("li", class_="referencia"):
            notes.append(li.get_text(strip=True))
    return notes


def _extract_origin_affectations_from_div(div: Tag) -> list[dict[str, Any]]:
    """Parse OUTGOING affectations from a ``NotasOrigen`` toggle div — what an
    article derogates/modifies of other norms.

    Same structure as incoming affectations: <li class="referencia"> with a
    <span> type and an <a> linking the affected target norm.
    """
    out: list[dict[str, Any]] = []
    for li in div.find_all("li", class_="referencia"):
        span = li.find("span")
        raw_type = span.get_text(strip=True) if span else ""
        a_tag = li.find("a")
        target_text = a_tag.get_text(strip=True) if a_tag else ""
        target_suin_id = None
        target_anchor = None
        if a_tag and a_tag.get("href"):
            href = a_tag["href"]
            m = _SUIN_ID_FROM_HREF.search(href)
            if m:
                target_suin_id = m.group(1)
            m2 = _ANCHOR_FROM_HREF.search(href)
            if m2:
                target_anchor = f"ver_{m2.group(1)}"

        full = li.get_text(strip=True)
        context = None
        if "(" in full and ")" in full:
            context = full[full.index("(") : full.rindex(")") + 1]

        normalized_type, mapped = normalize_affectation_type(raw_type)
        out.append({
            "normalized_type": normalized_type.value,
            "raw_type": raw_type,
            "mapped": mapped,
            "target_text": target_text,
            "target_suin_id": target_suin_id,
            "target_anchor": target_anchor,
            "context": context,
        })
    return out


def _extract_previous_versions(
    soup: BeautifulSoup, art_id: str
) -> list[dict[str, str]]:
    """Extract previous versions (legislación anterior) for an article."""
    versions: list[dict[str, str]] = []
    div = soup.find("div", id=f"div{art_id}leg_ant")
    if not div:
        return versions

    for version_box in div.find_all(
        "div", style=re.compile(r"background-color.*#E0E0E0")
    ):
        text = version_box.get_text(strip=True)
        # Try to extract date range
        date_div = version_box.find("font", color="#336600")
        date_range = date_div.get_text(strip=True) if date_div else ""

        # Get the article text without the date info
        content_parts = []
        for p in version_box.find_all("p"):
            t = p.get_text(strip=True)
            if t and t != date_range:
                content_parts.append(t)

        versions.append(
            {
                "text": "\n".join(content_parts),
                "date_range": date_range,
            }
        )

    return versions


def detect_needs_ocr(html: str) -> bool:
    """Detect if a SUIN page is an image scan (needs OCR, not HTML text).

    Scanned norms typically have <img> tags for the content instead of
    text in <p> tags, or have very little text relative to the page size.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Check for the main content area
    body = soup.find("body", class_="documento_cms")
    if not body:
        return False

    # Count text-bearing paragraphs vs images in the content area
    paragraphs = body.find_all("p")
    text_len = sum(len(p.get_text(strip=True)) for p in paragraphs)

    # If the page is very large but has very little text, it's likely a scan
    if len(html) > 10000 and text_len < 500:
        return True

    # Check for embedded images that look like page scans
    scan_imgs = body.find_all("img", src=re.compile(r"(\.tif|\.jpg|\.png|scan)", re.I))
    if len(scan_imgs) > 2 and text_len < 1000:
        return True

    return False
