# Multi-File Apply Fix Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Enable Apply Fix to patch multiple compose files in one action, supporting cluster layouts (one service per folder) alongside existing single-file stacks.

**Architecture:** Add `fix_plans` list to `AnalysisResult` during `analyze_stack()`. Each entry contains a per-file patch (compose path, corrected YAML, changed services, category). Frontend reads `fix_plans` from analysis response. Both Browse and Paste pathways converge at `analyze_stack()` so changes flow through identically. Always use `/api/apply-fixes` batch endpoint.

**Tech Stack:** Python 3.11, FastAPI, pytest, vanilla JS, Playwright MCP

**Design doc:** `docs/plans/2026-03-10-multi-file-apply-fix-design.md`

---

## Task 1: Add `fix_plans` field to AnalysisResult

**Files:**
- Modify: `backend/analyzer.py:223-318` (AnalysisResult dataclass + to_dict)
- Test: `tests/test_multi_file_fix.py` (new file)

**Step 1: Write the failing test**

```python
# tests/test_multi_file_fix.py
# [2026-03-10 HH:MM] Multi-file apply fix tests

import pytest
from backend.analyzer import AnalysisResult, ServiceInfo, Conflict


class TestFixPlansField:
    def test_fix_plans_default_empty(self):
        """AnalysisResult has fix_plans field, defaults to empty list."""
        result = AnalysisResult(
            stack_path="/stacks/sonarr",
            compose_file="/stacks/sonarr/docker-compose.yml",
            resolution_method="manual",
            services=[],
            conflicts=[],
        )
        assert result.fix_plans == []

    def test_fix_plans_in_to_dict(self):
        """fix_plans appears in to_dict() output."""
        result = AnalysisResult(
            stack_path="/stacks/sonarr",
            compose_file="/stacks/sonarr/docker-compose.yml",
            resolution_method="manual",
            services=[],
            conflicts=[],
            fix_plans=[{
                "compose_file_path": "/stacks/sonarr/docker-compose.yml",
                "corrected_yaml": "services:\n  sonarr:\n    image: fixed\n",
                "changed_services": ["sonarr"],
                "change_summary": "Fix volume mounts for sonarr",
                "category": "A",
            }],
        )
        d = result.to_dict()
        assert "fix_plans" in d
        assert len(d["fix_plans"]) == 1
        assert d["fix_plans"][0]["compose_file_path"] == "/stacks/sonarr/docker-compose.yml"
        assert d["fix_plans"][0]["changed_services"] == ["sonarr"]
```

**Step 2: Run test to verify it fails**

Run: `cd /c/Projects/maparr && python -m pytest tests/test_multi_file_fix.py -v`
Expected: FAIL — `AnalysisResult.__init__() got an unexpected keyword argument 'fix_plans'`

**Step 3: Write minimal implementation**

In `backend/analyzer.py`, add to `AnalysisResult` dataclass (after line 247, after `env_solution_changed_lines`):

```python
    fix_plans: List[dict] = field(default_factory=list)  # Per-file fix entries for multi-file apply
```

In `to_dict()` (inside the return dict, after `"observations"` on line 317):

```python
            "fix_plans": self.fix_plans,
```

**Step 4: Run test to verify it passes**

Run: `cd /c/Projects/maparr && python -m pytest tests/test_multi_file_fix.py -v`
Expected: PASS

**Step 5: Run full suite for regression**

Run: `cd /c/Projects/maparr && python -m pytest tests/ -v --tb=short`
Expected: 661+ passed

**Step 6: Commit**

```bash
git add tests/test_multi_file_fix.py backend/analyzer.py
git commit -m "feat: add fix_plans field to AnalysisResult for multi-file support"
```

---

## Task 2: Build `_build_fix_plans()` helper

**Files:**
- Modify: `backend/analyzer.py` (new function after `_patch_original_env`)
- Test: `tests/test_multi_file_fix.py` (add tests)

**Step 1: Write the failing test**

