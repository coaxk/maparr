# MapArr v1.5.1 Work List

**Generated:** 2026-03-11
**Source:** ERROR_MESSAGES_REPORT.md, BACKLOG-3, BACKLOG-4, BACKLOG-5, PRERELEASE_REPORT.md

## Batch 1: Input Size Limits (BACKLOG-3) — 3 items

| # | File | Line | Change | Source |
|---|------|------|--------|--------|
| 1 | backend/main.py | 304 | Add 100KB limit on `error_text` field in `/api/parse-error` | BACKLOG-3 |
| 2 | backend/main.py | 946 | Add 1MB limit on `corrected_yaml` field in `/api/apply-fix` | BACKLOG-3 |
| 3 | backend/main.py | 577 | Unify `/api/list-directories` blocklist (3 entries) with `/api/change-stacks-path` blocklist (10 entries at line 498) | BACKLOG-3 |

## Batch 2: Critical Error Messages (BACKLOG-4 core) — 10 items

### 4 CRITICAL items

| # | File | Line | Change | Source |
|---|------|------|--------|--------|
| 4 | backend/main.py | 1054 | `/api/apply-fixes`: Change `"Invalid JSON"` → include line:column from `json.JSONDecodeError` | CRITICAL-1 |
| 5 | backend/main.py | 1086 | `/api/redeploy`: Change `"Invalid JSON"` → include line:column from `json.JSONDecodeError` | CRITICAL-1 |
| 6 | backend/main.py | 818-826 | `/api/analyze`: Catch specific exception types (ResolveError, yaml.YAMLError, OSError, ValueError), log traceback, return categorized safe message instead of `str(e)` | CRITICAL-2 |
| 7 | backend/main.py | 418-420 | `/api/pipeline-scan`: Change `"No valid scan directory available"` → add guidance: "Set MAPARR_STACKS_PATH or use the Change Path button" | CRITICAL-3 |
| 8 | backend/main.py | 987 | `/api/apply-fix`: Change `"Target is not a recognised compose file"` → list valid filenames from COMPOSE_FILENAMES | CRITICAL-4 |
| 9 | backend/apply_multi.py | 57 | `validate_fixes_batch`: Change `"Not a recognised compose file: {path.name}"` → list valid filenames | CRITICAL-4 |

### 6 JSON parse pattern fixes (same pattern as CRITICAL-1)

| # | File | Line | Change | Source |
|---|------|------|--------|--------|
| 10 | backend/main.py | 298-301 | `/api/parse-error`: Catch `json.JSONDecodeError` specifically, include position context | P1-pattern |
| 11 | backend/main.py | 465-468 | `/api/change-stacks-path`: Same JSON error improvement | P1-pattern |
| 12 | backend/main.py | 630-633 | `/api/select-stack`: Same JSON error improvement | P1-pattern |
| 13 | backend/main.py | 695-698 | `/api/analyze`: Same JSON error improvement | P1-pattern |
| 14 | backend/main.py | 869-872 | `/api/smart-match`: Same JSON error improvement | P1-pattern |
| 15 | backend/main.py | 939-942 | `/api/apply-fix`: Same JSON error improvement | P1-pattern |

## Batch 3: Pattern Error Message Fixes (NEEDS WORK) — 24 items

### Backend: basename context loss (4 items)

| # | File | Line | Change | Source |
|---|------|------|--------|--------|
| 16 | backend/main.py | 646 | `/api/select-stack`: `f"Directory not found: {os.path.basename(stack_path)}"` → show relative path from stacks root | P1-pattern |
| 17 | backend/main.py | 710 | `/api/analyze`: Same basename → relative path fix | P1-pattern |
| 18 | backend/main.py | 960 | `/api/apply-fix`: `f"File not found: {os.path.basename(...)}"` → show relative path from stacks root | P1-pattern |
| 19 | backend/redeploy.py | 52 | `validate_for_redeploy`: `f"No compose file found in {os.path.basename(...)}"` → show relative path | P1-pattern |

### Backend: "outside stacks" without guidance (4 items)

| # | File | Line | Change | Source |
|---|------|------|--------|--------|
| 20 | backend/main.py | 433 | `/api/pipeline-scan`: `"Scan directory is outside the stacks root"` → add MAPARR_STACKS_PATH guidance | P1-pattern |
| 21 | backend/main.py | 654 | `/api/select-stack`: `"Path is outside the stacks directory"` → add MAPARR_STACKS_PATH guidance | P1-pattern |
| 22 | backend/main.py | 718 | `/api/analyze`: Same outside-stacks guidance | P1-pattern |
| 23 | backend/main.py | 979 | `/api/apply-fix`: Already has guidance (line 973-975). Skip — already GOOD. | N/A |

### Backend: bare exception `str(e)` leaks (6 items)

