# MAPARR — ELDER COUNCIL REVIEW PREP
## Claude Code Instructions: Full Codebase Intelligence Extraction

**Purpose:** Extract everything needed to prepare a comprehensive AI Elder Council review brief for MapArr v1.5.1.
**Run this in:** Claude Code, from the MapArr project root directory.
**Output:** A self-contained `ELDER_COUNCIL_BRIEF.md` file ready to submit to the 5 Elder AIs:

| Elder | Platform | URL |
|-------|----------|-----|
| Elder DeepSeek | DeepSeek Chat | https://chat.deepseek.com |
| Elder Gemini | Google Gemini | https://gemini.google.com |
| Elder ChatGPT | OpenAI ChatGPT | https://chatgpt.com |
| Elder Grok | xAI Grok | https://grok.com |
| Elder Perplexity | Perplexity AI | https://perplexity.ai |

**Each Elder receives the identical brief. Reviews are independent. No cross-contamination.**

---

## STEP 0 — ORIENT & CONFIRM ROOT

```bash
# Confirm you're in the MapArr project root
pwd
ls -la

# Show the full top-level directory tree (depth 3, excluding noise)
find . -maxdepth 3 \
  -not -path '*/.git/*' \
  -not -path '*/__pycache__/*' \
  -not -path '*/.venv/*' \
  -not -path '*/maparr_charm/*' \
  -not -path '*/.worktrees/*' \
  -not -path '*/img/services/*' \
  | sort
```

---

## STEP 1 — PROJECT STRUCTURE SNAPSHOT

```bash
# Full directory tree (depth 4, excluding noise)
find . -maxdepth 4 \
  -not -path '*/.git/*' \
  -not -path '*/__pycache__/*' \
  -not -path '*/.venv/*' \
  -not -path '*/.mypy_cache/*' \
  -not -path '*/maparr_charm/*' \
  -not -path '*/.worktrees/*' \
  -not -path '*/img/services/*' \
  | sort > /tmp/maparr_tree.txt

cat /tmp/maparr_tree.txt

# Count files by type
echo "--- FILE COUNTS BY EXTENSION ---"
find . \
  -not -path '*/.git/*' \
  -not -path '*/__pycache__/*' \
  -not -path '*/maparr_charm/*' \
  -not -path '*/.worktrees/*' \
  -type f | sed 's/.*\.//' | sort | uniq -c | sort -rn | head -20

# Total lines of code (Python backend + JS frontend)
echo "--- TOTAL LINES OF CODE ---"
find . -type f \( -name "*.py" -o -name "*.js" -o -name "*.css" -o -name "*.html" \) \
  -not -path '*/.git/*' \
  -not -path '*/__pycache__/*' \
  -not -path '*/maparr_charm/*' \
  -not -path '*/.worktrees/*' \
  | xargs wc -l 2>/dev/null | tail -1
```

---

## STEP 2 — DEPENDENCY & TECH STACK AUDIT

```bash
# --- Python / FastAPI backend ---
echo "=== PYTHON DEPENDENCIES ==="
[ -f requirements.txt ] && cat requirements.txt
[ -f pyproject.toml ] && cat pyproject.toml

# --- Docker ---
echo "=== DOCKER FILES ==="
for f in Dockerfile docker-compose.yml docker-compose.yaml docker-entrypoint.sh; do
  [ -f "$f" ] && echo "--- $f ---" && cat "$f"
done

# --- Data files ---
echo "=== IMAGE DATABASE ==="
[ -f data/images.json ] && echo "[data/images.json exists — $(wc -l < data/images.json) lines, $(python3 -c "import json; print(len(json.load(open('data/images.json'))['images']))" 2>/dev/null || echo '?') images]"
[ -f data/custom-images.json ] && echo "--- data/custom-images.json ---" && cat data/custom-images.json

# --- Unraid template ---
echo "=== UNRAID TEMPLATE ==="
[ -f unraid/maparr.xml ] && cat unraid/maparr.xml
```

---

## STEP 3 — ARCHITECTURE & ENTRY POINTS

