"""Tests for /api/host-info endpoint — first-run wizard."""
import pytest
from fastapi.testclient import TestClient
from backend.main import app


def test_host_info_returns_platform_and_ids():
    """Host info endpoint must return platform, uid, gid."""
    client = TestClient(app)
    response = client.get("/api/host-info")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    assert "platform" in data, "Response must include 'platform'"
    assert "uid" in data, "Response must include 'uid'"
    assert "gid" in data, "Response must include 'gid'"
    assert isinstance(data["uid"], int), "uid must be an integer"
    assert isinstance(data["gid"], int), "gid must be an integer"


def test_host_info_platform_is_string():
    """Platform field must be a non-empty string."""
    client = TestClient(app)
    response = client.get("/api/host-info")
    data = response.json()
    assert isinstance(data["platform"], str), "platform must be a string"
    assert len(data["platform"]) > 0, "platform must not be empty"


def test_host_info_uid_gid_non_negative():
    """UID and GID should be non-negative integers."""
    client = TestClient(app)
    response = client.get("/api/host-info")
    data = response.json()
    assert data["uid"] >= 0, "uid must be non-negative"
    assert data["gid"] >= 0, "gid must be non-negative"
