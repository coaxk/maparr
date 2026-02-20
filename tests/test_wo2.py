"""
Tests for MapArr — Analysis Engine.

Tests cover:
  - Compose resolution (manual .env parsing, variable substitution)
  - Volume parsing (short syntax, long syntax, Windows paths)
  - Service classification (*arr, download client, media server)
  - Conflict detection (no shared mount, different host paths, unreachable paths)
  - Fix generation (TRaSH-compliant recommendations)
  - API endpoint integration
"""

import os
import tempfile
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from backend.main import app
from backend.resolver import resolve_compose, ResolveError, _load_env_file, _substitute_vars
from backend.analyzer import (
    analyze_stack,
    _extract_services,
    _classify_service,
    _parse_short_volume,
    _parse_long_volume,
    _detect_conflicts,
    _get_path_root,
    VolumeMount,
    ServiceInfo,
    Conflict,
)


client = TestClient(app)


# ─── Helper: Create temp stack ───

def make_stack(compose_data: dict, env_vars: dict = None) -> str:
    """Create a temporary stack directory with compose file and optional .env."""
    tmpdir = tempfile.mkdtemp(prefix="maparr_test_")
    compose_path = Path(tmpdir) / "docker-compose.yml"
    compose_path.write_text(yaml.dump(compose_data), encoding="utf-8")

    if env_vars:
        env_path = Path(tmpdir) / ".env"
        lines = [f"{k}={v}" for k, v in env_vars.items()]
        env_path.write_text("\n".join(lines), encoding="utf-8")

    return tmpdir


# ═══════════════════════════════════════════
# Resolver Tests
# ═══════════════════════════════════════════

class TestResolver:

    def test_resolve_simple_compose(self):
        stack = make_stack({
            "services": {
                "sonarr": {
                    "image": "linuxserver/sonarr",
                    "volumes": ["/host/data:/data", "/host/config:/config"],
                }
            }
        })
        result = resolve_compose(stack)
        assert "services" in result
        assert "sonarr" in result["services"]

    def test_resolve_with_env_substitution(self):
        stack = make_stack(
            {"services": {"app": {"image": "test", "volumes": ["${DATA_PATH}:/data"]}}},
            env_vars={"DATA_PATH": "/mnt/storage"},
        )
        result = resolve_compose(stack)
        volumes = result["services"]["app"]["volumes"]
        assert any("/mnt/storage" in str(v) for v in volumes)

    def test_resolve_with_default_values(self):
        stack = make_stack({
            "services": {
                "app": {
                    "image": "test",
                    "environment": {"PUID": "${PUID:-1000}"},
                }
            }
        })
        result = resolve_compose(stack)
        env = result["services"]["app"]["environment"]
        # YAML parses unquoted 1000 as int — both are valid
        assert str(env.get("PUID")) == "1000"

    def test_resolve_no_compose_file(self):
        tmpdir = tempfile.mkdtemp(prefix="maparr_test_empty_")
        with pytest.raises(ResolveError, match="No compose file"):
            resolve_compose(tmpdir)

    def test_resolve_invalid_yaml(self):
        tmpdir = tempfile.mkdtemp(prefix="maparr_test_bad_")
        (Path(tmpdir) / "docker-compose.yml").write_text(
            "services:\n  bad:\n    - not: valid: yaml: {{{}",
            encoding="utf-8",
        )
        with pytest.raises(ResolveError, match="Invalid YAML"):
            resolve_compose(tmpdir)

    def test_resolve_no_services_key(self):
        stack = make_stack({"version": "3", "networks": {"default": {}}})
        with pytest.raises(ResolveError, match="no 'services' key"):
            resolve_compose(stack)

    def test_resolve_metadata_fields(self):
        stack = make_stack({"services": {"app": {"image": "test"}}})
        result = resolve_compose(stack)
        assert "_resolution" in result
        assert "_compose_file" in result
        assert "_warnings" in result