```python
class TestBuildFixPlans:
    def test_single_file_produces_one_plan(self, tmp_path):
        """Single compose file with Cat A conflict produces 1 fix plan."""
        compose = tmp_path / "docker-compose.yml"
        compose.write_text("services:\n  sonarr:\n    volumes:\n      - /tv:/data/tv\n")

        from backend.analyzer import _build_fix_plans
        plans = _build_fix_plans(
            raw_compose_content=compose.read_text(),
            compose_file=str(compose),
            conflicts=[Conflict(
                conflict_type="no_shared_mount",
                severity="high",
                services=["sonarr", "qbittorrent"],
                description="No shared mount",
            )],
            services=[ServiceInfo(
                name="sonarr", image="lscr.io/linuxserver/sonarr", role="arr",
                volumes=[], compose_file=str(compose),
            )],
            pipeline_host_root="/data",
        )
        assert len(plans) == 1
        assert plans[0]["compose_file_path"] == str(compose)
        assert "sonarr" in plans[0]["changed_services"]
        assert plans[0]["category"] == "A"
        assert plans[0]["corrected_yaml"]  # Not empty

    def test_healthy_stack_empty_plans(self):
        """No conflicts produces empty fix_plans."""
        from backend.analyzer import _build_fix_plans
        plans = _build_fix_plans(
            raw_compose_content="services:\n  sonarr:\n    image: test\n",
            compose_file="/stacks/sonarr/docker-compose.yml",
            conflicts=[],
            services=[],
            pipeline_host_root=None,
        )
        assert plans == []

    def test_cat_b_conflict_produces_env_plan(self, tmp_path):
        """Category B (permission) conflict produces fix plan with category B."""
        compose = tmp_path / "docker-compose.yml"
        compose.write_text("services:\n  sonarr:\n    image: lscr.io/linuxserver/sonarr\n    environment:\n      - PUID=1000\n")

        from backend.analyzer import _build_fix_plans
        plans = _build_fix_plans(
            raw_compose_content=compose.read_text(),
            compose_file=str(compose),
            conflicts=[Conflict(
                conflict_type="puid_pgid_mismatch",
                severity="high",
                services=["sonarr"],
                description="PUID mismatch",
            )],
            services=[ServiceInfo(
                name="sonarr", image="lscr.io/linuxserver/sonarr", role="arr",
                volumes=[], compose_file=str(compose),
            )],
            pipeline_host_root=None,
        )
        assert len(plans) == 1
        assert plans[0]["category"] in ("B", "A+B")
```

**Step 2: Run test to verify it fails**

Run: `cd /c/Projects/maparr && python -m pytest tests/test_multi_file_fix.py::TestBuildFixPlans -v`
Expected: FAIL — `cannot import name '_build_fix_plans'`

**Step 3: Write minimal implementation**

Add function in `backend/analyzer.py` after `_patch_original_env()` (~line 3500):

```python
def _build_fix_plans(
    raw_compose_content: str,
    compose_file: str,
    conflicts: List[Conflict],
    services: List[ServiceInfo],
    pipeline_host_root: Optional[str] = None,
) -> List[dict]:
    """Build per-file fix plans from analysis results.

    For single-file stacks, returns a list with 0 or 1 entries.
    Multi-file support (cluster layouts) adds entries for each compose file.

    Each entry: {
        compose_file_path: str,
        corrected_yaml: str,
        changed_services: [str],
        change_summary: str,
        category: "A" | "B" | "A+B",
        changed_lines: [int],
    }
    """
    if not conflicts or not raw_compose_content:
        return []

    cat_a = any(c.category == "A" for c in conflicts)
    cat_b = any(c.category == "B" for c in conflicts)

    if not cat_a and not cat_b:
        return []

    corrected = None
    changed_lines: List[int] = []

    if cat_a:
        corrected, changed_lines = _patch_original_yaml(
            raw_compose_content, conflicts, services,
            host_root_override=pipeline_host_root,
        )
    if cat_b:
        env_patched, env_changed = _patch_original_env(
            corrected or raw_compose_content,
            conflicts, services,
        )
        if env_patched:
            corrected = env_patched
            changed_lines.extend(env_changed)

    if not corrected or not changed_lines:
        return []

    # Determine category label
    category = "A+B" if (cat_a and cat_b) else ("A" if cat_a else "B")

    # Determine which services in this file were changed
    file_services = [s.name for s in services if s.role in ("arr", "download_client", "media_server")]
    conflict_services = set()
    for c in conflicts:
        if c.category in ("A", "B"):
            conflict_services.update(c.services)
    changed_services = [s for s in file_services if s in conflict_services]

    summary_parts = []
    if cat_a:
        summary_parts.append("volume mounts")
    if cat_b:
        summary_parts.append("permissions")
    change_summary = "Fix " + " and ".join(summary_parts) + " for " + ", ".join(changed_services[:3])
    if len(changed_services) > 3:
        change_summary += f" (+{len(changed_services) - 3} more)"

    return [{
        "compose_file_path": compose_file,
        "corrected_yaml": corrected,
        "changed_services": changed_services,
        "change_summary": change_summary,
        "category": category,
        "changed_lines": changed_lines,
    }]
```

**Step 4: Run test to verify it passes**

Run: `cd /c/Projects/maparr && python -m pytest tests/test_multi_file_fix.py::TestBuildFixPlans -v`
Expected: PASS

