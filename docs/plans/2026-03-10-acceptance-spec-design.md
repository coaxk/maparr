# MapArr v2.0 Acceptance Spec Design

**Date:** 2026-03-10
**Branch:** `feature/pipeline-dashboard`
**Purpose:** Comprehensive acceptance testing — release gate + regression suite

## Problem

Manual smoke testing immediately catches UI flow issues and component bugs that 682 unit/integration tests miss. The automated tests validate backend logic in isolation but never exercise:
- Frontend rendering correctness (DOM elements present, correct states, visible text)
- User journeys end-to-end (browse → scan → analyze → fix → redeploy)
- API contract adherence (response shapes, error codes, rate limits)
- Docker deployment correctness (entrypoint, PUID/PGID, healthcheck)

**Evidence from this session's manual testing:**
- Directory picker returned folder name only, not full path → users couldn't scan
- Cross-stack permission conflicts had no Apply Fix button → incomplete UX
- These were invisible to automated tests because no test exercises the full UI flow

## Design

### Architecture: 4-Layer Acceptance Spec

```
Layer 1: Component Specs     — "Is this DOM element present and correct?"
Layer 2: User Journeys        — "Can a user complete this workflow end-to-end?"
Layer 3: API Contracts         — "Does the backend return what the frontend expects?"
Layer 4: Docker Deployment     — "Does the container start and serve correctly?"
```

Each layer catches different failure modes. Together they form a complete release gate.

### Testing Tools

| Layer | Tool | Why |
|-------|------|-----|
| Components | Playwright | DOM assertions, visibility, text content, CSS classes |
| Journeys | Playwright | Click-through flows, state transitions, multi-step workflows |
| API Contracts | pytest + httpx | Response shapes, status codes, edge cases |
| Docker | pytest + subprocess | Container lifecycle, healthcheck, PUID/PGID |

### Data Sources

1. **Synthetic test stacks** (`tests/e2e/fixtures/`) — deterministic, CI-safe, covers all 20 conflict types
2. **Sanitized real data snapshot** (`tests/e2e/fixtures/real-snapshot/`) — read-only copy from user's C:\DockerContainers, never modifies source

---

## Layer 1: Component Specs (21 Components)

Each spec asserts specific DOM state. Failures read like bug reports:
`"Expected #health-banner to have class .health-problem but found .health-ok"`

### 1.1 First Launch Screen
- **Visible:** `#first-launch` section, `#first-launch-scan` button, `#first-launch-browse` button
- **Hidden:** `#pipeline-dashboard`, `#boot-screen`
- **Text:** Input placeholder contains "path" or "directory"
- **Interaction:** Browse button opens directory browser modal (`.dir-browser-overlay` visible)

### 1.2 Directory Browser Modal
- **Visible:** `.dir-browser-overlay`, `.dir-browser`, `.dir-browser-header`, `.dir-browser-list`
- **Items:** `.dir-browser-item` elements rendered for each subdirectory
- **Navigation:** Click item → list updates to show subdirectories of clicked path
- **Breadcrumb:** `.dir-browser-header` shows current path
- **Select:** Footer button closes modal and populates path input with full absolute path
- **Cancel:** Close button dismisses modal without changing path

### 1.3 Boot Terminal
- **Visible:** `#boot-screen` with terminal animation
- **Terminal lines:** `#boot-terminal-body` contains `<div>` lines appearing sequentially
- **Dots:** `.dot-red`, `.dot-yellow`, `.dot-green` present in terminal header
- **Transition:** After scan completes → `#boot-screen` hidden, `#pipeline-dashboard` visible

### 1.4 Pipeline Dashboard
- **Visible:** `#pipeline-dashboard`, `#health-banner`, `#service-groups`
- **Health banner:** `#health-banner-text` contains descriptive text
- **Health icon:** `#health-banner-icon` has class `.health-ok` OR `.health-problem`
- **Service count:** `#service-count` shows integer > 0
- **Welcome:** `#dashboard-welcome` paragraph present

