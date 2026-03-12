"""Tests for write boundary enforcement without MAPARR_STACKS_PATH.

Grok Elder Council finding (MEDIUM): bare-metal dev runs with no env var
allow apply-fix to write to any path. Must return 403 when no root is set.
"""
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient
from backend.main import app, _session, _is_path_within_stacks


class TestWriteBoundaryEnforcement:
    """Write endpoints must refuse when no stacks root is configured."""

    def setup_method(self):
        _session["parsed_error"] = None
        _session["selected_stack"] = None
        _session["pipeline"] = None
        _session.pop("custom_stacks_path", None)

    def test_is_path_within_stacks_no_root_denies_writes(self):
        """require_root=True must return False when no root is configured."""
        with patch.dict("os.environ", {}, clear=True):
            _session.pop("custom_stacks_path", None)
            assert _is_path_within_stacks("/any/path", require_root=True) is False, \
                "Write operations must be denied when no stacks root is configured"

    def test_is_path_within_stacks_no_root_allows_reads(self):
        """require_root=False should still work without a root (read operations)."""
        with patch.dict("os.environ", {}, clear=True):
            _session.pop("custom_stacks_path", None)
            result = _is_path_within_stacks("/any/path", require_root=False)
            assert isinstance(result, bool), \
                "Read operations without root should return a boolean, not crash"

    def test_apply_fix_403_without_stacks_path(self):
        """POST /api/apply-fix must return 403 when no stacks root."""
        client = TestClient(app)
        with patch.dict("os.environ", {}, clear=True):
            _session.pop("custom_stacks_path", None)
            response = client.post("/api/apply-fix", json={
                "compose_file_path": "/tmp/test/compose.yaml",
                "corrected_yaml": "services:\n  test:\n    image: test\n",
            })
            assert response.status_code == 403, \
                f"Expected 403 without MAPARR_STACKS_PATH, got {response.status_code}"
            body = response.json()
            error_msg = body.get("error", "") or body.get("detail", "")
            assert "MAPARR_STACKS_PATH" in error_msg, \
                "403 response should mention MAPARR_STACKS_PATH for user guidance"

    def test_apply_fixes_403_without_stacks_path(self):
        """POST /api/apply-fixes must return 403 when no stacks root."""
        client = TestClient(app)
        with patch.dict("os.environ", {}, clear=True):
            _session.pop("custom_stacks_path", None)
            response = client.post("/api/apply-fixes", json={
                "fixes": [{"compose_file_path": "/tmp/test/compose.yaml",
                           "corrected_yaml": "services:\n  test:\n    image: test\n"}],
            })
            assert response.status_code == 403, \
                f"Expected 403 without MAPARR_STACKS_PATH, got {response.status_code}"
            body = response.json()
            error_msg = body.get("error", "") or body.get("detail", "")
            assert "MAPARR_STACKS_PATH" in error_msg, \
                "403 response should mention MAPARR_STACKS_PATH for user guidance"

    def test_is_path_within_stacks_with_root_validates(self):
        """When a root IS set, paths outside it should be denied."""
        with patch.dict("os.environ", {"MAPARR_STACKS_PATH": "/opt/stacks"}, clear=True):
            _session.pop("custom_stacks_path", None)
            assert _is_path_within_stacks("/etc/passwd", require_root=True) is False, \
                "Paths outside the stacks root must be denied for writes"

    def test_is_path_within_stacks_with_custom_path(self):
        """custom_stacks_path in session should act as a valid root."""
        with patch.dict("os.environ", {}, clear=True):
            _session["custom_stacks_path"] = "/opt/stacks"
            # Path within root should be allowed
            assert _is_path_within_stacks("/opt/stacks/myapp/compose.yaml", require_root=True) is True, \
                "Paths within custom_stacks_path should be allowed for writes"
            _session.pop("custom_stacks_path", None)
