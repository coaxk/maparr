"""
discovery.py — Compose file discovery for MapArr.

Finds docker-compose files on the filesystem and extracts minimal metadata
(service names) for stack selection. This is intentionally shallow parsing —
just enough to populate the stack selection UI.

Deep parsing with `docker compose config` (variable substitution, .env
resolution, extends/include merging) happens in the analyze endpoint. This module
only needs to answer: "what stacks exist and what services do they contain?"

DOCKER VOLUME STRATEGY:
  When MapArr runs as a Docker container, it cannot see the host filesystem
  directly. Users mount their compose directories into the container:

    docker run -v /path/to/stacks:/stacks:ro -p 3000:3000 maparr

  Discovery then scans /stacks inside the container, which maps to the
  host's compose directory. The :ro flag ensures MapArr never modifies
  compose files — it's read-only analysis.

  For `docker compose config` (analysis), the container also needs:
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
    volume_targets: List[str] = field(default_factory=list)  # Container-side data mount targets (for error path matching)

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
            "volume_targets": self.volume_targets,
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
        logger.info("Discovery: scanning custom path %s", custom_path)
        if os.path.isdir(custom_path):
            _scan_directory(custom_path, stacks, seen_paths, source="custom")
    else:
        # 1. Check MAPARR_STACKS_PATH (Docker container mount point)
        stacks_env = os.environ.get("MAPARR_STACKS_PATH", "")
        if stacks_env and os.path.isdir(stacks_env):
            logger.info("Discovery: scanning MAPARR_STACKS_PATH=%s", stacks_env)
            _scan_directory(stacks_env, stacks, seen_paths, source="env")

        # 2. Check common host locations (when running outside Docker)
        for search_path in _get_search_paths():
            if os.path.isdir(search_path):
                _scan_directory(search_path, stacks, seen_paths, source="common")

    logger.info("Discovery: found %d stacks (%d with media services)",
                len(stacks),
                sum(1 for s in stacks if s.health != "unknown"))

    # Cross-stack health pass: upgrade single-service media stacks
    # when complementary services exist in sibling stacks with compatible mounts
    upgraded = _cross_stack_health_pass(stacks)
    if upgraded > 0:
        logger.info("Health pass: %d stacks upgraded via cross-stack mount check", upgraded)

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

    Deep resolution happens in the analyze endpoint via `docker compose config`.
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

        # Extract container-side volume targets for error path matching
        volume_targets = _extract_volume_targets(services_raw)

        return Stack(
            path=str(Path(compose_path).parent),
            compose_file=compose_path,
            services=service_names,
            service_count=len(service_names),
            source=source,
            health=health,
            health_hint=health_hint,
            volume_targets=volume_targets,
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

# Config/system/utility container paths to skip — not part of the media data pipeline.
# These are ancillary mounts (config, backups, logs, caches) that should not
# influence data-path analysis or cross-stack mount comparison.
_CONFIG_TARGETS = {
    "/config", "/app", "/etc", "/var", "/tmp", "/run", "/dev",
    "/backup", "/backups", "/restore", "/log", "/logs",
    "/cache", "/certs", "/ssl", "/scripts",
}


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
    participants = {}       # service_name -> set of normalized source paths
    named_vol_only = []     # participants with only named volumes
    has_named_vols = False  # any participant uses named volumes for data

    for name in service_names:
        name_lower = name.lower()
        is_participant = any(app in name_lower for app in _HL_ALL)
        if not is_participant:
            continue

        config = services_raw.get(name, {})
        if not isinstance(config, dict):
            continue

        volumes = config.get("volumes", [])
        sources, has_named = _extract_host_sources(volumes)
        if has_named:
            has_named_vols = True
        if sources:
            participants[name] = sources
        elif has_named:
            named_vol_only.append(name)

    # Participants with ONLY named volumes → problem
    if named_vol_only and not participants:
        return "problem", "Named volumes used — hardlinks impossible"

    # No hardlink participants at all → unknown (infrastructure stack)
    if not participants and not named_vol_only:
        return "unknown", ""

    # Some have named volumes mixed with bind mounts → warning at minimum
    if named_vol_only:
        return "problem", "Mixed named volumes and bind mounts"

    # Only one participant with data volumes → warning
    if len(participants) < 2:
        return "warning", "Single media service — analyze for full picture"

    # Check if all participants share a common mount source
    # Use FULL source paths — not just roots (roots are too coarse)
    all_source_sets = list(participants.values())

    # First: check if any single source path is shared by ALL participants
    common = all_source_sets[0]
    for sources in all_source_sets[1:]:
        common = common & sources

    if common:
        return "ok", "Shared mount detected"

    # Second: check if all sources share a common PARENT
    # Only flag as OK if ALL sources from ALL participants fall under one tree
    all_sources = set()
    for sources in all_source_sets:
        all_sources.update(sources)

    # Check if any source is a parent that encompasses ALL sources from ALL services
    for candidate in sorted(all_sources, key=len):
        candidate_norm = candidate.rstrip("/") + "/"
        all_under_candidate = True
        for sources in all_source_sets:
            # Every source in this participant must be under the candidate
            if not all(s == candidate or s.startswith(candidate_norm) for s in sources):
                all_under_candidate = False
                break
        if all_under_candidate:
            return "ok", "Shared mount detected"

    return "problem", "Separate mount trees — hardlinks will fail"


def _extract_host_sources(volumes: list) -> tuple:
    """
    Extract normalized host source paths from volume declarations.

    Returns (sources: set, has_named_volumes: bool).
    sources contains full normalized paths (not roots).
    has_named_volumes is True if any data volume uses a named volume.
    """
    sources = set()
    has_named = False

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
            # Handle NFS syntax (nfs-server:/remote/path:/container/path)
            elif len(parts) >= 3 and "/" in parts[1]:
                # nfs-server:/remote/path:/container/path
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

        # Check for named volumes (no path separator) — flag but don't add
        is_host_path = (source.startswith("/") or source.startswith("./") or
                        source.startswith("../") or source.startswith("~") or
                        (len(source) >= 2 and source[1] == ":"))

        # Also detect NFS/remote mounts (contain :/ pattern)
        is_remote = ":/" in source

        if not is_host_path and not is_remote:
            has_named = True
            continue

        # Normalize path for comparison
        norm = source.replace("\\", "/").rstrip("/")
        if norm:
            sources.add(norm)

    return sources, has_named


def _extract_volume_targets(services_raw: dict) -> List[str]:
    """
    Extract unique container-side data volume targets from all services.

    Used by the frontend to match error paths (e.g. /downloads/tv-sonarr/...)
    against a specific stack's volume layout. Skips config mounts.
    """
    targets = set()

    for name, config in services_raw.items():
        if not isinstance(config, dict):
            continue
        for vol in config.get("volumes", []):
            target = ""
            if isinstance(vol, str):
                parts = vol.split(":")
                if len(parts) < 2:
                    continue
                # Windows paths (C:\path:container:opts)
                if len(parts) >= 3 and len(parts[0]) == 1 and parts[0].isalpha():
                    target = parts[2]
                # NFS syntax: hostname:/remote:/container (hostname has no /)
                elif len(parts) >= 3 and "/" not in parts[0] and "/" in parts[1]:
                    target = parts[2]
                else:
                    # Standard: /host:/container or /host:/container:ro
                    target = parts[1]
            elif isinstance(vol, dict):
                target = vol.get("target", "")

            if not target:
                continue

            target_clean = target.rstrip("/").split(":")[0]  # strip :ro etc
            if any(target_clean == c or target_clean.startswith(c + "/") for c in _CONFIG_TARGETS):
                continue
            if target_clean:
                targets.add(target_clean)

    return sorted(targets)


def _cross_stack_health_pass(stacks: List[Stack]) -> int:
    """
    Post-processing pass to upgrade/downgrade health dots for single-service
    media stacks based on whether complementary services exist in siblings.

    Without this, every single-service sonarr/qbittorrent/plex stack shows
    a yellow "warning" dot. With this, we check siblings and show green if
    mounts align, red if they conflict.

    Mutates stacks in-place. Returns number of stacks whose health changed.
    """
    if not stacks:
        return 0

    upgraded = 0

    # Build a quick lookup: which stacks have which media roles and their sources
    stack_roles: dict = {}  # stack.path -> {"roles": set, "sources": set}
    for stack in stacks:
        roles = set()
        sources = set()
        for svc_name in stack.services:
            name_lower = svc_name.lower()
            if any(app in name_lower for app in _HL_ARR):
                roles.add("arr")
            elif any(app in name_lower for app in _HL_DL):
                roles.add("download_client")
            elif any(app in name_lower for app in _HL_MEDIA):
                roles.add("media_server")
        if roles:
            # Re-parse the compose file to get host sources
            # (we already have this data from the parse, but it's not stored on Stack)
            try:
                with open(stack.compose_file, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                if isinstance(data, dict) and "services" in data:
                    for name, config in data.get("services", {}).items():
                        if isinstance(config, dict):
                            vols = config.get("volumes", [])
                            svc_sources, _ = _extract_host_sources(vols)
                            sources.update(svc_sources)
            except Exception:
                pass
            stack_roles[stack.path] = {"roles": roles, "sources": sources}

    # Find single-role media stacks that are currently "warning"
    for stack in stacks:
        if stack.health != "warning":
            continue
        info = stack_roles.get(stack.path)
        if not info or len(info["roles"]) != 1:
            continue

        my_role = next(iter(info["roles"]))
        my_sources = info["sources"]

        # Look for complementary roles in sibling stacks
        complementary_found = False
        all_compatible = True

        for other_path, other_info in stack_roles.items():
            if other_path == stack.path:
                continue
            # Does this sibling have a role we're missing?
            if other_info["roles"] & info["roles"]:
                continue  # Same role, not complementary
            if not (other_info["roles"] - info["roles"]):
                continue

            # Found a complementary sibling
            complementary_found = True

            # Check mount compatibility
            if my_sources and other_info["sources"]:
                # Both have data volumes — check if they share a root
                all_combined = sorted(my_sources | other_info["sources"])
                if len(all_combined) < 2:
                    continue
                try:
                    common = os.path.commonpath([p.replace("\\", "/") for p in all_combined])
                    common = common.replace("\\", "/")
                    # Too shallow = not shared
                    if common in ("", "/") or (len(common) <= 3 and common[1:2] == ":"):
                        all_compatible = False
                except ValueError:
                    all_compatible = False

        if complementary_found:
            upgraded += 1
            if all_compatible:
                stack.health = "ok"
                stack.health_hint = "Shared mount detected (cross-stack)"
            else:
                stack.health = "problem"
                stack.health_hint = "Different mount roots (cross-stack)"

    return upgraded


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
