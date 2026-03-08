# Pipeline Dashboard — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the stack-grid UI with a service-first Pipeline Dashboard that shows all media services grouped by role, supports multi-file fixes, and optionally redeploys containers via Docker API.

**Architecture:** Frontend rewrite (app.js, index.html, styles.css) with targeted backend additions (multi-file apply endpoint, redeploy endpoint). Pipeline engine, 4-pass analysis, Image DB, fix generation, and error parser carry forward unchanged. Incremental delivery: backend endpoints first, then frontend piece by piece.

**Tech Stack:** Python 3.11 / FastAPI backend, vanilla JS frontend, no new dependencies.

---

## Task 1: Multi-File Apply Fix Endpoint

The foundation — batch apply fixes to multiple compose files in one request.

**Files:**
- Create: `backend/apply_multi.py`
- Modify: `backend/main.py` (add route + rate limit)
- Create: `tests/test_apply_multi.py`

**Step 1: Write tests for batch validation**

```python
# tests/test_apply_multi.py
import os
import tempfile
import pytest
from backend.apply_multi import validate_fixes_batch, apply_fixes_batch

class TestValidateFixesBatch:
    def test_all_valid(self, tmp_path):
        """All files exist, valid YAML, within boundary."""
        f1 = tmp_path / "sonarr" / "docker-compose.yml"
        f1.parent.mkdir()
        f1.write_text("services:\n  sonarr:\n    image: test\n")
        f2 = tmp_path / "radarr" / "docker-compose.yml"
        f2.parent.mkdir()
        f2.write_text("services:\n  radarr:\n    image: test\n")

        fixes = [
            {"compose_file_path": str(f1), "corrected_yaml": "services:\n  sonarr:\n    image: fixed\n"},
            {"compose_file_path": str(f2), "corrected_yaml": "services:\n  radarr:\n    image: fixed\n"},
        ]
        errors = validate_fixes_batch(fixes, stacks_root=str(tmp_path))
        assert errors == []

    def test_file_not_found(self, tmp_path):
        fixes = [{"compose_file_path": str(tmp_path / "nope" / "docker-compose.yml"), "corrected_yaml": "services:\n  x:\n    image: y\n"}]
        errors = validate_fixes_batch(fixes, stacks_root=str(tmp_path))
        assert len(errors) == 1
        assert "not found" in errors[0]["error"].lower()

    def test_path_outside_boundary(self, tmp_path):
        evil = tmp_path / ".." / "etc" / "docker-compose.yml"
        fixes = [{"compose_file_path": str(evil), "corrected_yaml": "services:\n  x:\n    image: y\n"}]
        errors = validate_fixes_batch(fixes, stacks_root=str(tmp_path))
        assert len(errors) == 1
        assert "outside" in errors[0]["error"].lower()

    def test_invalid_yaml(self, tmp_path):
        f1 = tmp_path / "bad" / "docker-compose.yml"
        f1.parent.mkdir()
        f1.write_text("services:\n  x:\n    image: test\n")
        fixes = [{"compose_file_path": str(f1), "corrected_yaml": "not: valid: yaml: [[["}]
        errors = validate_fixes_batch(fixes, stacks_root=str(tmp_path))
        assert len(errors) == 1

    def test_yaml_missing_services_key(self, tmp_path):
        f1 = tmp_path / "nosvcs" / "docker-compose.yml"
        f1.parent.mkdir()
        f1.write_text("services:\n  x:\n    image: test\n")
        fixes = [{"compose_file_path": str(f1), "corrected_yaml": "version: '3'\nnetworks:\n  default:\n"}]
        errors = validate_fixes_batch(fixes, stacks_root=str(tmp_path))
        assert len(errors) == 1
        assert "services" in errors[0]["error"].lower()

    def test_bad_filename(self, tmp_path):
        f1 = tmp_path / "hack" / "config.yml"
        f1.parent.mkdir()
        f1.write_text("services:\n  x:\n    image: test\n")
        fixes = [{"compose_file_path": str(f1), "corrected_yaml": "services:\n  x:\n    image: fixed\n"}]
        errors = validate_fixes_batch(fixes, stacks_root=str(tmp_path))
        assert len(errors) == 1
        assert "compose file" in errors[0]["error"].lower()
```

**Step 2: Write tests for batch apply**

```python
# tests/test_apply_multi.py (continued)

class TestApplyFixesBatch:
    def test_applies_all_successfully(self, tmp_path):
        f1 = tmp_path / "sonarr" / "docker-compose.yml"
        f1.parent.mkdir()
        f1.write_text("services:\n  sonarr:\n    image: old\n")
        f2 = tmp_path / "radarr" / "docker-compose.yml"
        f2.parent.mkdir()
        f2.write_text("services:\n  radarr:\n    image: old\n")

        fixes = [
            {"compose_file_path": str(f1), "corrected_yaml": "services:\n  sonarr:\n    image: new\n"},
            {"compose_file_path": str(f2), "corrected_yaml": "services:\n  radarr:\n    image: new\n"},
        ]
        result = apply_fixes_batch(fixes, stacks_root=str(tmp_path))
        assert result["status"] == "applied"
        assert result["applied_count"] == 2
        assert result["failed_count"] == 0
        assert "new" in f1.read_text()
        assert "new" in f2.read_text()
        # Backups exist
        assert (tmp_path / "sonarr" / "docker-compose.yml.bak").exists()
        assert (tmp_path / "radarr" / "docker-compose.yml.bak").exists()

    def test_backups_created_before_any_write(self, tmp_path):
        f1 = tmp_path / "a" / "docker-compose.yml"
        f1.parent.mkdir()
        original = "services:\n  a:\n    image: original\n"
        f1.write_text(original)

        fixes = [{"compose_file_path": str(f1), "corrected_yaml": "services:\n  a:\n    image: fixed\n"}]
        result = apply_fixes_batch(fixes, stacks_root=str(tmp_path))
        bak = tmp_path / "a" / "docker-compose.yml.bak"
        assert bak.exists()
        assert bak.read_text() == original

    def test_validation_failure_blocks_all_writes(self, tmp_path):
        f1 = tmp_path / "good" / "docker-compose.yml"
        f1.parent.mkdir()
        f1.write_text("services:\n  x:\n    image: old\n")

        fixes = [
            {"compose_file_path": str(f1), "corrected_yaml": "services:\n  x:\n    image: new\n"},
            {"compose_file_path": str(tmp_path / "missing" / "docker-compose.yml"), "corrected_yaml": "services:\n  y:\n    image: z\n"},
        ]
        result = apply_fixes_batch(fixes, stacks_root=str(tmp_path))
        assert result["status"] == "validation_failed"
        # First file should NOT have been written
        assert "old" in f1.read_text()

    def test_line_endings_normalized(self, tmp_path):
        f1 = tmp_path / "crlf" / "docker-compose.yml"
        f1.parent.mkdir()
        f1.write_text("services:\n  x:\n    image: old\n")
        yaml_with_crlf = "services:\r\n  x:\r\n    image: fixed\r\n"
        fixes = [{"compose_file_path": str(f1), "corrected_yaml": yaml_with_crlf}]
        result = apply_fixes_batch(fixes, stacks_root=str(tmp_path))
        assert result["status"] == "applied"
        assert "\r\n" not in f1.read_text()

    def test_empty_fixes_list(self, tmp_path):
        result = apply_fixes_batch([], stacks_root=str(tmp_path))
        assert result["status"] == "applied"
        assert result["applied_count"] == 0
```