### 1.5 Service Groups
- **Container:** `#service-groups` contains role-grouped sections
- **Role headers:** Each group has header text (e.g., "Arr Apps", "Download Clients", "Media Servers")
- **Service rows:** Each service row contains:
  - Service icon (`<img>` with `src` containing `/img/services/` OR generic fallback)
  - Service name text
  - Health dot with class `.healthy`, `.issue`, `.problem`, or `.awaiting`
  - Stack name label
- **Click:** Clicking service row expands detail or navigates to analysis

### 1.6 Health Banner
- **Healthy pipeline:** `.health-ok` class, green icon, text contains "healthy" or "looking good"
- **Problem pipeline:** `.health-problem` class, red icon, text describes issue count
- **Action buttons:** When problems exist → Fix All button visible in `#health-banner-actions`

### 1.7 Paste Area
- **Toggle:** Click `#fork-paste` → `#paste-area` becomes visible
- **Elements:** `#paste-error-input` textarea, `#paste-error-go` button, `#paste-area-close` button
- **Example pills:** `.paste-pill` buttons with `[data-example]` attributes
- **Pill click:** Populates textarea with example error text
- **Parse result:** After submit → `#paste-bar-result` visible with matched service name

### 1.8 Conflict Cards
- **Container:** `#conflict-cards` holds individual conflict cards
- **Card structure:** Each card shows:
  - Conflict type label (from CONFLICT_HANDRAILS — plain English)
  - Severity badge (critical/high/medium/low)
  - Affected services list
  - Category indicator (A/B/C/D)
- **Expand:** Click card → detail panel expands with full explanation
- **Category A cards:** Show "View Fix" or drill-through to solution
- **Category B cards:** Show permission fix details
- **Category C cards:** Show guidance text only, no fix button
- **Category D cards:** Collapsed in observations section

### 1.9 Analysis Terminal
- **Visible:** `#step-analyzing` during analysis
- **Terminal output:** `#terminal-output` shows step-by-step progress lines
- **Step counter:** Terminal title shows "Step X/6"
- **Completion:** Terminal hides, result sections appear

### 1.10 Problem Card (Step: Problem)
- **Visible:** `#step-problem` when conflicts found
- **Content:** `#problem-details` contains conflict explanation
- **CONFLICT_HANDRAILS text:** Description matches entry from CONFLICT_HANDRAILS constant
- **Severity styling:** Card border/header reflects severity level

### 1.11 Current Setup Card (Step: Current Setup)
- **Visible:** `#step-current-setup`
- **Content:** `#current-setup-details` shows mount structure diagram
- **Mount classifications:** Each mount shown with source → target mapping
- **Remote FS warnings:** If remote mounts detected → warning banner within section

### 1.12 Solution Card — Recommended Fix (Category A)
- **Visible:** `#step-solution` with `#solution-tabs`
- **Tabs:** `#tab-recommended` (active by default), `#tab-original`
- **YAML display:** `#solution-yaml` contains formatted YAML with syntax highlighting
- **Changed lines:** Lines with changes have highlight class applied
- **Buttons:** `#btn-copy` (Copy to Clipboard), `#btn-apply-fix` (Apply Fix)
- **Apply confirmation:** Clicking Apply → `#apply-confirm` visible with file path in `#apply-confirm-file`
- **Apply result:** After confirm → `#apply-result` shows success/failure message

### 1.13 Solution Card — Fix Permissions (Category B)
- **Tab:** Separate tab or section for environment variable fixes
- **YAML display:** Shows env changes (PUID, PGID, TZ, UMASK)
- **Changed lines:** Environment lines highlighted
- **Cross-stack message:** When current stack is correct but siblings differ → explanatory text:
  "This stack's permissions are already correct. Other services in your pipeline use different PUID/PGID values"
