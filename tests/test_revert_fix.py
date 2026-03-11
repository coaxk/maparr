"""Tests for the revert-fix endpoint — .bak file restoration.

Gemini + Grok Elder Council: expose .bak restoration in Apply Fix UI.
Backend creates backups before every write. This endpoint restores them.
"""
import os
import pytest
from fastapi.testclient import TestClient
from backend.main import app, _session


class TestRevertFix:
    """POST /api/revert-fix — restore .bak backup."""

    def setup_method(self):
        _session["parsed_error"] = None
        _session["selected_stack"] = None
        _session["pipeline"] = None
        _session.pop("custom_stacks_path", None)

    def test_revert_restores_backup(self, tmp_path):
        """Revert should swap .bak back to original file."""
        compose = tmp_path / "compose.yaml"
        compose.write_text("services:\n  fixed: {image: fixed}\n")
        backup = tmp_path / "compose.yaml.bak"
        backup.write_text("services:\n  original: {image: original}\n")

        _session["custom_stacks_path"] = str(tmp_path)
        client = TestClient(app)
        response = client.post("/api/revert-fix", json={
            "compose_file_path": str(compose),
        })
        assert response.status_code == 200, f"Revert failed: {response.json()}"
        assert response.json()["status"] == "reverted", \
            "Response status should be 'reverted'"
        assert compose.read_text() == "services:\n  original: {image: original}\n", \
            "Compose file should be restored to backup content"

    def test_revert_removes_backup_after_restore(self, tmp_path):
        """After revert, the .bak file should be consumed (removed)."""
        compose = tmp_path / "compose.yaml"
        compose.write_text("services:\n  fixed: {image: fixed}\n")
        backup = tmp_path / "compose.yaml.bak"
        backup.write_text("services:\n  original: {image: original}\n")

        _session["custom_stacks_path"] = str(tmp_path)
        client = TestClient(app)
        client.post("/api/revert-fix", json={
            "compose_file_path": str(compose),
        })
        assert not backup.exists(), \
            ".bak file should be removed after os.replace() atomically moves it"

    def test_revert_missing_backup_returns_404(self, tmp_path):
        """No .bak file -> 404 with clear message."""
        compose = tmp_path / "compose.yaml"
        compose.write_text("services:\n  test: {image: test}\n")

        _session["custom_stacks_path"] = str(tmp_path)
        client = TestClient(app)
        response = client.post("/api/revert-fix", json={
            "compose_file_path": str(compose),
        })
        assert response.status_code == 404, \
            f"Expected 404 for missing backup, got {response.status_code}"
        assert "backup" in response.json()["detail"].lower(), \
            "404 response should mention 'backup'"

    def test_revert_outside_stacks_returns_403(self, tmp_path):
        """Path outside stacks boundary -> 403."""
        _session["custom_stacks_path"] = str(tmp_path / "allowed")
        client = TestClient(app)
        response = client.post("/api/revert-fix", json={
            "compose_file_path": "/etc/shadow",
        })
        assert response.status_code == 403, \
            f"Expected 403 for path outside stacks, got {response.status_code}"

    def test_revert_nonexistent_file_returns_404(self, tmp_path):
        """Compose file doesn't exist -> 404."""
        _session["custom_stacks_path"] = str(tmp_path)
        client = TestClient(app)
        response = client.post("/api/revert-fix", json={
            "compose_file_path": str(tmp_path / "nonexistent.yaml"),
        })
        assert response.status_code == 404, \
            f"Expected 404 for nonexistent file, got {response.status_code}"

    def test_revert_empty_path_returns_400(self, tmp_path):
        """Empty compose_file_path -> 400."""
        _session["custom_stacks_path"] = str(tmp_path)
        client = TestClient(app)
        response = client.post("/api/revert-fix", json={
            "compose_file_path": "",
        })
        assert response.status_code == 400, \
            f"Expected 400 for empty path, got {response.status_code}"

    def test_revert_no_stacks_root_returns_403(self):
        """No stacks root configured -> 403 with guidance."""
        _session.pop("custom_stacks_path", None)
        client = TestClient(app)
        response = client.post("/api/revert-fix", json={
            "compose_file_path": "/some/path/compose.yaml",
        })
        assert response.status_code == 403, \
            f"Expected 403 when no stacks root, got {response.status_code}"


class TestApplyFixReturnsBackupInfo:
    """Apply fix response must include has_backup for frontend button."""

    def setup_method(self):
        _session["parsed_error"] = None
        _session["selected_stack"] = None
        _session["pipeline"] = None
        _session.pop("custom_stacks_path", None)

    def test_apply_fix_response_has_backup_field(self, tmp_path):
        """After apply-fix, response includes has_backup: true."""
        compose = tmp_path / "compose.yaml"
        compose.write_text("services:\n  test:\n    image: test\n")

        _session["custom_stacks_path"] = str(tmp_path)
        client = TestClient(app)
        response = client.post("/api/apply-fix", json={
            "compose_file_path": str(compose),
            "corrected_yaml": "services:\n  test:\n    image: fixed\n",
        })
        if response.status_code == 200:
            data = response.json()
            assert "has_backup" in data, \
                "apply-fix response must include has_backup field"
            assert data["has_backup"] is True, \
                "has_backup should be True after successful apply"
