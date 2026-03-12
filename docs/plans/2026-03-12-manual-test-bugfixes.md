# Manual Test Bug Fixes — MapArr v1.5.2

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix 7 bugs discovered during the 42-scenario manual testing suite (2026-03-11), plus update test expectations where the testing form was wrong.

**Architecture:** All fixes are in the existing FastAPI backend (`main.py`, `analyzer.py`, `pipeline.py`, `parser.py`, `cross_stack.py`) and vanilla JS frontend (`app.js`). No new files except tests. Each bug is independent — fixes don't interfere with each other.

**Tech Stack:** Python 3.11+ / FastAPI, vanilla JavaScript, pytest

**Important Context:** The manual tests were run against v1.5.1 code (pre-Slice 1-3). Our Slice 3 changes already fixed BUG 1 (duplicate "Remember" sentence — replaced by redeploy banner in commit `0aad850`). All remaining bugs exist in the current codebase.

---

## Bug Triage (Priority Order)

| # | Bug | Severity | Category |
|---|-----|----------|----------|
| 4 | Malformed YAML / Empty Compose hangs at "scan in progress" | Blocking | Backend + Frontend |
| 3 | Pipeline-view "Apply All Fixes" fails with "path outside stacks directory" | Blocking | Backend + Frontend |
| 6 | Paste textarea not cleared after fix/navigation | UX | Frontend |
| 7 | Inconsistent Apply Fix flow (diff preview vs toast) | UX | Frontend |
| 5 | Paste error parser can't identify service in P02/P05/P06 | Functional | Backend |
| 8 | TZ mismatch labelled as "permission issue" | UX | Frontend |
| 2 | RPM Wizard not showing in some Cat A scenarios | Functional | Backend + Frontend |

### Already Fixed (no action needed)
- **BUG 1:** Duplicate "Remember: restart your stack..." — replaced by `renderRedeployBanner()` in commit `0aad850`

### Test Expectation Corrections (no code change)
- **A02, A03:** Health dots should be **red** for Category A path conflicts, not yellow. Test form expectations were wrong.
- **C01:** No apply fix for WSL2 paths is **correct** — Cat C is guidance only.
- **C03:** Yellow dots persisting after fix is **correct** — Cat C infra suggestions remain.
- **C04:** Red dots for NFS remote filesystem is **correct** for HIGH severity infra.
- **E01:** Green with env var substitution is **correct** — `${PUID}` resolves, paths consistent.
- **E05:** Green for single service is **correct** — no conflicts possible by definition.

### UX Observations (deferred — not bugs, design decisions for later)
- M04: Dir picker scan UX, "Explore Pipeline" button, multi-expand, redundant buttons
- These are v1.6 UX improvements, not v1.5.2 bugs

---

## Task 1: BUG 4 — Malformed YAML / Empty Compose hangs pipeline scan

**Root Cause:** `_parse_sibling_services()` in `cross_stack.py:416-418` catches ALL exceptions and returns `{}` silently. `_list_service_names()` in `pipeline.py:145-146` does the same. Backend returns HTTP 200 with `media_services: []`. Frontend sets `state.scanning = false` and calls `renderDashboard()`, but the dashboard shows no services and no error — user sees stale "scan in progress" message or empty state with no explanation.

**Files:**
- Modify: `backend/cross_stack.py:416-418`
- Modify: `backend/pipeline.py:137-147, 269-310`
- Modify: `frontend/app.js:892-906` (renderDashboard empty-state handling)
- Test: `tests/test_pipeline_errors.py` (create)

### Step 1: Write failing tests

Create `tests/test_pipeline_errors.py`:

