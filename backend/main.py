"""
MapArr — Path Mapping Problem Solver

Lean FastAPI backend. Five jobs:
  1. Parse error text (extract service + path + error type)
  2. Discover compose stacks on the filesystem
  3. Accept stack selection
  4. Analyze stack: resolve compose, detect conflicts, generate fix
  5. Serve application logs to the frontend log panel

No Docker SDK dependency. No SQLite. No persistence, no jobs system.
"""

import asyncio
import logging
import os
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import yaml

from backend.parser import parse_error, parse_errors
from backend.discovery import discover_stacks
from backend.resolver import resolve_compose, ResolveError, COMPOSE_FILENAMES
from backend.analyzer import analyze_stack
from backend.smart_match import smart_match
from backend.pipeline import run_pipeline_scan, get_pipeline_context_for_stack
from backend.log_handler import install_log_handler, get_log_handler


# ─── Security: System Directory Blocklist ───
# Unified blocklist for all directory-browsing endpoints. Prevents users from
# scanning system directories that could leak sensitive information.
_BLOCKED_PREFIXES = (
    "/etc", "/proc", "/sys", "/dev", "/boot", "/sbin", "/root", "/home",
    "C:\\Windows", "C:\\Program Files",
)


# ─── Trusted Proxy IP Resolution ───
# Behind reverse proxies (Traefik, Caddy, Nginx), request.client.host returns
# the proxy IP, not the real client. This lets attackers bypass rate limiting.
# Configure MAPARR_TRUSTED_PROXIES=ip1,ip2 to trust specific proxy IPs, then
# _get_client_ip() walks X-Forwarded-For right-to-left to find the real client.

