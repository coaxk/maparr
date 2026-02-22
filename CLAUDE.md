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
- `app.js` — ~6500 lines, entire frontend logic
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

### RPM Wizard (v1.5.0)
5-gate guided wizard for Remote Path Mapping as a "Quick Fix" alternative to
mount restructuring. Lives entirely in frontend `app.js` (`renderRpmWizard()`).
- Gate 1: Auto-detected mounts + overlap check (informational)
- Gate 2: User verifies DC category paths (gated on input)
- Gate 3: Calculated RPM entries displayed (review)
- Gate 4: Step-by-step apply instructions per *arr app (gated on checkboxes)
- Gate 5: Test verification (works/broken outcome)
Backend provides `rpm_mappings` in analysis response via `_calculate_rpm_mappings()`.

### Solution Track Selector
Cross-stack conflicts show two tracks: Quick Fix (RPM Wizard) and Proper Fix
(mount restructure). Track selector in `showCrossStackConflict()`. Defaults to
Quick Fix when RPM is possible, Proper Fix when host paths don't overlap.

### Pre-flight Override & Source of Truth
When user pastes an error, overrides pre-flight warning on a healthy stack:
- `state.preflightOverridden` flag tracks the override
- If ALL conflicts are `path_unreachable` type → stack is actually healthy
- Terminal lines retroactively modified: yellow `!` lines get strikethrough + dimmed
- Green `RESULT` banner dominates visual hierarchy
- Result card shows "This Stack Is Healthy" with context about the pasted error
- Key principle: NEVER report false issues, even if user did something dumb

### Apply Fix Pipeline Refresh
After Apply Fix writes corrected YAML:
1. Frontend calls `/api/pipeline-scan` to refresh cache
2. Backend safety net: if compose mtime > pipeline scanned_at, forces inline rescan
3. Pipeline majority root captured regardless of within-stack conflicts
4. All media services expanded as affected when pipeline override active

### Service Classification
Hardcoded sets in `analyzer.py`: `ARR_APPS`, `DOWNLOAD_CLIENTS`, `MEDIA_SERVERS`.
Classification checks both service name and image name (case-insensitive substring match).
Download clients include: qbittorrent, sabnzbd, nzbget, transmission, deluge,
rtorrent, jdownloader, aria2, flood, rdtclient.

### Quick-Switch Combobox
All 3 stack search inputs (fix mode filter, browse collapsed bar, bottom-of-card)
use shared `populateQuickSwitch()` + `wireQuickSwitchCombobox()` helpers.
Click to browse all stacks, type to filter. Shows health dots + service counts.

### Navigation
- `backToStackList()` — returns to full stack grid from analysis results
- "Show all stacks" link in collapsed bar uses same function
- Browse mode bottom actions include "Back to Stack List" button

## Gotchas
- **Windows pytest:** Always use `-p no:capture` to avoid Rich/capture conflicts
- **Session state is ephemeral** — in-memory dict, lost on restart
- **`_session["pipeline"]`** must be invalidated when scan path changes
- **`compose_file_path`** in analysis results is the full path (needed for apply-fix)
- **Frontend XSS safety:** All user-derived content uses `textContent`, never `innerHTML`
- **UNC paths on Windows:** `os.path.commonpath` raises `ValueError` for UNC paths — tests guard with `sys.platform == "win32"`

## Session Discipline
**Before every commit and at end of session**, update knowledge files:
1. `CLAUDE.md` (this file) — architecture, patterns, gotchas, key functions
2. `MEMORY.md` (global at `~/.claude/projects/C--DockerContainers/memory/MEMORY.md`) — cross-project state, user prefs, ecosystem strategy
Do this proactively. Don't wait to be asked. If you built it, document it.

## Ecosystem Strategy
Part of a 4-tool ecosystem: MapArr, ComposeArr, SubBrainArr, Apart.
Shared code extraction planned for Phase 15+ into a `shared/` directory.
Extraction targets: compose discovery, parsing, analysis, models, styles.
Cross-Claude communication via CLAUDE.md files and comprehensive code comments.
**Rumplestiltskin** — banked framework concept: extract ethos + methodology into pluggable analysis engine with domain plugins + ethos engine + output depth ladder.

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
