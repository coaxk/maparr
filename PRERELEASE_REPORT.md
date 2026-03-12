# MapArr Pre-Release Validation Report

**Date:** 2026-03-11
**Version:** v1.5.0
**Auditor:** Claude Code (Opus 4.6)

## Executive Summary

MapArr v1.5.0 is a well-built, performant application with strong security fundamentals
and consistent cross-browser behavior. Performance scales linearly to 100+ stacks with
sub-3-second response times. No cross-browser issues detected. Security posture is strong
(A-) with minor input size limit gaps. Error messages need improvement in 4 critical
locations before release. Docker deployment deferred (Docker CLI unavailable in test
environment) but code review confirms correct configuration.

## Release Readiness Verdict

[x] **READY WITH FIXES** -- 4 error message improvements recommended before tagging release

## Blocking Issues (Must Fix Before Release)

None. The 4 CRITICAL error messages are poor UX, not data-loss or crash scenarios.
They can ship as-is but should be fixed in v1.5.1 at latest.

## Recommended Fixes Before Release

### P0 -- Error Messages (BACKLOG-4)
1. **"Invalid JSON"** on /api/apply-fixes and /api/redeploy -- add line:column context
2. **Analysis exception leak** -- catch specific types, return categorized error
3. **"No valid scan directory"** -- add MAPARR_STACKS_PATH guidance
4. **Compose filename not listed** -- include valid filenames in rejection message

### P0 -- Input Size Limits (BACKLOG-3)
5. **error_text** on /api/parse-error: add 100KB limit
6. **corrected_yaml** on /api/apply-fix[es]: add 1MB-per-file limit
7. **Directory listing blocklist**: expand to match change-stacks-path

### P1 -- Test Updates (BACKLOG-5)
8. **2 E2E journey tests** need updating for paste auto-drill flow changes

## Acceptable Known Issues

- **Batch test session bleed**: Fixed in commit 95f6040 but should be monitored
- **Rate limiting triggers on rapid profiling**: Correct behavior, not a bug
- **Docker deployment untested in this session**: Docker CLI not available from
  Windows shell. Layer 4 tests exist and will run in CI/CD.
- **2 E2E test failures**: Test infrastructure issue (paste auto-drill changed UI flow),
  not user-facing bugs. Same failures across all 3 browsers confirms not browser-specific.

## Known Infrastructure Issues

- **E2E port conflict on 19494**: Stale Python process from previous test run. Fixed by
  killing PID. Root cause: E2E conftest.py session-scoped server fixture doesn't clean up
  on ungraceful pytest termination. Consider adding atexit handler.

## Test Results Summary

| Suite | Count | Result | Time |
|-------|-------|--------|------|
| pytest unit | 682 | 682 passed | 10.4s |
| API contracts (Layer 3) | 28 | 28 passed | 2.5s |
| Browser - Chromium (Layer 1+2) | 45 | 43 passed, 2 failed | 69.0s |
| Browser - Firefox | 45 | 43 passed, 2 failed | 67.4s |
| Browser - WebKit/Safari | 45 | 43 passed, 2 failed | 68.9s |
| Docker deployment (Layer 4) | 3 | 3 skipped (no Docker) | 0s |
| **Total** | **848** | **839 passed, 6 failed*, 3 skipped** | **~218s** |

*Same 2 tests fail on all 3 browsers = 2 unique failures (test infrastructure, not bugs)

## Performance Characteristics

| Metric | Value | Verdict |
|--------|-------|---------|
| 5 services, 1 stack | 19.7ms | Excellent |
| 20 services, 1 stack | 56.3ms | Excellent (<500ms threshold) |
| 50 services, 1 stack | 130.5ms | Excellent (<2000ms threshold) |
| 100 stacks (1000 services) | 2902ms | Good (<5000ms threshold) |
| Scaling curve | Sub-linear (services), Linear (stacks) | Acceptable |
| Peak memory (100 stacks) | 9.83MB | Negligible |
| 5 concurrent requests | All 200, no errors | Safe |

**Projection:** Power users with 200+ stacks may notice ~6s scan time. Consider
progress indicator for v1.6.

## Security Posture

**Overall Grade: A-**

| Category | Status |
|----------|--------|
| Path traversal prevention | Strong (relative_to checks) |
| YAML injection | Strong (safe_load everywhere) |
| Command injection | Strong (list-form subprocess) |
| Rate limiting | Strong (3-tier, sliding window) |
| Input validation | Partial (size limits missing) |
| XSS prevention | Strong (textContent only) |
| Subprocess timeouts | Strong (30s/120s) |

See DEPLOYMENT_SECURITY.md for full deployment guide.

## Confirmed Deployment Configurations

Docker deployment code-reviewed but not runtime-tested (Docker CLI unavailable).
Verified via code review:
- Dockerfile: multi-stage, gosu, non-root, healthcheck
- docker-entrypoint.sh: PUID/PGID remapping, socket group detection
- docker-compose.yml: log rotation, socket mount, port config

Manual verification commands documented in DEPLOYMENT_RESULTS.md.

## Cross-Browser Results

**Zero browser-specific issues.** Chromium, Firefox, and WebKit produce identical
results (43/45 passed, same 2 failures). CSS, JS, SVG, and layout all consistent.

## Error Message Quality

| Verdict | Backend | Frontend | Total |
|---------|---------|----------|-------|
| GOOD | 16 | 16 | 32 |
| NEEDS WORK | 31 | 9 | 40 |
| CRITICAL | 4 | 0 | 4 |

4 CRITICAL items are poor UX (not crashes). See ERROR_MESSAGES_REPORT.md.

## v1.6 Backlog Items Logged

- **BACKLOG-1:** Paste Result Alternatives (alternative resolution paths)
- **BACKLOG-2:** Light Mode / prefers-color-scheme
- **BACKLOG-3:** Input Size Limits (security hardening)
- **BACKLOG-4:** Error Message Improvements (4 CRITICAL + patterns)
- **BACKLOG-5:** Update E2E Tests for Paste Auto-Drill

## What's Exceptional

- **Sub-linear performance scaling** on service count -- 10x services = 6.6x time
- **Zero cross-browser issues** -- all three engines produce identical results
- **Strong security fundamentals** -- path traversal, YAML injection, command injection
  all properly mitigated with defense-in-depth
- **4-pass analysis engine** covers path, hardlink, permission, and platform issues
- **RPM Wizard** -- genuinely novel UX that walks users through Remote Path Mappings
  step-by-step, with host field advisory and jargon-free language
- **Pipeline-first architecture** -- unified view across all stacks, automatic cluster
  detection, category-aware conflict routing
- **219-image recognition database** with 7-family classification and hardcoded fallback
- **Paste auto-drill** -- paste an error, get taken directly to the fix. No decisions needed.

---

## Supporting Documents

| File | Content |
|------|---------|
| PERF_RESULTS.md | Performance profiling data and scaling analysis |
| CROSSBROWSER_RESULTS.md | Cross-browser test results (Chromium/Firefox/WebKit) |
| ERROR_MESSAGES_REPORT.md | Error message quality audit (76 locations) |
| DEPLOYMENT_RESULTS.md | Docker deployment verification (code review + manual steps) |
| DEPLOYMENT_SECURITY.md | Security guide for deployment configurations |
| BACKLOG.md | Tracked items for v1.6+ |
