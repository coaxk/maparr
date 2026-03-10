# MapArr — Web Project (v2.0.0-dev)

## What This Is
Path Mapping Problem Solver for Docker *arr apps. Web UI with FastAPI backend.
Analyzes Docker Compose volume mounts, detects hardlink-breaking configurations,
and generates specific fixes following the TRaSH Guides pattern.
Recognizes 218+ Docker images across 7 families via a JSON Image DB.

## Stack
- **Backend:** Python 3.11, FastAPI (>=0.115.0), uvicorn (>=0.30.0), PyYAML (>=6.0.2), python-multipart (>=0.0.18)
- **Frontend:** Vanilla HTML/CSS/JS (single-page, no framework, no build step)
- **Tests:** pytest (682 tests), run with `pytest tests/ -p no:capture` on Windows
- **Docker:** Multi-stage build, gosu for PUID/PGID, Docker CLI + compose plugin

## Architecture

### Backend Modules (`backend/`)
| File | Purpose |
|------|---------|
| `main.py` | FastAPI app, 13 API routes, session state, rate limiter middleware, registry init |
| `apply_multi.py` | Batch apply-fix: validate all → backup all → write all (atomic-ish) |
| `redeploy.py` | Docker Compose redeploy via subprocess (list-form args, 120s timeout) |
| `image_registry.py` | **Image DB** — `ImageRegistry` class, JSON-driven service classification |
| `pipeline.py` | **Core innovation** — full-directory scan, unified media service map |
| `analyzer.py` | Per-stack 4-pass analysis: path conflicts, hardlinks, permissions, platform |
| `cross_stack.py` | Sibling scanning for single-service stacks (legacy, pipeline supersedes) |
| `discovery.py` | Compose file filesystem scanner |
| `resolver.py` | Compose resolution (docker compose config + manual .env fallback) |
| `parser.py` | Error text parser (service, path, error type extraction, multi-error split, dedup) |
| `smart_match.py` | Intelligent error-to-stack matching with scoring |
| `mounts.py` | Mount type classification (NFS, CIFS, WSL2, local) |
| `log_handler.py` | In-memory ring buffer + SSE streaming for logs |

### Frontend (`frontend/`)
- `index.html` — Pipeline Dashboard SPA: service groups, health banner, conflict cards, paste bar
- `app.js` — ~6800 lines, pipeline dashboard + analysis detail cards (old mode UI removed)
- `styles.css` — Full CSS with dark theme, role-colored service groups, fix plan rows
- `img/services/` — 177 bundled service icons (SVG/PNG) (CC-BY-4.0 from dashboard-icons), `generic.svg` fallback

### Data & Scripts
| File | Purpose |
|------|---------|
| `data/images.json` | Generated Image DB (218 images, 7 families) — committed to repo |
| `data/custom-images.json` | Optional user overrides (mounted via compose) |
| `scripts/seed_images.py` | Dev-time seed script: LSIO fleet API → merge manual → write images.json |
| `scripts/manual_entries.json` | Hand-curated families + non-LSIO images for seed merge |

### Docker & Deployment Files
| File | Purpose |
|------|---------|
| `Dockerfile` | Multi-stage build: Docker CLI, gosu, non-root user |
| `docker-entrypoint.sh` | PUID/PGID remapping via gosu, socket group detection |
| `docker-compose.yml` | Production compose with log rotation, PUID/PGID, socket proxy |
| `QUICK_START.md` | Platform guides: Linux, Unraid, Synology, macOS, Windows/WSL2, Portainer |
| `unraid/maparr.xml` | Unraid Community Applications template |

### API Endpoints
| Method | Route | Purpose | Rate Limit |
|--------|-------|---------|------------|
| GET | `/api/health` | Health check + version | none |
| POST | `/api/parse-error` | Extract service/path/error from pasted text | 60/min |
| GET | `/api/discover-stacks` | Scan for compose stacks | 60/min |
| POST | `/api/pipeline-scan` | Full-directory media pipeline scan | 20/min |
| POST | `/api/change-stacks-path` | Runtime scan path change | 10/min |
| POST | `/api/select-stack` | Store selected stack | 60/min |
| POST | `/api/analyze` | Full stack analysis (4-pass) | 20/min |
| POST | `/api/smart-match` | Error-to-stack matching | 60/min |
| POST | `/api/apply-fix` | Write corrected YAML (with backup) | 10/min |
| POST | `/api/apply-fixes` | Batch multi-file apply (validate all → backup → write) | 10/min |
| POST | `/api/redeploy` | Docker Compose redeploy (up -d with timeout) | 10/min |
| GET | `/api/logs` | Fetch log entries | 60/min |
| GET | `/api/logs/stream` | SSE live log stream | 60/min |

## Key Patterns

### Pipeline-First Analysis
The pipeline scans the entire root directory on boot, builds a unified map of all
media services (role, mount paths), and caches the result in `_session["pipeline"]`.
Per-stack analysis receives this as `pipeline_context` — no more isolated analysis.