```bash
# Main entry point
echo "=== MAIN ENTRY POINT ==="
cat backend/main.py

# Find all FastAPI app/route definitions
echo "=== ALL ENTRY POINTS DETECTED ==="
grep -rn "app = FastAPI\|if __name__\|@app\." \
  --include="*.py" \
  -not -path '*/.git/*' \
  . 2>/dev/null | head -40

# API routes (all endpoints with rate limit tiers)
echo "=== API ROUTES ==="
grep -rn "@app\.\(get\|post\|put\|delete\|patch\)" \
  --include="*.py" \
  -not -path '*/.git/*' \
  . 2>/dev/null | grep -v "test\|mock" | head -30

# Rate limiter configuration
echo "=== RATE LIMITER ==="
grep -A5 "WRITE_LIMIT\|ANALYSIS_LIMIT\|READ_LIMIT" backend/main.py 2>/dev/null
```

---

## STEP 4 — CORE FEATURE MODULES (FULL SOURCE)

```bash
# Read every backend Python source file (non-test)
echo "=== ALL BACKEND PYTHON SOURCE FILES ==="
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
    echo ""
    echo "================================================================"
    echo "FILE: $f ($(wc -l < "$f") lines)"
    echo "================================================================"
    cat "$f"
  fi
done

# Frontend files
echo "=== FRONTEND FILES ==="
for f in \
  frontend/index.html \
  frontend/app.js \
  frontend/styles.css; do
  if [ -f "$f" ]; then
    echo ""
    echo "================================================================"
    echo "FILE: $f ($(wc -l < "$f") lines)"
    echo "================================================================"
    cat "$f"
  fi
done

# Seed/utility scripts
echo "=== UTILITY SCRIPTS ==="
find ./scripts -name "*.py" -not -path '*/__pycache__/*' 2>/dev/null | sort | while read f; do
  echo ""
  echo "================================================================"
  echo "FILE: $f ($(wc -l < "$f") lines)"
  echo "================================================================"
  cat "$f"
done
```

---

## STEP 5 — TEST SUITE AUDIT

```bash
# List all test files with line counts
echo "=== TEST FILE INVENTORY ==="
find . -name "test_*.py" -not -path '*/.git/*' -not -path '*/__pycache__/*' \
  | sort | while read f; do
    echo "$f — $(wc -l < "$f") lines"
  done

# E2E fixtures & conftest
echo "=== E2E INFRASTRUCTURE ==="
for f in tests/e2e/conftest.py; do
  [ -f "$f" ] && echo "--- $f ---" && cat "$f"
done

# All test source files
echo "=== ALL TEST FILES ==="
find . -name "test_*.py" -not -path '*/.git/*' -not -path '*/__pycache__/*' \
  | sort | while read f; do
    echo ""
    echo "================================================================"
    echo "FILE: $f"
    echo "================================================================"
    cat "$f"
  done

# Run unit tests
echo "=== TEST RUN (unit) ==="
python -m pytest tests/ --ignore=tests/e2e --tb=short -q 2>&1 || echo "[pytest failed or unavailable]"

# Run API contract tests (no server needed)
echo "=== TEST RUN (API contracts) ==="
python -m pytest tests/e2e/test_api_contracts.py --tb=short -q 2>&1 || echo "[API contract tests failed or unavailable]"

# E2E Playwright summary (don't run — takes 70s)
echo "=== E2E PLAYWRIGHT TEST LIST ==="
grep -rn "def test_" tests/e2e/test_components.py tests/e2e/test_journeys.py 2>/dev/null
```

---

## STEP 6 — SECURITY SURFACE ANALYSIS

