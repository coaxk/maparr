# Pre-Beta Implementation Plan — MapArr v1.5.2

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement all Stage 1 (Before Private Beta) items from the Global Task List — security hardening, core UX fixes, UX features, and code hygiene — delivering a hardened, polished MapArr ready for beta testers.

**Architecture:** Four sequential slices: (1) Security + hygiene — pure backend, hardens foundation; (2) Core UX fixes — backend endpoints + frontend for undo/revert, error messages, warning dismiss; (3) UX features — frontend-heavy first-run wizard, collapsible sections, Docker restart, diagnostic export, icon fallback; (4) Docs + release prep. Each slice is independently testable and committable.

**Tech Stack:** Python 3.11 / FastAPI / PyYAML (backend), Vanilla JS / HTML / CSS (frontend), pytest / Playwright / httpx TestClient (testing)

**Key Files Reference:**
- `backend/main.py` — FastAPI app, routes, RateLimiter (:52), SSEConnectionLimiter (:170), `_is_path_within_stacks()` (:216), `_get_stacks_root()` (:208), `COMPOSE_FILENAMES` (:243)
- `backend/resolver.py` — `_try_docker_compose_config()` (:139), subprocess at (:154)
- `backend/apply_multi.py` — `COMPOSE_FILENAMES` (:32), `apply_fixes_batch()` (:96)
- `backend/redeploy.py` — Docker compose subprocess pattern
- `frontend/app.js` — `getServiceIconUrl()` (:909), `friendlyError()` (:2760), `generateDiagnosticMarkdown()` (:7635), first-launch (:411), Other Stacks (:747)
- `tests/conftest.py` — `_clear_session` (:19), `client` fixture, `make_stack`
- `tests/e2e/conftest.py` — `maparr_server` (:37), port 19494

**Test Commands:**
```bash
# Unit tests
pytest tests/ --ignore=tests/e2e -v -p no:capture

# API contracts
pytest tests/e2e/test_api_contracts.py -v -p no:capture

# Playwright (components + journeys)
pytest tests/e2e/test_components.py tests/e2e/test_journeys.py -v

# All tests
pytest tests/ -v -p no:capture

# Single test
pytest tests/test_file.py::TestClass::test_name -v -p no:capture
```

---

# SLICE 1: Security + Hygiene Foundation

---

## Task 1: S3 — DOCKER_HOST Allowlist

**Files:**
- Modify: `backend/resolver.py:139` (add validation before subprocess)
- Modify: `docker-entrypoint.sh:51-65` (add validation)
- Test: `tests/test_resolver_security.py` (new file)

**Step 1: Write the failing tests**

Create `tests/test_resolver_security.py`:

```python
"""Tests for DOCKER_HOST validation — SSRF prevention.

Grok Elder Council finding (HIGH): resolver.py blindly honours any DOCKER_HOST URI.
A malicious/misconfigured env can point subprocess calls at internal services.
Guard: only allow unix://, tcp://127.*, tcp://localhost, tcp://socket-proxy patterns.
"""
import pytest
from unittest.mock import patch
from backend.resolver import _validate_docker_host


class TestDockerHostAllowlist:
    """DOCKER_HOST environment variable validation."""

    def test_empty_is_allowed(self):
        """Empty/None DOCKER_HOST means use default socket."""
        assert _validate_docker_host(None) is None
        assert _validate_docker_host("") is None

    def test_unix_socket_allowed(self):
        """Standard unix socket paths are always allowed."""
        assert _validate_docker_host("unix:///var/run/docker.sock") == "unix:///var/run/docker.sock"
        assert _validate_docker_host("unix:///run/docker.sock") == "unix:///run/docker.sock"

    def test_tcp_loopback_allowed(self):
        """TCP to localhost/127.x is allowed (local socket proxy)."""
        assert _validate_docker_host("tcp://127.0.0.1:2375") == "tcp://127.0.0.1:2375"
        assert _validate_docker_host("tcp://127.0.0.1:2376") == "tcp://127.0.0.1:2376"
        assert _validate_docker_host("tcp://localhost:2375") == "tcp://localhost:2375"

    def test_tcp_socket_proxy_allowed(self):
        """Common socket proxy container names are allowed."""
        assert _validate_docker_host("tcp://socket-proxy:2375") == "tcp://socket-proxy:2375"

    def test_tcp_dotlocal_allowed(self):
        """*.local hostnames are allowed (mDNS/local network)."""
        assert _validate_docker_host("tcp://docker.local:2375") == "tcp://docker.local:2375"

    def test_arbitrary_tcp_denied(self):
        """Arbitrary TCP hosts are SSRF vectors — must be rejected."""
        assert _validate_docker_host("tcp://192.168.1.100:2375") is None
        assert _validate_docker_host("tcp://redis:6379") is None
        assert _validate_docker_host("tcp://internal-service:8080") is None
        assert _validate_docker_host("tcp://10.0.0.1:2375") is None

    def test_non_docker_schemes_denied(self):
        """Non-docker URI schemes must be rejected."""
        assert _validate_docker_host("http://evil.com") is None
        assert _validate_docker_host("ssh://root@host") is None
        assert _validate_docker_host("ftp://files.local") is None

    def test_credential_uris_denied(self):
        """URIs with embedded credentials must be rejected."""
        assert _validate_docker_host("tcp://user:pass@host:2375") is None

    def test_denied_value_logs_warning(self, caplog):
        """Denied DOCKER_HOST values should log a sanitised warning."""
        import logging
        with caplog.at_level(logging.WARNING):
            _validate_docker_host("tcp://evil-internal:6379")
        assert "DOCKER_HOST" in caplog.text
        # Must NOT log the full URI (could contain credentials)
        assert "evil-internal:6379" not in caplog.text
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_resolver_security.py -v -p no:capture`
Expected: FAIL — `_validate_docker_host` does not exist yet

**Step 3: Implement `_validate_docker_host()` in resolver.py**

Add at top of `backend/resolver.py` (before `_try_docker_compose_config`):

```python
import re
import logging

logger = logging.getLogger("maparr.resolver")

# Allowed DOCKER_HOST patterns — everything else is SSRF risk (Grok Elder Council HIGH)
_DOCKER_HOST_ALLOWED = re.compile(
    r"^("
    r"unix://.*"                          # Any unix socket path
    r"|tcp://127\.\d{1,3}\.\d{1,3}\.\d{1,3}(:\d+)?"  # tcp://127.x.x.x
    r"|tcp://localhost(:\d+)?"            # tcp://localhost
    r"|tcp://socket-proxy(:\d+)?"         # Common socket proxy name
    r"|tcp://[a-zA-Z0-9._-]+\.local(:\d+)?"  # *.local mDNS names
    r")$"
)


def _validate_docker_host(value):
    """Validate DOCKER_HOST env var against allowlist.

    Returns the value unchanged if allowed, None if denied or empty.
    Denied values log a WARNING with a sanitised message (no credentials leaked).
    """
    if not value:
        return None
    if _DOCKER_HOST_ALLOWED.match(value):
        return value
    # Log sanitised — only scheme + "denied", never the full URI
    scheme = value.split("://")[0] if "://" in value else "unknown"
    logger.warning(
        "DOCKER_HOST denied: %s:// URI not in allowlist. "
        "Allowed: unix://, tcp://127.*/localhost/socket-proxy/*.local. "
        "Falling back to manual compose parsing.",
        scheme,
    )
    return None
```

