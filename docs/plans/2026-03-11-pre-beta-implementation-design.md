# Pre-Beta Implementation Design — MapArr v1.5.1 → v1.5.2

> Date: 2026-03-11
> Sources: Elder Council (4 reviews), Global Task List Stage 1
> Approach: Vertical Slices (A) — dependency-ordered, independently testable chunks
> Priority: Quality over speed. Get it right.

---

## Implementation Slices

### Slice 1: Security + Hygiene Foundation

**Scope:** S3, S4, S5, C4, H1, H2

All backend, no UI. Hardens the foundation before features land on top.

#### S3 — DOCKER_HOST Allowlist

New `_validate_docker_host()` in `resolver.py`, called at top of `_try_docker_compose_config()` before any subprocess. Also validated in `docker-entrypoint.sh`.

**Allowed:**
- `unix://` (any path)
- `tcp://127.*` or `tcp://localhost` (loopback)
- `tcp://socket-proxy` or `tcp://*.local` (documented socket proxy pattern)
- Empty / unset (default socket)

**Denied:** Everything else. Log WARNING with sanitised value (no credentials leaked). Return `None` → resolver falls back to manual parsing. Never crash.

**Why allow socket-proxy:** QUICK_START.md documents `DOCKER_HOST=tcp://socket-proxy:2375`. Blocking it breaks our own docs. The SSRF risk is arbitrary internal services, not intentional proxy names.

#### S4 — Trusted Proxy IP Handling

New env var `MAPARR_TRUSTED_PROXIES` (comma-separated IPs, default empty).

When set: rate limiter reads `X-Forwarded-For`, uses rightmost IP not in trust list.
When unset: unchanged behaviour (`request.client.host`).

New `_get_client_ip(request)` helper in `main.py`, called by `RateLimiter.check()` and `SSEConnectionLimiter`. ~20 lines.

**Why not ProxyHeadersMiddleware:** Uvicorn's middleware trusts ALL proxies by default — worse than status quo.

#### S5 — Write Boundary Enforcement

In `_is_path_within_stacks()`: when `require_root=True` and no root configured, return `False` immediately. 403 already exists — closing the gap.

Startup log WARNING if no `MAPARR_STACKS_PATH`: "Write endpoints disabled — set MAPARR_STACKS_PATH to enable Apply Fix."

#### C4 — SSE Hard Timeout

Track connection start time in SSE generator. After 5 minutes, yield `event: timeout` and break. Frontend SSE client already has reconnect + exponential backoff.

#### H1 — Unused Imports

`ruff check --select F401` sweep across all backend modules. Fix all.

#### H2 — Dead/Duplicated Code

- Consolidate `COMPOSE_FILENAMES` to single definition (likely `resolver.py`), import elsewhere
- Consolidate `_get_stacks_root()` to single implementation
- Remove dead `cross_stack.py` imports where pipeline supersedes
- Remove frontend `friendlyError()` — superseded by C2's backend error categorisation

#### Slice 1 Testing

| Item | TDD | Unit | API Contract | E2E | Manual |
|------|-----|------|-------------|-----|--------|
| S3 | Yes — test denied patterns first | Allowed/denied patterns, fallback | Resolver with bad DOCKER_HOST → manual parse | — | `DOCKER_HOST=tcp://evil:1234` |
| S4 | Yes — test spoofed header blocked | `_get_client_ip()` with/without forwarded | Rate limiter respects forwarded IP | — | Behind reverse proxy |
| S5 | Yes — test 403 without root | `_is_path_within_stacks()` no root → False | apply-fix 403 without MAPARR_STACKS_PATH | — | Bare-metal dev run |
| C4 | — | — | — | SSE reconnects after 5min | Watch log panel |
| H1/H2 | — | All 755 existing tests pass | All 28 contracts pass | All journeys pass | — |

---

### Slice 2: Core UX Fixes

**Scope:** C1, C2, C3

