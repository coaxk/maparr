# MapArr — Web Project (v1.0-web-pivot branch)

## What This Is
Path Mapping Problem Solver for Docker *arr apps. Web UI with FastAPI backend.
Analyzes Docker Compose volume mounts, detects hardlink-breaking configurations,
and generates specific fixes following the TRaSH Guides pattern.

## Stack
- **Backend:** Python 3.11, FastAPI, uvicorn, PyYAML
- **Frontend:** Vanilla HTML/CSS/JS (single-page, no framework, no build step)
- **Tests:** pytest (360+ tests), run with `pytest tests/ -p no:capture` on Windows

## Architecture

### Backend Modules (`backend/`)
| File | Purpose |
|------|---------|
| `main.py` | FastAPI app, 9 API routes, session state |
| `pipeline.py` | **Core innovation** — full-directory scan, unified media service map |
| `analyzer.py` | Per-stack analysis: volumes, conflicts, fix generation |
| `cross_stack.py` | Sibling scanning for single-service stacks (legacy, pipeline supersedes) |
| `discovery.py` | Compose file filesystem scanner |
| `resolver.py` | Compose resolution (docker compose config + manual .env fallback) |
| `parser.py` | Error text parser (service, path, error type extraction) |
| `smart_match.py` | Intelligent error-to-stack matching with scoring |
| `mounts.py` | Mount type classification (NFS, CIFS, WSL2, local) |
| `log_handler.py` | In-memory ring buffer + SSE streaming for logs |

### Frontend (`frontend/`)
- `index.html` — Single-page app with all sections
- `app.js` — ~4200 lines, entire frontend logic
- `styles.css` — Full CSS with dark theme

### API Endpoints
| Method | Route | Purpose |
|--------|-------|---------|
| GET | `/api/health` | Health check + version |
| POST | `/api/parse-error` | Extract service/path/error from pasted text |
| GET | `/api/discover-stacks` | Scan for compose stacks |
| POST | `/api/pipeline-scan` | Full-directory media pipeline scan |
| POST | `/api/change-stacks-path` | Runtime scan path change |
| POST | `/api/select-stack` | Store selected stack |
| POST | `/api/analyze` | Full stack analysis |
| POST | `/api/smart-match` | Error-to-stack matching |
| POST | `/api/apply-fix` | Write corrected YAML (with backup) |
| GET | `/api/logs` | Fetch log entries |
| GET | `/api/logs/stream` | SSE live log stream |

## Key Patterns

### Pipeline-First Analysis
The pipeline scans the entire root directory on boot, builds a unified map of all
media services (role, mount paths), and caches the result in `_session["pipeline"]`.
Per-stack analysis receives this as `pipeline_context` — no more isolated analysis.

### Session State
```python
_session = {
    "parsed_error": None,
    "selected_stack": None,
    "custom_stacks_path": None,
    "pipeline": None,  # Cached PipelineResult.to_dict()
}
```
Invalidated when stacks path changes.

### Status Values (AnalysisResult.to_dict())
- `healthy` — No issues, no pipeline context
- `healthy_pipeline` — No issues, pipeline confirms all services aligned
- `conflicts_found` — Local volume conflicts detected
- `pipeline_conflict` — Pipeline-level mount mismatch
- `healthy_cross_stack` — Legacy: siblings found via cross-stack scan
- `cross_stack_conflict` — Legacy: siblings have conflicting mounts
- `incomplete` — Single-service stack, no siblings found

### Service Classification
Hardcoded sets in `analyzer.py`: `ARR_APPS`, `DOWNLOAD_CLIENTS`, `MEDIA_SERVERS`.
Classification checks both service name and image name (case-insensitive substring match).

## Gotchas
- **Windows pytest:** Always use `-p no:capture` to avoid Rich/capture conflicts
- **Session state is ephemeral** — in-memory dict, lost on restart
- **`_session["pipeline"]`** must be invalidated when scan path changes
- **`compose_file_path`** in analysis results is the full path (needed for apply-fix)
- **Frontend XSS safety:** All user-derived content uses `textContent`, never `innerHTML`
- **UNC paths on Windows:** `os.path.commonpath` raises `ValueError` for UNC paths — tests guard with `sys.platform == "win32"`

## Ecosystem Strategy
Part of a 4-tool ecosystem: MapArr, ComposeArr, SubBrainArr, Apart.
Shared code extraction planned for Phase 15+ into a `shared/` directory.
Extraction targets: compose discovery, parsing, analysis, models, styles.
Cross-Claude communication via CLAUDE.md files and comprehensive code comments.

## Running
```bash
# Development
uvicorn backend.main:app --host 0.0.0.0 --port 9494 --reload

# Tests
pytest tests/ -v -p no:capture

# Docker
docker compose up --build
```

## Branch
`v1.0-web-pivot` — all web development happens here.
The `charm-tui-alpha` branch has a separate Go/Charm TUI at `maparr_charm/`.