_TRUSTED_PROXIES: frozenset[str] = frozenset(
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


# ─── Rate Limiting ───
# Simple in-memory sliding window rate limiter. No external dependencies.
# Classifies endpoints into tiers with different request-per-minute limits.
# Thread-safe via a lock (needed because uvicorn may use thread pools).

class RateLimiter:
    """Per-IP sliding window rate limiter with tiered endpoint limits."""

    # Endpoint classification: (path_prefixes, requests_per_minute)
    WRITE_PATHS = ("/api/apply-fix", "/api/apply-fixes", "/api/change-stacks-path", "/api/redeploy")
    ANALYSIS_PATHS = ("/api/analyze", "/api/pipeline-scan")
    SKIP_PATHS = ("/api/health",)
    STATIC_PREFIXES = ("/", "/static")

    WRITE_LIMIT = 10       # requests per minute
    ANALYSIS_LIMIT = 20    # requests per minute
    READ_LIMIT = 60        # requests per minute

    WINDOW = 60.0          # sliding window in seconds
    CLEANUP_INTERVAL = 300.0  # purge stale entries every 5 minutes

    def __init__(self):
        # {ip: {"write": [timestamps], "analysis": [...], "read": [...]}}
        self._requests: dict[str, dict[str, list[float]]] = defaultdict(
            lambda: defaultdict(list)
        )
        self._lock = threading.Lock()
        self._last_cleanup = time.time()

    def _classify(self, path: str) -> tuple[str, int] | None:
        """Classify a request path into a tier. Returns (tier, limit) or None to skip."""
        # Skip health checks and static files entirely
        if path in self.SKIP_PATHS:
            return None
        if path == "/" or path.startswith("/static"):
            return None

        # Write endpoints (most restrictive)
        if path in self.WRITE_PATHS:
            return ("write", self.WRITE_LIMIT)

        # Analysis endpoints
        if path in self.ANALYSIS_PATHS:
            return ("analysis", self.ANALYSIS_LIMIT)

        # Everything else under /api/ is a read endpoint
        if path.startswith("/api/"):
            return ("read", self.READ_LIMIT)

        # Non-API paths (shouldn't happen, but don't rate limit)
        return None

    def check(self, ip: str, path: str) -> tuple[bool, int]:
        """
        Check if a request is allowed.

        Returns (allowed, retry_after_seconds).
        When allowed=True, retry_after is 0.
        When allowed=False, retry_after is seconds until the oldest
        request in the window expires.
        """
        classification = self._classify(path)
        if classification is None:
            return (True, 0)

        tier, limit = classification
        now = time.time()
        cutoff = now - self.WINDOW

        with self._lock:
            # Periodic cleanup of stale IPs
            if now - self._last_cleanup > self.CLEANUP_INTERVAL:
                self._cleanup(now)

            timestamps = self._requests[ip][tier]

            # Prune expired timestamps for this IP+tier
            self._requests[ip][tier] = timestamps = [
                t for t in timestamps if t > cutoff
            ]

            if len(timestamps) >= limit:
                # Rate limited — calculate when the oldest request expires
                oldest = min(timestamps)
                retry_after = int(oldest + self.WINDOW - now) + 1
                return (False, max(retry_after, 1))

            # Allowed — record this request
            timestamps.append(now)
            return (True, 0)

    def reset(self):
        """Clear all rate limit state. Used by test fixtures."""
        with self._lock:
            self._requests.clear()
            self._last_cleanup = time.time()

    def _cleanup(self, now: float):
        """Remove IPs with no recent activity. Called under lock."""
        cutoff = now - self.WINDOW
        stale_ips = []
        for ip, tiers in self._requests.items():
            all_empty = True
            for tier, timestamps in tiers.items():
                # Filter in place
                tiers[tier] = [t for t in timestamps if t > cutoff]
                if tiers[tier]:
                    all_empty = False
            if all_empty:
                stale_ips.append(ip)
        for ip in stale_ips:
            del self._requests[ip]
        self._last_cleanup = now


_rate_limiter = RateLimiter()


# ─── SSE Connection Limiter ───
# Caps per-IP concurrent SSE connections to prevent file descriptor exhaustion.
# Unlike the rate limiter (requests-per-minute), this tracks *active* long-lived
# connections. Thread-safe via a lock since connect/disconnect can race.

class SSEConnectionLimiter:
    """Per-IP concurrent SSE connection limiter."""

    MAX_PER_IP = 5  # max concurrent SSE streams per client IP

    def __init__(self):
        self._connections: dict[str, int] = defaultdict(int)
        self._lock = threading.Lock()

    def try_connect(self, ip: str) -> bool:
        """Attempt to register a new SSE connection. Returns False if at limit."""
        with self._lock:
            if self._connections[ip] >= self.MAX_PER_IP:
                return False
            self._connections[ip] += 1
            return True

    def disconnect(self, ip: str) -> None:
        """Release an SSE connection slot."""
        with self._lock:
            self._connections[ip] = max(0, self._connections[ip] - 1)
            if self._connections[ip] == 0:
                del self._connections[ip]

    def reset(self):
        """Clear all state. Used by test fixtures."""
        with self._lock:
            self._connections.clear()


_sse_limiter = SSEConnectionLimiter()
SSE_HARD_TIMEOUT_SECONDS = 300  # 5-minute hard timeout on SSE connections (Grok)


# ─── Security: Path Validation ───
# All endpoints that accept filesystem paths from the client MUST validate
# that the resolved path is within the allowed stacks directory. This prevents
# path traversal attacks (e.g., writing to /etc/passwd via apply-fix).

def _get_stacks_root() -> str:
    """Return the current stacks root directory (custom or env-based)."""
    return (
        _session.get("custom_stacks_path")
        or os.environ.get("MAPARR_STACKS_PATH", "")
    )


def _is_path_within_stacks(path: str, require_root: bool = False) -> bool:
    """
    Check that a resolved path is within the stacks root directory.

    When require_root=False (read operations like scan/analyze):
      Returns True if no stacks root is configured.
    When require_root=True (write operations like apply-fix):
      Returns False if no stacks root is configured — writes are only
      allowed when a boundary is explicitly set. This prevents accidental
      writes to arbitrary compose files when running outside Docker.
    """
    stacks_root = _get_stacks_root()
    if not stacks_root:
        if require_root:
            return False  # Writes require an explicit boundary
        return True  # Reads are permissive without a configured root
    try:
        # Resolve symlinks and normalize both paths
        real_path = Path(path).resolve()
        real_root = Path(stacks_root).resolve()
        # Use pathlib's relative_to — raises ValueError if not a subpath
        real_path.relative_to(real_root)
        return True
    except (ValueError, OSError):
        return False


# ─── Logging ───

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("maparr")

# Install the in-memory log handler so logs are available via /api/logs
_log_handler = install_log_handler()

# ─── Write Boundary Startup Check ───
# Warn early if no stacks root is configured — write endpoints will refuse.
if not os.environ.get("MAPARR_STACKS_PATH"):
    logger.warning(
        "MAPARR_STACKS_PATH not set — write endpoints (Apply Fix, Revert) are disabled. "
        "Set MAPARR_STACKS_PATH to the directory containing your compose files."
    )

# ─── Version ───
# Single source of truth — used in FastAPI metadata and /api/health.
# Frontend reads this via the health endpoint on page load.
VERSION = "1.5.0"

# ─── Image Registry ───
# Eagerly initialize the singleton so startup logs show image count.
# The registry itself lives in image_registry.py (avoids circular imports).
from backend.image_registry import get_registry as _get_registry
registry = _get_registry()

# ─── App ───

app = FastAPI(
    title="MapArr",
    description="Path Mapping Problem Solver for *arr apps",
    version=VERSION,
)

logger.info("MapArr v%s starting up", VERSION)


# ─── Rate Limiting Middleware ───

@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """Enforce per-IP rate limits based on endpoint tier."""
    client_ip = _get_client_ip(request)
    path = request.url.path

    allowed, retry_after = _rate_limiter.check(client_ip, path)
    if not allowed:
        logger.warning("Rate limited: %s on %s (retry after %ds)", client_ip, path, retry_after)
        return JSONResponse(
            {"error": "Too many requests. Please slow down.", "retry_after": retry_after},
            status_code=429,
            headers={"Retry-After": str(retry_after)},
        )

    return await call_next(request)


# ─── State ───
# Minimal in-memory state for the current session.
# No persistence — MapArr is a single-use problem solver.

_session = {
    "parsed_error": None,
    "selected_stack": None,
    "pipeline": None,  # Cached PipelineResult from pipeline scan
}


# ─── Helpers: Error Formatting ───

import json

def _json_error_detail(exc: Exception) -> str:
    """Format a JSON parse error with position context when available."""
    if isinstance(exc, json.JSONDecodeError):
        return f"Invalid JSON in request body (line {exc.lineno}, column {exc.colno}): {exc.msg}"
    return "Invalid JSON in request body"


def _categorize_os_error(e: OSError, action: str) -> str:
    """Return a user-friendly message for common OS errors without leaking internals."""
    import errno
    if e.errno == errno.EACCES:
        return f"{action}: permission denied"
    if e.errno == errno.ENOSPC:
        return f"{action}: disk full"
    if e.errno == errno.EROFS:
        return f"{action}: read-only filesystem"
    if e.errno == errno.ENOENT:
        return f"{action}: file not found"
    return f"{action}: system error (check logs for details)"


def _relative_path_display(full_path: str) -> str:
    """Show path relative to stacks root for user context, falling back to basename."""
    try:
        root = _get_stacks_root()
    except NameError:
        root = None
    if not root:
        return os.path.basename(full_path)
    try:
        return str(Path(full_path).resolve().relative_to(Path(root).resolve()))
    except (ValueError, OSError):
        return os.path.basename(full_path)


# ─── Frontend ───

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"


@app.get("/")
async def serve_index():
    """Serve the web UI."""
    index_path = FRONTEND_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return JSONResponse(
        {"error": "Frontend not found. Check frontend/ directory."},
        status_code=404,
    )


# Serve static assets (CSS, JS)
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


# ─── API: Parse Error ───

@app.post("/api/parse-error")
async def api_parse_error(request: Request):
    """
    Parse user's error input. Extract service, path, error type.

    Always returns 200 with a result — even for garbage input.
    The confidence field tells the frontend how much we understood.
    Frontend should NEVER dead-end the user based on parse results.
    """
    try:
        body = await request.json()
    except Exception as exc:
        return JSONResponse(
            {"error": _json_error_detail(exc)},
            status_code=400,
        )

    error_text = body.get("error_text", "").strip()
    if not error_text:
        return JSONResponse(
            {"error": "No error text provided"},
            status_code=400,
        )

    # Input size limit: 100KB max error text to prevent memory exhaustion
    if len(error_text) > 100_000:
        return JSONResponse(
            {"error": "Error text too large (max 100KB)"},
            status_code=400,
        )

    # Parse — check for multiple errors
    all_results = parse_errors(error_text)

    # Primary result from first chunk (avoids redundant re-parse of full text)
    if all_results:
        primary = all_results[0].copy()
    else:
        primary = parse_error(error_text).to_dict()
    _session["parsed_error"] = primary

    # Include multi-error data when >1 error detected
    if len(all_results) > 1:
        primary["multiple_errors"] = all_results
        primary["error_count"] = len(all_results)

    logger.info("Parse error: service=%s path=%s type=%s confidence=%s",
                primary.get("service", "?"), primary.get("path", "?"),
                primary.get("error_type", "?"), primary.get("confidence", "?"))

    return primary


# ─── API: Discover Stacks ───

@app.get("/api/discover-stacks")
async def api_discover_stacks():
    """
    Find Docker compose stacks on the filesystem.

    Scans MAPARR_STACKS_PATH (Docker mount), common locations, and CWD.
    Returns stacks with service names for the selection UI.

    This is shallow discovery — just enough to populate the stack list.
    Deep resolution via `docker compose config` happens in the analyze endpoint.
    """
    custom = _session.get("custom_stacks_path")
    logger.info("Discover stacks: scanning %s", custom or "default locations")
    t0 = time.time()
    if custom:
        stacks = discover_stacks(custom_path=custom)
    else:
        stacks = discover_stacks()
    elapsed = time.time() - t0
    logger.info("Discovery complete: %d stacks found in %.2fs", len(stacks), elapsed)

    # Determine the effective scan path to display
    scan_path = custom or os.environ.get("MAPARR_STACKS_PATH", "")
    if not scan_path and stacks:
        # Show the directory containing the most stacks
        from collections import Counter
        counts = Counter(os.path.dirname(s.path) for s in stacks)
        top_path, top_count = counts.most_common(1)[0]
        scan_path = top_path

    return {
        "stacks": [s.to_dict() for s in stacks],
        "total": len(stacks),
        "scan_path": scan_path,
        "search_note": _get_search_note(custom),
    }


def _get_search_note(custom_path: Optional[str] = None) -> str:
    """Generate a human-readable note about where we searched."""
    if custom_path:
        return f"Scanning custom path: {custom_path}"
    stacks_env = os.environ.get("MAPARR_STACKS_PATH", "")
    if stacks_env:
        return f"Scanning mounted path: {stacks_env}"
    return "Scanned common locations. Set MAPARR_STACKS_PATH or use Change Path below."


# ─── API: Pipeline Scan ───

@app.post("/api/pipeline-scan")
async def api_pipeline_scan(request: Request):
    """
    Scan the entire root directory and build a unified media pipeline view.

    This is the foundation of MapArr's intelligence. Instead of analyzing
    stacks in isolation, the pipeline scan understands the FULL layout:
    all media services, all mount paths, all relationships. Both Fix mode
    and Browse mode draw from this context.

    Triggers on boot, on path change, and on manual rescan.
    Result is cached in session state.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}

    scan_dir = (body.get("scan_dir", "") or "").strip()
    if not scan_dir:
        # Default to custom path or env var or parent of most stacks
        scan_dir = _session.get("custom_stacks_path") or os.environ.get("MAPARR_STACKS_PATH", "")

    if not scan_dir:
        # Fall back to most common stack parent directory
        custom = _session.get("custom_stacks_path")
        stacks = discover_stacks(custom_path=custom) if custom else discover_stacks()
        if stacks:
            from collections import Counter
            counts = Counter(os.path.dirname(s.path) for s in stacks)
            scan_dir = counts.most_common(1)[0][0]

    if not scan_dir or not os.path.isdir(scan_dir):
        return JSONResponse(
            {"error": "No valid scan directory available. Set MAPARR_STACKS_PATH or use the Change Path button to select your stacks directory."},
            status_code=400,
        )

    # Security: if MAPARR_STACKS_PATH env var is set (Docker deployment),
    # enforce that scan_dir is within it. User-chosen paths (custom_stacks_path)
    # are updated freely — the user is explicitly navigating.
    env_stacks_root = os.environ.get("MAPARR_STACKS_PATH", "")
    if env_stacks_root:
        try:
            Path(scan_dir).resolve().relative_to(Path(env_stacks_root).resolve())
        except (ValueError, OSError):
            logger.warning("Pipeline scan blocked: %s outside MAPARR_STACKS_PATH %s", scan_dir, env_stacks_root)
            return JSONResponse(
                {"error": "Scan directory is outside the configured stacks root. Check MAPARR_STACKS_PATH or use Change Path to update."},
                status_code=403,
            )

    t0 = time.time()
    result = run_pipeline_scan(scan_dir)
    elapsed = time.time() - t0
    _session["pipeline"] = result.to_dict()

    # Persist the scan directory as the stacks root so write operations
    # (apply-fixes, redeploy) can validate paths against it.
    if _session.get("custom_stacks_path") != scan_dir:
        _session["custom_stacks_path"] = scan_dir
        logger.info("Pipeline scan: set custom_stacks_path=%s", scan_dir)

    logger.info("Pipeline scan: %s → %s (%.2fs)", scan_dir, result.summary, elapsed)

    return _session["pipeline"]


# ─── API: Change Stacks Path ───

@app.post("/api/change-stacks-path")
async def api_change_stacks_path(request: Request):
    """
    Let the user change the stacks scan directory at runtime.

    This doesn't modify environment variables — it stores the custom path
    in session state and re-runs discovery against it.
    """
    try:
        body = await request.json()
    except Exception as exc:
        return JSONResponse(
            {"error": _json_error_detail(exc)},
            status_code=400,
        )

    new_path = body.get("path", "").strip()
    if not new_path:
        # Clear custom path, revert to default
        _session["custom_stacks_path"] = None
        return {"status": "reset", "message": "Reverted to default scan locations."}

    if not os.path.isdir(new_path):
        return JSONResponse(
            {"error": f"Directory not found: {new_path}"},
            status_code=400,
        )

    # Security: if MAPARR_STACKS_PATH is set (Docker deployment), the admin
    # controls the boundary — new path must be within it. But if the root was
    # set by user choice (custom_stacks_path), switching directories is allowed.
    env_stacks_root = os.environ.get("MAPARR_STACKS_PATH", "")
    if env_stacks_root:
        try:
            Path(new_path).resolve().relative_to(Path(env_stacks_root).resolve())
        except (ValueError, OSError):
            logger.warning("Change path blocked: %s outside MAPARR_STACKS_PATH %s", new_path, env_stacks_root)
            return JSONResponse(
                {"error": "Path must be within the stacks directory (set by MAPARR_STACKS_PATH)"},
                status_code=403,
            )

    # Defense-in-depth: block obvious system directories
    resolved_new = str(Path(new_path).resolve())
    if any(resolved_new.startswith(p) for p in _BLOCKED_PREFIXES):
        logger.warning("Change path blocked: system directory: %s", new_path)
        return JSONResponse(
            {"error": "Cannot scan system directories"},
            status_code=403,
        )

    old_path = _session.get("custom_stacks_path", "default")
    _session["custom_stacks_path"] = new_path
    _session["pipeline"] = None  # Invalidate — pipeline was built from old path
    logger.info("Stacks path changed: %s → %s (pipeline cache cleared)", old_path, new_path)

    # Run discovery on the new path immediately
    t0 = time.time()
    stacks = discover_stacks(custom_path=new_path)
    logger.info("Re-discovery on new path: %d stacks found (%.2fs)", len(stacks), time.time() - t0)

    return {
        "status": "ok",
        "path": new_path,
        "scan_path": new_path,
        "stacks": [s.to_dict() for s in stacks],
        "total": len(stacks),
        "search_note": _get_search_note(new_path),
    }


# ─── API: List Directories (for folder browser) ───

@app.post("/api/list-directories")
async def api_list_directories(request: Request):
    """
    List subdirectories of a given path for the folder browser UI.

    Returns immediate child directories (not recursive) so the frontend
    can render a navigable folder tree. On Windows with no path specified,
    returns available drive letters as roots.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}

    req_path = body.get("path", "").strip()

    # Windows: if no path given, list drive letters
    if not req_path and os.name == "nt":
        import string
        drives = []
        for letter in string.ascii_uppercase:
            drive = f"{letter}:\\"
            if os.path.isdir(drive):
                drives.append({"name": f"{letter}:", "path": drive})
        return {"path": "", "parent": None, "directories": drives}

    # Unix: default to /
    if not req_path:
        req_path = "/"

    # Normalize path
    try:
        resolved = str(Path(req_path).resolve())
    except (ValueError, OSError):
        return JSONResponse(
            {"error": f"Invalid path: {req_path}"},
            status_code=400,
        )

    if not os.path.isdir(resolved):
        return JSONResponse(
            {"error": f"Directory not found: {req_path}"},
            status_code=400,
        )

    # Block system directories (uses unified blocklist)
    if any(resolved.startswith(p) for p in _BLOCKED_PREFIXES):
        return JSONResponse(
            {"error": "Cannot browse system directories"},
            status_code=403,
        )

    # List subdirectories, skip hidden and inaccessible
    dirs = []
    try:
        for entry in sorted(Path(resolved).iterdir()):
            if not entry.is_dir():
                continue
            if entry.name.startswith("."):
                continue
            try:
                # Test readability (some dirs exist but can't be listed)
                next(entry.iterdir(), None)
                dirs.append({"name": entry.name, "path": str(entry)})
            except (PermissionError, OSError):
                # Still show it but mark as inaccessible
                dirs.append({"name": entry.name, "path": str(entry), "locked": True})
    except PermissionError:
        return JSONResponse(
            {"error": f"Permission denied: {req_path}"},
            status_code=403,
        )

    # Calculate parent for up-navigation
    parent_path = str(Path(resolved).parent)
    if parent_path == resolved:
        # At root (/ or C:\) — no parent
        parent_path = None if os.name != "nt" else ""

    return {
        "path": resolved,
        "parent": parent_path,
        "directories": dirs,
    }