Then in `_try_docker_compose_config()` at line ~154, add the guard before subprocess:

```python
# Validate DOCKER_HOST before any subprocess call (SSRF prevention)
docker_host = os.environ.get("DOCKER_HOST", "")
if docker_host and _validate_docker_host(docker_host) is None:
    logger.info("DOCKER_HOST rejected by allowlist — skipping docker compose config")
    return None  # Caller falls back to manual parsing
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_resolver_security.py -v -p no:capture`
Expected: ALL PASS

**Step 5: Run full test suite for regression**

Run: `pytest tests/ --ignore=tests/e2e -v -p no:capture`
Expected: All existing tests pass + new tests pass

**Step 6: Update docker-entrypoint.sh**

Add validation near line 51:

```bash
# Validate DOCKER_HOST if set (SSRF prevention — Grok Elder Council)
if [ -n "$DOCKER_HOST" ]; then
    case "$DOCKER_HOST" in
        unix://*|tcp://127.*|tcp://localhost*|tcp://socket-proxy*|tcp://*.local*)
            echo "[maparr] DOCKER_HOST=$DOCKER_HOST (allowed)"
            ;;
        *)
            echo "[maparr] WARNING: DOCKER_HOST=$( echo "$DOCKER_HOST" | sed 's|://.*|://[REDACTED]|' ) not in allowlist, unsetting"
            unset DOCKER_HOST
            ;;
    esac
fi
```

**Step 7: Commit**

```bash
git add backend/resolver.py docker-entrypoint.sh tests/test_resolver_security.py
git commit -m "feat(security): add DOCKER_HOST allowlist to prevent SSRF

Validates DOCKER_HOST env var against allowlist before subprocess calls.
Allowed: unix://, tcp://127.*/localhost/socket-proxy/*.local.
Denied values log sanitised warning and fall back to manual parsing.
Also guards docker-entrypoint.sh — unsets invalid DOCKER_HOST on startup.

Elder Council: Grok HIGH finding.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 2: S4 — Trusted Proxy IP Handling

**Files:**
- Modify: `backend/main.py:99` (RateLimiter.check), `backend/main.py:170` (SSEConnectionLimiter)
- Test: `tests/test_proxy_ip.py` (new file)

**Step 1: Write the failing tests**

Create `tests/test_proxy_ip.py`:

```python
"""Tests for trusted proxy IP resolution — rate limiter bypass prevention.

Grok Elder Council finding (HIGH): request.client.host behind reverse proxy
returns proxy IP, not real client. Rate limiter bypassed via X-Forwarded-For
spoofing or IPv6 ::1.
"""
import pytest
from unittest.mock import MagicMock
from backend.main import _get_client_ip


class TestGetClientIp:
    """Client IP resolution with proxy awareness."""

    def test_direct_connection_uses_client_host(self):
        """Without trusted proxies, use request.client.host as-is."""
        request = MagicMock()
        request.client.host = "192.168.1.50"
        request.headers = {}
        assert _get_client_ip(request) == "192.168.1.50"

    def test_forwarded_for_ignored_without_trust(self):
        """X-Forwarded-For is ignored when no proxies are trusted."""
        request = MagicMock()
        request.client.host = "172.18.0.2"
        request.headers = {"x-forwarded-for": "1.2.3.4, 172.18.0.2"}
        # No trusted proxies configured — ignore header
        assert _get_client_ip(request) == "172.18.0.2"

    def test_forwarded_for_with_trusted_proxy(self):
        """With trusted proxy, extract real client from X-Forwarded-For."""
        request = MagicMock()
        request.client.host = "172.18.0.2"  # This is the proxy
        request.headers = {"x-forwarded-for": "203.0.113.50, 172.18.0.2"}
        assert _get_client_ip(request, trusted_proxies={"172.18.0.2"}) == "203.0.113.50"

    def test_chained_proxies(self):
        """Multiple proxies — use rightmost untrusted IP."""
        request = MagicMock()
        request.client.host = "10.0.0.1"
        request.headers = {"x-forwarded-for": "203.0.113.50, 10.0.0.2, 10.0.0.1"}
        trusted = {"10.0.0.1", "10.0.0.2"}
        assert _get_client_ip(request, trusted_proxies=trusted) == "203.0.113.50"

    def test_all_trusted_falls_back_to_leftmost(self):
        """If all IPs in chain are trusted, use the leftmost (origin)."""
        request = MagicMock()
        request.client.host = "10.0.0.1"
        request.headers = {"x-forwarded-for": "10.0.0.3, 10.0.0.2"}
        trusted = {"10.0.0.1", "10.0.0.2", "10.0.0.3"}
        assert _get_client_ip(request, trusted_proxies=trusted) == "10.0.0.3"

    def test_ipv6_localhost_normalised(self):
        """IPv6 ::1 should be treated as 127.0.0.1."""
        request = MagicMock()
        request.client.host = "::1"
        request.headers = {}
        assert _get_client_ip(request) == "127.0.0.1"

    def test_empty_forwarded_for_uses_client_host(self):
        """Empty X-Forwarded-For header falls back to client.host."""
        request = MagicMock()
        request.client.host = "192.168.1.50"
        request.headers = {"x-forwarded-for": ""}
        assert _get_client_ip(request, trusted_proxies={"172.18.0.2"}) == "192.168.1.50"

    def test_no_client_returns_unknown(self):
        """Missing client info returns 'unknown'."""
        request = MagicMock()
        request.client = None
        request.headers = {}
        assert _get_client_ip(request) == "unknown"
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_proxy_ip.py -v -p no:capture`
Expected: FAIL — `_get_client_ip` does not exist

**Step 3: Implement `_get_client_ip()` in main.py**

Add near the top of `backend/main.py` (after imports, before RateLimiter):

```python
# Trusted proxy IPs — parsed from MAPARR_TRUSTED_PROXIES env var
_TRUSTED_PROXIES = frozenset(
    ip.strip() for ip in os.environ.get("MAPARR_TRUSTED_PROXIES", "").split(",")
    if ip.strip()
)
if _TRUSTED_PROXIES:
    logger.info("Trusted proxies configured: %s", ", ".join(sorted(_TRUSTED_PROXIES)))


