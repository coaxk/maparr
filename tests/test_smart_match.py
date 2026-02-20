"""
Tests for MapArr — Smart Match (Fix Mode Stack Selection).

Covers:
  - smart_match() — scoring and ranking of candidate stacks
  - Confidence levels (high, medium, low)
  - Signal scoring: dir name, completeness, path reachability, uniqueness, health
  - _get_service_volumes() — per-service volume extraction
  - Edge cases: no candidates, no service, no error path
"""

import os
import textwrap

import pytest

from conftest import SONARR_YAML, QBITTORRENT_YAML, HEALTHY_MULTI_YAML


# ═══════════════════════════════════════════
# Unit Tests: smart_match()
# ═══════════════════════════════════════════

class TestSmartMatch:
    """Intelligent stack-to-error matching."""

    def test_dir_name_match_high_confidence(self, make_stack):
        """Stack directory named after service → high confidence."""
        from backend.smart_match import smart_match

        stack_path = make_stack(SONARR_YAML, dirname="sonarr")
        candidates = [{
            "path": stack_path,
            "services": ["sonarr"],
            "service_count": 1,
            "volume_targets": ["/data"],
            "health": "unknown",
        }]
        parsed_error = {"service": "sonarr", "path": "/data/tv/show.mkv", "error_type": "import_failed"}

        result = smart_match(parsed_error, candidates)

        assert result["best"] is not None
        assert result["best"]["path"] == stack_path

    def test_multiple_candidates_ranking(self, make_stack, tmp_path):
        """Multiple candidates with different scores → correct ranking."""
        from backend.smart_match import smart_match

        # Create two stacks
        sonarr_dir = tmp_path / "sonarr"
        sonarr_dir.mkdir()
        (sonarr_dir / "docker-compose.yml").write_text(SONARR_YAML)

        other_dir = tmp_path / "mediastack"
        other_dir.mkdir()
        (other_dir / "docker-compose.yml").write_text(HEALTHY_MULTI_YAML)

        candidates = [
            {
                "path": str(sonarr_dir),
                "services": ["sonarr"],
                "service_count": 1,
                "volume_targets": ["/data"],
                "health": "unknown",
            },
            {
                "path": str(other_dir),
                "services": ["sonarr", "qbittorrent"],
                "service_count": 2,
                "volume_targets": ["/data"],
                "health": "unknown",
            },
        ]
        parsed_error = {"service": "sonarr", "path": "/data/tv/show.mkv", "error_type": "import_failed"}

        result = smart_match(parsed_error, candidates)

        # sonarr dir should win (dir name match = 100 pts)
        assert result["ranked"][0]["stack"]["path"] == str(sonarr_dir)
        assert result["ranked"][0]["score"] > result["ranked"][1]["score"]

    def test_no_candidates(self):
        from backend.smart_match import smart_match

        result = smart_match(
            {"service": "sonarr", "path": "/data/tv", "error_type": "import_failed"},
            [],
        )

        assert result["best"] is None
        assert result["confidence"] == "low"
        assert result["ranked"] == []

    def test_completeness_bonus(self, tmp_path):
        """Stack with both arr + download client gets completeness bonus over equally-named stacks."""
        from backend.smart_match import smart_match

        # Both stacks have neutral names (no service name match) to isolate completeness signal
        complete = tmp_path / "mediastack"
        complete.mkdir()
        (complete / "docker-compose.yml").write_text(HEALTHY_MULTI_YAML)

        solo = tmp_path / "arronly"
        solo.mkdir()
        (solo / "docker-compose.yml").write_text(SONARR_YAML)

        candidates = [
            {
                "path": str(complete),
                "services": ["sonarr", "qbittorrent"],
                "service_count": 2,
                "volume_targets": ["/data"],
                "health": "unknown",
            },
            {
                "path": str(solo),
                "services": ["sonarr"],
                "service_count": 1,
                "volume_targets": ["/data"],
                "health": "unknown",
            },
        ]
        parsed_error = {"service": "sonarr", "path": "/data/tv/show.mkv", "error_type": "import_failed"}

        result = smart_match(parsed_error, candidates)
        ranked_scores = {r["stack"]["path"]: r["score"] for r in result["ranked"]}

        # Complete stack should score higher (completeness + import error bonus)
        assert ranked_scores[str(complete)] > ranked_scores[str(solo)]

    def test_health_correlation(self, tmp_path):
        """Stack with known problems scores higher for error matching."""
        from backend.smart_match import smart_match

        candidates = [
            {
                "path": "/stacks/sonarr-healthy",
                "services": ["sonarr"],
                "service_count": 1,
                "volume_targets": ["/data"],
                "health": "healthy",
            },
            {
                "path": "/stacks/sonarr-broken",
                "services": ["sonarr"],
                "service_count": 1,
                "volume_targets": ["/data"],
                "health": "problem",
            },
        ]
        parsed_error = {"service": "sonarr", "path": "/data/tv/show.mkv", "error_type": "hardlink_failed"}

        result = smart_match(parsed_error, candidates)
        ranked_scores = {r["stack"]["path"]: r["score"] for r in result["ranked"]}

        assert ranked_scores["/stacks/sonarr-broken"] > ranked_scores["/stacks/sonarr-healthy"]

    def test_no_service_in_error(self):
        """Error with no service detected → still produces results."""
        from backend.smart_match import smart_match

        candidates = [{
            "path": "/stacks/sonarr",
            "services": ["sonarr"],
            "service_count": 1,
            "volume_targets": ["/data"],
            "health": "unknown",
        }]

        result = smart_match(
            {"service": None, "path": "/data/tv", "error_type": "import_failed"},
            candidates,
        )

        assert result["best"] is not None
        assert result["confidence"] in ("high", "medium", "low")

    def test_confidence_levels(self, tmp_path):
        """Verify confidence is determined by score + gap."""
        from backend.smart_match import smart_match

        # Single candidate with strong match → high confidence
        candidates = [{
            "path": str(tmp_path / "sonarr"),
            "services": ["sonarr"],
            "service_count": 1,
            "volume_targets": ["/data/tv"],
            "health": "problem",
        }]
        (tmp_path / "sonarr").mkdir(exist_ok=True)
        (tmp_path / "sonarr" / "docker-compose.yml").write_text(SONARR_YAML)

        result = smart_match(
            {"service": "sonarr", "path": "/data/tv/show.mkv", "error_type": "hardlink_failed"},
            candidates,
        )

        # With dir name match (100), health (15+20), path reachability, etc.
        assert result["confidence"] == "high"

    def test_result_shape(self):
        """Verify the return dict has all expected keys."""
        from backend.smart_match import smart_match

        result = smart_match(
            {"service": "sonarr", "path": "/data/tv", "error_type": "import_failed"},
            [{"path": "/stacks/sonarr", "services": ["sonarr"], "service_count": 1, "volume_targets": ["/data"], "health": "unknown"}],
        )

        assert "best" in result
        assert "confidence" in result
        assert "reason" in result
        assert "ranked" in result
        assert isinstance(result["ranked"], list)
        assert "score" in result["ranked"][0]
        assert "reasons" in result["ranked"][0]