- **Buttons:** Copy to Clipboard + Apply Fix (when fix_plans available)

### 1.14 Solution Card — Your Config (Corrected) Tab
- **Tab:** `#tab-original` — shows user's original compose with corrections inline
- **YAML display:** `#solution-yaml-original` with original formatting preserved
- **Changed lines highlighted:** Only modified lines have highlight class
- **Copy button:** `#btn-copy-original`

### 1.15 Why It Works Card
- **Visible:** `#step-why`
- **Content:** `#why-details` contains root cause explanation
- **TRaSH reference:** Links to TRaSH Guides where applicable

### 1.16 Next Steps Card
- **Visible:** `#step-next`
- **Content:** `#next-steps-checklist` contains ordered action items
- **Checklist format:** Each item is actionable (e.g., "Run docker compose up -d")

### 1.17 Healthy Result
- **Visible:** `#step-healthy` when no conflicts
- **Content:** Green confirmation message
- **Permission summary:** `renderPermissionSummaryInto()` shows UID:GID table
- **Cross-stack context:** If pipeline has other stacks → shows sibling info

### 1.18 Observations Section (Category D)
- **Container:** `#observations-container`
- **Collapsed by default:** User can expand
- **Items:** restart policy, latest tag, missing TZ, privileged mode, no healthcheck
- **No health impact:** These don't affect health dot color

### 1.19 Non-Media Services
- **Layout:** Flex-wrap chip layout (not collapsed `<details>`)
- **Icons:** Service icons where available, generic fallback
- **Explanatory note:** Brief text explaining these aren't analyzed for media conflicts

### 1.20 Service Icons
- **Path:** `/static/img/services/{name}.svg`
- **Fallback:** Generic icon when specific icon not found
- **Sizes:** 20px in service rows, 16px in setup tables
- **Loading:** `loading="lazy"` attribute present
- **Coverage:** 115+ named mappings in SERVICE_ICONS constant

### 1.21 Path Editor (Header)
- **Toggle:** Click `#header-path` → `#path-editor` becomes visible
- **Elements:** `#header-path-input` (text), `#header-path-go` (Scan), `#header-path-browse` (Browse)
- **Current path:** `#header-path-text` shows active scan directory
- **Scan:** Enter path + click Go → triggers new pipeline scan
- **Browse:** Opens directory browser modal

---

## Layer 2: User Journeys (10 Journeys)

Each journey is a Playwright test that clicks through the full workflow. Assertions at each step verify the expected UI state.

### 2.1 First Launch → Browse → Scan → Dashboard
1. App loads → `#first-launch` visible
2. Click `#first-launch-browse` → `.dir-browser-overlay` appears
3. Navigate to test stacks directory → click Select
4. Path input populated with full absolute path
5. Click `#first-launch-scan` → `#boot-screen` appears with terminal animation
6. Terminal lines appear sequentially
7. Boot completes → `#pipeline-dashboard` visible, `#service-count` > 0

### 2.2 First Launch → Manual Path → Scan
1. App loads → `#first-launch` visible
2. Type path into input manually
3. Click `#first-launch-scan` → boot → dashboard
4. Verify same result as browse path

### 2.3 Dashboard → Analyze Healthy Stack
1. Dashboard visible with services
2. Click a service known to be healthy
3. Analysis terminal runs (Step 1/6 through 6/6)
4. `#step-healthy` appears with green confirmation
5. Permission summary table shows UID:GID
6. Back button returns to dashboard

### 2.4 Dashboard → Analyze Stack with Path Conflict → Apply Fix
1. Click service with known path conflict (Category A)
2. Analysis completes → `#step-problem` visible
3. Problem description matches CONFLICT_HANDRAILS text
4. `#step-solution` visible with recommended fix tab active
5. YAML displayed with changed lines highlighted
6. Click `#btn-apply-fix` → `#apply-confirm` modal appears
7. Confirm → `#apply-result` shows success
8. Health dot changes to `.awaiting` (blue)
9. Back to dashboard → service shows awaiting state