# ─── API: Select Stack ───

@app.post("/api/select-stack")
async def api_select_stack(request: Request):
    """
    User selected a stack for analysis.

    Stores the selection in session state. The analyze endpoint uses this to run
    `docker compose config` on the selected stack and perform deep analysis.
    """
    try:
        body = await request.json()
    except Exception as exc:
        return JSONResponse(
            {"error": _json_error_detail(exc)},
            status_code=400,
        )

    stack_path = body.get("stack_path", "").strip()
    if not stack_path:
        return JSONResponse(
            {"error": "No stack_path provided"},
            status_code=400,
        )

    # Validate the path exists
    if not os.path.isdir(stack_path):
        return JSONResponse(
            {"error": f"Directory not found: {_relative_path_display(stack_path)}"},
            status_code=400,
        )

    # Security: validate the path is within stacks root
    if not _is_path_within_stacks(stack_path):
        logger.warning("Select stack blocked: path outside stacks root: %s", stack_path)
        return JSONResponse(
            {"error": "Path is outside the stacks directory. Set MAPARR_STACKS_PATH or use Change Path to configure the correct root."},
            status_code=403,
        )

    _session["selected_stack"] = {
        "stack_path": stack_path,
        "parsed_error": _session.get("parsed_error"),
    }
    error_ctx = _session.get("parsed_error")
    if error_ctx:
        logger.info("Stack selected: %s (carrying error context: service=%s, type=%s)",
                    os.path.basename(stack_path),
                    error_ctx.get("service", "?"), error_ctx.get("error_type", "?"))
    else:
        logger.info("Stack selected: %s (browse mode — no error context)",
                    os.path.basename(stack_path))

    return {
        "status": "ready",
        "stack_path": stack_path,
        "parsed_error": _session.get("parsed_error"),
        "next_step": "Ready for analysis",
    }


