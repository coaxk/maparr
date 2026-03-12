# Analysis Pipeline Rebuild — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Rebuild the solution generation layer so every conflict type gets the right fix output, the right frontend presentation, and honest health signals — eliminating all 11 detected disconnects.

**Architecture:** A 4-category classification system (Path / Permission / Infrastructure / Observation) gates all downstream behavior: solution YAML type, RPM wizard visibility, frontend tabs, dashboard health. One constant dict, referenced everywhere.

**Tech Stack:** Python 3.11+ (FastAPI backend), vanilla JS frontend, pytest

---

### Task 1: Conflict Category Constant + Conflict Dataclass Update

**Files:**
- Modify: `backend/analyzer.py:160-182` (Conflict dataclass)
- Modify: `backend/analyzer.py` (new constant near top, after imports ~line 30)
- Test: `tests/test_categories.py` (new)

**Step 1: Write the failing test**

```python
# tests/test_categories.py
"""Tests for the conflict category system."""
import pytest
from backend.analyzer import CONFLICT_CATEGORIES, Conflict


class TestConflictCategories:
    """Verify the category constant covers all known conflict types."""

    def test_all_path_types_in_category_a(self):
        for ct in ["no_shared_mount", "different_host_paths", "named_volume_data", "path_unreachable"]:
            assert CONFLICT_CATEGORIES[ct] == "A", f"{ct} should be Category A"

    def test_all_permission_types_in_category_b(self):
        for ct in [
            "puid_pgid_mismatch", "missing_puid_pgid", "root_execution",
            "umask_inconsistent", "umask_restrictive", "cross_stack_puid_mismatch",
            "tz_mismatch",
        ]:
            assert CONFLICT_CATEGORIES[ct] == "B", f"{ct} should be Category B"

    def test_all_infra_types_in_category_c(self):
        for ct in ["wsl2_performance", "mixed_mount_types", "windows_path_in_compose", "remote_filesystem"]:
            assert CONFLICT_CATEGORIES[ct] == "C", f"{ct} should be Category C"

    def test_all_observation_types_in_category_d(self):
        for ct in [
            "missing_restart_policy", "latest_tag_usage", "missing_tz",
            "privileged_mode", "no_healthcheck",
        ]:
            assert CONFLICT_CATEGORIES[ct] == "D", f"{ct} should be Category D"

    def test_no_unknown_categories(self):
        valid = {"A", "B", "C", "D"}
        for ct, cat in CONFLICT_CATEGORIES.items():
            assert cat in valid, f"{ct} has invalid category {cat}"


class TestConflictCategory:
    """Verify Conflict.category property."""

    def test_path_conflict_returns_a(self):
        c = Conflict("no_shared_mount", "critical", ["sonarr"], "desc")
        assert c.category == "A"

    def test_permission_conflict_returns_b(self):
        c = Conflict("puid_pgid_mismatch", "high", ["sonarr"], "desc")
        assert c.category == "B"

    def test_infra_conflict_returns_c(self):
        c = Conflict("wsl2_performance", "medium", ["sonarr"], "desc")
        assert c.category == "C"

    def test_unknown_type_returns_none(self):
        c = Conflict("bogus_type", "low", ["sonarr"], "desc")
        assert c.category is None
```

**Step 2: Run test to verify it fails**

Run: `cd C:\Projects\maparr && python -m pytest tests/test_categories.py -v -p no:capture`
Expected: FAIL — `CONFLICT_CATEGORIES` doesn't exist, `Conflict.category` doesn't exist

**Step 3: Write minimal implementation**

In `backend/analyzer.py` after imports (~line 30), add:

```python
# ─── Conflict Category System ───
# Every conflict type belongs to exactly one category.
# All downstream decisions — solution YAML, RPM wizard, frontend tabs,
# dashboard health, problem/solution card content — key off this category.
#
# A: Path Conflicts       → fixable by YAML volume changes
# B: Permission & Env     → fixable by YAML environment changes
# C: Infrastructure       → NOT fixable by YAML — guidance only
# D: Observations         → informational only, no health impact

CONFLICT_CATEGORIES: dict[str, str] = {
    # Category A: Path Conflicts
    "no_shared_mount": "A",
    "different_host_paths": "A",
    "named_volume_data": "A",
    "path_unreachable": "A",
    # Category B: Permission & Environment
    "puid_pgid_mismatch": "B",
    "missing_puid_pgid": "B",
    "root_execution": "B",
    "umask_inconsistent": "B",
    "umask_restrictive": "B",
    "cross_stack_puid_mismatch": "B",
    "tz_mismatch": "B",
    # Category C: Infrastructure Advisories
    "wsl2_performance": "C",
    "mixed_mount_types": "C",
    "windows_path_in_compose": "C",
    "remote_filesystem": "C",
    # Category D: Observations
    "missing_restart_policy": "D",
    "latest_tag_usage": "D",
    "missing_tz": "D",
    "privileged_mode": "D",
    "no_healthcheck": "D",
}
```

Update the `Conflict` dataclass at line 160:

```python
@dataclass
class Conflict:
    """A detected path mapping conflict."""
    conflict_type: str
    severity: str
    services: List[str]
    description: str
    detail: str = ""
    fix: Optional[str] = None
    rpm_hint: Optional[dict] = None

    @property
    def category(self) -> Optional[str]:
        """Return the conflict category (A/B/C/D) or None if unknown."""
        return CONFLICT_CATEGORIES.get(self.conflict_type)

    def to_dict(self) -> dict:
        d = {
            "type": self.conflict_type,
            "severity": self.severity,
            "services": self.services,
            "description": self.description,
            "detail": self.detail,
            "fix": self.fix,
            "category": self.category,
        }
        if self.rpm_hint:
            d["rpm_hint"] = self.rpm_hint
        return d
```

**Step 4: Run test to verify it passes**

Run: `cd C:\Projects\maparr && python -m pytest tests/test_categories.py -v -p no:capture`
Expected: All PASS

**Step 5: Run full test suite to check for regressions**

Run: `cd C:\Projects\maparr && python -m pytest tests/ -v -p no:capture --tb=short`
Expected: All 577+ tests PASS. The new `category` key in `to_dict()` is additive — no existing assertions should break.

**Step 6: Commit**

```bash
git add backend/analyzer.py tests/test_categories.py
git commit -m "feat: add conflict category system (A/B/C/D) with Conflict.category property"
```

---

### Task 2: WSL2 Regex Fix in mounts.py

