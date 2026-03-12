#!/bin/bash
# Build ELDER_COUNCIL_BRIEF.md by assembling all source files and metadata.
# Run from MapArr project root.

OUT="ELDER_COUNCIL_BRIEF.md"

cat > "$OUT" << 'HEADER'
# MAPARR v1.5.1 — ELDER COUNCIL REVIEW BRIEF

## 1. EXECUTIVE SUMMARY

**MapArr** is a Docker Compose path mapping diagnostic tool for the *arr ecosystem (Sonarr, Radarr, Lidarr, Prowlarr, etc.). It solves the #1 pain point for homelab users running Docker-based media automation: misconfigured volume paths that silently break imports, hardlinks, and cross-service communication.

**Target user:** Homelab enthusiasts running Docker *arr stacks — typically 5-30 containers across 1-10 compose files, on Linux, Unraid, Synology, or Windows/WSL2.

**Current status:** v1.5.1, pre-release, public-aimed. All planned features complete. 3 security audits passed. Beta-tagged.

**Tech stack:** Python 3.11 / FastAPI backend + vanilla HTML/CSS/JS frontend (~7000 LOC frontend, ~5000 LOC backend). No frontend framework. Distributed as a Docker container.

**Test summary:** 682 unit tests + 28 API contract tests + 45 Playwright E2E tests (38 component + 7 journey) + 3 Docker deployment tests = **758 tests across 4 layers**. All passing.

**Security posture:** 3 audits completed (2026-02-23, 2026-03-09, 2026-03-11). Path traversal prevention, input size limits, error message safety (no `str(e)` leaks), XSS prevention (textContent only), 3-tier rate limiting, subprocess list-form args only.

---

## 2. FULL PROJECT STRUCTURE

```
HEADER

# Step 1: Directory tree
find . -maxdepth 4 \
  -not -path '*/.git/*' \
  -not -path '*/__pycache__/*' \
  -not -path '*/.venv/*' \
  -not -path '*/.mypy_cache/*' \
  -not -path '*/maparr_charm/*' \
  -not -path '*/.worktrees/*' \
  -not -path '*/img/services/*' \
  -not -path '*/.pytest_cache/*' \
  | sort >> "$OUT"

cat >> "$OUT" << 'SECTION'
```

### File Counts by Extension
```
SECTION

find . \
  -not -path '*/.git/*' \
  -not -path '*/__pycache__/*' \
  -not -path '*/maparr_charm/*' \
  -not -path '*/.worktrees/*' \
  -not -path '*/.venv/*' \
  -type f | sed 's/.*\.//' | sort | uniq -c | sort -rn | head -20 >> "$OUT"

echo '```' >> "$OUT"
echo "" >> "$OUT"

# Total LOC
echo "**Total lines of code (Python + JS + CSS + HTML):**" >> "$OUT"
echo '```' >> "$OUT"
find . -type f \( -name "*.py" -o -name "*.js" -o -name "*.css" -o -name "*.html" \) \
  -not -path '*/.git/*' \
  -not -path '*/__pycache__/*' \
  -not -path '*/maparr_charm/*' \
  -not -path '*/.worktrees/*' \
  -not -path '*/.venv/*' \
  | xargs wc -l 2>/dev/null | tail -1 >> "$OUT"
echo '```' >> "$OUT"

cat >> "$OUT" << 'SECTION'

---

## 3. TECH STACK & DEPENDENCIES

### Python Dependencies (requirements.txt)
```
SECTION
cat requirements.txt >> "$OUT"
echo '```' >> "$OUT"
echo "" >> "$OUT"

echo "### Dev Dependencies (requirements-dev.txt)" >> "$OUT"
echo '```' >> "$OUT"
cat requirements-dev.txt >> "$OUT"
echo '```' >> "$OUT"
echo "" >> "$OUT"

echo "### Dockerfile" >> "$OUT"
echo '```dockerfile' >> "$OUT"
cat Dockerfile >> "$OUT"
echo '```' >> "$OUT"
echo "" >> "$OUT"

echo "### docker-compose.yml" >> "$OUT"
echo '```yaml' >> "$OUT"
cat docker-compose.yml >> "$OUT"
echo '```' >> "$OUT"
echo "" >> "$OUT"

