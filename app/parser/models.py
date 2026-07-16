"""
app/parser/models.py
─────────────────────
Pure-Python dataclasses representing the parsed document tree.

These are **decoupled from SQLAlchemy** — the parser returns instances of
these classes, and the ingestion service translates them into ORM models.
This separation means the parser can be tested independently of the database.

Node type discriminator values:
    "section"  – heading or body-text node
    "table"    – extracted table; body_text holds JSON rows
    "list"     – bullet or numbered list; body_text holds JSON array of strings
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ParsedNode:
    """
    A single element in the parsed document hierarchy.

    Attributes:
        node_uuid:     Stable cross-version key (numbering or title slug).
        title:         Section heading text (empty for body/table/list nodes).
        heading_level: 0 = document root; 1–6 = heading depth.
        numbering:     Dotted numbering string, e.g. "2.1.3" (empty if none).
        body_text:     Raw text for sections; JSON string for tables / lists.
        node_type:     One of "section", "table", "list".
        content_hash:  SHA-256 fingerprint for change detection.
        children:      Ordered list of child ParsedNodes.
        parent:        Reference to parent node (None for root).
    """

    node_uuid: str
    title: str
    heading_level: int
    numbering: str
    body_text: str
    node_type: str  # "section" | "table" | "list"
    content_hash: str
    children: list[ParsedNode] = field(default_factory=list)
    parent: Optional[ParsedNode] = field(default=None, repr=False, compare=False)

    def add_child(self, child: ParsedNode) -> None:
        """Attach a child node and set its parent pointer."""
        child.parent = self
        self.children.append(child)

    def flatten(self) -> list[ParsedNode]:
        """Return self and all descendants in pre-order (depth-first)."""
        result: list[ParsedNode] = [self]
        for child in self.children:
            result.extend(child.flatten())
        return result

    def __repr__(self) -> str:
        return (
            f"ParsedNode(level={self.heading_level}, "
            f"numbering={self.numbering!r}, title={self.title!r}, "
            f"type={self.node_type!r}, children={len(self.children)})"
        )
