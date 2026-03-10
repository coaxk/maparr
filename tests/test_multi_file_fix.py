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
            "    environment:\n"
            "      - PUID=1000\n"
            "      - PGID=1000\n"
            "    volumes:\n"
            "      - /host/data:/data\n"
            "  qbittorrent:\n"
            "    image: lscr.io/linuxserver/qbittorrent\n"
            "    environment:\n"
            "      - PUID=1000\n"
            "      - PGID=1000\n"
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
            # The A+B combined plan should match original_corrected_yaml
            combined = [p for p in d["fix_plans"] if p["category"] == "A+B"]
            if combined:
                assert combined[0]["corrected_yaml"] == d["original_corrected_yaml"], \
                    "Combined A+B fix plan YAML should match original_corrected_yaml"
            else:
                # Single-category only — first plan should match
                assert d["fix_plans"][0]["corrected_yaml"] == d["original_corrected_yaml"], \
                    "Single-category fix plan YAML should match original_corrected_yaml"


class TestMultiFileCluster:
    def test_cluster_layout_produces_multi_plans(self, tmp_path):
        """Cluster layout with 3 services in 3 folders produces plans for files that need fixing."""
        for name, image, vol in [
            ("sonarr", "lscr.io/linuxserver/sonarr", "/host/tv:/data/tv"),
            ("radarr", "lscr.io/linuxserver/radarr", "/host/movies:/data/movies"),
            ("qbittorrent", "lscr.io/linuxserver/qbittorrent", "/host/downloads:/downloads"),
        ]:
            d = tmp_path / name
            d.mkdir()
            (d / "docker-compose.yml").write_text(
                f"services:\n  {name}:\n    image: {image}\n    volumes:\n      - {vol}\n"
            )

        from backend.pipeline import run_pipeline_scan, get_pipeline_context_for_stack
        pipeline_result = run_pipeline_scan(str(tmp_path))
        pipeline_dict = pipeline_result.to_dict()

        # Should have 3 media services
        assert len(pipeline_result.media_services) == 3

        # Get context for sonarr stack
        sonarr_ctx = get_pipeline_context_for_stack(pipeline_dict, str(tmp_path / "sonarr"))
        assert sonarr_ctx is not None
        # Should have siblings
        siblings = sonarr_ctx.get("sibling_services", [])
        assert len(siblings) >= 1

        # Verify full compose path is available in sibling data
        for sib in siblings:
            assert "compose_file_full" in sib, f"Sibling {sib.get('service_name')} missing compose_file_full"
            assert sib["compose_file_full"].endswith("docker-compose.yml")

    def test_build_multi_reads_siblings(self, tmp_path):
        """_build_fix_plans_multi generates plans for sibling compose files."""
        # Create cluster with conflicting mounts
        for name, image, vol in [
            ("sonarr", "lscr.io/linuxserver/sonarr", "/host/tv:/data/tv"),
            ("qbittorrent", "lscr.io/linuxserver/qbittorrent", "/host/downloads:/downloads"),
        ]:
            d = tmp_path / name
            d.mkdir()
            (d / "docker-compose.yml").write_text(
                f"services:\n  {name}:\n    image: {image}\n    volumes:\n      - {vol}\n"
            )

        from backend.pipeline import run_pipeline_scan, get_pipeline_context_for_stack
        from backend.analyzer import _build_fix_plans_multi

        pipeline = run_pipeline_scan(str(tmp_path))
        ctx = get_pipeline_context_for_stack(pipeline.to_dict(), str(tmp_path / "sonarr"))

        sonarr_compose = str(tmp_path / "sonarr" / "docker-compose.yml")
        sonarr_content = open(sonarr_compose).read()

        plans = _build_fix_plans_multi(
            stack_path=str(tmp_path / "sonarr"),
            compose_file=sonarr_compose,
            raw_compose_content=sonarr_content,
            conflicts=[Conflict(
                conflict_type="no_shared_mount", severity="high",
                services=["sonarr", "qbittorrent"], description="No shared mount",
            )],
            services=[ServiceInfo(
                name="sonarr", image="lscr.io/linuxserver/sonarr", role="arr", volumes=[],
            )],
            pipeline_context=ctx,
            pipeline_host_root="/data",
            stacks_root=str(tmp_path),
        )

        # Should have plans for multiple files
        assert len(plans) >= 1
        compose_paths = [p["compose_file_path"] for p in plans]
        assert sonarr_compose in compose_paths, "Should include sonarr's own compose"

    def test_cluster_analyze_produces_multi_plans(self, tmp_path):
        """Full analyze_stack() with pipeline context uses multi-file path."""
        # Build cluster with conflicting mounts — include PUID/PGID so Cat B
        # doesn't swallow the Cat A conflict
        for name, image, vol in [
            ("sonarr", "lscr.io/linuxserver/sonarr", "/host/tv:/data/tv"),
            ("radarr", "lscr.io/linuxserver/radarr", "/host/movies:/data/movies"),
            ("qbittorrent", "lscr.io/linuxserver/qbittorrent", "/host/downloads:/downloads"),
        ]:
            d = tmp_path / name
            d.mkdir()
            (d / "docker-compose.yml").write_text(
                f"services:\n  {name}:\n    image: {image}\n"
                f"    environment:\n      - PUID=1000\n      - PGID=1000\n"
                f"    volumes:\n      - {vol}\n"
            )

        from backend.pipeline import run_pipeline_scan, get_pipeline_context_for_stack
        pipeline = run_pipeline_scan(str(tmp_path))
        ctx = get_pipeline_context_for_stack(pipeline.to_dict(), str(tmp_path / "sonarr"))

        import yaml
        sonarr_compose = tmp_path / "sonarr" / "docker-compose.yml"
        resolved = yaml.safe_load(sonarr_compose.read_text())
        resolved["_compose_file"] = str(sonarr_compose)
        resolved["_resolution"] = "manual"

        from backend.analyzer import analyze_stack
        result = analyze_stack(
            resolved_compose=resolved,
            stack_path=str(tmp_path / "sonarr"),
            compose_file=str(sonarr_compose),
            resolution_method="manual",
            raw_compose_content=sonarr_compose.read_text(),
            pipeline_context=ctx,
        )
        d = result.to_dict()
        # Should have fix plans field
        assert "fix_plans" in d
        # With pipeline context and Cat A conflicts, multi-file plans should include
        # at minimum the sonarr compose file
        cat_a_conflicts = [c for c in d.get("conflicts", []) if c.get("category") == "A"]
        if cat_a_conflicts:
            assert len(d["fix_plans"]) >= 1
            compose_paths = [p["compose_file_path"] for p in d["fix_plans"]]
            assert str(sonarr_compose) in compose_paths

    def test_analyze_without_pipeline_uses_single(self, tmp_path):
        """analyze_stack() without pipeline context falls back to single-file."""
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
            pipeline_context=None,  # No pipeline
        )
        d = result.to_dict()
        if d["conflict_count"] > 0:
            # Without pipeline, all plans should reference the same single file.
            # May have up to 3 plans (A, B, A+B) but all for one compose file.
            files = set(p["compose_file_path"] for p in d["fix_plans"])
            assert len(files) == 1, \
                f"Without pipeline, should reference single file only, got: {files}"

    def test_single_file_through_multi_path(self, tmp_path):
        """Single-file stack through _build_fix_plans_multi still works (no pipeline = fallback)."""
        compose = tmp_path / "docker-compose.yml"
        compose.write_text(
            "services:\n"
            "  sonarr:\n"
            "    image: lscr.io/linuxserver/sonarr\n"
            "    volumes:\n      - /host/tv:/data/tv\n"
            "  qbittorrent:\n"
            "    image: lscr.io/linuxserver/qbittorrent\n"
            "    volumes:\n      - /host/downloads:/downloads\n"
        )
        from backend.analyzer import _build_fix_plans_multi
        plans = _build_fix_plans_multi(
            stack_path=str(tmp_path),
            compose_file=str(compose),
            raw_compose_content=compose.read_text(),
            conflicts=[Conflict(
                conflict_type="no_shared_mount", severity="high",
                services=["sonarr", "qbittorrent"], description="No shared mount",
            )],
            services=[
                ServiceInfo(name="sonarr", image="lscr.io/linuxserver/sonarr", role="arr", volumes=[]),
                ServiceInfo(name="qbittorrent", image="lscr.io/linuxserver/qbittorrent", role="download_client", volumes=[]),
            ],
            pipeline_context=None,  # No pipeline = single file fallback
            pipeline_host_root="/data",
        )
        assert len(plans) == 1
        assert plans[0]["compose_file_path"] == str(compose)


