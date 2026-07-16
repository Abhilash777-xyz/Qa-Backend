"""
tests/test_llm.py
──────────────────
Tests for the LLM service.

All OpenRouter HTTP calls are mocked so these tests run without a real API key
or network connection.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from app.core.exceptions import LLMNotConfiguredError, LLMResponseParseError
from app.services.llm_service import (
    _parse_llm_response,
    _reconstruct_text,
)
from app.models.document import DocumentNode
from app.parser.utils import compute_content_hash


# ── _parse_llm_response ───────────────────────────────────────────────────────


class TestParseLLMResponse:
    def _valid_cases_json(self, count: int = 3) -> str:
        cases = [
            {
                "title": f"Test Case {i}",
                "objective": f"Verify that feature {i} works correctly.",
                "preconditions": ["System is powered on", "Test fixture connected"],
                "steps": ["Step 1: Do something", "Step 2: Check result"],
                "expected_result": "The expected output matches specification.",
                "priority": "High",
                "requirement_reference": f"REQ-{i:03d}",
            }
            for i in range(1, count + 1)
        ]
        return json.dumps(cases)

    def test_valid_response_parses(self):
        raw = self._valid_cases_json(3)
        cases = _parse_llm_response(raw)
        assert len(cases) == 3
        assert cases[0].title == "Test Case 1"
        assert cases[0].priority == "High"

    def test_valid_with_markdown_fences(self):
        raw = "```json\n" + self._valid_cases_json(3) + "\n```"
        cases = _parse_llm_response(raw)
        assert len(cases) == 3

    def test_five_test_cases(self):
        raw = self._valid_cases_json(5)
        cases = _parse_llm_response(raw)
        assert len(cases) == 5

    def test_missing_field_raises(self):
        cases = [
            {
                "title": "TC1",
                # "objective" is missing
                "preconditions": [],
                "steps": ["Step 1"],
                "expected_result": "Pass",
                "priority": "Medium",
                "requirement_reference": "REQ-001",
            }
        ]
        with pytest.raises((ValueError, Exception)):
            _parse_llm_response(json.dumps(cases))

    def test_invalid_priority_raises(self):
        cases = [
            {
                "title": "TC1",
                "objective": "Some objective",
                "preconditions": [],
                "steps": ["Step 1"],
                "expected_result": "Pass",
                "priority": "CRITICAL",  # Invalid — must be High/Medium/Low.
                "requirement_reference": "REQ-001",
            }
        ]
        with pytest.raises(Exception):
            _parse_llm_response(json.dumps(cases))

    def test_no_json_array_raises(self):
        with pytest.raises(ValueError):
            _parse_llm_response("This is not JSON at all.")

    def test_empty_array_raises(self):
        with pytest.raises(ValueError):
            _parse_llm_response("[]")

    def test_json_object_not_array_raises(self):
        with pytest.raises(ValueError):
            _parse_llm_response('{"title": "TC1"}')


# ── _reconstruct_text ─────────────────────────────────────────────────────────


class TestReconstructText:
    def _make_orm_node(self, title, num, body, node_type="section"):
        n = MagicMock(spec=DocumentNode)
        n.id = 1
        n.title = title
        n.numbering = num
        n.body_text = body
        n.node_type = node_type
        n.heading_level = 1
        n.content_hash = compute_content_hash(title, num, body)
        return n

    def test_section_text_included(self):
        node = self._make_orm_node("Safety", "2.1", "All safety interlocks must be tested.")
        text = _reconstruct_text([node])
        assert "Safety" in text
        assert "All safety interlocks" in text

    def test_list_items_rendered(self):
        node = self._make_orm_node("", "", '["Item A", "Item B"]', node_type="list")
        text = _reconstruct_text([node])
        assert "Item A" in text
        assert "Item B" in text

    def test_table_rendered_as_pipe_separated(self):
        node = self._make_orm_node(
            "", "", '[["Header", "Value"], ["Row1", "24V"]]', node_type="table"
        )
        text = _reconstruct_text([node])
        assert "Header" in text
        assert "24V" in text
        assert "|" in text

    def test_nodes_sorted_by_numbering(self):
        n1 = self._make_orm_node("First", "1", "First body")
        n2 = self._make_orm_node("Second", "2", "Second body")
        n3 = self._make_orm_node("Third", "3", "Third body")
        text = _reconstruct_text([n3, n1, n2])  # Pass in wrong order.
        pos1 = text.index("First")
        pos2 = text.index("Second")
        pos3 = text.index("Third")
        assert pos1 < pos2 < pos3


# ── LLM not configured ─────────────────────────────────────────────────────────


class TestLLMNotConfigured:
    def test_generate_raises_when_no_key(self):
        """generate_test_cases should raise LLMNotConfiguredError if key is absent."""
        from app.services.llm_service import generate_test_cases
        from unittest.mock import MagicMock

        db = MagicMock()
        with patch("app.services.llm_service.settings") as mock_settings:
            mock_settings.llm_configured = False
            with pytest.raises(LLMNotConfiguredError):
                generate_test_cases(db, selection_id=1)
