"""
app/services/llm_service.py
────────────────────────────
LLM test-case generation pipeline:

  1. Load the selection's nodes from the database.
  2. Reconstruct a coherent text passage from the nodes.
  3. Build a versioned prompt instructing the LLM to produce 3–5 QA test
     cases in a specific JSON schema.
  4. Call OpenRouter via httpx (synchronous — FastAPI runs it in a thread pool
     via ``run_in_executor`` if the route is async, but the service itself
     is synchronous for simplicity and testability).
  5. Parse and validate the JSON response against the ``TestCase`` Pydantic
     model.  If validation fails, retry once.
  6. Persist the output to a JSON file and create a
     ``GeneratedTestCaseMetadata`` database row.
  7. Return the structured test cases.

Prompt version is stored so that future prompt changes don't silently alter
what was used for earlier generations.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import (
    LLMError,
    LLMNotConfiguredError,
    LLMResponseParseError,
    NotFoundError,
)
from app.core.logging_config import get_logger
from app.models.document import DocumentNode
from app.models.generation import GeneratedTestCaseMetadata
from app.models.selection import Selection
from app.schemas.generation import (
    GeneratedTestCaseOutput,
    GenerationResponse,
    TestCase,
)
from app.services.selection_service import get_selection_nodes

logger = get_logger(__name__)

# ── Prompt templates ───────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are a senior QA engineer specialising in hardware and embedded systems.
Your task is to generate structured QA test cases from the provided technical
document excerpt.

IMPORTANT: You MUST respond with ONLY a valid JSON array. No markdown fences, no
explanation, no preamble. Start your response with [ and end with ].

Each test case object MUST have exactly these keys:
{
  "title": "string",
  "objective": "string",
  "preconditions": ["string", ...],
  "steps": ["string", ...],
  "expected_result": "string",
  "priority": "High" | "Medium" | "Low",
  "requirement_reference": "string"
}

Generate between 3 and 5 test cases. Each test case must be specific,
actionable, and directly traceable to the provided content.
"""

_USER_PROMPT_V1 = """\
Generate 3–5 QA test cases for the following technical document sections.
Return ONLY a JSON array as described in your instructions.

--- DOCUMENT CONTENT START ---
{content}
--- DOCUMENT CONTENT END ---
"""


# ── Public API ─────────────────────────────────────────────────────────────────


def generate_test_cases(
    db: Session,
    selection_id: int,
) -> GenerationResponse:
    """
    Generate QA test cases for a named selection of document nodes.

    Args:
        db:           Active session.
        selection_id: ID of the Selection to generate test cases for.

    Returns:
        GenerationResponse with structured test cases and metadata.

    Raises:
        NotFoundError:          If the selection does not exist.
        LLMNotConfiguredError:  If no API key is configured.
        LLMResponseParseError:  If the LLM returns unparseable output after retry.
        LLMError:               On network / HTTP errors.
    """
    if not settings.llm_configured:
        raise LLMNotConfiguredError(
            "OpenRouter API key is not configured. "
            "Set OPENROUTER_API_KEY in your .env file."
        )

    # ── Load selection and nodes ─────────────────────────────────────────────
    selection = db.get(Selection, selection_id)
    if selection is None:
        raise NotFoundError(f"Selection {selection_id} not found.")

    nodes = get_selection_nodes(db, selection_id)
    if not nodes:
        raise NotFoundError(f"Selection {selection_id} has no nodes.")

    # ── Reconstruct document text ────────────────────────────────────────────
    content = _reconstruct_text(nodes)
    logger.debug("Reconstructed text length: %d chars", len(content))

    # ── Call LLM (with one retry) ────────────────────────────────────────────
    test_cases = _call_llm_with_retry(content)

    # ── Persist output to disk ───────────────────────────────────────────────
    node_ids = [n.id for n in nodes]
    content_hashes = [n.content_hash for n in nodes]
    now = datetime.now(timezone.utc)

    output = GeneratedTestCaseOutput(
        selection_id=selection_id,
        document_version_id=selection.document_version_id,
        node_ids=node_ids,
        test_cases=test_cases,
        generated_at=now.isoformat(),
        llm_provider="openrouter",
        llm_model=settings.openrouter_model,
        prompt_version=settings.prompt_version,
    )

    output_path = _save_output(output, selection_id)

    # ── Create metadata row ──────────────────────────────────────────────────
    metadata = GeneratedTestCaseMetadata(
        selection_id=selection_id,
        document_version_id=selection.document_version_id,
        node_ids_json=json.dumps(node_ids),
        content_hashes_json=json.dumps(content_hashes),
        llm_provider="openrouter",
        llm_model=settings.openrouter_model,
        prompt_version=settings.prompt_version,
        output_file_path=str(output_path),
        is_stale=False,
        stale_reason="",
    )
    db.add(metadata)
    db.flush()

    logger.info(
        "Generated %d test cases for selection=%d (metadata_id=%d)",
        len(test_cases),
        selection_id,
        metadata.id,
    )

    return GenerationResponse(
        generation_id=metadata.id,
        selection_id=selection_id,
        document_version_id=selection.document_version_id,
        node_ids=node_ids,
        test_cases=test_cases,
        generated_at=now,
        llm_provider="openrouter",
        llm_model=settings.openrouter_model,
        prompt_version=settings.prompt_version,
        is_stale=False,
    )