**Step 3: Implement apply_multi.py**

```python
# backend/apply_multi.py
"""
Multi-file apply fix — batch apply corrected YAML to multiple compose files.

Validates all files before writing any. Creates backups for all files first.
If any validation fails, no files are written.
"""
import logging
import os
import shutil
from pathlib import Path
from typing import List, Optional

import yaml

logger = logging.getLogger(__name__)

COMPOSE_FILENAMES = {
    "docker-compose.yml", "docker-compose.yaml",
    "compose.yml", "compose.yaml",
}


def validate_fixes_batch(fixes: List[dict], stacks_root: str) -> List[dict]:
    """
    Validate all fixes before any writes.

    Returns list of error dicts. Empty list = all valid.
    Each error: {"compose_file_path": str, "error": str}
    """
    errors = []
    root = Path(stacks_root).resolve()

    for fix in fixes:
        path_str = fix.get("compose_file_path", "").strip()
        corrected = fix.get("corrected_yaml", "").strip()

        if not path_str:
            errors.append({"compose_file_path": path_str, "error": "Empty path"})
            continue

        path = Path(path_str)

        # File exists?
        if not path.is_file():
            errors.append({"compose_file_path": path_str, "error": f"File not found: {path.name}"})
            continue

        # Within stacks root?
        try:
            path.resolve().relative_to(root)
        except ValueError:
            errors.append({"compose_file_path": path_str, "error": "Path outside stacks directory"})
            continue

        # Valid compose filename?
        if path.name not in COMPOSE_FILENAMES:
            errors.append({"compose_file_path": path_str, "error": f"Not a recognised compose file: {path.name}"})
            continue

        # Valid YAML with services key?
        if not corrected:
            errors.append({"compose_file_path": path_str, "error": "Empty corrected YAML"})
            continue

        try:
            parsed = yaml.safe_load(corrected)
            if not isinstance(parsed, dict) or "services" not in parsed:
                errors.append({"compose_file_path": path_str, "error": "Corrected YAML missing services key"})
                continue
        except yaml.YAMLError as e:
            errors.append({"compose_file_path": path_str, "error": f"Invalid YAML: {e}"})
            continue

    return errors


def apply_fixes_batch(fixes: List[dict], stacks_root: str) -> dict:
    """
    Apply corrected YAML to multiple compose files.

    Strategy: validate all → backup all → write all.
    If validation fails, no files are touched.
    If a write fails mid-batch, already-written files stay written,
    backups remain for manual recovery.

    Returns:
        {
            "status": "applied" | "validation_failed" | "partial",
            "applied_count": int,
            "failed_count": int,
            "results": [{"compose_file_path", "status", "backup_file", "error?"}, ...],
            "errors": [{"compose_file_path", "error"}, ...]  (validation errors only)
        }
    """
    if not fixes:
        return {"status": "applied", "applied_count": 0, "failed_count": 0, "results": [], "errors": []}

    # Phase 1: Validate all
    errors = validate_fixes_batch(fixes, stacks_root)
    if errors:
        return {"status": "validation_failed", "applied_count": 0, "failed_count": len(errors), "results": [], "errors": errors}

    # Phase 2: Create all backups
    backups = {}
    for fix in fixes:
        path_str = fix["compose_file_path"]
        backup_path = path_str + ".bak"
        try:
            shutil.copy2(path_str, backup_path)
            backups[path_str] = backup_path
        except OSError as e:
            logger.error("Failed to create backup for %s: %s", path_str, e)
            return {
                "status": "partial",
                "applied_count": 0,
                "failed_count": len(fixes),
                "results": [{"compose_file_path": path_str, "status": "backup_failed", "error": str(e)}],
                "errors": [],
            }

    # Phase 3: Write all files
    results = []
    applied = 0
    failed = 0

    for fix in fixes:
        path_str = fix["compose_file_path"]
        corrected = fix["corrected_yaml"].replace("\r\n", "\n")
        try:
            with open(path_str, "w", encoding="utf-8", newline="") as f:
                f.write(corrected)
            results.append({
                "compose_file_path": path_str,
                "status": "applied",
                "compose_file": os.path.basename(path_str),
                "backup_file": os.path.basename(backups[path_str]),
            })
            applied += 1
            logger.info("Applied fix to %s (backup: %s)", path_str, backups[path_str])
        except OSError as e:
            results.append({
                "compose_file_path": path_str,
                "status": "failed",
                "error": str(e),
            })
            failed += 1
            logger.error("Failed to write %s: %s", path_str, e)

    status = "applied" if failed == 0 else "partial" if applied > 0 else "failed"
    return {
        "status": status,
        "applied_count": applied,
        "failed_count": failed,
        "results": results,
        "errors": [],
    }
```

**Step 4: Add endpoint to main.py**

Add to `RateLimiter.WRITE_PATHS`:
```python
WRITE_PATHS = ("/api/apply-fix", "/api/apply-fixes", "/api/change-stacks-path")
```

Add endpoint after existing `/api/apply-fix`:
```python
@app.post("/api/apply-fixes")
async def api_apply_fixes(request: Request):
    """Apply corrected YAML to multiple compose files in one batch."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    fixes = body.get("fixes", [])
    if not isinstance(fixes, list):
        return JSONResponse({"error": "fixes must be a list"}, status_code=400)
    if len(fixes) > 20:
        return JSONResponse({"error": "Maximum 20 files per batch"}, status_code=400)

    stacks_root = _get_stacks_root()
    if not stacks_root:
        return JSONResponse(
            {"error": "Apply Fix requires MAPARR_STACKS_PATH to be set for security."},
            status_code=403,
        )

    from backend.apply_multi import apply_fixes_batch
    result = apply_fixes_batch(fixes, stacks_root)

    if result["status"] == "validation_failed":
        return JSONResponse(result, status_code=400)

    return JSONResponse(result)
```

