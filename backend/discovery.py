"""
discovery.py — Compose file discovery for MapArr v1.0.

Finds docker-compose files on the filesystem and extracts minimal metadata
(service names) for stack selection. This is intentionally shallow parsing —
just enough to populate the stack selection UI.

Deep parsing with `docker compose config` (variable substitution, .env
resolution, extends/include merging) happens in Work Order 2. This module
only needs to answer: "what stacks exist and what services do they contain?"

DOCKER VOLUME STRATEGY:
  When MapArr runs as a Docker container, it cannot see the host filesystem
  directly. Users mount their compose directories into the container:

    docker run -v /path/to/stacks:/stacks:ro -p 3000:3000 maparr

  Discovery then scans /stacks inside the container, which maps to the
  host's compose directory. The :ro flag ensures MapArr never modifies
  compose files — it's read-only analysis.

  For `docker compose config` (WO2), the container also needs:
    -v /var/run/docker.sock:/var/run/docker.sock

  This gives MapArr access to the Docker daemon for resolved config.
  Security trade-off: socket access means MapArr could do anything Docker
  can. Mitigated by running MapArr as a non-root user inside the container
  and documenting the trust model clearly.

SCAN STRATEGY:
  1. Check MAPARR_STACKS_PATH env var (Docker mount point, default /stacks)
  2. Check common host locations (when running directly, not in container)
  3. Scan up to 3 levels deep for compose files
  4. Parse YAML minimally: just extract service names from `services:` key
  5. Skip unreadable files silently (permissions, corrupt YAML)
"""

import os
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import yaml

logger = logging.getLogger("maparr.discovery")

# Compose file names to look for, in priority order.
COMPOSE_FILENAMES = [
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
]

# Maximum file size to attempt parsing (10 MB). Protects against
# accidentally scanning a binary file or YAML bomb.
MAX_COMPOSE_FILE_SIZE = 10 * 1024 * 1024

# Maximum directory depth to scan. Keeps discovery fast and bounded.
MAX_SCAN_DEPTH = 3


@dataclass
class Stack:
    """A discovered Docker stack (directory containing compose files)."""
    path: str                          # Absolute path to stack directory
    compose_file: str                  # Path to the compose file used
    services: List[str] = field(default_factory=list)  # Service names
    service_count: int = 0             # Number of services
    source: str = "scan"               # How we found it: "env", "common", "scan", "custom"
    error: Optional[str] = None        # Parse error (stack still returned)
    health: str = "unknown"            # Quick health: "ok", "warning", "problem", "unknown"
    health_hint: str = ""              # Brief explanation of health status

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "compose_file": self.compose_file,
            "services": self.services,
            "service_count": self.service_count,
            "source": self.source,
            "error": self.error,
            "health": self.health,
            "health_hint": self.health_hint,
        }


def discover_stacks(custom_path: Optional[str] = None) -> List[Stack]:
    """
    Find all Docker stacks on the filesystem.

    Args:
        custom_path: If provided, scan ONLY this directory (user override).
                     If None, use MAPARR_STACKS_PATH + common locations.

    Returns a deduplicated list of stacks sorted by service count (largest
    first), so the most interesting stacks appear at the top of the UI.
    """
    stacks: List[Stack] = []
    seen_paths: set = set()

    if custom_path:
        # User specified a custom path — scan only that
        if os.path.isdir(custom_path):
            _scan_directory(custom_path, stacks, seen_paths, source="custom")
    else:
        # 1. Check MAPARR_STACKS_PATH (Docker container mount point)
        stacks_env = os.environ.get("MAPARR_STACKS_PATH", "")
        if stacks_env and os.path.isdir(stacks_env):
            _scan_directory(stacks_env, stacks, seen_paths, source="env")

        # 2. Check common host locations (when running outside Docker)
        for search_path in _get_search_paths():
            if os.path.isdir(search_path):
                _scan_directory(search_path, stacks, seen_paths, source="common")

    # Sort: largest stacks first (most useful for analysis)
    stacks.sort(key=lambda s: s.service_count, reverse=True)

    return stacks