| # | File | Line | Change | Source |
|---|------|------|--------|--------|
| 24 | backend/main.py | 1000-1003 | `/api/apply-fix` YAML validation: `f"Corrected YAML is not valid: {e}"` → sanitize, keep only line/column | P1-pattern |
| 25 | backend/main.py | 1012-1015 | `/api/apply-fix` backup failure: `f"Failed to create backup: {e}"` → categorize (permission denied, disk full, etc.) | P1-pattern |
| 26 | backend/main.py | 1026-1038 | `/api/apply-fix` write failure: `f"Failed to write file: {e}"` → categorize OS errors | P1-pattern |
| 27 | backend/apply_multi.py | 117 | `apply_fixes_batch` backup failure: `str(e)` → categorize OS error | P1-pattern |
| 28 | backend/apply_multi.py | 144 | `apply_fixes_batch` write failure: `str(e)` → categorize OS error | P1-pattern |
| 29 | backend/redeploy.py | 109 | `run_compose_action` generic exception: `str(e)` → categorize, log full traceback, return safe message | P1-pattern |

### Backend: other NEEDS WORK (1 item)

| # | File | Line | Change | Source |
|---|------|------|--------|--------|
| 30 | backend/apply_multi.py | 52 | `validate_fixes_batch`: `"Path outside stacks directory"` → add guidance | P1-pattern |

### Frontend: NEEDS WORK messages (9 items)

| # | File | Line | Change | Source |
|---|------|------|--------|--------|
| 31 | frontend/app.js | ~491 | Paste bar scan failure: `"Scan failed: " + err.message` → use `friendlyError()`, add retry guidance | P2-frontend |
| 32 | frontend/app.js | ~1330 | `drillIntoConflict`: `"Could not find service..."` → add "try rescanning" guidance | P2-frontend |
| 33 | frontend/app.js | ~1557 | Single apply fix failure: `"Apply failed: " + friendlyError(e)` → detect permission/YAML/disk errors | P2-frontend |
| 34 | frontend/app.js | ~1757 | Batch apply fix failure: Same as #33 | P2-frontend |
| 35 | frontend/app.js | ~2214 | Paste parse: `"Could not identify a service in this error."` → add guidance on supported formats | P2-frontend |
| 36 | frontend/app.js | ~2273 | Paste exception: `"Parse failed: " + e.message` → use `friendlyError()` | P2-frontend |
| 37 | frontend/app.js | ~6318 | Apply modal response: `result.error || "Failed to apply fix."` → show backend error detail | P2-frontend |
| 38 | frontend/app.js | ~6754 | No Cat B suggestion: `"No path fix available to apply."` → suggest Fix Permissions tab | P2-frontend |
| 39 | frontend/app.js | ~7520 | Browse/paste path scan failure: `data.error || "Failed to scan path."` → add troubleshooting context | P2-frontend |

## Batch 4: E2E Test Fixes (BACKLOG-5) — 3 items

| # | File | Line | Change | Source |
|---|------|------|--------|--------|
| 40 | tests/e2e/test_journeys.py | 366-401 | `test_paste_error_matches_service`: Update to handle paste auto-drill navigation (close button moved/hidden) | BACKLOG-5 |
| 41 | tests/e2e/test_journeys.py | 410-429 | `test_change_path_reloads_dashboard`: Fix timing — wait for pipeline scan completion before checking `#service-count` | BACKLOG-5 |
| 42 | tests/e2e/conftest.py | 87-93 | Add `atexit` handler to kill server process on ungraceful pytest exit (prevents stale port 19494) | BACKLOG-5 |

---

## Cross-Reference

| Report Category | Report Count | Work List Items | Notes |
|-----------------|-------------|-----------------|-------|
| CRITICAL error messages | 4 | #4-9 (6 items) | 4 CRITICALs + 2 additional locations for CRITICAL-4 pattern |
| NEEDS WORK (backend) | 31 | #10-30 (21 items) | 6 JSON parse, 4 basename, 3 outside-stacks (1 already good), 6 str(e), 1 apply_multi outside-stacks |
| NEEDS WORK (frontend) | 9 | #31-39 (9 items) | All 9 from ERROR_MESSAGES_REPORT.md |
| Input size limits | 3 locations | #1-3 (3 items) | BACKLOG-3 |
| E2E test fixes | 2 tests + 1 infra | #40-42 (3 items) | BACKLOG-5 |
| **TOTAL** | **50 report items** | **42 work items** | Item #23 skipped (already good). Some report items consolidated (CRITICAL-4 = 2 locations). |

**Note:** The ERROR_MESSAGES_REPORT.md counts 40 NEEDS WORK + 4 CRITICAL = 44 error items. This work list covers all 4 CRITICALs and all addressable NEEDS WORK items. Item #23 is skipped because line 973-975 already has MAPARR_STACKS_PATH guidance (confirmed by code review). The remaining delta between 44 and 39 error items comes from: (a) 5 NEEDS WORK items in the report that are duplicates of patterns already covered by other items (e.g., the 6 JSON parse errors counted in both CRITICAL and NEEDS WORK), and (b) some report NEEDS WORK entries referencing the same code location as a CRITICAL item. All unique locations are covered.