**Step 5: Run tests**

Run: `pytest tests/test_apply_multi.py -v -p no:capture`
Expected: All pass

**Step 6: Commit**

```bash
git add backend/apply_multi.py tests/test_apply_multi.py backend/main.py
git commit -m "feat: multi-file apply fix endpoint with batch validation"
```

---

## Task 2: Redeploy Endpoint

Docker-managed restart via compose commands.

**Files:**
- Create: `backend/redeploy.py`
- Modify: `backend/main.py` (add route + rate limit)
- Create: `tests/test_redeploy.py`

**Step 1: Write tests**

```python
# tests/test_redeploy.py
import pytest
from unittest.mock import patch, MagicMock
from backend.redeploy import run_compose_action, validate_for_redeploy

class TestValidateForRedeploy:
    def test_valid_stack(self, tmp_path):
        f = tmp_path / "sonarr" / "docker-compose.yml"
        f.parent.mkdir()
        f.write_text("services:\n  sonarr:\n    image: test\n")
        errors = validate_for_redeploy(str(f.parent), stacks_root=str(tmp_path))
        assert errors == []

    def test_no_compose_file(self, tmp_path):
        d = tmp_path / "empty"
        d.mkdir()
        errors = validate_for_redeploy(str(d), stacks_root=str(tmp_path))
        assert len(errors) == 1
        assert "compose file" in errors[0].lower()

    def test_outside_boundary(self, tmp_path):
        errors = validate_for_redeploy("/etc", stacks_root=str(tmp_path))
        assert len(errors) == 1
        assert "outside" in errors[0].lower()

class TestRunComposeAction:
    @patch("backend.redeploy.subprocess.run")
    def test_up_success(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(returncode=0, stdout="Creating sonarr...", stderr="")
        compose_file = str(tmp_path / "docker-compose.yml")
        result = run_compose_action(str(tmp_path), compose_file, "up")
        assert result["status"] == "success"
        assert "docker" in mock_run.call_args[0][0][0]

    @patch("backend.redeploy.subprocess.run")
    def test_restart_success(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(returncode=0, stdout="Restarting...", stderr="")
        compose_file = str(tmp_path / "docker-compose.yml")
        result = run_compose_action(str(tmp_path), compose_file, "restart")
        assert result["status"] == "success"

    @patch("backend.redeploy.subprocess.run")
    def test_command_failure(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Error: no such service")
        compose_file = str(tmp_path / "docker-compose.yml")
        result = run_compose_action(str(tmp_path), compose_file, "up")
        assert result["status"] == "error"
        assert "no such service" in result["error"]

    @patch("backend.redeploy.subprocess.run")
    def test_timeout(self, mock_run, tmp_path):
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="docker", timeout=120)
        compose_file = str(tmp_path / "docker-compose.yml")
        result = run_compose_action(str(tmp_path), compose_file, "up")
        assert result["status"] == "error"
        assert "timeout" in result["error"].lower()

    def test_invalid_action(self, tmp_path):
        with pytest.raises(ValueError, match="Unknown action"):
            run_compose_action(str(tmp_path), "fake.yml", "destroy")
```

**Step 2: Implement redeploy.py**

```python
# backend/redeploy.py
"""
Docker Compose redeploy — restart services after applying fixes.

Uses subprocess with list-form args (no shell injection).
Validates stack paths within boundary before executing.
"""
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

COMPOSE_FILENAMES = [
    "docker-compose.yml", "docker-compose.yaml",
    "compose.yml", "compose.yaml",
]


def find_compose_file(stack_path: str) -> Optional[str]:
    """Find the compose file in a stack directory."""
    for name in COMPOSE_FILENAMES:
        path = os.path.join(stack_path, name)
        if os.path.isfile(path):
            return path
    return None


def validate_for_redeploy(stack_path: str, stacks_root: str) -> List[str]:
    """
    Validate a stack is safe to redeploy.

    Returns list of error strings. Empty = valid.
    """
    errors = []
    root = Path(stacks_root).resolve()

    try:
        Path(stack_path).resolve().relative_to(root)
    except ValueError:
        errors.append("Stack path outside stacks directory")
        return errors

    if not os.path.isdir(stack_path):
        errors.append(f"Directory not found: {stack_path}")
        return errors

    compose = find_compose_file(stack_path)
    if not compose:
        errors.append(f"No compose file found in {os.path.basename(stack_path)}")

    return errors


def run_compose_action(stack_path: str, compose_file: str, action: str) -> dict:
    """
    Run a docker compose command for a stack.

    Args:
        stack_path: Directory of the stack (cwd for command)
        compose_file: Full path to compose file
        action: "up", "restart", or "pull"

    Returns:
        {"status": "success"|"error", "duration_ms": int, "output": str, "error"?: str}
    """
    if action == "up":
        cmd = ["docker", "compose", "-f", compose_file, "up", "-d"]
    elif action == "restart":
        cmd = ["docker", "compose", "-f", compose_file, "restart"]
    elif action == "pull":
        cmd = ["docker", "compose", "-f", compose_file, "pull"]
    else:
        raise ValueError(f"Unknown action: {action}")

    t0 = time.time()
    try:
        result = subprocess.run(
            cmd,
            cwd=stack_path,
            capture_output=True,
            text=True,
            timeout=120,
        )
        duration_ms = int((time.time() - t0) * 1000)

        if result.returncode == 0:
            logger.info("Redeploy %s (%s): success in %dms", os.path.basename(stack_path), action, duration_ms)
            return {"status": "success", "duration_ms": duration_ms, "output": result.stdout.strip()}
        else:
            logger.warning("Redeploy %s (%s): failed — %s", os.path.basename(stack_path), action, result.stderr.strip())
            return {"status": "error", "duration_ms": duration_ms, "output": result.stdout.strip(), "error": result.stderr.strip()}

    except subprocess.TimeoutExpired:
        duration_ms = int((time.time() - t0) * 1000)
        logger.error("Redeploy %s (%s): timeout after %ds", os.path.basename(stack_path), action, 120)
        return {"status": "error", "duration_ms": duration_ms, "error": f"Timeout after 120s"}

    except FileNotFoundError:
        duration_ms = int((time.time() - t0) * 1000)
        logger.error("Docker compose not found — is Docker installed?")
        return {"status": "error", "duration_ms": duration_ms, "error": "docker compose not found. Is Docker installed and in PATH?"}

    except Exception as e:
        duration_ms = int((time.time() - t0) * 1000)
        logger.error("Redeploy %s (%s): unexpected error — %s", os.path.basename(stack_path), action, e)
        return {"status": "error", "duration_ms": duration_ms, "error": str(e)}


def redeploy_stacks(stacks: List[dict], stacks_root: str) -> dict:
    """
    Redeploy multiple stacks.

    Args:
        stacks: [{"stack_path": str, "action": "up"|"restart"|"pull"}, ...]
        stacks_root: Boundary for path validation

    Returns:
        {"status", "results": [...], "summary": str}
    """
    results = []
    succeeded = 0
    failed = 0

    for entry in stacks:
        stack_path = entry.get("stack_path", "").strip()
        action = entry.get("action", "up").strip()

        # Validate
        errors = validate_for_redeploy(stack_path, stacks_root)
        if errors:
            results.append({
                "stack_path": stack_path,
                "stack_name": os.path.basename(stack_path),
                "action": action,
                "status": "error",
                "error": "; ".join(errors),
            })
            failed += 1
            continue

        compose_file = find_compose_file(stack_path)
        result = run_compose_action(stack_path, compose_file, action)
        result["stack_path"] = stack_path
        result["stack_name"] = os.path.basename(stack_path)
        result["action"] = action
        results.append(result)

        if result["status"] == "success":
            succeeded += 1
        else:
            failed += 1

    status = "success" if failed == 0 else "partial" if succeeded > 0 else "error"
    summary = f"{succeeded} redeployed" + (f", {failed} failed" if failed else "")

    return {"status": status, "results": results, "summary": summary}
```

