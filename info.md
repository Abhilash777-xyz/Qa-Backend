# Project Architecture & Workflow: QA Backend

This document details the system design, parsing algorithms, database schema, and operational workflows of the **QA Backend** project. Use this to understand the project components, explain its architecture, or prepare for code walkthroughs.

---

## 1. High-Level Architecture

The project is built on **Hexagonal / Clean Architecture** principles, enforcing strict separation of concerns into independent layers:

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

*   **API Router (`app/api/v1/`):** Exposes FastAPI routes. Validates requests via Pydantic; does not handle business logic or raw SQL queries.
*   **Service Layer (`app/services/`):** The heart of the application. Dictates ingestion, diff generation, selection tracking, and LLM orchestration.
*   **PDF Parser (`app/parser/`):** A standalone utility. Takes a raw file path and outputs pure-Python node structures, isolated from database models.
*   **LLM Service (`app/services/llm_service.py`):** Integrates with OpenRouter API. Formulates structured prompts, validates LLM responses via Pydantic, and writes test suites to persistent JSON files.

---

## 2. In-Depth Feature Walkthrough

### ❶ The PDF Parsing Pipeline (`app/parser/pdf_parser.py`)

A primary technical challenge is extracting clean headings, subsections, lists, and tables from structured PDFs like user manuals.

#### Pass 1: Font Size Clustering (Adaptive Thresholding)
Instead of hardcoding font thresholds (e.g., assuming `H1` is exactly 24px), the parser collects the font size of every character in the document and clusters them:
1. Sizes within `0.5 pt` of each other are clustered.
2. The clusters are sorted descending.
3. The top sizes are dynamically mapped to `H1` through `H6`. Smaller font sizes are treated as standard body text.
This makes the parser **fully adaptive** to any document stylesheet.

#### Pass 2: Bounding Box Filtering
1. **Header/Footer Removal:** Filters out content in the top and bottom 5% of each page height to discard page numbers, running headers, and footers.
2. **Table Isolation:** Uses `pdfplumber` line strategies to find tables first, tracking their bounding boxes. Any text inside these boxes is skipped during paragraph parsing to prevent tables from being garbled into the text stream.
3. **Element Grouping:** Group text lines into headings, standard body text, or bulleted/numbered lists.

#### Pass 3: Tree Construction & Identity Stability
A heading level stack tracks parenting:
*   A new heading pops the stack until it finds a parent with a higher heading level, then appends itself as a child.
*   Paragraphs, tables, and lists append to the nearest active heading.
*   **Deterministic UUIDs (`node_uuid`):** To track changes across document versions, each node requires a stable ID. We generate it from:
    *   The section numbering (e.g., `"8.1"`).
    *   For unnumbered sections, a path combination of parent nodes and a slugified title (e.g., `"root:user-manual:classification"`).
    Even if layout boundaries shift, the node UUID remains identical.

---

### ❷ Version Comparison & Diffing (`app/services/version_service.py`)

When an updated document version is uploaded, we compare the old node list with the new node list:

1. **Deterministic Content Hashing:**
   Each node calculates a SHA-256 hash from its normalized text components:
   $$\text{SHA-256}(\text{normalized title} \parallel "|" \parallel \text{numbering} \parallel "|" \parallel \text{normalized body text})$$
2. **Diff Categorization:**
   We map nodes by their `node_uuid` to find differences:
   *   **`UNCHANGED`:** The node UUID exists in both versions, and the content hash matches.
   *   **`MODIFIED`:** The node UUID exists in both versions, but the content hash differs (indicating edits, content updates, or movement to a new numbering section).
   *   **`ADDED`:** The node UUID exists only in the new version.
   *   **`REMOVED`:** The node UUID exists only in the old version.

---

### ❸ Selection Pinning & Version Isolation (`app/services/selection_service.py`)

Named selections (e.g., a "Safety Critical Suite" selection) allow users to bundle document sections together for testing.
*   **Immutable Pinning:** A selection is created against a specific document version.
*   **Anti-Drift Guard:** Once created, a selection is permanently locked to the nodes of its original version. If subsequent versions add, modify, or delete sections, the selection’s historical scope remains unaffected.

---

### ❹ Staleness Detection (`app/services/staleness_service.py`)

When a user updates a document, any test suites generated for prior versions might become outdated.
1. The service looks up all test case generations associated with the document.
2. For each generation, it maps the original node IDs to their stable `node_uuid`s.
3. It checks the latest version of the document to see if those `node_uuid`s:
    *   Have been **removed**.
    *   Have **differing content hashes** (modified).
4. If either condition is met, the generation's metadata is flagged as `is_stale = true` with a clear explanation of the drift.
5. This is a **one-way ratchet**: once marked stale, a generation stays stale until regenerated.

---

### ❺ LLM Generation & 429 Rate-Limit Mitigation (`app/services/llm_service.py`)

The generation endpoint gathers selected requirement nodes, structures them into a coherent context, and calls an LLM via OpenRouter.

#### Rate-Limit Backoff Handler
Because OpenRouter free-tier keys have rate limits, the service handles `429 Too Many Requests` status codes directly using a backoff loop:
*   On receiving a `429`, the system pauses for `5 seconds`, then `15 seconds`, and finally `30 seconds` before giving up.
*   If a request fails validation (malformed JSON format), the system retries once.

---

## 3. Database Design (SQLite Schema)

Six tables represent the full operational flow of the system:

1.  **`documents`**: Tracks document identities (e.g., "CT200 Manual").
2.  **`document_versions`**: Represents immutable versions (Version 1, Version 2) and holds the file's overall hash.
3.  **`document_nodes`**: Stores individual elements (titles, content hashes, text body, parent-child tree links).
4.  **`selections`**: Saves grouped nodes for generation. Pinned to a specific version.
5.  **`selection_nodes`**: The join table mapping selections to nodes.
6.  **`generated_test_case_metadata`**: Contains model parameters, prompts, file locations on disk, and the `is_stale` flag.

---

## 4. Run & Test Instructions

### Initial Setup
Ensure python `3.11+` is active:
```powershell
# Create virtual environment and install packages
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### Running Server & CLI Commands
```powershell
# Run backend server
uvicorn app.main:app --reload

# Ingest CT200 Manual Version 1
python scripts/ingest.py --pdf data/ct200_manual.pdf --version 1

# Ingest CT200 Manual Version 2 (Runs comparisons and flags stale suites automatically)
python scripts/ingest.py --pdf data/ct200_manual_v2.pdf --version 2
```

### Running the Test Suite
```powershell
# Execute all 102 unit/integration tests
pytest tests/ -v
```