# ═══════════════════════════════════════════
# Unit Tests: _get_service_volumes()
# ═══════════════════════════════════════════

class TestGetServiceVolumes:
    """Per-service volume extraction from compose file."""

    def test_extracts_data_volumes(self, make_stack):
        from backend.smart_match import _get_service_volumes

        stack_path = make_stack(SONARR_YAML, dirname="sonarr")
        volumes = _get_service_volumes(stack_path, "sonarr")

        assert volumes is not None
        assert len(volumes) > 0
        # Should have /data but not /config
        assert any("/data" in v for v in volumes)
        assert not any("/config" in v for v in volumes)

    def test_service_not_found(self, make_stack):
        from backend.smart_match import _get_service_volumes

        stack_path = make_stack(SONARR_YAML, dirname="sonarr")
        volumes = _get_service_volumes(stack_path, "nonexistent")

        assert volumes is None

    def test_no_compose_file(self, tmp_path):
        from backend.smart_match import _get_service_volumes

        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        volumes = _get_service_volumes(str(empty_dir), "sonarr")

        assert volumes is None

    def test_case_insensitive_service_match(self, make_stack):
        from backend.smart_match import _get_service_volumes

        stack_path = make_stack(SONARR_YAML, dirname="sonarr")
        volumes = _get_service_volumes(stack_path, "Sonarr")

        assert volumes is not None


# ═══════════════════════════════════════════
# API Tests: /api/smart-match
# ═══════════════════════════════════════════

class TestSmartMatchAPI:
    """Integration tests for the smart-match endpoint."""

    def test_smart_match_endpoint(self, client, make_pipeline_dir):
        """API expects candidate_paths (not candidate_stacks) — it resolves them via discovery."""
        root = make_pipeline_dir({
            "sonarr": SONARR_YAML,
            "qbittorrent": QBITTORRENT_YAML,
        })

        # Set the custom stacks path so discovery finds our test stacks
        client.post("/api/change-stacks-path", json={"path": root})

        # API expects parsed_error + candidate_paths (list of path strings)
        sonarr_path = os.path.join(root, "sonarr")
        resp = client.post("/api/smart-match", json={
            "parsed_error": {
                "service": "sonarr",
                "path": "/data/tv/show.mkv",
                "error_type": "import_failed",
            },
            "candidate_paths": [sonarr_path],
        })

        assert resp.status_code == 200
        data = resp.json()
        assert "best" in data
        assert "confidence" in data
        assert "ranked" in data