**Files:**
- Modify: `backend/mounts.py:241` (`_check_wsl2` regex)
- Test: `tests/test_wo4.py` (existing mount classification tests — add/update cases)

**Context:** The current regex `^/mnt/([a-zA-Z])(/.*)?$` matches any single-letter dir under `/mnt/`. This means `/mnt/n/export` (a NAS abbreviation) and `/mnt/a/something` (not a real Windows drive) are misclassified as WSL2. The fix restricts to drive letters c-z (A/B are floppies) and requires at least one path component after the drive letter.

**Step 1: Write the failing test**

Add to `tests/test_wo4.py` (or the file that tests `classify_path`):

```python
class TestWsl2Classification:
    """WSL2 regex must not match non-Windows single-letter /mnt/ paths."""

    def test_mnt_n_is_not_wsl2(self):
        """Single-letter NAS mount abbreviation — NOT a Windows drive."""
        mc = classify_path("/mnt/n/export")
        assert mc.mount_type != "wsl2"

    def test_mnt_a_is_not_wsl2(self):
        """/mnt/a is floppy territory, not a real Windows drive."""
        mc = classify_path("/mnt/a/something")
        assert mc.mount_type != "wsl2"

    def test_mnt_b_is_not_wsl2(self):
        """/mnt/b is floppy territory."""
        mc = classify_path("/mnt/b/data")
        assert mc.mount_type != "wsl2"

    def test_mnt_c_users_is_wsl2(self):
        """Standard WSL2 C: drive path."""
        mc = classify_path("/mnt/c/Users/media")
        assert mc.mount_type == "wsl2"

    def test_mnt_d_downloads_is_wsl2(self):
        """D: drive via WSL2."""
        mc = classify_path("/mnt/d/Downloads")
        assert mc.mount_type == "wsl2"

    def test_mnt_z_data_is_wsl2(self):
        """Rare but valid Windows drive letter z."""
        mc = classify_path("/mnt/z/data")
        assert mc.mount_type == "wsl2"

    def test_mnt_c_alone_is_not_wsl2(self):
        """Bare /mnt/c with no subdirectory — ambiguous, don't classify."""
        mc = classify_path("/mnt/c")
        assert mc.mount_type != "wsl2"

    def test_mnt_nas_is_local(self):
        """Multi-letter name is obviously not a drive letter."""
        mc = classify_path("/mnt/nas/media")
        assert mc.mount_type == "local"
```

**Step 2: Run test to verify failures**

Run: `cd C:\Projects\maparr && python -m pytest tests/test_wo4.py::TestWsl2Classification -v -p no:capture`
Expected: `test_mnt_n_is_not_wsl2`, `test_mnt_a_is_not_wsl2`, `test_mnt_b_is_not_wsl2`, `test_mnt_c_alone_is_not_wsl2` FAIL

**Step 3: Fix the regex**

In `backend/mounts.py:241`, change:

```python
# BEFORE:
match = re.match(r'^/mnt/([a-zA-Z])(/.*)?$', path)

# AFTER — only Windows drive letters c-z, must have subdirectory
match = re.match(r'^/mnt/([c-zC-Z])(/.+)$', path)
```

This change:
- Excludes a/b (floppy drives, not real paths)
- Requires `(/.+)` instead of `(/.*)?` — the drive letter must be followed by at least `/something`
- `/mnt/c` alone no longer matches (ambiguous without a path component)

**Step 4: Run test to verify it passes**

Run: `cd C:\Projects\maparr && python -m pytest tests/test_wo4.py::TestWsl2Classification -v -p no:capture`
Expected: All PASS

**Step 5: Run full test suite**

Run: `cd C:\Projects\maparr && python -m pytest tests/ -p no:capture --tb=short`
Expected: All PASS (verify no existing WSL2 tests broke)

**Step 6: Commit**

```bash
git add backend/mounts.py tests/test_wo4.py
git commit -m "fix: restrict WSL2 regex to c-z drives with required subdirectory"
```

---

### Task 3: TZ Mismatch Detection

**Files:**
- Modify: `backend/analyzer.py:1340-1382` (`_check_permissions` — add TZ check)
- Test: `tests/test_permissions.py` (add TZ mismatch tests)

**Context:** The permissions pass already groups services by PUID/PGID. TZ mismatch follows the same pattern: group services by their TZ environment variable, flag when 2+ distinct values exist. This is a Category B conflict — fixable by environment YAML changes.

**Step 1: Write the failing test**

Add to `tests/test_permissions.py`:

```python
class TestTzMismatch:
    """TZ mismatch detection across media services."""

    def test_tz_mismatch_detected(self, make_stack):
        """Services with different TZ values should produce tz_mismatch conflict."""
        path = make_stack("""
            services:
              sonarr:
                image: lscr.io/linuxserver/sonarr:latest
                environment:
                  - PUID=1000
                  - PGID=1000
                  - TZ=America/New_York
                volumes:
                  - /data:/data
              radarr:
                image: lscr.io/linuxserver/radarr:latest
                environment:
                  - PUID=1000
                  - PGID=1000
                  - TZ=Europe/London
                volumes:
                  - /data:/data
              qbittorrent:
                image: lscr.io/linuxserver/qbittorrent:latest
                environment:
                  - PUID=1000
                  - PGID=1000
                  - TZ=America/New_York
                volumes:
                  - /data:/data
        """)
        result = analyze_and_get_result(path)
        tz_conflicts = [c for c in result["conflicts"] if c["type"] == "tz_mismatch"]
        assert len(tz_conflicts) == 1
        assert tz_conflicts[0]["severity"] == "low"
        assert tz_conflicts[0]["category"] == "B"

    def test_matching_tz_no_conflict(self, make_stack):
        """Same TZ everywhere — no tz_mismatch conflict."""
        path = make_stack("""
            services:
              sonarr:
                image: lscr.io/linuxserver/sonarr:latest
                environment:
                  - PUID=1000
                  - PGID=1000
                  - TZ=America/New_York
                volumes:
                  - /data:/data
              radarr:
                image: lscr.io/linuxserver/radarr:latest
                environment:
                  - PUID=1000
                  - PGID=1000
                  - TZ=America/New_York
                volumes:
                  - /data:/data
        """)
        result = analyze_and_get_result(path)
        tz_conflicts = [c for c in result["conflicts"] if c["type"] == "tz_mismatch"]
        assert len(tz_conflicts) == 0

    def test_missing_tz_not_flagged_as_mismatch(self, make_stack):
        """Services without TZ at all — that's missing_tz (Cat D), not tz_mismatch."""
        path = make_stack("""
            services:
              sonarr:
                image: lscr.io/linuxserver/sonarr:latest
                environment:
                  - PUID=1000
                  - PGID=1000
                volumes:
                  - /data:/data
              radarr:
                image: lscr.io/linuxserver/radarr:latest
                environment:
                  - PUID=1000
                  - PGID=1000
                volumes:
                  - /data:/data
        """)
        result = analyze_and_get_result(path)
        tz_conflicts = [c for c in result["conflicts"] if c["type"] == "tz_mismatch"]
        assert len(tz_conflicts) == 0
```

