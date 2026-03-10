"""
cross_stack.py — Cross-stack analysis for MapArr.

Solves the #1 real-world usage gap: single-service stacks.

When a user manages services via Komodo, Portainer, or Dockge, each service
typically lives in its own directory with its own compose file:

    /stacks/
        sonarr/docker-compose.yml       ← has /mnt/nas:/data
        qbittorrent/docker-compose.yml  ← has /mnt/nas:/data
        plex/docker-compose.yml         ← has /mnt/nas:/data

Analyzing "sonarr" alone says "incomplete — no download client." But qbittorrent
is right next door. This module scans sibling directories for complementary
services and compares their host-side volume mounts.

KEY DESIGN DECISION: Lightweight sibling scan.
  - Parses raw YAML only (no `docker compose config`)
  - Only extracts service names + volume mounts
  - Fast and safe — no Docker socket needed for siblings
  - Reuses existing functions from discovery.py and analyzer.py
"""

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

import yaml

from backend.analyzer import _classify_service
from backend.discovery import (
    COMPOSE_FILENAMES, _extract_host_sources, MAX_COMPOSE_FILE_SIZE,
    _CONFIG_TARGETS,
)

logger = logging.getLogger("maparr.cross_stack")


# ─── Data Structures ───

@dataclass
class SiblingService:
    """A media-related service found in a sibling stack."""
    stack_path: str          # Absolute path to the sibling stack directory
    stack_name: str          # Directory name (e.g. "qbittorrent")
    service_name: str        # Service name from compose (e.g. "qbittorrent")
    role: str                # "arr", "download_client", "media_server"
    host_sources: Set[str]   # Normalized host paths for data volumes
    compose_file: str        # Path to the compose file

    def to_dict(self) -> dict:
        return {
            "stack_path": self.stack_path,
            "stack_name": self.stack_name,
            "service_name": self.service_name,
            "role": self.role,
            "host_sources": sorted(self.host_sources),
            "compose_file": os.path.basename(self.compose_file),
        }


@dataclass
class CrossStackResult:
    """Result of scanning sibling stacks for complementary services."""
    siblings_found: List[SiblingService] = field(default_factory=list)
    missing_roles_filled: List[str] = field(default_factory=list)
    shared_mount: bool = False
    mount_root: str = ""                    # The common root if shared
    conflicts: List[dict] = field(default_factory=list)
    summary: str = ""
    sibling_count_scanned: int = 0          # How many siblings we checked

    def to_dict(self) -> dict:
        return {
            "siblings": [s.to_dict() for s in self.siblings_found],
            "missing_roles_filled": self.missing_roles_filled,
            "shared_mount": self.shared_mount,
            "mount_root": self.mount_root,
            "conflicts": self.conflicts,
            "summary": self.summary,
            "sibling_count_scanned": self.sibling_count_scanned,
        }


# ─── Core Function ───