# ─── API: Analyze Stack ───

@app.post("/api/analyze")
async def api_analyze(request: Request):
    """
    Full stack analysis: resolve compose, detect conflicts, generate fix.

    This is where MapArr delivers its value. Takes the stack path and
    optional error context from the parse step, resolves the compose file, analyzes
    volume mounts, detects path conflicts, and returns specific fixes.

    Always returns 200 with results — errors are reported in the response
    body with appropriate context, never as dead-end HTTP errors.
    """
    try:
        body = await request.json()
    except Exception as exc:
        return JSONResponse(
            {"error": _json_error_detail(exc)},
            status_code=400,
        )

    stack_path = body.get("stack_path", "").strip()
    if not stack_path:
        return JSONResponse(
            {"error": "No stack_path provided"},
            status_code=400,
        )

    if not os.path.isdir(stack_path):
        return JSONResponse(
            {"error": f"Directory not found: {_relative_path_display(stack_path)}"},
            status_code=400,
        )

    # Security: validate the path is within stacks root
    if not _is_path_within_stacks(stack_path):
        logger.warning("Analyze blocked: path outside stacks root: %s", stack_path)
        return JSONResponse(
            {"error": "Path is outside the stacks directory. Set MAPARR_STACKS_PATH or use Change Path to configure the correct root."},
            status_code=403,
        )

    # Get error context (optional — from the parse step)
    error_info = body.get("error", _session.get("parsed_error"))
    error_service = None
    error_path = None
    if isinstance(error_info, dict):
        error_service = error_info.get("service")
        error_path = error_info.get("path")

    stack_name = os.path.basename(stack_path)
    logger.info("Analyze: starting analysis of %s (error_service=%s, error_path=%s)",
                stack_name, error_service or "none", error_path or "none")
    t0_total = time.time()

    # Step 1: Resolve compose file
    steps = [
        {"icon": "run", "text": f"Resolving compose for {stack_name}..."},
    ]
    t0_resolve = time.time()
    try:
        resolved = resolve_compose(stack_path)
    except ResolveError as e:
        logger.error("Analyze: resolution failed for %s: %s (%.2fs)",
                     stack_name, e, time.time() - t0_resolve)
        steps.append({"icon": "fail", "text": f"Resolution failed: {e}"})
        return JSONResponse({
            "status": "error",
            "error": str(e),
            "stage": "resolution",
            "stack_path": os.path.basename(stack_path),
            "steps": steps,
        }, status_code=200)
    resolve_elapsed = time.time() - t0_resolve
    resolve_method = resolved.get("_resolution", "unknown")
    svc_resolved = len(resolved.get("services", {}))
    logger.info("Analyze: compose resolved via %s — %d services (%.2fs)",
                resolve_method, svc_resolved, resolve_elapsed)

    # Read raw compose content for patching in the "Your Config (Corrected)" tab
    raw_compose_content = None
    compose_file_path = resolved.get("_compose_file", "")
    if compose_file_path:
        try:
            raw_compose_content = Path(compose_file_path).read_text(encoding="utf-8")
        except Exception:
            pass

    # Determine scan directory for cross-stack analysis.
    # If user set a custom stacks path, use that. Otherwise, the parent of
    # the selected stack is the scan root (sibling stacks live next to it).
    scan_dir = _session.get("custom_stacks_path") or os.path.dirname(stack_path)

    # Build pipeline context for this stack (if pipeline scan has run).
    # Safety net: if the compose file was modified AFTER the last pipeline scan,
    # the pipeline cache is stale (e.g. Apply Fix wrote a corrected compose but
    # the frontend's refresh didn't complete). Force an inline rescan so the
    # analysis always runs against current compose data.
    pipeline = _session.get("pipeline")
    if pipeline and compose_file_path:
        try:
            compose_mtime = os.path.getmtime(compose_file_path)
            pipeline_scanned_at = pipeline.get("scanned_at", 0)
            if compose_mtime > pipeline_scanned_at:
                logger.info("Analyze: pipeline stale (compose mtime %.0f > scan %.0f) — rescanning",
                            compose_mtime, pipeline_scanned_at)
                fresh = run_pipeline_scan(scan_dir)
                pipeline = fresh.to_dict()
                _session["pipeline"] = pipeline
        except Exception as e:
            logger.warning("Analyze: pipeline freshness check failed: %s", e)

    pipeline_context = None
    if pipeline:
        pipeline_context = get_pipeline_context_for_stack(
            pipeline, stack_path
        )
        logger.info("Analyze: pipeline context available (role=%s, %d siblings)",
                     pipeline_context.get("role", "?"),
                     len(pipeline_context.get("sibling_services", [])))

    # Step 2: Analyze — run in thread executor so the event loop stays free.
    # This lets SSE stream log entries in real-time as analysis progresses,
    # instead of buffering them until the sync call returns.
    t0_analyze = time.time()
    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, lambda: analyze_stack(
            resolved_compose=resolved,
            stack_path=stack_path,
            compose_file=resolved.get("_compose_file", ""),
            resolution_method=resolved.get("_resolution", "unknown"),
            error_service=error_service,
            error_path=error_path,
            raw_compose_content=raw_compose_content,
            scan_dir=scan_dir,
            pipeline_context=pipeline_context,
        ))
    except (OSError, ValueError, TypeError, KeyError) as e:
        logger.exception("Analysis failed for %s", os.path.basename(stack_path))
        safe_msg = _categorize_os_error(e, "Analysis") if isinstance(e, OSError) else "Analysis failed — check the log panel for details"
        steps.append({"icon": "fail", "text": safe_msg})
        return JSONResponse({
            "status": "error",
            "error": safe_msg,
            "stage": "analysis",
            "stack_path": os.path.basename(stack_path),
            "steps": steps,
        }, status_code=200)
    except Exception as e:
        logger.exception("Analysis failed for %s (unexpected)", os.path.basename(stack_path))
        steps.append({"icon": "fail", "text": "Analysis failed — check the log panel for details"})
        return JSONResponse({
            "status": "error",
            "error": "Analysis failed — check the log panel for details",
            "stage": "analysis",
            "stack_path": os.path.basename(stack_path),
            "steps": steps,
        }, status_code=200)

    analyze_elapsed = time.time() - t0_analyze
    total_elapsed = time.time() - t0_total

    rd = result.to_dict()
    svc_count = len(rd.get("services", []))
    conflict_count = rd.get("conflict_count", 0)
    status = rd.get("status", "?")
    cs = rd.get("cross_stack")
    cs_summary = ""
    if cs and cs.get("siblings"):
        cs_summary = " | cross-stack: %d siblings, shared=%s" % (
            len(cs["siblings"]), cs.get("shared_mount", False))
    pipeline_summary = ""
    if rd.get("pipeline_role"):
        pipeline_summary = " | pipeline: role=%s" % rd["pipeline_role"]
    logger.info("Analyze: %s → %s (%d services, %d conflicts%s%s) [resolve=%.2fs, analyze=%.2fs, total=%.2fs]",
                stack_name, status, svc_count, conflict_count,
                cs_summary, pipeline_summary,
                resolve_elapsed, analyze_elapsed, total_elapsed)

    return rd