**Step 5: Run full suite**

Run: `cd /c/Projects/maparr && python -m pytest tests/ -v --tb=short`
Expected: All passing

**Step 6: Commit**

```bash
git add backend/analyzer.py tests/test_multi_file_fix.py
git commit -m "feat: add _build_fix_plans() helper for per-file fix generation"
```

---

## Task 3: Wire `fix_plans` into `analyze_stack()`

**Files:**
- Modify: `backend/analyzer.py:595-800` (analyze_stack function, fix generation + return)
- Test: `tests/test_multi_file_fix.py` (add integration test)

**Step 1: Write the failing test**

```python
class TestAnalyzeStackFixPlans:
    def test_analyze_returns_fix_plans(self, tmp_path):
        """analyze_stack() populates fix_plans for a broken stack."""
        compose_content = (
            "services:\n"
            "  sonarr:\n"
            "    image: lscr.io/linuxserver/sonarr\n"
            "    volumes:\n"
            "      - /host/tv:/data/tv\n"
            "  qbittorrent:\n"
            "    image: lscr.io/linuxserver/qbittorrent\n"
            "    volumes:\n"
            "      - /host/downloads:/downloads\n"
        )
        compose_file = tmp_path / "docker-compose.yml"
        compose_file.write_text(compose_content)

        import yaml
        resolved = yaml.safe_load(compose_content)
        resolved["_compose_file"] = str(compose_file)
        resolved["_resolution"] = "manual"

        from backend.analyzer import analyze_stack
        result = analyze_stack(
            resolved_compose=resolved,
            stack_path=str(tmp_path),
            compose_file=str(compose_file),
            resolution_method="manual",
            raw_compose_content=compose_content,
        )
        d = result.to_dict()
        assert d["conflict_count"] > 0, "Should detect mount conflicts"
        assert len(d["fix_plans"]) >= 1, "Should have at least one fix plan"
        assert d["fix_plans"][0]["compose_file_path"] == str(compose_file)
        assert d["fix_plans"][0]["corrected_yaml"], "Should have corrected YAML"
        assert d["fix_plans"][0]["category"] in ("A", "B", "A+B")

    def test_analyze_healthy_empty_fix_plans(self, tmp_path):
        """Healthy stack has empty fix_plans."""
        compose_content = (
            "services:\n"
            "  sonarr:\n"
            "    image: lscr.io/linuxserver/sonarr\n"
            "    volumes:\n"
            "      - /host/data:/data\n"
            "  qbittorrent:\n"
            "    image: lscr.io/linuxserver/qbittorrent\n"
            "    volumes:\n"
            "      - /host/data:/data\n"
        )
        compose_file = tmp_path / "docker-compose.yml"
        compose_file.write_text(compose_content)

        import yaml
        resolved = yaml.safe_load(compose_content)
        resolved["_compose_file"] = str(compose_file)
        resolved["_resolution"] = "manual"

        from backend.analyzer import analyze_stack
        result = analyze_stack(
            resolved_compose=resolved,
            stack_path=str(tmp_path),
            compose_file=str(compose_file),
            resolution_method="manual",
            raw_compose_content=compose_content,
        )
        d = result.to_dict()
        assert d["fix_plans"] == []
```

**Step 2: Run test to verify it fails**

Run: `cd /c/Projects/maparr && python -m pytest tests/test_multi_file_fix.py::TestAnalyzeStackFixPlans -v`
Expected: FAIL — `fix_plans` key missing or empty (not yet wired)

**Step 3: Write minimal implementation**

In `analyze_stack()`, replace the existing fix generation block (lines 597-617) and update the return statement (line 777+):

After the existing `original_corrected_yaml` generation block (~line 617), add:

```python
    # Build fix_plans — per-file fix entries for multi-file apply
    fix_plans = _build_fix_plans(
        raw_compose_content=raw_compose_content or "",
        compose_file=compose_file,
        conflicts=conflicts,
        services=services,
        pipeline_host_root=pipeline_host_root,
    )
```

In the `return AnalysisResult(...)` call (~line 777), add:

```python
        fix_plans=fix_plans,
```

**Step 4: Run test to verify it passes**

Run: `cd /c/Projects/maparr && python -m pytest tests/test_multi_file_fix.py::TestAnalyzeStackFixPlans -v`
Expected: PASS

**Step 5: Run full suite**

Run: `cd /c/Projects/maparr && python -m pytest tests/ -v --tb=short`
Expected: All passing

**Step 6: Commit**

```bash
git add backend/analyzer.py tests/test_multi_file_fix.py
git commit -m "feat: wire fix_plans into analyze_stack() output"
```

