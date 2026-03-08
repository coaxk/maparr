"""
Docker Compose redeploy — restart services after applying fixes.

Uses subprocess with list-form args (no shell injection).
Validates stack paths within boundary before executing.
"""
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

COMPOSE_FILENAMES = [
    "docker-compose.yml", "docker-compose.yaml",
    "compose.yml", "compose.yaml",
]


def find_compose_file(stack_path: str) -> Optional[str]:
    """Find the compose file in a stack directory."""
    for name in COMPOSE_FILENAMES:
        path = os.path.join(stack_path, name)
        if os.path.isfile(path):
            return path
    return None


def validate_for_redeploy(stack_path: str, stacks_root: str) -> List[str]:
    """
    Validate a stack is safe to redeploy.

    Returns list of error strings. Empty = valid.
    """
    errors = []
    root = Path(stacks_root).resolve()

    try:
        Path(stack_path).resolve().relative_to(root)
    except ValueError:
        errors.append("Stack path outside stacks directory")
        return errors

    if not os.path.isdir(stack_path):
        errors.append(f"Directory not found: {stack_path}")
        return errors

    compose = find_compose_file(stack_path)
    if not compose:
        errors.append(f"No compose file found in {os.path.basename(stack_path)}")

    return errors


def run_compose_action(stack_path: str, compose_file: str, action: str) -> dict:
    """
    Run a docker compose command for a stack.

    Args:
        stack_path: Directory of the stack (cwd for command)
        compose_file: Full path to compose file
        action: "up", "restart", or "pull"

    Returns:
        {"status": "success"|"error", "duration_ms": int, "output": str, "error"?: str}
    """
    if action == "up":
        cmd = ["docker", "compose", "-f", compose_file, "up", "-d"]
    elif action == "restart":
        cmd = ["docker", "compose", "-f", compose_file, "restart"]
    elif action == "pull":
        cmd = ["docker", "compose", "-f", compose_file, "pull"]
    else:
        raise ValueError(f"Unknown action: {action}")

    t0 = time.time()
    try:
        result = subprocess.run(
            cmd,
            cwd=stack_path,
            capture_output=True,
            text=True,
            timeout=120,
        )
        duration_ms = int((time.time() - t0) * 1000)

        if result.returncode == 0:
            logger.info("Redeploy %s (%s): success in %dms", os.path.basename(stack_path), action, duration_ms)
            return {"status": "success", "duration_ms": duration_ms, "output": result.stdout.strip()}
        else:
            logger.warning("Redeploy %s (%s): failed — %s", os.path.basename(stack_path), action, result.stderr.strip())
            return {"status": "error", "duration_ms": duration_ms, "output": result.stdout.strip(), "error": result.stderr.strip()}

    except subprocess.TimeoutExpired:
        duration_ms = int((time.time() - t0) * 1000)
        logger.error("Redeploy %s (%s): timeout after %ds", os.path.basename(stack_path), action, 120)
        return {"status": "error", "duration_ms": duration_ms, "error": "Timeout after 120s"}

    except FileNotFoundError:
        duration_ms = int((time.time() - t0) * 1000)
        logger.error("Docker compose not found — is Docker installed?")
        return {"status": "error", "duration_ms": duration_ms, "error": "docker compose not found. Is Docker installed and in PATH?"}

    except Exception as e:
        duration_ms = int((time.time() - t0) * 1000)
        logger.error("Redeploy %s (%s): unexpected error — %s", os.path.basename(stack_path), action, e)
        return {"status": "error", "duration_ms": duration_ms, "error": str(e)}


def redeploy_stacks(stacks: List[dict], stacks_root: str) -> dict:
    """
    Redeploy multiple stacks.

    Args:
        stacks: [{"stack_path": str, "action": "up"|"restart"|"pull"}, ...]
        stacks_root: Boundary for path validation

    Returns:
        {"status", "results": [...], "summary": str}
    """
    results = []
    succeeded = 0
    failed = 0

    for entry in stacks:
        stack_path = entry.get("stack_path", "").strip()
        action = entry.get("action", "up").strip()

        # Validate
        errors = validate_for_redeploy(stack_path, stacks_root)
        if errors:
            results.append({
                "stack_path": stack_path,
                "stack_name": os.path.basename(stack_path),
                "action": action,
                "status": "error",
                "error": "; ".join(errors),
            })
            failed += 1
            continue

        compose_file = find_compose_file(stack_path)
        result = run_compose_action(stack_path, compose_file, action)
        result["stack_path"] = stack_path
        result["stack_name"] = os.path.basename(stack_path)
        result["action"] = action
        results.append(result)

        if result["status"] == "success":
            succeeded += 1
        else:
            failed += 1

    status = "success" if failed == 0 else "partial" if succeeded > 0 else "error"
    summary = f"{succeeded} succeeded, {failed} failed" if results else "No stacks to redeploy"

    return {
        "status": status,
        "results": results,
        "summary": summary,
    }