# ─── API: Smart Match ───

@app.post("/api/smart-match")
async def api_smart_match(request: Request):
    """
    Intelligently match a parsed error to the best candidate stack.

    Used by Fix mode when multiple stacks contain the detected service.
    Instead of asking the user to pick, we figure out which stack most
    likely produced the error based on volume layout, path reachability,
    and error type correlation.

    Returns the best match with confidence level. Frontend auto-selects
    on high/medium confidence, shows pill picker fallback on low.
    """
    try:
        body = await request.json()
    except Exception as exc:
        return JSONResponse(
            {"error": _json_error_detail(exc)},
            status_code=400,
        )

    parsed_error = body.get("parsed_error", {})
    candidate_paths = body.get("candidate_paths", [])

    if not parsed_error or not candidate_paths:
        return JSONResponse(
            {"error": "Need parsed_error and candidate_paths"},
            status_code=400,
        )

    # Build candidate stack dicts from the current discovery data
    custom = _session.get("custom_stacks_path")
    stacks = discover_stacks(custom_path=custom) if custom else discover_stacks()
    stack_map = {s.path: s.to_dict() for s in stacks}

    candidates = []
    for p in candidate_paths:
        # Normalize path separators for matching
        s = stack_map.get(p)
        if not s:
            # Try with backslash normalization
            for key, val in stack_map.items():
                if key.replace("\\", "/") == p.replace("\\", "/"):
                    s = val
                    break
        if s:
            candidates.append(s)

    logger.info("Smart match: %d candidates for service=%s (type=%s, path=%s)",
                 len(candidates), parsed_error.get("service", "?"),
                 parsed_error.get("error_type", "?"), parsed_error.get("path", "?"))
    t0 = time.time()
    result = smart_match(parsed_error, candidates)
    logger.info("Smart match result: confidence=%s best=%s (%.2fs)",
                 result["confidence"],
                 os.path.basename(result["best"].get("path", "?")) if result["best"] else "none",
                 time.time() - t0)

    return {
        "best": result["best"],
        "confidence": result["confidence"],
        "reason": result["reason"],
        "ranked": [
            {"path": r["stack"]["path"], "score": r["score"], "reasons": r["reasons"]}
            for r in result["ranked"]
        ],
    }


