"""
resolver.py — Compose file resolution for MapArr.

Two strategies, tried in order:
  1. `docker compose config` — fully resolved YAML (env vars, extends, includes)
  2. Raw YAML + manual .env parsing — works without Docker CLI

Strategy 1 is ideal but requires Docker CLI + compose plugin. Strategy 2
covers the common case: simple compose files with ${VAR:-default} syntax
and a .env file in the same directory. It won't handle extends/include
but those are rare in *arr stacks.

The caller doesn't need to know which strategy was used. Both return the
same shape: a dict with a `services` key.
"""

import logging
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

logger = logging.getLogger("maparr.resolver")

# Compose file names to try, in priority order.
COMPOSE_FILENAMES = [
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
]


def resolve_compose(
    stack_path: str,
    compose_file: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Resolve a compose file to a fully-substituted dict.

    Tries `docker compose config` first for perfect resolution.
    Falls back to raw YAML + .env parsing if Docker CLI is unavailable.

    Returns:
        Dict with at least a "services" key. Also includes:
        - "_resolution": "docker" or "manual"
        - "_compose_file": path to the compose file used
        - "_warnings": list of non-fatal issues encountered
    """
    stack = Path(stack_path)
    warnings: List[str] = []

    # Find compose file
    if compose_file:
        cf_path = stack / compose_file
        if not cf_path.is_file():
            raise ResolveError(f"Compose file not found: {compose_file}")
        logger.info("Resolving specified compose file: %s", compose_file)
    else:
        cf_path = _find_compose_file(stack)
        if not cf_path:
            raise ResolveError(
                f"No compose file found in {stack.name}. "
                f"Looked for: {', '.join(COMPOSE_FILENAMES)}"
            )
        logger.info("Found compose file: %s in %s/", cf_path.name, stack.name)

    # Strategy 1: docker compose config
    logger.info("Attempting resolution via docker compose config...")
    t0 = time.time()
    result = _try_docker_compose_config(stack, cf_path)
    if result is not None:
        svc_count = len(result.get("services", {}))
        logger.info("Docker compose config succeeded: %d services resolved (%.2fs)",
                    svc_count, time.time() - t0)
        result["_resolution"] = "docker"
        result["_compose_file"] = str(cf_path)
        result["_warnings"] = warnings
        return result

    # Provide actionable guidance based on why docker compose config failed.
    # Common cause: socket proxy blocking the compose endpoint, Docker not
    # installed, or DOCKER_HOST pointing to an unreachable daemon.
    docker_host = os.environ.get("DOCKER_HOST", "")
    if docker_host:
        logger.warning("Resolution fallback for %s — docker compose config failed "
                       "(DOCKER_HOST=%s). If using a socket proxy, ensure compose "
                       "endpoints are allowed.", stack.name, docker_host)
        warnings.append(
            "docker compose config failed (DOCKER_HOST=" + docker_host + "). "
            "If using a socket proxy, ensure it allows compose API access. "
            "Falling back to manual resolution — extends/include won't resolve."
        )
    else:
        logger.warning("Resolution fallback: docker compose config unavailable for %s — "
                       "using manual YAML + .env parsing (extends/include won't resolve)",
                       stack.name)
        warnings.append(
            "docker compose config unavailable — using manual resolution. "
            "extends/include directives won't be resolved."
        )

    # Strategy 2: Raw YAML + .env
    t0_manual = time.time()
    result = _resolve_manual(stack, cf_path)
    svc_count = len(result.get("services", {}))
    logger.info("Manual resolution succeeded: %d services parsed (%.2fs)",
                svc_count, time.time() - t0_manual)
    result["_resolution"] = "manual"
    result["_compose_file"] = str(cf_path)
    result["_warnings"] = warnings
    return result


class ResolveError(Exception):
    """Raised when compose resolution fails completely."""
    pass


# ─── Strategy 1: docker compose config ───


def _try_docker_compose_config(
    stack: Path, compose_file: Path
) -> Optional[Dict[str, Any]]:
    """
    Run `docker compose config` and parse the output.

    Returns None if Docker CLI is unavailable or the command fails.
    This lets the caller fall back to manual resolution.
    """
    try:
        cmd = [
            "docker", "compose",
            "-f", str(compose_file),
            "config",
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(stack),
            timeout=30,
        )

        if result.returncode != 0:
            logger.info(
                "docker compose config failed (rc=%d): %s",
                result.returncode,
                result.stderr.strip()[:200],
            )
            return None

        data = yaml.safe_load(result.stdout)
        if not isinstance(data, dict) or "services" not in data:
            logger.info("docker compose config returned unexpected output")
            return None

        return data

    except FileNotFoundError:
        # Docker CLI not installed
        logger.info("Docker CLI not found — falling back to manual resolution")
        return None
    except subprocess.TimeoutExpired:
        logger.warning("docker compose config timed out after 30s")
        return None
    except Exception as e:
        logger.info("docker compose config error: %s", e)
        return None


# ─── Strategy 2: Manual YAML + .env ───


def _resolve_manual(stack: Path, compose_file: Path) -> Dict[str, Any]:
    """
    Parse compose YAML and substitute variables from .env file.

    Handles:
      - ${VAR} and $VAR syntax
      - ${VAR:-default} default values
      - ${VAR:?error} required variables (warns but doesn't fail)
      - .env file in the stack directory
      - System environment variables as fallback

    Does NOT handle:
      - extends/include directives
      - Multiple compose file merging
      - Profiles
    """
    # Load .env
    env_vars = _load_env_file(stack)
    if env_vars:
        logger.info("Loaded %d variables from .env file", len(env_vars))

    # Read and parse YAML
    try:
        raw_content = compose_file.read_text(encoding="utf-8")
    except Exception as e:
        raise ResolveError(f"Cannot read {compose_file.name}: {e}")

    # Substitute variables in the raw YAML string before parsing.
    # This handles vars inside quoted strings that YAML wouldn't see.
    substituted = _substitute_vars(raw_content, env_vars)

    try:
        data = yaml.safe_load(substituted)
    except yaml.YAMLError as e:
        raise ResolveError(f"Invalid YAML in {compose_file.name}: {e}")

    if not isinstance(data, dict):
        raise ResolveError(f"{compose_file.name} is not a valid compose file")

    if "services" not in data:
        raise ResolveError(
            f"{compose_file.name} has no 'services' key — not a compose file"
        )

    if not isinstance(data["services"], dict):
        raise ResolveError(
            f"{compose_file.name} has invalid 'services' — expected a mapping, "
            f"got {type(data['services']).__name__}"
        )

    return data


def _load_env_file(stack: Path) -> Dict[str, str]:
    """
    Load .env file from the stack directory.

    Handles:
      - KEY=VALUE (with optional quotes)
      - Comments (#)
      - Empty lines
      - Inline comments (KEY=VALUE # comment)
    """
    env_path = stack / ".env"
    env_vars: Dict[str, str] = {}

    if not env_path.is_file():
        return env_vars

    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            # Split on first =
            if "=" not in line:
                continue

            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()

            # Remove surrounding quotes
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]

            # Strip inline comments (only for unquoted values)
            if " #" in value and not (value.startswith('"') or value.startswith("'")):
                value = value.split(" #")[0].strip()

            if key:
                env_vars[key] = value

    except Exception as e:
        logger.warning("Error reading .env file: %s", e)

    return env_vars


def _substitute_vars(content: str, env_vars: Dict[str, str]) -> str:
    """
    Replace ${VAR}, ${VAR:-default}, ${VAR:?err}, and $VAR in content.

    Resolution order:
      1. .env file values (env_vars dict)
      2. System environment variables
      3. Default value (if :-default syntax)
      4. Empty string (last resort)
    """

    def replace_match(match: re.Match) -> str:
        expr = match.group(1) if match.group(1) else match.group(2)

        if not expr:
            return match.group(0)

        # ${VAR:-default}
        if ":-" in expr:
            var_name, _, default = expr.partition(":-")
            return _lookup_var(var_name, env_vars, default)

        # ${VAR:?error} — required, but we just warn and use empty
        if ":?" in expr:
            var_name, _, _ = expr.partition(":?")
            value = _lookup_var(var_name, env_vars, None)
            if value is None:
                logger.warning("Required variable %s is not set", var_name)
                return ""
            return value

        # ${VAR} or $VAR
        return _lookup_var(expr, env_vars, "")

    # Match ${...} and $VAR (but not $$)
    pattern = r'\$\{([^}]+)\}|\$([A-Za-z_][A-Za-z0-9_]*)'
    return re.sub(pattern, replace_match, content)


def _lookup_var(
    name: str, env_vars: Dict[str, str], default: Optional[str]
) -> str:
    """Look up a variable: .env → system env → default → empty."""
    if name in env_vars:
        return env_vars[name]
    if name in os.environ:
        return os.environ[name]
    if default is not None:
        return default
    return ""


def _find_compose_file(stack: Path) -> Optional[Path]:
    """Find the first matching compose file in a directory."""
    for name in COMPOSE_FILENAMES:
        path = stack / name
        if path.is_file():
            return path
    return None
