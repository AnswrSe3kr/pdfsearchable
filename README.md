# pdfsearchable

[![CI](https://github.com/AnswrSe3kr/pdfsearchable/actions/workflows/ci.yml/badge.svg)](https://github.com/AnswrSe3kr/pdfsearchable/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://img.shields.io/badge/tests-901%20passing-brightgreen.svg)](tests/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![security: bandit](https://img.shields.io/badge/security-bandit-yellow.svg)](https://github.com/PyCQA/bandit)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)

**Index, search, and explore your PDF collection — entirely offline.**

A command-line tool that turns a folder of PDFs into a searchable, browsable knowledge base with a beautiful Apple-like interactive web application. No cloud required. OCR built in. AI enrichment via local Ollama.

---

## Features

| Feature | Details |
|---|---|
| **Indexing** | PyMuPDF text extraction + Tesseract OCR on every page |
| **OCR pipeline** | OSD orientation, Otsu binarisation, auto-deskew, confidence retry; historical mode with CLAHE, Sauvola, morphological cleaning |
| **Full-text search** | SQLite FTS5 with `AND / OR / NOT` and exact-phrase support |
| **Semantic search** | Ollama embeddings (`nomic-embed-text`), pure-Python cosine similarity |
| **Interactive SPA** | Apple-like 3-column web app: sidebar, document list, detail panel |
| **Document viewer** | Full-page viewer with inline PDF, metadata sidebar, annotations, RAG chat |
| **Word cloud** | Interactive canvas word cloud by document type or full collection |
| **Location map** | Leaflet map of extracted locations with optional Nominatim geocoding |
| **Knowledge graph** | Force-directed D3 canvas graph of entity relationships |
| **Document timeline** | Chronological view of documents with date-range filtering |
| **AI enrichment** | Doc-type classification, summary, tags, parties, values — via Ollama |
| **RAG chat** | Multi-document Q&A in the terminal (`pdfsearchable chat`) |
| **Annotations** | Add, list, and manage per-page annotations on indexed documents |
| **Contracts** | Contract analysis with expiry alerts and summary dashboard |
| **Forensics** | PDF forensic analysis: metadata, hidden text, embedded files |
| **Redaction detection** | Detect redacted content in PDFs |
| **Table extraction** | Extract structured tables to CSV or JSON |
| **MCP server** | Expose your library to Claude Desktop, Cursor, Zed via MCP stdio |
| **Watch mode** | Auto-index new and modified PDFs as they appear |
| **Export** | JSON (full index), JSONL (LLM/RAG pipelines), CSV (tabular), Markdown (one file per doc), Obsidian/Logseq YAML frontmatter |
| **Backup / Verify** | `.tar.gz` archive + hash-drift integrity check |
| **Real-time updates** | SSE push — SPA shows a banner when index changes |
| **Offline-first** | Leaflet, wordcloud2 downloaded locally; no CDN dependency at runtime |
| **Auth** | Optional Bearer token for the HTTP server (`PDFSEARCHABLE_AUTH_TOKEN`) |
| **CORS** | Configurable CORS headers for cross-origin API access |
| **Audit log** | Every action appended to `.pdfsearchable/audit.jsonl` |
| **Classifier feedback** | Record corrections to improve future document classification |
| **Auto snapshots** | Rotating index snapshots before every save (`PDFSEARCHABLE_AUTO_SNAPSHOT=1`) |
| **Schema migration** | `pdfsearchable migrate` forces v1/v2 → v3 with dry-run preview |
| **Dry-run indexing** | `pdfsearchable add --dry-run` previews new/reprocess/ignore before writing |
| **Incremental embeddings** | `pdfsearchable add --embed` generates embeddings only for new/changed docs |
| **Semantic dedup** | `pdfsearchable dedup-semantic` finds near-duplicates (translations, versions) by cosine ≥ threshold |
| **Inspect command** | `pdfsearchable inspect` shows 5 Rich panels or JSON for any doc |
| **Ollama keep-alive** | Model stays warm between calls (`PDFSEARCHABLE_OLLAMA_KEEP_ALIVE=30m`) — 2–5 s/doc saved |
| **Formula preservation** | Detects LaTeX (`$…$`, `$$…$$`, `\[…\]`, `equation/align` envs) **and** Unicode math clusters (∫ ∑ ∏ √ ∂ π θ …). Enable via `PDFSEARCHABLE_DETECT_FORMULAS=1`. Stored in `metadata.formulas` and re-emitted in Markdown export as `$$ … $$` blocks. |
| **Markdown benchmark** | `pdfsearchable benchmark-markdown <file>` measures PDF→Markdown speedup vs naive PyMuPDF re-parse. Measured **7.7× faster** on a 20-page synthetic PDF (10 iterations, macOS arm64); the gain comes from reading the cached pre-extracted text instead of re-parsing. |

---

## Installation

**Requirements:** Python 3.10+, [Tesseract](https://github.com/tesseract-ocr/tesseract) (for OCR), [Ollama](https://ollama.com) (optional, for AI features)

```bash
pip install pdfsearchable
```

Or install from source:

```bash
git clone https://github.com/AnswrSe3kr/pdfsearchable.git
cd pdfsearchable
pip install -e .
```

Optional extras:

```bash
pip install "pdfsearchable[ai]"            # OpenAI classification
pip install "pdfsearchable[htr]"           # HTR (TrOCR) for handwritten text
pip install "pdfsearchable[tables-ocr]"   # Table extraction from scanned PDFs
pip install "pdfsearchable[dev]"           # pytest, ruff
```

---

## Quick start

```bash
# 1. Index a folder of PDFs
pdfsearchable add ~/Documents/contracts/

# 2. Open the interactive web application
pdfsearchable serve
# → Opens http://127.0.0.1:8000/app.html

# 3. Search from the terminal
pdfsearchable search "rescisão contratual"

# 4. Get document details
pdfsearchable info contrato-aluguel
```

The web application (`app.html`) is a single-page app that loads all data dynamically via a REST API. It provides:

- **Sidebar** — document type filters, tag filters, statistics
- **Document list** — searchable, sortable list of all indexed PDFs
- **Detail panel** — metadata, extracted text, annotations, inline RAG chat
- **Word cloud view** — `/wordcloud.html` — interactive word frequency canvas
- **Location map** — `/map.html` — Leaflet map with geocoded locations
- **Knowledge graph** — `/graph.html` — force-directed entity relationship graph
- **Timeline** — `/timeline.html` — chronological document browser

The static `report.html` can be generated separately with `pdfsearchable report` — it is a standalone snapshot, independent of the live server.

---

## Command reference

### Indexing

```bash
pdfsearchable add FILE_OR_DIR [OPTIONS]

  --workers N                Parallel workers (0=auto up to 16; 1=sequential; 2+=multiprocessing)
  --batch-size N             Process in batches of N files (gc between batches)
  --order-by size|mtime|name Order files before processing
  --recursive / --no-recursive  Include PDFs in subfolders (default: recursive)
  --resume                   Resume from interrupted run (Ctrl+C)
  --continue                 Skip already indexed and continue on errors
  --reprocess                Re-index even if content hash matches
  --extract-mode text|blocks|dict   PyMuPDF extraction mode
  --compress                 Compress stored text (gzip)
  --password TEXT            PDF password (or env PDF_PASSWORD)
  --confirm-type             Confirm AI-classified type interactively
  --embed                    Generate semantic embeddings (incremental) after indexing
  --dry-run                  Preview what would be indexed without writing anything
```

OCR is configured via environment variables: `PDFSEARCHABLE_OCR_LANG` (default `por`), `PDFSEARCHABLE_OCR_DPI` (default `300`), `PDFSEARCHABLE_OCR_HISTORICAL` (`off` / `auto` / `on`).

```bash
pdfsearchable remove FILE_OR_ID [--yes]   # Remove from index
pdfsearchable status                      # Project status (indexed files, totals)
pdfsearchable info ID_OR_NAME             # Detailed metadata for one document
pdfsearchable inspect ID_OR_NAME [--json] # 5-panel view (identification, enrichment, entities, detections, pdf meta)
pdfsearchable migrate [--dry-run]         # Force index schema migration (v1/v2 → v3)
pdfsearchable dedup-semantic [--threshold 0.98] [--model nomic-embed-text]   # Near-duplicates by cosine similarity
```

### Search

```bash
pdfsearchable search QUERY [OPTIONS]

  --type TYPE       Filter by document type
  --language LANG   Filter by language (e.g. pt-BR, en)
  --date-from DATE  Indexed since (YYYY-MM-DD)
  --date-to DATE    Indexed until (YYYY-MM-DD)
  --semantic        Semantic search via embeddings (run `pdfsearchable embed` first)
  --ollama          Expand query terms via Ollama
```

FTS operators: `"exact phrase"`, `term1 AND term2`, `term1 OR term2`, `NOT term`

### Web server

```bash
pdfsearchable serve [OPTIONS]

  --host HOST     Bind address (default: 127.0.0.1)
  --port PORT     Port (default: 8000)
  --open          Open browser automatically
```

`serve` copies all SPA template files to `.pdfsearchable/` and starts the HTTP server. Opening `http://host:port/` redirects to `/app.html`.

**API endpoints exposed by the server:**

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Redirect to `/app.html` |
| GET | `/api/health` | Health check (index + Ollama status) |
| GET | `/api/index` | Full index JSON for SPA bootstrap |
| GET | `/api/search` | FTS + semantic search (`?q=&type=`) |
| GET | `/api/text` | Full extracted text for a document (`?id=`) |
| GET | `/api/page` | Text for a specific page (`?id=&page=`) |
| GET | `/api/annotations` | List annotations for a document (`?id=`) |
| GET | `/api/wordcloud` | Word frequencies (`?type=&limit=`) |
| GET | `/api/locations` | Locations with optional geocoding (`?geocode=0\|1`) |
| GET | `/api/graph` | Knowledge graph nodes and edges |
| GET | `/api/timeline` | Timeline entries and statistics |
| GET | `/api/events` | SSE stream for real-time index updates |
| GET | `/arquivos-processados/<file>` | Serve PDF files |
| POST | `/api/meta/update` | Update doc_type, tags, subject |
| POST | `/api/annotations` | Add a new annotation |
| POST | `/api/ask` | RAG question answering via Ollama (rate limited) |

**Server features:** Bearer/Basic auth (`PDFSEARCHABLE_AUTH_TOKEN`), CORS with `Max-Age` preflight caching, rate limiting on `/api/ask`, gzip compression for large responses, SSE connection timeout (5 min), geocoding cache, JSON error responses on all endpoints, directory listing disabled.

### Report (static, standalone)

```bash
pdfsearchable report [--output PATH]
```

Generates a self-contained `report.html` snapshot of the current index. This file can be opened in any browser without a running server. It is separate from `serve` and the live SPA.

### AI features (Ollama)

```bash
# Enrich all indexed documents with AI metadata
PDFSEARCHABLE_AI=ollama pdfsearchable add docs/

# Generate semantic embeddings for vector search
pdfsearchable embed

# Chat with your document collection
pdfsearchable chat
pdfsearchable chat --doc DOCUMENT_ID    # Single-document focus

# Semantic search
pdfsearchable search --semantic "quem assinou o contrato"
```

### Document analysis

```bash
# Detect redacted content in a PDF
pdfsearchable redactions [PDF]

# Forensic analysis (metadata, hidden text, embedded files)
pdfsearchable forensics [PDF]

# Extract tables to CSV or JSON
pdfsearchable tables [PDF]

# Contract summary and expiry alerts
pdfsearchable contracts

# Document timeline in the terminal
pdfsearchable timeline

# Generate knowledge graph HTML
pdfsearchable knowledge-graph
```

### Annotations and feedback

```bash
# Manage document annotations (list, add, delete)
pdfsearchable annotations [OPTIONS]

# Record classifier corrections for future training
pdfsearchable feedback
```

### MCP server (Claude Desktop / Cursor / Zed)

```bash
pdfsearchable mcp
```

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "pdfsearchable": {
      "command": "pdfsearchable",
      "args": ["mcp"],
      "cwd": "/path/to/your/pdf/folder"
    }
  }
}
```

Exposed tools: `list_documents`, `search_documents`, `get_document_text`, `ask_document`, `ask_all_documents`, `index_document`, `get_redaction_report`, `get_forensics_summary`

### Automation

```bash
pdfsearchable watch [DIR]          # Auto-index new/modified PDFs
pdfsearchable watch --interval 5   # Check every 5 seconds

pdfsearchable backup [OUTPUT]      # Create .tar.gz of the index
pdfsearchable verify               # Check hash integrity of all PDFs

pdfsearchable export --format jsonl --output colecao.jsonl
pdfsearchable export --format markdown --output ./docs_md/
pdfsearchable export --format csv --output metadados.csv
pdfsearchable export --format obsidian --output-dir ~/vault/PDFs

pdfsearchable doctor               # Diagnose environment (PyMuPDF, Tesseract, Ollama, disk…)
```

---

## Configuration

All settings are environment variables with the `PDFSEARCHABLE_` prefix, or saved in `.pdfsearchable/config.json` (created by `pdfsearchable init`).

| Variable | Default | Description |
|---|---|---|
| `PDFSEARCHABLE_AI` | `heuristic` | `heuristic` / `ollama` / `openai` |
| `PDFSEARCHABLE_OLLAMA_URL` | `http://localhost:11434` | Ollama base URL |
| `PDFSEARCHABLE_OLLAMA_MODEL` | `llama3.2` | Chat/classification model |
| `PDFSEARCHABLE_OLLAMA_CLASSIFY_MODEL` | _(none)_ | Smaller model used only for classification (e.g. `llama3.2:1b`) |
| `PDFSEARCHABLE_OLLAMA_KEEP_ALIVE` | `5m` | Keep model loaded between calls (`-1`=permanent, `0`=unload) |
| `PDFSEARCHABLE_AUTO_SNAPSHOT` | `0` | `1` to snapshot the index before every save |
| `PDFSEARCHABLE_SNAPSHOT_KEEP` | `5` | Number of rotating snapshots kept |
| `PDFSEARCHABLE_DETECT_REDACTIONS` | `0` | `1` to detect redacted zones during indexing |
| `PDFSEARCHABLE_FORENSICS` | `0` | `1` to run PDF forensic analysis during indexing |
| `PDFSEARCHABLE_CONTRACTS` | `0` | `1` to detect contract clauses during indexing |
| `PDFSEARCHABLE_OCR_WORKERS` | auto (3·cpu/4, 2–8) | Parallel OCR workers per page |
| `PDFSEARCHABLE_DETECT_FORMULAS` | `0` | `1` to detect LaTeX / Unicode math formulas during indexing |
| `PDFSEARCHABLE_AUTH_TOKEN` | _(none)_ | Bearer token for HTTP server |
| `PDFSEARCHABLE_CORS` | `0` | `1` to enable CORS headers |
| `PDFSEARCHABLE_CORS_ORIGIN` | `*` | Allowed CORS origin |
| `PDFSEARCHABLE_ASK_RATE_LIMIT` | _(none)_ | Requests/min for `/api/ask` (0 = unlimited) |
| `PDFSEARCHABLE_HTTP_LOG` | `0` | `1` to log HTTP requests |
| `PDFSEARCHABLE_WEBHOOK_URL` | _(none)_ | POST JSON after each indexing run |
| `PDFSEARCHABLE_OCR_LANG` | `por` | Tesseract language code(s) |
| `PDFSEARCHABLE_OCR_DPI` | `300` | Rendering DPI for OCR |
| `PDFSEARCHABLE_OCR_CORRECT` | `0` | `1` to fix OCR errors via LLM |
| `PDFSEARCHABLE_OCR_HISTORICAL` | `off` | `off` / `auto` / `on` — historical document pipeline (CLAHE + Sauvola + morphology + HTR históricos) |
| `OPENAI_API_KEY` | _(none)_ | Required when `PDFSEARCHABLE_AI=openai` |

---

## Storage layout

```
your-project/
├── .pdfsearchable/
│   ├── index.json          # Document registry
│   ├── fts.sqlite          # Full-text search index (FTS5)
│   ├── embeddings.sqlite   # Semantic embeddings (optional)
│   ├── audit.jsonl         # Action log
│   ├── assets/             # Offline JS/CSS assets
│   ├── config.json         # Project config
│   ├── app.html            # Main SPA (copied by serve)
│   ├── document-view.html  # Full-page document viewer (copied by serve)
│   ├── wordcloud.html      # Word cloud view (copied by serve)
│   ├── map.html            # Location map view (copied by serve)
│   ├── graph.html          # Knowledge graph view (copied by serve)
│   ├── timeline.html       # Timeline view (copied by serve)
│   └── report.html         # Static report (generated by pdfsearchable report)
└── arquivos-processados/
    ├── <id>.pdf            # Symlink / copy of original
    └── <id>/               # Extracted text per page
        ├── page_001.txt
        └── …
```

---

## Development

```bash
git clone https://github.com/AnswrSe3kr/pdfsearchable.git
cd pdfsearchable
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Pre-commit hooks (security, lint, format)
pip install pre-commit && pre-commit install

# Full test pyramid
pytest tests/unit/        -q   # unit (white-box)
pytest tests/integration/ -q   # integration (grey-box)
pytest tests/system/      -q   # system / E2E (black-box)
pytest tests/acceptance/  -q   # UAT
pytest tests/security/    -q   # security (OWASP)
pytest tests/regression/  -q   # regression (bug guards)
pytest tests/performance/ -q   # load & throughput

# All at once with coverage
pytest tests/ --cov=pdfsearchable --cov-report=term-missing

# Lint + format
ruff check src/ && ruff format --check src/

# SAST security scan
bandit -r src/ -c pyproject.toml

# Dependency vulnerability scan
pip-audit
```

---

## License

MIT
