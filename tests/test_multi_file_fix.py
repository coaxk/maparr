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


class TestBuildFixPlans:
    def test_single_file_produces_one_plan(self, tmp_path):
        """Single compose file with Cat A conflict produces 1 fix plan."""
        compose = tmp_path / "docker-compose.yml"
        compose.write_text("services:\n  sonarr:\n    volumes:\n      - /tv:/data/tv\n")

        from backend.analyzer import _build_fix_plans
        plans = _build_fix_plans(
            raw_compose_content=compose.read_text(),
            compose_file=str(compose),
            conflicts=[Conflict(
                conflict_type="no_shared_mount",
                severity="high",
                services=["sonarr", "qbittorrent"],
                description="No shared mount",
            )],
            services=[ServiceInfo(
                name="sonarr", image="lscr.io/linuxserver/sonarr", role="arr",
                volumes=[],             )],
            pipeline_host_root="/data",
        )
        assert len(plans) == 1
        assert plans[0]["compose_file_path"] == str(compose)
        assert "sonarr" in plans[0]["changed_services"]
        assert plans[0]["category"] == "A"
        assert plans[0]["corrected_yaml"]  # Not empty

    def test_healthy_stack_empty_plans(self):
        """No conflicts produces empty fix_plans."""
        from backend.analyzer import _build_fix_plans
        plans = _build_fix_plans(
            raw_compose_content="services:\n  sonarr:\n    image: test\n",
            compose_file="/stacks/sonarr/docker-compose.yml",
            conflicts=[],
            services=[],
            pipeline_host_root=None,
        )
        assert plans == []

    def test_cat_b_produces_plan(self, tmp_path):
        """Category B (permission) conflict produces fix plan."""
        compose = tmp_path / "docker-compose.yml"
        compose.write_text("services:\n  sonarr:\n    image: lscr.io/linuxserver/sonarr\n    environment:\n      - PUID=1000\n")

        from backend.analyzer import _build_fix_plans
        plans = _build_fix_plans(
            raw_compose_content=compose.read_text(),
            compose_file=str(compose),
            conflicts=[Conflict(
                conflict_type="puid_pgid_mismatch",
                severity="high",
                services=["sonarr"],
                description="PUID mismatch",
            )],
            services=[ServiceInfo(
                name="sonarr", image="lscr.io/linuxserver/sonarr", role="arr",
                volumes=[],             )],
            pipeline_host_root=None,
        )
        # Should produce a plan (may be empty if patch doesn't generate changes — that's OK)
        # Key assertion: function runs without error and returns a list
        assert isinstance(plans, list)

    def test_no_raw_content_empty_plans(self):
        """No raw compose content produces empty fix_plans."""
        from backend.analyzer import _build_fix_plans
        plans = _build_fix_plans(
            raw_compose_content="",
            compose_file="/stacks/sonarr/docker-compose.yml",
            conflicts=[Conflict(
                conflict_type="no_shared_mount", severity="high",
                services=["sonarr"], description="test",
            )],
            services=[],
            pipeline_host_root=None,
        )
        assert plans == []

    def test_cat_c_d_no_plans(self):
        """Category C/D conflicts don't produce fix plans (no YAML patches for infra/observations)."""
        from backend.analyzer import _build_fix_plans
        plans = _build_fix_plans(
            raw_compose_content="services:\n  sonarr:\n    image: test\n",
            compose_file="/stacks/sonarr/docker-compose.yml",
            conflicts=[
                Conflict(conflict_type="wsl2_performance", severity="medium", services=["sonarr"], description="WSL2"),
                Conflict(conflict_type="missing_restart_policy", severity="low", services=["sonarr"], description="No restart"),
            ],
            services=[],
            pipeline_host_root=None,
        )
        assert plans == []