### 2.5 Dashboard → Analyze Stack with Permission Issue
1. Click service with known permission conflict (Category B)
2. Analysis completes → permission conflict card visible
3. Environment fix section shows PUID/PGID changes
4. If cross-stack: explanatory message about other stacks
5. Copy to Clipboard works (button text changes temporarily)
6. If fix_plans available: Apply Fix button functional

### 2.6 Dashboard → Paste Error → Auto-Match → Analyze
1. Click `#fork-paste` → paste area opens
2. Click example pill → textarea populated
3. Click `#paste-error-go` → service matched in `#paste-bar-result`
4. Matched service highlighted in dashboard
5. Click highlighted service → analysis runs for correct stack
6. Results relevant to pasted error

### 2.7 Change Stacks Path (Header)
1. Click `#header-path` → path editor appears
2. Enter new path → click `#header-path-go`
3. Boot terminal re-runs for new directory
4. Dashboard refreshes with new stacks
5. Service count updates
6. Previous analysis state cleared

### 2.8 Multi-File Fix (Cluster Layout)
1. Scan directory containing cluster layout (one-service-per-subfolder)
2. All cluster services discovered and shown in dashboard
3. Analyze cluster service with conflict
4. Fix plan shows multiple files to modify
5. Apply Fix handles all files in batch
6. Each file gets .bak backup

### 2.9 Apply Fix → Redeploy Flow
1. Apply fix to a stack (journey 2.4)
2. Redeploy prompt appears after fix applied
3. Click redeploy → progress shown
4. Result shows success/failure per stack
5. Service health updates after redeploy

### 2.10 RPM Wizard (Category A Only)
1. Analyze stack with path conflict that has RPM data
2. RPM wizard section appears (5-gate flow)
3. Wizard only shown for Category A conflicts (not B/C/D)
4. RPM table renders with service mappings
5. Solution YAML reflects RPM recommendations

---

## Layer 3: API Contracts (14 Endpoints)

pytest tests using httpx TestClient. Each test validates response shape, status codes, and edge cases.

### 3.1 GET /api/health
- **200:** `{"status": "ok", "version": "1.5.0"}` — both fields present, version is string
- **Healthcheck:** Response within 5 seconds

### 3.2 POST /api/parse-error
- **200 (single error):** Response has `service`, `path`, `error_type`, `confidence` fields
- **200 (multi error):** Response has `multiple_errors` array and `error_count` > 1
- **200 (no match):** Response has `service: null` or empty
- **422:** Missing `error_text` field → validation error

### 3.3 POST /api/pipeline-scan
- **200:** Response has `scan_dir`, `scanned_at`, `media_services` (array), `roles_present`, `health`, `summary`, `steps`
- **Each media_service:** Has `service_name`, `role`, `stack_name`, `compose_file`, `host_sources`
- **roles_present:** Subset of `["arr", "download_client", "media_server"]`
- **health:** One of `"ok"`, `"warning"`, `"problem"`
- **Cluster detection:** Cluster stacks appear as individual services with distinct `compose_file` paths

### 3.4 POST /api/change-stacks-path
- **200 (valid path):** `{"status": "ok", "message": "..."}`
- **200 (reset):** `{"status": "reset"}` when path cleared
- **400 (invalid):** Blocked paths (/proc, /sys, /dev, C:\Windows) rejected
- **Rate limited:** 10 req/min tier

### 3.5 POST /api/list-directories
- **200:** Response has `path`, `parent`, `directories` array
- **Each directory:** Has `name`, `path` fields
- **Windows root:** Returns drive letters when no path given
- **Permission denied:** Directories with `locked: true` flag
- **Blocked paths:** /proc, /sys, /dev not listed

