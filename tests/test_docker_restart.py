"""Tests for Docker stack restart and capabilities endpoints.

Gemini Elder Council: direct stack restart via Docker socket after Apply Fix.
"""
import os
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from backend.main import app, _session


class TestDockerCapabilities:
    """GET /api/docker-capabilities — report Docker availability."""

    def test_capabilities_returns_fields(self):
        """Response must include socket_available, socket_writable, compose_available."""
        client = TestClient(app)
        response = client.get("/api/docker-capabilities")
        assert response.status_code == 200
        data = response.json()
        assert "socket_available" in data, "Must report socket_available"
        assert "socket_writable" in data, "Must report socket_writable"
        assert "compose_available" in data, "Must report compose_available"
        for key in ["socket_available", "socket_writable", "compose_available"]:
            assert isinstance(data[key], bool), f"{key} must be boolean"

    @patch("shutil.which", return_value="/usr/bin/docker")
    @patch("os.access", return_value=True)
    @patch("os.path.exists", return_value=True)
    def test_capabilities_all_available(self, mock_exists, mock_access, mock_which):
        """When Docker socket exists and is writable, all capabilities are True."""
        client = TestClient(app)
        response = client.get("/api/docker-capabilities")
        data = response.json()
        assert data["socket_available"] is True
        assert data["socket_writable"] is True
        assert data["compose_available"] is True

    @patch("shutil.which", return_value=None)
    @patch("os.path.exists", return_value=False)
    def test_capabilities_nothing_available(self, mock_exists, mock_which):
        """When Docker is not installed, all capabilities are False."""
        client = TestClient(app)
        response = client.get("/api/docker-capabilities")
        data = response.json()
        assert data["socket_available"] is False
        assert data["socket_writable"] is False
        assert data["compose_available"] is False


class TestRestartStack:
    """POST /api/restart-stack — restart Docker stack."""

    def setup_method(self):
        _session.pop("custom_stacks_path", None)

    def test_restart_outside_stacks_returns_403(self, tmp_path):
        """Path outside stacks boundary -> 403."""
        _session["custom_stacks_path"] = str(tmp_path / "allowed")
        client = TestClient(app)
        response = client.post("/api/restart-stack", json={
            "compose_file_path": "/etc/evil/compose.yaml",
        })
        assert response.status_code == 403, \
            f"Expected 403 for path outside stacks, got {response.status_code}"

    def test_restart_missing_file_returns_404(self, tmp_path):
        """Non-existent compose file -> 404."""
        _session["custom_stacks_path"] = str(tmp_path)
        client = TestClient(app)
        response = client.post("/api/restart-stack", json={
            "compose_file_path": str(tmp_path / "nonexistent.yaml"),
        })
        assert response.status_code == 404, \
            f"Expected 404 for missing file, got {response.status_code}"

    def test_restart_empty_path_returns_400(self):
        """Empty path -> 400."""
        client = TestClient(app)
        response = client.post("/api/restart-stack", json={
            "compose_file_path": "",
        })
        assert response.status_code == 400, \
            f"Expected 400 for empty path, got {response.status_code}"

    def test_restart_success(self, tmp_path):
        """Successful restart returns status=restarted."""
        _session["custom_stacks_path"] = str(tmp_path)
        compose_file = tmp_path / "docker-compose.yml"
        compose_file.write_text("version: '3'\nservices:\n  test:\n    image: alpine\n")

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Container test started"
        mock_result.stderr = ""

        client = TestClient(app)
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            response = client.post("/api/restart-stack", json={
                "compose_file_path": str(compose_file),
            })
            assert response.status_code == 200, \
                f"Expected 200 for successful restart, got {response.status_code}"
            data = response.json()
            assert data["status"] == "restarted", "Status should be 'restarted'"
            assert data["compose_file"] == str(compose_file)

            # Verify subprocess was called with correct args
            mock_run.assert_called_once()
            call_args = mock_run.call_args
            assert call_args[0][0] == ["docker", "compose", "-f", str(compose_file), "up", "-d"]
            assert call_args[1]["cwd"] == str(tmp_path)
            assert call_args[1]["timeout"] == 60

    def test_restart_docker_failure_returns_500(self, tmp_path):
        """Docker compose failure returns 500 with stderr."""
        _session["custom_stacks_path"] = str(tmp_path)
        compose_file = tmp_path / "docker-compose.yml"
        compose_file.write_text("version: '3'\n")

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "Error: service 'test' failed to start"

        client = TestClient(app)
        with patch("subprocess.run", return_value=mock_result):
            response = client.post("/api/restart-stack", json={
                "compose_file_path": str(compose_file),
            })
            assert response.status_code == 500
            data = response.json()
            assert "stderr" in data, "Error response must include stderr"

    def test_restart_timeout_returns_504(self, tmp_path):
        """Docker compose timeout returns 504."""
        import subprocess
        _session["custom_stacks_path"] = str(tmp_path)
        compose_file = tmp_path / "docker-compose.yml"
        compose_file.write_text("version: '3'\n")

        client = TestClient(app)
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="docker", timeout=60)):
            response = client.post("/api/restart-stack", json={
                "compose_file_path": str(compose_file),
            })
            assert response.status_code == 504, \
                f"Expected 504 for timeout, got {response.status_code}"

    def test_restart_docker_not_found_returns_500(self, tmp_path):
        """Missing Docker CLI returns 500 with helpful message."""
        _session["custom_stacks_path"] = str(tmp_path)
        compose_file = tmp_path / "docker-compose.yml"
        compose_file.write_text("version: '3'\n")

        client = TestClient(app)
        with patch("subprocess.run", side_effect=FileNotFoundError("docker not found")):
            response = client.post("/api/restart-stack", json={
                "compose_file_path": str(compose_file),
            })
            assert response.status_code == 500
            data = response.json()
            assert "Docker CLI" in data["detail"], "Error should mention Docker CLI"

    def test_restart_no_stacks_root_returns_403(self):
        """No stacks root configured -> 403 (require_root=True)."""
        # Ensure no stacks root is set
        _session.pop("custom_stacks_path", None)
        old_env = os.environ.pop("MAPARR_STACKS_PATH", None)
        try:
            client = TestClient(app)
            response = client.post("/api/restart-stack", json={
                "compose_file_path": "/some/path/compose.yaml",
            })
            assert response.status_code == 403, \
                f"Expected 403 when no stacks root configured, got {response.status_code}"
        finally:
            if old_env is not None:
                os.environ["MAPARR_STACKS_PATH"] = old_env