Note: `analyze_and_get_result` is a helper — check if it exists in `test_permissions.py` already, otherwise create it using the standard `make_stack → resolve → analyze → to_dict()` pattern.

**Step 2: Run test to verify it fails**

Run: `cd C:\Projects\maparr && python -m pytest tests/test_permissions.py::TestTzMismatch -v -p no:capture`
Expected: FAIL — no `tz_mismatch` conflict type is ever generated

**Step 3: Implement TZ mismatch detection**

In `backend/analyzer.py`, add a new function after `_check_umask_consistency`:

```python
def _check_tz_mismatch(participants: List[ServiceInfo]) -> List[Conflict]:
    """Detect timezone mismatches across media services.

    Only flags when 2+ distinct TZ values are explicitly set.
    Services with no TZ are excluded (that's missing_tz, a Cat D observation).
    """
    tz_map: Dict[str, List[str]] = {}  # TZ value → [service names]
    for svc in participants:
        tz = svc.environment.get("TZ", "").strip()
        if tz:
            tz_map.setdefault(tz, []).append(svc.name)

    if len(tz_map) <= 1:
        return []

    # Find majority TZ
    majority_tz = max(tz_map, key=lambda k: len(tz_map[k]))
    all_services = [name for names in tz_map.values() for name in names]
    minority_desc = ", ".join(
        f"{name} ({tz})" for tz, names in tz_map.items()
        if tz != majority_tz for name in names
    )

    return [Conflict(
        conflict_type="tz_mismatch",
        severity="low",
        services=all_services,
        description=(
            f"Timezone mismatch: majority use {majority_tz}, "
            f"but {minority_desc}"
        ),
        detail=f"TZ groups: {dict(tz_map)}",
    )]
```

Wire it into `_check_permissions()` at line ~1374:

```python
    # Check 5b: TZ mismatch (low — scheduling confusion)
    conflicts.extend(_check_tz_mismatch(participants))
```

Also ensure `ServiceInfo` has environment access. Check that `svc.environment` is a dict with TZ values accessible — it should be, since permissions pass already reads PUID/PGID from it.

**Step 4: Run test to verify it passes**

Run: `cd C:\Projects\maparr && python -m pytest tests/test_permissions.py::TestTzMismatch -v -p no:capture`
Expected: All PASS

**Step 5: Run full test suite**

Run: `cd C:\Projects\maparr && python -m pytest tests/ -p no:capture --tb=short`

**Step 6: Commit**

```bash
git add backend/analyzer.py tests/test_permissions.py
git commit -m "feat: detect TZ mismatch across media services (Category B)"
```

---

### Task 4: Environment Solution Generator (`_generate_env_solution`)

**Files:**
- Modify: `backend/analyzer.py` (new function `_generate_env_solution`, ~after `_generate_solution_yaml` at line 2731)
- Test: `tests/test_env_solution.py` (new)

**Context:** This is the core of the rebuild. Currently, only `_generate_solution_yaml()` exists and it always produces volume YAML. We need a parallel generator for Category B conflicts that produces corrected `environment:` blocks.

**Step 1: Write the failing test**

```python
# tests/test_env_solution.py
"""Tests for environment solution generation (Category B)."""
import pytest
import textwrap
from tests.conftest import *  # noqa — get fixtures


def analyze_and_get_result(make_stack, yaml_content, dirname="teststack"):
    """Helper: create stack, resolve, analyze, return result dict."""
    import yaml as pyyaml
    from backend.analyzer import analyze_stack
    path = make_stack(yaml_content, dirname=dirname)
    compose_file = f"{path}/docker-compose.yml"
    with open(compose_file) as f:
        raw = f.read()
    data = pyyaml.safe_load(raw)
    result = analyze_stack(
        resolved_compose=data, stack_path=path,
        compose_file=compose_file, resolution_method="direct",
        raw_compose_content=raw,
    )
    return result.to_dict()


class TestEnvSolution:
    """Environment solution YAML should only appear for Category B conflicts."""

    def test_puid_mismatch_generates_env_yaml(self, make_stack):
        """PUID mismatch should produce env_solution_yaml with corrected PUID/PGID."""
        result = analyze_and_get_result(make_stack, """
            services:
              sonarr:
                image: lscr.io/linuxserver/sonarr:latest
                environment:
                  - PUID=1000
                  - PGID=1000
                volumes:
                  - /data:/data
              radarr:
                image: lscr.io/linuxserver/radarr:latest
                environment:
                  - PUID=1001
                  - PGID=1001
                volumes:
                  - /data:/data
        """)
        assert result.get("env_solution_yaml") is not None
        assert "PUID=" in result["env_solution_yaml"]
        # Should NOT produce volume solution YAML (no path conflicts)
        assert result.get("solution_yaml") is None

    def test_path_only_no_env_yaml(self, make_stack):
        """Path-only conflict should produce solution_yaml but NOT env_solution_yaml."""
        result = analyze_and_get_result(make_stack, """
            services:
              sonarr:
                image: lscr.io/linuxserver/sonarr:latest
                environment:
                  - PUID=1000
                  - PGID=1000
                volumes:
                  - /srv/tv:/data/tv
              qbittorrent:
                image: lscr.io/linuxserver/qbittorrent:latest
                environment:
                  - PUID=1000
                  - PGID=1000
                volumes:
                  - /home/user/downloads:/downloads
        """)
        assert result.get("solution_yaml") is not None
        assert result.get("env_solution_yaml") is None

    def test_mixed_path_and_perm_generates_both(self, make_stack):
        """Mixed A+B should produce BOTH solution_yaml and env_solution_yaml."""
        result = analyze_and_get_result(make_stack, """
            services:
              sonarr:
                image: lscr.io/linuxserver/sonarr:latest
                environment:
                  - PUID=1000
                  - PGID=1000
                volumes:
                  - /srv/tv:/data/tv
              qbittorrent:
                image: lscr.io/linuxserver/qbittorrent:latest
                environment:
                  - PUID=1001
                  - PGID=1001
                volumes:
                  - /home/user/downloads:/downloads
        """)
        assert result.get("solution_yaml") is not None
        assert result.get("env_solution_yaml") is not None

    def test_infra_only_no_yaml_at_all(self, make_stack):
        """Infrastructure-only stack — no solution YAML of any kind."""
        result = analyze_and_get_result(make_stack, """
            services:
              sonarr:
                image: lscr.io/linuxserver/sonarr:latest
                environment:
                  - PUID=1000
                  - PGID=1000
                volumes:
                  - /mnt/c/Users/data:/data
              radarr:
                image: lscr.io/linuxserver/radarr:latest
                environment:
                  - PUID=1000
                  - PGID=1000
                volumes:
                  - /mnt/c/Users/data:/data
        """)
        # WSL2 perf warning is Cat C — no YAML fix possible
        assert result.get("solution_yaml") is None
        assert result.get("env_solution_yaml") is None
```