**Step 3: Add endpoint to main.py**

Update rate limiter:
```python
WRITE_PATHS = ("/api/apply-fix", "/api/apply-fixes", "/api/redeploy", "/api/change-stacks-path")
```

Add endpoint:
```python
@app.post("/api/redeploy")
async def api_redeploy(request: Request):
    """Redeploy stacks via docker compose after applying fixes."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    stacks = body.get("stacks", [])
    if not isinstance(stacks, list) or not stacks:
        return JSONResponse({"error": "stacks must be a non-empty list"}, status_code=400)
    if len(stacks) > 20:
        return JSONResponse({"error": "Maximum 20 stacks per redeploy"}, status_code=400)

    stacks_root = _get_stacks_root()
    if not stacks_root:
        return JSONResponse(
            {"error": "Redeploy requires MAPARR_STACKS_PATH to be set."},
            status_code=403,
        )

    from backend.redeploy import redeploy_stacks
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, lambda: redeploy_stacks(stacks, stacks_root))
    return JSONResponse(result)
```

**Step 4: Run tests**

Run: `pytest tests/test_redeploy.py -v -p no:capture`
Expected: All pass

**Step 5: Commit**

```bash
git add backend/redeploy.py tests/test_redeploy.py backend/main.py
git commit -m "feat: redeploy endpoint — docker compose restart via API"
```

---

## Task 3: Pipeline Dashboard HTML Structure

Replace the stack-grid sections with the new dashboard layout.

**Files:**
- Modify: `frontend/index.html`

**Step 1: Replace mode selector + stack grid + fix sections with dashboard**

Remove these sections (keep their IDs noted for JS migration):
- `#step-mode` (mode selector)
- `#step-stacks` (browse mode stack grid)
- `#step-error` (fix mode textarea — repositioned to dashboard bottom)
- `#step-parse-result` (parse output)
- `#step-fix-match` (matching pills)

Add new sections:

```html
<!-- First Launch — shown when no directory is configured -->
<section class="card" id="first-launch">
    <div class="first-launch-content">
        <h2>Where are your Docker stacks?</h2>
        <p class="step-desc">
            Enter the root directory that contains your compose files —
            either one big stack or separate folders per service.
        </p>
        <div class="first-launch-input">
            <input type="text" id="first-launch-path" placeholder="/opt/docker"
                   aria-label="Stacks directory path">
            <button id="first-launch-scan" class="btn-primary">Scan</button>
        </div>
        <p class="first-launch-examples">
            Examples: <code>/opt/docker</code> <code>/home/user/stacks</code>
            <code>C:\DockerContainers</code>
        </p>
    </div>
</section>

<!-- Pipeline Dashboard — main service-first view -->
<section class="hidden" id="pipeline-dashboard">
    <!-- Health Banner -->
    <div class="health-banner" id="health-banner">
        <div class="health-banner-status">
            <span class="health-banner-icon" id="health-banner-icon"></span>
            <span class="health-banner-text" id="health-banner-text"></span>
        </div>
        <div class="health-banner-actions" id="health-banner-actions"></div>
    </div>

    <!-- Service Groups -->
    <div id="service-groups"></div>

    <!-- Conflict Cards -->
    <div id="conflict-cards"></div>

    <!-- Error Paste Bar -->
    <div class="paste-bar" id="paste-bar">
        <div class="paste-bar-input">
            <textarea id="paste-error-input" rows="1"
                      placeholder="Paste an error from your *arr app..."
                      aria-label="Paste error text" disabled></textarea>
            <button id="paste-error-go" class="btn-primary" disabled>Go</button>
        </div>
        <div class="paste-bar-examples" id="paste-bar-examples"></div>
        <div class="paste-bar-result hidden" id="paste-bar-result"></div>
    </div>
</section>

<!-- Terminal (kept — used during scan + analysis) -->
<!-- step-analyzing section stays as-is -->

<!-- Analysis detail cards (kept — used for drill-down) -->
<!-- step-problem, step-current-setup, step-solution, step-healthy etc stay -->
<!-- They'll be shown inline within the dashboard context -->
```

**Step 2: Update header to include prominent path selector**

Replace the current header path display with:
```html
<header>
    <div class="header-left">
        <h1 class="logo">MapArr</h1>
        <span class="tagline">Path Mapping Problem Solver</span>
    </div>
    <div class="header-right">
        <div class="path-selector" id="path-selector">
            <span class="path-icon">&#128193;</span>
            <button class="path-display" id="header-path" aria-label="Change stacks directory">
                <span id="header-path-text">No directory selected</span>
                <span class="path-chevron">&#9662;</span>
            </button>
        </div>
        <div class="connection-status" id="connection-status">
            <span class="status-dot" id="status-dot"></span>
            <span id="status-text">Connecting...</span>
        </div>
        <span class="service-count" id="service-count"></span>
    </div>
</header>
```

**Step 3: Commit**

```bash
git add frontend/index.html
git commit -m "feat: pipeline dashboard HTML structure + first-launch screen"
```

---

## Task 4: Dashboard CSS

Style the new dashboard layout.