def check_cross_stack(
    stack_path: str,
    scan_dir: str,
    current_services: list,
    current_host_sources: Optional[Set[str]] = None,
) -> Optional[CrossStackResult]:
    """
    Scan sibling directories for complementary media services.

    Called when analyze_stack() detects an incomplete stack (has arr but
    no download client, or vice versa). Looks at sibling directories
    under scan_dir for services that fill the missing roles.

    Args:
        stack_path: Path to the stack being analyzed
        scan_dir: Parent directory to scan for siblings
        current_services: ServiceInfo list from the current analysis
        current_host_sources: Pre-extracted host paths from current stack
            (if None, will be extracted from current_services)

    Returns:
        CrossStackResult if siblings were found, None if scan_dir is invalid
        or no siblings exist.
    """
    if not scan_dir or not os.path.isdir(scan_dir):
        return None

    stack_name = os.path.basename(stack_path)
    logger.info("Cross-stack scan: %s (scan_dir=%s)", stack_name, scan_dir)

    # Determine what roles we have and what's missing
    has_roles = set()
    for svc in current_services:
        if hasattr(svc, "role"):
            has_roles.add(svc.role)

    missing_roles = set()
    has_media_role = has_roles & {"arr", "download_client", "media_server"}
    if has_media_role:
        for role in ("arr", "download_client", "media_server"):
            if role not in has_roles:
                missing_roles.add(role)

    if not missing_roles:
        logger.debug("Cross-stack: stack is complete, no scan needed")
        return None  # Stack is complete — no cross-stack needed

    logger.info("Cross-stack: looking for %s in sibling directories", _role_names(missing_roles))

    # Extract host sources from the current stack if not provided
    if current_host_sources is None:
        current_host_sources = _extract_sources_from_services(current_services)

    # Scan siblings
    stack_path_resolved = str(Path(stack_path).resolve())
    siblings: List[SiblingService] = []
    sibling_count = 0

    try:
        entries = sorted(Path(scan_dir).iterdir())
    except PermissionError:
        logger.debug("Permission denied scanning %s", scan_dir)
        return None

    for entry in entries:
        if not entry.is_dir():
            continue
        # Skip hidden directories
        if entry.name.startswith("."):
            continue
        # Skip the current stack
        if str(entry.resolve()) == stack_path_resolved:
            continue

        # Look for a compose file in this sibling
        compose_file = _find_compose_file(str(entry))
        if not compose_file:
            continue

        sibling_count += 1

        # Parse minimally — extract services + volumes
        sibling_services = _parse_sibling_services(compose_file)
        if not sibling_services:
            continue

        # Check if any service fills a missing role
        for svc_name, svc_info in sibling_services.items():
            role = svc_info["role"]
            if role in missing_roles:
                logger.info("Cross-stack: found %s (%s) in sibling %s/",
                            svc_name, role, entry.name)
                siblings.append(SiblingService(
                    stack_path=str(entry),
                    stack_name=entry.name,
                    service_name=svc_name,
                    role=role,
                    host_sources=svc_info["host_sources"],
                    compose_file=compose_file,
                ))

    logger.info("Cross-stack: scanned %d siblings, found %d complementary services",
                 sibling_count, len(siblings))

    if not siblings:
        # Scanned siblings but none filled missing roles
        result = CrossStackResult(sibling_count_scanned=sibling_count)
        if sibling_count > 0:
            result.summary = (
                f"Scanned {sibling_count} sibling stacks but no "
                f"{_role_names(missing_roles)} found nearby."
            )
        return result

    # Determine which missing roles were filled
    filled_roles = {s.role for s in siblings}
    missing_roles_filled = sorted(filled_roles & missing_roles)

    # Compare mount paths: current stack sources vs all sibling sources
    all_sources = set(current_host_sources)
    sibling_source_map: Dict[str, Set[str]] = {}  # service_name -> sources
    for sib in siblings:
        all_sources.update(sib.host_sources)
        sibling_source_map[sib.service_name] = sib.host_sources

    # Log the mount paths being compared
    logger.info("Cross-stack mount comparison: current=%s", sorted(current_host_sources))
    for sib in siblings:
        if sib.host_sources:
            logger.info("Cross-stack mount comparison: %s=%s", sib.service_name, sorted(sib.host_sources))

    shared_mount, mount_root = _check_shared_root(current_host_sources, siblings)
    if shared_mount:
        logger.info("Cross-stack: shared mount root → %s (hardlinks OK)", mount_root)
    elif current_host_sources:
        logger.warning("Cross-stack: mount roots differ — hardlinks will NOT work")

    # Build conflicts if mounts don't align
    conflicts = []
    if not shared_mount and current_host_sources:
        # Find which siblings conflict
        for sib in siblings:
            if sib.host_sources and not _paths_share_root(current_host_sources, sib.host_sources):
                logger.warning("Cross-stack conflict: %s mounts %s (vs current %s)",
                               sib.service_name, sorted(sib.host_sources), sorted(current_host_sources))
                conflicts.append({
                    "type": "cross_stack_mount_mismatch",
                    "severity": "critical",
                    "current_sources": sorted(current_host_sources),
                    "sibling_name": sib.service_name,
                    "sibling_stack": sib.stack_name,
                    "sibling_sources": sorted(sib.host_sources),
                    "description": (
                        f"{sib.service_name} (in {sib.stack_name}/) mounts different "
                        f"host paths. Hardlinks cannot work across different mount trees."
                    ),
                })

    # Build summary
    sib_names = [f"{s.service_name} ({s.stack_name}/)" for s in siblings]
    if shared_mount:
        summary = (
            f"Found {', '.join(sib_names)} in sibling stacks. "
            f"All services share {mount_root} — hardlinks will work."
        )
    elif conflicts:
        summary = (
            f"Found {', '.join(sib_names)} in sibling stacks, but "
            f"host mount paths differ. Hardlinks will NOT work until "
            f"all services share the same host directory."
        )
    else:
        summary = f"Found {', '.join(sib_names)} in sibling stacks."

    return CrossStackResult(
        siblings_found=siblings,
        missing_roles_filled=missing_roles_filled,
        shared_mount=shared_mount,
        mount_root=mount_root,
        conflicts=conflicts,
        summary=summary,
        sibling_count_scanned=sibling_count,
    )