Backend endpoints + frontend UI changes. Interrelated — error messages inform revert UX.

#### C1 — Undo/Revert Button

**Backend:** `POST /api/revert-fix` — accepts `{compose_file_path}`, validates path within stacks, checks `.bak` exists, swaps via `os.replace()`. Rate limited 10/min.

**Frontend:** After Apply Fix success, banner gains "Revert to Backup" button. Only shown when `.bak` exists (backend returns `has_backup: true` in apply response). On revert → pipeline rescan (same post-apply flow).

**Edge cases:** Only restores most recent `.bak`. Document: "Reverts your last fix." If `.bak` missing, button hidden.

#### C2 — Specific Error Messages

**Backend:** `_categorize_analysis_error()` maps exceptions to structured responses:
- `yaml.YAMLError` → `{type: "yaml_parse", message, line}`
- `FileNotFoundError` → `{type: "file_missing", message, path}`
- `PermissionError` → `{type: "permission_denied", message, path}`
- Docker timeout → `{type: "docker_unreachable", message, hint}`
- No services → `{type: "no_services", message, hint}`

**Frontend:** `renderAnalysisError()` switches on type. Each gets icon + colour + actionable one-liner. Unknown types → current generic fallback.

#### C3 — Warning Dismiss

**Frontend only.** `localStorage` set of dismissed conflict types. When rendering conflict cards, skip dismissed types (still count in log panel).

**Dismissable:** Only Cat B low/medium — `root_execution`, `umask_inconsistent`, `umask_restrictive`, `tz_mismatch`, `missing_tz`. Never dismiss Cat A or HIGH severity.

"Dismiss" link per card. "Reset dismissed" in log panel footer.

#### Slice 2 Testing

| Item | TDD | Unit | API Contract | E2E | Manual |
|------|-----|------|-------------|-----|--------|
| C1 | Yes — test revert endpoint | Swap, path validation, missing .bak 404 | POST /api/revert-fix shape | Journey: apply → revert → verify | Full cycle |
| C2 | Yes — test each error type | `_categorize_analysis_error()` per type | Error responses include `type` field | Component: error cards per type | Trigger each type |
| C3 | — | — | — | Component: dismissed don't render | Dismiss → refresh → verify |

---

### Slice 3: UX Features

**Scope:** U1, U2, U3, U4, U6, U7

Frontend-heavy. Can be parallelised within the slice.

#### U1 — First-Run Wizard

**Trigger:** No `MAPARR_STACKS_PATH` AND no localStorage `maparr_preferred_path`.

**Step 1 — Choose Folder:** Reuse `/api/list-directories`. Tree browser from common roots (`/`, `/opt`, `/srv`, `/docker`, `/mnt`). "Select this folder" button.

**Step 2 — Verify PUID/PGID:** New `GET /api/host-info` returns `{uid, gid}`. Pre-populated, user confirms. Stored in localStorage for reference only.

**Step 3 — Scan:** `/api/change-stacks-path` + `/api/pipeline-scan`. Boot terminal animation. Transition to dashboard.

**Skip:** "I know what I'm doing" link at Step 1 bottom.

**Subsequent launches:** Never shows again once path stored. Header path editor for changes.

#### U2 — Collapsible Other Stacks

Frontend only. Wrap chip container. Default collapsed >10 chips. State in localStorage. Header: "Other Services (N)" with chevron.

#### U3 — Direct Stack Restart

**Backend:** `POST /api/restart-stack` — runs `docker compose -f <file> up -d`. Same subprocess pattern as `redeploy.py`.

**Capability check:** `GET /api/docker-capabilities` → `{socket_available, socket_writable, compose_available}`. Frontend shows/hides restart button accordingly.

#### U4 — Export Diagnostic (Supersedes Copy Diagnostic)

**Backend:** `GET /api/export-diagnostics` — collects compose files, pipeline result, analysis result, version + platform. In-memory zip. Secret redaction on env values matching `key|token|password|secret` patterns.