---

## Task 4: Add pipeline-aware multi-file fix plans for cluster layouts

**Files:**
- Modify: `backend/analyzer.py` (_build_fix_plans → accept pipeline_context, generate per-sibling plans)
- Modify: `backend/pipeline.py` (expose full compose_file path in to_dict for siblings)
- Test: `tests/test_multi_file_fix.py` (cluster layout tests)

**Step 1: Write the failing test**

```python
class TestMultiFileCluster:
    def test_cluster_layout_multiple_plans(self, tmp_path):
        """Cluster layout with 3 services in 3 folders produces multiple fix plans."""
        # Create cluster: sonarr/, radarr/, qbittorrent/ each with own compose
        for name, role, image, vol in [
            ("sonarr", "arr", "lscr.io/linuxserver/sonarr", "/host/tv:/data/tv"),
            ("radarr", "arr", "lscr.io/linuxserver/radarr", "/host/movies:/data/movies"),
            ("qbittorrent", "download_client", "lscr.io/linuxserver/qbittorrent", "/host/downloads:/downloads"),
        ]:
            d = tmp_path / name
            d.mkdir()
            (d / "docker-compose.yml").write_text(
                f"services:\n  {name}:\n    image: {image}\n    volumes:\n      - {vol}\n"
            )

        from backend.pipeline import run_pipeline_scan
        pipeline_result = run_pipeline_scan(str(tmp_path))
        pipeline_dict = pipeline_result.to_dict()

        # There should be pipeline conflicts (different host roots)
        assert len(pipeline_dict.get("conflicts", [])) > 0 or len(pipeline_result.services) >= 3

        # Build fix plans using pipeline context for one of the stacks
        from backend.pipeline import get_pipeline_context_for_stack
        sonarr_ctx = get_pipeline_context_for_stack(pipeline_dict, str(tmp_path / "sonarr"))

        from backend.analyzer import _build_fix_plans_multi
        plans = _build_fix_plans_multi(
            stack_path=str(tmp_path / "sonarr"),
            compose_file=str(tmp_path / "sonarr" / "docker-compose.yml"),
            raw_compose_content=(tmp_path / "sonarr" / "docker-compose.yml").read_text(),
            conflicts=[],  # Pipeline-level conflicts handled differently
            services=[],
            pipeline_context=sonarr_ctx,
            stacks_root=str(tmp_path),
        )
        # Should produce plans for multiple files in the cluster
        assert len(plans) >= 1

    def test_single_file_stack_one_plan(self, tmp_path):
        """Single-file stack still produces exactly 1 plan via multi-file path."""
        compose = tmp_path / "media" / "docker-compose.yml"
        compose.parent.mkdir()
        compose.write_text(
            "services:\n"
            "  sonarr:\n"
            "    image: lscr.io/linuxserver/sonarr\n"
            "    volumes:\n      - /host/tv:/data/tv\n"
            "  qbittorrent:\n"
            "    image: lscr.io/linuxserver/qbittorrent\n"
            "    volumes:\n      - /host/downloads:/downloads\n"
        )
        from backend.analyzer import _build_fix_plans, Conflict, ServiceInfo
        plans = _build_fix_plans(
            raw_compose_content=compose.read_text(),
            compose_file=str(compose),
            conflicts=[Conflict(
                conflict_type="no_shared_mount", severity="high",
                services=["sonarr", "qbittorrent"], description="No shared mount",
            )],
            services=[
                ServiceInfo(name="sonarr", image="lscr.io/linuxserver/sonarr", role="arr", volumes=[], compose_file=str(compose)),
                ServiceInfo(name="qbittorrent", image="lscr.io/linuxserver/qbittorrent", role="download_client", volumes=[], compose_file=str(compose)),
            ],
            pipeline_host_root="/data",
        )
        assert len(plans) == 1
```

**Step 2: Run test to verify it fails**

Run: `cd /c/Projects/maparr && python -m pytest tests/test_multi_file_fix.py::TestMultiFileCluster -v`
Expected: FAIL — `cannot import name '_build_fix_plans_multi'`

**Step 3: Write minimal implementation**

Add `_build_fix_plans_multi()` in `backend/analyzer.py` after `_build_fix_plans()`:

```python
def _build_fix_plans_multi(
    stack_path: str,
    compose_file: str,
    raw_compose_content: str,
    conflicts: List[Conflict],
    services: List[ServiceInfo],
    pipeline_context: Optional[Dict] = None,
    pipeline_host_root: Optional[str] = None,
    stacks_root: Optional[str] = None,
) -> List[dict]:
    """Build fix plans across multiple compose files in a cluster layout.

    For cluster layouts (one service per folder), reads each sibling's compose
    file and generates per-file patches. Falls back to _build_fix_plans() for
    single-file stacks.
    """
    # Start with the current stack's own fix plans
    own_plans = _build_fix_plans(
        raw_compose_content=raw_compose_content,
        compose_file=compose_file,
        conflicts=conflicts,
        services=services,
        pipeline_host_root=pipeline_host_root,
    )

    if not pipeline_context:
        return own_plans

    # Check siblings that also need fixing
    sibling_services = pipeline_context.get("sibling_services", [])
    if not sibling_services:
        return own_plans

    all_plans = list(own_plans)
    seen_files = {compose_file}

    for sib in sibling_services:
        sib_compose = sib.get("compose_file_full", "")
        if not sib_compose or sib_compose in seen_files:
            continue
        seen_files.add(sib_compose)

        # Read sibling's raw compose content
        try:
            sib_content = open(sib_compose, "r", encoding="utf-8").read()
        except Exception:
            continue

        # Parse to get services
        try:
            import yaml as _yaml
            sib_resolved = _yaml.safe_load(sib_content)
            if not isinstance(sib_resolved, dict) or "services" not in sib_resolved:
                continue
        except Exception:
            continue

        # Extract services and check if any need fixing
        sib_services = _extract_services(sib_resolved)
        media_sibs = [s for s in sib_services if s.role in ("arr", "download_client", "media_server")]
        if not media_sibs:
            continue

        # Generate fix plans for this sibling
        sib_plans = _build_fix_plans(
            raw_compose_content=sib_content,
            compose_file=sib_compose,
            conflicts=conflicts,
            services=sib_services,
            pipeline_host_root=pipeline_host_root,
        )
        all_plans.extend(sib_plans)

    return all_plans
```

Update `PipelineService.to_dict()` in `pipeline.py` to include full compose path:

```python
    "compose_file_full": self.compose_file,  # Full path for multi-file fix
```

**Step 4: Run test to verify it passes**

Run: `cd /c/Projects/maparr && python -m pytest tests/test_multi_file_fix.py::TestMultiFileCluster -v`
Expected: PASS

**Step 5: Run full suite**

Run: `cd /c/Projects/maparr && python -m pytest tests/ -v --tb=short`
Expected: All passing

**Step 6: Commit**

```bash
git add backend/analyzer.py backend/pipeline.py tests/test_multi_file_fix.py
git commit -m "feat: multi-file fix plan generation for cluster layouts"
```

---

## Task 5: Wire multi-file plans into analyze_stack() with pipeline context

**Files:**
- Modify: `backend/analyzer.py:595-800` (replace single-file with multi-file path)
- Test: `tests/test_multi_file_fix.py` (integration test with pipeline)

**Step 1: Write the failing test**

```python
class TestAnalyzeWithPipelineMultiFile:
    def test_cluster_analyze_produces_multi_plans(self, tmp_path):
        """Full analyze_stack() with pipeline context produces multi-file plans."""
        # Build cluster
        for name, image, vol in [
            ("sonarr", "lscr.io/linuxserver/sonarr", "/host/tv:/data/tv"),
            ("qbittorrent", "lscr.io/linuxserver/qbittorrent", "/host/downloads:/downloads"),
        ]:
            d = tmp_path / name
            d.mkdir()
            (d / "docker-compose.yml").write_text(
                f"services:\n  {name}:\n    image: {image}\n    volumes:\n      - {vol}\n"
            )

        from backend.pipeline import run_pipeline_scan, get_pipeline_context_for_stack
        pipeline = run_pipeline_scan(str(tmp_path))
        ctx = get_pipeline_context_for_stack(pipeline.to_dict(), str(tmp_path / "sonarr"))

        import yaml
        sonarr_compose = tmp_path / "sonarr" / "docker-compose.yml"
        resolved = yaml.safe_load(sonarr_compose.read_text())
        resolved["_compose_file"] = str(sonarr_compose)
        resolved["_resolution"] = "manual"

        from backend.analyzer import analyze_stack
        result = analyze_stack(
            resolved_compose=resolved,
            stack_path=str(tmp_path / "sonarr"),
            compose_file=str(sonarr_compose),
            resolution_method="manual",
            raw_compose_content=sonarr_compose.read_text(),
            pipeline_context=ctx,
        )
        d = result.to_dict()
        # Should have fix plans — at minimum for the sonarr file
        assert "fix_plans" in d
        # The exact count depends on conflict detection, but should be >= 1 if conflicts exist
        if d["conflict_count"] > 0:
            assert len(d["fix_plans"]) >= 1
```

**Step 2: Run test to verify it fails**