class TestEnvParsing:

    def test_load_basic_env(self):
        tmpdir = tempfile.mkdtemp()
        env_path = Path(tmpdir) / ".env"
        env_path.write_text("FOO=bar\nBAZ=123\n", encoding="utf-8")
        result = _load_env_file(Path(tmpdir))
        assert result == {"FOO": "bar", "BAZ": "123"}

    def test_load_quoted_values(self):
        tmpdir = tempfile.mkdtemp()
        env_path = Path(tmpdir) / ".env"
        env_path.write_text('FOO="bar baz"\nQUX=\'hello\'', encoding="utf-8")
        result = _load_env_file(Path(tmpdir))
        assert result["FOO"] == "bar baz"
        assert result["QUX"] == "hello"

    def test_load_comments(self):
        tmpdir = tempfile.mkdtemp()
        env_path = Path(tmpdir) / ".env"
        env_path.write_text("# comment\nFOO=bar\n\n# another", encoding="utf-8")
        result = _load_env_file(Path(tmpdir))
        assert result == {"FOO": "bar"}

    def test_load_missing_env(self):
        tmpdir = tempfile.mkdtemp()
        result = _load_env_file(Path(tmpdir))
        assert result == {}


class TestVarSubstitution:

    def test_simple_var(self):
        assert _substitute_vars("${FOO}", {"FOO": "bar"}) == "bar"

    def test_dollar_var(self):
        assert _substitute_vars("$FOO", {"FOO": "bar"}) == "bar"

    def test_default_value(self):
        assert _substitute_vars("${MISSING:-fallback}", {}) == "fallback"

    def test_default_not_used_when_set(self):
        assert _substitute_vars("${FOO:-fallback}", {"FOO": "real"}) == "real"

    def test_multiple_vars(self):
        result = _substitute_vars(
            "${A}:${B}", {"A": "/host", "B": "/container"}
        )
        assert result == "/host:/container"

    def test_unset_var_becomes_empty(self):
        assert _substitute_vars("${NOPE}", {}) == ""


# ═══════════════════════════════════════════
# Volume Parsing Tests
# ═══════════════════════════════════════════

class TestVolumeParsing:

    def test_short_syntax_basic(self):
        vol = _parse_short_volume("/host/data:/data")
        assert vol.source == "/host/data"
        assert vol.target == "/data"
        assert vol.is_bind_mount
        assert not vol.read_only

    def test_short_syntax_readonly(self):
        vol = _parse_short_volume("/host/data:/data:ro")
        assert vol.read_only

    def test_short_syntax_named_volume(self):
        vol = _parse_short_volume("myvolume:/data")
        assert vol.source == "myvolume"
        assert vol.target == "/data"
        assert vol.is_named_volume
        assert not vol.is_bind_mount

    def test_short_syntax_relative(self):
        vol = _parse_short_volume("./config:/config")
        assert vol.source == "./config"
        assert vol.is_bind_mount
        assert not vol.is_named_volume

    def test_short_syntax_windows_path(self):
        vol = _parse_short_volume("C:\\Users\\data:/data")
        assert vol.source == "C:\\Users\\data"  # Reconstructed with :
        assert vol.target == "/data"
        assert vol.is_bind_mount

    def test_long_syntax_bind(self):
        vol = _parse_long_volume({
            "type": "bind",
            "source": "/host/data",
            "target": "/data",
        })
        assert vol.source == "/host/data"
        assert vol.target == "/data"
        assert vol.is_bind_mount

    def test_long_syntax_volume(self):
        vol = _parse_long_volume({
            "type": "volume",
            "source": "myvolume",
            "target": "/data",
        })
        assert vol.is_named_volume
        assert not vol.is_bind_mount

    def test_long_syntax_readonly(self):
        vol = _parse_long_volume({
            "type": "bind",
            "source": "/host",
            "target": "/data",
            "read_only": True,
        })
        assert vol.read_only


# ═══════════════════════════════════════════
# Service Classification Tests
# ═══════════════════════════════════════════

class TestServiceClassification:

    def test_sonarr(self):
        assert _classify_service("sonarr", "linuxserver/sonarr") == "arr"

    def test_radarr_by_image(self):
        assert _classify_service("media-grabber", "hotio/radarr") == "arr"

    def test_qbittorrent(self):
        assert _classify_service("qbittorrent", "linuxserver/qbittorrent") == "download_client"

    def test_plex(self):
        assert _classify_service("plex", "plexinc/pms-docker") == "media_server"

    def test_jellyfin(self):
        assert _classify_service("jellyfin", "jellyfin/jellyfin") == "media_server"

    def test_overseerr(self):
        assert _classify_service("overseerr", "sctx/overseerr") == "request"

    def test_unknown(self):
        assert _classify_service("postgres", "postgres:15") == "other"

    def test_custom_name_with_arr_image(self):
        assert _classify_service("tv-manager", "linuxserver/sonarr:latest") == "arr"