**Frontend:** Primary: "Export Diagnostic" → zip download. Secondary: "Copy Summary" → clipboard markdown (existing `generateDiagnosticMarkdown()` preserved). Same button location, upgraded capability.

#### U6 — Redeploy Risk Warning

Frontend only. Replace dismissible modal with inline banner post-Apply-Fix. Persists until: restart clicked (U3), "I'll restart later" (collapses to amber indicator), or rescan confirms fix.

#### U7 — Service Icon Fallback

Frontend only. Enhance `getServiceIconUrl()`:
1. Exact match (current)
2. **New:** Segment match — split on `-`/`_`, check segments against icon keys
3. **New:** Image basename — extract from image string, check against keys
4. Fallback `generic.svg` (current)

#### Slice 3 Testing

| Item | TDD | Unit | API Contract | E2E | Manual |
|------|-----|------|-------------|-----|--------|
| U1 | — | Test `/api/host-info` | Response shape | Journey: fresh → wizard → dashboard | Full walkthrough |
| U2 | — | — | — | Component: collapsed >10 | Visual large stack |
| U3 | Yes | Restart endpoint, capability | Both endpoints | Journey: apply → restart | Real Docker restart |
| U4 | Yes | Zip generation, redaction | Returns valid zip, secrets stripped | — | Download + inspect |
| U6 | — | — | — | Component: banner renders | Visual after apply |
| U7 | — | — | — | Component: fuzzy icons | Custom image stacks (F03) |

---

### Slice 4: Documentation & Release Prep

**Scope:** D1, D2, D3, D4

Execute after Slices 1-3 committed, all tests green.

- **D1:** Full docs audit against updated codebase (new endpoints, features, env vars)
- **D2:** GitHub repo polish (templates, description, topics, social preview)
- **D3:** GIF recording against final pre-beta UI
- **D4:** Beta release plan + adapted feedback form for beta testers

---

## Cross-Cutting Concerns

### Commit Discipline
Each task gets its own commit. Slice boundaries are natural review points.

### Test Pyramid (Every Change)
- **Security items:** TDD — test asserts guard exists BEFORE writing the guard
- **Backend endpoints:** Unit test + API contract test
- **Frontend features:** E2E component + journey test where applicable
- **Regression gate:** All existing 755 tests must pass on every commit

### Testing Tools
- `superpowers:test-driven-development` — security items
- `pytest` — unit tests, all backend
- `httpx TestClient` — API contract tests (Layer 3)
- `Playwright` — component (Layer 1) + journey (Layer 2) tests
- `tools/testing-form.html` — 52-scenario manual suite (Layer 0)
- `superpowers:requesting-code-review` — after each slice
- `superpowers:verification-before-completion` — run all suites, confirm green
- Security scanning — OWASP patterns on every new endpoint per standing orders

### Code Style
- **Backend:** FastAPI patterns, `yaml.safe_load()`, subprocess list-form, path validation on writes, structured errors
- **Frontend:** Vanilla JS, `textContent` only, `createElement` for DOM, `addEventListener` (no inline), localStorage for prefs
- **Both:** Comprehensive code comments for cross-Claude communication

### Security Standing Orders
- Every new endpoint: validate all user-supplied paths, URLs, inputs
- OWASP top 10 scan on new code
- No `str(e)` in responses — use `_categorize_os_error()` / `_json_error_detail()`
- No `innerHTML` with dynamic data
- No `shell=True` in subprocess

---

## Dependency Graph

```
Slice 1 (Security + Hygiene)
    ↓
Slice 2 (Core UX Fixes)
    ↓  (C2 error messages inform U1 wizard error handling)
Slice 3 (UX Features)
    ↓  (code must be stable before docs)
Slice 4 (Docs + Release)
```

Slices are sequential. Within Slice 3, U1-U7 can be parallelised.