```python
"""Tests for pipeline scan error handling — malformed YAML and empty compose files."""
import pytest
from unittest.mock import patch, MagicMock
from backend.pipeline import run_pipeline_scan, _list_service_names


class TestListServiceNames:
    """_list_service_names should handle errors gracefully with logging."""

    def test_malformed_yaml_returns_empty_list(self, tmp_path):
        """Malformed YAML should return empty list, not raise."""
        bad_file = tmp_path / "docker-compose.yml"
        bad_file.write_text("services:\n  sonarr\n    image: bad yaml here")
        result = _list_service_names(str(bad_file))
        assert result == [], "Malformed YAML should return empty list"

    def test_empty_file_returns_empty_list(self, tmp_path):
        """Empty file should return empty list."""
        empty = tmp_path / "docker-compose.yml"
        empty.write_text("")
        result = _list_service_names(str(empty))
        assert result == [], "Empty file should return empty list"

    def test_valid_yaml_returns_service_names(self, tmp_path):
        """Valid compose file should return service names."""
        good = tmp_path / "docker-compose.yml"
        good.write_text("services:\n  sonarr:\n    image: sonarr\n  radarr:\n    image: radarr\n")
        result = _list_service_names(str(good))
        assert set(result) == {"sonarr", "radarr"}, "Should return both service names"


class TestPipelineScanParseErrors:
    """Pipeline scan should report parse errors, not silently skip broken stacks."""

    def test_malformed_yaml_reported_in_result(self, tmp_path):
        """Stacks with malformed YAML should appear in parse_errors."""
        stack_dir = tmp_path / "bad-stack"
        stack_dir.mkdir()
        (stack_dir / "docker-compose.yml").write_text(
            "services:\n  sonarr\n    image: bad"
        )
        result = run_pipeline_scan(str(tmp_path))
        assert hasattr(result, "parse_errors") or "parse_errors" in result.to_dict(), \
            "Pipeline result must include parse_errors field"
        errors = result.to_dict().get("parse_errors", [])
        assert len(errors) >= 1, "Should report at least 1 parse error for malformed YAML"

    def test_empty_compose_reported_in_result(self, tmp_path):
        """Stacks with empty compose files should appear in parse_errors."""
        stack_dir = tmp_path / "empty-stack"
        stack_dir.mkdir()
        (stack_dir / "docker-compose.yml").write_text("services: {}")
        result = run_pipeline_scan(str(tmp_path))
        # Empty services is not an error — it's just an empty stack
        # The key is: the scan should COMPLETE without hanging
        assert result is not None, "Pipeline scan should complete even with empty compose"

    def test_mixed_good_and_bad_stacks(self, tmp_path):
        """Pipeline should process good stacks even when bad stacks exist."""
        # Good stack
        good = tmp_path / "good"
        good.mkdir()
        (good / "docker-compose.yml").write_text(
            "services:\n  sonarr:\n    image: lscr.io/linuxserver/sonarr\n"
            "    environment:\n      - PUID=1000\n      - PGID=1000\n"
            "    volumes:\n      - /srv/data:/data\n"
        )
        # Bad stack
        bad = tmp_path / "bad"
        bad.mkdir()
        (bad / "docker-compose.yml").write_text("invalid: yaml: here: [")
        result = run_pipeline_scan(str(tmp_path))
        # Good stack should still be found
        media = result.to_dict().get("media_services", [])
        assert any(s["service_name"] == "sonarr" for s in media), \
            "Good stacks should be discovered even when bad stacks exist"
```

### Step 2: Run tests to verify they fail

