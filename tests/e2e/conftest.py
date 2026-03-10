"""
E2E test fixtures for MapArr acceptance tests.

Starts a real MapArr server against synthetic test stacks,
provides Playwright browser pages pointed at it.
"""

import atexit
import os
import shutil
import subprocess
import socket
import sys
import textwrap
import time
from pathlib import Path

import pytest

# ─── Constants ───

E2E_PORT = 19494  # Avoid clashing with dev server on 9494
E2E_FIXTURES = Path(__file__).parent / "fixtures" / "stacks"
E2E_BASE_URL = f"http://localhost:{E2E_PORT}"
SERVER_STARTUP_TIMEOUT = 15  # seconds


# ─── Server Management ───

def _port_is_open(port: int) -> bool:
    """Check if a TCP port is accepting connections."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


@pytest.fixture(scope="session")
def maparr_server():
    """Start a MapArr server for the E2E test session.

    Points MAPARR_STACKS_PATH at the synthetic test stacks directory.
    Kills the server after all tests complete.
    """
    if _port_is_open(E2E_PORT):
        pytest.fail(
            f"Port {E2E_PORT} already in use — kill the existing process first"
        )

    env = os.environ.copy()
    env["MAPARR_STACKS_PATH"] = str(E2E_FIXTURES)
    env["MAPARR_PORT"] = str(E2E_PORT)
    # Point DOCKER_HOST at a non-existent endpoint so docker compose config
    # fails immediately instead of hanging for 30s. The resolver falls back
    # to manual YAML parsing which is all E2E tests need.
    env["DOCKER_HOST"] = "tcp://127.0.0.1:1"

    # Start uvicorn as a subprocess
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn",
            "backend.main:app",
            "--host", "127.0.0.1",
            "--port", str(E2E_PORT),
            "--log-level", "warning",
        ],
        env=env,
        cwd=str(Path(__file__).parent.parent.parent),  # project root
        # Use DEVNULL instead of PIPE to prevent Windows pipe buffer deadlocks.
        # When using PIPE, the server can hang if the buffer fills up and nobody
        # reads the output.
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Wait for server to be ready
    deadline = time.time() + SERVER_STARTUP_TIMEOUT
    while time.time() < deadline:
        if _port_is_open(E2E_PORT):
            break
        if proc.poll() is not None:
            stderr = proc.stderr.read().decode() if proc.stderr else ""
            pytest.fail(f"MapArr server exited early: {stderr}")
        time.sleep(0.3)
    else:
        proc.kill()
        pytest.fail(f"MapArr server didn't start within {SERVER_STARTUP_TIMEOUT}s")

    # Register atexit handler so the server gets killed even if pytest
    # terminates ungracefully (e.g. KeyboardInterrupt, unhandled exception).
    # This prevents stale processes holding port 19494 between test runs.
    def _cleanup_server():
        try:
            proc.kill()
        except Exception:
            pass

    atexit.register(_cleanup_server)

    yield {"url": E2E_BASE_URL, "port": E2E_PORT, "process": proc}

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()

    # Unregister atexit handler after clean shutdown
    atexit.unregister(_cleanup_server)


@pytest.fixture(scope="session")
def base_url(maparr_server):
    """Base URL for the running MapArr server."""
    return maparr_server["url"]


# ─── Playwright Fixtures ───
# pytest-playwright provides `page`, `browser`, `context` automatically
# when pytest-playwright is installed. We just need to configure the base URL.

@pytest.fixture(scope="session")
def browser_context_args(browser_context_args, base_url):
    """Configure Playwright browser context with MapArr base URL."""
    return {
        **browser_context_args,
        "base_url": base_url,
    }


# ─── Test Data Helpers ───

@pytest.fixture(scope="session")
def stacks_dir():
    """Path to the synthetic E2E test stacks directory."""
    return E2E_FIXTURES


@pytest.fixture
def clean_stacks(stacks_dir):
    """Reset any .bak files created by Apply Fix tests.

    Runs after each test that modifies compose files.
    """
    yield stacks_dir
    # Clean up .bak files
    for bak in stacks_dir.rglob("*.bak"):
        bak.unlink(missing_ok=True)
    # Restore any modified compose files from git
    subprocess.run(
        ["git", "checkout", "--", str(stacks_dir)],
        cwd=str(stacks_dir.parent.parent.parent),
        capture_output=True,
    )