echo "### docker-entrypoint.sh" >> "$OUT"
echo '```bash' >> "$OUT"
cat docker-entrypoint.sh >> "$OUT"
echo '```' >> "$OUT"
echo "" >> "$OUT"

echo "### Image Database" >> "$OUT"
IMAGE_COUNT=$(python -c "import json; print(len(json.load(open('data/images.json'))['images']))" 2>/dev/null || echo '?')
echo "- \`data/images.json\`: $(wc -l < data/images.json) lines, $IMAGE_COUNT images" >> "$OUT"
echo "" >> "$OUT"

if [ -f unraid/maparr.xml ]; then
  echo "### Unraid Template" >> "$OUT"
  echo '```xml' >> "$OUT"
  cat unraid/maparr.xml >> "$OUT"
  echo '```' >> "$OUT"
  echo "" >> "$OUT"
fi

cat >> "$OUT" << 'SECTION'

---

## 4. ARCHITECTURE OVERVIEW

### Entry Points

MapArr is a single-process FastAPI application served by Uvicorn. The frontend is served as static files from the same process.

- **Main entry:** `backend/main.py` — FastAPI app, all API routes, middleware, session state
- **Frontend:** `frontend/index.html` + `frontend/app.js` (~7000 LOC) + `frontend/styles.css`
- **Docker:** `docker-entrypoint.sh` handles PUID/PGID remapping, Docker socket group detection, then exec's uvicorn

### 15 API Routes (with Rate Limit Tiers)

| Route | Method | Rate Tier | Purpose |
|-------|--------|-----------|---------|
| `/` | GET | none | Serve frontend |
| `/api/parse-error` | POST | analysis | Parse pasted error text |
| `/api/discover-stacks` | GET | read | Auto-detect stacks directories |
| `/api/pipeline-scan` | POST | analysis | Full directory scan → pipeline |
| `/api/change-stacks-path` | POST | write | Update stacks root path |
| `/api/list-directories` | POST | read | Server-side directory browser |
| `/api/select-stack` | POST | read | Select a stack for analysis |
| `/api/analyze` | POST | analysis | Run 4-pass analysis on stack |
| `/api/smart-match` | POST | analysis | Match error to pipeline service |
| `/api/apply-fix` | POST | write | Apply single-file fix |
| `/api/apply-fixes` | POST | write | Apply multi-file batch fix |
| `/api/redeploy` | POST | write | Docker compose up -d |
| `/api/health` | GET | none | Health check |
| `/api/logs` | GET | read | Fetch log entries |
| `/api/logs/stream` | GET | none | SSE live log stream |

### Rate Limiter

3-tier sliding window rate limiter:
- **Write:** 10 req/min (apply-fix, change-path, redeploy)
- **Analysis:** 20 req/min (parse-error, pipeline-scan, analyze, smart-match)
- **Read:** 60 req/min (discover-stacks, list-directories, select-stack, logs)

### Data Flow

```
User Action → API Request → Backend Processing → JSON Response → Frontend Render

Pipeline Scan Flow:
  /api/pipeline-scan → discovery.py (find compose files)
                      → parser.py (parse YAML)
                      → resolver.py (resolve docker compose config)
                      → pipeline.py (build service map, run health checks)
                      → cross_stack.py (cross-stack analysis)
                      → JSON response → renderDashboard()

Analysis Flow:
  /api/analyze → analyzer.py (4-pass analysis)
               Pass 1: Path conflicts (mount comparison)
               Pass 2: Hardlink breakage (cross-device detection)
               Pass 3: Permissions (PUID/PGID/UMASK)
               Pass 4: Platform recommendations (WSL2, NFS, Windows)
               → Solution generation (YAML patches, env patches)
               → Fix plan generation (per-file apply instructions)
               → JSON response → showAnalysisResult()
```

### Session State (In-Memory)

```python
_session = {
    "pipeline": None,           # Cached PipelineResult from last scan
    "custom_stacks_path": None, # User-specified stacks root
    "selected_stack": None,     # Currently selected stack
    "parsed_error": None,       # Last parsed error text
}
```