```bash
echo "=== SECURITY SURFACE: DOCKER SOCKET ACCESS ==="
grep -rn "docker\|socket\|unix\|pipe\|DOCKER_HOST\|docker.sock" \
  --include="*.py" \
  -not -path '*/.git/*' \
  -not -path '*/__pycache__/*' \
  . 2>/dev/null | grep -v "test\|#" | head -40

echo "=== SECURITY SURFACE: FILE OPERATIONS ==="
grep -rn "open(\|os\.path\|pathlib\|shutil\|os\.remove\|os\.rename" \
  --include="*.py" \
  -not -path '*/.git/*' \
  -not -path '*/__pycache__/*' \
  . 2>/dev/null | grep -v "test" | head -40

echo "=== SECURITY SURFACE: PATH VALIDATION ==="
grep -rn "relative_to\|_is_path_within\|_BLOCKED_PREFIXES\|COMPOSE_FILENAMES\|_get_stacks_root" \
  --include="*.py" \
  -not -path '*/.git/*' \
  . 2>/dev/null | head -30

echo "=== SECURITY SURFACE: USER INPUT HANDLING ==="
grep -rn "request\.json\|body\.get\|\.strip()\|size.*limit\|100_000\|1_000_000" \
  --include="*.py" \
  -not -path '*/.git/*' \
  . 2>/dev/null | grep -v "test" | head -40

echo "=== SECURITY SURFACE: ENV VARS ==="
grep -rn "os\.environ\|os\.getenv\|MAPARR_\|PUID\|PGID\|DOCKER_HOST" \
  --include="*.py" \
  -not -path '*/.git/*' \
  . 2>/dev/null | grep -v "test\|example" | head -30

echo "=== SECURITY SURFACE: SUBPROCESS/EXEC ==="
grep -rn "subprocess\|os\.system\|shell=True\|Popen" \
  --include="*.py" \
  -not -path '*/.git/*' \
  . 2>/dev/null | head -20

echo "=== SECURITY SURFACE: YAML HANDLING ==="
grep -rn "yaml\.\|safe_load\|safe_dump\|YAMLError" \
  --include="*.py" \
  -not -path '*/.git/*' \
  . 2>/dev/null | grep -v "test" | head -30

echo "=== SECURITY SURFACE: XSS PREVENTION (frontend) ==="
grep -n "innerHTML\|outerHTML\|insertAdjacentHTML\|document\.write\|\.html(" \
  frontend/app.js 2>/dev/null | head -20
echo "--- textContent usage count ---"
grep -c "textContent" frontend/app.js 2>/dev/null

echo "=== SECURITY SURFACE: ERROR MESSAGE SAFETY ==="
grep -rn "_json_error_detail\|_categorize_os_error\|_relative_path_display\|friendlyError\|str(e)" \
  --include="*.py" --include="*.js" \
  -not -path '*/.git/*' \
  . 2>/dev/null | grep -v "test" | head -30

echo "=== SECURITY DOCS ==="
[ -f DEPLOYMENT_SECURITY.md ] && cat DEPLOYMENT_SECURITY.md
```

---

## STEP 7 — PERFORMANCE & SCALABILITY SIGNALS

```bash
echo "=== CACHING / SESSION STATE ==="
grep -rn "_session\|cache\|lru_cache\|functools" \
  --include="*.py" \
  -not -path '*/.git/*' \
  . 2>/dev/null | grep -v "test" | head -30

echo "=== ASYNC / CONCURRENCY ==="
grep -rn "async def\|await \|asyncio\|threading\|run_in_executor" \
  --include="*.py" \
  -not -path '*/.git/*' \
  . 2>/dev/null | grep -v "test" | head -30

echo "=== SSE / STREAMING ==="
grep -rn "SSE\|EventSource\|StreamingResponse\|server-sent\|event-stream" \
  --include="*.py" --include="*.js" \
  -not -path '*/.git/*' \
  . 2>/dev/null | head -20

echo "=== RATE LIMITING ==="
grep -rn "RateLimiter\|rate_limit\|429\|Retry-After\|sliding.window" \
  --include="*.py" \
  -not -path '*/.git/*' \
  . 2>/dev/null | head -20

echo "=== PERFORMANCE PROFILE ==="
[ -f PERF_RESULTS.md ] && cat PERF_RESULTS.md
```

---

## STEP 8 — DOCUMENTATION & COMMENTS