**Step 2: Run test to verify it fails**

Run: `cd C:\Projects\maparr && python -m pytest tests/test_env_solution.py -v -p no:capture`
Expected: FAIL — `env_solution_yaml` key doesn't exist in result dict

**Step 3: Implement `_generate_env_solution()`**

Add new function in `backend/analyzer.py` after `_generate_solution_yaml` (~line 2731):

```python
def _generate_env_solution(
    conflicts: List[Conflict], services: List[ServiceInfo],
) -> Tuple[Optional[str], List[int]]:
    """
    Generate corrected environment YAML for Category B (permission) conflicts.

    Only processes conflicts with category == "B". Produces a complete
    services section with corrected environment blocks.

    Returns:
        (yaml_string, changed_lines) — the YAML and 1-indexed line numbers
        of lines that differ from the original.
    """
    cat_b = [c for c in conflicts if c.category == "B"]
    if not cat_b:
        return None, []

    participants = [s for s in services if s.role in ("arr", "download_client", "media_server")]
    if not participants:
        return None, []

    # Determine majority values for PUID, PGID, TZ, UMASK
    majority_puid = _find_majority_env(participants, "PUID", default="1000")
    majority_pgid = _find_majority_env(participants, "PGID", default="1000")
    majority_tz = _find_majority_env(participants, "TZ", default=None)
    target_umask = "002"  # Standard permissive value

    # Determine which env vars need fixing per conflict type
    conflict_types = {c.conflict_type for c in cat_b}
    fix_puid = bool(conflict_types & {"puid_pgid_mismatch", "missing_puid_pgid", "root_execution", "cross_stack_puid_mismatch"})
    fix_umask = bool(conflict_types & {"umask_inconsistent", "umask_restrictive"})
    fix_tz = "tz_mismatch" in conflict_types

    lines = [
        "# Corrected environment configuration",
        "# All media services aligned to the same user identity",
        "#",
    ]
    if fix_puid:
        lines.append(f"# Target PUID:PGID = {majority_puid}:{majority_pgid}")
    if fix_umask:
        lines.append(f"# Target UMASK = {target_umask}")
    if fix_tz and majority_tz:
        lines.append(f"# Target TZ = {majority_tz}")
    lines.append("#")
    lines.append("")
    lines.append("services:")

    changed_lines: List[int] = []

    for svc in services:
        lines.append(f"  {svc.name}:")
        lines.append("    environment:")

        env = dict(svc.environment) if svc.environment else {}
        is_participant = svc.role in ("arr", "download_client", "media_server")

        if is_participant:
            if fix_puid:
                old_puid = env.get("PUID", "")
                old_pgid = env.get("PGID", "")
                env["PUID"] = majority_puid
                env["PGID"] = majority_pgid
                # Track if we changed something
                if old_puid != majority_puid or old_pgid != majority_pgid:
                    pass  # Mark changed below

            if fix_umask:
                env["UMASK"] = target_umask

            if fix_tz and majority_tz:
                env["TZ"] = majority_tz

        # Write env vars
        for key in sorted(env.keys()):
            line = f"      - {key}={env[key]}"
            lines.append(line)
            # Mark as changed if this is a corrected value on a participant
            if is_participant:
                original = svc.environment.get(key, "")
                if str(original) != str(env[key]):
                    changed_lines.append(len(lines))

        # Keep existing volumes reference
        if svc.volumes:
            lines.append("    volumes:")
            for vol in svc.volumes:
                lines.append(f"      - {vol.raw}")

        lines.append("")

    return "\n".join(lines), changed_lines


def _find_majority_env(
    services: List[ServiceInfo], key: str, default: Optional[str] = None,
) -> Optional[str]:
    """Find the most common value for an env var across services."""
    counts: Dict[str, int] = {}
    for svc in services:
        val = svc.environment.get(key, "").strip()
        if val:
            counts[val] = counts.get(val, 0) + 1
    if not counts:
        return default
    return max(counts, key=lambda k: counts[k])
```

Add `env_solution_yaml` to the `AnalysisResult` dataclass at line 186:

```python
    env_solution_yaml: Optional[str] = None
    env_solution_changed_lines: List[int] = field(default_factory=list)
```

Add to `AnalysisResult.to_dict()`:

```python
    "env_solution_yaml": self.env_solution_yaml,
    "env_solution_changed_lines": self.env_solution_changed_lines,
```

Wire into `analyze_stack()` at ~line 469 (after existing `_generate_solution_yaml` call):

```python
    env_solution_yaml, env_solution_changed_lines = _generate_env_solution(
        conflicts, services
    )
```

And set on the result object.

**Step 4: Run test to verify it passes**

Run: `cd C:\Projects\maparr && python -m pytest tests/test_env_solution.py -v -p no:capture`
Expected: All PASS

**Step 5: Run full test suite**

Run: `cd C:\Projects\maparr && python -m pytest tests/ -p no:capture --tb=short`

**Step 6: Commit**

```bash
git add backend/analyzer.py tests/test_env_solution.py
git commit -m "feat: add _generate_env_solution() for Category B permission fixes"
```

---

### Task 5: Category-Aware `_generate_solution_yaml()` Gating

**Files:**
- Modify: `backend/analyzer.py:2650-2731` (`_generate_solution_yaml`)
- Test: `tests/test_env_solution.py` (extend existing tests)

