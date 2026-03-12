"""Tests for diagnostic export zip generation.

Grok Elder Council: export zip for GitHub issue attachments.
"""
import io
import zipfile
import pytest
from fastapi.testclient import TestClient
from backend.main import app, _session


class TestExportDiagnostics:
    """GET /api/export-diagnostics — zip download."""

    def setup_method(self):
        _session["parsed_error"] = None
        _session["selected_stack"] = None
        _session["pipeline"] = None

    def test_export_returns_zip(self):
        """Response must be a valid zip file."""
        client = TestClient(app)
        response = client.get("/api/export-diagnostics")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        assert "application/zip" in response.headers.get("content-type", ""), \
            "Content-Type must be application/zip"
        # Verify it's a valid zip
        zf = zipfile.ZipFile(io.BytesIO(response.content))
        assert len(zf.namelist()) > 0, "Zip must contain at least one file"

    def test_export_contains_info_file(self):
        """Zip must contain a system info file."""
        client = TestClient(app)
        response = client.get("/api/export-diagnostics")
        zf = zipfile.ZipFile(io.BytesIO(response.content))
        names = zf.namelist()
        assert any("maparr" in n.lower() for n in names), \
            f"Zip should contain a maparr info file, got: {names}"

    def test_export_info_contains_version(self):
        """Info file must contain version and platform information."""
        client = TestClient(app)
        response = client.get("/api/export-diagnostics")
        zf = zipfile.ZipFile(io.BytesIO(response.content))
        info_content = zf.read("maparr-info.txt").decode("utf-8")
        assert "Version:" in info_content, "Info file must contain version"
        assert "Platform:" in info_content, "Info file must contain platform"
        assert "Python:" in info_content, "Info file must contain Python version"

    def test_export_with_pipeline_data(self):
        """When pipeline data exists, zip must contain pipeline summary."""
        _session["pipeline"] = {
            "total_stacks": 5,
            "media_stack_count": 3,
            "health_tier": "good",
            "health_message": "Looking healthy",
        }
        client = TestClient(app)
        response = client.get("/api/export-diagnostics")
        zf = zipfile.ZipFile(io.BytesIO(response.content))
        names = zf.namelist()
        assert "pipeline-summary.json" in names, \
            f"Zip should contain pipeline-summary.json when pipeline data exists, got: {names}"
        import json
        summary = json.loads(zf.read("pipeline-summary.json").decode("utf-8"))
        assert summary["total_stacks"] == 5, "Pipeline summary must preserve total_stacks"
        assert summary["health_tier"] == "good", "Pipeline summary must preserve health_tier"

    def test_export_without_pipeline_data(self):
        """When no pipeline data exists, zip should still be valid."""
        _session["pipeline"] = None
        client = TestClient(app)
        response = client.get("/api/export-diagnostics")
        assert response.status_code == 200
        zf = zipfile.ZipFile(io.BytesIO(response.content))
        names = zf.namelist()
        assert "pipeline-summary.json" not in names, \
            "Zip should not contain pipeline-summary.json when no pipeline data"

    def test_export_redacts_secrets(self, tmp_path):
        """Environment values matching secret patterns must be redacted."""
        # Create a compose file with secrets
        compose = tmp_path / "compose.yaml"
        compose.write_text(
            "services:\n"
            "  test:\n"
            "    image: test\n"
            "    environment:\n"
            "      API_KEY: super-secret-123\n"
            "      NORMAL_VAR: hello\n"
            "      DB_PASSWORD: my-password-456\n"
            "      AUTH_TOKEN: tok-789\n"
        )
        _session["custom_stacks_path"] = str(tmp_path)

        client = TestClient(app)
        response = client.get("/api/export-diagnostics")
        zf = zipfile.ZipFile(io.BytesIO(response.content))

        # Check all files in zip for the secret values
        for name in zf.namelist():
            content = zf.read(name).decode("utf-8", errors="replace")
            assert "super-secret-123" not in content, \
                f"API_KEY secret value found in {name} — must be redacted"
            assert "my-password-456" not in content, \
                f"DB_PASSWORD secret value found in {name} — must be redacted"
            assert "tok-789" not in content, \
                f"AUTH_TOKEN secret value found in {name} — must be redacted"

        # Verify non-secret values are preserved
        found_normal = False
        for name in zf.namelist():
            content = zf.read(name).decode("utf-8", errors="replace")
            if "hello" in content:
                found_normal = True
                break
        assert found_normal, "Non-secret NORMAL_VAR value 'hello' should be preserved"

        # Clean up session
        _session["custom_stacks_path"] = None

    def test_export_redacts_key_equals_format(self, tmp_path):
        """Secrets in KEY=value list format must also be redacted."""
        compose = tmp_path / "docker-compose.yml"
        compose.write_text(
            "services:\n"
            "  app:\n"
            "    image: app\n"
            "    environment:\n"
            "      - SECRET_KEY=my-big-secret\n"
            "      - NORMAL=visible\n"
        )
        _session["custom_stacks_path"] = str(tmp_path)

        client = TestClient(app)
        response = client.get("/api/export-diagnostics")
        zf = zipfile.ZipFile(io.BytesIO(response.content))

        for name in zf.namelist():
            content = zf.read(name).decode("utf-8", errors="replace")
            assert "my-big-secret" not in content, \
                f"SECRET_KEY value found in {name} — must be redacted"

        _session["custom_stacks_path"] = None

    def test_export_content_disposition(self):
        """Response must have Content-Disposition for download."""
        client = TestClient(app)
        response = client.get("/api/export-diagnostics")
        cd = response.headers.get("content-disposition", "")
        assert "maparr-diagnostic.zip" in cd, \
            f"Content-Disposition must suggest maparr-diagnostic.zip, got: {cd}"