# ─── Sibling Parsing ───

def _find_compose_file(directory: str) -> Optional[str]:
    """Find the first compose file in a directory."""
    for filename in COMPOSE_FILENAMES:
        path = os.path.join(directory, filename)
        if os.path.isfile(path):
            return path
    return None


def _extract_volume_mounts(volumes: list) -> List[dict]:
    """
    Extract data volume mount pairs (host source + container target) from raw
    volume declarations. Filters out config/utility mounts using the same rules
    as _extract_host_sources. Returns list of {"source": str, "target": str}.

    Used by the RPM calculator to compute container-path translations between
    download clients and *arr apps.
    """
    mounts = []
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
                target = parts[2] if len(parts) > 2 else ""
            # 3-part with access mode: /host:/container:ro or /host:/container:rw
            elif len(parts) == 3 and parts[2] in ("ro", "rw", "z", "Z", "shared",
                                                    "slave", "private", "rshared",
                                                    "rslave", "rprivate"):
                source = parts[0]
                target = parts[1]
            # Handle NFS syntax (nfs-server:/remote/path:/container/path)
            elif len(parts) >= 3 and "/" in parts[1]:
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

        # Skip config mounts (same filter as _extract_host_sources)
        target_clean = target.rstrip("/").split(":")[0]  # strip :ro etc
        if any(target_clean == c or target_clean.startswith(c + "/")
               for c in _CONFIG_TARGETS):
            continue

        # Skip named volumes — only keep host-path bind mounts
        is_host_path = (source.startswith("/") or source.startswith("./") or
                        source.startswith("../") or source.startswith("~") or
                        (len(source) >= 2 and source[1] == ":"))
        is_remote = ":/" in source

        if not is_host_path and not is_remote:
            continue

        norm_source = source.replace("\\", "/").rstrip("/")
        norm_target = target_clean.rstrip("/")
        if norm_source and norm_target:
            mounts.append({"source": norm_source, "target": norm_target})

    return mounts


