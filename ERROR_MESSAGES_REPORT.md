# MapArr Error Message Quality Audit

**Date:** 2026-03-11
**Version:** 1.5.0
**Scope:** All backend + frontend error paths

## Summary

| Layer | Total | GOOD | NEEDS WORK | CRITICAL |
|-------|-------|------|------------|----------|
| Backend | 51 | 16 | 31 | 4 |
| Frontend | 25 | 16 | 9 | 0 |
| **Total** | **76** | **32** | **40** | **4** |

## Critical Issues (Fix Before Release)

### CRITICAL-1: "Invalid JSON" on batch endpoints
- **Location:** main.py:1054 (/api/apply-fixes), main.py:1086 (/api/redeploy)
- **Current:** `{"error": "Invalid JSON"}`
- **Fix:** Report line:column from json.JSONDecodeError

### CRITICAL-2: Exception traceback leakage in analysis
- **Location:** main.py:820-826
- **Current:** `{"error": "Analysis failed: {str(exception)}"}`
- **Fix:** Catch specific types, log full traceback, return categorized error

### CRITICAL-3: No guidance for missing scan directory
- **Location:** main.py:418-420
- **Current:** `{"error": "No valid scan directory available"}`
- **Fix:** Add "Set MAPARR_STACKS_PATH or use the Change Path button"

### CRITICAL-4: Valid compose filenames not listed
- **Location:** main.py:987, apply_multi.py:57
- **Current:** "Target is not a recognised compose file"
- **Fix:** List valid names in the error message

## Major Patterns

### Backend
- **6x** "Invalid JSON in request body" with zero detail
- **4x** basename loses path context (user confused which directory)
- **4x** "path outside stacks directory" with no guidance on setting MAPARR_STACKS_PATH
- **3x** YAML/OS exception strings leak implementation details

### Frontend
- **9 NEEDS WORK** messages mostly lack diagnostic context
- All centered on "what went wrong" without "why" or "what to do"
- Strong infrastructure: friendlyError(), showSimpleToast(), showAnalysisError()
- SSE failure handling is excellent (invisible to user, polling fallback)

## Priority Fixes

### P0 (Before Release)
1. Fix 4 CRITICAL items above
2. Add error_text size limit (100KB) on /api/parse-error
3. Add corrected_yaml size limit (1MB) on /api/apply-fix[es]

### P1 (This Sprint)
4. All 6 JSON error messages: add json.JSONDecodeError context
5. All 4 basename issues: show relative path from stacks root
6. All 4 "outside stacks" errors: add MAPARR_STACKS_PATH guidance
7. Frontend Apply Fix failure: detect permission/YAML/disk errors

### P2 (Next Sprint)
8. Frontend parse failure: split by error type (malformed, timeout, not found)
9. Frontend "no path fix available": suggest Fix Permissions tab
10. Backend YAML error wrapping: parse details, suggest indentation fixes