### 3.6 POST /api/analyze
- **200 (conflicts):** `status: "conflicts_found"`, `conflicts` array non-empty, each conflict has `conflict_type`, `severity`, `services`, `description`, `category`
- **200 (healthy):** `status: "healthy_pipeline"`, `conflicts` empty
- **200 (error):** `status: "error"`, `message` present
- **Category A response:** `solution_yaml` and `original_corrected_yaml` present, `solution_changed_lines` non-empty
- **Category B response:** `env_solution_yaml` present when permission conflicts found
- **fix_plans:** Array of `{compose_file_path, corrected_yaml}` objects for multi-file
- **observations:** Array of Category D items (no health impact)
- **Pipeline context:** `pipeline` object with sibling services when cross-stack
- **Security:** Path outside stacks root → 403

### 3.7 POST /api/apply-fix
- **200:** `{"status": "ok", "backup_created": true, "compose_file_path": "..."}`
- **400:** Invalid YAML → rejection before write
- **403:** Path outside stacks root
- **Rate limited:** 10 req/min tier
- **Side effect:** .bak file created alongside compose file

### 3.8 POST /api/apply-fixes (batch)
- **200 (all succeed):** `{"status": "ok", "applied": [...], "failed": []}`
- **200 (partial):** `{"status": "partial", "applied": [...], "failed": [{path, error}]}`
- **400:** More than 20 files → rejected
- **Each applied file:** .bak backup created

### 3.9 POST /api/redeploy
- **200:** `{"status": "ok", "deployed": [...], "failed": []}`
- **400:** More than 10 stacks → rejected
- **Each stack:** Runs `docker compose -f {file} up -d`

### 3.10 GET /api/discover-stacks
- **200:** `{"stacks": [...], "total": N, "scan_path": "..."}`
- **Each stack:** Has `path`, `services`, `service_count`

### 3.11 POST /api/select-stack
- **200:** Stack details with service list
- **404:** Stack path not found

### 3.12 POST /api/smart-match
- **200:** Best-matched service name from error text
- **200 (no match):** Empty or null response

### 3.13 GET /api/logs
- **200:** `{"entries": [{level, timestamp, message}], "total": N}`
- **Query params:** `limit`, `level`, `since` all optional

### 3.14 Rate Limiting (Cross-Cutting)
- **Write tier (10/min):** /api/apply-fix, /api/apply-fixes, /api/change-stacks-path, /api/redeploy
- **Analysis tier (20/min):** /api/analyze, /api/pipeline-scan
- **Read tier (60/min):** All other endpoints
- **429 response:** When limit exceeded, includes `Retry-After` header or message

---

## Layer 4: Docker Deployment (6 Tests)

### 4.1 Container Starts with Defaults
- Build image → run with minimal config (stacks volume only)
- Container reaches healthy state within 30 seconds
- `GET /api/health` returns 200

### 4.2 PUID/PGID Remapping
- Run with `PUID=1500 PGID=1500`
- Process inside container runs as UID 1500
- Files created in /app owned by UID 1500

### 4.3 Custom Port
- Run with `MAPARR_PORT=8080`
- App listens on port 8080, healthcheck passes on 8080

### 4.4 Docker Socket Access
- Mount docker.sock into container
- `/api/pipeline-scan` uses `docker compose config` resolution
- Resolution method in response: `"docker"`

### 4.5 Socket Proxy (DOCKER_HOST)
- Set `DOCKER_HOST=tcp://host:2375` without socket mount
- App starts without error
- Logs show socket proxy awareness message

### 4.6 Read-Only Stacks Volume
- Mount stacks as `:ro`
- Pipeline scan works (read-only operation)
- Apply fix correctly fails with permission error (not silent corruption)

---

## Test Data Strategy

### Synthetic Test Stacks (`tests/e2e/fixtures/`)

Purpose: Deterministic, CI-safe, covers all conflict types.