# ═══════════════════════════════════════════
# Path Root Detection Tests
# ═══════════════════════════════════════════

class TestPathRoot:

    def test_absolute_deep(self):
        assert _get_path_root("/host/data/tv/shows") == "/host/data"

    def test_absolute_shallow(self):
        assert _get_path_root("/data") == "/data"

    def test_absolute_two_levels(self):
        assert _get_path_root("/host/data") == "/host/data"

    def test_relative(self):
        assert _get_path_root("./data/media") == "./data"

    def test_empty(self):
        assert _get_path_root("") is None


# ═══════════════════════════════════════════
# Conflict Detection Tests
# ═══════════════════════════════════════════

class TestConflictDetection:

    def _make_service(self, name, role, volumes):
        """Helper to create ServiceInfo with volume mounts."""
        svc = ServiceInfo(name=name, role=role)
        for src, tgt in volumes:
            svc.volumes.append(VolumeMount(
                raw=f"{src}:{tgt}",
                source=src,
                target=tgt,
                is_bind_mount=True,
            ))
        svc.data_paths = [v.target for v in svc.volumes]
        return svc

    def test_no_conflicts_shared_mount(self):
        """Services sharing same host root — no conflict."""
        services = [
            self._make_service("sonarr", "arr", [("/data", "/data")]),
            self._make_service("qbittorrent", "download_client", [("/data", "/data")]),
        ]
        conflicts = _detect_conflicts(services, None, None)
        assert len(conflicts) == 0

    def test_no_shared_mount_conflict(self):
        """Classic problem: separate mount trees."""
        services = [
            self._make_service("sonarr", "arr", [("/host/tv", "/data/tv")]),
            self._make_service("qbittorrent", "download_client", [("/host/downloads", "/downloads")]),
        ]
        conflicts = _detect_conflicts(services, None, None)
        assert any(c.conflict_type == "no_shared_mount" for c in conflicts)
        assert any(c.severity == "critical" for c in conflicts)

    def test_different_host_paths_same_target(self):
        """Two services map different host paths to same container path."""
        services = [
            self._make_service("sonarr", "arr", [("/host1/data", "/data")]),
            self._make_service("radarr", "arr", [("/host2/data", "/data")]),
        ]
        conflicts = _detect_conflicts(services, None, None)
        assert any(c.conflict_type == "different_host_paths" for c in conflicts)

    def test_path_unreachable(self):
        """Error path not covered by any volume mount."""
        services = [
            self._make_service("sonarr", "arr", [("/host/tv", "/data/tv")]),
        ]
        conflicts = _detect_conflicts(services, "sonarr", "/data/downloads/file.mkv")
        assert any(c.conflict_type == "path_unreachable" for c in conflicts)

    def test_path_reachable(self):
        """Error path IS covered by a mount — no unreachable conflict."""
        services = [
            self._make_service("sonarr", "arr", [("/host/data", "/data")]),
        ]
        conflicts = _detect_conflicts(services, "sonarr", "/data/tv/show.mkv")
        assert not any(c.conflict_type == "path_unreachable" for c in conflicts)

    def test_single_service_no_conflict(self):
        """Only one hardlink participant — can't have cross-service conflicts."""
        services = [
            self._make_service("sonarr", "arr", [("/host/data", "/data")]),
            self._make_service("postgres", "other", [("pgdata", "/var/lib/postgresql")]),
        ]
        conflicts = _detect_conflicts(services, None, None)
        assert len(conflicts) == 0

    def test_conflict_severity_ordering(self):
        """Conflicts should be sorted by severity."""
        services = [
            self._make_service("sonarr", "arr", [("/host/tv", "/data/tv")]),
            self._make_service("qbittorrent", "download_client", [("/host/downloads", "/downloads")]),
        ]
        conflicts = _detect_conflicts(services, "sonarr", "/data/downloads/file.mkv")
        if len(conflicts) >= 2:
            severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
            for i in range(len(conflicts) - 1):
                assert severity_order[conflicts[i].severity] <= severity_order[conflicts[i + 1].severity]


# ═══════════════════════════════════════════
# Full Analysis Pipeline Tests
# ═══════════════════════════════════════════