No persistence. State resets on server restart. Designed for single-user local network use.

SECTION

# API Routes grep
echo '### API Route Definitions (from source)' >> "$OUT"
echo '```' >> "$OUT"
grep -n "@app\.\(get\|post\|put\|delete\|patch\)" backend/main.py | grep -v "test\|mock" >> "$OUT"
echo '```' >> "$OUT"

cat >> "$OUT" << 'SECTION'

---

## 5. COMPLETE SOURCE CODE

### Backend Python Modules

SECTION

# All backend source files
for f in \
  backend/main.py \
  backend/pipeline.py \
  backend/analyzer.py \
  backend/apply_multi.py \
  backend/redeploy.py \
  backend/image_registry.py \
  backend/cross_stack.py \
  backend/discovery.py \
  backend/resolver.py \
  backend/parser.py \
  backend/smart_match.py \
  backend/mounts.py \
  backend/log_handler.py; do
  if [ -f "$f" ]; then
    LINES=$(wc -l < "$f")
    echo "" >> "$OUT"
    echo "#### $f ($LINES lines)" >> "$OUT"
    echo '```python' >> "$OUT"
    cat "$f" >> "$OUT"
    echo '```' >> "$OUT"
  fi
done

echo "" >> "$OUT"
echo "### Frontend Files" >> "$OUT"

for f in frontend/index.html frontend/app.js frontend/styles.css; do
  if [ -f "$f" ]; then
    LINES=$(wc -l < "$f")
    EXT="${f##*.}"
    echo "" >> "$OUT"
    echo "#### $f ($LINES lines)" >> "$OUT"
    echo "\`\`\`$EXT" >> "$OUT"
    cat "$f" >> "$OUT"
    echo '```' >> "$OUT"
  fi
done

echo "" >> "$OUT"
echo "### Utility Scripts" >> "$OUT"

find ./scripts -name "*.py" -not -path '*/__pycache__/*' 2>/dev/null | sort | while read f; do
  LINES=$(wc -l < "$f")
  echo "" >> "$OUT"
  echo "#### $f ($LINES lines)" >> "$OUT"
  echo '```python' >> "$OUT"
  cat "$f" >> "$OUT"
  echo '```' >> "$OUT"
done

cat >> "$OUT" << 'SECTION'

---

## 6. TEST SUITE

### 4-Layer Test Architecture

| Layer | Type | Count | Tooling | Purpose |
|-------|------|-------|---------|---------|
| Unit | pytest | 682 | pytest | Logic validation — analyzers, pipeline, parsers |
| API Contracts | httpx TestClient | 28 | pytest + httpx | Response shape validation, no server needed |
| Component | Playwright | 38 | pytest-playwright | DOM assertions — elements exist, visible, correct |
| Journey | Playwright | 7 | pytest-playwright | End-to-end workflows — click X, expect Y |
| Docker | subprocess | 3 | pytest (skipped w/o Docker) | Build, port, healthcheck |

### Test Results (Latest Run)
```
Unit tests:    682 passed in 11.60s
API contracts:  28 passed in 2.58s
Playwright:     45 passed (38 component + 7 journey)
Docker:          3 skipped (requires Docker)
Total:         758 tests, 0 failures
```

### E2E Test Infrastructure

SECTION

echo '```python' >> "$OUT"
cat tests/e2e/conftest.py >> "$OUT"
echo '```' >> "$OUT"
echo "" >> "$OUT"

echo "### E2E Playwright Test List" >> "$OUT"
echo '```' >> "$OUT"
grep -n "def test_" tests/e2e/test_components.py tests/e2e/test_journeys.py tests/e2e/test_docker.py 2>/dev/null >> "$OUT"
echo '```' >> "$OUT"
echo "" >> "$OUT"

echo "### All Test Files" >> "$OUT"

find . -name "test_*.py" -not -path '*/.git/*' -not -path '*/__pycache__/*' -not -path '*/.worktrees/*' -not -path '*/.venv/*' \
  | sort | while read f; do
    LINES=$(wc -l < "$f")
    echo "" >> "$OUT"
    echo "#### $f ($LINES lines)" >> "$OUT"
    echo '```python' >> "$OUT"
    cat "$f" >> "$OUT"
    echo '```' >> "$OUT"
  done