**Cluster layout discovery:** When a subdirectory has no compose file, the scanner
checks one level deeper for compose files (max depth 2). This detects Dockhand/Portainer/
DockSTARTer layouts where each service has its own subfolder. Directories with their
own compose file are never cluster-scanned (no double counting).

### 4-Pass Analysis Engine
1. **Path conflicts** — separate mount trees, inconsistent host paths, unreachable paths
2. **Hardlink breakage** — cross-filesystem mounts, remote filesystems (NFS/CIFS)
3. **Permissions** — PUID/PGID mismatch, missing PUID/PGID, root execution, UMASK inconsistency
4. **Platform recommendations** — WSL2 performance, mixed mount types, Windows paths

### Session State
```python
_session = {
    "parsed_error": None,
    "selected_stack": None,
    "pipeline": None,  # Cached PipelineResult.to_dict()
}
```
Invalidated when stacks path changes.

### Rate Limiting
In-memory sliding window rate limiter (`RateLimiter` class in main.py):
- Three tiers: write (10/min), analysis (20/min), read (60/min)
- Skips /api/health and static files
- Per-IP tracking with periodic cleanup (every 5 minutes)
- Returns 429 with Retry-After header

### Fetch Timeouts (Frontend)
All fetch calls use AbortController with tiered timeouts:
- 10s: health, parse-error, discover-stacks, change-stacks-path, logs
- 30s: pipeline-scan, smart-match, apply-fix
- 60s: analyze (slow compose resolution through socket proxies)
- 15s: external GitHub API calls

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
- Key principle: NEVER report false issues, even if user did something dumb

### Service Icons
`SERVICE_ICONS` constant in app.js maps 140+ service names to bundled icons (SVG/PNG/ICO).
`getServiceIconUrl()` does exact match → fuzzy partial match → `generic.svg` fallback.
Icons sourced from homarr-labs/dashboard-icons (CC-BY-4.0), see `img/services/ATTRIBUTION.md`.

### Apply Fix (Cat A + Cat B) — Multi-File
Apply Fix works for both volume restructuring (Cat A) and permission env fixes (Cat B).
Both tabs have Copy + Apply buttons. Backend `_patch_original_env()` patches the user's
full compose file (not the snippet), chains with volume patches for mixed A+B stacks.

**Multi-file fix plans** (v2.0): `fix_plans` array on `AnalysisResult` bundles per-file patches.
- `_build_fix_plans()` — single-file plan builder (one entry with corrected_yaml, changed_services, category)
- `_build_fix_plans_multi()` — reads sibling compose files from pipeline context via `compose_file_full`
- `PipelineService.to_dict()` exposes `compose_file_full` (full filesystem path)
- Frontend `generateFixPlans()` prefers `fix_plans` from response, falls back to per-stack API
- Adaptive labels: "Apply Fix" (1 file) vs "Apply All Fixes" (N files)
- Unified batch: frontend always calls `/api/apply-fixes`, even for single files

After Apply Fix writes corrected YAML:
1. Frontend calls `/api/pipeline-scan` to refresh cache
2. Backend safety net: if compose mtime > pipeline scanned_at, forces inline rescan
3. Pipeline majority root captured regardless of within-stack conflicts
4. All media services expanded as affected when pipeline override active

### Image DB & Service Classification
`ImageRegistry` in `backend/image_registry.py` replaces all hardcoded service lists.
Two-layer JSON at `data/images.json` (218 images, 7 families), seeded from LSIO fleet API.

**Classification priority (3-pass):**
1. Image string → `patterns` (substring, case-insensitive) — precise
2. Service name → `keywords` (substring, longest-first, first-position-wins) — fuzzy
3. No match → `{role: "other", family: None, hardlink_capable: False}`

**Key internals:**
- `_by_pattern` / `_by_keyword` indexes sorted longest-first (prevents "nzb" stealing "nzbget")
- `_family_by_pattern` index for family-level prefix matching (e.g., `hotio/` → Hotio UID/GID)
- `get_registry()` singleton in `image_registry.py` (not `main.py`) to avoid circular imports
- `__getattr__` in `analyzer.py` provides backward-compat `ARR_APPS`, `DOWNLOAD_CLIENTS`, etc.
- `_identify_image_family()` wraps dict results in `SimpleNamespace` for attribute access

**Data pipeline (dev-time only, zero runtime API calls):**
- `scripts/seed_images.py` pulls LSIO fleet → merges `scripts/manual_entries.json` → writes `data/images.json`
- `scripts/manual_entries.json` = hand-curated families (7) + non-LSIO images (23)
- `data/custom-images.json` (optional, user-mounted) merges at boot

**Roles:** `arr`, `download_client`, `media_server`, `request`, `other`
**Families:** linuxserver, hotio, jlesage, binhex, official_plex, official_jellyfin, seerr

