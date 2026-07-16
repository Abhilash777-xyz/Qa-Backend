"""
app/parser/pdf_parser.py
─────────────────────────
PDF → hierarchical ParsedNode tree.

Algorithm overview
──────────────────
1.  Open the PDF with pdfplumber.
2.  For each page, extract:
    a. Tables (pdfplumber bbox-based table extraction).
    b. Text lines with per-character font metadata.
3.  Classify each text line as:
    - HEADING   – font size above a threshold OR matches a dotted-numbering
                  pattern (e.g. "3.2.1 Safety Interlock").
    - LIST_ITEM – starts with a bullet character (•, –, *) or a letter/
                  digit followed by ')' or '.'.
    - BODY      – everything else.
4.  Maintain a heading stack (similar to an HTML parser) to reconstruct the
    parent-child relationship.  Each new heading pops the stack to its level,
    then pushes itself.
5.  Body text and list items accumulate on the *current* heading node.
6.  Tables are inserted as child nodes of the current heading.

Font-size thresholds are computed **per document** by clustering all distinct
font sizes observed so that the parser adapts to documents with non-standard
heading sizes.  The clustering uses a simple gap-based approach (no ML).

Limitations (documented in APPROACH.md):
  - Multi-column layouts may interleave text from different columns.
  - Headers/footers are filtered by y-position heuristic (top/bottom 5 %).
  - Scanned/image-only PDFs are not supported (pdfplumber needs embedded text).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

import pdfplumber

from app.core.logging_config import get_logger
from app.parser.models import ParsedNode
from app.parser.utils import (
    compute_content_hash,
    extract_numbering,
    list_to_json,
    normalize_text,
    slugify,
    table_to_json,
)

logger = get_logger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

# Regex patterns for list-item detection.
_BULLET_RE = re.compile(r"^[•●▪▸\-\*]\s+")
_ORDERED_RE = re.compile(r"^\s*(?:\d+|[a-zA-Z])[.)]\s+")
_NUMBERING_RE = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+\S")


# ── Font-size clustering ───────────────────────────────────────────────────────


def _cluster_font_sizes(sizes: list[float]) -> dict[float, int]:
    """
    Assign a heading level (1–6) to each distinct font size.

    The algorithm:
      1. Deduplicate and sort sizes descending.
      2. Collapse sizes within 0.5 pt of each other into one cluster.
      3. Assign level 1 to the largest cluster, 2 to the next, etc.
      4. Sizes that map to level > 6 are considered body text (level 0).

    Returns:
        Mapping of font_size → heading_level (0 = body).
    """
    if not sizes:
        return {}

    unique = sorted(set(sizes), reverse=True)
    clusters: list[float] = []
    for s in unique:
        if not clusters or (clusters[-1] - s) > 0.5:
            clusters.append(s)

    level_map: dict[float, int] = {}
    for size in unique:
        # Find which cluster this size belongs to.
        for rank, cluster_rep in enumerate(clusters, start=1):
            if abs(size - cluster_rep) <= 0.5:
                level_map[size] = rank if rank <= 6 else 0
                break
    return level_map


# ── Line type classification ───────────────────────────────────────────────────


def _is_list_item(text: str) -> bool:
    return bool(_BULLET_RE.match(text) or _ORDERED_RE.match(text))


def _strip_list_marker(text: str) -> str:
    text = _BULLET_RE.sub("", text)
    text = _ORDERED_RE.sub("", text)
    return text.strip()


# ── Page element extraction ────────────────────────────────────────────────────


def _extract_page_elements(
    page: "pdfplumber.page.Page",
    level_map: dict[float, int],
    body_threshold: float,
) -> list[dict]:
    """
    Extract ordered elements from a single page.

    Each element is a dict with keys:
        type: "heading" | "list_item" | "body" | "table"
        text: str  (empty for tables)
        level: int (heading level; 0 for body/list)
        table: list[list[str]]  (only for type=="table")

    Tables are extracted first and their bounding boxes are recorded so that
    overlapping text lines can be suppressed.
    """
    elements: list[dict] = []
    page_height = page.height

    # ── Table extraction ────────────────────────────────────────────────────
    table_bboxes: list[tuple[float, float, float, float]] = []
    try:
        tables = page.extract_tables(
            table_settings={
                "vertical_strategy": "lines",
                "horizontal_strategy": "lines",
            }
        )
        for i, table in enumerate(tables or []):
            if not table:
                continue
            # Find the bbox via the finder.
            finder = page.find_tables(
                table_settings={
                    "vertical_strategy": "lines",
                    "horizontal_strategy": "lines",
                }
            )
            if i < len(finder):
                bbox = finder[i].bbox
                table_bboxes.append(bbox)

            elements.append({"type": "table", "text": "", "level": 0, "table": table})
    except Exception as exc:
        logger.warning("Table extraction failed on page: %s", exc)

    # ── Text line extraction ─────────────────────────────────────────────────
    try:
        words = page.extract_words(extra_attrs=["size", "fontname"])
    except Exception:
        words = []

    # Group words into lines by rounding their top-y coordinate.
    lines_by_y: dict[int, list[dict]] = {}
    for word in words:
        top = int(round(word.get("top", 0)))
        lines_by_y.setdefault(top, []).append(word)

    # Filter out header/footer area (top 5 % and bottom 5 % of page height).
    min_y = page_height * 0.05
    max_y = page_height * 0.95

    for top_y in sorted(lines_by_y.keys()):
        if top_y < min_y or top_y > max_y:
            continue  # Skip header/footer.

        line_words = lines_by_y[top_y]

        # Skip words that fall inside a table bounding box.
        in_table = False
        for bbox in table_bboxes:
            x0, y0, x1, y1 = bbox
            # If the word's midpoint is inside the table bbox, skip.
            if y0 <= top_y <= y1:
                in_table = True
                break
        if in_table:
            continue

        # Build line text and determine dominant font size.
        line_words_sorted = sorted(line_words, key=lambda w: w.get("x0", 0))
        line_text = " ".join(w.get("text", "") for w in line_words_sorted).strip()
        if not line_text:
            continue

        sizes = [w.get("size", 0.0) for w in line_words if w.get("size")]
        dominant_size = max(sizes) if sizes else 0.0
        heading_level = level_map.get(dominant_size, 0)

        # Even if the font size suggests body, a dotted-numbering prefix
        # makes it a heading.
        if heading_level == 0 and _NUMBERING_RE.match(line_text):
            heading_level = 1  # Will be refined by numbering depth later.

        if _is_list_item(line_text):
            elements.append(
                {
                    "type": "list_item",
                    "text": _strip_list_marker(line_text),
                    "level": 0,
                    "table": None,
                }
            )
        elif heading_level > 0:
            elements.append(
                {"type": "heading", "text": line_text, "level": heading_level, "table": None}
            )
        else:
            elements.append(
                {"type": "body", "text": line_text, "level": 0, "table": None}
            )

    return elements


# ── Tree builder ───────────────────────────────────────────────────────────────


def _build_tree(elements: list[dict]) -> ParsedNode:
    """
    Convert a flat list of page elements into a ParsedNode tree.

    Uses a heading stack to maintain the current ancestry.  Each new heading
    pops the stack until it finds a node of lower level, then appends itself
    as a child.  Body text, list items, and tables accumulate as children of
    the current heading.
    """
    root = ParsedNode(
        node_uuid="root",
        title="Document Root",
        heading_level=0,
        numbering="",
        body_text="",
        node_type="section",
        content_hash=compute_content_hash("Document Root", "", ""),
    )

    # Stack holds the currently open ancestor chain from root down.
    stack: list[ParsedNode] = [root]

    # Accumulators for merging consecutive body lines and list items.
    pending_body: list[str] = []
    pending_list: list[str] = []

    def _flush_body(parent: ParsedNode) -> None:
        if pending_body:
            text = normalize_text(" ".join(pending_body))
            if text:
                parent.body_text = (parent.body_text + " " + text).strip()
            pending_body.clear()

    def _flush_list(parent: ParsedNode) -> None:
        if pending_list:
            node_uuid = f"{parent.node_uuid}:list:{len(parent.children)}"
            items = list(pending_list)
            list_node = ParsedNode(
                node_uuid=node_uuid,
                title="",
                heading_level=parent.heading_level + 1,
                numbering="",
                body_text=list_to_json(items),
                node_type="list",
                content_hash=compute_content_hash("", "", list_to_json(items)),
            )
            parent.add_child(list_node)
            pending_list.clear()

    for elem in elements:
        current = stack[-1]

        if elem["type"] == "table":
            _flush_body(current)
            _flush_list(current)
            raw_table: list[list] = elem["table"]
            node_uuid = f"{current.node_uuid}:table:{len(current.children)}"
            json_body = table_to_json(raw_table)
            table_node = ParsedNode(
                node_uuid=node_uuid,
                title="",
                heading_level=current.heading_level + 1,
                numbering="",
                body_text=json_body,
                node_type="table",
                content_hash=compute_content_hash("", "", json_body),
            )
            current.add_child(table_node)

        elif elem["type"] == "heading":
            _flush_body(current)
            _flush_list(current)

            raw_text = elem["text"]
            numbering, title = extract_numbering(raw_text)

            # Determine actual level: prefer numbering depth, fall back to
            # font-size level.
            from app.parser.utils import numbering_depth
            if numbering:
                level = numbering_depth(numbering)
            else:
                level = elem["level"]
            level = max(1, min(level, 6))

            # Pop the stack until the parent has a lower level.
            while len(stack) > 1 and stack[-1].heading_level >= level:
                stack.pop()

            parent_node = stack[-1]

            # Build a stable node_uuid: prefer dotted numbering; otherwise
            # use parent uuid + slugified title.
            if numbering:
                node_uuid = numbering
            else:
                node_uuid = f"{parent_node.node_uuid}:{slugify(title)}"

            heading_node = ParsedNode(
                node_uuid=node_uuid,
                title=title,
                heading_level=level,
                numbering=numbering,
                body_text="",
                node_type="section",
                content_hash=compute_content_hash(title, numbering, ""),
            )
            parent_node.add_child(heading_node)
            stack.append(heading_node)

        elif elem["type"] == "list_item":
            _flush_body(current)
            pending_list.append(elem["text"])

        elif elem["type"] == "body":
            _flush_list(current)
            pending_body.append(elem["text"])

    # Flush any remaining accumulators.
    current = stack[-1]
    _flush_body(current)
    _flush_list(current)

    # Re-compute content hash for all nodes now that body_text is finalised.
    for node in root.flatten():
        node.content_hash = compute_content_hash(
            node.title, node.numbering, node.body_text
        )

    return root


# ── Public API ─────────────────────────────────────────────────────────────────


def parse_pdf(pdf_path: Path) -> ParsedNode:
    """
    Parse a PDF file into a hierarchical ParsedNode tree.

    Args:
        pdf_path: Absolute or relative path to the PDF file.

    Returns:
        The root ParsedNode.  Iterate ``root.children`` or call
        ``root.flatten()`` to get all nodes.

    Raises:
        FileNotFoundError: If pdf_path does not exist.
        ParserError: If pdfplumber cannot open the file.
    """
    from app.core.exceptions import ParserError

    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    logger.info("Parsing PDF: %s", pdf_path)

    try:
        with pdfplumber.open(pdf_path) as pdf:
            num_pages = len(pdf.pages)
            logger.info("PDF has %d pages", num_pages)

            # ── Pass 1: collect all font sizes across the document ──────────
            all_sizes: list[float] = []
            for page in pdf.pages:
                try:
                    words = page.extract_words(extra_attrs=["size"])
                    all_sizes.extend(
                        w.get("size", 0.0) for w in words if w.get("size", 0.0) > 0
                    )
                except Exception:
                    pass

            level_map = _cluster_font_sizes(all_sizes)
            if level_map:
                body_threshold = min(level_map.keys())
            else:
                body_threshold = 10.0

            logger.debug(
                "Font-size → heading-level map (sample): %s",
                dict(list(level_map.items())[:10]),
            )

            # ── Pass 2: extract elements page by page ────────────────────────
            all_elements: list[dict] = []
            for page_num, page in enumerate(pdf.pages, start=1):
                try:
                    page_elements = _extract_page_elements(page, level_map, body_threshold)
                    all_elements.extend(page_elements)
                    logger.debug(
                        "Page %d/%d: %d elements extracted",
                        page_num,
                        num_pages,
                        len(page_elements),
                    )
                except Exception as exc:
                    logger.warning("Error processing page %d: %s", page_num, exc)

    except Exception as exc:
        raise ParserError(f"Failed to parse PDF '{pdf_path.name}': {exc}") from exc

    if not all_elements:
        raise ParserError(
            f"No text content extracted from '{pdf_path.name}'. "
            "The file may be image-only or corrupted."
        )

    logger.info("Total elements extracted: %d — building tree", len(all_elements))
    tree = _build_tree(all_elements)

    total_nodes = len(tree.flatten())
    logger.info("Tree built: %d nodes (including root)", total_nodes)
    return tree