cat >> "$OUT" << 'SECTION'

---

## 7. SECURITY ANALYSIS SURFACE

### Docker Socket Access
SECTION

echo '```' >> "$OUT"
grep -rn "docker\|socket\|unix\|pipe\|DOCKER_HOST\|docker.sock" \
  --include="*.py" \
  . 2>/dev/null | grep -v ".venv" | grep -v __pycache__ | grep -v "test\|#" | head -40 >> "$OUT"
echo '```' >> "$OUT"
echo "" >> "$OUT"

echo "### File Operations" >> "$OUT"
echo '```' >> "$OUT"
grep -rn "open(\|os\.path\|pathlib\|shutil\|os\.remove\|os\.rename" \
  --include="*.py" \
  . 2>/dev/null | grep -v ".venv" | grep -v __pycache__ | grep -v "test" | head -40 >> "$OUT"
echo '```' >> "$OUT"
echo "" >> "$OUT"

echo "### Path Validation" >> "$OUT"
echo '```' >> "$OUT"
grep -rn "relative_to\|_is_path_within\|_BLOCKED_PREFIXES\|COMPOSE_FILENAMES\|_get_stacks_root" \
  --include="*.py" \
  . 2>/dev/null | grep -v ".venv" | grep -v __pycache__ | head -30 >> "$OUT"
echo '```' >> "$OUT"
echo "" >> "$OUT"

echo "### User Input Handling" >> "$OUT"
echo '```' >> "$OUT"
grep -rn "request\.json\|body\.get\|\.strip()\|size.*limit\|100_000\|1_000_000" \
  --include="*.py" \
  . 2>/dev/null | grep -v ".venv" | grep -v __pycache__ | grep -v "test" | head -40 >> "$OUT"
echo '```' >> "$OUT"
echo "" >> "$OUT"

echo "### Subprocess Calls" >> "$OUT"
echo '```' >> "$OUT"
grep -rn "subprocess\|os\.system\|shell=True\|Popen" \
  --include="*.py" \
  . 2>/dev/null | grep -v ".venv" | grep -v __pycache__ | head -20 >> "$OUT"
echo '```' >> "$OUT"
echo "" >> "$OUT"

echo "### YAML Handling" >> "$OUT"
echo '```' >> "$OUT"
grep -rn "yaml\.\|safe_load\|safe_dump\|YAMLError" \
  --include="*.py" \
  . 2>/dev/null | grep -v ".venv" | grep -v __pycache__ | grep -v "test" | head -30 >> "$OUT"
echo '```' >> "$OUT"
echo "" >> "$OUT"

echo "### XSS Prevention (Frontend)" >> "$OUT"
echo '```' >> "$OUT"
echo "innerHTML usages:" >> "$OUT"
grep -n "innerHTML\|outerHTML\|insertAdjacentHTML\|document\.write" \
  frontend/app.js 2>/dev/null >> "$OUT"
echo "" >> "$OUT"
echo "textContent usage count: $(grep -c 'textContent' frontend/app.js)" >> "$OUT"
echo '```' >> "$OUT"
echo "" >> "$OUT"

echo "### Error Message Safety" >> "$OUT"
echo '```' >> "$OUT"
grep -rn "_json_error_detail\|_categorize_os_error\|_relative_path_display\|friendlyError\|str(e)" \
  --include="*.py" --include="*.js" \
  . 2>/dev/null | grep -v ".venv" | grep -v __pycache__ | grep -v "test" | head -30 >> "$OUT"
echo '```' >> "$OUT"
echo "" >> "$OUT"

echo "### Deployment Security Documentation" >> "$OUT"
if [ -f DEPLOYMENT_SECURITY.md ]; then
  echo '```markdown' >> "$OUT"
  cat DEPLOYMENT_SECURITY.md >> "$OUT"
  echo '```' >> "$OUT"
fi

cat >> "$OUT" << 'SECTION'

---

## 8. PERFORMANCE & SCALABILITY

### Caching / Session State
SECTION

