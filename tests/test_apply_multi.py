# tests/test_apply_multi.py
import os
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
        assert "outside" in errors[0]["error"].lower() or "not found" in errors[0]["error"].lower()

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

    def test_empty_path(self, tmp_path):
        fixes = [{"compose_file_path": "", "corrected_yaml": "services:\n  x:\n    image: y\n"}]
        errors = validate_fixes_batch(fixes, stacks_root=str(tmp_path))
        assert len(errors) == 1

    def test_empty_corrected_yaml(self, tmp_path):
        f1 = tmp_path / "empty" / "docker-compose.yml"
        f1.parent.mkdir()
        f1.write_text("services:\n  x:\n    image: test\n")
        fixes = [{"compose_file_path": str(f1), "corrected_yaml": ""}]
        errors = validate_fixes_batch(fixes, stacks_root=str(tmp_path))
        assert len(errors) == 1


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


class TestPathBoundaryValidation:
    """Focused tests for path boundary checking in validate_fixes_batch."""

    def test_path_outside_root_rejected(self, tmp_path):
        """Path that resolves outside stacks_root must be rejected."""
        outside = tmp_path / ".." / "outside" / "docker-compose.yml"
        # The file won't exist, so it may fail on 'not found' before boundary check.
        # Create the file outside root to isolate the boundary check.
        outside_dir = (tmp_path / ".." / "outside").resolve()
        outside_dir.mkdir(parents=True, exist_ok=True)
        outside_file = outside_dir / "docker-compose.yml"
        outside_file.write_text("services:\n  x:\n    image: test\n")

        fixes = [{"compose_file_path": str(outside_file), "corrected_yaml": "services:\n  x:\n    image: y\n"}]
        errors = validate_fixes_batch(fixes, stacks_root=str(tmp_path))
        assert len(errors) == 1, "Path outside stacks root must produce exactly one error"
        assert "outside" in errors[0]["error"].lower(), (
            f"Error message must mention 'outside', got: {errors[0]['error']}"
        )

    def test_path_within_root_accepted(self, tmp_path):
        """Path that resolves inside stacks_root must pass boundary check."""
        f1 = tmp_path / "mystack" / "docker-compose.yml"
        f1.parent.mkdir()
        f1.write_text("services:\n  x:\n    image: old\n")

        fixes = [{"compose_file_path": str(f1), "corrected_yaml": "services:\n  x:\n    image: new\n"}]
        errors = validate_fixes_batch(fixes, stacks_root=str(tmp_path))
        assert errors == [], f"Valid path within root should produce no errors, got: {errors}"

    def test_empty_path_rejected(self, tmp_path):
        """Empty string path must be rejected."""
        fixes = [{"compose_file_path": "", "corrected_yaml": "services:\n  x:\n    image: y\n"}]
        errors = validate_fixes_batch(fixes, stacks_root=str(tmp_path))
        assert len(errors) == 1, "Empty path must produce exactly one error"
        assert "empty" in errors[0]["error"].lower(), (
            f"Error message must mention 'empty', got: {errors[0]['error']}"
        )

    def test_whitespace_only_path_rejected(self, tmp_path):
        """Whitespace-only path must be rejected as empty."""
        fixes = [{"compose_file_path": "   ", "corrected_yaml": "services:\n  x:\n    image: y\n"}]
        errors = validate_fixes_batch(fixes, stacks_root=str(tmp_path))
        assert len(errors) == 1, "Whitespace-only path must produce exactly one error"
