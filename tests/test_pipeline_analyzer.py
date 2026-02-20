"""
Tests for MapArr — Pipeline Context in Analyzer.

Covers:
  - analyze_stack() with pipeline_context parameter
  - Pipeline-aware status values: healthy_pipeline, pipeline_conflict
  - Pipeline-aware fix summary (shows pipeline service count, shared mount)
  - Pipeline data on AnalysisResult
  - Cross-stack result populated from pipeline (backward compat)
  - Legacy fallback when no pipeline context
"""

import os
import textwrap

import pytest

from conftest import (
    SONARR_YAML, QBITTORRENT_YAML, PLEX_YAML, RADARR_YAML,
    HEALTHY_MULTI_YAML, BROKEN_MULTI_YAML, UTILITY_YAML,
)


def _resolve_and_analyze(stack_path, yaml_content, pipeline_context=None, scan_dir=None):
    """Helper: resolve a compose file and run analysis."""
    from backend.resolver import resolve_compose
    from backend.analyzer import analyze_stack

    compose_file = os.path.join(stack_path, "docker-compose.yml")
    resolved = resolve_compose(stack_path)

    return analyze_stack(
        resolved_compose=resolved,
        stack_path=stack_path,
        compose_file=compose_file,
        resolution_method="manual",
        pipeline_context=pipeline_context,
        scan_dir=scan_dir,
    )


# ═══════════════════════════════════════════
# Pipeline-Aware Analysis
# ═══════════════════════════════════════════