**Context:** Currently `_generate_solution_yaml()` processes ALL conflicts. After this task, it only processes Category A (path) conflicts. If no Category A conflicts exist, it returns `(None, [])`.

**Step 1: Write the failing test**

Add to `tests/test_env_solution.py`:

```python
class TestSolutionYamlGating:
    """_generate_solution_yaml should only fire for Category A conflicts."""

    def test_permission_only_no_volume_yaml(self, make_stack):
        """Permission-only stack must NOT get volume solution YAML."""
        result = analyze_and_get_result(make_stack, """
            services:
              sonarr:
                image: lscr.io/linuxserver/sonarr:latest
                environment:
                  - PUID=1000
                  - PGID=1000
                volumes:
                  - /data:/data
              radarr:
                image: lscr.io/linuxserver/radarr:latest
                environment:
                  - PUID=1001
                  - PGID=1001
                volumes:
                  - /data:/data
        """)
        assert result["solution_yaml"] is None
        assert result["solution_changed_lines"] == []
```

**Step 2: Run test to verify it fails**

Run: `cd C:\Projects\maparr && python -m pytest tests/test_env_solution.py::TestSolutionYamlGating -v -p no:capture`
Expected: FAIL — `solution_yaml` is not None (current code generates volume YAML for any conflict)

**Step 3: Add category filter to `_generate_solution_yaml`**

In `backend/analyzer.py:2650`, modify the function:

```python
def _generate_solution_yaml(
    conflicts: List[Conflict], services: List[ServiceInfo],
    host_root_override: Optional[str] = None,
) -> Tuple[Optional[str], List[int]]:
    """..."""
    # Only process Category A (path) conflicts
    path_conflicts = [c for c in conflicts if c.category == "A"]
    if not path_conflicts:
        return None, []

    # ... rest of function uses path_conflicts instead of conflicts
```

Replace all references to `conflicts` inside this function with `path_conflicts`.

**Step 4: Run test to verify it passes**

Run: `cd C:\Projects\maparr && python -m pytest tests/test_env_solution.py -v -p no:capture`
Expected: All PASS

**Step 5: Run full test suite**

Run: `cd C:\Projects\maparr && python -m pytest tests/ -p no:capture --tb=short`
Expected: Check carefully — some existing tests may assert `solution_yaml is not None` for stacks that only have permission conflicts. Fix those test expectations.

**Step 6: Commit**

```bash
git add backend/analyzer.py tests/test_env_solution.py
git commit -m "feat: gate _generate_solution_yaml to Category A (path) conflicts only"
```

---

### Task 6: Category-Aware `_patch_original_yaml()`

**Files:**
- Modify: `backend/analyzer.py:2885-2939` (`_patch_original_yaml`)
- Modify: `backend/analyzer.py:~480` (call site in `analyze_stack`)
- Test: `tests/test_env_solution.py` (extend)

**Context:** `_patch_original_yaml()` currently only patches `volumes:` blocks. It needs a companion `_patch_original_env()` for Category B, or the existing function needs to become category-aware and patch `environment:` blocks when Cat B conflicts are present.

**Approach:** Add a new `_patch_original_env()` function that patches environment lines. The call site in `analyze_stack()` decides which patcher(s) to invoke based on conflict categories present.

**Step 1: Write the failing test**

```python
class TestPatchOriginalEnv:
    """Patching user's original YAML for environment fixes."""

    def test_puid_mismatch_patches_env_in_original(self, make_stack):
        """Original corrected YAML should show patched environment vars."""
        result = analyze_and_get_result(make_stack, """
            services:
              sonarr:
                image: lscr.io/linuxserver/sonarr:latest
                environment:
                  - PUID=1000
                  - PGID=1000
                volumes:
                  - /data:/data
              radarr:
                image: lscr.io/linuxserver/radarr:latest
                environment:
                  - PUID=1001
                  - PGID=1001
                volumes:
                  - /data:/data
        """)
        corrected = result.get("original_corrected_yaml")
        assert corrected is not None
        # The corrected version should have the majority PUID/PGID applied
        assert "PUID=1000" in corrected
        # And there should be changed lines marked
        assert len(result.get("original_changed_lines", [])) > 0
```

**Step 2: Run test, verify failure, implement, verify pass** (same TDD cycle)

**Step 3: Implement `_patch_original_env()`**

Similar structure to `_patch_original_yaml()` but targets `environment:` blocks instead of `volumes:` blocks. Line-based parsing, finds each affected service's environment section, replaces the PUID/PGID/UMASK/TZ lines.

**Step 4: Update `analyze_stack()` call site**

At ~line 477-486, make the original_corrected generation category-aware:

```python
    # Step 7: Generate corrected version of user's original compose
    original_corrected_yaml = None
    original_changed_lines: List[int] = []
    cat_a = any(c.category == "A" for c in conflicts)
    cat_b = any(c.category == "B" for c in conflicts)

    if raw_compose_content:
        if cat_a:
            original_corrected_yaml, original_changed_lines = _patch_original_yaml(
                raw_compose_content, conflicts, services,
                host_root_override=pipeline_host_root,
            )
        if cat_b:
            env_patched, env_changed = _patch_original_env(
                original_corrected_yaml or raw_compose_content,
                conflicts, services,
            )
            if env_patched:
                original_corrected_yaml = env_patched
                original_changed_lines.extend(env_changed)
```

This means mixed A+B stacks get BOTH patches applied sequentially.

**Step 5: Run full test suite, commit**

```bash
git add backend/analyzer.py tests/test_env_solution.py
git commit -m "feat: add _patch_original_env() for Category B environment patching"
```

---

### Task 7: RPM Wizard Gating

**Files:**
- Modify: `backend/analyzer.py:~line 469` (call site) or `_calculate_rpm_mappings:2446`
- Test: `tests/test_env_solution.py` (extend)

**Context:** RPM wizard should only appear when Category A path conflicts exist AND `rpm_mappings` has `possible: true`. Never for permission-only or infrastructure-only stacks.

**Step 1: Write the failing test**

```python
class TestRpmGating:
    """RPM wizard must only appear for Category A path conflicts."""

    def test_permission_only_no_rpm(self, make_stack):
        """Permission-only stack — RPM mappings should be empty."""
        result = analyze_and_get_result(make_stack, """
            services:
              sonarr:
                image: lscr.io/linuxserver/sonarr:latest
                environment:
                  - PUID=1000
                  - PGID=1000
                volumes:
                  - /data:/data
              radarr:
                image: lscr.io/linuxserver/radarr:latest
                environment:
                  - PUID=1001
                  - PGID=1001
                volumes:
                  - /data:/data
        """)
        assert result["rpm_mappings"] == []

    def test_path_conflict_has_rpm(self, make_stack):
        """Path conflict stack should have RPM mappings (when pipeline available)."""
        # This test needs pipeline_context — may need to set up a pipeline dir
        # Test verifies RPM is NOT suppressed when Cat A conflicts exist
        pass  # Implementation: set up pipeline context with arr+dc
```

