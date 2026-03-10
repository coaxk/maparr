# tests/test_multi_file_fix.py
# [2026-03-10] Multi-file apply fix tests

import pytest
from backend.analyzer import AnalysisResult, ServiceInfo, Conflict


class TestFixPlansField:
    def test_fix_plans_default_empty(self):
        """AnalysisResult has fix_plans field, defaults to empty list."""
        result = AnalysisResult(
            stack_path="/stacks/sonarr",
            compose_file="/stacks/sonarr/docker-compose.yml",
            resolution_method="manual",
            services=[],
            conflicts=[],
        )
        assert result.fix_plans == []

    def test_fix_plans_in_to_dict(self):
        """fix_plans appears in to_dict() output."""
        result = AnalysisResult(
            stack_path="/stacks/sonarr",
            compose_file="/stacks/sonarr/docker-compose.yml",
            resolution_method="manual",
            services=[],
            conflicts=[],
            fix_plans=[{
                "compose_file_path": "/stacks/sonarr/docker-compose.yml",
                "corrected_yaml": "services:\n  sonarr:\n    image: fixed\n",
                "changed_services": ["sonarr"],
                "change_summary": "Fix volume mounts for sonarr",
                "category": "A",
            }],
        )
        d = result.to_dict()
        assert "fix_plans" in d
        assert len(d["fix_plans"]) == 1
        assert d["fix_plans"][0]["compose_file_path"] == "/stacks/sonarr/docker-compose.yml"
        assert d["fix_plans"][0]["changed_services"] == ["sonarr"]