### Pipeline Dashboard (v2.0)
Service-first UI replacing the old stack-grid/mode-selector. All media services
grouped by role (arr, download_client, media_server, request, other) with:
- **Health banner**: aggregate pipeline health, Fix All shortcut
- **Service rows**: health dot + name + family + file location, expandable detail
- **Conflict cards**: severity-badged issues with multi-file fix plans
- **Fix plans**: per-file rows with checkboxes, Apply/Apply All, YAML preview with diff
- **Redeploy prompt**: role-based risk warnings, Docker Compose up -d or manual commands
- **Paste bar**: sticky bottom, paste an error → highlights matching services + scrolls to conflict
- **Three-state health**: `healthy` | `issue` | `awaiting` (fix applied, awaiting rescan)
- **Directory selection**: inline header path editor, first-launch welcome screen

**Key functions in app.js:**
`runPipelineScan()` → `renderDashboard()` → `renderServiceGroups()` / `renderConflictCards()`
`generateFixPlans()` → `renderFixPlan()` → `applySingleFix()` / `applyAllFixes()`
`showRedeployPrompt()` → `doRedeploy()` / `showManualRedeploy()`
`enablePasteBar()` → `handlePasteError()` → `highlightServices()`

### Quick-Switch Combobox
All 3 stack search inputs (fix mode filter, browse collapsed bar, bottom-of-card)
use shared `populateQuickSwitch()` + `wireQuickSwitchCombobox()` helpers.
Click to browse all stacks, type to filter. Shows health dots + service counts.

### Navigation
- `backToDashboard()` — returns to pipeline dashboard from analysis detail cards
- Analysis card bottom actions use same function for all back-navigation
- Old mode-based functions (backToStackList, analyzeAnother) removed in v2.0

### Multi-Error Detection
`parse_errors()` splits pasted text on double-newlines, log-level prefixes, and
repeated error prefixes. Near-duplicate dedup via (service, path, error_type) tuple.
CRLF normalized before splitting (Windows clipboard compatibility).

### Stack Cards — Last Scanned
`state.lastAnalyzed` maps stack paths to timestamps. `renderStackItem()` shows
relative time ("analyzed 2m ago") via `formatRelativeTime()` helper. Updated on
every successful analysis completion.

## Security
- **Path traversal prevention:** `_is_path_within_stacks()` with `require_root` param for writes
- **Write boundary:** Apply Fix requires `MAPARR_STACKS_PATH` to be set
- **Compose filename whitelist:** Only writes to `COMPOSE_FILENAMES` set
- **System directory denylist:** `/etc`, `/proc`, `/sys`, `/dev`, `/boot`, `/sbin`, `/root`, `/home`, `C:\Windows`, `C:\Program Files`
- **XSS prevention:** All user content via `textContent`, zero `innerHTML` with untrusted data
- **CSP readiness:** All inline onclick handlers migrated to addEventListener
- **Safe YAML:** `yaml.safe_load()` only
- **No shell injection:** Subprocess uses list-form args, never `shell=True`
- **Bounded resources:** SSE queue maxsize=100, exponential backoff 5s→60s
- **Rate limiting:** In-memory sliding window, three tiers, 429 with Retry-After
- **Dependency hygiene:** All deps pinned to minimum safe versions, CVE-2024-47874 patched

## Gotchas
- **Windows pytest:** Always use `-p no:capture` to avoid Rich/capture conflicts
- **Session state is ephemeral** — in-memory dict, lost on restart
- **`_session["pipeline"]`** must be invalidated when scan path changes
- **`compose_file_path`** in analysis results is the full path (needed for apply-fix)
- **Frontend XSS safety:** All user-derived content uses `textContent`, never `innerHTML`
- **UNC paths on Windows:** `os.path.commonpath` raises `ValueError` for UNC paths — tests guard with `sys.platform == "win32"`
- **Batch test failures:** 27 API tests fail in batch mode (session state bleed from path security checks) — pass individually. Pre-existing issue.
- **CRLF:** `split_errors()` normalizes `\r\n` → `\n` before regex split (Windows paste)

## Session Discipline
**Before every commit and at end of session**, update knowledge files:
1. `CLAUDE.md` (this file) — architecture, patterns, gotchas, key functions
2. `MEMORY.md` (global at `~/.claude/projects/C--DockerContainers/memory/MEMORY.md`) — cross-project state, user prefs, ecosystem strategy
Do this proactively. Don't wait to be asked. If you built it, document it.

## Ecosystem Strategy
Part of a 3-tool ecosystem: MapArr, ComposeArr, SubBrainArr.
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

## Environment Variables
| Variable | Default | Description |
|----------|---------|-------------|
| `MAPARR_PORT` | `9494` | Port to run on |
| `MAPARR_STACKS_PATH` | `/stacks` | Path to scan for compose files |
| `DOCKER_SOCKET` | `/var/run/docker.sock` | Docker socket path |
| `DOCKER_HOST` | (none) | Socket proxy address (e.g., `tcp://socket-proxy:2375`) |
| `LOG_LEVEL` | `info` | Logging level |
| `PUID` | `1000` | User ID to run as |
| `PGID` | `1000` | Group ID to run as |

## Branch
`feature/pipeline-dashboard` — Pipeline Dashboard v2.0 development branch.
`main` — stable v1.5.0 (merged from v1.0-web-pivot, 2026-03-08).
The Go/Charm TUI lives at `maparr_charm/` (embedded repo, separate Go module).