echo '```' >> "$OUT"
grep -rn "_session\|cache\|lru_cache\|functools" \
  --include="*.py" \
  . 2>/dev/null | grep -v ".venv" | grep -v __pycache__ | grep -v "test" | head -30 >> "$OUT"
echo '```' >> "$OUT"
echo "" >> "$OUT"

echo "### Async / Concurrency" >> "$OUT"
echo '```' >> "$OUT"
grep -rn "async def\|await \|asyncio\|threading\|run_in_executor" \
  --include="*.py" \
  . 2>/dev/null | grep -v ".venv" | grep -v __pycache__ | grep -v "test" | head -30 >> "$OUT"
echo '```' >> "$OUT"
echo "" >> "$OUT"

echo "### SSE / Streaming" >> "$OUT"
echo '```' >> "$OUT"
grep -rn "SSE\|EventSource\|StreamingResponse\|event-stream" \
  --include="*.py" --include="*.js" \
  . 2>/dev/null | grep -v ".venv" | head -20 >> "$OUT"
echo '```' >> "$OUT"
echo "" >> "$OUT"

echo "### Performance Profile" >> "$OUT"
if [ -f PERF_RESULTS.md ]; then
  echo '```markdown' >> "$OUT"
  cat PERF_RESULTS.md >> "$OUT"
  echo '```' >> "$OUT"
fi

cat >> "$OUT" << 'SECTION'

---

## 9. DOCUMENTATION STATUS

### README.md
SECTION

echo '```markdown' >> "$OUT"
cat README.md >> "$OUT"
echo '```' >> "$OUT"
echo "" >> "$OUT"

echo "### CHANGELOG.md" >> "$OUT"
echo '```markdown' >> "$OUT"
cat CHANGELOG.md >> "$OUT"
echo '```' >> "$OUT"
echo "" >> "$OUT"

echo "### QUICK_START.md" >> "$OUT"
echo '```markdown' >> "$OUT"
cat QUICK_START.md >> "$OUT"
echo '```' >> "$OUT"
echo "" >> "$OUT"

echo "### TROUBLESHOOTING.md" >> "$OUT"
echo '```markdown' >> "$OUT"
cat TROUBLESHOOTING.md >> "$OUT"
echo '```' >> "$OUT"
echo "" >> "$OUT"

echo "### CLAUDE.md (Project Knowledge)" >> "$OUT"
echo '```markdown' >> "$OUT"
cat CLAUDE.md >> "$OUT"
echo '```' >> "$OUT"
echo "" >> "$OUT"

echo "### TODO/FIXME/HACK Comments" >> "$OUT"
echo '```' >> "$OUT"
grep -rn "TODO\|FIXME\|HACK\|XXX\|BUG\|OPTIMIZE" \
  --include="*.py" --include="*.js" \
  . 2>/dev/null | grep -v ".venv" | grep -v __pycache__ | grep -v ".worktrees" | head -40 >> "$OUT"
echo '```' >> "$OUT"
echo "" >> "$OUT"

echo "### Pre-Release Reports" >> "$OUT"
for f in PRERELEASE_REPORT.md ERROR_MESSAGES_REPORT.md CROSSBROWSER_RESULTS.md \
         DEPLOYMENT_RESULTS.md BACKLOG.md V151_IMPLEMENTATION_REPORT.md; do
  if [ -f "$f" ]; then
    echo "" >> "$OUT"
    echo "#### $f" >> "$OUT"
    echo '```markdown' >> "$OUT"
    cat "$f" >> "$OUT"
    echo '```' >> "$OUT"
  fi
done

cat >> "$OUT" << 'SECTION'

---

## 10. DEVELOPMENT HISTORY

### Git Log (last 50 commits)
```
SECTION

git log --oneline -50 >> "$OUT"

cat >> "$OUT" << 'SECTION'
```

### Git Branches
```
SECTION

git branch -a >> "$OUT"

cat >> "$OUT" << 'SECTION'
```

### Recent Changes (last 14 days)
```
SECTION

git log --since="14 days ago" --name-only --pretty=format:"[%ad] %s" --date=short | head -120 >> "$OUT"

cat >> "$OUT" << 'SECTION'
```

