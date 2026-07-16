# Approach Document — QA Backend

## 1. Architecture

The system follows a **layered clean architecture**:

```
HTTP Request
    │
    ▼
Route Handler (app/api/v1/)
    │  Validates HTTP layer only; no business logic
    ▼
Service Layer (app/services/)
    │  All business logic; no HTTP concepts
    ▼
Repository / ORM (app/models/ + SQLAlchemy)
    │
    ▼
SQLite via SQLAlchemy
```

**Parser** (`app/parser/`) is a fully independent module.  It returns pure
Python dataclasses (`ParsedNode`) and has zero ORM or HTTP imports.  This
makes it testable in isolation and swappable without touching the rest of the
system.

**LLM outputs** are stored as JSON files on disk with a metadata row in SQLite.
This avoids schema churn when the LLM output format changes — only the JSON
file format evolves, and the metadata row fields (hashes, timestamps, stale
flag) remain stable.

---

## 2. Parser Design

### Tool Choice: pdfplumber

`pdfplumber` was chosen over `PyMuPDF` and `pdfminer` because:
- Best-in-class table extraction with bounding-box detection.
- Per-character font metadata (size, name) enabling heading classification.
- Active maintenance and MIT license.

### Two-Pass Algorithm

**Pass 1 — Font-size clustering:**
Collect all font sizes across the entire document.  Cluster sizes within 0.5 pt
of each other.  Assign heading levels 1–6 to the largest clusters.  Sizes that
would map to level > 6 are treated as body text.

This is **adaptive** — it works whether the document uses 24pt headings or
18pt headings without hardcoding any threshold.

**Pass 2 — Element extraction:**
For each page:
1. Extract tables using line-based strategy; record their bounding boxes.
2. Extract text words with font metadata; group into lines by y-coordinate.
3. Filter out header/footer zones (top and bottom 5 % of page height).
4. Skip text words whose y-position falls inside a table bounding box.
5. Classify each line as HEADING / LIST_ITEM / BODY.

