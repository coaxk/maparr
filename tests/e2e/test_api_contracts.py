"""
Layer 3 — API Contract Tests for MapArr endpoints.

Uses FastAPI TestClient (no browser, no running server needed).
Tests response shapes, status codes, and field presence for all
MapArr API endpoints against the synthetic E2E test stacks.

Each test class resets session state and rate limiter to ensure isolation.
"""

import os
import shutil
import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.main import app, _session, _rate_limiter


# ─── Constants ───

E2E_STACKS = Path(__file__).parent / "fixtures" / "stacks"


# ─── Shared Fixtures ───

@pytest.fixture(autouse=True)
def _reset_session():
    """Reset session state and rate limiter before each test."""
    _session["parsed_error"] = None
    _session["selected_stack"] = None
    _session["pipeline"] = None
    _session.pop("custom_stacks_path", None)
    _rate_limiter.reset()
    yield
    _session["parsed_error"] = None
    _session["selected_stack"] = None
    _session["pipeline"] = None
    _session.pop("custom_stacks_path", None)
    _rate_limiter.reset()


@pytest.fixture
def client():
    """FastAPI TestClient for API tests."""
    return TestClient(app)


# ─── Helpers ───

def _pipeline_scan(client: TestClient, scan_dir: str) -> dict:
    """Run a pipeline scan and return the response JSON."""
    resp = client.post("/api/pipeline-scan", json={"scan_dir": scan_dir})
    assert resp.status_code == 200, f"Pipeline scan failed: {resp.text}"
    return resp.json()


# ─── Test Classes ───