**Files:**
- Modify: `frontend/styles.css`

**Step 1: Add dashboard styles**

Add new CSS sections for:
- `.first-launch-content` — centered welcome screen
- `.health-banner` — top status bar with health dot, text, Fix All button
- `.service-group` — role-grouped service list (reuse existing `.stack-group` patterns)
- `.service-row` — individual service with accordion expand
- `.service-detail` — expanded service info panel
- `.conflict-card` — pipeline conflict with fix plan
- `.fix-plan` — per-file diff with checkboxes
- `.paste-bar` — persistent bottom bar
- `.redeploy-prompt` — post-apply redeploy UI
- `.path-selector` — header path dropdown

Key design tokens to maintain:
- Reuse existing color scheme (`--accent`, `--success`, `--warning`, `--error`)
- Reuse `.card` base styles
- Reuse `.health-dot` indicators
- Reuse `.terminal` styles for scan progress
- Reuse `.conflict-item`, `.conflict-severity` for conflict cards
- Reuse role colors: arr=blue, download=green, media=purple, other=grey

**Step 2: Commit**

```bash
git add frontend/styles.css
git commit -m "feat: pipeline dashboard CSS — service groups, health banner, paste bar"
```

---

## Task 5: Dashboard JavaScript — Core Rendering

The heart of the frontend rewrite. Replace mode-based flow with pipeline dashboard.

**Files:**
- Modify: `frontend/app.js`

**Step 1: New state shape**

Replace the mode-based state with pipeline-centric state:
```javascript
const state = {
    // Directory
    stacksPath: "",              // Current stacks root
    pathConfigured: false,       // Whether a path is set

    // Pipeline (primary data source)
    pipeline: null,              // Full pipeline scan result
    services: [],                // Flattened media services from pipeline
    servicesByRole: {},          // {arr: [...], download_client: [...], ...}

    // Interaction
    expandedService: null,       // Currently expanded service name
    expandedConflict: null,      // Currently expanded conflict index
    fixProgress: {},             // {compose_file_path: "pending"|"applied"|"failed"}

    // Error paste
    pastedError: null,           // Parsed error result
    highlightedServices: [],     // Service names to highlight

    // Scan state
    scanning: false,
    bootComplete: false,

    // Existing (carried forward)
    lastAnalyzed: {},
    verifiedStacks: new Set(),
};
```

**Step 2: Boot sequence — scan and render dashboard**

```javascript
async function boot() {
    // 1. Check health
    const health = await fetchHealth();
    if (!health) { showOffline(); return; }

    // 2. Determine stacks path
    state.stacksPath = health.stacks_path || "";
    state.pathConfigured = !!state.stacksPath;

    if (!state.pathConfigured) {
        showFirstLaunch();
        return;
    }

    // 3. Run pipeline scan
    await runPipelineScan();
}

async function runPipelineScan() {
    state.scanning = true;
    showScanProgress();

    const result = await fetchPipelineScan(state.stacksPath);
    state.pipeline = result;
    state.services = result.media_services || [];
    state.servicesByRole = result.services_by_role || {};
    state.scanning = false;

    renderDashboard();
}
```

**Step 3: Core render functions**

```javascript
function renderDashboard() {
    // Update header
    updateHeaderPath(state.stacksPath);
    updateServiceCount(state.services.length);

    // Health banner
    renderHealthBanner(state.pipeline);

    // Service groups by role
    renderServiceGroups(state.servicesByRole);

    // Conflict cards (if any)
    renderConflictCards(state.pipeline.conflicts || []);

    // Enable paste bar
    enablePasteBar();

    // Show dashboard
    show("pipeline-dashboard");
}

function renderHealthBanner(pipeline) {
    const banner = document.getElementById("health-banner");
    const icon = document.getElementById("health-banner-icon");
    const text = document.getElementById("health-banner-text");
    const actions = document.getElementById("health-banner-actions");
    actions.replaceChildren();

    const conflicts = pipeline.conflicts || [];
    const health = pipeline.health;

    if (health === "ok" && conflicts.length === 0) {
        icon.className = "health-banner-icon health-ok";
        text.textContent = `All ${pipeline.media_service_count} services healthy`;
    } else {
        icon.className = "health-banner-icon health-problem";
        text.textContent = `${conflicts.length} issue${conflicts.length !== 1 ? "s" : ""} found across ${pipeline.media_service_count} services`;
        const fixAll = document.createElement("button");
        fixAll.className = "btn-primary btn-sm";
        fixAll.textContent = "Fix All";
        fixAll.addEventListener("click", () => scrollToConflicts());
        actions.appendChild(fixAll);
    }
}

function renderServiceGroups(servicesByRole) {
    const container = document.getElementById("service-groups");
    container.replaceChildren();

    const roleOrder = [
        {key: "arr", label: "Arr Apps", cssClass: "service-group-arr"},
        {key: "download_client", label: "Download Clients", cssClass: "service-group-download"},
        {key: "media_server", label: "Media Servers", cssClass: "service-group-media"},
        {key: "request", label: "Request Apps", cssClass: "service-group-request"},
        {key: "other", label: "Other Services", cssClass: "service-group-other"},
    ];

    for (const {key, label, cssClass} of roleOrder) {
        const services = servicesByRole[key] || [];
        if (services.length === 0) continue;

        const group = document.createElement("div");
        group.className = `service-group ${cssClass}`;

        const header = document.createElement("div");
        header.className = "service-group-header";
        header.textContent = `${label} (${services.length})`;
        group.appendChild(header);

        const list = document.createElement("div");
        list.className = "service-group-items";

        for (const svc of services) {
            list.appendChild(renderServiceRow(svc));
        }

        group.appendChild(list);
        container.appendChild(group);
    }
}

function renderServiceRow(svc) {
    const row = document.createElement("div");
    row.className = "service-row";
    row.setAttribute("data-service", svc.service_name);

    // Health dot
    const dot = document.createElement("span");
    dot.className = `health-dot ${getServiceHealth(svc)}`;
    row.appendChild(dot);

    // Service info
    const info = document.createElement("div");
    info.className = "service-info";

    const name = document.createElement("span");
    name.className = "service-name";
    name.textContent = svc.service_name;
    info.appendChild(name);

    const meta = document.createElement("span");
    meta.className = "service-meta";
    // Family badge + mount summary
    const family = svc.family_name || "Independent";
    const mount = (svc.host_sources || []).join(", ") || "no data mounts";
    meta.textContent = `${family} · ${mount}`;
    info.appendChild(meta);

    row.appendChild(info);

    // File location
    const file = document.createElement("span");
    file.className = "service-file";
    file.textContent = svc.stack_name + "/";
    row.appendChild(file);

    // Click to expand
    row.addEventListener("click", () => toggleServiceDetail(svc));

    return row;
}
```