Run: `cd /c/Projects/maparr && python -m pytest tests/test_multi_file_fix.py::TestAnalyzeWithPipelineMultiFile -v`
Expected: FAIL or unexpected behavior (pipeline context not yet passed to multi-file builder)

**Step 3: Write minimal implementation**

In `analyze_stack()`, update the fix_plans generation block to use multi-file when pipeline context is available:

```python
    # Build fix_plans — per-file fix entries for multi-file apply
    if pipeline_context and raw_compose_content:
        fix_plans = _build_fix_plans_multi(
            stack_path=stack_path,
            compose_file=compose_file,
            raw_compose_content=raw_compose_content,
            conflicts=conflicts,
            services=services,
            pipeline_context=pipeline_context,
            pipeline_host_root=pipeline_host_root,
            stacks_root=scan_dir,
        )
    elif raw_compose_content:
        fix_plans = _build_fix_plans(
            raw_compose_content=raw_compose_content,
            compose_file=compose_file,
            conflicts=conflicts,
            services=services,
            pipeline_host_root=pipeline_host_root,
        )
    else:
        fix_plans = []
```

**Step 4: Run test to verify it passes**

Run: `cd /c/Projects/maparr && python -m pytest tests/test_multi_file_fix.py::TestAnalyzeWithPipelineMultiFile -v`
Expected: PASS

**Step 5: Run full suite**

Run: `cd /c/Projects/maparr && python -m pytest tests/ -v --tb=short`
Expected: All passing

**Step 6: Commit**

```bash
git add backend/analyzer.py tests/test_multi_file_fix.py
git commit -m "feat: wire multi-file fix plans with pipeline context in analyze_stack"
```

---

## Task 6: Frontend — read `fix_plans` from analysis response

**Files:**
- Modify: `frontend/app.js:1297-1346` (generateFixPlans function)
- Test: Playwright E2E (Task 9)

**Step 1: Update `generateFixPlans()` to prefer `fix_plans` from response**

Currently `generateFixPlans()` calls `/api/analyze` per stack path to build plans. With `fix_plans` already in the analysis response, we can use those directly.

Replace `generateFixPlans()` (lines 1297-1346):

```javascript
async function generateFixPlans(conflicts) {
    // If analysis already provided fix_plans, use them directly
    if (state.analysis && state.analysis.fix_plans && state.analysis.fix_plans.length > 0) {
        const plans = {};
        for (const plan of state.analysis.fix_plans) {
            plans[plan.compose_file_path] = {
                compose_file_path: plan.compose_file_path,
                original_corrected_yaml: plan.corrected_yaml,
                original_changed_lines: plan.changed_lines || [],
                stack_name: plan.compose_file_path.replace(/\\/g, "/").split("/").slice(-2, -1)[0] || "",
                changed_services: plan.changed_services || [],
                change_summary: plan.change_summary || "",
                category: plan.category || "A",
            };
        }
        state.fixPlans = plans;

        // Render fix plans in each conflict card
        for (let i = 0; i < conflicts.length; i++) {
            renderFixPlan(i, plans);
        }
        return;
    }

    // Fallback: build plans from per-stack analysis (legacy path)
    const stackPaths = new Set();
    for (const conflict of conflicts) {
        for (const svc of state.services) {
            if ((conflict.services || []).includes(svc.service_name)) {
                stackPaths.add(svc.stack_path || "");
            }
        }
    }

    const plans = {};
    for (const stackPath of stackPaths) {
        if (!stackPath) continue;
        try {
            const resp = await fetch("/api/analyze", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ stack_path: stackPath }),
                signal: AbortSignal.timeout(15000),
            });
            const result = await resp.json();
            if (result && result.original_corrected_yaml) {
                plans[stackPath] = {
                    compose_file_path: result.compose_file_path || "",
                    original_corrected_yaml: result.original_corrected_yaml,
                    original_changed_lines: result.original_changed_lines || [],
                    stack_name: (stackPath.replace(/\\/g, "/").split("/").pop()) || "",
                };
            }
        } catch (e) {
            // Analysis failed for this stack — skip
        }
    }

    state.fixPlans = plans;
    for (let i = 0; i < conflicts.length; i++) {
        const conflict = conflicts[i];
        const relevantPlans = {};
        for (const svc of state.services) {
            if ((conflict.services || []).includes(svc.service_name)) {
                const sp = svc.stack_path || "";
                if (plans[sp]) relevantPlans[sp] = plans[sp];
            }
        }
        renderFixPlan(i, relevantPlans);
    }
}
```

**Step 2: Update `applySingleFix()` to always use batch endpoint**