class TestPipelineAwareAnalysis:
    """Analyzer behavior when pipeline_context is provided."""

    def test_healthy_pipeline_status(self, make_stack):
        """Single-service stack + healthy pipeline → healthy_pipeline status."""
        stack_path = make_stack(SONARR_YAML, dirname="sonarr")

        pipeline_ctx = {
            "role": "arr",
            "total_media": 5,
            "shared_mount": True,
            "mount_root": "/mnt/nas/data",
            "health": "ok",
            "conflicts": [],
            "sibling_services": [
                {"service_name": "radarr", "role": "arr", "stack_name": "radarr"},
                {"service_name": "qbittorrent", "role": "download_client", "stack_name": "qbittorrent"},
                {"service_name": "plex", "role": "media_server", "stack_name": "plex"},
            ],
            "services_by_role": {
                "arr": [{"service_name": "sonarr"}, {"service_name": "radarr"}],
                "download_client": [{"service_name": "qbittorrent"}],
                "media_server": [{"service_name": "plex"}],
            },
            "summary": "5 media services across 5 stacks",
        }

        result = _resolve_and_analyze(stack_path, SONARR_YAML, pipeline_context=pipeline_ctx)
        d = result.to_dict()

        assert d["status"] == "healthy_pipeline"
        assert result.pipeline is not None
        assert result.pipeline["role"] == "arr"
        assert result.pipeline["total_media"] == 5

    def test_pipeline_conflict_status(self, make_stack):
        """Single-service stack + pipeline conflict → pipeline_conflict status."""
        stack_path = make_stack(SONARR_YAML, dirname="sonarr")

        pipeline_ctx = {
            "role": "arr",
            "total_media": 3,
            "shared_mount": False,
            "mount_root": "",
            "health": "problem",
            "conflicts": [
                {"stack_name": "sonarr", "description": "Mount conflict"},
            ],
            "sibling_services": [
                {"service_name": "qbittorrent", "role": "download_client", "stack_name": "qbittorrent"},
            ],
            "services_by_role": {
                "arr": [{"service_name": "sonarr"}],
                "download_client": [{"service_name": "qbittorrent"}],
            },
            "summary": "3 services — mount conflict",
        }

        result = _resolve_and_analyze(stack_path, SONARR_YAML, pipeline_context=pipeline_ctx)
        d = result.to_dict()

        assert d["status"] == "pipeline_conflict"

    def test_pipeline_fix_summary_with_shared_mount(self, make_stack):
        """Fix summary references pipeline when context is available."""
        stack_path = make_stack(SONARR_YAML, dirname="sonarr")

        pipeline_ctx = {
            "role": "arr",
            "total_media": 10,
            "shared_mount": True,
            "mount_root": "/mnt/nas",
            "health": "ok",
            "conflicts": [],
            "sibling_services": [],
            "services_by_role": {},
            "summary": "10 media services",
        }

        result = _resolve_and_analyze(stack_path, SONARR_YAML, pipeline_context=pipeline_ctx)

        assert result.fix_summary is not None
        assert "10-service" in result.fix_summary
        assert "/mnt/nas" in result.fix_summary

    def test_pipeline_steps_show_role(self, make_stack):
        """Terminal steps should include pipeline role and service count."""
        stack_path = make_stack(SONARR_YAML, dirname="sonarr")

        pipeline_ctx = {
            "role": "arr",
            "total_media": 4,
            "shared_mount": True,
            "mount_root": "/mnt/nas/data",
            "health": "ok",
            "conflicts": [],
            "sibling_services": [
                {"service_name": "qbittorrent", "role": "download_client", "stack_name": "qbittorrent"},
            ],
            "services_by_role": {
                "arr": [{"service_name": "sonarr"}],
                "download_client": [{"service_name": "qbittorrent"}],
            },
            "summary": "",
        }

        result = _resolve_and_analyze(stack_path, SONARR_YAML, pipeline_context=pipeline_ctx)
        step_texts = [s["text"] for s in result.steps]

        # Should mention pipeline role
        assert any("pipeline role" in t for t in step_texts)
        # Should mention service count
        assert any("4 services" in t for t in step_texts)

    def test_pipeline_cross_stack_backward_compat(self, make_stack):
        """Pipeline data should populate cross_stack field for backward compat."""
        stack_path = make_stack(SONARR_YAML, dirname="sonarr")

        pipeline_ctx = {
            "role": "arr",
            "total_media": 3,
            "shared_mount": True,
            "mount_root": "/mnt/nas/data",
            "health": "ok",
            "conflicts": [],
            "sibling_services": [
                {"service_name": "qbittorrent", "role": "download_client", "stack_name": "qbittorrent"},
            ],
            "services_by_role": {
                "arr": [{"service_name": "sonarr"}],
                "download_client": [{"service_name": "qbittorrent"}],
            },
            "summary": "",
        }

        result = _resolve_and_analyze(stack_path, SONARR_YAML, pipeline_context=pipeline_ctx)

        assert result.cross_stack is not None
        assert result.cross_stack.get("source") == "pipeline"
        assert result.cross_stack.get("shared_mount") is True

    def test_pipeline_data_stored_on_result(self, make_stack):
        """Pipeline dict should be stored on the result for frontend use."""
        stack_path = make_stack(SONARR_YAML, dirname="sonarr")

        pipeline_ctx = {
            "role": "arr",
            "total_media": 5,
            "shared_mount": True,
            "mount_root": "/mnt/nas",
            "health": "ok",
            "conflicts": [],
            "sibling_services": [],
            "services_by_role": {
                "arr": [{"service_name": "sonarr"}],
                "download_client": [{"service_name": "qbittorrent"}],
            },
            "summary": "",
        }

        result = _resolve_and_analyze(stack_path, SONARR_YAML, pipeline_context=pipeline_ctx)
        d = result.to_dict()

        assert d["pipeline"] is not None
        assert d["pipeline"]["role"] == "arr"
        assert d["pipeline"]["health"] == "ok"
        assert d["pipeline"]["shared_mount"] is True


# ═══════════════════════════════════════════
# Legacy Fallback (No Pipeline)
# ═══════════════════════════════════════════

class TestLegacyFallback:
    """Analyzer behavior without pipeline context."""

    def test_no_pipeline_healthy_multi(self, make_stack):
        """Multi-service healthy stack without pipeline → healthy status."""
        stack_path = make_stack(HEALTHY_MULTI_YAML, dirname="mediastack")

        result = _resolve_and_analyze(stack_path, HEALTHY_MULTI_YAML)
        d = result.to_dict()

        assert d["status"] == "healthy"
        assert result.pipeline is None
        assert len(result.conflicts) == 0

    def test_no_pipeline_broken_multi(self, make_stack):
        """Multi-service broken stack without pipeline → conflicts_found status."""
        stack_path = make_stack(BROKEN_MULTI_YAML, dirname="mediastack")

        result = _resolve_and_analyze(stack_path, BROKEN_MULTI_YAML)
        d = result.to_dict()

        assert d["status"] == "conflicts_found"
        assert len(result.conflicts) >= 1

    def test_no_pipeline_single_service_incomplete(self, make_stack):
        """Single-service stack without pipeline → incomplete status."""
        stack_path = make_stack(SONARR_YAML, dirname="sonarr")

        result = _resolve_and_analyze(stack_path, SONARR_YAML)
        d = result.to_dict()

        assert d["status"] == "incomplete"
        assert result.incomplete_stack is True

    def test_no_pipeline_cross_stack_fallback(self, make_pipeline_dir):
        """Without pipeline, cross-stack scan runs as fallback."""
        root = make_pipeline_dir({
            "sonarr": SONARR_YAML,
            "qbittorrent": QBITTORRENT_YAML,
        })
        sonarr_path = os.path.join(root, "sonarr")

        result = _resolve_and_analyze(sonarr_path, SONARR_YAML, scan_dir=root)
        d = result.to_dict()

        # Should find qbittorrent as a sibling
        assert result.cross_stack is not None
        assert d["status"] in ("healthy_cross_stack", "incomplete")


