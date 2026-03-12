"""Tests for structured analysis error messages.

DeepSeek + Gemini + Grok: replace generic 'check log panel' with
type-specific actionable messages.
"""
import yaml
import pytest
from backend.main import _categorize_analysis_error


class TestCategorizeAnalysisError:
    """Map exceptions to structured error responses."""

    def test_yaml_error(self):
        """YAML parse errors include type and line info."""
        try:
            yaml.safe_load("invalid: yaml: content: [")
        except yaml.YAMLError as exc:
            result = _categorize_analysis_error(exc, "/path/to/compose.yaml")
        assert result["type"] == "yaml_parse", \
            f"YAML errors should be type 'yaml_parse', got '{result['type']}'"
        assert "line" in result or "message" in result, \
            "YAML error should include line info or message"

    def test_file_not_found(self):
        """FileNotFoundError maps to file_missing type."""
        exc = FileNotFoundError("No such file: /path/to/compose.yaml")
        result = _categorize_analysis_error(exc, "/path/to/compose.yaml")
        assert result["type"] == "file_missing", \
            f"FileNotFoundError should be type 'file_missing', got '{result['type']}'"
        assert "message" in result, \
            "file_missing error should include a message"

    def test_permission_denied(self):
        """PermissionError maps to permission_denied type."""
        exc = PermissionError("Permission denied: /path/to/compose.yaml")
        result = _categorize_analysis_error(exc, "/path/to/compose.yaml")
        assert result["type"] == "permission_denied", \
            f"PermissionError should be type 'permission_denied', got '{result['type']}'"
        assert "message" in result, \
            "permission_denied error should include a message"

    def test_timeout_error(self):
        """TimeoutError or subprocess timeout maps to docker_unreachable."""
        exc = TimeoutError("Connection timed out")
        result = _categorize_analysis_error(exc, "/path/to/compose.yaml")
        assert result["type"] == "docker_unreachable", \
            f"TimeoutError should be type 'docker_unreachable', got '{result['type']}'"
        assert "hint" in result, \
            "docker_unreachable error should include a hint"

    def test_unknown_error_type(self):
        """Unknown exceptions get generic 'unknown' type."""
        exc = RuntimeError("Something unexpected")
        result = _categorize_analysis_error(exc, "/path/to/compose.yaml")
        assert result["type"] == "unknown", \
            f"Unknown errors should be type 'unknown', got '{result['type']}'"
        assert "message" in result, \
            "Unknown error should include a message"

    def test_no_services_error(self):
        """ValueError with 'no services' maps to no_services type."""
        exc = ValueError("No services found in compose file")
        result = _categorize_analysis_error(exc, "/path/to/compose.yaml")
        assert result["type"] == "no_services", \
            f"'No services' ValueError should be type 'no_services', got '{result['type']}'"

    def test_error_never_leaks_exception_string(self):
        """Error responses must never include raw str(e) — security standing order."""
        exc = PermissionError("/etc/shadow: permission denied for user root")
        result = _categorize_analysis_error(exc, "/etc/shadow")
        # Must NOT contain the raw exception string
        result_str = str(result)
        assert "/etc/shadow" not in result_str or "path" in result, \
            "Error response should use structured fields, not raw exception strings"
