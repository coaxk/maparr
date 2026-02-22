"""
Integration tests for MapArr — Discovery & Parse APIs.

Tests the three API endpoints + the parser and discovery modules.
"""

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.parser import parse_error
from backend.discovery import discover_stacks


client = TestClient(app)


# ─── Health ───

def test_health():
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["version"] == "1.5.0"


# ─── Parse Error API ───

def test_parse_error_high_confidence():
    resp = client.post("/api/parse-error", json={
        "error_text": "Sonarr - Import failed, path does not exist: /data/tv/Show"
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["service"] == "sonarr"
    assert data["path"] == "/data/tv/Show"
    assert data["confidence"] == "high"
    assert data["error_type"] == "import_failed"


def test_parse_error_medium_confidence():
    resp = client.post("/api/parse-error", json={
        "error_text": "cannot access /mnt/data/movies"
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["confidence"] == "medium"
    assert data["path"] is not None


def test_parse_error_low_confidence():
    resp = client.post("/api/parse-error", json={
        "error_text": "something about volume mount problems"
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["confidence"] == "low"


def test_parse_error_no_confidence():
    resp = client.post("/api/parse-error", json={
        "error_text": "hello world nothing useful"
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["confidence"] == "none"
    assert len(data["suggestions"]) > 0


def test_parse_error_empty():
    resp = client.post("/api/parse-error", json={
        "error_text": ""
    })
    assert resp.status_code == 400


def test_parse_error_no_json():
    resp = client.post("/api/parse-error", content=b"not json",
                       headers={"Content-Type": "application/json"})
    assert resp.status_code == 400


# ─── Parser Unit Tests ───

def test_parser_extracts_service():
    result = parse_error("Radarr cannot import movie")
    assert result.service == "radarr"


def test_parser_extracts_qbit_abbreviation():
    result = parse_error("qbit download stuck at 99%")
    assert result.service == "qbittorrent"


def test_parser_extracts_unix_path():
    result = parse_error("File not found: /data/media/movies/Film.mkv")
    assert result.path == "/data/media/movies/Film.mkv"


def test_parser_extracts_windows_path():
    result = parse_error("Error at C:\\DockerContainers\\sonarr\\config")
    assert result.path is not None
    assert "DockerContainers" in result.path


def test_parser_extracts_error_type_permission():
    result = parse_error("permission denied accessing /data")
    assert result.error_type == "permission_denied"


def test_parser_extracts_error_type_hardlink():
    result = parse_error("cross-device link failed")
    assert result.error_type == "hardlink_failed"


def test_parser_never_returns_none():
    result = parse_error("")
    assert result is not None
    assert result.confidence == "none"


def test_parser_garbage_input():
    result = parse_error("asdfghjkl 12345 !@#$%")
    assert result is not None
    assert result.confidence == "none"


# ─── Discover Stacks API ───

def test_discover_stacks_endpoint():
    resp = client.get("/api/discover-stacks")
    assert resp.status_code == 200
    data = resp.json()
    assert "stacks" in data
    assert "total" in data
    assert isinstance(data["stacks"], list)
    assert "search_note" in data


# ─── Discovery Unit Tests ───

def test_discover_returns_list():
    stacks = discover_stacks()
    assert isinstance(stacks, list)


def test_stack_has_required_fields():
    stacks = discover_stacks()
    if stacks:
        s = stacks[0]
        d = s.to_dict()
        assert "path" in d
        assert "compose_file" in d
        assert "services" in d
        assert "service_count" in d


# ─── Select Stack API ───

def test_select_stack_no_path():
    resp = client.post("/api/select-stack", json={
        "stack_path": ""
    })
    assert resp.status_code == 400


def test_select_stack_bad_path():
    resp = client.post("/api/select-stack", json={
        "stack_path": "/nonexistent/path/xyz123"
    })
    assert resp.status_code == 400


# ─── Frontend Serving ───

def test_serve_index():
    resp = client.get("/")
    assert resp.status_code == 200
    assert "MapArr" in resp.text