class TestFullAnalysis:

    def test_healthy_stack(self):
        """Stack with correct unified mounts — should be healthy."""
        compose = {
            "services": {
                "sonarr": {
                    "image": "linuxserver/sonarr",
                    "volumes": ["/data:/data", "./config/sonarr:/config"],
                },
                "qbittorrent": {
                    "image": "linuxserver/qbittorrent",
                    "volumes": ["/data:/data", "./config/qbit:/config"],
                },
            },
            "_resolution": "manual",
            "_compose_file": "docker-compose.yml",
            "_warnings": [],
        }
        result = analyze_stack(compose, "/tmp/test", "docker-compose.yml", "manual")
        assert result.to_dict()["status"] == "healthy"
        assert len(result.conflicts) == 0

    def test_broken_stack(self):
        """Classic broken stack — separate mount trees."""
        compose = {
            "services": {
                "sonarr": {
                    "image": "linuxserver/sonarr",
                    "volumes": ["/mnt/tv:/data/tv", "./config/sonarr:/config"],
                },
                "qbittorrent": {
                    "image": "linuxserver/qbittorrent",
                    "volumes": ["/mnt/downloads:/downloads", "./config/qbit:/config"],
                },
            },
            "_resolution": "manual",
            "_compose_file": "docker-compose.yml",
            "_warnings": [],
        }
        result = analyze_stack(compose, "/tmp/test", "docker-compose.yml", "manual")
        assert result.to_dict()["status"] == "conflicts_found"
        assert len(result.conflicts) > 0
        assert result.fix_summary is not None

    def test_fix_generated_for_no_shared_mount(self):
        """Fix recommendation should mention unified mount."""
        compose = {
            "services": {
                "sonarr": {
                    "image": "linuxserver/sonarr",
                    "volumes": ["/host/tv:/tv"],
                },
                "transmission": {
                    "image": "linuxserver/transmission",
                    "volumes": ["/host/downloads:/downloads"],
                },
            },
            "_resolution": "manual",
            "_compose_file": "docker-compose.yml",
            "_warnings": [],
        }
        result = analyze_stack(compose, "/tmp/test", "docker-compose.yml", "manual")
        no_shared = [c for c in result.conflicts if c.conflict_type == "no_shared_mount"]
        assert len(no_shared) > 0
        assert "unified" in no_shared[0].fix.lower() or "same mount" in no_shared[0].fix.lower()

    def test_analysis_with_error_context(self):
        """Error context should produce path_unreachable when appropriate."""
        compose = {
            "services": {
                "sonarr": {
                    "image": "linuxserver/sonarr",
                    "volumes": ["/host/tv:/data/tv"],
                },
            },
            "_resolution": "manual",
            "_compose_file": "docker-compose.yml",
            "_warnings": [],
        }
        result = analyze_stack(
            compose, "/tmp/test", "docker-compose.yml", "manual",
            error_service="sonarr",
            error_path="/data/downloads/file.mkv",
        )
        unreachable = [c for c in result.conflicts if c.conflict_type == "path_unreachable"]
        assert len(unreachable) > 0

    def test_result_to_dict_shape(self):
        """AnalysisResult.to_dict() has all expected keys."""
        compose = {
            "services": {"app": {"image": "test", "volumes": []}},
            "_resolution": "manual",
            "_compose_file": "test.yml",
            "_warnings": [],
        }
        result = analyze_stack(compose, "/tmp", "test.yml", "manual")
        d = result.to_dict()
        assert "services" in d
        assert "conflicts" in d
        assert "status" in d
        assert "fix_summary" in d
        assert "service_count" in d
        assert "conflict_count" in d


# ═══════════════════════════════════════════
# End-to-End: Resolver + Analyzer
# ═══════════════════════════════════════════

