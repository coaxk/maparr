"""
MapArr v1.0 — Path Mapping Problem Solver

Lean FastAPI backend. Four jobs:
  1. Parse error text (extract service + path + error type)
  2. Discover compose stacks on the filesystem
  3. Accept stack selection
  4. Analyze stack: resolve compose, detect conflicts, generate fix

No Docker SDK dependency. No SQLite. No SSE streaming.
No history, no persistence, no jobs system.
"""

import logging
import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.parser import parse_error
from backend.discovery import discover_stacks
from backend.resolver import resolve_compose, ResolveError
from backend.analyzer import analyze_stack

# ─── Logging ───

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("maparr")

# ─── App ───

app = FastAPI(
    title="MapArr",
    description="Path Mapping Problem Solver for *arr apps",
    version="1.0.0",
)

# ─── State ───
# Minimal in-memory state for the current session.
# No persistence — MapArr is a single-use problem solver.

_session = {
    "parsed_error": None,
    "selected_stack": None,
}


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
    except Exception:
        return JSONResponse(
            {"error": "Invalid JSON in request body"},
            status_code=400,
        )

    error_text = body.get("error_text", "").strip()
    if not error_text:
        return JSONResponse(
            {"error": "No error text provided"},
            status_code=400,
        )

    # Parse — always succeeds, returns confidence level
    result = parse_error(error_text)
    _session["parsed_error"] = result.to_dict()

    return result.to_dict()


# ─── API: Discover Stacks ───

@app.get("/api/discover-stacks")
async def api_discover_stacks():
    """
    Find Docker compose stacks on the filesystem.

    Scans MAPARR_STACKS_PATH (Docker mount), common locations, and CWD.
    Returns stacks with service names for the selection UI.

    This is shallow discovery — just enough to populate the stack list.
    Deep resolution via `docker compose config` happens in WO2.
    """
    stacks = discover_stacks()

    return {
        "stacks": [s.to_dict() for s in stacks],
        "total": len(stacks),
        "search_note": _get_search_note(),
    }


def _get_search_note() -> str:
    """Generate a human-readable note about where we searched."""
    stacks_env = os.environ.get("MAPARR_STACKS_PATH", "")
    if stacks_env:
        return f"Scanned mounted path: {stacks_env}"
    return "Scanned common locations. Set MAPARR_STACKS_PATH to specify your stacks directory."


# ─── API: Select Stack ───

@app.post("/api/select-stack")
async def api_select_stack(request: Request):
    """
    User selected a stack for analysis.

    Stores the selection in session state. WO2 will use this to run
    `docker compose config` on the selected stack and perform deep analysis.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            {"error": "Invalid JSON in request body"},
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
            {"error": f"Directory not found: {os.path.basename(stack_path)}"},
            status_code=400,
        )

    _session["selected_stack"] = {
        "stack_path": stack_path,
        "parsed_error": _session.get("parsed_error"),
    }

    return {
        "status": "ready",
        "stack_path": stack_path,
        "parsed_error": _session.get("parsed_error"),
        "next_step": "Analysis engine (Work Order 2)",
    }


# ─── API: Analyze Stack (WO2) ───

@app.post("/api/analyze")
async def api_analyze(request: Request):
    """
    Full stack analysis: resolve compose, detect conflicts, generate fix.

    This is where MapArr delivers its value. Takes the stack path and
    optional error context from WO1, resolves the compose file, analyzes
    volume mounts, detects path conflicts, and returns specific fixes.

    Always returns 200 with results — errors are reported in the response
    body with appropriate context, never as dead-end HTTP errors.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            {"error": "Invalid JSON in request body"},
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
            {"error": f"Directory not found: {os.path.basename(stack_path)}"},
            status_code=400,
        )

    # Get error context (optional — from WO1 parse step)
    error_info = body.get("error", _session.get("parsed_error"))
    error_service = None
    error_path = None
    if isinstance(error_info, dict):
        error_service = error_info.get("service")
        error_path = error_info.get("path")

    # Step 1: Resolve compose file
    try:
        resolved = resolve_compose(stack_path)
    except ResolveError as e:
        return JSONResponse({
            "status": "error",
            "error": str(e),
            "stage": "resolution",
            "stack_path": os.path.basename(stack_path),
        }, status_code=200)

    # Step 2: Analyze
    try:
        result = analyze_stack(
            resolved_compose=resolved,
            stack_path=stack_path,
            compose_file=resolved.get("_compose_file", ""),
            resolution_method=resolved.get("_resolution", "unknown"),
            error_service=error_service,
            error_path=error_path,
        )
    except Exception as e:
        logger.exception("Analysis failed for %s", os.path.basename(stack_path))
        return JSONResponse({
            "status": "error",
            "error": f"Analysis failed: {e}",
            "stage": "analysis",
            "stack_path": os.path.basename(stack_path),
        }, status_code=200)

    return result.to_dict()


# ─── API: Health ───

@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "1.0.0"}


# ─── Dev Server ───

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9494, log_level="info")
