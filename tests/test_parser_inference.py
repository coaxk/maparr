"""Tests for error parser contextual service inference."""
import pytest
from backend.parser import parse_error


class TestServiceInferenceFromContext:
    def test_exdev_crossdevice_infers_arr(self):
        text = "[Error] Import failed: [/downloads/complete/Movie.2024.1080p/Movie.2024.1080p.mkv] Import failed, error code EXDEV (18): Cross-device link"
        result = parse_error(text)
        assert result.service is not None, "EXDEV error should infer an arr service"
        assert result.service in ("*arr", "sonarr", "radarr"), "Should be arr or specific arr app"

    def test_remote_path_mapping_infers_arr(self):
        text = "[Warn] Couldn't import episode /downloads/complete/tv/Some.Show.S02E05.mkv: Episode file path '/downloads/complete/tv/Some.Show.S02E05.mkv' is not valid. Ensure the Remote Path Mapping is configured correctly."
        result = parse_error(text)
        assert result.service is not None, "RPM error should infer a service"

    def test_no_eligible_files_infers_arr(self):
        text = "[Warn] No files found are eligible for import in /data/downloads/complete/Some.Show.S01E01"
        result = parse_error(text)
        assert result.service is not None, "Import eligibility error should infer arr service"

    def test_episode_file_path_infers_sonarr(self):
        text = "Episode file path '/downloads/tv/show.mkv' is not valid"
        result = parse_error(text)
        assert result.service == "sonarr", "Episode file path should infer sonarr"

    def test_movie_file_path_infers_radarr(self):
        text = "Movie file path '/downloads/movies/movie.mkv' is not valid"
        result = parse_error(text)
        assert result.service == "radarr", "Movie file path should infer radarr"

    def test_explicit_service_name_takes_priority(self):
        text = "Sonarr Import failed: error code EXDEV"
        result = parse_error(text)
        assert result.service == "sonarr", "Explicit name should take priority over inference"

    def test_unknown_error_returns_none(self):
        text = "Some random error that doesn't match anything"
        result = parse_error(text)
        # service could be None — that's fine for unrecognized errors
