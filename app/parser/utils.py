"""
app/parser/utils.py
────────────────────
Stateless helper functions used by the PDF parser and the version service.

All functions are pure (no side-effects, no I/O) so they are trivial to unit
test in isolation.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata


# ── Text normalisation ────────────────────────────────────────────────────────


def normalize_text(text: str) -> str:
    """
    Collapse whitespace and strip leading/trailing blanks.
    Normalises unicode to NFC so that "é" and "e\\u0301" hash identically.
    """
    text = unicodedata.normalize("NFC", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def slugify(text: str, max_len: int = 64) -> str:
    """
    Convert arbitrary text to a stable, URL-safe slug.

    Used to build ``node_uuid`` for nodes that have no dotted numbering.
    Example: "3.2 Safety Requirements" → "3-2-safety-requirements"
    """
    text = normalize_text(text).lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text[:max_len].strip("-")


# ── Numbering detection ───────────────────────────────────────────────────────

_NUMBERING_PATTERN = re.compile(
    r"^(\d+(?:\.\d+)*)"   # One or more dot-separated digit groups.
    r"\.?\s+"             # Optional trailing dot, then whitespace.
)


def extract_numbering(text: str) -> tuple[str, str]:
    """
    Extract a dotted-numbering prefix from a heading string.

    Returns:
        (numbering, remainder_title)
        e.g. ("2.1.3", "Safety Interlocks") from "2.1.3 Safety Interlocks"
        or   ("",      "Introduction")       from "Introduction"
    """
    match = _NUMBERING_PATTERN.match(text.strip())
    if match:
        numbering = match.group(1)
        remainder = text[match.end():].strip()
        return numbering, remainder
    return "", text.strip()


def numbering_depth(numbering: str) -> int:
    """
    Return the depth implied by a dotted numbering string.

    Examples:
        "1"     → 1
        "2.1"   → 2
        "3.4.2" → 3
        ""      → 0
    """
    if not numbering:
        return 0
    return len(numbering.split("."))


# ── Hashing ───────────────────────────────────────────────────────────────────


def compute_content_hash(title: str, numbering: str, body_text: str) -> str:
    """
    Compute a deterministic SHA-256 fingerprint for a node's content.

    The hash is over the **normalised** concatenation of title + numbering +
    body_text so that trivial whitespace changes do not produce a new hash,
    but any actual content change does.

    Returns:
        A 64-character hex string.
    """
    canonical = normalize_text(title) + "|" + numbering + "|" + normalize_text(body_text)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_file_hash(file_bytes: bytes) -> str:
    """SHA-256 fingerprint of a raw file (used for DocumentVersion.file_hash)."""
    return hashlib.sha256(file_bytes).hexdigest()


# ── Table serialisation ───────────────────────────────────────────────────────


def table_to_json(rows: list[list[str | None]]) -> str:
    """
    Serialise a table (list of rows, each a list of cell strings) to a compact
    JSON string suitable for storage in ``body_text``.

    None cells are converted to empty strings for consistency.
    """
    cleaned = [[str(cell) if cell is not None else "" for cell in row] for row in rows]
    return json.dumps(cleaned, ensure_ascii=False)


def json_to_table(json_str: str) -> list[list[str]]:
    """Deserialise a JSON table back to a list-of-rows."""
    return json.loads(json_str)


# ── List serialisation ────────────────────────────────────────────────────────


def list_to_json(items: list[str]) -> str:
    """Serialise a list of strings to JSON for storage in ``body_text``."""
    return json.dumps(items, ensure_ascii=False)


def json_to_list(json_str: str) -> list[str]:
    """Deserialise a JSON list back to a Python list."""
    return json.loads(json_str)


# ── Heading level inference ───────────────────────────────────────────────────


def infer_heading_level_from_numbering(numbering: str) -> int:
    """
    When font-size information is unavailable, infer heading level from the
    depth of the dotted numbering:  "" → 0, "1" → 1, "1.2" → 2, etc.
    """
    return numbering_depth(numbering)