def _get_search_paths() -> List[str]:
    """
    Return platform-appropriate paths to scan for compose files.

    Checks environment variables and well-known locations. Skips paths
    that don't exist (the caller checks os.path.isdir anyway).
    """
    paths = []
    home = Path.home()

    # Cross-platform: user's home docker directories
    paths.extend([
        str(home / "docker"),
        str(home / "stacks"),
        str(home / "compose"),
        str(home / "Docker"),
    ])

    # Current working directory (useful for development)
    cwd = os.getcwd()
    if cwd:
        paths.append(cwd)

    # Linux-specific
    if os.name != "nt":
        paths.extend([
            "/opt/docker",
            "/opt/stacks",
            "/srv/docker",
            "/srv/stacks",
        ])

    # Windows-specific
    if os.name == "nt":
        for drive in ["C", "D", "E"]:
            paths.extend([
                f"{drive}:\\Docker",
                f"{drive}:\\DockerContainers",
                f"{drive}:\\docker",
            ])
        # Expand %USERPROFILE%
        userprofile = os.environ.get("USERPROFILE", "")
        if userprofile:
            paths.append(os.path.join(userprofile, "docker"))
            paths.append(os.path.join(userprofile, "Docker"))

    return paths


def _scan_directory(
    root: str,
    stacks: List[Stack],
    seen: set,
    source: str = "scan",
    depth: int = 0,
) -> None:
    """
    Recursively scan a directory for compose files.

    Stops at MAX_SCAN_DEPTH to keep discovery fast. Each directory
    containing a compose file becomes one Stack entry.
    """
    if depth > MAX_SCAN_DEPTH:
        return

    root_path = Path(root)
    if not root_path.is_dir():
        return

    # Normalize path for deduplication
    real_path = str(root_path.resolve())
    if real_path in seen:
        return

    # Check for compose files in this directory
    for filename in COMPOSE_FILENAMES:
        compose_path = root_path / filename
        if compose_path.is_file():
            seen.add(real_path)
            stack = _parse_compose_minimal(str(compose_path), source)
            if stack:
                stacks.append(stack)
            # Don't scan deeper once we find a compose file in this dir
            return

    # No compose file here — scan subdirectories
    try:
        entries = sorted(root_path.iterdir())
    except PermissionError:
        return

    for entry in entries:
        if not entry.is_dir():
            continue
        # Skip hidden dirs and known non-compose directories
        name = entry.name
        if name.startswith(".") or name in _SKIP_DIRS:
            continue
        _scan_directory(str(entry), stacks, seen, source, depth + 1)


# Directories to skip during scanning. These never contain compose files
# and scanning them wastes time or causes permission errors.
_SKIP_DIRS = {
    "node_modules", "__pycache__", ".git", ".venv", "venv",
    "vendor", "dist", "build", ".cache", "logs", "log",
    "Library", "AppData", "Application Support",
}