def _get_client_ip(request, trusted_proxies=None):
    """Extract real client IP, respecting X-Forwarded-For when proxies are trusted.

    Without trusted proxies: returns request.client.host directly.
    With trusted proxies: walks X-Forwarded-For right-to-left, returns
    rightmost IP not in the trust set. Normalises IPv6 ::1 to 127.0.0.1.
    """
    if trusted_proxies is None:
        trusted_proxies = _TRUSTED_PROXIES

    if not request.client:
        return "unknown"

    client_ip = request.client.host

    # Normalise IPv6 loopback
    if client_ip == "::1":
        client_ip = "127.0.0.1"

    if not trusted_proxies:
        return client_ip

    forwarded = request.headers.get("x-forwarded-for", "")
    if not forwarded:
        return client_ip

    # Parse chain: "client, proxy1, proxy2" — rightmost untrusted is real client
    ips = [ip.strip() for ip in forwarded.split(",") if ip.strip()]
    if not ips:
        return client_ip

    # Walk right to left, skip trusted proxies
    for ip in reversed(ips):
        normalised = "127.0.0.1" if ip == "::1" else ip
        if normalised not in trusted_proxies:
            return normalised

    # All IPs are trusted — use leftmost as origin
    return ips[0]
```

Then update `RateLimiter.check()` (line ~99) to use `_get_client_ip(request)` instead of `request.client.host`.
Update `SSEConnectionLimiter` (line ~170) similarly.

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_proxy_ip.py -v -p no:capture`
Expected: ALL PASS

**Step 5: Run full suite**

Run: `pytest tests/ --ignore=tests/e2e -v -p no:capture`
Expected: All pass

**Step 6: Update CLAUDE.md env vars table**

Add `MAPARR_TRUSTED_PROXIES` to the Environment Variables section.

**Step 7: Commit**

```bash
git add backend/main.py tests/test_proxy_ip.py CLAUDE.md
git commit -m "feat(security): add trusted proxy IP handling for rate limiter

New _get_client_ip() resolves real client IP from X-Forwarded-For when
MAPARR_TRUSTED_PROXIES is configured. Prevents rate limiter bypass behind
reverse proxies (Traefik, Caddy, Nginx). Normalises IPv6 ::1 to 127.0.0.1.

Elder Council: Grok HIGH finding.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 3: S5 — Force Write Boundary

**Files:**
- Modify: `backend/main.py:216` (`_is_path_within_stacks`)
- Test: `tests/test_write_boundary.py` (new file)

**Step 1: Write the failing test**

Create `tests/test_write_boundary.py`:

```python
"""Tests for write boundary enforcement without MAPARR_STACKS_PATH.

Grok Elder Council finding (MEDIUM): bare-metal dev runs with no env var
allow apply-fix to write to any path. Must return 403 when no root is set.
"""
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient
from backend.main import app, _session, _is_path_within_stacks


class TestWriteBoundaryEnforcement:
    """Write endpoints must refuse when no stacks root is configured."""

    def setup_method(self):
        _session["parsed_error"] = None
        _session["selected_stack"] = None
        _session["pipeline"] = None
        _session.pop("custom_stacks_path", None)

    def test_is_path_within_stacks_no_root_denies_writes(self):
        """require_root=True must return False when no root is configured."""
        with patch.dict("os.environ", {}, clear=True):
            _session.pop("custom_stacks_path", None)
            assert _is_path_within_stacks("/any/path", require_root=True) is False

    def test_is_path_within_stacks_no_root_allows_reads(self):
        """require_root=False should still work without a root (read operations)."""
        with patch.dict("os.environ", {}, clear=True):
            _session.pop("custom_stacks_path", None)
            # Reads don't require root — should not crash
            result = _is_path_within_stacks("/any/path", require_root=False)
            assert isinstance(result, bool)

    def test_apply_fix_403_without_stacks_path(self):
        """POST /api/apply-fix must return 403 when no stacks root."""
        client = TestClient(app)
        with patch.dict("os.environ", {}, clear=True):
            _session.pop("custom_stacks_path", None)
            response = client.post("/api/apply-fix", json={
                "compose_file_path": "/tmp/test/compose.yaml",
                "corrected_yaml": "services:\n  test:\n    image: test\n",
            })
            assert response.status_code == 403, (
                f"Expected 403 without MAPARR_STACKS_PATH, got {response.status_code}"
            )
            assert "MAPARR_STACKS_PATH" in response.json().get("detail", "")
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_write_boundary.py -v -p no:capture`
Expected: FAIL — the 403 check may already partially work but the message won't match

**Step 3: Update `_is_path_within_stacks()` in main.py**

At line ~216, modify the function. When `require_root=True` and no root is configured:

```python
def _is_path_within_stacks(path_str, require_root=False):
    """Check if path is within the configured stacks directory.

    When require_root=True (write operations), returns False if no stacks root
    is configured. This prevents writes to arbitrary paths in bare-metal dev runs.
    """
    root = _get_stacks_root()
    if require_root and root is None:
        logger.warning("Write operation denied — no MAPARR_STACKS_PATH configured")
        return False
    if root is None:
        return True  # Read operations without root — allow (existing behaviour)
    # ... rest of existing validation ...
```

Also update the 403 responses in apply-fix/apply-fixes endpoints to include `MAPARR_STACKS_PATH` in the detail message:

```python
detail="Path is outside the configured stacks directory. Set MAPARR_STACKS_PATH to enable Apply Fix."
```

**Step 4: Add startup warning**

Near the end of `main.py`'s startup section, add:

```python
if not os.environ.get("MAPARR_STACKS_PATH"):
    logger.warning(
        "MAPARR_STACKS_PATH not set — write endpoints (Apply Fix, Revert) are disabled. "
        "Set MAPARR_STACKS_PATH to the directory containing your compose files."
    )
```

**Step 5: Run tests**

Run: `pytest tests/test_write_boundary.py tests/ --ignore=tests/e2e -v -p no:capture`
Expected: ALL PASS

**Step 6: Commit**

```bash
git add backend/main.py tests/test_write_boundary.py
git commit -m "feat(security): enforce write boundary without MAPARR_STACKS_PATH

Write endpoints (apply-fix, apply-fixes) now return 403 when no stacks root
is configured. Prevents bare-metal dev runs from writing to arbitrary paths.
Startup log warns when MAPARR_STACKS_PATH is not set.

Elder Council: Grok MEDIUM finding.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 4: C4 — SSE Generator Hard Timeout

**Files:**
- Modify: `backend/main.py:1283` (SSE event_generator)
- Test: `tests/test_sse_timeout.py` (new file)