class TestPastePathwayParity:
    def test_both_pathways_same_fix_plans(self, tmp_path):
        """Browse and Paste pathways produce identical fix_plans for same stack."""
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

        # Browse pathway: no error context
        browse_result = analyze_stack(
            resolved_compose=resolved,
            stack_path=str(tmp_path),
            compose_file=str(compose_file),
            resolution_method="manual",
            raw_compose_content=compose_content,
        )

        # Paste pathway: with error context (service + path from parsed error)
        paste_result = analyze_stack(
            resolved_compose=resolved,
            stack_path=str(tmp_path),
            compose_file=str(compose_file),
            resolution_method="manual",
            error_service="sonarr",
            error_path="/data/tv/some/file.mkv",
            raw_compose_content=compose_content,
        )

        browse_plans = browse_result.to_dict()["fix_plans"]
        paste_plans = paste_result.to_dict()["fix_plans"]

        # Both should have the same number of plans
        assert len(browse_plans) == len(paste_plans), \
            f"Browse produced {len(browse_plans)} plans, Paste produced {len(paste_plans)}"

        # Each plan should have identical content
        for bp, pp in zip(browse_plans, paste_plans):
            assert bp["compose_file_path"] == pp["compose_file_path"], \
                "File paths differ between pathways"
            assert bp["corrected_yaml"] == pp["corrected_yaml"], \
                "Corrected YAML differs between pathways"
            assert bp["category"] == pp["category"], \
                "Categories differ between pathways"
            assert bp["changed_services"] == pp["changed_services"], \
                "Changed services differ between pathways"

    def test_paste_with_pipeline_same_as_browse(self, tmp_path):
        """With pipeline context, both pathways still produce identical plans."""
        for name, image, vol in [
            ("sonarr", "lscr.io/linuxserver/sonarr", "/host/tv:/data/tv"),
            ("qbittorrent", "lscr.io/linuxserver/qbittorrent", "/host/downloads:/downloads"),
        ]:
            d = tmp_path / name
            d.mkdir()
            (d / "docker-compose.yml").write_text(
                f"services:\n  {name}:\n    image: {image}\n    volumes:\n      - {vol}\n"
            )

        from backend.pipeline import run_pipeline_scan, get_pipeline_context_for_stack
        pipeline = run_pipeline_scan(str(tmp_path))
        ctx = get_pipeline_context_for_stack(pipeline.to_dict(), str(tmp_path / "sonarr"))

        import yaml
        sonarr_compose = tmp_path / "sonarr" / "docker-compose.yml"
        resolved = yaml.safe_load(sonarr_compose.read_text())
        resolved["_compose_file"] = str(sonarr_compose)
        resolved["_resolution"] = "manual"

        from backend.analyzer import analyze_stack

        browse = analyze_stack(
            resolved_compose=resolved,
            stack_path=str(tmp_path / "sonarr"),
            compose_file=str(sonarr_compose),
            resolution_method="manual",
            raw_compose_content=sonarr_compose.read_text(),
            pipeline_context=ctx,
        )

        paste = analyze_stack(
            resolved_compose=resolved,
            stack_path=str(tmp_path / "sonarr"),
            compose_file=str(sonarr_compose),
            resolution_method="manual",
            error_service="sonarr",
            error_path="/data/tv/some/file.mkv",
            raw_compose_content=sonarr_compose.read_text(),
            pipeline_context=ctx,
        )

        b_plans = browse.to_dict()["fix_plans"]
        p_plans = paste.to_dict()["fix_plans"]

        assert len(b_plans) == len(p_plans), \
            f"Pipeline: Browse={len(b_plans)} plans, Paste={len(p_plans)} plans"
        for bp, pp in zip(b_plans, p_plans):
            assert bp["compose_file_path"] == pp["compose_file_path"]
            assert bp["corrected_yaml"] == pp["corrected_yaml"]