# ─── API: Apply Fix ───

@app.post("/api/apply-fix")
async def api_apply_fix(request: Request):
    """
    Apply the corrected compose YAML back to the user's file.

    Safety-first: creates a .bak backup before writing. The frontend
    should confirm with the user before calling this endpoint.

    Accepts the "Your Config (Corrected)" YAML — the patched version of
    the user's original file with only the affected volumes changed.
    This preserves comments, formatting, networks, ports, labels, etc.
    """
    try:
        body = await request.json()
    except Exception as exc:
        return JSONResponse(
            {"error": _json_error_detail(exc)},
            status_code=400,
        )

    compose_file_path = body.get("compose_file_path", "").strip()
    corrected_yaml = body.get("corrected_yaml", "").strip()

    if not compose_file_path:
        return JSONResponse(
            {"error": "No compose_file_path provided"},
            status_code=400,
        )
    if not corrected_yaml:
        return JSONResponse(
            {"error": "No corrected_yaml provided"},
            status_code=400,
        )

    # Input size limit: 1MB max corrected YAML per file
    if len(corrected_yaml) > 1_000_000:
        return JSONResponse(
            {"error": "Corrected YAML too large (max 1MB per file)"},
            status_code=400,
        )

    # Security: validate the path is within the stacks directory.
    # This check MUST come before the file existence check — we want to deny
    # writes based on policy before even confirming the path exists on disk.
    # Write operations require an explicit boundary (MAPARR_STACKS_PATH or
    # custom_stacks_path) — without one, we refuse to write to prevent
    # accidental modifications to arbitrary compose files on the host.
    if not _is_path_within_stacks(compose_file_path, require_root=True):
        stacks_root = _get_stacks_root()
        if not stacks_root:
            logger.warning("Apply fix blocked: no stacks root configured (set MAPARR_STACKS_PATH)")
            return JSONResponse(
                {"error": "Apply Fix requires MAPARR_STACKS_PATH to be set for security. "
                          "Set the environment variable or use Change Path in the UI."},
                status_code=403,
            )
        logger.warning("Apply fix blocked: path outside stacks root: %s", compose_file_path)
        return JSONResponse(
            {"error": "Path is outside the stacks directory"},
            status_code=403,
        )

    if not os.path.isfile(compose_file_path):
        return JSONResponse(
            {"error": f"File not found: {_relative_path_display(compose_file_path)}"},
            status_code=400,
        )

    # Security: validate it's actually a compose file
    if os.path.basename(compose_file_path) not in COMPOSE_FILENAMES:
        logger.warning("Apply fix blocked: not a compose file: %s", compose_file_path)
        return JSONResponse(
            {"error": f"Target is not a recognised compose file. Valid names: {', '.join(sorted(COMPOSE_FILENAMES))}"},
            status_code=400,
        )

    # Validate the corrected YAML is parseable before writing
    try:
        parsed = yaml.safe_load(corrected_yaml)
        if not isinstance(parsed, dict) or "services" not in parsed:
            return JSONResponse(
                {"error": "Corrected YAML doesn't contain a valid services section"},
                status_code=400,
            )
    except yaml.YAMLError as e:
        # Extract line/column if available, don't leak full exception
        mark = getattr(e, 'problem_mark', None)
        if mark:
            return JSONResponse(
                {"error": f"Corrected YAML is not valid (line {mark.line + 1}, column {mark.column + 1}): check indentation and syntax"},
                status_code=400,
            )
        return JSONResponse(
            {"error": "Corrected YAML is not valid: check indentation and syntax"},
            status_code=400,
        )
    except Exception:
        return JSONResponse(
            {"error": "Corrected YAML could not be parsed"},
            status_code=400,
        )

    # Create backup
    backup_path = compose_file_path + ".bak"
    try:
        import shutil
        shutil.copy2(compose_file_path, backup_path)
        logger.info("Apply fix: backup created at %s", backup_path)
    except OSError as e:
        logger.error("Apply fix: backup failed: %s", e)
        return JSONResponse(
            {"error": _categorize_os_error(e, "Failed to create backup")},
            status_code=500,
        )

    # Write the corrected YAML with explicit LF line endings.
    # Docker compose files should use Unix line endings regardless of host OS.
    # newline="" prevents Python from translating \n to \r\n on Windows.
    try:
        with open(compose_file_path, "w", encoding="utf-8", newline="") as f:
            f.write(corrected_yaml.replace("\r\n", "\n"))
        logger.info("Apply fix: wrote corrected YAML to %s", compose_file_path)
    except OSError as e:
        logger.error("Apply fix: write failed: %s", e)
        # Try to restore from backup
        try:
            import shutil
            shutil.copy2(backup_path, compose_file_path)
            logger.info("Apply fix: restored from backup after write failure")
        except Exception:
            pass
        return JSONResponse(
            {"error": f"{_categorize_os_error(e, 'Failed to write file')}. Backup preserved at {os.path.basename(backup_path)}"},
            status_code=500,
        )

    return {
        "status": "applied",
        "compose_file": os.path.basename(compose_file_path),
        "backup_file": os.path.basename(backup_path),
        "message": f"Fix applied to {os.path.basename(compose_file_path)}. Backup saved as {os.path.basename(backup_path)}.",
    }


