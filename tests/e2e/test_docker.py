"""
Layer 4 — Docker Deployment Tests for MapArr.

Tests the Docker container lifecycle: build, run, healthcheck, custom port,
and read-only stack mounts. All tests are skipped if Docker is not available
on the host machine.

Uses httpx for HTTP requests to the running container and subprocess for
Docker CLI operations.
"""

import socket
import subprocess
import time
from pathlib import Path

import httpx
import pytest

# ─── Constants ───

E2E_STACKS = str(Path(__file__).parent / "fixtures" / "stacks")
PROJECT_ROOT = str(Path(__file__).parent.parent.parent)
IMAGE_NAME = "maparr-e2e-test"


# ─── Docker Availability Check ───

def _docker_available() -> bool:
    """Check if Docker daemon is running and accessible."""
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


DOCKER_AVAILABLE = _docker_available()

pytestmark = pytest.mark.skipif(
    not DOCKER_AVAILABLE, reason="Docker not available"
)


# ─── Helper Functions ───

def _port_is_open(port: int) -> bool:
    """Check if a TCP port is accepting connections."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _build_image() -> None:
    """Build the MapArr Docker image from the project root."""
    result = subprocess.run(
        ["docker", "build", "-t", IMAGE_NAME, "."],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        pytest.fail(f"Docker build failed:\n{result.stderr}")


def _run_container(
    port: int,
    extra_env: dict | None = None,
    extra_volumes: list[str] | None = None,
    name: str | None = None,
) -> str:
    """Run a MapArr container and return its container ID.

    Args:
        port: Host port to map to the container's app port.
        extra_env: Additional environment variables (-e KEY=VALUE).
        extra_volumes: Additional volume mounts (-v host:container).
        name: Container name (--name).

    Returns:
        Container ID string.
    """
    # Determine the container port (custom MAPARR_PORT or default 9494)
    container_port = 9494
    if extra_env and "MAPARR_PORT" in extra_env:
        container_port = int(extra_env["MAPARR_PORT"])

    cmd = ["docker", "run", "-d"]

    if name:
        cmd.extend(["--name", name])

    cmd.extend(["-p", f"{port}:{container_port}"])

    # Always set DOCKER_HOST to a dead endpoint so compose resolution
    # doesn't hang trying to reach the host Docker socket.
    cmd.extend(["-e", "DOCKER_HOST=tcp://127.0.0.1:1"])

    if extra_env:
        for key, value in extra_env.items():
            cmd.extend(["-e", f"{key}={value}"])

    if extra_volumes:
        for vol in extra_volumes:
            cmd.extend(["-v", vol])

    cmd.append(IMAGE_NAME)

    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        pytest.fail(f"docker run failed:\n{result.stderr}")

    return result.stdout.strip()


def _cleanup_container(container_id: str) -> None:
    """Force-remove a container by ID."""
    subprocess.run(
        ["docker", "rm", "-f", container_id],
        capture_output=True,
        timeout=15,
    )


def _wait_for_healthy(port: int, timeout: int = 30) -> None:
    """Poll /api/health until it returns 200 or timeout expires."""
    deadline = time.time() + timeout
    url = f"http://localhost:{port}/api/health"
    last_error = None

    while time.time() < deadline:
        try:
            resp = httpx.get(url, timeout=2.0)
            if resp.status_code == 200:
                return
        except (httpx.ConnectError, httpx.ReadError, httpx.TimeoutException) as e:
            last_error = e
        time.sleep(0.5)

    pytest.fail(
        f"Container on port {port} didn't become healthy within {timeout}s. "
        f"Last error: {last_error}"
    )


# ─── Module-Scoped Image Build ───

@pytest.fixture(scope="module", autouse=True)
def docker_image():
    """Build the Docker image once for all tests in this module."""
    _build_image()
    yield IMAGE_NAME
    # Optionally remove the image after all tests.
    # Leave it for now to speed up re-runs.


# ─── Test Classes ───

class TestContainerDefaults:
    """4.1 — Container starts with minimal config and passes healthcheck."""

    def test_default_startup_and_health(self):
        """Build and run with stacks volume only, verify /api/health returns 200."""
        port = 29494
        container_id = None
        try:
            container_id = _run_container(
                port=port,
                extra_volumes=[f"{E2E_STACKS}:/stacks:ro"],
                name="maparr-e2e-defaults",
            )

            _wait_for_healthy(port, timeout=30)

            resp = httpx.get(f"http://localhost:{port}/api/health", timeout=5.0)
            assert resp.status_code == 200

            body = resp.json()
            assert body["status"] == "ok"
            assert "version" in body
        finally:
            if container_id:
                _cleanup_container(container_id)


class TestCustomPort:
    """4.3 — Container respects MAPARR_PORT environment variable."""

    def test_custom_port(self):
        """Run with MAPARR_PORT=28080 and verify the app serves on that port."""
        host_port = 28080
        container_id = None
        try:
            container_id = _run_container(
                port=host_port,
                extra_env={"MAPARR_PORT": "28080"},
                extra_volumes=[f"{E2E_STACKS}:/stacks:ro"],
                name="maparr-e2e-custom-port",
            )

            _wait_for_healthy(host_port, timeout=30)

            resp = httpx.get(
                f"http://localhost:{host_port}/api/health", timeout=5.0
            )
            assert resp.status_code == 200
            assert resp.json()["status"] == "ok"
        finally:
            if container_id:
                _cleanup_container(container_id)


class TestReadOnlyStacks:
    """4.6 — Pipeline scan works with read-only stacks mount."""

    def test_pipeline_scan_readonly(self):
        """Mount stacks as :ro and verify pipeline scan finds media services."""
        port = 29495
        container_id = None
        try:
            container_id = _run_container(
                port=port,
                extra_volumes=[f"{E2E_STACKS}:/stacks:ro"],
                name="maparr-e2e-readonly",
            )

            _wait_for_healthy(port, timeout=30)

            # Trigger a pipeline scan
            resp = httpx.post(
                f"http://localhost:{port}/api/pipeline-scan",
                timeout=30.0,
            )
            assert resp.status_code == 200

            body = resp.json()
            # Pipeline scan should find media services in the test stacks
            assert "media_services" in body
            assert len(body["media_services"]) > 0
        finally:
            if container_id:
                _cleanup_container(container_id)