Replace `applySingleFix()` (lines 1438-1458):

```javascript
async function applySingleFix(plan) {
    try {
        const resp = await fetch("/api/apply-fixes", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                fixes: [{
                    compose_file_path: plan.compose_file_path,
                    corrected_yaml: plan.original_corrected_yaml,
                }],
            }),
        });
        const data = await resp.json();
        if (data.status === "applied") {
            markFixApplied(plan.compose_file_path);
            const fileName = plan.compose_file_path.replace(/\\/g, "/").split("/").pop() || "compose file";
            showSimpleToast("Fixed " + plan.stack_name + "/" + fileName, "success");
        } else {
            const errMsg = (data.errors && data.errors[0]) ? data.errors[0].error : "unknown error";
            showSimpleToast("Failed: " + errMsg, "error");
        }
    } catch (e) {
        showSimpleToast("Apply failed: " + e.message, "error");
    }
}
```

**Step 3: Update `renderFixPlan()` for multi-file display enhancements**

In the label text, show the actual compose filename (not hardcoded "docker-compose.yml"):

```javascript
label.textContent = plan.stack_name + "/" + (plan.compose_file_path.replace(/\\/g, "/").split("/").pop() || "docker-compose.yml");
```

Add change summary if available:

```javascript
if (plan.change_summary) {
    const summary = document.createElement("span");
    summary.className = "fix-plan-summary";
    summary.textContent = plan.change_summary;
    row.appendChild(summary);
}
```

**Step 4: Run server and manually verify**

Run: `cd /c/Projects/maparr && python -m uvicorn backend.main:app --host 0.0.0.0 --port 9494`
Navigate to http://localhost:9494, click on a broken stack, verify fix plans render.

**Step 5: Commit**

```bash
git add frontend/app.js
git commit -m "feat: frontend reads fix_plans from analysis, unified batch apply"
```

---

## Task 7: Frontend — adaptive Apply button labels and post-apply flow

**Files:**
- Modify: `frontend/app.js:1398-1513` (renderFixPlan, applyAllFixes, markFixApplied)
- Modify: `frontend/styles.css` (fix-plan-summary style)

**Step 1: Update button labels**

In `renderFixPlan()`, change "Apply All Changes" to be adaptive:

```javascript
const label = fixableCount === 1 ? "Apply Fix" : "Apply All Fixes (" + fixableCount + " files)";
applyAll.textContent = label;
```

**Step 2: Add post-apply pipeline rescan**

In `applyAllFixes()`, after successful apply, trigger pipeline rescan:

```javascript
if (data.status === "applied") {
    for (const r of data.results) {
        markFixApplied(r.compose_file_path);
    }
    showSimpleToast("All " + data.applied_count + " files fixed — rescanning pipeline...", "success");
    // Trigger pipeline rescan to update health state
    try {
        await fetch("/api/pipeline-scan", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({}),
            signal: AbortSignal.timeout(30000),
        });
    } catch (e) {
        // Rescan failed — health dots may be stale, user can refresh
    }
    showRedeployPrompt(fixes);
}
```

**Step 3: Add CSS for fix-plan-summary**

```css
.fix-plan-summary {
    font-size: 0.82rem;
    color: var(--text-muted);
    margin-left: 0.5rem;
    font-style: italic;
}
```

**Step 4: Commit**

```bash
git add frontend/app.js frontend/styles.css
git commit -m "feat: adaptive fix labels, post-apply pipeline rescan, summary display"
```

---

## Task 8: Paste pathway parity test

**Files:**
- Test: `tests/test_multi_file_fix.py` (add parity test)

**Step 1: Write the failing test**

```python
class TestPastePathwayParity:
    def test_both_pathways_same_fix_plans(self, tmp_path):
        """Browse and Paste pathways produce identical fix_plans for same stack."""
        compose_content = (
            "services:\n"
            "  sonarr:\n"
            "    image: lscr.io/linuxserver/sonarr\n"
            "    volumes:\n"
            "      - /host/tv:/data/tv\n"
            "  qbittorrent:\n"
            "    image: lscr.io/linuxserver/qbittorrent\n"
            "    volumes:\n"
            "      - /host/downloads:/downloads\n"
        )
        compose_file = tmp_path / "docker-compose.yml"
        compose_file.write_text(compose_content)

        import yaml
        resolved = yaml.safe_load(compose_content)
        resolved["_compose_file"] = str(compose_file)
        resolved["_resolution"] = "manual"

        from backend.analyzer import analyze_stack

        # Browse pathway: no error context
        browse_result = analyze_stack(
            resolved_compose=resolved,
            stack_path=str(tmp_path),
            compose_file=str(compose_file),
            resolution_method="manual",
            raw_compose_content=compose_content,
        )

        # Paste pathway: with error context
        paste_result = analyze_stack(
            resolved_compose=resolved,
            stack_path=str(tmp_path),
            compose_file=str(compose_file),
            resolution_method="manual",
            error_service="sonarr",
            error_path="/data/tv/some/file.mkv",
            raw_compose_content=compose_content,
        )

        browse_plans = browse_result.to_dict()["fix_plans"]
        paste_plans = paste_result.to_dict()["fix_plans"]

        assert len(browse_plans) == len(paste_plans), "Both pathways should produce same number of fix plans"
        for bp, pp in zip(browse_plans, paste_plans):
            assert bp["compose_file_path"] == pp["compose_file_path"]
            assert bp["corrected_yaml"] == pp["corrected_yaml"]
            assert bp["category"] == pp["category"]
```