**Step 4: Service accordion expand**

```javascript
function toggleServiceDetail(svc) {
    const existing = document.querySelector(".service-detail-panel");
    const row = document.querySelector(`[data-service="${svc.service_name}"]`);

    // Collapse if already expanded
    if (state.expandedService === svc.service_name) {
        if (existing) existing.remove();
        state.expandedService = null;
        return;
    }

    // Collapse previous
    if (existing) existing.remove();

    // Build detail panel
    const panel = document.createElement("div");
    panel.className = "service-detail-panel";

    // Image DB info
    addDetailRow(panel, "Image", svc.image || "unknown");
    addDetailRow(panel, "Family", svc.family_name || "Independent");
    if (svc.environment) {
        const uid = svc.environment.PUID || svc.environment.USER_ID || "—";
        const gid = svc.environment.PGID || svc.environment.GROUP_ID || "—";
        addDetailRow(panel, "UID:GID", `${uid}:${gid}`);
    }
    addDetailRow(panel, "File", svc.compose_file || `${svc.stack_name}/docker-compose.yml`);

    // Volumes
    if (svc.volume_mounts && svc.volume_mounts.length > 0) {
        const volHeader = document.createElement("div");
        volHeader.className = "detail-section-header";
        volHeader.textContent = "Volumes";
        panel.appendChild(volHeader);

        for (const mount of svc.volume_mounts) {
            const line = document.createElement("div");
            line.className = "detail-volume";
            line.textContent = `${mount.source} : ${mount.target}`;
            panel.appendChild(line);
        }
    }

    // Insert after the row
    row.after(panel);
    state.expandedService = svc.service_name;
}
```

**Step 5: Conflict cards with fix plan**

```javascript
function renderConflictCards(conflicts) {
    const container = document.getElementById("conflict-cards");
    container.replaceChildren();

    if (conflicts.length === 0) return;

    for (let i = 0; i < conflicts.length; i++) {
        const conflict = conflicts[i];
        container.appendChild(renderConflictCard(conflict, i));
    }
}

function renderConflictCard(conflict, index) {
    const card = document.createElement("div");
    card.className = `conflict-card conflict-${conflict.severity || "high"}`;

    // Severity + description
    const header = document.createElement("div");
    header.className = "conflict-card-header";

    const badge = document.createElement("span");
    badge.className = `conflict-severity severity-${conflict.severity}`;
    badge.textContent = (conflict.severity || "HIGH").toUpperCase();
    header.appendChild(badge);

    const desc = document.createElement("span");
    desc.className = "conflict-card-desc";
    desc.textContent = conflict.description || conflict.type;
    header.appendChild(desc);

    card.appendChild(header);

    // Affected services
    if (conflict.services && conflict.services.length > 0) {
        const affected = document.createElement("div");
        affected.className = "conflict-affected";
        affected.textContent = "Affects: " + conflict.services.join(", ");
        card.appendChild(affected);
    }

    // Fix plan (per-file diffs) — populated when analysis runs
    const fixPlan = document.createElement("div");
    fixPlan.className = "fix-plan";
    fixPlan.id = `fix-plan-${index}`;
    card.appendChild(fixPlan);

    return card;
}
```

**Step 6: Error paste integration**

```javascript
function enablePasteBar() {
    const input = document.getElementById("paste-error-input");
    const btn = document.getElementById("paste-error-go");
    input.disabled = false;
    input.placeholder = "Paste an error from your *arr app...";
    btn.disabled = false;

    btn.addEventListener("click", handlePasteError);
    input.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            handlePasteError();
        }
    });
}

async function handlePasteError() {
    const input = document.getElementById("paste-error-input");
    const text = input.value.trim();
    if (!text) return;

    // Parse the error
    const parsed = await fetchParseError(text);
    if (!parsed || !parsed.service) {
        showPasteResult("Could not identify a service in this error.");
        return;
    }

    state.pastedError = parsed;

    // Find matching service(s) in pipeline
    const matched = state.services.filter(s =>
        s.service_name.toLowerCase().includes(parsed.service.toLowerCase()) ||
        parsed.service.toLowerCase().includes(s.service_name.toLowerCase())
    );

    if (matched.length > 0) {
        // Highlight matched services
        highlightServices(matched.map(s => s.service_name));

        // Find and expand relevant conflict
        const relevantConflict = findConflictForService(parsed.service);
        if (relevantConflict !== null) {
            scrollToConflict(relevantConflict);
            showPasteResult(`${parsed.service} — ${parsed.error_type || "mount conflict"} detected`);
        } else {
            showPasteResult(`${parsed.service} — no conflicts found. Your setup looks correct.`);
        }
    } else {
        showPasteResult(`Service "${parsed.service}" not found in your pipeline. Check the stacks directory.`);
    }
}

function highlightServices(serviceNames) {
    // Remove previous highlights
    document.querySelectorAll(".service-row.highlighted").forEach(el =>
        el.classList.remove("highlighted")
    );

    // Add highlights with animation
    for (const name of serviceNames) {
        const row = document.querySelector(`[data-service="${name}"]`);
        if (row) {
            row.classList.add("highlighted");
            row.scrollIntoView({behavior: "smooth", block: "center"});
        }
    }

    state.highlightedServices = serviceNames;
}
```

**Step 7: Commit**

```bash
git add frontend/app.js
git commit -m "feat: pipeline dashboard JS — service groups, conflict cards, paste bar"
```

---

## Task 6: Fix Plan Generation & Multi-File Apply UI

Wire the fix plan into conflict cards and connect to the batch apply endpoint.

**Files:**
- Modify: `frontend/app.js`

**Step 1: Generate per-file fix plans from pipeline analysis**

When the dashboard loads and conflicts exist, run analysis on each affected stack to get corrected YAML. Use the existing `POST /api/analyze` endpoint — it already generates `original_corrected_yaml`.

```javascript
async function generateFixPlans(conflicts) {
    // For each unique stack_path in conflicts, run analysis
    const stackPaths = new Set();
    for (const conflict of conflicts) {
        for (const svc of state.services) {
            if ((conflict.services || []).includes(svc.service_name)) {
                stackPaths.add(svc.stack_path);
            }
        }
    }

    const plans = {}; // stack_path → analysis result
    for (const stackPath of stackPaths) {
        const result = await fetchAnalyze(stackPath, {
            pipeline_context: state.pipeline,
        });
        if (result && result.original_corrected_yaml) {
            plans[stackPath] = {
                compose_file_path: result.compose_file_path,
                original_corrected_yaml: result.original_corrected_yaml,
                original_changed_lines: result.original_changed_lines || [],
                stack_name: result.stack_path.split(/[/\\]/).pop(),
            };
        }
    }

    return plans;
}
```