def _parse_compose_minimal(compose_path: str, source: str) -> Optional[Stack]:
    """
    Parse a compose file just enough to extract service names.

    This is intentionally shallow. We don't resolve variables, don't
    follow includes, don't validate structure. Just: does it have a
    `services` key, and what are the service names?

    Deep resolution happens in WO2 via `docker compose config`.
    """
    try:
        file_size = os.path.getsize(compose_path)
        if file_size > MAX_COMPOSE_FILE_SIZE:
            return Stack(
                path=str(Path(compose_path).parent),
                compose_file=compose_path,
                source=source,
                error=f"File too large ({file_size // 1024 // 1024} MB)",
            )

        with open(compose_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if not isinstance(data, dict) or "services" not in data:
            return None  # Not a compose file (or empty/malformed)

        services_raw = data.get("services", {})
        if not isinstance(services_raw, dict):
            return None

        service_names = list(services_raw.keys())

        # Quick health check — lightweight volume analysis
        health, health_hint = _quick_health_check(service_names, services_raw)

        return Stack(
            path=str(Path(compose_path).parent),
            compose_file=compose_path,
            services=service_names,
            service_count=len(service_names),
            source=source,
            health=health,
            health_hint=health_hint,
        )

    except yaml.YAMLError as e:
        return Stack(
            path=str(Path(compose_path).parent),
            compose_file=compose_path,
            source=source,
            error=f"YAML parse error: {e}",
        )
    except PermissionError:
        return Stack(
            path=str(Path(compose_path).parent),
            compose_file=compose_path,
            source=source,
            error="Permission denied",
        )
    except Exception as e:
        logger.debug(f"Error parsing {compose_path}: {e}")
        return None


# ─── Quick Health Check ───

# Services that participate in hardlink workflows
_HL_ARR = {"sonarr", "radarr", "lidarr", "readarr", "whisparr", "bazarr"}
_HL_DL = {"qbittorrent", "sabnzbd", "nzbget", "transmission", "deluge", "rtorrent", "jdownloader"}
_HL_MEDIA = {"plex", "jellyfin", "emby"}
_HL_ALL = _HL_ARR | _HL_DL | _HL_MEDIA

# Config/system container paths to skip
_CONFIG_TARGETS = {"/config", "/app", "/etc", "/var", "/tmp", "/run", "/dev"}


def _quick_health_check(
    service_names: List[str],
    services_raw: dict,
) -> tuple:
    """
    Lightweight health check using raw YAML data (no docker compose config).

    Returns (health, health_hint) where health is one of:
      "ok"      — shared parent mount detected across participants (green)
      "warning" — can't fully determine / single participant / no data vols (yellow)
      "problem" — separate mount trees detected (red)
      "unknown" — no hardlink participants in this stack (grey)
    """
    # Identify hardlink participants by name
    participants = {}  # service_name -> set of host path roots
    for name in service_names:
        name_lower = name.lower()
        is_participant = any(app in name_lower for app in _HL_ALL)
        if not is_participant:
            continue

        config = services_raw.get(name, {})
        if not isinstance(config, dict):
            continue

        volumes = config.get("volumes", [])
        roots = _extract_host_roots(volumes)
        if roots:
            participants[name] = roots

    # No hardlink participants → unknown (infrastructure stack)
    if not participants:
        return "unknown", ""

    # Only one participant with data volumes → warning
    if len(participants) < 2:
        return "warning", "Single media service — analyze for full picture"

    # Check for shared roots
    all_root_sets = list(participants.values())
    common = all_root_sets[0]
    for roots in all_root_sets[1:]:
        common = common & roots

    if common:
        return "ok", "Shared mount detected"
    else:
        return "problem", "Separate mount trees — hardlinks will fail"


def _extract_host_roots(volumes: list) -> set:
    """Extract host path roots from volume declarations (quick and dirty)."""
    roots = set()

    for vol in volumes:
        source = ""
        target = ""

        if isinstance(vol, str):
            parts = vol.split(":")
            if len(parts) < 2:
                continue
            # Handle Windows paths (C:\path:...)
            if len(parts) >= 3 and len(parts[0]) == 1 and parts[0].isalpha():
                source = parts[0] + ":" + parts[1]
                target = parts[2]
            else:
                source = parts[0]
                target = parts[1]
        elif isinstance(vol, dict):
            source = vol.get("source", "")
            target = vol.get("target", "")

        if not source or not target:
            continue

        # Skip config mounts
        target_clean = target.rstrip("/").split(":")[0]  # strip :ro etc
        if any(target_clean == c or target_clean.startswith(c + "/") for c in _CONFIG_TARGETS):
            continue

        # Skip named volumes (no path separator)
        if not (source.startswith("/") or source.startswith("./") or
                source.startswith("../") or source.startswith("~") or
                (len(source) >= 2 and source[1] == ":")):
            continue

        # Get root (first 2 meaningful path components)
        root = _get_quick_root(source)
        if root:
            roots.add(root)

    return roots


def _get_quick_root(path: str) -> Optional[str]:
    """Get first 2 meaningful components of a path for root comparison."""
    path = path.replace("\\", "/").rstrip("/")
    if not path:
        return None

    if path.startswith("/"):
        parts = [p for p in path.split("/") if p]
        if len(parts) >= 2:
            return "/" + "/".join(parts[:2])
        elif parts:
            return "/" + parts[0]
        return "/"

    # Relative or Windows
    parts = path.split("/")
    if len(parts) >= 2:
        return "/".join(parts[:2])
    return parts[0] if parts else None