# ═══════════════════════════════════════════
# Status Logic
# ═══════════════════════════════════════════

class TestStatusLogic:
    """AnalysisResult.to_dict() status determination."""

    def test_conflicts_always_win(self, make_stack):
        """conflicts_found status takes priority over pipeline context."""
        stack_path = make_stack(BROKEN_MULTI_YAML, dirname="mediastack")

        pipeline_ctx = {
            "role": "arr",
            "total_media": 5,
            "shared_mount": True,
            "mount_root": "/mnt/nas",
            "health": "ok",
            "conflicts": [],
            "sibling_services": [],
            "services_by_role": {},
            "summary": "",
        }

        result = _resolve_and_analyze(stack_path, BROKEN_MULTI_YAML, pipeline_context=pipeline_ctx)
        d = result.to_dict()

        # Local conflicts trump pipeline health
        assert d["status"] == "conflicts_found"

    def test_pipeline_healthy_over_incomplete(self, make_stack):
        """Pipeline context should override 'incomplete' status."""
        stack_path = make_stack(SONARR_YAML, dirname="sonarr")

        pipeline_ctx = {
            "role": "arr",
            "total_media": 4,
            "shared_mount": True,
            "mount_root": "/mnt/nas",
            "health": "ok",
            "conflicts": [],
            "sibling_services": [
                {"service_name": "qbittorrent", "role": "download_client", "stack_name": "qbittorrent"},
            ],
            "services_by_role": {
                "arr": [{"service_name": "sonarr"}],
                "download_client": [{"service_name": "qbittorrent"}],
            },
            "summary": "",
        }

        result = _resolve_and_analyze(stack_path, SONARR_YAML, pipeline_context=pipeline_ctx)
        d = result.to_dict()

        # Should NOT be "incomplete" — pipeline knows about siblings
        assert d["status"] == "healthy_pipeline"
        assert d["status"] != "incomplete"


# ═══════════════════════════════════════════
# API Integration
# ═══════════════════════════════════════════

class TestAnalyzeWithPipeline:
    """Full API flow: pipeline scan → analyze with context."""

    def test_analyze_after_pipeline_scan(self, client, make_pipeline_dir):
        """After pipeline-scan, analyze should receive pipeline context."""
        root = make_pipeline_dir({
            "sonarr": SONARR_YAML,
            "qbittorrent": QBITTORRENT_YAML,
            "plex": PLEX_YAML,
        })

        # Run pipeline scan first
        resp1 = client.post("/api/pipeline-scan", json={"scan_dir": root})
        assert resp1.status_code == 200

        # Now analyze sonarr — should have pipeline context
        sonarr_path = os.path.join(root, "sonarr")
        resp2 = client.post("/api/analyze", json={"stack_path": sonarr_path})
        assert resp2.status_code == 200

        data = resp2.json()
        assert data.get("pipeline") is not None
        assert data["status"] in ("healthy_pipeline", "healthy")

    def test_path_change_clears_pipeline(self, client, make_pipeline_dir, tmp_path):
        """Changing stacks path should invalidate cached pipeline."""
        root = make_pipeline_dir({
            "sonarr": SONARR_YAML,
        })

        # Pipeline scan
        client.post("/api/pipeline-scan", json={"scan_dir": root})

        # Change path (invalidates pipeline)
        new_dir = tmp_path / "newroot"
        new_dir.mkdir()
        client.post("/api/change-stacks-path", json={"path": str(new_dir)})

        # Analyze — pipeline should be gone
        sonarr_path = os.path.join(root, "sonarr")
        resp = client.post("/api/analyze", json={"stack_path": sonarr_path})
        assert resp.status_code == 200
        data = resp.json()
        # Pipeline should be None since we changed paths
        assert data.get("pipeline") is None