**Step 1: Write the failing test**

Create `tests/test_sse_timeout.py`:

```python
"""Tests for SSE generator hard timeout.

Grok Elder Council finding (LOW): /api/logs/stream generator runs forever.
Add 5-minute hard timeout so connections are recycled.
"""
import pytest
import time
from unittest.mock import patch, AsyncMock
from backend.main import SSE_HARD_TIMEOUT_SECONDS


def test_sse_timeout_constant_exists():
    """SSE hard timeout constant must be defined."""
    assert SSE_HARD_TIMEOUT_SECONDS == 300, "SSE hard timeout should be 5 minutes (300 seconds)"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_sse_timeout.py -v -p no:capture`
Expected: FAIL — `SSE_HARD_TIMEOUT_SECONDS` does not exist

**Step 3: Implement**

In `backend/main.py`, add constant near the top:

```python
SSE_HARD_TIMEOUT_SECONDS = 300  # 5-minute hard timeout on SSE connections (Grok)
```

In the `event_generator()` async function (line ~1283), add timeout check:

```python
async def event_generator():
    start_time = time.monotonic()
    try:
        while True:
            # Hard timeout — force client to reconnect (Grok Elder Council)
            if time.monotonic() - start_time > SSE_HARD_TIMEOUT_SECONDS:
                yield {"event": "timeout", "data": "Connection recycled after 5 minutes"}
                break
            # ... existing queue.get logic ...
    finally:
        # ... existing cleanup ...
```

**Step 4: Run tests**

Run: `pytest tests/test_sse_timeout.py tests/ --ignore=tests/e2e -v -p no:capture`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add backend/main.py tests/test_sse_timeout.py
git commit -m "feat(security): add 5-minute hard timeout to SSE log stream

SSE generator now breaks after 300 seconds, yielding a timeout event.
Frontend SSE client already has reconnect with exponential backoff.

Elder Council: Grok LOW finding.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 5: H1 + H2 — Code Hygiene

**Files:**
- Modify: Multiple backend files (unused imports)
- Modify: `backend/main.py:243` (COMPOSE_FILENAMES → import from resolver)
- Modify: `backend/apply_multi.py:32` (COMPOSE_FILENAMES → import from resolver)
- Modify: `backend/resolver.py` (add canonical COMPOSE_FILENAMES)
- Modify: `frontend/app.js:2760` (remove friendlyError if fully superseded by C2)

**Step 1: Run ruff to find unused imports**

Run: `cd /c/Projects/maparr && python -m ruff check --select F401 backend/`

Fix all reported unused imports.

**Step 2: Consolidate COMPOSE_FILENAMES**

Move the canonical definition to `backend/resolver.py` (it owns compose file knowledge):

```python
# Canonical compose filename whitelist — single source of truth
COMPOSE_FILENAMES = {"docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"}
```

In `backend/main.py`, replace the local definition with:
```python
from backend.resolver import COMPOSE_FILENAMES
```

In `backend/apply_multi.py`, replace the local definition with:
```python
from backend.resolver import COMPOSE_FILENAMES
```

**Step 3: Consolidate _get_stacks_root()**

Identify the 3 locations. Keep the one in `main.py` as canonical. Others import from main.
(If circular import risk, extract to a `backend/config.py` shared module.)

**Step 4: Remove dead cross_stack imports**

Search for `from backend.cross_stack import` or `import cross_stack` in files where pipeline supersedes. Remove unused imports. Do NOT remove cross_stack.py itself — it may still be used in legacy analysis paths.

**Step 5: Run full test suite**

Run: `pytest tests/ --ignore=tests/e2e -v -p no:capture`
Expected: ALL PASS — hygiene changes must not break anything

**Step 6: Commit**

```bash
git add backend/
git commit -m "chore: code hygiene — unused imports, deduplicate constants

Remove unused imports (ruff F401). Consolidate COMPOSE_FILENAMES to single
definition in resolver.py. Consolidate _get_stacks_root() to single source.
Remove dead cross_stack.py imports where pipeline supersedes.

Elder Council: DeepSeek + Grok findings.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 6: Slice 1 Review Checkpoint

**Step 1: Run ALL tests**

```bash
pytest tests/ -v -p no:capture
pytest tests/e2e/test_api_contracts.py -v -p no:capture
```

Expected: All pass, zero regressions.

**Step 2: Security verification**

Manually verify:
- `DOCKER_HOST=tcp://evil:1234` → falls back to manual parsing (check logs)
- Apply Fix without `MAPARR_STACKS_PATH` → 403 with helpful message
- SSE stream disconnects after 5 minutes

**Step 3: Code review**

Use `superpowers:requesting-code-review` to review Slice 1 changes.

---

# SLICE 2: Core UX Fixes

---

## Task 7: C1 — Undo/Revert Button (Backend)

**Files:**
- Modify: `backend/main.py` (new endpoint, modify apply-fix response)
- Test: `tests/test_revert_fix.py` (new file)

**Step 1: Write the failing tests**

Create `tests/test_revert_fix.py`:

```python
"""Tests for the revert-fix endpoint — .bak file restoration.

Gemini + Grok Elder Council: expose .bak restoration in Apply Fix UI.
Backend creates backups before every write. This endpoint restores them.
"""
import os
import pytest
from fastapi.testclient import TestClient
from backend.main import app, _session


class TestRevertFix:
    """POST /api/revert-fix — restore .bak backup."""

    def setup_method(self):
        _session["parsed_error"] = None
        _session["selected_stack"] = None
        _session["pipeline"] = None
        _session.pop("custom_stacks_path", None)

    def test_revert_restores_backup(self, tmp_path):
        """Revert should swap .bak back to original file."""
        compose = tmp_path / "compose.yaml"
        compose.write_text("services:\n  fixed: {image: fixed}\n")
        backup = tmp_path / "compose.yaml.bak"
        backup.write_text("services:\n  original: {image: original}\n")

        _session["custom_stacks_path"] = str(tmp_path)
        client = TestClient(app)
        response = client.post("/api/revert-fix", json={
            "compose_file_path": str(compose),
        })
        assert response.status_code == 200, f"Revert failed: {response.json()}"
        assert response.json()["status"] == "reverted"
        assert compose.read_text() == "services:\n  original: {image: original}\n"

    def test_revert_missing_backup_returns_404(self, tmp_path):
        """No .bak file → 404 with clear message."""
        compose = tmp_path / "compose.yaml"
        compose.write_text("services:\n  test: {image: test}\n")

        _session["custom_stacks_path"] = str(tmp_path)
        client = TestClient(app)
        response = client.post("/api/revert-fix", json={
            "compose_file_path": str(compose),
        })
        assert response.status_code == 404
        assert "backup" in response.json()["detail"].lower()

    def test_revert_outside_stacks_returns_403(self, tmp_path):
        """Path outside stacks boundary → 403."""
        _session["custom_stacks_path"] = str(tmp_path / "allowed")
        client = TestClient(app)
        response = client.post("/api/revert-fix", json={
            "compose_file_path": "/etc/shadow",
        })
        assert response.status_code == 403

    def test_revert_nonexistent_file_returns_404(self, tmp_path):
        """Compose file doesn't exist → 404."""
        _session["custom_stacks_path"] = str(tmp_path)
        client = TestClient(app)
        response = client.post("/api/revert-fix", json={
            "compose_file_path": str(tmp_path / "nonexistent.yaml"),
        })
        assert response.status_code == 404


class TestApplyFixReturnsBackupInfo:
    """Apply fix response must include has_backup for frontend button."""

    def test_apply_fix_response_has_backup_field(self, tmp_path):
        """After apply-fix, response includes has_backup: true."""
        compose = tmp_path / "compose.yaml"
        compose.write_text("services:\n  test:\n    image: test\n")

        _session["custom_stacks_path"] = str(tmp_path)
        client = TestClient(app)
        response = client.post("/api/apply-fix", json={
            "compose_file_path": str(compose),
            "corrected_yaml": "services:\n  test:\n    image: fixed\n",
        })
        if response.status_code == 200:
            data = response.json()
            assert "has_backup" in data, "apply-fix response must include has_backup field"
            assert data["has_backup"] is True
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_revert_fix.py -v -p no:capture`
Expected: FAIL — no `/api/revert-fix` endpoint, no `has_backup` field

