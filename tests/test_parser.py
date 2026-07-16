"""
tests/test_parser.py
─────────────────────
Unit tests for the PDF parser and tree builder.

These tests use the ParsedNode fixtures from conftest.py and exercise the
pure-Python parser utilities without touching the database.
"""

from __future__ import annotations

import json

import pytest

from app.parser.models import ParsedNode
from app.parser.utils import (
    compute_content_hash,
    extract_numbering,
    normalize_text,
    slugify,
    table_to_json,
    list_to_json,
    numbering_depth,
)
from tests.conftest import make_node


# ── normalize_text ─────────────────────────────────────────────────────────────


class TestNormalizeText:
    def test_collapses_whitespace(self):
        assert normalize_text("hello   world") == "hello world"

    def test_strips_edges(self):
        assert normalize_text("  hello  ") == "hello"

    def test_handles_tabs_and_newlines(self):
        assert normalize_text("a\t\nb") == "a b"

    def test_empty_string(self):
        assert normalize_text("") == ""


# ── extract_numbering ─────────────────────────────────────────────────────────


class TestExtractNumbering:
    def test_simple(self):
        n, title = extract_numbering("1 Introduction")
        assert n == "1"
        assert title == "Introduction"

    def test_two_level(self):
        n, title = extract_numbering("2.1 Safety Requirements")
        assert n == "2.1"
        assert title == "Safety Requirements"

    def test_three_level(self):
        n, title = extract_numbering("3.4.2 Electrical Interlocks")
        assert n == "3.4.2"
        assert title == "Electrical Interlocks"

    def test_no_numbering(self):
        n, title = extract_numbering("Introduction")
        assert n == ""
        assert title == "Introduction"

    def test_trailing_dot(self):
        n, title = extract_numbering("2.1. Safety")
        assert n == "2.1"
        assert title == "Safety"


# ── numbering_depth ────────────────────────────────────────────────────────────


class TestNumberingDepth:
    def test_empty(self):
        assert numbering_depth("") == 0

    def test_level1(self):
        assert numbering_depth("1") == 1

    def test_level2(self):
        assert numbering_depth("2.1") == 2

    def test_level3(self):
        assert numbering_depth("3.4.2") == 3


# ── slugify ────────────────────────────────────────────────────────────────────


class TestSlugify:
    def test_basic(self):
        assert slugify("Hello World") == "hello-world"

    def test_special_chars_removed(self):
        slug = slugify("Safety (Critical) – Requirements!")
        assert "[" not in slug
        assert "(" not in slug

    def test_max_length(self):
        long_text = "word " * 30
        assert len(slugify(long_text, max_len=20)) <= 20


# ── content hash ──────────────────────────────────────────────────────────────


class TestComputeContentHash:
    def test_deterministic(self):
        h1 = compute_content_hash("Title", "1.2", "Body text")
        h2 = compute_content_hash("Title", "1.2", "Body text")
        assert h1 == h2

    def test_different_body_different_hash(self):
        h1 = compute_content_hash("Title", "1.2", "Body text A")
        h2 = compute_content_hash("Title", "1.2", "Body text B")
        assert h1 != h2

    def test_whitespace_normalised(self):
        h1 = compute_content_hash("Title", "1", "Body  text")
        h2 = compute_content_hash("Title", "1", "Body text")
        assert h1 == h2

    def test_returns_64_hex_chars(self):
        h = compute_content_hash("A", "1", "B")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)


# ── table_to_json / list_to_json ──────────────────────────────────────────────


class TestSerialisation:
    def test_table_roundtrip(self):
        rows = [["Header A", "Header B"], ["Val 1", "Val 2"], [None, "Val 3"]]
        j = table_to_json(rows)
        parsed = json.loads(j)
        assert parsed[0] == ["Header A", "Header B"]
        assert parsed[2][0] == ""  # None → ""

    def test_list_roundtrip(self):
        items = ["item one", "item two", "item three"]
        j = list_to_json(items)
        assert json.loads(j) == items


# ── ParsedNode tree ───────────────────────────────────────────────────────────


class TestParsedNodeTree:
    def test_parent_set_on_add_child(self, sample_tree):
        for child in sample_tree.children:
            assert child.parent is sample_tree

    def test_flatten_returns_all_nodes(self, sample_tree):
        flat = sample_tree.flatten()
        # Root + 3 top-level + 1.1 + 2.1 + 2.1:list + 2.2 + 2.2:table
        assert len(flat) >= 8

    def test_hierarchy_depth(self, sample_tree):
        # Safety Requirements → General Safety → list node
        safety = next(n for n in sample_tree.children if n.numbering == "2")
        general = next(n for n in safety.children if n.numbering == "2.1")
        assert general.heading_level == 2
        assert len(general.children) == 1
        assert general.children[0].node_type == "list"

    def test_table_node_type(self, sample_tree):
        safety = next(n for n in sample_tree.children if n.numbering == "2")
        electrical = next(n for n in safety.children if n.numbering == "2.2")
        assert electrical.children[0].node_type == "table"

    def test_node_uuid_uniqueness(self, sample_tree):
        flat = sample_tree.flatten()
        uuids = [n.node_uuid for n in flat]
        assert len(uuids) == len(set(uuids)), "node_uuid values must be unique"

    def test_content_hash_set_on_all_nodes(self, sample_tree):
        for node in sample_tree.flatten():
            assert node.content_hash, f"Node {node.node_uuid} has empty content_hash"

    def test_deep_nesting(self):
        """ParsedNode should handle 6 levels of nesting without errors."""
        root = make_node("root", "Root", heading_level=0)
        current = root
        for level in range(1, 7):
            child = make_node(
                f"node-{level}",
                f"Level {level}",
                heading_level=level,
                numbering=".".join(str(i) for i in range(1, level + 1)),
            )
            current.add_child(child)
            current = child

        flat = root.flatten()
        assert len(flat) == 7  # root + 6 levels

    def test_duplicate_title_different_uuid(self):
        """Two sections with the same title must have different node_uuids."""
        root = make_node("root", "Root", heading_level=0)
        n1 = make_node("1", "Safety", heading_level=1, numbering="1")
        n2 = make_node("2", "Safety", heading_level=1, numbering="2")
        root.add_child(n1)
        root.add_child(n2)

        flat = root.flatten()
        uuids = [n.node_uuid for n in flat]
        assert len(uuids) == len(set(uuids))
