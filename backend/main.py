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
from backend.smart_match import smart_match

# ─── Logging ───

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("maparr")

# ─── Version ───
# Single source of truth — used in FastAPI metadata and /api/health.
# Frontend reads this via the health endpoint on page load.
VERSION = "1.0.0"

# ─── App ───

app = FastAPI(
    title="MapArr",
    description="Path Mapping Problem Solver for *arr apps",
    version=VERSION,
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
    custom = _session.get("custom_stacks_path")
    if custom:
        stacks = discover_stacks(custom_path=custom)
    else:
        stacks = discover_stacks()

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
    except Exception:
        return JSONResponse(
            {"error": "Invalid JSON in request body"},
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

    _session["custom_stacks_path"] = new_path
    logger.info("Stacks path changed to: %s", new_path)

    # Run discovery on the new path immediately
    stacks = discover_stacks(custom_path=new_path)

    return {
        "status": "ok",
        "path": new_path,
        "stacks": [s.to_dict() for s in stacks],
        "total": len(stacks),
        "search_note": _get_search_note(new_path),
    }


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
    steps = [
        {"icon": "run", "text": f"Resolving compose for {os.path.basename(stack_path)}..."},
    ]
    try:
        resolved = resolve_compose(stack_path)
    except ResolveError as e:
        steps.append({"icon": "fail", "text": f"Resolution failed: {e}"})
        return JSONResponse({
            "status": "error",
            "error": str(e),
            "stage": "resolution",
            "stack_path": os.path.basename(stack_path),
            "steps": steps,
        }, status_code=200)

    # Read raw compose content for patching in the "Your Config (Corrected)" tab
    raw_compose_content = None
    compose_file_path = resolved.get("_compose_file", "")
    if compose_file_path:
        try:
            raw_compose_content = Path(compose_file_path).read_text(encoding="utf-8")
        except Exception:
            pass

    # Step 2: Analyze
    try:
        result = analyze_stack(
            resolved_compose=resolved,
            stack_path=stack_path,
            compose_file=resolved.get("_compose_file", ""),
            resolution_method=resolved.get("_resolution", "unknown"),
            error_service=error_service,
            error_path=error_path,
            raw_compose_content=raw_compose_content,
        )
    except Exception as e:
        logger.exception("Analysis failed for %s", os.path.basename(stack_path))
        steps.append({"icon": "fail", "text": f"Analysis failed: {e}"})
        return JSONResponse({
            "status": "error",
            "error": f"Analysis failed: {e}",
            "stage": "analysis",
            "stack_path": os.path.basename(stack_path),
            "steps": steps,
        }, status_code=200)

    return result.to_dict()


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
    except Exception:
        return JSONResponse(
            {"error": "Invalid JSON in request body"},
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

    result = smart_match(parsed_error, candidates)

    return {
        "best": result["best"],
        "confidence": result["confidence"],
        "reason": result["reason"],
        "ranked": [
            {"path": r["stack"]["path"], "score": r["score"], "reasons": r["reasons"]}
            for r in result["ranked"]
        ],
    }


# ─── API: Health ───

@app.get("/api/health")
async def health():
    return {"status": "ok", "version": VERSION}


# ─── Dev Server ───

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9494, log_level="info")
