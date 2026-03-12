"""Tests for pipeline scan error handling — malformed YAML and empty compose files."""
import pytest
from backend.pipeline import run_pipeline_scan, _list_service_names


class TestListServiceNames:
    def test_malformed_yaml_returns_empty_list(self, tmp_path):
        bad_file = tmp_path / "docker-compose.yml"
        bad_file.write_text("services:\n  sonarr\n    image: bad yaml here")
        result = _list_service_names(str(bad_file))
        assert result == [], "Malformed YAML should return empty list"

    def test_empty_file_returns_empty_list(self, tmp_path):
        empty = tmp_path / "docker-compose.yml"
        empty.write_text("")
        result = _list_service_names(str(empty))
        assert result == [], "Empty file should return empty list"

    def test_valid_yaml_returns_service_names(self, tmp_path):
        good = tmp_path / "docker-compose.yml"
        good.write_text("services:\n  sonarr:\n    image: sonarr\n  radarr:\n    image: radarr\n")
        result = _list_service_names(str(good))
        assert set(result) == {"sonarr", "radarr"}, "Should return both service names"


class TestPipelineScanParseErrors:
    def test_malformed_yaml_reported_in_result(self, tmp_path):
        stack_dir = tmp_path / "bad-stack"
        stack_dir.mkdir()
        (stack_dir / "docker-compose.yml").write_text(
            "services:\n  sonarr\n    image: bad"
        )
        result = run_pipeline_scan(str(tmp_path))
        d = result.to_dict()
        assert "parse_errors" in d, "Pipeline result must include parse_errors field"
        assert len(d["parse_errors"]) >= 1, "Should report at least 1 parse error"
        assert "YAML" in d["parse_errors"][0]["error"] or "syntax" in d["parse_errors"][0]["error"].lower(), \
            "Error should mention YAML syntax issue"

    def test_empty_compose_completes_scan(self, tmp_path):
        stack_dir = tmp_path / "empty-stack"
        stack_dir.mkdir()
        (stack_dir / "docker-compose.yml").write_text("services: {}")
        result = run_pipeline_scan(str(tmp_path))
        assert result is not None, "Pipeline scan should complete with empty compose"

    def test_mixed_good_and_bad_stacks(self, tmp_path):
        good = tmp_path / "good"
        good.mkdir()
        (good / "docker-compose.yml").write_text(
            "services:\n  sonarr:\n    image: lscr.io/linuxserver/sonarr\n"
            "    environment:\n      - PUID=1000\n      - PGID=1000\n"
            "    volumes:\n      - /srv/data:/data\n"
        )
        bad = tmp_path / "bad"
        bad.mkdir()
        (bad / "docker-compose.yml").write_text("invalid: yaml: here: [")
        result = run_pipeline_scan(str(tmp_path))
        media = result.to_dict().get("media_services", [])
        assert any(s["service_name"] == "sonarr" for s in media), \
            "Good stacks should be discovered even when bad stacks exist"
        assert len(result.to_dict().get("parse_errors", [])) >= 1, \
            "Bad stack should appear in parse_errors"