def load_generation(db: Session, generation_id: int) -> GenerationResponse:
    """
    Load a previously generated set of test cases from disk.

    Args:
        db:            Active session.
        generation_id: ID of the GeneratedTestCaseMetadata row.

    Returns:
        GenerationResponse (potentially with is_stale=True).

    Raises:
        NotFoundError if the metadata row or output file is missing.
    """
    meta = db.get(GeneratedTestCaseMetadata, generation_id)
    if meta is None:
        raise NotFoundError(f"Generation {generation_id} not found.")

    output_path = Path(meta.output_file_path)
    if not output_path.exists():
        raise NotFoundError(
            f"Output file for generation {generation_id} not found at {output_path}."
        )

    with output_path.open("r", encoding="utf-8") as f:
        raw = json.load(f)

    output = GeneratedTestCaseOutput.model_validate(raw)

    return GenerationResponse(
        generation_id=meta.id,
        selection_id=meta.selection_id,
        document_version_id=meta.document_version_id,
        node_ids=json.loads(meta.node_ids_json),
        test_cases=output.test_cases,
        generated_at=meta.generated_at,
        llm_provider=meta.llm_provider,
        llm_model=meta.llm_model,
        prompt_version=meta.prompt_version,
        is_stale=meta.is_stale,
    )


# ── Private helpers ────────────────────────────────────────────────────────────


def _reconstruct_text(nodes: list[DocumentNode]) -> str:
    """
    Build a readable text passage from a list of DocumentNode ORM objects.

    Nodes are sorted by (numbering, id) to preserve document order.
    Tables and lists are rendered as simple text for the LLM prompt.
    """
    import json as _json

    sorted_nodes = sorted(
        nodes,
        key=lambda n: (_natural_sort_key(n.numbering), n.id),
    )

    parts: list[str] = []
    for node in sorted_nodes:
        heading_prefix = ("#" * max(1, node.heading_level) + " ") if node.heading_level else ""
        if node.numbering and node.title:
            parts.append(f"{heading_prefix}{node.numbering} {node.title}")
        elif node.title:
            parts.append(f"{heading_prefix}{node.title}")

        if node.node_type == "table":
            try:
                rows = _json.loads(node.body_text)
                for row in rows:
                    parts.append(" | ".join(str(c) for c in row))
            except Exception:
                parts.append(node.body_text)
        elif node.node_type == "list":
            try:
                items = _json.loads(node.body_text)
                for item in items:
                    parts.append(f"- {item}")
            except Exception:
                parts.append(node.body_text)
        else:
            if node.body_text:
                parts.append(node.body_text)

    return "\n\n".join(parts)


def _natural_sort_key(numbering: str) -> list[int]:
    """
    Convert "2.1.10" → [2, 1, 10] for correct numeric ordering.
    Empty strings sort first (empty = root level).
    """
    if not numbering:
        return []
    parts = numbering.split(".")
    result: list[int] = []
    for p in parts:
        try:
            result.append(int(p))
        except ValueError:
            result.append(0)
    return result