@app.post("/api/apply-fixes")
async def api_apply_fixes(request: Request):
    """Apply corrected YAML to multiple compose files in one batch."""
    try:
        body = await request.json()
    except Exception as exc:
        return JSONResponse({"error": _json_error_detail(exc)}, status_code=400)

    fixes = body.get("fixes", [])
    if not isinstance(fixes, list):
        return JSONResponse({"error": "fixes must be a list"}, status_code=400)
    if len(fixes) > 20:
        return JSONResponse({"error": "Maximum 20 files per batch"}, status_code=400)

    stacks_root = _get_stacks_root()
    if not stacks_root:
        return JSONResponse(
            {"error": "Apply Fix requires MAPARR_STACKS_PATH to be set for security."},
            status_code=403,
        )

    from backend.apply_multi import apply_fixes_batch
    result = apply_fixes_batch(fixes, stacks_root)

    if result["status"] == "validation_failed":
        return JSONResponse(result, status_code=400)

    return JSONResponse(result)


# ─── API: Redeploy ───

@app.post("/api/redeploy")
async def api_redeploy(request: Request):
    """Redeploy Docker stacks after applying fixes."""
    try:
        body = await request.json()
    except Exception as exc:
        return JSONResponse({"error": _json_error_detail(exc)}, status_code=400)

    stacks = body.get("stacks", [])
    if not isinstance(stacks, list):
        return JSONResponse({"error": "stacks must be a list"}, status_code=400)
    if len(stacks) > 10:
        return JSONResponse({"error": "Maximum 10 stacks per batch"}, status_code=400)

    stacks_root = _get_stacks_root()
    if not stacks_root:
        return JSONResponse(
            {"error": "Redeploy requires MAPARR_STACKS_PATH to be set for security."},
            status_code=403,
        )

    from backend.redeploy import redeploy_stacks
    result = redeploy_stacks(stacks, stacks_root)

    status_code = 200 if result["status"] != "error" else 500
    return JSONResponse(result, status_code=status_code)