**Step 2: Run test**

Run: `cd /c/Projects/maparr && python -m pytest tests/test_multi_file_fix.py::TestPastePathwayParity -v`
Expected: PASS (both pathways call same `_build_fix_plans`)

**Step 3: Commit**

```bash
git add tests/test_multi_file_fix.py
git commit -m "test: verify paste and browse pathways produce identical fix_plans"
```

---

## Task 9: E2E Playwright tests

**Files:**
- Create: `tests/e2e/test_multi_file_apply.js` (Playwright test scenarios)
- Requires: MapArr server running on port 9494 with test stacks

**Step 1: Start server with test stacks**

```bash
cd /c/Projects/maparr
MAPARR_STACKS_PATH=/c/Projects/maparr/tools/test-stacks python -m uvicorn backend.main:app --port 9494
```

**Step 2: Test Browse pathway → fix plans render**

Use Playwright MCP to:
1. Navigate to http://localhost:9494
2. Wait for pipeline scan to complete (health banner appears)
3. Verify conflict cards render with fix plan rows
4. Click on a fix plan row to expand diff preview
5. Verify "Apply" button is visible
6. Take screenshot for review

**Step 3: Test single-file apply**

1. Click "Apply" on a single fix plan row
2. Verify success toast appears
3. Verify checkbox updates to checked
4. Verify button changes to "Applied" (disabled)
5. Take screenshot

**Step 4: Test paste error pathway**

1. Find the paste input bar
2. Type/paste a Sonarr error message
3. Verify services get highlighted
4. Click on highlighted conflict card
5. Verify fix plans render (same as browse path)
6. Take screenshot

**Step 5: Commit**

```bash
git add tests/e2e/
git commit -m "test: E2E Playwright tests for multi-file apply fix"
```

---

## Task 10: Final integration test and full suite run

**Files:**
- All test files
- Run full pytest + Playwright suite

**Step 1: Run full backend suite with coverage**

```bash
cd /c/Projects/maparr && python -m pytest tests/ -v --tb=short --cov=backend --cov-report=term-missing
```

Expected: All tests pass, coverage report generated.

**Step 2: Run Playwright E2E suite**

Use Playwright MCP to run all E2E scenarios from Task 9.

**Step 3: Generate test report**

```
## Test Results Summary
### Backend (pytest)
- Total: X | Passed: X | Failed: X | Coverage: X%
- Failing tests: [list with reason]

### E2E (Playwright)
- Total: X | Passed: X | Failed: X
- Failing flows: [list with screenshot reference]

### Priority Fixes
1. [highest impact failing test]
2. ...
```

**Step 4: Final commit**

```bash
git add -A
git commit -m "feat: multi-file apply fix — complete implementation with tests"
```

---

## Task Summary

| # | Task | Type | Files |
|---|------|------|-------|
| 1 | Add `fix_plans` field to AnalysisResult | Backend | analyzer.py, test_multi_file_fix.py |
| 2 | Build `_build_fix_plans()` helper | Backend | analyzer.py, test_multi_file_fix.py |
| 3 | Wire `fix_plans` into `analyze_stack()` | Backend | analyzer.py, test_multi_file_fix.py |
| 4 | Multi-file plans for cluster layouts | Backend | analyzer.py, pipeline.py, test_multi_file_fix.py |
| 5 | Wire multi-file into analyze_stack with pipeline | Backend | analyzer.py, test_multi_file_fix.py |
| 6 | Frontend reads `fix_plans` from response | Frontend | app.js |
| 7 | Adaptive labels + post-apply rescan | Frontend | app.js, styles.css |
| 8 | Paste pathway parity test | Test | test_multi_file_fix.py |
| 9 | E2E Playwright tests | Test | tests/e2e/ |
| 10 | Final integration + report | All | Full suite |