**Step 2: Implement gating**

In `analyze_stack()`, gate the RPM call:

```python
    # Only calculate RPM mappings when Category A path conflicts exist
    has_cat_a = any(c.category == "A" for c in conflicts)
    if has_cat_a:
        rpm_mappings = _calculate_rpm_mappings(services, pipeline_context, stack_path)
    else:
        rpm_mappings = []
```

**Step 3: Run tests, commit**

```bash
git add backend/analyzer.py tests/test_env_solution.py
git commit -m "feat: gate RPM wizard to Category A path conflicts only"
```

---

### Task 8: Pipeline Health Awareness (Permissions + TZ)

**Files:**
- Modify: `backend/pipeline.py:285-304` (health determination)
- Modify: `backend/pipeline.py:~200` (add permission/TZ checks after mount analysis)
- Test: `tests/test_pipeline.py` (extend)

**Context:** The pipeline scan currently only checks mount consistency. It needs lightweight permission and TZ checks so the dashboard health dots can show yellow for permission mismatches without requiring a full drill-down analysis.

**Step 1: Write the failing test**

```python
class TestPipelinePermissionAwareness:
    """Pipeline scan should detect permission mismatches across stacks."""

    def test_cross_stack_puid_mismatch_shows_warning(self, make_pipeline_dir):
        """Two stacks with different PUID should produce pipeline health=warning."""
        from backend.pipeline import run_pipeline_scan
        scan_dir = make_pipeline_dir({
            "sonarr": """
                services:
                  sonarr:
                    image: lscr.io/linuxserver/sonarr:latest
                    environment:
                      - PUID=1000
                      - PGID=1000
                    volumes:
                      - /data:/data
            """,
            "radarr": """
                services:
                  radarr:
                    image: lscr.io/linuxserver/radarr:latest
                    environment:
                      - PUID=1001
                      - PGID=1001
                    volumes:
                      - /data:/data
            """,
        })
        result = run_pipeline_scan(scan_dir).to_dict()
        # Should have a permission-related conflict or warning
        perm_conflicts = [c for c in result["conflicts"] if "puid" in c.get("type", "").lower() or "permission" in c.get("type", "").lower()]
        assert len(perm_conflicts) > 0 or result["health"] in ("warning", "problem")
```

**Step 2: Implement pipeline permission check**

In `pipeline.py`, after the mount consistency analysis (~line 284), add:

```python
    # Permission consistency check across all media services
    _check_pipeline_permissions(result, all_services, steps)
```

New function:

```python
def _check_pipeline_permissions(
    result: PipelineResult,
    all_services: List[PipelineService],
    steps: List[dict],
) -> None:
    """Lightweight permission check: flag when PUID/PGID groups diverge."""
    puid_groups: Dict[str, List[str]] = {}
    for svc in all_services:
        puid = svc.environment.get("PUID", "")
        pgid = svc.environment.get("PGID", "")
        if puid and pgid:
            key = f"{puid}:{pgid}"
            puid_groups.setdefault(key, []).append(svc.service_name)

    if len(puid_groups) > 1:
        majority_key = max(puid_groups, key=lambda k: len(puid_groups[k]))
        for key, svc_names in puid_groups.items():
            if key != majority_key:
                for name in svc_names:
                    result.conflicts.append({
                        "type": "pipeline_permission_mismatch",
                        "severity": "high",
                        "service_name": name,
                        "services": [name],
                        "description": f"{name} runs as {key} but majority use {majority_key}",
                    })
        steps.append({
            "icon": "warn",
            "text": f"Permission mismatch: {len(puid_groups)} different PUID:PGID groups",
        })
```

Update health determination at ~line 285:

```python
    # Determine overall health
    mount_conflicts = [c for c in result.conflicts if c.get("type") == "pipeline_mount_mismatch"]
    perm_conflicts = [c for c in result.conflicts if c.get("type") == "pipeline_permission_mismatch"]

    if mount_conflicts:
        result.health = "problem"
    elif perm_conflicts:
        result.health = "warning"
    elif result.roles_missing:
        result.health = "warning"
    else:
        result.health = "ok"
```

**Step 3: Run tests, commit**

```bash
git add backend/pipeline.py tests/test_pipeline.py
git commit -m "feat: add permission awareness to pipeline scan for dashboard health"
```

---

### Task 9: Frontend Category-Aware Rendering

**Files:**
- Modify: `frontend/app.js` — `showProblem()` (~line 2658), `showSolution()` (~line 2771), `getServiceHealth()` (~line 710)
- Modify: `frontend/styles.css` — blue info badge, recommendation card styles
- Modify: `frontend/index.html` — observation section container

**Context:** The frontend needs to render different UI based on conflict category. This is the largest frontend task. Key changes:
1. `showProblem()` — add one-sentence handrail per conflict type, rename to "Recommendation" for Cat C
2. `showSolution()` — hide RPM tab for non-Cat-A, show "Fix Permissions" tab for Cat B, show "What You Can Do" for Cat C
3. `getServiceHealth()` — use `category` field from backend to determine health dot color
4. Dashboard conflict summary bar

**Step 1: Add conflict handrails constant**

In `app.js`, add near the top:

```javascript
// Plain-English handrails for each conflict type — the "knowledgeable friend" voice
const CONFLICT_HANDRAILS = {
    // Category A: Path Conflicts
    no_shared_mount: "Your download client saves files to one folder, but your *arr app is looking in a different folder. They can't see each other's files.",
    different_host_paths: "These services think they're sharing the same folder, but on the host they're actually pointing at different directories.",
    named_volume_data: "Docker named volumes are isolated from each other. Files in one volume are invisible to services using a different volume.",
    path_unreachable: "The error path doesn't match any mount in your compose — the app can't reach the file it's looking for.",
    // Category B: Permission Conflicts
    puid_pgid_mismatch: "Your services run as different Linux users. Files created by one app can't be read by another.",
    missing_puid_pgid: "Without explicit PUID/PGID, these containers default to an internal user that probably doesn't match your other services.",
    root_execution: "Running as root (UID 0) means files are owned by root. Other services running as a normal user can't modify them — and it's a security risk.",
    umask_inconsistent: "UMASK controls who can access newly created files. Different values mean some apps can't read files created by others.",
    umask_restrictive: "UMASK controls who can access newly created files. Different values mean some apps can't read files created by others.",
    tz_mismatch: "Services in different timezones will schedule grabs at unexpected times and show confusing timestamps in logs.",
    cross_stack_puid_mismatch: "This service runs as a different Linux user than services in other stacks. Files won't be accessible across your setup.",
    // Category C: Infrastructure
    wsl2_performance: "Your media data lives on a Windows drive accessed through WSL2's filesystem bridge. This works but is significantly slower than native Linux storage.",
    remote_filesystem: "Your data is on a network share. Hardlinks don't work across network boundaries.",
    mixed_mount_types: "Some services use local storage, others use network storage. Hardlinks can't cross that boundary.",
    windows_path_in_compose: "Windows-style paths work but forward slashes and native Linux paths perform better in Docker.",
};
```

**Step 2: Update `showProblem()` to render handrails**

After the existing description element, insert:

```javascript
const handrail = CONFLICT_HANDRAILS[conflict.type];
if (handrail) {
    const handrailEl = document.createElement('p');
    handrailEl.className = 'conflict-handrail';
    handrailEl.textContent = handrail;
    problemCard.appendChild(handrailEl);
}
```

For Category C conflicts, rename the card header from "The Problem" to "Recommendation" and use blue info badge instead of red/yellow.

**Step 3: Update `showSolution()` for category awareness**

```javascript
const categories = new Set(conflicts.map(c => c.category));
const hasCatA = categories.has('A');
const hasCatB = categories.has('B');
const hasCatC = categories.has('C');

// RPM wizard tab: only for Category A
if (hasCatA && hasRpmMappings) {
    // Show RPM wizard tab (existing code)
}

// Volume fix tab: only for Category A
if (hasCatA) {
    // Show "Proper Fix (Restructure)" tab (existing code)
}

// Permissions fix tab: only for Category B
if (hasCatB) {
    // Show "Fix Permissions" tab with env_solution_yaml
    // Intro: "These environment variable changes align your services..."
}

// Category C: guidance only
if (hasCatC && !hasCatA && !hasCatB) {
    // Show "What You Can Do" with plain text guidance
    // No YAML tabs
}
```

**Step 4: Update `getServiceHealth()`**

```javascript
function getServiceHealth(service) {
    if (!service.conflicts || service.conflicts.length === 0) return 'healthy';
    const categories = new Set(service.conflicts.map(c => c.category));
    if (categories.has('A')) return 'problem';  // Red
    if (categories.has('B')) return 'issue';     // Yellow
    return 'healthy';  // Cat C/D don't affect health dot
}
```

**Step 5: Add CSS for new elements**

```css
.conflict-handrail {
    color: var(--text-muted);
    font-size: 0.88rem;
    margin-top: 0.5rem;
    line-height: 1.5;
    font-style: italic;
}

.badge-info {
    background: var(--info, #3b82f6);
    color: white;
}

.recommendation-card {
    border-left: 3px solid var(--info, #3b82f6);
}
```

**Step 6: Manual test + commit**

This task is frontend-heavy. Test by:
1. Starting server: `cd C:\Projects\maparr && python -m backend.main`
2. Analyzing a permission-only stack (audit stack 01)
3. Verify: no RPM tab, "Fix Permissions" tab shown, handrail text visible
4. Analyzing a WSL2 stack (audit stack 06)
5. Verify: "Recommendation" header, blue badge, "What You Can Do" section

```bash
git add frontend/app.js frontend/styles.css frontend/index.html
git commit -m "feat: category-aware frontend rendering with handrails and gated tabs"
```

---

### Task 10: Category D Observations Collection

**Files:**
- Modify: `backend/analyzer.py` (new `_collect_observations()` function, add to `analyze_stack`)
- Modify: `backend/analyzer.py:186` (add `observations` field to `AnalysisResult`)
- Test: `tests/test_categories.py` (extend)

**Context:** Category D items are noticed-but-not-actioned: missing restart policy, latest tag usage, missing TZ (when no TZ at all), privileged mode, no healthcheck. These are collected during analysis and returned separately from conflicts. They have no health impact and no fix buttons.

**Step 1: Write the failing test**

```python
class TestObservations:
    """Category D observations: noticed, not actioned."""

    def test_latest_tag_observed(self, make_stack):
        result = analyze_and_get_result(make_stack, """
            services:
              sonarr:
                image: lscr.io/linuxserver/sonarr:latest
                environment:
                  - PUID=1000
                  - PGID=1000
                volumes:
                  - /data:/data
              radarr:
                image: lscr.io/linuxserver/radarr:latest
                environment:
                  - PUID=1000
                  - PGID=1000
                volumes:
                  - /data:/data
        """)
        obs = result.get("observations", [])
        latest_obs = [o for o in obs if o["type"] == "latest_tag_usage"]
        assert len(latest_obs) > 0

    def test_missing_restart_policy_observed(self, make_stack):
        result = analyze_and_get_result(make_stack, """
            services:
              sonarr:
                image: lscr.io/linuxserver/sonarr:latest
                environment:
                  - PUID=1000
                  - PGID=1000
                volumes:
                  - /data:/data
        """)
        obs = result.get("observations", [])
        restart_obs = [o for o in obs if o["type"] == "missing_restart_policy"]
        assert len(restart_obs) > 0

    def test_observations_have_no_health_impact(self, make_stack):
        """Healthy stack with only observations should still be healthy."""
        result = analyze_and_get_result(make_stack, """
            services:
              sonarr:
                image: lscr.io/linuxserver/sonarr:latest
                environment:
                  - PUID=1000
                  - PGID=1000
                volumes:
                  - /data:/data
              radarr:
                image: lscr.io/linuxserver/radarr:latest
                environment:
                  - PUID=1000
                  - PGID=1000
                volumes:
                  - /data:/data
        """)
        # Observations exist but status should still be healthy
        assert result["status"] in ("healthy", "healthy_pipeline", "healthy_cross_stack")
```

**Step 2: Implement `_collect_observations()`**