# ─── API: Health ───

@app.get("/api/health")
async def health():
    return {"status": "ok", "version": VERSION}


# ─── API: Logs ───

@app.get("/api/logs")
async def api_get_logs(limit: int = 100, level: str = "", since: float = 0):
    """
    Fetch recent log entries from the in-memory buffer.

    Query params:
      limit — max entries to return (default 100, max 500)
      level — minimum level filter: "DEBUG", "INFO", "WARNING", "ERROR"
      since — Unix timestamp, only return entries after this time
    """
    handler = get_log_handler()
    limit = min(limit, 500)
    entries = handler.get_entries(
        limit=limit,
        level=level or None,
        since=since or None,
    )
    return {
        "entries": [e.to_dict() for e in entries],
        "total_buffered": handler.count,
    }


@app.get("/api/logs/stream")
async def api_log_stream(request: Request):
    """
    Server-Sent Events stream for live log entries.

    The frontend connects once and receives log entries as they happen.
    Used by the log panel for real-time updates and by the toast system
    for WARN/ERROR notifications.

    Per-IP concurrent connection cap prevents file descriptor exhaustion
    from runaway clients or browser tab accumulation.
    """
    client_ip = _get_client_ip(request)

    # Enforce per-IP SSE connection limit
    if not _sse_limiter.try_connect(client_ip):
        logger.warning("SSE connection rejected: %s at limit (%d)",
                       client_ip, SSEConnectionLimiter.MAX_PER_IP)
        return JSONResponse(
            {"error": "Too many concurrent log streams"},
            status_code=429,
        )

    async def event_generator():
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)

        def on_log(entry):
            try:
                queue.put_nowait(entry)
            except asyncio.QueueFull:
                pass  # Drop if consumer is too slow

        handler = get_log_handler()
        handler.add_listener(on_log)
        logger.info("Log stream: client connected (SSE) [%s]", client_ip)
        start_time = time.monotonic()
        try:
            # Send initial keepalive
            yield "event: connected\ndata: {}\n\n"
            while True:
                # Hard timeout — force client to reconnect (Grok Elder Council)
                if time.monotonic() - start_time > SSE_HARD_TIMEOUT_SECONDS:
                    yield "event: timeout\ndata: Connection recycled after 5 minutes\n\n"
                    break
                try:
                    entry = await asyncio.wait_for(queue.get(), timeout=30.0)
                    import json
                    data = json.dumps(entry.to_dict())
                    yield f"event: log\ndata: {data}\n\n"
                except asyncio.TimeoutError:
                    # Send keepalive to prevent connection timeout
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            handler.remove_listener(on_log)
            _sse_limiter.disconnect(client_ip)
            logger.debug("Log stream: client disconnected [%s]", client_ip)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ─── Dev Server ───

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("MAPARR_PORT", "9494"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