**Step 2: Render fix plan in conflict cards**

```javascript
function renderFixPlan(conflictIndex, plans) {
    const container = document.getElementById(`fix-plan-${conflictIndex}`);
    container.replaceChildren();

    const entries = Object.entries(plans);
    if (entries.length === 0) return;

    for (const [stackPath, plan] of entries) {
        const row = document.createElement("div");
        row.className = "fix-plan-row";
        row.setAttribute("data-stack-path", stackPath);

        const checkbox = document.createElement("span");
        checkbox.className = "fix-plan-check";
        checkbox.textContent = "☐";
        row.appendChild(checkbox);

        const label = document.createElement("span");
        label.className = "fix-plan-label";
        label.textContent = `${plan.stack_name}/docker-compose.yml`;
        row.appendChild(label);

        if (plan.original_changed_lines.length > 0) {
            const changes = document.createElement("span");
            changes.className = "fix-plan-changes";
            changes.textContent = `${plan.original_changed_lines.length} lines changed`;
            row.appendChild(changes);

            const applyBtn = document.createElement("button");
            applyBtn.className = "btn-sm";
            applyBtn.textContent = "Apply";
            applyBtn.addEventListener("click", (e) => {
                e.stopPropagation();
                applySingleFix(plan);
            });
            row.appendChild(applyBtn);
        } else {
            const noChange = document.createElement("span");
            noChange.className = "fix-plan-no-change";
            noChange.textContent = "no change needed";
            row.appendChild(noChange);
        }

        // Click to preview YAML diff
        row.addEventListener("click", () => toggleFixPreview(stackPath, plan));

        container.appendChild(row);
    }

    // Apply All button
    const fixableCount = entries.filter(([_, p]) => p.original_changed_lines.length > 0).length;
    if (fixableCount > 1) {
        const applyAll = document.createElement("button");
        applyAll.className = "btn-primary fix-plan-apply-all";
        applyAll.textContent = `Apply All Changes (${fixableCount} files)`;
        applyAll.addEventListener("click", () => applyAllFixes(plans));
        container.appendChild(applyAll);
    }
}
```

**Step 3: Single and batch apply**

```javascript
async function applySingleFix(plan) {
    const resp = await fetch("/api/apply-fix", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
            compose_file_path: plan.compose_file_path,
            corrected_yaml: plan.original_corrected_yaml,
        }),
    });
    const data = await resp.json();
    if (data.status === "applied") {
        markFixApplied(plan.compose_file_path);
        showToast(`Fixed ${plan.stack_name}/docker-compose.yml`);
    } else {
        showToast(`Failed: ${data.error}`, "error");
    }
}

async function applyAllFixes(plans) {
    const fixes = Object.entries(plans)
        .filter(([_, p]) => p.original_changed_lines.length > 0)
        .map(([_, p]) => ({
            compose_file_path: p.compose_file_path,
            corrected_yaml: p.original_corrected_yaml,
        }));

    const resp = await fetch("/api/apply-fixes", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({fixes}),
    });
    const data = await resp.json();

    if (data.status === "applied") {
        for (const r of data.results) {
            markFixApplied(r.compose_file_path);
        }
        showToast(`All ${data.applied_count} files fixed`);
        showRedeployPrompt(fixes);
    } else if (data.status === "partial") {
        for (const r of data.results) {
            if (r.status === "applied") markFixApplied(r.compose_file_path);
        }
        showToast(`${data.applied_count} applied, ${data.failed_count} failed`, "warning");
    } else {
        showToast(`Fix failed: ${data.errors?.[0]?.error || "unknown error"}`, "error");
    }
}

function markFixApplied(composePath) {
    state.fixProgress[composePath] = "applied";
    // Update checkbox in fix plan
    const row = document.querySelector(`[data-stack-path="${composePath}"]`);
    if (row) {
        const check = row.querySelector(".fix-plan-check");
        if (check) check.textContent = "✓";
        row.classList.add("fix-applied");
    }
    // Update health banner count
    updateHealthBannerAfterFix();
}
```

**Step 4: Commit**

```bash
git add frontend/app.js
git commit -m "feat: fix plan UI with per-file diffs + Apply All"
```

---

## Task 7: Redeploy UI

Post-apply redeploy prompt with risk warnings.

**Files:**
- Modify: `frontend/app.js`
- Modify: `frontend/styles.css`

**Step 1: Redeploy prompt after apply**

```javascript
function showRedeployPrompt(appliedFixes) {
    // Build list of affected stacks with role-specific warnings
    const roleWarnings = {
        "arr": "will stop monitoring and importing",
        "download_client": "active downloads will be interrupted",
        "media_server": "active streams will disconnect",
        "request": "request UI will be briefly unavailable",
        "other": "service will restart",
    };

    const stacks = [];
    for (const fix of appliedFixes) {
        const svc = state.services.find(s => fix.compose_file_path.includes(s.stack_name));
        if (svc) {
            stacks.push({
                stack_path: svc.stack_path,
                stack_name: svc.stack_name,
                service_name: svc.service_name,
                role: svc.role,
                warning: roleWarnings[svc.role] || roleWarnings.other,
            });
        }
    }

    const container = document.getElementById("conflict-cards");

    const prompt = document.createElement("div");
    prompt.className = "card redeploy-prompt";

    // Header
    const header = document.createElement("div");
    header.className = "step-header";
    const icon = document.createElement("span");
    icon.className = "step-number info-icon";
    icon.textContent = "↻";
    header.appendChild(icon);
    const h2 = document.createElement("h2");
    h2.textContent = "Redeploy";
    header.appendChild(h2);
    prompt.appendChild(header);

    // Backup reminder
    const backup = document.createElement("p");
    backup.className = "step-desc";
    backup.textContent = "Backups saved alongside each file (.bak). To undo: rename .bak back to docker-compose.yml.";
    prompt.appendChild(backup);

    // Warnings per service
    const warnings = document.createElement("div");
    warnings.className = "redeploy-warnings";
    for (const s of stacks) {
        const line = document.createElement("div");
        line.className = "redeploy-warning-line";
        line.textContent = `• ${s.service_name} — ${s.warning}`;
        warnings.appendChild(line);
    }
    prompt.appendChild(warnings);

    // Reassurance
    const reassure = document.createElement("p");
    reassure.className = "step-desc";
    reassure.textContent = "Services restart in seconds. No data is lost.";
    prompt.appendChild(reassure);

    // Buttons
    const actions = document.createElement("div");
    actions.className = "redeploy-actions";

    const deployBtn = document.createElement("button");
    deployBtn.className = "btn-primary";
    deployBtn.textContent = `Redeploy ${stacks.length} Service${stacks.length !== 1 ? "s" : ""}`;
    deployBtn.addEventListener("click", () => doRedeploy(stacks));
    actions.appendChild(deployBtn);

    const manualBtn = document.createElement("button");
    manualBtn.className = "btn-secondary";
    manualBtn.textContent = "I'll do it myself";
    manualBtn.addEventListener("click", () => showManualRedeploy(stacks));
    actions.appendChild(manualBtn);

    prompt.appendChild(actions);
    container.appendChild(prompt);
    prompt.scrollIntoView({behavior: "smooth"});
}

async function doRedeploy(stacks) {
    const body = {
        stacks: stacks.map(s => ({stack_path: s.stack_path, action: "up"})),
    };

    showToast("Redeploying...", "info");

    const resp = await fetch("/api/redeploy", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(120000),
    });
    const data = await resp.json();

    if (data.status === "success") {
        showToast(`All ${stacks.length} services redeployed`);
        // Auto-rescan pipeline
        await runPipelineScan();
    } else if (data.status === "partial") {
        showToast(`${data.summary}`, "warning");
        await runPipelineScan();
    } else {
        const firstErr = data.results?.find(r => r.status === "error");
        showToast(`Redeploy failed: ${firstErr?.error || "unknown"}`, "error");
        showManualRedeploy(stacks);
    }
}

function showManualRedeploy(stacks) {
    // Show copy-paste commands
    const commands = stacks.map(s =>
        `cd ${s.stack_name} && docker compose up -d`
    ).join(" && cd .. && ");

    // Render in a code block with copy button
    // (reuse existing code-block pattern)
}
```

