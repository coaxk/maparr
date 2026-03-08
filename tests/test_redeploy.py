# tests/test_redeploy.py
import pytest
from unittest.mock import patch, MagicMock
from backend.redeploy import run_compose_action, validate_for_redeploy, find_compose_file, redeploy_stacks


class TestFindComposeFile:
    def test_finds_docker_compose_yml(self, tmp_path):
        f = tmp_path / "docker-compose.yml"
        f.write_text("services:\n  x:\n    image: test\n")
        assert find_compose_file(str(tmp_path)) == str(f)

    def test_finds_compose_yaml(self, tmp_path):
        f = tmp_path / "compose.yaml"
        f.write_text("services:\n  x:\n    image: test\n")
        assert find_compose_file(str(tmp_path)) == str(f)

    def test_returns_none_when_missing(self, tmp_path):
        assert find_compose_file(str(tmp_path)) is None

    def test_prefers_docker_compose_yml(self, tmp_path):
        """docker-compose.yml takes priority over compose.yml."""
        f1 = tmp_path / "docker-compose.yml"
        f1.write_text("services:\n  x:\n    image: test\n")
        f2 = tmp_path / "compose.yml"
        f2.write_text("services:\n  y:\n    image: test\n")
        result = find_compose_file(str(tmp_path))
        assert result == str(f1)


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

    def test_nonexistent_directory(self, tmp_path):
        errors = validate_for_redeploy(str(tmp_path / "nope"), stacks_root=str(tmp_path))
        assert len(errors) == 1


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
    def test_pull_success(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(returncode=0, stdout="Pulling...", stderr="")
        compose_file = str(tmp_path / "docker-compose.yml")
        result = run_compose_action(str(tmp_path), compose_file, "pull")
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

    @patch("backend.redeploy.subprocess.run")
    def test_docker_not_found(self, mock_run, tmp_path):
        mock_run.side_effect = FileNotFoundError()
        compose_file = str(tmp_path / "docker-compose.yml")
        result = run_compose_action(str(tmp_path), compose_file, "up")
        assert result["status"] == "error"
        assert "not found" in result["error"].lower()

    def test_invalid_action(self, tmp_path):
        with pytest.raises(ValueError, match="Unknown action"):
            run_compose_action(str(tmp_path), "fake.yml", "destroy")


class TestRedeployStacks:
    @patch("backend.redeploy.subprocess.run")
    def test_single_stack_success(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(returncode=0, stdout="OK", stderr="")
        d = tmp_path / "sonarr"
        d.mkdir()
        (d / "docker-compose.yml").write_text("services:\n  sonarr:\n    image: test\n")

        result = redeploy_stacks(
            [{"stack_path": str(d), "action": "up"}],
            stacks_root=str(tmp_path)
        )
        assert result["status"] == "success"
        assert len(result["results"]) == 1

    @patch("backend.redeploy.subprocess.run")
    def test_mixed_results(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(returncode=0, stdout="OK", stderr="")
        d = tmp_path / "sonarr"
        d.mkdir()
        (d / "docker-compose.yml").write_text("services:\n  sonarr:\n    image: test\n")

        result = redeploy_stacks(
            [
                {"stack_path": str(d), "action": "up"},
                {"stack_path": str(tmp_path / "missing"), "action": "up"},
            ],
            stacks_root=str(tmp_path)
        )
        assert result["status"] == "partial"

    def test_empty_stacks(self, tmp_path):
        result = redeploy_stacks([], stacks_root=str(tmp_path))
        assert result["status"] == "success"