### Most Changed Files
```
SECTION

git log --name-only --pretty=format: | sort | uniq -c | sort -rn | head -20 >> "$OUT"

cat >> "$OUT" << 'SECTION'
```

### Uncommitted Changes
```
SECTION

git status --short | head -20 >> "$OUT"

cat >> "$OUT" << 'SECTION'
```

---

## 11. UI/UX LAYER

### Frontend Architecture

MapArr uses a single-page vanilla JS frontend (~7000 LOC in `app.js`). No framework, no build step. Key patterns:

- **State management:** Single `const state = {}` object with properties for pipeline data, UI state, selections
- **Rendering:** Imperative DOM manipulation via `document.createElement()` + `textContent` (XSS-safe)
- **Navigation:** Show/hide sections by ID — no router. `show(id)` / `hide(id)` helpers.
- **Events:** `addEventListener` throughout (CSP-ready, no inline handlers)
- **Error handling:** `friendlyError()` normalizes all error displays
- **Icons:** 177 bundled SVG/PNG service icons with fuzzy name matching

### Key Frontend Functions
```
SECTION

grep -n "^function \|^async function " frontend/app.js | head -80 >> "$OUT"

echo '```' >> "$OUT"
echo "" >> "$OUT"

echo "### CSS Structure" >> "$OUT"
echo '```' >> "$OUT"
grep -n "^/\*\|^--\|^:root\|^@media\|^\.health-\|^\.service-\|^\.conflict-\|^\.fix-" frontend/styles.css | head -60 >> "$OUT"
echo '```' >> "$OUT"
echo "" >> "$OUT"

echo "### Service Icons" >> "$OUT"
ICON_COUNT=$(ls frontend/img/services/ 2>/dev/null | wc -l)
echo "- **$ICON_COUNT bundled icons** in \`frontend/img/services/\`" >> "$OUT"
echo '```' >> "$OUT"
ls frontend/img/services/ 2>/dev/null | head -40 >> "$OUT"
echo "... and more" >> "$OUT"
echo '```' >> "$OUT"
echo "" >> "$OUT"

echo "### Frontend State Management" >> "$OUT"
echo '```javascript' >> "$OUT"
grep -n "const state\|state\.\|_session\[" frontend/app.js | head -30 >> "$OUT"
echo '```' >> "$OUT"

# Section 12: Elder Council Mission
cat >> "$OUT" << 'SECTION12'

---

# ELDER COUNCIL — YOUR MISSION

You are one of 5 independent AI Elder reviewers conducting a comprehensive pre-release review of **MapArr v1.5.1**.

The other Elders reviewing this same brief are: **DeepSeek, Gemini, ChatGPT, Grok, and Perplexity.**
Your review is **independent**. Do not hedge. Do not assume another Elder caught something. Your verdict stands alone.

MapArr is a **public-aimed Docker path mapping tool** for the *arr ecosystem (Sonarr, Radarr, Lidarr, etc.).
It solves the #1 pain point for homelab users: misconfigured volume paths that silently break media automation.
It is built with a **Python 3.11/FastAPI backend** and a **vanilla HTML/CSS/JS frontend** (no framework, ~7000 LOC), distributed as a Docker container.

**Key capabilities:**
- 4-pass analysis engine: path conflicts, hardlink breakage, permissions, platform recommendations
- 219-image recognition database with 7-family classification
- Pipeline-first architecture: full-directory scan, unified media service map
- RPM Wizard: guided Remote Path Mapping as alternative to mount restructuring
- Multi-file Apply Fix with backup and diff preview
- Paste an error → auto-drill to the fix

**Security context:** Users run MapArr with Docker socket access (read-only). It reads and writes Docker Compose files. It runs `docker compose config` via subprocess. It has no authentication (designed for local network use).

**Your review must be exhaustive. No politeness. No held punches. This is going public.**

---

## REVIEW MANDATE — ALL 4 AREAS REQUIRED

### AREA 1: SECURITY REVIEW
Scrutinise every security surface. MapArr is public-aimed. Users will run this with Docker socket access.

