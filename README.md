# QA Backend

> Production-grade document ingestion, versioning, change detection, and LLM-powered QA test-case generation API.

Built with **FastAPI · SQLAlchemy · pdfplumber · OpenRouter**.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Setup & Installation](#setup--installation)
3. [Environment Variables](#environment-variables)
4. [Database Initialisation](#database-initialisation)
5. [Running the Server](#running-the-server)
6. [Ingesting Documents](#ingesting-documents)
7. [Running Tests](#running-tests)
8. [API Reference](#api-reference)
9. [Example curl Commands](#example-curl-commands)

---

## Architecture Overview

```
qa-backend/
├── app/
│   ├── api/v1/          # Route handlers (thin — delegate to services)
│   ├── core/            # Config, logging, custom exceptions
│   ├── database/        # SQLAlchemy engine & session dependency
│   ├── models/          # ORM models (Document, Version, Node, Selection, Generation)
│   ├── parser/          # PDF → ParsedNode tree (no ORM dependency)
│   ├── schemas/         # Pydantic request/response models
│   └── services/        # Business logic (ingestion, search, LLM, staleness)
├── alembic/             # Database migrations
├── data/                # PDF source files
├── llm_outputs/         # JSON files for each LLM generation
├── scripts/             # CLI tools
└── tests/               # pytest test suite
```

---

## Setup & Installation

### Prerequisites

- Python **3.11+**
- pip

### Steps

```bash
# 1. Clone / navigate to the project
cd qa-backend

# 2. Create a virtual environment
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux

# 3. Install dependencies
pip install -r requirements.txt

# 4. Copy the environment template and fill in your API key
copy .env.example .env
```

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./qa_backend.db` | SQLAlchemy connection string |
| `OPENROUTER_API_KEY` | *(required for LLM)* | Your OpenRouter API key |
| `OPENROUTER_MODEL` | `google/gemini-flash-1.5` | Model to use for generation |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | OpenRouter API base URL |
| `DATA_DIR` | `data` | Directory for source PDFs |
| `LLM_OUTPUT_DIR` | `llm_outputs` | Directory for generation JSON files |
| `LOG_LEVEL` | `INFO` | Python logging level |
| `APP_ENV` | `development` | Environment name |
| `LLM_MAX_RETRIES` | `1` | Retry count on malformed LLM output |
| `PROMPT_VERSION` | `v1` | Prompt version tag stored with generations |

---

## Database Initialisation

Tables are created automatically on first server startup.

For manual migration management via Alembic:

```bash
# Generate initial migration (after any model changes)
alembic revision --autogenerate -m "initial schema"

# Apply migrations
alembic upgrade head
```

---

## Running the Server

```bash
uvicorn app.main:app --reload
```

The API will be available at:
- **API**: `http://localhost:8000/api/v1/`
- **Swagger UI**: `http://localhost:8000/docs`
- **ReDoc**: `http://localhost:8000/redoc`
- **Health check**: `http://localhost:8000/health`

---

## Ingesting Documents

### Via CLI Script (recommended for initial load)

```bash
# Ingest Version 1
python scripts/ingest.py --pdf data/ct200_manual.pdf --version 1

# Ingest Version 2
python scripts/ingest.py --pdf data/ct200_manual_v2.pdf --version 2
```

After ingesting Version 2, a staleness check runs automatically and any
previously generated test cases whose source nodes have changed will be
marked as stale.

### Via API (multipart upload)

```bash
curl -X POST http://localhost:8000/api/v1/documents/ingest \
  -F "file=@data/ct200_manual.pdf" \
  -F "version_number=1"
```

---

## Running Tests

```bash
# Run all tests
pytest tests/ -v

# Run a specific test file
pytest tests/test_parser.py -v

# Run with coverage report
pip install pytest-cov
pytest tests/ --cov=app --cov-report=term-missing
```

---

## API Reference

### Documents & Versions

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/v1/documents/ingest` | Upload & ingest a PDF |
| `GET` | `/api/v1/documents` | List all documents |
| `GET` | `/api/v1/documents/{id}` | Get document by ID |
| `GET` | `/api/v1/versions?document_id=` | List versions for a document |
| `GET` | `/api/v1/versions/{id}` | Get version by ID |

### Nodes

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/v1/nodes?version_id=` | Paginated flat node list |
| `GET` | `/api/v1/nodes/{id}` | Get node by ID |
| `GET` | `/api/v1/nodes/{id}/children` | Direct children of a node |
| `GET` | `/api/v1/nodes/{id}/tree` | Full recursive subtree |

### Search

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/v1/search?q=&version_id=&limit=` | Full-text search |

### Change Detection

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/v1/changes/{node_id}` | Has this node changed vs latest? |
| `GET` | `/api/v1/changes/versions?v1=&v2=` | Full version diff |

### Selections

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/v1/selections` | Create a version-pinned selection |
| `GET` | `/api/v1/selections` | List all selections |
| `GET` | `/api/v1/selections/{id}` | Get selection with nodes |
| `GET` | `/api/v1/selections/{id}/nodes` | Get just the node list |

### Test Case Generation

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/v1/generate` | Generate test cases for a selection |
| `GET` | `/api/v1/testcases/{generation_id}` | Get a generation by ID |
| `GET` | `/api/v1/testcases/selection/{selection_id}` | All generations for a selection |
| `GET` | `/api/v1/testcases/node/{node_id}` | Generations covering a node |

---

## Example curl Commands

### Ingest Version 1

```bash
python scripts/ingest.py --pdf data/ct200_manual.pdf --version 1
```

### Ingest Version 2

```bash
python scripts/ingest.py --pdf data/ct200_manual_v2.pdf --version 2
```

### List all documents

```bash
curl http://localhost:8000/api/v1/documents
```

### Get all versions of document 1

```bash
curl "http://localhost:8000/api/v1/versions?document_id=1"
```

### Get nodes for version 1 (first page)

```bash
curl "http://localhost:8000/api/v1/nodes?version_id=1&limit=20"
```

### Search for "safety"

```bash
curl "http://localhost:8000/api/v1/search?q=safety&limit=10"
```

### Check if node 5 has changed

```bash
curl http://localhost:8000/api/v1/changes/5
```

### Full diff between version 1 and version 2

```bash
curl "http://localhost:8000/api/v1/changes/versions?v1=1&v2=2"
```

### Create a selection named "Safety" with nodes 10, 11, 12 from version 1

```bash
curl -X POST http://localhost:8000/api/v1/selections \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Safety",
    "description": "Safety-critical sections",
    "version_id": 1,
    "node_ids": [10, 11, 12]
  }'
```

### Generate test cases for selection 1

```bash
curl -X POST http://localhost:8000/api/v1/generate \
  -H "Content-Type: application/json" \
  -d '{"selection_id": 1}'
```

### Retrieve generation 1

```bash
curl http://localhost:8000/api/v1/testcases/1
```

### Get all generations for selection 1

```bash
curl http://localhost:8000/api/v1/testcases/selection/1
```