def _parse_sibling_services(compose_file: str) -> Dict[str, dict]:
    """
    Parse a sibling compose file minimally.

    Returns dict of {service_name: {"role": str, "host_sources": set, "volume_mounts": list}}
    Only includes media-relevant services (arr, download_client, media_server).
    volume_mounts is a list of {"source": str, "target": str} dicts for data volumes,
    needed by the RPM calculator to compute container-path translations.
    """
    try:
        file_size = os.path.getsize(compose_file)
        if file_size > MAX_COMPOSE_FILE_SIZE:
            return {}

        with open(compose_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if not isinstance(data, dict) or "services" not in data:
            return {}

        services_raw = data.get("services", {})
        if not isinstance(services_raw, dict):
            return {}

        result = {}
        for name, config in services_raw.items():
            if not isinstance(config, dict):
                continue

            image = config.get("image", "")
            role = _classify_service(name, image)

            if role not in ("arr", "download_client", "media_server"):
                continue

            volumes = config.get("volumes", [])
            host_sources, has_named = _extract_host_sources(volumes)
            volume_mounts = _extract_volume_mounts(volumes)

            # Extract environment and user identity for permissions analysis
            env_raw = config.get("environment", {})
            environment = {}
            if isinstance(env_raw, dict):
                environment = {k: str(v) for k, v in env_raw.items()}
            elif isinstance(env_raw, list):
                for item in env_raw:
                    if "=" in str(item):
                        key, _, val = str(item).partition("=")
                        environment[key] = val

            compose_user = str(config["user"]) if "user" in config else None

            result[name] = {
                "role": role,
                "host_sources": host_sources,
                "has_named_volumes": has_named,
                "volume_mounts": volume_mounts,
                "environment": environment,
                "compose_user": compose_user,
                "image": image,
            }

        return result

    except Exception as e:
        logger.debug("Error parsing sibling %s: %s", compose_file, e)
        return {}


# ─── Mount Comparison ───

def _extract_sources_from_services(services: list) -> Set[str]:
    """Extract normalized host sources from ServiceInfo objects (data volumes only)."""
    sources = set()
    config_targets = {
        "/config", "/app", "/etc", "/var", "/tmp", "/run", "/dev",
        "/backup", "/backups", "/restore", "/log", "/logs",
        "/cache", "/certs", "/ssl", "/scripts",
    }

    for svc in services:
        if not hasattr(svc, "volumes"):
            continue
        for vol in svc.volumes:
            if not vol.is_bind_mount or not vol.source:
                continue
            # Skip config mounts
            target = vol.target.rstrip("/")
            if any(target == c or target.startswith(c + "/") for c in config_targets):
                continue
            norm = vol.source.replace("\\", "/").rstrip("/")
            if norm:
                sources.add(norm)

    return sources


def _check_shared_root(
    current_sources: Set[str],
    siblings: List[SiblingService],
) -> tuple:
    """
    Check if the current stack and all siblings share a common host path root.

    Returns (shared: bool, root: str).
    """
    if not current_sources:
        # No data volumes in current stack — can't compare
        return False, ""

    # Collect all host sources
    all_source_sets = [current_sources]
    for sib in siblings:
        if sib.host_sources:
            all_source_sets.append(sib.host_sources)

    if len(all_source_sets) < 2:
        # Only one set has sources — can't confirm shared mount
        return False, ""

    # Flatten all sources
    all_sources = set()
    for s in all_source_sets:
        all_sources.update(s)

    if not all_sources:
        return False, ""

    # Check 1: exact match — any source appears in ALL sets
    common = all_source_sets[0].copy()
    for s in all_source_sets[1:]:
        common &= s
    if common:
        logger.debug("Shared root: exact match on %s", sorted(common)[0])
        return True, sorted(common)[0]

    # Check 2: parent-child — all sources fall under one common root
    all_flat = sorted(all_sources, key=len)
    for candidate in all_flat:
        candidate_norm = candidate.rstrip("/") + "/"
        all_under = True
        for source_set in all_source_sets:
            if not all(
                s == candidate or s.startswith(candidate_norm) or candidate.startswith(s.rstrip("/") + "/")
                for s in source_set
            ):
                all_under = False
                break
        if all_under:
            logger.debug("Shared root: parent-child match on %s", candidate)
            return True, candidate

    # Check 3: common path prefix (e.g. /mnt/nas/media and /mnt/nas/downloads share /mnt/nas)
    all_paths = sorted(all_sources)
    try:
        common_prefix = os.path.commonpath([p.replace("\\", "/") for p in all_paths])
        common_prefix = common_prefix.replace("\\", "/")
    except ValueError:
        return False, ""

    # Must be at least 2 levels deep to be meaningful (not just "/" or "C:/")
    if common_prefix in ("", "/") or (len(common_prefix) <= 3 and common_prefix[1:2] == ":"):
        return False, ""

    # Verify the common prefix is substantive (at least /x/y)
    parts = [p for p in common_prefix.split("/") if p]
    if len(parts) >= 2:
        return True, common_prefix

    return False, ""


def _paths_share_root(sources_a: Set[str], sources_b: Set[str]) -> bool:
    """Check if two sets of paths share a common meaningful root."""
    shared, _ = _check_shared_root(sources_a, [
        SiblingService("", "", "", "", sources_b, "")
    ])
    return shared


# ─── Helpers ───

def _role_names(roles: set) -> str:
    """Convert role set to human-readable names."""
    names = []
    for role in sorted(roles):
        if role == "arr":
            names.append("*arr app")
        elif role == "download_client":
            names.append("download client")
        elif role == "media_server":
            names.append("media server")
    return " or ".join(names)