**Step 3: Implement revert endpoint in main.py**

Add new endpoint after the apply-fixes endpoint:

```python
@app.post("/api/revert-fix")
async def revert_fix(request: Request):
    """Restore a compose file from its .bak backup.

    Validates path within stacks boundary, checks .bak exists,
    swaps backup to original via os.replace() for atomicity.
    """
    _check_rate_limit(request, "revert-fix", limit=10)

    body = await request.json()
    compose_path = body.get("compose_file_path", "")

    if not compose_path:
        return JSONResponse(status_code=400, content={"detail": "compose_file_path is required"})

    if not _is_path_within_stacks(compose_path, require_root=True):
        return JSONResponse(
            status_code=403,
            content={"detail": "Path is outside the configured stacks directory."},
        )

    backup_path = compose_path + ".bak"

    if not os.path.isfile(backup_path):
        return JSONResponse(
            status_code=404,
            content={"detail": "No backup file found. Cannot revert."},
        )

    if not os.path.isfile(compose_path):
        return JSONResponse(
            status_code=404,
            content={"detail": "Compose file not found at the specified path."},
        )

    try:
        os.replace(backup_path, compose_path)
        logger.info("Reverted %s from backup", _relative_path_display(compose_path))
        return {"status": "reverted", "compose_file": compose_path}
    except OSError as exc:
        return JSONResponse(
            status_code=500,
            content=_json_error_detail("revert", exc, compose_path),
        )
```

Also modify the apply-fix endpoint response to include `has_backup`:

```python
# In the apply-fix success response, add:
"has_backup": os.path.isfile(compose_path + ".bak"),
```

**Step 4: Run tests**

Run: `pytest tests/test_revert_fix.py tests/ --ignore=tests/e2e -v -p no:capture`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add backend/main.py tests/test_revert_fix.py
git commit -m "feat: add revert-fix endpoint for .bak restoration

New POST /api/revert-fix swaps .bak backup back to original compose file
using os.replace() for atomicity. Path validated within stacks boundary.
Apply-fix response now includes has_backup field for frontend button.

Elder Council: Gemini + Grok finding.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 8: C1 — Undo/Revert Button (Frontend)

**Files:**
- Modify: `frontend/app.js` (add revert button in post-apply UI)

**Step 1: Implement revert button in frontend**

In `app.js`, after the Apply Fix success handler (where the success banner is rendered), add:

```javascript
// Revert button — shown when backend confirms backup exists
if (result.has_backup) {
    const revertBtn = document.createElement("button");
    revertBtn.className = "btn btn-subtle btn-revert";
    revertBtn.textContent = "Revert to Backup";
    revertBtn.addEventListener("click", async () => {
        if (!confirm("Revert this file to its backup? Your applied fix will be undone.")) return;
        revertBtn.disabled = true;
        revertBtn.textContent = "Reverting...";
        try {
            const resp = await fetch("/api/revert-fix", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({compose_file_path: result.compose_file}),
                signal: AbortSignal.timeout(10000),
            });
            if (!resp.ok) {
                const err = await resp.json();
                showSimpleToast(err.detail || "Revert failed", "error");
                revertBtn.disabled = false;
                revertBtn.textContent = "Revert to Backup";
                return;
            }
            showSimpleToast("Reverted to backup successfully", "success");
            // Trigger pipeline rescan to refresh health
            await runPipelineScan();
        } catch (e) {
            showSimpleToast("Revert failed — " + (e.message || "unknown error"), "error");
            revertBtn.disabled = false;
            revertBtn.textContent = "Revert to Backup";
        }
    });
    // Append after the success message in the apply-fix result area
    successBanner.appendChild(revertBtn);
}
```

**Step 2: Add CSS for revert button**

In `frontend/styles.css`:

```css
.btn-revert {
    margin-left: 12px;
    color: var(--color-warning);
    border-color: var(--color-warning);
}
.btn-revert:hover {
    background: rgba(210, 153, 34, 0.1);
}
```

**Step 3: Manual test**

1. Start MapArr with test stacks
2. Analyze a stack with path conflicts (A01)
3. Apply Fix → verify "Revert to Backup" button appears
4. Click Revert → verify file is restored and dashboard rescans
5. Verify button is hidden when no .bak exists

**Step 4: Commit**

```bash
git add frontend/app.js frontend/styles.css
git commit -m "feat(ui): add Revert to Backup button after Apply Fix

Shows 'Revert to Backup' button in post-apply success banner when
backend confirms .bak exists. Calls /api/revert-fix, triggers pipeline
rescan on success. Confirmation dialog prevents accidental reverts.

Elder Council: Gemini + Grok finding.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 9: C2 — Specific Error Messages (Backend)

**Files:**
- Modify: `backend/main.py` (analyze endpoint error handling)
- Test: `tests/test_error_categorisation.py` (new file)

**Step 1: Write failing tests**

Create `tests/test_error_categorisation.py`:

```python
"""Tests for structured analysis error messages.

DeepSeek + Gemini + Grok: replace generic 'check log panel' with
type-specific actionable messages.
"""
import yaml
import pytest
from backend.main import _categorize_analysis_error