class TestEndToEnd:

    def test_resolve_and_analyze_healthy(self):
        """Full pipeline: resolve compose file, then analyze — healthy case."""
        stack = make_stack({
            "services": {
                "sonarr": {
                    "image": "linuxserver/sonarr",
                    "volumes": ["/data:/data"],
                },
                "qbittorrent": {
                    "image": "linuxserver/qbittorrent",
                    "volumes": ["/data:/data"],
                },
            }
        })
        resolved = resolve_compose(stack)
        result = analyze_stack(
            resolved, stack, resolved["_compose_file"],
            resolved["_resolution"],
        )
        assert result.to_dict()["status"] == "healthy"

    def test_resolve_and_analyze_broken(self):
        """Full pipeline: resolve compose file, then analyze — broken case."""
        stack = make_stack({
            "services": {
                "sonarr": {
                    "image": "linuxserver/sonarr",
                    "volumes": ["/mnt/tv:/tv"],
                },
                "qbittorrent": {
                    "image": "linuxserver/qbittorrent",
                    "volumes": ["/mnt/downloads:/downloads"],
                },
            }
        })
        resolved = resolve_compose(stack)
        result = analyze_stack(
            resolved, stack, resolved["_compose_file"],
            resolved["_resolution"],
        )
        assert result.to_dict()["status"] == "conflicts_found"

    def test_resolve_with_env_and_analyze(self):
        """Full pipeline with .env variable substitution."""
        stack = make_stack(
            {
                "services": {
                    "sonarr": {
                        "image": "linuxserver/sonarr",
                        "volumes": ["${MEDIA_ROOT}:/data"],
                    },
                    "qbittorrent": {
                        "image": "linuxserver/qbittorrent",
                        "volumes": ["${MEDIA_ROOT}:/data"],
                    },
                }
            },
            env_vars={"MEDIA_ROOT": "/mnt/storage"},
        )
        resolved = resolve_compose(stack)
        result = analyze_stack(
            resolved, stack, resolved["_compose_file"],
            resolved["_resolution"],
        )
        # Both use same source via env var — should be healthy
        assert result.to_dict()["status"] == "healthy"


# ═══════════════════════════════════════════
# API Endpoint Tests
# ═══════════════════════════════════════════

class TestAnalyzeAPI:

    def test_analyze_endpoint_healthy(self):
        stack = make_stack({
            "services": {
                "sonarr": {
                    "image": "linuxserver/sonarr",
                    "volumes": ["/data:/data"],
                },
                "qbittorrent": {
                    "image": "linuxserver/qbittorrent",
                    "volumes": ["/data:/data"],
                },
            }
        })
        resp = client.post("/api/analyze", json={"stack_path": stack})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["conflict_count"] == 0

    def test_analyze_endpoint_broken(self):
        stack = make_stack({
            "services": {
                "sonarr": {
                    "image": "linuxserver/sonarr",
                    "volumes": ["/host/tv:/tv"],
                },
                "qbittorrent": {
                    "image": "linuxserver/qbittorrent",
                    "volumes": ["/host/downloads:/downloads"],
                },
            }
        })
        resp = client.post("/api/analyze", json={"stack_path": stack})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "conflicts_found"
        assert data["conflict_count"] > 0
        assert data["conflicts"][0]["fix"] is not None

    def test_analyze_endpoint_with_error(self):
        stack = make_stack({
            "services": {
                "sonarr": {
                    "image": "linuxserver/sonarr",
                    "volumes": ["/host/tv:/data/tv"],
                },
            }
        })
        resp = client.post("/api/analyze", json={
            "stack_path": stack,
            "error": {
                "service": "sonarr",
                "path": "/data/downloads/file.mkv",
            },
        })
        assert resp.status_code == 200
        data = resp.json()
        # Should detect path_unreachable
        types = [c["type"] for c in data["conflicts"]]
        assert "path_unreachable" in types

    def test_analyze_endpoint_no_path(self):
        resp = client.post("/api/analyze", json={"stack_path": ""})
        assert resp.status_code == 400

    def test_analyze_endpoint_bad_path(self):
        resp = client.post("/api/analyze", json={
            "stack_path": "/nonexistent/test/xyz123"
        })
        assert resp.status_code == 400

    def test_analyze_endpoint_no_compose(self):
        """Stack dir exists but has no compose file."""
        tmpdir = tempfile.mkdtemp(prefix="maparr_test_empty_")
        resp = client.post("/api/analyze", json={"stack_path": tmpdir})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "error"
        assert data["stage"] == "resolution"


# ═══════════════════════════════════════════
# Discovery & parse tests still pass
# ═══════════════════════════════════════════

def test_wo1_health_still_works():
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_wo1_parse_still_works():
    resp = client.post("/api/parse-error", json={
        "error_text": "Sonarr import failed /data/tv"
    })
    assert resp.status_code == 200
    assert resp.json()["confidence"] in ("high", "medium", "low", "none")