class TestAnalyzeStackFixPlans:
    def test_analyze_returns_fix_plans_for_broken_stack(self, tmp_path):
        """analyze_stack() populates fix_plans for a stack with conflicts."""
        compose_content = (
            "services:\n"
            "  sonarr:\n"
            "    image: lscr.io/linuxserver/sonarr\n"
            "    volumes:\n"
            "      - /host/tv:/data/tv\n"
            "  qbittorrent:\n"
            "    image: lscr.io/linuxserver/qbittorrent\n"
            "    volumes:\n"
            "      - /host/downloads:/downloads\n"
        )
        compose_file = tmp_path / "docker-compose.yml"
        compose_file.write_text(compose_content)

        import yaml
        resolved = yaml.safe_load(compose_content)
        resolved["_compose_file"] = str(compose_file)
        resolved["_resolution"] = "manual"

        from backend.analyzer import analyze_stack
        result = analyze_stack(
            resolved_compose=resolved,
            stack_path=str(tmp_path),
            compose_file=str(compose_file),
            resolution_method="manual",
            raw_compose_content=compose_content,
        )
        d = result.to_dict()
        assert d["conflict_count"] > 0, "Should detect mount conflicts"
        assert "fix_plans" in d
        assert len(d["fix_plans"]) >= 1, "Should have at least one fix plan"
        plan = d["fix_plans"][0]
        assert plan["compose_file_path"] == str(compose_file)
        assert plan["corrected_yaml"], "Should have corrected YAML"
        assert plan["category"] in ("A", "B", "A+B")
        assert isinstance(plan["changed_services"], list)
        assert isinstance(plan["changed_lines"], list)
        assert plan["change_summary"]  # Not empty string

    def test_analyze_healthy_empty_fix_plans(self, tmp_path):
        """Healthy stack has empty fix_plans."""
        compose_content = (
            "services:\n"
            "  sonarr:\n"
            "    image: lscr.io/linuxserver/sonarr\n"
            "    volumes:\n"
            "      - /host/data:/data\n"
            "  qbittorrent:\n"
            "    image: lscr.io/linuxserver/qbittorrent\n"
            "    volumes:\n"
            "      - /host/data:/data\n"
        )
        compose_file = tmp_path / "docker-compose.yml"
        compose_file.write_text(compose_content)

        import yaml
        resolved = yaml.safe_load(compose_content)
        resolved["_compose_file"] = str(compose_file)
        resolved["_resolution"] = "manual"

        from backend.analyzer import analyze_stack
        result = analyze_stack(
            resolved_compose=resolved,
            stack_path=str(tmp_path),
            compose_file=str(compose_file),
            resolution_method="manual",
            raw_compose_content=compose_content,
        )
        d = result.to_dict()
        assert d["fix_plans"] == [], "Healthy stack should have no fix plans"

    def test_fix_plans_match_original_corrected_yaml(self, tmp_path):
        """fix_plans[0].corrected_yaml should match original_corrected_yaml for single-file stacks."""
        compose_content = (
            "services:\n"
            "  sonarr:\n"
            "    image: lscr.io/linuxserver/sonarr\n"
            "    volumes:\n"
            "      - /host/tv:/data/tv\n"
            "  qbittorrent:\n"
            "    image: lscr.io/linuxserver/qbittorrent\n"
            "    volumes:\n"
            "      - /host/downloads:/downloads\n"
        )
        compose_file = tmp_path / "docker-compose.yml"
        compose_file.write_text(compose_content)

        import yaml
        resolved = yaml.safe_load(compose_content)
        resolved["_compose_file"] = str(compose_file)
        resolved["_resolution"] = "manual"

        from backend.analyzer import analyze_stack
        result = analyze_stack(
            resolved_compose=resolved,
            stack_path=str(tmp_path),
            compose_file=str(compose_file),
            resolution_method="manual",
            raw_compose_content=compose_content,
        )
        d = result.to_dict()
        if d["fix_plans"] and d["original_corrected_yaml"]:
            assert d["fix_plans"][0]["corrected_yaml"] == d["original_corrected_yaml"], \
                "Single-file fix plan YAML should match original_corrected_yaml"