class TestCategorizeAnalysisError:
    """Map exceptions to structured error responses."""

    def test_yaml_error(self):
        """YAML parse errors include line number."""
        try:
            yaml.safe_load("invalid: yaml: content: [")
        except yaml.YAMLError as exc:
            result = _categorize_analysis_error(exc, "/stacks/test/compose.yaml")
            assert result["type"] == "yaml_parse"
            assert "line" in result or "YAML" in result["message"]

    def test_file_not_found(self):
        """Missing file errors include the path."""
        exc = FileNotFoundError("[Errno 2] No such file or directory: '/stacks/test/compose.yaml'")
        result = _categorize_analysis_error(exc, "/stacks/test/compose.yaml")
        assert result["type"] == "file_missing"
        assert "compose" in result["message"].lower() or "found" in result["message"].lower()

    def test_permission_denied(self):
        """Permission errors include PUID/PGID hint."""
        exc = PermissionError("[Errno 13] Permission denied: '/stacks/test/compose.yaml'")
        result = _categorize_analysis_error(exc, "/stacks/test/compose.yaml")
        assert result["type"] == "permission_denied"
        assert "permission" in result["message"].lower()

    def test_docker_timeout(self):
        """Docker timeout errors include DOCKER_HOST hint."""
        exc = TimeoutError("docker compose config timed out")
        result = _categorize_analysis_error(exc, "/stacks/test/compose.yaml")
        assert result["type"] == "docker_unreachable"
        assert "docker" in result["message"].lower()

    def test_unknown_error_is_generic(self):
        """Unknown exceptions get generic type but still no raw str(e)."""
        exc = RuntimeError("something unexpected")
        result = _categorize_analysis_error(exc, "/stacks/test/compose.yaml")
        assert result["type"] == "unknown"
        assert "something unexpected" not in result["message"], "Raw exception must not leak"

    def test_all_results_have_required_fields(self):
        """Every error result must have type and message fields."""
        for exc in [
            yaml.YAMLError("bad yaml"),
            FileNotFoundError("missing"),
            PermissionError("denied"),
            TimeoutError("timeout"),
            RuntimeError("unknown"),
        ]:
            result = _categorize_analysis_error(exc, "/stacks/test/compose.yaml")
            assert "type" in result, f"Missing 'type' for {type(exc).__name__}"
            assert "message" in result, f"Missing 'message' for {type(exc).__name__}"
```

**Step 2: Run tests → FAIL**

**Step 3: Implement `_categorize_analysis_error()` in main.py**

```python
def _categorize_analysis_error(exc, compose_path=""):
    """Map analysis exceptions to structured, actionable error responses.

    Never leaks raw exception text. Each type includes user-facing guidance.
    """
    display_path = _relative_path_display(compose_path) if compose_path else "compose file"

    if isinstance(exc, yaml.YAMLError):
        line_info = ""
        if hasattr(exc, "problem_mark") and exc.problem_mark:
            line_info = f" (line {exc.problem_mark.line + 1})"
        return {
            "type": "yaml_parse",
            "message": f"YAML syntax error in {display_path}{line_info}. Check for missing colons, incorrect indentation, or unclosed brackets.",
        }

    if isinstance(exc, FileNotFoundError):
        return {
            "type": "file_missing",
            "message": f"Compose file not found at {display_path}. Verify the file exists and the path is correct.",
        }

    if isinstance(exc, PermissionError):
        return {
            "type": "permission_denied",
            "message": f"Cannot read {display_path} — permission denied. Check that the MapArr process user (PUID/PGID) has read access to this file.",
        }

    if isinstance(exc, (TimeoutError, subprocess.TimeoutExpired)):
        return {
            "type": "docker_unreachable",
            "message": "Docker daemon did not respond in time. Check that Docker is running and DOCKER_HOST is configured correctly.",
            "hint": "If using a socket proxy, verify the proxy container is running.",
        }

    # Unknown — generic but safe (no raw exception text)
    return {
        "type": "unknown",
        "message": f"Analysis encountered an unexpected issue with {display_path}. Check the log panel for details.",
    }
```

Then update the `/api/analyze` endpoint's exception handler to use this function:

```python
except Exception as exc:
    logger.exception("Analysis failed for %s", stack_path)
    error_info = _categorize_analysis_error(exc, compose_path)
    return JSONResponse(status_code=500, content={"status": "error", **error_info})
```

**Step 4: Run tests → ALL PASS**

**Step 5: Commit**

```bash
git add backend/main.py tests/test_error_categorisation.py
git commit -m "feat: structured error messages for analysis failures

New _categorize_analysis_error() maps exceptions to typed, actionable
responses: yaml_parse (with line), file_missing, permission_denied,
docker_unreachable. Never leaks raw exception text. Frontend can switch
on error type for specific icons and guidance.

Elder Council: DeepSeek + Gemini + Grok.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 10: C2 — Specific Error Messages (Frontend)

**Files:**
- Modify: `frontend/app.js` (analysis error rendering)

**Step 1: Implement error type rendering**

Update the analysis error display in `app.js` to switch on error type:

```javascript
function renderAnalysisError(errorData) {
    const icons = {
        yaml_parse: "\u26A0\uFE0F",      // warning
        file_missing: "\uD83D\uDCC2",    // folder
        permission_denied: "\uD83D\uDD12", // lock
        docker_unreachable: "\uD83D\uDD0C", // plug
        no_services: "\uD83D\uDCE6",     // package
        unknown: "\u2753",                 // question
    };
    const type = errorData.type || "unknown";
    const icon = icons[type] || icons.unknown;
    const message = errorData.message || "Analysis failed — check log panel for details.";

    // Build error card with type-specific styling
    const card = document.createElement("div");
    card.className = "analysis-error error-type-" + type;

    const header = document.createElement("div");
    header.className = "analysis-error-header";
    header.textContent = icon + " " + message;
    card.appendChild(header);

    if (errorData.hint) {
        const hint = document.createElement("div");
        hint.className = "analysis-error-hint";
        hint.textContent = errorData.hint;
        card.appendChild(hint);
    }

    return card;
}
```

**Step 2: Manual test** — trigger each error type (malformed YAML via E03, missing file, etc.)

**Step 3: Commit**

```bash
git add frontend/app.js frontend/styles.css
git commit -m "feat(ui): render type-specific analysis error messages

Frontend switches on error type from backend: yaml_parse, file_missing,
permission_denied, docker_unreachable. Each gets icon + actionable message.
Falls back to generic for unknown types.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 11: C3 — Warning Dismiss

**Files:**
- Modify: `frontend/app.js` (conflict card rendering, localStorage)

**Step 1: Implement dismiss logic**

```javascript
// Dismissable warning types — only Cat B low/medium severity
const DISMISSABLE_WARNINGS = new Set([
    "root_execution", "umask_inconsistent", "umask_restrictive",
    "tz_mismatch", "missing_tz",
]);