**Step 2: Commit**

```bash
git add frontend/app.js frontend/styles.css
git commit -m "feat: redeploy UI with role-specific risk warnings"
```

---

## Task 8: Directory Selection & Path Change

First-launch flow and header path selector.

**Files:**
- Modify: `frontend/app.js`

**Step 1: First launch handler**

```javascript
function showFirstLaunch() {
    hide("pipeline-dashboard");
    show("first-launch");

    document.getElementById("first-launch-scan").addEventListener("click", async () => {
        const path = document.getElementById("first-launch-path").value.trim();
        if (!path) return;

        // Change path via API
        await fetch("/api/change-stacks-path", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({path}),
        });

        state.stacksPath = path;
        state.pathConfigured = true;
        hide("first-launch");
        await runPipelineScan();
    });
}
```

**Step 2: Header path selector**

```javascript
function setupHeaderPath() {
    const btn = document.getElementById("header-path");
    btn.addEventListener("click", () => {
        // Toggle inline editor
        const current = document.getElementById("header-path-text").textContent;
        const input = document.createElement("input");
        input.type = "text";
        input.value = current;
        input.className = "header-path-edit";

        input.addEventListener("keydown", async (e) => {
            if (e.key === "Enter") {
                const newPath = input.value.trim();
                if (newPath && newPath !== current) {
                    await changeStacksPath(newPath);
                }
                input.replaceWith(document.getElementById("header-path-text"));
            } else if (e.key === "Escape") {
                input.replaceWith(document.getElementById("header-path-text"));
            }
        });

        input.addEventListener("blur", () => {
            input.replaceWith(document.getElementById("header-path-text"));
        });

        document.getElementById("header-path-text").replaceWith(input);
        input.focus();
        input.select();
    });
}

async function changeStacksPath(newPath) {
    await fetch("/api/change-stacks-path", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({path: newPath}),
    });

    state.stacksPath = newPath;
    state.pipeline = null;
    state.services = [];
    state.pastedError = null;
    state.fixProgress = {};

    await runPipelineScan();
}
```

**Step 3: Commit**

```bash
git add frontend/app.js
git commit -m "feat: first-launch directory prompt + header path selector"
```

---

## Task 9: Wire Everything Together & Clean Up

Remove dead code, update CLAUDE.md, run full test suite.

**Files:**
- Modify: `frontend/app.js` (remove old mode functions)
- Modify: `frontend/index.html` (remove old sections)
- Modify: `frontend/styles.css` (remove orphaned styles)
- Modify: `CLAUDE.md`

**Step 1: Remove old mode-based code**

Delete or comment out:
- `enterFixMode()`, `enterBrowseMode()`, `switchToFixMode()`, `switchToBrowseMode()`
- `showStackSelection()`, `renderStacks()`, `renderStackItem()`
- Old `parseError()` flow (replaced by `handlePasteError()`)
- `autoMatchStacks()`, fix-match-pills rendering
- `backToStackList()` (replaced by dashboard being always visible)
- Mode selector HTML and CSS

Keep:
- `showProblem()`, `showCurrentSetup()`, `showSolution()` — these render analysis detail cards that can be used for service drill-down
- Terminal rendering functions
- Apply Fix functions (single-file — still used for individual apply)
- Quick-switch combobox (may repurpose for path history)
- Toast system
- All fetch helpers

**Step 2: Update CLAUDE.md**

Update:
- Architecture section: "Pipeline Dashboard" replaces "stack grid"
- Remove Browse/Fix mode references
- Add new endpoints to API table
- Update frontend description
- Note the pre-pivot tag

**Step 3: Run full test suite**

Run: `pytest tests/ -v -p no:capture`
Expected: 546+ tests pass (backend unchanged, new tests added)

**Step 4: Commit**

```bash
git add -A
git commit -m "refactor: remove old stack-grid mode, clean up dead code"
```

---

## Task Dependency Order

```
Task 1 (multi-file apply endpoint) ─── no dependencies
Task 2 (redeploy endpoint) ─── no dependencies
Task 3 (dashboard HTML) ─── no dependencies
Task 4 (dashboard CSS) ─── depends on Task 3
Task 5 (dashboard JS core) ─── depends on Tasks 3, 4
Task 6 (fix plan UI) ─── depends on Tasks 1, 5
Task 7 (redeploy UI) ─── depends on Tasks 2, 5
Task 8 (directory selection) ─── depends on Task 5
Task 9 (cleanup) ─── depends on all above
```

Tasks 1, 2, 3 can be done in parallel.
Tasks 4 depends on 3.
Task 5 depends on 3+4.
Tasks 6, 7, 8 depend on 5 and their respective backend tasks.
Task 9 is final cleanup.