Questions to answer:
- Is Docker socket access safe? Could read-only socket mount be exploited?
- Are file operations protected from path traversal? Review `_is_path_within_stacks()`, `COMPOSE_FILENAMES`, `_BLOCKED_PREFIXES`.
- Is user input validated and sanitised everywhere? Review size limits, JSON parse handling.
- Are environment variables handled securely?
- Is there any SSRF risk via DOCKER_HOST manipulation?
- Are there injection risks in YAML parsing? Is `yaml.safe_load()` used everywhere?
- Are backup operations safe from overwrite races?
- Do error messages leak internal paths or system info? Review `_categorize_os_error()`, `_json_error_detail()`.
- Are subprocess calls safe? Is `shell=True` used anywhere?
- Is the rate limiter bypassable?
- What are the top 3 most critical security fixes required?

Rate: CRITICAL / HIGH / MEDIUM / LOW for each finding.

---

### AREA 2: CODE QUALITY & OPTIMIZATION
Review the codebase as a senior engineer doing a production readiness check.

Questions to answer:
- Are there dead code paths, unused imports, unreachable functions?
- Are hot paths optimised? Any obvious O(n^2) or worse in the pipeline scan?
- Are async tasks cleaned up properly (SSE streams, event loops)?
- Is error handling consistent and complete?
- Is the code idiomatic Python? Are there anti-patterns?
- Are there any race conditions in the session state?
- Is logging appropriate (not too much, not too little)?
- Is the frontend JS well-structured for ~7000 LOC without a framework?
- What are the top 5 refactors that would most improve quality?

Rate each finding by effort (Low/Med/High) and impact (Low/Med/High).

---

### AREA 3: ARCHITECTURE & DESIGN
Assess the overall architecture for correctness, scalability, and maintainability.

Questions to answer:
- Is the separation of concerns clean (backend modules / frontend / data)?
- Does the API design make sense for this tool's purpose?
- Is the data model correct for representing path mappings and conflicts?
- Are there tight couplings that will create future pain?
- Is the pipeline scan architecture robust enough for production?
- How will this scale if users have 50+ stacks? 100+? 200+?
- Is the in-memory session state approach correct, or should there be persistence?
- Are there missing abstractions that would simplify the code?
- Is the 4-pass analysis engine well-factored?
- What architectural decisions will cause regret at v2.0?

---

### AREA 4: USER EXPERIENCE & PRODUCT
Review the tool as a homelab enthusiast who just discovered it.

Questions to answer:
- Is the pipeline dashboard intuitive for a first-time user?
- Are error messages actually helpful to a non-developer user?
- Does the paste-error → auto-drill flow work well?
- Is the RPM Wizard clear and confidence-inspiring?
- Is the Apply Fix flow (backup, diff preview, apply) trustworthy?
- Are edge cases (no Docker, no stacks found, partial configs) handled gracefully?
- Is the visual design polished enough for a public release?
- What would a first-time user be confused by?
- What's missing that users will immediately request?
- Does it feel like a professional tool or a hobby project?
- What single change would most improve the user experience?

---

## DELIVERABLE FORMAT

Structure your review exactly as follows:

```
# ELDER COUNCIL REVIEW — [Your Elder Name]

## SECURITY FINDINGS
[List each finding with severity rating]

## CODE QUALITY FINDINGS
[List each finding with effort/impact ratings]

## ARCHITECTURE FINDINGS
[List each finding]

## UX/PRODUCT FINDINGS
[List each finding]

## PRIORITY MATRIX
[Top 10 issues across all categories, ranked by must-fix-before-public-release]

## SHIP VERDICT
[ ] NOT READY — Critical blockers found (list them)
[ ] NEARLY READY — Minor fixes needed (list them, max 1 day work)
[ ] READY TO SHIP — Approve v1.5.1 release
[ ] EXCEPTIONAL — Ready + recommended improvements for v1.6

## YOUR TOP 3 RECOMMENDATIONS
1. [Most important thing to fix/change]
2. [Second most important]
3. [Third most important]
```
SECTION12

echo "" >> "$OUT"
echo "--- END OF ELDER COUNCIL BRIEF ---" >> "$OUT"

# Report size
LINES=$(wc -l < "$OUT")
SIZE=$(du -h "$OUT" | cut -f1)
echo "✅ Built $OUT: $LINES lines, $SIZE"