**Tree building:**
A heading stack (similar to an HTML parser's element stack) maintains the
current ancestry chain.  Each new heading pops the stack to its level, then
pushes itself as a child of the remaining top-of-stack.  Body lines and list
items accumulate on the current heading node.  Tables become child nodes of
the current heading.

### node_uuid

`node_uuid` is the **cross-version identity key** for a node.  It is:
- The dotted numbering string (e.g. `"3.2.1"`) if the heading has one.
- `"{parent_uuid}:{slugified_title}"` for un-numbered headings.

This makes it deterministic and stable — the same section in v1 and v2 will
have the same `node_uuid` even if the surrounding page layout changes.

---

## 3. Database Design

### Core tables

| Table | Purpose |
|---|---|
| `documents` | One row per document identity (e.g. "CT200 Manual") |
| `document_versions` | Immutable snapshot rows; never updated after creation |
| `document_nodes` | The tree nodes; `parent_id` FK is a self-reference |
| `selections` | Named node collections pinned to a `document_version_id` |
| `selection_nodes` | Many-to-many pivot between selections and nodes |
| `generated_test_case_metadata` | LLM run provenance; `is_stale` flag |

### Immutability guarantees

`document_versions` rows are **never updated or deleted**.  The ingestion
service has a conflict guard that rejects a second ingest of the same
(document, version_number) pair.  This ensures the audit trail is permanent.

### Version pinning

`selections.document_version_id` is set at creation and has no update path
in the service layer.  All node lookups for a selection filter by that
`document_version_id` so they always resolve to the pinned snapshot.

---

## 4. Version Matching Strategy

### Primary: node_uuid (dotted numbering)

Most sections in the CT200 manual have numbered headings (1.1, 2.3.4 etc.).
The parser extracts these as `node_uuid`.  Matching v1 nodes to v2 nodes by
`node_uuid` is O(n) via dictionary lookup and is exact.

### Fallback: slugified title under the same parent

For un-numbered headings, the slug is `"{parent_uuid}:{slugify(title)}"`.
If a section is renamed between versions, it appears as REMOVED + ADDED (no
fuzzy matching).  This is intentional — a rename is a content change and
should be flagged for review.

### No fuzzy matching

Fuzzy string matching (e.g. Levenshtein distance) was considered and rejected:
- It produces false positives on short headings ("1. Scope" → "2. Scope").
- The CT200 manual uses consistent numbered headings making it unnecessary.
- It adds non-trivial complexity and runtime cost.

---

## 5. Hash Strategy

Each node's `content_hash` is:
```python
SHA-256(normalize(title) + "|" + numbering + "|" + normalize(body_text))
```

**Why SHA-256?**  Collision-free at this document scale; 64-char hex fits
comfortably in a VARCHAR column; industry-standard.

**Why include numbering?**  A section moved from 2.1 to 3.1 with identical
body text is semantically a different requirement — the numbering change
should show up as MODIFIED.

**Why normalise text?**  Whitespace differences between PDF renders (extra
spaces, line breaks) should not trigger false MODIFIED detections.

---

## 6. LLM Integration

### Provider: OpenRouter

OpenRouter was chosen because:
- Provides a unified API over many models (Gemini, Llama, Mistral, etc.).
- Free tier available with `google/gemini-flash-1.5`.
- Single API key; easy to swap models without code changes.

### Prompt Design

- **System prompt** sets the LLM's role and specifies the exact JSON schema.
- **User prompt** contains only the reconstructed document text.
- Temperature is set to 0.2 for structured, reproducible output.
- The prompt is versioned (`PROMPT_VERSION` env var) so every generation
  records which prompt produced it.

### Retry Logic

On first attempt, the LLM response is parsed and validated by Pydantic.  If
validation fails (missing field, wrong type, invalid priority value), the
service retries the full LLM call once.  If the second attempt also fails, a
`LLMResponseParseError` (HTTP 502) is returned with the validation error
details.

---

## 7. Staleness Detection

After each new version ingestion:
1. Load the latest version's nodes into a `{node_uuid: content_hash}` dict.
2. For every `GeneratedTestCaseMetadata` row that is not yet stale:
   a. Load the stored `content_hashes_json` and `node_ids_json`.
   b. Map each stored node ID to its `node_uuid` (from the original version).
   c. Look up each `node_uuid` in the latest version's hash dict.
   d. If any hash differs or the node is absent → mark stale.

Staleness is a **one-way ratchet** — once stale, a generation is never
automatically un-stalened.  A new generation call is required.

---

## 8. Known Limitations

| Limitation | Mitigation |
|---|---|
| Scanned / image-only PDFs are not supported | pdfplumber requires embedded text |
| Multi-column layouts may interleave text | Single-column technical manuals are the target |
| Header/footer filtering is heuristic (5 %) | Works well for the CT200 manual; may need tuning |
| Search uses SQL LIKE (not full-text) | Sufficient at this scale; replace with FTS5 for large corpora |
| No authentication on any endpoint | Out of scope for this assignment; add OAuth2 / API keys for production |
| LLM output non-deterministic | Low temperature + structured prompt minimises variance |
| SQLite write concurrency | Acceptable for development; switch to Postgres for production |

---

## 9. Decision Log

| Decision | Alternatives | Reason chosen |
|---|---|---|
| pdfplumber | PyMuPDF, pdfminer | Best table extraction + font metadata |
| SQLite | Postgres | Zero-config for local dev; trivial to upgrade |
| JSON files for LLM output | MongoDB, JSONB column | Avoids schema migrations when output format evolves |
| OpenRouter | Gemini direct, OpenAI | Free tier; model-agnostic; requested by user |
| SHA-256 | MD5, CRC32, simhash | Collision-free; standard; fits VARCHAR(64) |
| Append-only versions | Soft-delete / overwrite | Immutability simplifies audit, debugging, pinning |
| Alembic | Just `create_all` | Production-correct; supports rollbacks and schema evolution |

---

## 10. Future Improvements

1. **Async LLM calls** — Move `httpx` call to `AsyncClient` and make the
   generation endpoint truly async.
2. **FTS5 / Postgres full-text search** — Replace LIKE queries for large docs.
3. **Authentication** — Add OAuth2 / API-key middleware.
4. **Background ingestion** — Queue large PDFs with Celery / ARQ; return a
   job ID immediately.
5. **Multi-column PDF support** — Use layout analysis (e.g. `layout-parser`)
   to detect and merge columns before text extraction.
6. **Fuzzy node matching** — Optional mode using token-set ratio for documents
   without consistent numbering.
7. **Webhook / event system** — Emit events on version ingestion so downstream
   systems can react to staleness without polling.
8. **Rate limiting** — Add slowapi or similar to protect LLM endpoints from
   abuse.