```bash
echo "=== README ==="
[ -f README.md ] && cat README.md

echo "=== CHANGELOG ==="
[ -f CHANGELOG.md ] && cat CHANGELOG.md

echo "=== QUICK START ==="
[ -f QUICK_START.md ] && cat QUICK_START.md

echo "=== TROUBLESHOOTING ==="
[ -f TROUBLESHOOTING.md ] && cat TROUBLESHOOTING.md

echo "=== PROJECT KNOWLEDGE (CLAUDE.md) ==="
[ -f CLAUDE.md ] && cat CLAUDE.md

echo "=== TODO/FIXME/HACK COMMENTS ==="
grep -rn "TODO\|FIXME\|HACK\|XXX\|BUG\|OPTIMIZE" \
  --include="*.py" --include="*.js" \
  -not -path '*/.git/*' \
  -not -path '*/__pycache__/*' \
  . 2>/dev/null | head -40

echo "=== PRE-RELEASE REPORTS ==="
for f in PRERELEASE_REPORT.md ERROR_MESSAGES_REPORT.md CROSSBROWSER_RESULTS.md \
         DEPLOYMENT_RESULTS.md BACKLOG.md V151_IMPLEMENTATION_REPORT.md; do
  [ -f "$f" ] && echo "--- $f ---" && cat "$f"
done
```

---

## STEP 9 — GIT HISTORY & DEVELOPMENT TIMELINE

```bash
echo "=== GIT LOG (last 50 commits) ==="
git log --oneline -50 2>/dev/null || echo "[git not available]"

echo "=== GIT BRANCHES ==="
git branch -a 2>/dev/null

echo "=== RECENT CHANGES (last 14 days) ==="
git log --since="14 days ago" --name-only --pretty=format:"[%ad] %s" --date=short 2>/dev/null | head -80

echo "=== MOST CHANGED FILES ==="
git log --name-only --pretty=format: 2>/dev/null | sort | uniq -c | sort -rn | head -20

echo "=== UNCOMMITTED CHANGES ==="
git status 2>/dev/null
git diff --stat 2>/dev/null | head -20
```

---

## STEP 10 — UI/UX LAYER AUDIT

```bash
echo "=== FRONTEND HTML STRUCTURE ==="
cat frontend/index.html

echo "=== FRONTEND JS — KEY FUNCTIONS ==="
# Extract function declarations with line numbers (overview)
grep -n "^function \|^async function " frontend/app.js | head -80

echo "=== CSS STRUCTURE ==="
# Show CSS section comments and custom properties
grep -n "^/\*\|^--\|^:root\|^@media\|^\.health-\|^\.service-\|^\.conflict-\|^\.fix-" frontend/styles.css | head -60

echo "=== SERVICE ICONS ==="
echo "Bundled icons: $(ls frontend/img/services/ 2>/dev/null | wc -l) files"
ls frontend/img/services/ 2>/dev/null | head -30

echo "=== FRONTEND STATE MANAGEMENT ==="
grep -n "const state\|state\.\|_session\[" frontend/app.js | head -30
```

---

## STEP 11 — COMPILE EVERYTHING INTO THE BRIEF

Now take all output from Steps 0-10 and produce a single file:

```
Create a new file called ELDER_COUNCIL_BRIEF.md in the project root.

Structure it as follows:

---

# MAPARR v1.5.1 — ELDER COUNCIL REVIEW BRIEF

## 1. EXECUTIVE SUMMARY
- What MapArr does (1 paragraph)
- Who it's for (target user: homelab enthusiasts running Docker *arr stacks)
- Current status (v1.5.1, pre-release, public-aimed)
- Tech stack summary (Python 3.11/FastAPI backend + vanilla HTML/CSS/JS frontend, no framework)
- Test summary (682 unit + 76 E2E across 4 layers)
- Security posture (A- grade, 3 audits completed)

## 2. FULL PROJECT STRUCTURE
[Paste the directory tree from Step 1]

## 3. TECH STACK & DEPENDENCIES
[Paste all dependency files from Step 2]

## 4. ARCHITECTURE OVERVIEW
- Entry points
- 13 API routes with rate limit tiers
- Data flow: user action → API → pipeline/analysis → frontend render
- Pipeline-first analysis: full-directory scan → per-stack 4-pass analysis
- Session state model (in-memory, no persistence)
[Paste Step 3 findings]

## 5. COMPLETE SOURCE CODE
[Paste ALL source files from Step 4, grouped by layer: backend → frontend → scripts]

## 6. TEST SUITE
- 4-layer test architecture: unit, component (Playwright), journey (Playwright), Docker
- 682 unit tests + 28 API contracts + 45 Playwright + 3 Docker
- Synthetic test stacks covering all 20 conflict types
[Paste Step 5 findings]

## 7. SECURITY ANALYSIS SURFACE
- Docker socket access (read-only mount, subprocess list-form args)
- File operations (path traversal prevention, compose filename whitelist)
- Input validation (size limits, system directory blocklist, YAML safe_load)
- Error message safety (no str(e) leaks, categorized OS errors)
- XSS prevention (textContent only, zero innerHTML with user data)
- Rate limiting (3-tier sliding window)
[Paste Step 6 findings]

## 8. PERFORMANCE & SCALABILITY
- Pipeline scan performance (sub-linear on services, linear on stacks)
- In-memory session state (no database)
- SSE log streaming
- Tested to 100 stacks / 1000 services in <3 seconds
[Paste Step 7 findings]

## 9. DOCUMENTATION STATUS
- README, QUICK_START, TROUBLESHOOTING, CHANGELOG
- CLAUDE.md project knowledge
- Pre-release validation reports
[Paste Step 8 findings]

## 10. DEVELOPMENT HISTORY
- Git commit log
- Most active files
- Outstanding uncommitted work
[Paste Step 9 findings]

## 11. UI/UX LAYER
- Pipeline Dashboard: service-first UI with role-grouped services
- Paste bar: paste an error → auto-drill to fix
- RPM Wizard: 5-gate guided Remote Path Mapping setup
- Multi-file Apply Fix with diff preview
- 177 bundled service icons
- Dark theme, responsive layout
[Paste Step 10 findings]

## 12. ELDER COUNCIL REVIEW INSTRUCTIONS
[Leave this blank — it will be filled in by the strategy brief below]

---
```

---

## STEP 12 — ELDER COUNCIL REVIEW INSTRUCTIONS (add to brief)

Append this section verbatim to the bottom of `ELDER_COUNCIL_BRIEF.md`:

---

```markdown
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
```

---

## FINAL CHECKLIST BEFORE HANDING TO ELDERS

Before sending `ELDER_COUNCIL_BRIEF.md` to any Elder, confirm:

- [ ] All backend source files included (13 modules)
- [ ] Frontend files included (index.html, app.js, styles.css)
- [ ] Docker deployment files included (Dockerfile, entrypoint, compose)
- [ ] Security surface grep outputs included
- [ ] Pre-release validation reports included
- [ ] Git history included
- [ ] README + QUICK_START + TROUBLESHOOTING included
- [ ] Test results included (unit + API contract runs)
- [ ] Brief is self-contained (Elder needs nothing else to review)

If any section is empty, note explicitly: `[NOT APPLICABLE — reason]`

---

**File to deliver:** `ELDER_COUNCIL_BRIEF.md` (will be large — that's correct and necessary)

**Submit to each Elder independently:**

| # | Elder | Platform | Notes |
|---|-------|----------|-------|
| 1 | DeepSeek | https://chat.deepseek.com | Paste full brief. Use DeepSeek-V3 or R1 if available |
| 2 | Gemini | https://gemini.google.com | Paste full brief. Use Gemini 2.5 Pro if available |
| 3 | ChatGPT | https://chatgpt.com | Paste full brief. Use GPT-4o or o3 if available |
| 4 | Grok | https://grok.com | Paste full brief. Use Grok 3 if available |
| 5 | Perplexity | https://perplexity.ai | Paste full brief. Use Pro mode if available |

**Each Elder reviews independently** — do not share one Elder's findings with another Elder.
**Collect all 5 verdicts**, then bring them back to Claude for synthesis + consolidated action plan.

**The Council will speak. Then you decide.**