| Stack | Conflict Type | Category | Expected Result |
|-------|--------------|----------|-----------------|
| healthy-arr | None | — | Green, permission summary shown |
| path-conflict | no_shared_mount | A | Red, solution YAML generated |
| different-paths | different_host_paths | A | Red, solution YAML generated |
| named-volume | named_volume_data | A | Red, solution YAML generated |
| puid-mismatch | puid_pgid_mismatch | B | Yellow, env solution generated |
| missing-puid | missing_puid_pgid | B | Yellow, env solution generated |
| root-user | root_execution | B | Yellow, env solution generated |
| umask-issue | umask_inconsistent | B | Yellow, env solution generated |
| tz-mismatch | tz_mismatch | B | Yellow, env solution generated |
| wsl2-paths | wsl2_performance | C | Warning, guidance only |
| remote-fs | remote_filesystem | C | Warning, guidance only |
| mixed-mounts | mixed_mount_types | C | Warning, guidance only |
| observations | missing_restart_policy + latest_tag | D | Info, collapsed section |
| cluster-layout | (various) | A+B | Multi-file fix plan generated |
| cross-stack-puid | cross_stack_puid_mismatch | B | Cross-stack messaging |
| rpm-scenario | path_unreachable | A | RPM wizard shown |

### Sanitized Real Data Snapshot (`tests/e2e/fixtures/real-snapshot/`)

Purpose: Pre-release validation against real-world compose files. Never modifies C:\DockerContainers.

- One-time copy of compose files from user's production directory
- Sanitized: passwords, API keys, tokens removed from environment variables
- Read-only: tests NEVER write to this directory
- Validates: service discovery count, role classification, real-world YAML parsing

---

## Assertion Philosophy

Every assertion should read like a bug report when it fails:

```python
# Good — reads like a bug report
assert page.locator("#health-banner").get_attribute("class").contains("health-problem"), \
    "Expected health banner to show PROBLEM state for stack with path conflicts"

# Bad — generic
assert banner_class == expected
```

Key principles:
1. **Descriptive failure messages** — tell you exactly what broke
2. **One concern per assertion** — don't combine visibility + content + styling
3. **DOM-grounded** — assert on actual DOM state, not internal JS variables
4. **Category-aware** — assertions know which category should produce which UI elements

---

## Manual Sign-Off Checklist

Automated tests cover correctness. Manual sign-off covers subjective quality:

- [ ] Terminal animations feel smooth (no flicker, no frozen frames)
- [ ] Service icons render at correct size (not blurry, not oversized)
- [ ] YAML syntax highlighting is readable (colors contrast with background)
- [ ] Health banner color transitions feel natural
- [ ] Directory browser modal is responsive on different viewport sizes
- [ ] Long stack names don't break layout
- [ ] Error messages are helpful (not raw tracebacks)
- [ ] Rate limit responses surface clearly (not silent failures)
- [ ] Apply Fix confirmation modal is clearly a "are you sure?" moment
- [ ] Redeploy progress feels responsive (not frozen)

---

## File Structure

```
tests/
  e2e/
    conftest.py              — Playwright fixtures, server startup, test data setup
    fixtures/
      stacks/                — 16 synthetic test stacks (compose files)
      real-snapshot/          — Sanitized copy of user's production stacks
    test_components.py       — Layer 1: 21 component specs
    test_journeys.py         — Layer 2: 10 user journey tests
    test_api_contracts.py    — Layer 3: 14 API contract tests
    test_docker.py           — Layer 4: 6 Docker deployment tests
    manual_checklist.md      — Layer 5: manual sign-off items
```

---

## Execution Strategy

**CI (every push):** Layers 1-3 (Components, Journeys, API Contracts)
- Synthetic test stacks only
- No Docker-in-Docker requirement
- Target: < 60 seconds total

**Pre-release (manual trigger):** All 4 layers
- Real data snapshot included
- Docker build + deployment tests
- Manual checklist reviewed by human

**Regression:** Any test failure blocks merge to main