Run: `cd C:\Projects\maparr && python -m pytest tests/test_pipeline_errors.py -v`
Expected: Some tests fail (parse_errors field doesn't exist, malformed YAML is silently swallowed)

### Step 3: Implement fixes

**3a. Add `parse_errors` field to PipelineResult** (`backend/pipeline.py`):

Find the `PipelineResult` class (around line 20-50) and add:
```python
parse_errors: List[Dict] = field(default_factory=list)  # Stacks that failed YAML parsing
```

And in `to_dict()`, include `"parse_errors": self.parse_errors`.

**3b. Track parse errors in `run_pipeline_scan()`** (`backend/pipeline.py`):

In the main scan loop (around line 269), where `_parse_sibling_services()` is called, wrap in try/except and track errors:

```python
try:
    parsed = _parse_sibling_services(svc_compose)
except Exception:
    parsed = {}

if not parsed:
    # Check if file is actually broken (YAML error) vs just no media services
    try:
        import yaml
        with open(svc_compose, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if data is None or not isinstance(data, dict):
            result.parse_errors.append({
                "file": svc_compose,
                "error": "Empty or invalid compose file",
                "stack": os.path.basename(os.path.dirname(svc_compose)),
            })
    except yaml.YAMLError as ye:
        line_info = ""
        if hasattr(ye, "problem_mark") and ye.problem_mark:
            line_info = f" (line {ye.problem_mark.line + 1})"
        result.parse_errors.append({
            "file": svc_compose,
            "error": f"YAML syntax error{line_info}",
            "stack": os.path.basename(os.path.dirname(svc_compose)),
        })
    except Exception:
        pass  # Non-YAML errors (permission, encoding) — skip
```

**3c. Report parse errors in frontend** (`frontend/app.js`):

In `renderDashboard()` (around line 892-906), after setting `state.pipeline`, check for parse errors:

```javascript
// After: state.scanning = false;
// Show parse error warnings if any stacks failed to load
const parseErrors = data.parse_errors || [];
if (parseErrors.length > 0) {
    for (const err of parseErrors) {
        showSimpleToast(err.stack + ": " + err.error, "error");
    }
}
```

Also, if `state.services.length === 0` and `parseErrors.length > 0`, show a specific message in the health banner instead of the generic empty state:

```javascript
if (state.services.length === 0 && parseErrors.length > 0) {
    // All stacks had parse errors — show meaningful error
    if (bannerText) bannerText.textContent =
        parseErrors.length + " compose file" + (parseErrors.length > 1 ? "s" : "") +
        " could not be parsed. Check for YAML syntax errors.";
    if (bannerIcon) bannerIcon.className = "health-banner-icon health-problem";
}
```

### Step 4: Run all tests

Run: `cd C:\Projects\maparr && python -m pytest tests/test_pipeline_errors.py -v`
Expected: All PASS

Run: `cd C:\Projects\maparr && python -m pytest tests/ -x --timeout=30`
Expected: All existing tests still pass (regression check)

### Step 5: Commit

```bash
git add tests/test_pipeline_errors.py backend/pipeline.py backend/cross_stack.py frontend/app.js
git commit -m "fix: report YAML parse errors in pipeline scan instead of hanging

Malformed YAML and empty compose files were silently swallowed by
_parse_sibling_services() and _list_service_names(), returning empty
results with no error. Frontend showed stale 'scan in progress' state.

Now: parse errors tracked in PipelineResult.parse_errors, surfaced
as toast messages, and health banner shows meaningful error when all
stacks fail to parse.

Fixes: E03, E04 manual test failures."
```

---

## Task 2: BUG 3 — Pipeline-view "Apply All Fixes" fails with path boundary error

**Root Cause Investigation Required:** This bug needs live debugging. The pipeline-view "Apply" button sends compose_file_path to `/api/apply-fixes`, which validates against `_get_stacks_root()`. The drill-in view uses `/api/apply-fix` with the same paths and works. Two theories:

1. **Rate limiter was blocking `/api/analyze` calls** in `generateFixPlans()` fallback (line 1942-1964), causing the per-stack analysis to fail silently (`catch (e) {}`), resulting in empty/wrong paths. Now that we've fixed the rate limiter, this may already work.

2. **Path resolution mismatch** between what `_get_stacks_root()` returns (session-based) and what `apply_multi.py` validates against. On Windows, `Path.resolve()` normalizations may differ.

**Files:**
- Modify: `frontend/app.js:1903-1964` (generateFixPlans error handling)
- Modify: `frontend/app.js:2248-2300` (applyAllFixes error display)
- Modify: `backend/apply_multi.py:34-64` (path validation logging)
- Test: `tests/test_apply_multi.py` (create or extend)

### Step 1: Add diagnostic logging to apply_multi.py

In `validate_fixes_batch()` around line 60-63, add logging:

```python
try:
    resolved_path = path.resolve()
    resolved_path.relative_to(root)
except ValueError:
    logger.warning(
        "Path boundary check failed: path=%s resolved=%s root=%s",
        path_str, path.resolve(), root,
    )
    errors.append(...)
```

### Step 2: Add error logging to generateFixPlans fallback

In `frontend/app.js` at line 1961-1962, the catch block silently swallows errors. Log them:

```javascript
} catch (e) {
    console.warn("[fix-plans] Analysis failed for", stackPath, e.message || e);
}
```

### Step 3: Test live with the rate limiter fix in place

Before writing implementation code, test R01 and P07 scenarios manually to see if the rate limiter fix already resolved this. If `/api/analyze` calls in `generateFixPlans()` were being rate-limited, the plans would have been empty, causing the "path outside stacks" error on the batch endpoint (because it gets empty paths or no plans at all).

**If the rate limiter fix already resolved this:**
- Add the logging improvements anyway (defensive)
- Add better error display in `applyAllFixes()` to show which specific file failed
- Commit and move on

**If it still fails after rate limiter fix:**
- Investigate the path resolution mismatch using the diagnostic logs
- The fix will likely be normalizing paths in `apply_multi.py` before comparison
- Both `path.resolve()` and `root.resolve()` should use `Path(x).resolve()` consistently

### Step 4: Improve error display in applyAllFixes

In `applyAllFixes()` at line 2268, when the response has errors, show which files failed:

```javascript
if (data.status === "validation_failed") {
    const errors = data.errors || [];
    for (const err of errors) {
        const fileName = (err.compose_file_path || "").split(/[/\\]/).pop() || "unknown";
        showSimpleToast(fileName + ": " + err.error, "error");
    }
}
```

### Step 5: Run all tests and commit

Run: `cd C:\Projects\maparr && python -m pytest tests/ -x --timeout=30`

```bash
git add frontend/app.js backend/apply_multi.py
git commit -m "fix: improve pipeline-view Apply Fix error handling and path logging

Add diagnostic logging to path boundary validation in apply_multi.py.
Log errors in generateFixPlans fallback instead of silently swallowing.
Show per-file error detail when batch apply fails validation.

May have been caused by rate limiter blocking /api/analyze calls in
generateFixPlans(), now resolved by rate limiter fix."
```

---

## Task 3: BUG 6 — Paste textarea not cleared after fix/navigation

**Root Cause:** `backToDashboard()` at line 3377 and `enablePasteBar()` at line 2899 don't reset the textarea value or paste-related state. After applying a fix and returning to dashboard, old error text persists.

**Files:**
- Modify: `frontend/app.js:3377-3380` (backToDashboard)
- No new test file (frontend-only, verified by manual test)

### Step 1: Clear paste state in backToDashboard

At `app.js:3377`, modify `backToDashboard()`:

```javascript
function backToDashboard() {
    // Clear paste state so old errors don't persist
    const pasteInput = document.getElementById("paste-error-input");
    if (pasteInput) {
        pasteInput.value = "";
        // Collapse the paste bar if it was expanded
        const pasteBar = pasteInput.closest(".paste-bar-expanded");
        if (pasteBar) pasteBar.classList.remove("paste-bar-expanded");
    }
    state.pastedError = null;
    state.highlightedServices = [];
    // Clear any paste result messages
    const pasteResult = document.getElementById("paste-result");
    if (pasteResult) pasteResult.textContent = "";

    hideAnalysisCards();
    show("pipeline-dashboard");
}
```

### Step 2: Verify manually

1. Browse to a paste test stack (P01)
2. Paste an error, analyze, apply fix
3. When returned to dashboard, paste textarea should be empty
4. Browse to a different stack (P02) — paste area should be clean

### Step 3: Commit

```bash
git add frontend/app.js
git commit -m "fix: clear paste textarea and state when returning to dashboard

After applying a fix via paste flow, backToDashboard() now resets the
textarea value, clears paste-related state (pastedError, highlightedServices),
and removes any paste result messages. Prevents stale error text from
persisting across stack navigation.

Fixes: P01, P02 paste-persistence test failures."
```

---

## Task 4: BUG 7 — Inconsistent Apply Fix flow (Proper Fix track)

**Root Cause:** The "Proper Fix" track's "Apply All Fixes" button at line 7005-7110 uses a separate code path: simple confirm modal → direct fetch to `/api/apply-fix` → toast only. Other apply buttons use `showDiffPreview()` → `applyAllFixes()` → diff modal + redeploy banner.

**Files:**
- Modify: `frontend/app.js:6995-7122` (Proper Fix track apply button)

### Step 1: Replace the inline apply flow with showDiffPreview

At `app.js:6995-7122`, the current code builds its own confirm modal and fetches directly. Replace the `applyBtn` click handler to use the same diff preview flow:

```javascript
// Apply Fix button for the Proper Fix track
if (data && data.original_corrected_yaml && data.compose_file_path) {
    _lastAnalysisForApply = data;

    const applyWrap = document.createElement("div");
    applyWrap.className = "cross-stack-apply-wrap";
    applyWrap.style.marginTop = "1rem";

    const applyBtn = document.createElement("button");
    applyBtn.className = "apply-btn";
    applyBtn.textContent = "Apply All Fixes";
    applyBtn.addEventListener("click", () => {
        // Build a plan compatible with showDiffPreview
        const plan = {
            compose_file_path: data.compose_file_path,
            original_corrected_yaml: data.original_corrected_yaml,
            original_changed_lines: data.original_changed_lines || [],
            corrected_yaml: data.original_corrected_yaml,
            changed_lines: data.original_changed_lines || [],
            stack_name: data.compose_file_path.replace(/\\/g, "/").split("/").slice(-2, -1)[0] || "",
        };
        showDiffPreview([plan], applyBtn);
    });
    applyWrap.appendChild(applyBtn);

    properContent.appendChild(applyWrap);
}
```

This removes the entire `confirmWrap`, `resultDiv`, inline fetch, and replaces it with the standard `showDiffPreview()` → `applyAllFixes()` flow that includes:
- Diff preview with line-level changes
- Proper confirm button
- `renderRedeployBanner()` after success
- Revert button if backup exists
- Pipeline rescan

### Step 2: Remove the old inline code

Delete lines 7009-7121 (the confirmWrap, btnRow, yesBtn, noBtn, resultDiv, the entire inline apply flow).

### Step 3: Verify manually

1. Browse to a Cat A stack (A01 or A02)
2. Drill into a service → analysis → RPM wizard should show "Proper Fix" track
3. Click "Apply All Fixes" in the Proper Fix track
4. Should see diff preview modal (same as other apply buttons)
5. Confirm → should see redeploy banner (not just toast)

### Step 4: Commit

```bash
git add frontend/app.js
git commit -m "fix: unify Proper Fix track apply flow with diff preview

The Proper Fix track's Apply All Fixes button used a separate inline
confirm-and-fetch flow that skipped the diff preview, redeploy banner,
and revert button. Now uses showDiffPreview() like all other apply
buttons for consistent UX.

Fixes: M02, M03, P03, P04 inconsistent apply-fix test failures."
```

---

## Task 5: BUG 5 — Paste error parser can't identify service in P02/P05/P06

**Root Cause:** `_extract_service()` in `parser.py:205-239` does substring search for known service names. These error messages don't mention the service name:
- P02: `"Import failed: [/downloads/complete/Movie.2024...] error code EXDEV (18): Cross-device link"` — no service name
- P05: `"Episode file path '/downloads/complete/tv/...' is not valid. Ensure the Remote Path Mapping is configured correctly."` — no service name
- P06: `"No files found are eligible for import in /data/downloads/complete/..."` — no service name

These are *arr app errors (Sonarr/Radarr), but the error text only describes the problem, not who logged it.

**Files:**
- Modify: `backend/parser.py:140-200` (parse_error function)
- Test: `tests/test_parser.py` (extend or create)

### Step 1: Write failing tests

```python
"""Tests for error parser service extraction from error text."""
import pytest
from backend.parser import parse_error


class TestParserServiceExtraction:
    """Parser should identify service from context clues when name isn't explicit."""

    def test_exdev_crossdevice_implies_arr(self):
        """EXDEV cross-device link errors come from arr apps during import."""
        text = "[Error] Import failed: [/downloads/complete/Movie.2024.1080p/Movie.2024.1080p.mkv] Import failed, error code EXDEV (18): Cross-device link"
        result = parse_error(text)
        assert result.service is not None, "EXDEV error should identify as arr app"
        assert result.error_type == "hardlink_failed", "EXDEV should be classified as hardlink failure"

    def test_remote_path_mapping_implies_arr(self):
        """Remote Path Mapping errors come from arr apps."""
        text = "[Warn] Couldn't import episode /downloads/complete/tv/Some.Show.S02E05.mkv: Episode file path '/downloads/complete/tv/Some.Show.S02E05.mkv' is not valid. Ensure the Remote Path Mapping is configured correctly."
        result = parse_error(text)
        assert result.service is not None, "RPM error should identify as arr app"
        assert result.error_type == "remote_path_mapping", "RPM error should be classified correctly"

    def test_no_eligible_files_implies_arr(self):
        """'No files found eligible for import' comes from arr apps."""
        text = "[Warn] No files found are eligible for import in /data/downloads/complete/Some.Show.S01E01"
        result = parse_error(text)
        assert result.service is not None, "Import eligibility error should identify as arr app"
```

### Step 2: Run tests to verify they fail

Run: `cd C:\Projects\maparr && python -m pytest tests/test_parser.py -v -k "service_extraction"`
Expected: Failures — `result.service` is None

### Step 3: Implement contextual service inference

In `parser.py`, after `_extract_service()` returns None, add a fallback that infers the likely service from error context:

```python
def _infer_service_from_context(text: str, error_type: str) -> Optional[str]:
    """
    When no service name is found in the error text, infer from context clues.

    Many *arr app errors (import failed, EXDEV, Remote Path Mapping) don't
    include the service name. We can confidently attribute them to the *arr
    category, but we return a generic "arr" indicator and let the frontend
    match against the user's actual pipeline.
    """
    text_lower = text.lower()

    # EXDEV / cross-device link errors are always from arr apps during import
    if "exdev" in text_lower or "cross-device link" in text_lower:
        return "*arr"  # Generic arr indicator

    # Remote Path Mapping errors are always arr app errors
    if "remote path mapping" in text_lower:
        return "*arr"

    # "Import failed" without a service name — arr app
    if "import failed" in text_lower:
        return "*arr"

    # "No files found are eligible for import" — arr app
    if "eligible for import" in text_lower or "no files found" in text_lower:
        return "*arr"

    # "Episode file path" / "movie file path" — Sonarr/Radarr specifically
    if "episode file path" in text_lower:
        return "sonarr"
    if "movie file path" in text_lower:
        return "radarr"

    return None
```

Then in the main `parse_error()` function, after `_extract_service()` returns None, call the inference:

```python
result.service = _extract_service(text)
if not result.service:
    result.service = _infer_service_from_context(text, result.error_type)
```

**Frontend change needed:** The `handlePasteError()` function at line 2966 checks `parsed.service` for exact match against `state.services`. When service is `"*arr"`, match any service with role "arr":

```javascript
if (!parsed || !parsed.service) {
    showPasteResult("Could not identify a service...", "error");
    return;
}

// Support generic "*arr" indicator — match any arr app in pipeline
let matched;
if (parsed.service === "*arr") {
    matched = state.services.filter(s => s.role === "arr");
} else {
    matched = state.services.filter(s =>
        s.service_name.toLowerCase().includes(parsed.service.toLowerCase()) ||
        parsed.service.toLowerCase().includes(s.service_name.toLowerCase())
    );
}
```

### Step 4: Run tests

Run: `cd C:\Projects\maparr && python -m pytest tests/test_parser.py -v`
Expected: All PASS

### Step 5: Commit

```bash
git add backend/parser.py frontend/app.js tests/test_parser.py
git commit -m "fix: infer service from error context when name not in text

EXDEV, Remote Path Mapping, and 'no eligible files' errors from arr apps
don't include the service name. Added _infer_service_from_context() that
detects arr-specific error patterns and returns '*arr' generic indicator.
Frontend matches '*arr' against any arr-role service in the pipeline.

Fixes: P02, P05, P06 paste parser test failures."
```

---

## Task 6: BUG 8 — TZ mismatch labelled as "permission issue"

**Root Cause:** `app.js:1025` hardcodes all Category B as "permission mismatch". But Category B includes timezone and umask issues that aren't permissions. Similarly, `app.js:4938` hardcodes "Apply Permission Fix" button text for all Cat B.

**Files:**
- Modify: `frontend/app.js:1023-1027` (category labels)
- Modify: `frontend/app.js:4936-4938` (button text)

### Step 1: Make Category B label dynamic

Replace the hardcoded label at line 1023-1027:

```javascript
const categories = [
    { key: "A", label: "path issue", plural: "path issues", cls: "summary-high" },
    { key: "B", label: null, plural: null, cls: "summary-medium" },  // Dynamic — set below
    { key: "C", label: "infrastructure note", plural: "infrastructure notes", cls: "summary-low" },
];
```

Before the rendering loop, compute the dynamic Cat B label from actual conflict types:

```javascript
// Compute dynamic label for Category B based on actual conflict types present
const catBConflicts = conflicts.filter(c => (c.category || "").toUpperCase() === "B");
const catBTypes = new Set(catBConflicts.map(c => c.type));
let catBLabel, catBPlural;
if (catBTypes.size === 1 && (catBTypes.has("tz_mismatch") || catBTypes.has("missing_tz"))) {
    catBLabel = "timezone issue";
    catBPlural = "timezone issues";
} else if (catBTypes.size === 1 && (catBTypes.has("umask_inconsistent") || catBTypes.has("umask_restrictive"))) {
    catBLabel = "umask issue";
    catBPlural = "umask issues";
} else if (catBTypes.has("tz_mismatch") || catBTypes.has("missing_tz") ||
           catBTypes.has("umask_inconsistent") || catBTypes.has("umask_restrictive")) {
    catBLabel = "permission/environment issue";
    catBPlural = "permission/environment issues";
} else {
    catBLabel = "permission mismatch";
    catBPlural = "permission mismatches";
}

// Patch the B category entry
for (const cat of categories) {
    if (cat.key === "B") {
        cat.label = catBLabel;
        cat.plural = catBPlural;
        break;
    }
}
```

### Step 2: Make Apply button text dynamic

At line 4936-4938, replace the hardcoded "Apply Permission Fix":

```javascript
// Determine button text based on actual conflict types
const envTypes = new Set(
    (data.conflicts || [])
        .filter(c => (c.category || "").toUpperCase() === "B")
        .map(c => c.type)
);
let envBtnLabel;
if (envTypes.size === 1 && (envTypes.has("tz_mismatch") || envTypes.has("missing_tz"))) {
    envBtnLabel = envFixPlans.length === 1 ? "Apply Timezone Fix" : "Apply Timezone Fixes (" + envFixPlans.length + " files)";
} else if (envTypes.size === 1 && (envTypes.has("umask_inconsistent") || envTypes.has("umask_restrictive"))) {
    envBtnLabel = envFixPlans.length === 1 ? "Apply Umask Fix" : "Apply Umask Fixes (" + envFixPlans.length + " files)";
} else {
    envBtnLabel = envFixPlans.length === 1 ? "Apply Environment Fix" : "Apply Environment Fixes (" + envFixPlans.length + " files)";
}
envApplyBtn.textContent = envBtnLabel;
```

Also update the "Fix Permissions" tab name at line 4876 to be dynamic:

```javascript
let envTabLabel;
if (envTypes.size === 1 && (envTypes.has("tz_mismatch") || envTypes.has("missing_tz"))) {
    envTabLabel = "Fix Timezone";
} else if (envTypes.size === 1 && (envTypes.has("umask_inconsistent") || envTypes.has("umask_restrictive"))) {
    envTabLabel = "Fix Umask";
} else {
    envTabLabel = "Fix Environment";
}
envTabBtn.textContent = envTabLabel;
```

### Step 3: Verify manually

1. Browse to B06-tz-mismatch
2. Summary should say "1 timezone issue" (not "1 permission mismatch")
3. Tab should say "Fix Timezone" (not "Fix Permissions")
4. Button should say "Apply Timezone Fix"
5. Browse to B01-puid-mismatch — should still say "permission mismatch"
6. Browse to M01-path-plus-permissions — mixed types should say "permission/environment issue"

### Step 4: Commit

```bash
git add frontend/app.js
git commit -m "fix: dynamic Category B labels based on actual conflict types

Hardcoded 'permission mismatch' label now adapts to the actual conflict
types present: timezone issues, umask issues, or mixed permission/environment.
Tab names and apply button text also adapt accordingly.

Fixes: B06 TZ-mismatch labelling test failure."
```

---

## Task 7: BUG 2 — RPM Wizard not showing in some Cat A scenarios

**Root Cause:** RPM wizard gating at `analyzer.py:780-782` requires `pipeline_context` AND at least one Cat A conflict AND `_calculate_rpm_mappings()` returns at least one mapping with `possible=True`. The `possible` flag requires host path overlap via `_find_host_overlap()`.

Many test stacks (A01, A04, A05) have path structures where the host paths DON'T overlap — meaning RPM literally can't help. In those cases, **not showing the wizard is correct behavior**.

**Investigation needed first:** For each failing test stack, check whether RPM should actually be possible:

- **A01 (Separate Mount Trees):** Services use completely different host paths (e.g., `/mnt/media` vs `/downloads`). No overlap → RPM impossible → wizard correctly hidden.
- **A04 (Unreachable Path):** Path doesn't exist in container. RPM maps paths, doesn't create them → wizard correctly hidden.
- **A05 (Partial Overlap):** Paths partially overlap. RPM might work if the overlap is meaningful.
- **M01 (Path + Permissions):** Mixed issues. RPM might apply to the path portion.
- **F03 (Custom Images):** Unknown image families → can't determine RPM app config format.
- **CL01 (Cluster Broken Paths):** Cross-file cluster → RPM might apply.

**Files:**
- Modify: `backend/analyzer.py:2778-2850` (only if legitimate gaps found)
- Modify: Test form expectations (if wizard correctly absent)

### Step 1: Audit each failing stack

For each test stack where RPM wizard didn't show, run the analyzer manually and check `rpm_mappings`:

```python
# Quick diagnostic script
import json
from backend.analyzer import analyze_stack

result = analyze_stack("C:/Projects/maparr/test-stacks/single/A01-separate-mount-trees")
print("RPM mappings:", json.dumps(result.get("rpm_mappings", []), indent=2))
print("Has Cat A:", any(c.get("category") == "A" for c in result.get("conflicts", [])))
```

Run this for A01, A04, A05, M01, F03, CL01.

### Step 2: Classify results

For each stack, determine:
- **RPM impossible (correct to hide):** Update test form expectations
- **RPM possible but not shown (bug):** Fix the gating logic

### Step 3: Fix only genuine bugs

If any stacks have `rpm_mappings` with `possible=True` but the wizard doesn't show, the bug is in the frontend gating (`app.js:4988-4993`). Check if `data.rpm_mappings` is populated but the frontend filter is wrong.

If all stacks correctly hide the wizard (RPM impossible for their path structures), update the test form's expectations to mark RPM as "N/A" for those scenarios.

### Step 4: Commit

```bash
git add backend/analyzer.py frontend/app.js  # only if code changes made
git commit -m "fix: audit RPM wizard visibility for edge case path structures

Investigated RPM wizard not showing in A01, A04, A05, M01, F03, CL01.
[Describe what was found — either test expectations updated or gating
logic fixed for specific path structures.]"
```

---

## Task 8: Update test form expectations

**Files:**
- Modify: `tools/testing-form.html` or equivalent test expectations file

Update the expected results for scenarios where the test form had incorrect expectations:

| Test | Current Expectation | Correct Expectation | Why |
|------|-------------------|-------------------|-----|
| A02, A03 | Health dots: yellow | Health dots: **red** | Cat A path conflicts are critical/high → red |
| C01 | Apply fix expected | **No apply fix** | Cat C is guidance only |
| C03 | Post-fix all green | **Post-fix: top green, services yellow** | Cat C infra persists |
| C04 | Health dots: yellow | Health dots: **red** | HIGH severity infra → red |
| E01 | Fail | **Pass** — green with env substitution is correct |
| E05 | Fail | **Pass** — single service green is correct |
| RPM wizard | Expected everywhere for Cat A | **Only when RPM is possible** (host paths overlap) |

### Step 1: Update expectations, commit

```bash
git add tools/testing-form.html
git commit -m "fix: correct test form expectations for health dots, Cat C, and RPM

Red health dots are correct for Cat A critical/high conflicts.
Cat C infrastructure suggestions don't have apply-fix buttons.
RPM wizard only shows when host paths overlap (not all Cat A).
Single service and env substitution scenarios are green by design."
```

---

## Execution Notes

- Tasks 1-6 are code changes — implement with TDD where tests exist
- Task 7 is investigation-first — audit before coding
- Task 8 is test expectations only
- Run full test suite after each task: `python -m pytest tests/ -x --timeout=30`
- Run full test suite after ALL tasks: `python -m pytest tests/ --timeout=30` (no `-x`, see all results)
- Manual smoke test after all tasks: re-run P01, P02, E03, E04, B06, R01 from the testing form