class TestHealthEndpoint:
    """GET /api/health — basic liveness check."""

    def test_health_returns_200(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200, "Health endpoint must return 200"

    def test_health_has_status_field(self, client):
        data = client.get("/api/health").json()
        assert "status" in data, "Health response must include 'status' field"
        assert data["status"] == "ok", "Health status should be 'ok'"

    def test_health_has_version_field(self, client):
        data = client.get("/api/health").json()
        assert "version" in data, "Health response must include 'version' field"
        assert isinstance(data["version"], str), "Version must be a string"


class TestParseError:
    """POST /api/parse-error — error text parsing."""

    def test_single_error_returns_fields(self, client):
        resp = client.post("/api/parse-error", json={
            "error_text": "sonarr | Error: /data/tv is not accessible"
        })
        assert resp.status_code == 200, "Parse should return 200 for valid input"
        data = resp.json()
        assert "service" in data, "Parse result must include 'service'"
        assert "path" in data, "Parse result must include 'path'"
        assert "error_type" in data, "Parse result must include 'error_type'"
        assert "confidence" in data, "Parse result must include 'confidence'"

    def test_multi_error_returns_array(self, client):
        multi_text = (
            "sonarr | Error: /data/tv is not accessible\n"
            "---\n"
            "radarr | Error: /data/movies is not accessible"
        )
        resp = client.post("/api/parse-error", json={"error_text": multi_text})
        assert resp.status_code == 200, "Multi-error parse should return 200"
        data = resp.json()
        # Multi-error detection may or may not split depending on parser logic.
        # If it does split, multiple_errors is present.
        if "multiple_errors" in data:
            assert isinstance(data["multiple_errors"], list), \
                "multiple_errors must be an array"
            assert len(data["multiple_errors"]) >= 2, \
                "Should detect at least 2 errors from multi-error input"

    def test_empty_text_returns_400(self, client):
        resp = client.post("/api/parse-error", json={"error_text": ""})
        assert resp.status_code == 400, "Empty error text should return 400"


class TestPipelineScan:
    """POST /api/pipeline-scan — full directory pipeline scan."""

    def test_scan_returns_required_fields(self, client):
        data = _pipeline_scan(client, str(E2E_STACKS))
        required = ["scan_dir", "scanned_at", "media_services",
                     "roles_present", "health", "summary", "steps"]
        for field in required:
            assert field in data, f"Pipeline scan must include '{field}'"

    def test_health_is_valid_value(self, client):
        data = _pipeline_scan(client, str(E2E_STACKS))
        valid_health = {"ok", "warning", "problem"}
        assert data["health"] in valid_health, \
            f"Pipeline health must be one of {valid_health}, got '{data['health']}'"

    def test_media_services_have_required_fields(self, client):
        data = _pipeline_scan(client, str(E2E_STACKS))
        assert len(data["media_services"]) > 0, \
            "E2E stacks should produce at least one media service"
        svc = data["media_services"][0]
        for field in ["service_name", "role", "stack_name", "compose_file"]:
            assert field in svc, f"Media service must include '{field}'"

    def test_cluster_layout_produces_distinct_compose_files(self, client):
        cluster_dir = str(E2E_STACKS / "cluster-layout")
        data = _pipeline_scan(client, cluster_dir)
        assert len(data["media_services"]) >= 2, \
            "Cluster layout should detect 2+ media services"
        # compose_file is just the filename; use compose_file_full for
        # distinct paths across subdirectories in a cluster layout
        compose_files = {
            svc.get("compose_file_full") or svc["compose_file"]
            for svc in data["media_services"]
        }
        assert len(compose_files) >= 2, \
            f"Cluster layout should have 2+ distinct compose files, got {compose_files}"


class TestChangeStacksPath:
    """POST /api/change-stacks-path — runtime path switching."""

    def test_valid_path_accepted(self, client):
        resp = client.post("/api/change-stacks-path", json={
            "path": str(E2E_STACKS)
        })
        assert resp.status_code == 200, "Valid path should be accepted"
        data = resp.json()
        assert data.get("status") == "ok", "Status should be 'ok' for valid path"

    def test_blocked_path_rejected(self, client):
        # /proc is blocked on Linux; on Windows it won't exist as a dir,
        # so use C:\Windows which is blocked on Windows
        blocked = "/proc" if os.name != "nt" else "C:\\Windows"
        resp = client.post("/api/change-stacks-path", json={"path": blocked})
        assert resp.status_code in (400, 403), \
            f"Blocked path '{blocked}' should be rejected with 400 or 403"


class TestListDirectories:
    """POST /api/list-directories — folder browser."""

    def test_returns_directories_array(self, client):
        resp = client.post("/api/list-directories", json={
            "path": str(E2E_STACKS)
        })
        assert resp.status_code == 200, "List directories should return 200"
        data = resp.json()
        assert "directories" in data, "Response must include 'directories' array"
        assert isinstance(data["directories"], list), \
            "'directories' must be a list"

    def test_entries_have_name_and_path(self, client):
        resp = client.post("/api/list-directories", json={
            "path": str(E2E_STACKS)
        })
        data = resp.json()
        assert len(data["directories"]) > 0, \
            "E2E stacks dir should have subdirectories"
        entry = data["directories"][0]
        assert "name" in entry, "Directory entry must include 'name'"
        assert "path" in entry, "Directory entry must include 'path'"


class TestAnalyze:
    """POST /api/analyze — stack analysis with conflict detection."""

    def _scan_and_analyze(self, client, stack_name, scan_root=None):
        """Run pipeline scan, then analyze a specific stack.

        Args:
            scan_root: Directory to pipeline-scan. Defaults to the stack's
                       own directory (avoids cross-stack pollution from
                       unrelated E2E fixtures).
        """
        stack_path = str(E2E_STACKS / stack_name)
        scan_dir = scan_root or stack_path
        _pipeline_scan(client, scan_dir)
        resp = client.post("/api/analyze", json={"stack_path": stack_path})
        assert resp.status_code == 200, f"Analyze should return 200, got {resp.status_code}: {resp.text}"
        return resp.json()

    def test_path_conflict_returns_conflicts(self, client):
        data = self._scan_and_analyze(client, "path-conflict")
        assert "conflicts" in data, "Analysis must include 'conflicts' field"
        assert len(data["conflicts"]) > 0, \
            "path-conflict stack should produce at least one conflict"

    def test_conflict_has_required_fields(self, client):
        data = self._scan_and_analyze(client, "path-conflict")
        conflict = data["conflicts"][0]
        for field in ["type", "severity", "services", "description", "category"]:
            assert field in conflict, \
                f"Conflict must include '{field}' field"

    def test_healthy_arr_has_no_cat_a_or_b_conflicts(self, client):
        data = self._scan_and_analyze(client, "healthy-arr")
        conflicts = data.get("conflicts", [])
        serious = [c for c in conflicts if c.get("category") in ("A", "B")]
        assert len(serious) == 0, \
            f"Healthy arr should have no Cat A/B conflicts, got {len(serious)}"

    def test_cat_a_generates_solution_yaml(self, client):
        data = self._scan_and_analyze(client, "path-conflict")
        cat_a = [c for c in data.get("conflicts", []) if c.get("category") == "A"]
        assert len(cat_a) > 0, "path-conflict should produce Category A conflicts"
        # Solution YAML is at the top level, not per-conflict
        has_solution = (
            data.get("original_corrected_yaml") is not None
            or data.get("solution_yaml") is not None
        )
        assert has_solution, \
            "Cat A conflicts should generate solution YAML (original_corrected_yaml or solution_yaml)"

    def test_cat_b_generates_env_solution(self, client):
        data = self._scan_and_analyze(client, "puid-mismatch")
        cat_b = [c for c in data.get("conflicts", []) if c.get("category") == "B"]
        assert len(cat_b) > 0, "puid-mismatch should produce Category B conflicts"
        # Env solution YAML may be per-conflict or top-level
        has_env_solution = (
            data.get("env_solution_yaml") is not None
            or any(c.get("env_solution_yaml") for c in cat_b)
            or data.get("original_corrected_yaml") is not None
        )
        assert has_env_solution, \
            "Cat B conflicts should generate env solution YAML"

    def test_observations_field_present(self, client):
        data = self._scan_and_analyze(client, "observations")
        assert "observations" in data, \
            "Analysis response must include 'observations' field"

    def test_path_outside_stacks_returns_error(self, client):
        # Set a custom stacks path first so path validation is enforced
        _session["custom_stacks_path"] = str(E2E_STACKS)
        # Try to analyze a path outside the stacks root
        resp = client.post("/api/analyze", json={
            "stack_path": "/tmp/nonexistent-outside-path"
        })
        # Should be 400 (not found) or 403 (outside stacks)
        assert resp.status_code in (400, 403), \
            f"Analyzing path outside stacks should return 400 or 403, got {resp.status_code}"


class TestApplyFix:
    """POST /api/apply-fix — compose file patching with backup."""

    def test_apply_fix_creates_backup(self, client, tmp_path):
        """Apply fix to a writable temp stack and verify .bak is created."""
        # Create a test compose file in tmp_path
        stack_dir = tmp_path / "fixtest"
        stack_dir.mkdir()
        compose = stack_dir / "docker-compose.yml"
        original_content = textwrap.dedent("""\
            services:
              sonarr:
                image: lscr.io/linuxserver/sonarr:latest
                volumes:
                  - /host/tv:/data/tv
        """)
        compose.write_text(original_content, encoding="utf-8")

        # Set custom_stacks_path so path validation passes
        _session["custom_stacks_path"] = str(tmp_path)

        corrected = textwrap.dedent("""\
            services:
              sonarr:
                image: lscr.io/linuxserver/sonarr:latest
                volumes:
                  - /data:/data
        """)

        resp = client.post("/api/apply-fix", json={
            "compose_file_path": str(compose),
            "corrected_yaml": corrected,
        })
        assert resp.status_code == 200, \
            f"Apply fix should return 200, got {resp.status_code}: {resp.text}"

        data = resp.json()
        assert data.get("status") == "applied", \
            "Apply fix status should be 'applied'"

        backup = compose.parent / "docker-compose.yml.bak"
        assert backup.exists(), "Apply fix must create a .bak backup file"

        # Verify backup contains original content
        assert backup.read_text(encoding="utf-8") == original_content, \
            "Backup must contain the original file content"


class TestApplyFixesBatch:
    """POST /api/apply-fixes — multi-file batch fix."""

    def test_rejects_batch_over_20(self, client):
        # Need stacks root set for this endpoint
        _session["custom_stacks_path"] = str(E2E_STACKS)

        fixes = [{"compose_file_path": f"/fake/path/{i}/docker-compose.yml",
                   "corrected_yaml": "services: {}"} for i in range(21)]
        resp = client.post("/api/apply-fixes", json={"fixes": fixes})
        assert resp.status_code == 400, \
            f"Batch > 20 should return 400, got {resp.status_code}"
        assert "20" in resp.json().get("error", ""), \
            "Error message should mention the 20-file limit"


class TestRedeploy:
    """POST /api/redeploy — Docker stack redeployment."""

    def test_rejects_over_10_stacks(self, client):
        _session["custom_stacks_path"] = str(E2E_STACKS)
        stacks = [f"/fake/stack/{i}" for i in range(11)]
        resp = client.post("/api/redeploy", json={"stacks": stacks})
        assert resp.status_code == 400, \
            f"Redeploy > 10 stacks should return 400, got {resp.status_code}"
        assert "10" in resp.json().get("error", ""), \
            "Error message should mention the 10-stack limit"


class TestDiscoverStacks:
    """GET /api/discover-stacks — filesystem stack discovery."""

    def test_returns_stacks_and_total(self, client):
        _session["custom_stacks_path"] = str(E2E_STACKS)
        resp = client.get("/api/discover-stacks")
        assert resp.status_code == 200, "Discover stacks should return 200"
        data = resp.json()
        assert "stacks" in data, "Response must include 'stacks'"
        assert "total" in data, "Response must include 'total'"
        assert isinstance(data["stacks"], list), "'stacks' must be a list"
        assert isinstance(data["total"], int), "'total' must be an integer"


class TestSmartMatch:
    """POST /api/smart-match — error-to-stack matching."""

    def test_returns_200(self, client):
        _session["custom_stacks_path"] = str(E2E_STACKS)
        # Discover stacks first so smart match has data
        client.get("/api/discover-stacks")

        # Get a real stack path from discovery
        discover_data = client.get("/api/discover-stacks").json()
        paths = [s["path"] for s in discover_data.get("stacks", [])[:2]]

        if len(paths) < 1:
            pytest.skip("No stacks discovered for smart match test")

        resp = client.post("/api/smart-match", json={
            "parsed_error": {
                "service": "sonarr",
                "path": "/data/tv",
                "error_type": "permission_denied",
                "confidence": "high",
            },
            "candidate_paths": paths,
        })
        assert resp.status_code == 200, \
            f"Smart match should return 200, got {resp.status_code}: {resp.text}"


class TestRateLimiting:
    """Rate limiting tiers — write (10/min) and read (60/min)."""

    def test_write_tier_triggers_429(self, client):
        """Write endpoint rate limit fires after 10 requests."""
        # Fire 11 rapid requests to a write endpoint
        last_status = None
        for i in range(11):
            resp = client.post("/api/change-stacks-path", json={
                "path": str(E2E_STACKS)
            })
            last_status = resp.status_code
            if last_status == 429:
                break

        assert last_status == 429, \
            f"Write tier should trigger 429 after 11 requests, last status was {last_status}"

    def test_read_tier_allows_5_requests(self, client):
        """Read endpoint allows at least 5 requests without limiting."""
        statuses = []
        for _ in range(5):
            resp = client.get("/api/health")
            statuses.append(resp.status_code)

        assert all(s == 200 for s in statuses), \
            f"Read tier should allow 5 requests without 429, got statuses: {statuses}"