def _call_llm_with_retry(content: str) -> list[TestCase]:
    """
    Call the OpenRouter API and parse the response.
    Retries on malformed output (parse errors) up to llm_max_retries.
    Rate-limit (429) retries are handled inside _call_openrouter.

    Raises:
        LLMResponseParseError after all retries are exhausted.
        LLMError on unrecoverable HTTP/network failure.
    """
    prompt = _USER_PROMPT_V1.format(content=content[:12000])  # Safety truncation.

    last_error: Exception | None = None
    max_attempts = 1 + settings.llm_max_retries
    for attempt in range(max_attempts):
        logger.info("LLM call attempt %d/%d", attempt + 1, max_attempts)
        try:
            raw_response = _call_openrouter(prompt)
            test_cases = _parse_llm_response(raw_response)
            return test_cases
        except LLMError:
            raise  # Don't retry on HTTP errors (already retried 429 inside _call_openrouter).
        except Exception as exc:
            last_error = exc
            logger.warning("LLM response parse failed (attempt %d): %s", attempt + 1, exc)
            if attempt < max_attempts - 1:
                time.sleep(2)  # Small pause before parse retry.

    raise LLMResponseParseError(
        f"LLM returned unparseable output after {max_attempts} attempts.",
        detail=str(last_error),
    )


def _call_openrouter(user_prompt: str) -> str:
    """
    Make a synchronous HTTP POST to OpenRouter's chat completions endpoint.

    Returns:
        The raw string content of the assistant's reply.

    Raises:
        LLMError on HTTP error or network failure.
    """
    url = f"{settings.openrouter_base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/qa-backend",
        "X-Title": "QA Backend",
    }
    payload = {
        "model": settings.openrouter_model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.2,  # Low temperature for structured output.
        "max_tokens": 4096,
    }

    # Retry up to 3 times on 429 (rate limit) with exponential backoff.
    _rate_limit_delays = [5, 15, 30]
    last_http_exc: Exception | None = None
    for _rl_attempt, _delay in enumerate([(0, *_rate_limit_delays)], 0):
        if _rl_attempt > 0:
            wait = _rate_limit_delays[_rl_attempt - 1]
            logger.warning("OpenRouter rate limited (429). Retrying in %ds...", wait)
            time.sleep(wait)
        try:
            with httpx.Client(timeout=settings.llm_timeout_seconds) as client:
                response = client.post(url, json=payload, headers=headers)
                if response.status_code == 429 and _rl_attempt < len(_rate_limit_delays):
                    last_http_exc = httpx.HTTPStatusError(
                        "429", request=response.request, response=response
                    )
                    continue  # Retry after backoff.
                response.raise_for_status()
                break  # Success.
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429 and _rl_attempt < len(_rate_limit_delays):
                last_http_exc = exc
                continue
            raise LLMError(
                f"OpenRouter returned HTTP {exc.response.status_code}.",
                detail=exc.response.text[:500],
            ) from exc
        except httpx.RequestError as exc:
            raise LLMError(f"Network error calling OpenRouter: {exc}") from exc
    else:
        raise LLMError(
            "OpenRouter rate limit (429) persisted after retries.",
            detail=str(last_http_exc),
        )

    data = response.json()
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise LLMError("Unexpected OpenRouter response shape.", detail=str(data)[:500]) from exc

    return content.strip()


def _parse_llm_response(raw: str) -> list[TestCase]:
    """
    Extract the JSON array from the LLM reply and validate each element
    against the ``TestCase`` Pydantic model.

    Raises:
        ValueError / pydantic.ValidationError on bad data (caller retries).
    """
    # Strip markdown code fences if present.
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(
            line for line in lines if not line.startswith("```")
        ).strip()

    # Find JSON array bounds.
    start = raw.find("[")
    end = raw.rfind("]")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"No JSON array found in LLM response: {raw[:200]}")

    json_str = raw[start : end + 1]
    raw_cases = json.loads(json_str)

    if not isinstance(raw_cases, list) or len(raw_cases) < 1:
        raise ValueError("LLM response JSON is not a non-empty array.")

    validated: list[TestCase] = []
    for i, case_dict in enumerate(raw_cases):
        try:
            validated.append(TestCase.model_validate(case_dict))
        except Exception as exc:
            raise ValueError(f"Test case {i} failed validation: {exc}") from exc

    return validated


def _save_output(output: GeneratedTestCaseOutput, selection_id: int) -> Path:
    """Write the generation output to a JSON file and return the path."""
    file_name = f"gen_{selection_id}_{uuid.uuid4().hex[:8]}.json"
    output_path = settings.llm_output_dir / file_name
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        f.write(output.model_dump_json(indent=2))
    logger.info("Saved LLM output to %s", output_path)
    return output_path