```python
def _collect_observations(
    resolved_compose: Dict[str, Any], services: List[ServiceInfo],
) -> List[dict]:
    """Collect Category D observations — informational only, no health impact."""
    observations = []
    compose_services = resolved_compose.get("services", {})

    for svc_name, svc_data in compose_services.items():
        # Missing restart policy
        if not svc_data.get("restart"):
            observations.append({
                "type": "missing_restart_policy",
                "service": svc_name,
                "message": f"{svc_name} doesn't have a restart policy — it won't come back after a reboot",
            })

        # Latest tag usage
        image = svc_data.get("image", "")
        if image.endswith(":latest") or ":" not in image:
            observations.append({
                "type": "latest_tag_usage",
                "service": svc_name,
                "message": f"{svc_name} uses the :latest tag — pinning to a version prevents surprise updates",
            })

        # Missing TZ (no TZ at all, vs mismatch which is Cat B)
        env = _parse_env_dict(svc_data.get("environment", []))
        if not env.get("TZ"):
            observations.append({
                "type": "missing_tz",
                "service": svc_name,
                "message": f"{svc_name} has no TZ set — it'll default to UTC which might confuse scheduling",
            })

        # Privileged mode
        if svc_data.get("privileged"):
            observations.append({
                "type": "privileged_mode",
                "service": svc_name,
                "message": f"{svc_name} runs in privileged mode — this gives it full host access",
            })

    return observations
```

Add `observations` field to `AnalysisResult` and its `to_dict()`.

**Step 3: Run tests, commit**

```bash
git add backend/analyzer.py tests/test_categories.py
git commit -m "feat: collect Category D observations (restart, tags, TZ, privileged)"
```

---

### Task 11: Frontend Observations Section

**Files:**
- Modify: `frontend/app.js` — new `renderObservations()` function
- Modify: `frontend/styles.css` — observation section styles
- Modify: `frontend/index.html` — observation container

**Context:** Observations render as a collapsed section at the bottom of the analysis detail. Casual tone, no badges, no fix buttons. Footer points to ComposeArr.

**Step 1: Implement the render function**

```javascript
function renderObservations(observations) {
    if (!observations || observations.length === 0) return '';

    const items = observations.map(o => `<li>${o.message}</li>`).join('');
    return `
        <details class="observations-section">
            <summary>A few other things we noticed (${observations.length})</summary>
            <ul class="observations-list">${items}</ul>
            <p class="observations-footer">
                For full compose hygiene analysis, check out
                <a href="https://github.com/coaxk/composearr" target="_blank">ComposeArr</a>
            </p>
        </details>
    `;
}
```

Wire it into the analysis detail view, after the solution section.

**Step 2: Add CSS**

```css
.observations-section {
    margin-top: 1.5rem;
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 0.75rem 1rem;
}
.observations-section summary {
    cursor: pointer;
    color: var(--text-muted);
    font-size: 0.85rem;
}
.observations-list {
    color: var(--text-muted);
    font-size: 0.85rem;
    line-height: 1.6;
}
.observations-footer {
    color: var(--text-dim);
    font-size: 0.78rem;
    margin-top: 0.5rem;
}
```

**Step 3: Manual test + commit**

```bash
git add frontend/app.js frontend/styles.css frontend/index.html
git commit -m "feat: add collapsed observations section with ComposeArr cross-reference"
```

---

### Task 12: Audit Verification — Zero Disconnects

**Files:**
- Run: `tools/audit_pipeline.py`
- Verify: 0 disconnects, 19/19 classifications

**Step 1: Run the audit**

Run: `cd C:\Projects\maparr && python tools/audit_pipeline.py`

Expected output:
```
Classification: 19/19 pass
Stacks tested: 16
Disconnects found: 0
  CRITICAL: 0
  HIGH: 0
  MEDIUM: 0
```

**Step 2: If any disconnects remain, fix them**

Common issues to watch for:
- Existing tests that assert `solution_yaml is not None` for permission-only stacks (update expectations)
- Audit stacks that need `tz_mismatch` coverage (add audit stack 16 if needed)
- Pipeline health dots not mapping correctly for permission conflicts

**Step 3: Run full test suite**

Run: `cd C:\Projects\maparr && python -m pytest tests/ -p no:capture --tb=short`
Expected: All tests PASS

**Step 4: Commit any fixups**

```bash
git add -A
git commit -m "fix: resolve remaining audit disconnects — 0 disconnects, 19/19 classification"
```

---

### Task 13: Final Integration Test + Memory Update

**Step 1: Start the server and manually verify**

1. `cd C:\Projects\maparr && python -m backend.main`
2. Point at audit-stacks directory
3. Walk through each audit stack:
   - 01 (puid mismatch) → Yellow health, "Fix Permissions" tab, no RPM, handrail text
   - 06 (wsl2) → Green health dot, blue "Recommendation" card in drill-down
   - 10 (no shared mount) → Red health, RPM + Restructure tabs, handrail text
   - 13 (path + perm) → Red health, ALL tabs shown (RPM + Restructure + Fix Permissions)
   - 14 (healthy) → Green, observations section if any
4. Verify dashboard conflict summary bar shows counts by category

**Step 2: Update CLAUDE.md and MEMORY.md**

Document:
- `CONFLICT_CATEGORIES` constant location
- `_generate_env_solution()` pattern
- `_patch_original_env()` exists
- Pipeline permission awareness
- Category D observations
- 0 disconnect audit result
- All new test files

**Step 3: Final commit**

```bash
git add -A
git commit -m "docs: update project docs for analysis pipeline rebuild"
```

---

## Execution Notes

**Task dependencies:**
- Tasks 1-2 are independent (can run in parallel)
- Task 3 depends on Task 1 (uses `CONFLICT_CATEGORIES`)
- Task 4 depends on Tasks 1+3
- Task 5 depends on Task 1
- Task 6 depends on Tasks 4+5
- Task 7 depends on Task 1
- Task 8 is independent of Tasks 3-7
- Task 9 depends on Tasks 4-7 (needs backend changes to render)
- Tasks 10-11 are independent of Tasks 3-9
- Task 12 depends on all previous tasks
- Task 13 depends on Task 12

**Parallel opportunities:**
- Tasks 1 + 2 (independent)
- Tasks 5 + 7 + 8 (all depend only on Task 1, not each other)
- Tasks 10 + 11 (independent of main pipeline work)

**Risk areas:**
- Task 5 (gating `_generate_solution_yaml`) may break existing tests that expect volume YAML for permission-only stacks — be prepared to update test expectations
- Task 9 (frontend) is the largest single task — consider splitting into sub-tasks if needed
- Task 6 (`_patch_original_env`) is the most complex new function — line-based YAML patching is fiddly