function getDismissedWarnings() {
    try {
        return new Set(JSON.parse(localStorage.getItem("maparr_dismissed_warnings") || "[]"));
    } catch { return new Set(); }
}

function dismissWarning(type) {
    const dismissed = getDismissedWarnings();
    dismissed.add(type);
    localStorage.setItem("maparr_dismissed_warnings", JSON.stringify([...dismissed]));
}

function resetDismissedWarnings() {
    localStorage.removeItem("maparr_dismissed_warnings");
}
```

In conflict card rendering, add dismiss check and link:

```javascript
// Skip rendering dismissed warnings (they still log)
const dismissed = getDismissedWarnings();
if (DISMISSABLE_WARNINGS.has(conflict.type) && dismissed.has(conflict.type)) {
    return null; // Don't render, but don't remove from data
}

// Add dismiss link for dismissable warnings
if (DISMISSABLE_WARNINGS.has(conflict.type)) {
    const dismissLink = document.createElement("button");
    dismissLink.className = "btn-link btn-dismiss-warning";
    dismissLink.textContent = "Don't warn me again";
    dismissLink.addEventListener("click", (e) => {
        e.stopPropagation();
        dismissWarning(conflict.type);
        // Re-render to remove the card
        renderDashboard();
    });
    card.appendChild(dismissLink);
}
```

Add "Reset dismissed warnings" in log panel footer.

**Step 2: Manual test** — dismiss a root_execution warning, refresh, verify hidden, reset, verify shown

**Step 3: Commit**

```bash
git add frontend/app.js frontend/styles.css
git commit -m "feat(ui): add warning dismiss for low-severity Cat B conflicts

Dismissable: root_execution, umask_inconsistent/restrictive, tz_mismatch,
missing_tz. Stored in localStorage. Dismissed warnings still count in logs
but don't render as conflict cards or affect health dots. Reset available
in log panel footer.

Elder Council: Gemini + Grok.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 12: Slice 2 Review Checkpoint

Run ALL tests. Code review via `superpowers:requesting-code-review`. Verify:
- Revert button appears/works after Apply Fix
- Error messages are type-specific
- Warning dismiss persists across refresh
- No regressions in existing 755 tests

---

# SLICE 3: UX Features

---

## Task 13: U7 — Service Icon Fallback Improvement

**Files:**
- Modify: `frontend/app.js:909` (`getServiceIconUrl`)

**Step 1: Implement enhanced fallback**

```javascript
function getServiceIconUrl(serviceName, imageName) {
    // Pass 1: Exact match (existing)
    if (SERVICE_ICONS[serviceName]) {
        return "/static/img/services/" + SERVICE_ICONS[serviceName];
    }

    // Pass 2: Segment match — split service name, check each segment
    const segments = serviceName.split(/[-_]/);
    for (const seg of segments) {
        if (seg.length >= 3 && SERVICE_ICONS[seg]) {
            return "/static/img/services/" + SERVICE_ICONS[seg];
        }
    }

    // Pass 3: Image basename — extract from image string
    if (imageName) {
        const basename = imageName.split("/").pop().split(":")[0].toLowerCase();
        if (SERVICE_ICONS[basename]) {
            return "/static/img/services/" + SERVICE_ICONS[basename];
        }
        // Also check basename segments
        const imgSegments = basename.split(/[-_]/);
        for (const seg of imgSegments) {
            if (seg.length >= 3 && SERVICE_ICONS[seg]) {
                return "/static/img/services/" + SERVICE_ICONS[seg];
            }
        }
    }

    // Pass 4: Fallback
    return "/static/img/services/generic.svg";
}
```

**Step 2: Manual test with F03 (custom unknown images) test stack**

**Step 3: Commit**

---

## Task 14: U2 — Collapsible Other Stacks

**Files:**
- Modify: `frontend/app.js:747` (Other Stacks rendering)

**Step 1: Implement collapsible wrapper**

Wrap the Other Stacks chip container. Default collapsed when >10 chips. Toggle state in localStorage. Show "Other Services (N)" header with chevron.

**Step 2: Manual test with large stack sets**

**Step 3: Commit**

---

## Task 15: U6 — Redeploy Risk Warning Prominence

**Files:**
- Modify: `frontend/app.js` (post-apply flow)
- Modify: `frontend/styles.css`

**Step 1: Replace dismissible modal with persistent inline banner**

After Apply Fix success, render an amber inline banner above the service card:
- "Changes applied — restart required for them to take effect"
- "Restart Now" button (ties to U3 when available, or shows manual commands)
- "I'll restart later" collapses to subtle amber indicator on service card
- Banner auto-removed when pipeline rescan confirms fix took effect

**Step 2: Manual test full apply→banner→restart cycle**

**Step 3: Commit**

---

## Task 16: U1 — First-Run Wizard

**Files:**
- Modify: `backend/main.py` (new `GET /api/host-info` endpoint)
- Modify: `frontend/app.js` (wizard UI replacing first-launch screen)
- Modify: `frontend/styles.css` (wizard styling)
- Test: `tests/test_host_info.py` (new file)

**Step 1: Write failing test for host-info endpoint**

```python
"""Tests for /api/host-info endpoint."""
import pytest
from fastapi.testclient import TestClient
from backend.main import app


def test_host_info_returns_uid_gid():
    """Host info endpoint returns uid and gid."""
    client = TestClient(app)
    response = client.get("/api/host-info")
    assert response.status_code == 200
    data = response.json()
    assert "uid" in data, "Response must include uid"
    assert "gid" in data, "Response must include gid"
    assert isinstance(data["uid"], int)
    assert isinstance(data["gid"], int)
```

**Step 2: Implement backend endpoint**

```python
@app.get("/api/host-info")
async def host_info():
    """Return host process UID/GID for first-run wizard PUID/PGID pre-population."""
    try:
        uid = os.getuid()
        gid = os.getgid()
    except AttributeError:
        # Windows — no getuid/getgid
        uid = 1000
        gid = 1000
    return {"uid": uid, "gid": gid, "platform": sys.platform}
```

**Step 3: Implement 3-step wizard frontend**

Replace the `first-launch` screen content with a wizard:
- Step 1: Folder browser (reuse `/api/list-directories`)
- Step 2: PUID/PGID confirmation (pre-populated from `/api/host-info`)
- Step 3: Scan + boot animation + transition to dashboard
- "Skip wizard" link

Store completed state in `localStorage.setItem("maparr_wizard_complete", "true")`.

**Step 4: Manual test — clear localStorage, reload, walk through wizard**

**Step 5: Run all tests**

**Step 6: Commit**

---

## Task 17: U3 — Direct Stack Restart

