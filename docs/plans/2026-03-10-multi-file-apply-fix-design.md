# Multi-File Apply Fix — Design Document

**Date:** 2026-03-10
**Status:** Approved
**Branch:** feature/pipeline-dashboard

## Problem

Apply Fix currently patches a single compose file. Cluster layouts (one service per folder — Dockhand, Portainer, DockSTARTer) need fixes spanning multiple compose files. The pipeline already detects cross-folder conflicts correctly; the gap is fix generation and application.

## Architecture Overview

User selects stack → `analyze_stack()` discovers all compose files in the pipeline context → generates per-file patches → bundles into `fix_plans` array on AnalysisResult → frontend renders unified confirmation modal → single batch apply via `/api/apply-fixes` → single re-scan.

Both the Browse pathway and Paste Error pathway converge at `analyze_stack()`, so multi-file changes flow through identically. The paste pathway's Tier 1 (parse + highlight) is unaffected. Tier 2 (user-triggered analysis) inherits `fix_plans` automatically.

## Backend Changes

### AnalysisResult — `fix_plans` field

```python
@dataclass
class AnalysisResult:
    # ... existing fields ...
    fix_plans: List[dict] = field(default_factory=list)
    # Each entry: {
    #   "compose_file_path": str,
    #   "corrected_yaml": str,
    #   "changed_services": List[str],
    #   "change_summary": str,  # e.g. "Fix volume mounts for sonarr"
    #   "category": str,        # "A", "B", or "A+B"
    # }
```

### Fix plan generation

During `analyze_stack()`, after conflict detection and solution generation:
1. Group conflicts by their source compose file
2. For each file, generate the patched YAML (volume patches for Cat A, env patches for Cat B)
3. Bundle into `fix_plans` array

Single-file stacks produce a `fix_plans` with 1 entry (backward compatible).
Healthy stacks produce empty `fix_plans`.

### Unified batch endpoint

Always use `/api/apply-fixes` (batch), even for single-file fixes. This simplifies the frontend to one code path. The existing `apply_multi.py` already implements the 3-phase strategy: validate all → backup all → write all.

`/api/apply-fix` (single) remains for backward compatibility but the frontend stops calling it.

## Frontend Changes

### Adaptive confirmation modal

- **Single file**: Simple view — file path, diff preview, Apply button
- **Multi-file**: File list with expand toggles for per-file diffs, "Apply All Fixes" button
- Button labels: "Apply Fix" (1 file) vs "Apply All Fixes" (N files)
- Each file row shows: filename, changed services, category badge

### Post-apply flow

1. Toast notification (success/partial/failure with per-file detail)
2. Single `pipeline-scan` refresh (not per-file)
3. Health dots update to "awaiting" state
4. Partial failure: clear messaging about which files succeeded vs failed

### Fix plan rendering

`generateFixPlans()` reads `fix_plans` from analysis response.
`renderFixPlan()` creates the modal UI.
`applyAllFixes()` sends the batch to `/api/apply-fixes`.

## Paste Error Pathway

Zero breaking changes. The paste pathway's two tiers:
- **Tier 1 (Parse + Highlight)**: Untouched — regex parsing + dashboard highlighting
- **Tier 2 (Analyze + Fix)**: Inherits `fix_plans` automatically via shared `analyze_stack()`

Both Browse and Paste arrive at the same fix modal. Standing checkpoint: every change verified against both pathways.

## Testing Strategy

### Phase A — Backend (pytest)
- `fix_plans` generation: single-file, cluster (3 files), mixed A+B, healthy (empty)
- Batch apply: all valid, validation failure (entire batch rejected), partial write failure
- Paste pathway parity: both entry points produce identical `fix_plans`
- Edge cases: empty stacks, single-service, no conflicts

### Phase B — Frontend (N/A, vanilla JS)

### Phase C — E2E (Playwright MCP)
- Browse → cluster stack → multi-file modal → Apply All → verify re-scan
- Paste error → highlight → click conflict → same modal → Apply All → verify
- Single-file stack: backward compat (no regression)
- UI state: button labels, disabled states, toast messages, health transitions

### Phase D — Reporting
Standard format: Backend (pytest) + E2E (Playwright) + Priority Fixes

### Phase E — Manual (user)
Full UI walkthrough after automated testing passes.

### TDD Discipline
- TDD Guard active: write ONE failing test → implement → refactor → next test
- Assert body content, not just status codes
- Both pathways tested for every change (blanket checkpoint)