**Files:**
- Modify: `backend/main.py` (two new endpoints)
- Modify: `frontend/app.js` (restart button in post-apply + U6 banner)
- Test: `tests/test_restart_stack.py` (new file)

**Step 1: Write failing tests**

```python
"""Tests for stack restart via Docker socket."""
import pytest
from fastapi.testclient import TestClient
from backend.main import app, _session


class TestDockerCapabilities:
    """GET /api/docker-capabilities."""

    def test_returns_capability_fields(self):
        client = TestClient(app)
        response = client.get("/api/docker-capabilities")
        assert response.status_code == 200
        data = response.json()
        assert "socket_available" in data
        assert "compose_available" in data
        assert isinstance(data["socket_available"], bool)


class TestRestartStack:
    """POST /api/restart-stack."""

    def test_restart_requires_stacks_path(self, tmp_path):
        """Must enforce write boundary."""
        _session.pop("custom_stacks_path", None)
        client = TestClient(app)
        response = client.post("/api/restart-stack", json={
            "stack_path": "/some/path",
            "compose_file": "compose.yaml",
        })
        assert response.status_code == 403
```

**Step 2: Implement endpoints**

`GET /api/docker-capabilities` — check socket exists, is writable, compose CLI available.
`POST /api/restart-stack` — validate path, run `docker compose -f <file> up -d`.

**Step 3: Frontend integration**

In the U6 restart banner, if capabilities allow, show "Restart Now" button that calls `/api/restart-stack`. Otherwise show manual commands.

**Step 4: Tests + commit**

---

## Task 18: U4 — Export Diagnostic Zip

**Files:**
- Modify: `backend/main.py` (new endpoint)
- Modify: `frontend/app.js` (upgrade Copy Diagnostic button)
- Test: `tests/test_export_diagnostics.py` (new file)

**Step 1: Write failing tests**

```python
"""Tests for diagnostic zip export."""
import io
import zipfile
import pytest
from fastapi.testclient import TestClient
from backend.main import app, _session


class TestExportDiagnostics:
    """GET /api/export-diagnostics."""

    def test_returns_zip(self, tmp_path):
        """Endpoint returns a valid zip file."""
        compose = tmp_path / "stack1" / "compose.yaml"
        compose.parent.mkdir()
        compose.write_text("services:\n  test:\n    image: test\n")
        _session["custom_stacks_path"] = str(tmp_path)
        _session["pipeline"] = {"scan_dir": str(tmp_path), "services": []}

        client = TestClient(app)
        response = client.get("/api/export-diagnostics")
        assert response.status_code == 200
        assert "application/zip" in response.headers.get("content-type", "")

        zf = zipfile.ZipFile(io.BytesIO(response.content))
        names = zf.namelist()
        assert any("compose" in n for n in names), f"Zip must contain compose files, got: {names}"

    def test_secrets_redacted(self, tmp_path):
        """Environment variable values matching secret patterns are redacted."""
        compose = tmp_path / "stack1" / "compose.yaml"
        compose.parent.mkdir()
        compose.write_text(
            "services:\n  sonarr:\n    image: lscr.io/linuxserver/sonarr\n"
            "    environment:\n      - API_KEY=supersecret123\n"
            "      - PUID=1000\n"
        )
        _session["custom_stacks_path"] = str(tmp_path)
        _session["pipeline"] = {"scan_dir": str(tmp_path), "services": []}

        client = TestClient(app)
        response = client.get("/api/export-diagnostics")
        zf = zipfile.ZipFile(io.BytesIO(response.content))
        for name in zf.namelist():
            content = zf.read(name).decode("utf-8", errors="replace")
            assert "supersecret123" not in content, f"Secret leaked in {name}"
```

**Step 2: Implement endpoint**

Collect compose files, redact secrets, bundle with pipeline result + version info into in-memory zip. Return as streaming response.

**Step 3: Frontend — upgrade button**

Replace "Copy Diagnostic" with dual action:
- Primary: "Export Diagnostic" → downloads zip
- Secondary: "Copy Summary" → clipboard (existing behaviour preserved)

**Step 4: Tests + commit**

---

## Task 19: Slice 3 Review Checkpoint

Run ALL tests including E2E. Code review. Manual verification of all 6 UX features.

---

# SLICE 4: Documentation & Release Prep

---

## Task 20: D1 — Comprehensive Docs Review

Audit all docs against the now-updated codebase. New endpoints, new env vars, new features.

## Task 21: D2 — GitHub Repo Polish

Templates, description, topics, social preview, contributing guidelines.

## Task 22: D3 — GIF Demo

Record against final UI. Embed in README.

## Task 23: D4 — Private Beta Release Plan

Tag format, Docker image strategy, beta feedback form (adapted from testing-form.html), tester selection, success criteria.

## Task 24: Final Verification

Use `superpowers:verification-before-completion`:
- `pytest tests/ -v -p no:capture` — ALL PASS
- `pytest tests/e2e/test_api_contracts.py -v -p no:capture` — ALL PASS
- `pytest tests/e2e/test_components.py tests/e2e/test_journeys.py -v` — ALL PASS
- Manual smoke test of key flows
- Security scan of all new endpoints
- CLAUDE.md + MEMORY.md updated

---

## Execution Order Summary

| Task | ID | Slice | Description | Effort |
|------|----|-------|-------------|--------|
| 1 | S3 | 1 | DOCKER_HOST allowlist | Small |
| 2 | S4 | 1 | Trusted proxy IP | Small |
| 3 | S5 | 1 | Write boundary enforcement | Small |
| 4 | C4 | 1 | SSE hard timeout | Trivial |
| 5 | H1+H2 | 1 | Code hygiene | Small |
| 6 | — | 1 | Slice 1 review checkpoint | — |
| 7 | C1 | 2 | Revert endpoint (backend) | Medium |
| 8 | C1 | 2 | Revert button (frontend) | Small |
| 9 | C2 | 2 | Error categorisation (backend) | Medium |
| 10 | C2 | 2 | Error rendering (frontend) | Small |
| 11 | C3 | 2 | Warning dismiss | Small |
| 12 | — | 2 | Slice 2 review checkpoint | — |
| 13 | U7 | 3 | Icon fallback improvement | Small |
| 14 | U2 | 3 | Collapsible Other Stacks | Small |
| 15 | U6 | 3 | Redeploy risk banner | Small |
| 16 | U1 | 3 | First-run wizard | Medium |
| 17 | U3 | 3 | Direct stack restart | Medium |
| 18 | U4 | 3 | Export diagnostic zip | Medium |
| 19 | — | 3 | Slice 3 review checkpoint | — |
| 20-23 | D1-D4 | 4 | Docs + release prep | Medium |
| 24 | — | 4 | Final verification | — |
