"""
pipeline.py — Pipeline-first analysis for MapArr.

Scans the entire root directory once, builds a unified picture of all
media services and their mount paths. Both Fix mode and Browse mode
draw from this context. The per-stack analyzer gains full pipeline
awareness — when you drill into sonarr, it already knows about plex,
radarr, qbittorrent, and every mount path in your setup.

This replaces the old per-stack analysis pattern where a single stack was
analyzed in isolation and siblings were checked as an afterthought.

KEY DESIGN: Reuses lightweight YAML parsing from cross_stack.py.
No docker compose config calls, no Docker socket. Just raw YAML.
Scans 35+ compose files in well under 1 second.
"""

import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

from backend.analyzer import _classify_service
from backend.cross_stack import (
    _find_compose_file,
    _parse_sibling_services,
    _check_shared_root,
    SiblingService,
)

logger = logging.getLogger("maparr.pipeline")


# ─── Data Structures ───

@dataclass
class PipelineService:
    """A media service discovered in the pipeline scan."""
    stack_path: str          # Absolute path to stack directory
    stack_name: str          # Directory name (e.g. "sonarr")
    service_name: str        # Service name from compose (e.g. "sonarr")
    role: str                # "arr", "download_client", "media_server"
    host_sources: Set[str]   # Normalized host data paths (config/utility mounts filtered)
    compose_file: str        # Path to the compose file
    volume_mounts: List[dict] = field(default_factory=list)  # [{source, target}] for RPM calc
    # Permissions analysis fields — populated from compose environment/user
    image: str = ""
    environment: Dict[str, str] = field(default_factory=dict)
    compose_user: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "stack_path": self.stack_path,
            "stack_name": self.stack_name,
            "service_name": self.service_name,
            "role": self.role,
            "host_sources": sorted(self.host_sources),
            "compose_file": os.path.basename(self.compose_file),
            "volume_mounts": self.volume_mounts,
            "image": self.image,
            "environment": self.environment,
            "compose_user": self.compose_user,
        }


@dataclass
class PipelineResult:
    """Complete pipeline scan of a root directory."""
    scan_dir: str
    scanned_at: float                                    # Unix timestamp
    stacks_scanned: int
    media_services: List[PipelineService] = field(default_factory=list)

    # Aggregated analysis
    roles_present: Set[str] = field(default_factory=set)
    roles_missing: Set[str] = field(default_factory=set)
    shared_mount: bool = False
    mount_root: str = ""
    conflicts: List[dict] = field(default_factory=list)

    # Lookup indexes (built after scan)
    services_by_stack: Dict[str, List[PipelineService]] = field(default_factory=dict)
    services_by_role: Dict[str, List[PipelineService]] = field(default_factory=dict)

    # Overall health
    health: str = "unknown"          # "ok", "warning", "problem"
    summary: str = ""                # Human-readable one-liner
    steps: List[dict] = field(default_factory=list)  # Terminal lines for UI

    def to_dict(self) -> dict:
        return {
            "scan_dir": self.scan_dir,
            "scanned_at": self.scanned_at,
            "stacks_scanned": self.stacks_scanned,
            "media_services": [s.to_dict() for s in self.media_services],
            "media_service_count": len(self.media_services),
            "roles_present": sorted(self.roles_present),
            "roles_missing": sorted(self.roles_missing),
            "shared_mount": self.shared_mount,
            "mount_root": self.mount_root,
            "conflicts": self.conflicts,
            "services_by_role": {
                role: [s.to_dict() for s in svcs]
                for role, svcs in self.services_by_role.items()
            },
            "health": self.health,
            "summary": self.summary,
            "steps": self.steps,
        }


# ─── Core Function ───

def run_pipeline_scan(scan_dir: str) -> PipelineResult:
    """
    Scan the entire root directory and build a unified media pipeline view.

    Iterates all subdirectories, finds compose files, extracts media services
    and their host mount paths, then checks mount consistency across the
    entire pipeline.

    Args:
        scan_dir: Root directory to scan (e.g. C:/DockerContainers)

    Returns:
        PipelineResult with all media services, mount analysis, and health status.
    """
    result = PipelineResult(
        scan_dir=scan_dir,
        scanned_at=time.time(),
        stacks_scanned=0,
    )
    steps: List[dict] = []

    if not scan_dir or not os.path.isdir(scan_dir):
        logger.warning("Pipeline scan: invalid directory %s", scan_dir)
        result.health = "problem"
        result.summary = "Invalid scan directory"
        steps.append({"icon": "fail", "text": f"Directory not found: {scan_dir}"})
        result.steps = steps
        return result

    logger.info("Pipeline scan: %s", scan_dir)
    steps.append({"icon": "run", "text": f"Scanning {scan_dir} for media services..."})

    # Iterate all subdirectories
    all_services: List[PipelineService] = []
    stacks_scanned = 0

    try:
        entries = sorted(Path(scan_dir).iterdir())
    except PermissionError:
        logger.warning("Pipeline scan: permission denied on %s", scan_dir)
        result.health = "problem"
        result.summary = "Permission denied"
        steps.append({"icon": "fail", "text": "Permission denied reading directory"})
        result.steps = steps
        return result

    dirs_checked = 0
    for entry in entries:
        if not entry.is_dir() or entry.name.startswith("."):
            continue

        compose_file = _find_compose_file(str(entry))
        if not compose_file:
            dirs_checked += 1
            continue

        stacks_scanned += 1
        dirs_checked += 1

        # Parse this stack's media services (lightweight YAML only)
        parsed = _parse_sibling_services(compose_file)
        for svc_name, svc_info in parsed.items():
            all_services.append(PipelineService(
                stack_path=str(entry),
                stack_name=entry.name,
                service_name=svc_name,
                role=svc_info["role"],
                host_sources=svc_info["host_sources"],
                compose_file=compose_file,
                volume_mounts=svc_info.get("volume_mounts", []),
                image=svc_info.get("image", ""),
                environment=svc_info.get("environment", {}),
                compose_user=svc_info.get("compose_user"),
            ))
            logger.debug("Pipeline: %s/%s → role=%s, mounts=%s",
                        entry.name, svc_name, svc_info["role"],
                        sorted(svc_info["host_sources"]) if svc_info["host_sources"] else "none")

    result.stacks_scanned = stacks_scanned
    result.media_services = all_services

    scan_elapsed = time.time() - result.scanned_at
    logger.info("Pipeline scan: %d dirs checked, %d stacks with compose, %d media services (%.2fs)",
                dirs_checked, stacks_scanned, len(all_services), scan_elapsed)

    if not all_services:
        result.health = "ok"
        result.summary = f"{stacks_scanned} stacks scanned — no media services detected"
        steps.append({"icon": "info", "text": result.summary})
        result.steps = steps
        return result

    # Build lookup indexes
    by_stack: Dict[str, List[PipelineService]] = {}
    by_role: Dict[str, List[PipelineService]] = {}
    for svc in all_services:
        by_stack.setdefault(svc.stack_name, []).append(svc)
        by_role.setdefault(svc.role, []).append(svc)
    result.services_by_stack = by_stack
    result.services_by_role = by_role

    # Determine roles present/missing
    result.roles_present = {svc.role for svc in all_services}
    all_media_roles = {"arr", "download_client", "media_server"}
    result.roles_missing = all_media_roles - result.roles_present

    # Log role breakdown
    arr_names = [s.service_name for s in by_role.get("arr", [])]
    dl_names = [s.service_name for s in by_role.get("download_client", [])]
    ms_names = [s.service_name for s in by_role.get("media_server", [])]
    logger.info("Pipeline roles: %d arr (%s), %d download (%s), %d media (%s)",
                len(arr_names), ", ".join(arr_names),
                len(dl_names), ", ".join(dl_names),
                len(ms_names), ", ".join(ms_names))

    role_parts = []
    if arr_names:
        role_parts.append(f"{len(arr_names)} *arr app{'s' if len(arr_names) != 1 else ''}")
    if dl_names:
        role_parts.append(f"{len(dl_names)} download client{'s' if len(dl_names) != 1 else ''}")
    if ms_names:
        role_parts.append(f"{len(ms_names)} media server{'s' if len(ms_names) != 1 else ''}")

    steps.append({
        "icon": "ok",
        "text": f"Found {len(all_services)} media services: {', '.join(role_parts)}",
    })

    # Mount consistency analysis across ALL media services
    # Build SiblingService wrappers for the _check_shared_root algorithm
    if len(all_services) >= 2:
        # Use the first service as "current" and rest as "siblings"
        first = all_services[0]
        rest_as_siblings = [
            SiblingService(
                stack_path=s.stack_path,
                stack_name=s.stack_name,
                service_name=s.service_name,
                role=s.role,
                host_sources=s.host_sources,
                compose_file=s.compose_file,
            )
            for s in all_services[1:]
        ]
        shared, mount_root = _check_shared_root(first.host_sources, rest_as_siblings)
        result.shared_mount = shared
        result.mount_root = mount_root

        if shared:
            logger.info("Pipeline: shared mount root → %s (hardlinks OK)", mount_root)
            steps.append({"icon": "ok", "text": f"Shared mount: {mount_root} — hardlinks will work"})
        else:
            # Find which services diverge
            _build_mount_conflicts(result, all_services, steps)
    elif len(all_services) == 1:
        # Single media service — no comparison needed
        svc = all_services[0]
        if svc.host_sources:
            result.mount_root = sorted(svc.host_sources)[0]
        result.shared_mount = True  # Vacuously true
        steps.append({"icon": "info", "text": "Single media service — no cross-service mount check needed"})

    # Determine overall health
    if result.conflicts:
        result.health = "problem"
    elif result.roles_missing:
        result.health = "warning"
    else:
        result.health = "ok"

    # Build summary
    mount_status = ""
    if result.shared_mount and result.mount_root:
        mount_status = f" | shared mount: {result.mount_root}"
    elif result.conflicts:
        mount_status = f" | {len(result.conflicts)} mount conflict{'s' if len(result.conflicts) != 1 else ''}"

    result.summary = f"{len(all_services)} media services across {len(by_stack)} stacks{mount_status}"
    logger.info("Pipeline scan complete: %s (health=%s)", result.summary, result.health)

    steps.append({"icon": "done", "text": f"Pipeline scan complete — {result.health}"})
    result.steps = steps
    return result


# ─── Mount Conflict Detection ───

def _build_mount_conflicts(
    result: PipelineResult,
    all_services: List[PipelineService],
    steps: List[dict],
) -> None:
    """
    Identify which services have conflicting mount roots.

    Groups services by their mount patterns and reports conflicts
    between groups. Only flags services that have data mounts —
    services with no host-mounted data volumes are skipped.
    """
    # Group services by their primary mount root
    groups: Dict[str, List[PipelineService]] = {}
    no_mounts: List[PipelineService] = []

    for svc in all_services:
        if not svc.host_sources:
            no_mounts.append(svc)
            continue
        # Use the shortest host source as the "primary" for grouping
        primary = sorted(svc.host_sources, key=len)[0]
        # Find which group this belongs to (check parent-child relationship)
        placed = False
        for group_root in list(groups.keys()):
            group_norm = group_root.rstrip("/") + "/"
            primary_norm = primary.rstrip("/") + "/"
            if (primary == group_root or
                primary.startswith(group_norm) or
                group_root.startswith(primary_norm)):
                groups[group_root].append(svc)
                placed = True
                break
        if not placed:
            groups[primary] = [svc]

    if len(groups) <= 1:
        # All services share the same tree — shouldn't get here, but just in case
        result.shared_mount = True
        if groups:
            result.mount_root = list(groups.keys())[0]
        return

    # Multiple mount groups = conflict
    logger.warning("Pipeline: %d separate mount groups detected", len(groups))

    # Find the majority group
    sorted_groups = sorted(groups.items(), key=lambda g: len(g[1]), reverse=True)
    majority_root, majority_svcs = sorted_groups[0]

    for root, svcs in sorted_groups[1:]:
        for svc in svcs:
            conflict = {
                "type": "pipeline_mount_mismatch",
                "severity": "critical",
                "service_name": svc.service_name,
                "stack_name": svc.stack_name,
                "service_sources": sorted(svc.host_sources),
                "majority_root": majority_root,
                "majority_services": [s.service_name for s in majority_svcs],
                "description": (
                    f"{svc.service_name} mounts {sorted(svc.host_sources)} "
                    f"but {len(majority_svcs)} other services use {majority_root}"
                ),
            }
            result.conflicts.append(conflict)
            logger.warning("Pipeline conflict: %s (%s) uses %s, majority uses %s",
                          svc.service_name, svc.stack_name,
                          sorted(svc.host_sources), majority_root)

    steps.append({
        "icon": "warn",
        "text": f"Mount conflict: {len(result.conflicts)} service{'s' if len(result.conflicts) != 1 else ''} "
                f"differ from the majority ({majority_root})",
    })


# ─── Helpers for Analyzer Integration ───

def get_pipeline_role(pipeline: dict, stack_path: str) -> Optional[str]:
    """
    Get the media role of a stack within the pipeline.

    Returns "arr", "download_client", "media_server", or None if the
    stack has no media services.
    """
    stack_name = os.path.basename(stack_path)
    by_stack = pipeline.get("services_by_role", {})
    # Search through all roles for this stack
    for svc in pipeline.get("media_services", []):
        if svc.get("stack_name") == stack_name or svc.get("stack_path") == stack_path:
            return svc.get("role")
    return None


def get_pipeline_context_for_stack(pipeline: dict, stack_path: str) -> dict:
    """
    Extract pipeline context relevant to a specific stack's analysis.

    Returns a dict with:
      - role: this stack's role in the pipeline
      - total_media: total media service count
      - shared_mount: whether the pipeline has consistent mounts
      - mount_root: the common mount root (if shared)
      - sibling_services: other media services in the pipeline
      - conflicts: any pipeline-level conflicts affecting this stack
      - summary: human-readable summary
    """
    stack_name = os.path.basename(stack_path)
    norm_path = stack_path.replace("\\", "/")

    all_media = pipeline.get("media_services", [])
    this_stack_role = None
    sibling_services = []

    for svc in all_media:
        svc_path = (svc.get("stack_path", "") or "").replace("\\", "/")
        svc_name = svc.get("stack_name", "")
        if svc_name == stack_name or svc_path == norm_path:
            this_stack_role = svc.get("role")
        else:
            sibling_services.append(svc)

    # Find conflicts that affect this stack specifically
    stack_conflicts = [
        c for c in pipeline.get("conflicts", [])
        if c.get("stack_name") == stack_name
    ]

    return {
        "role": this_stack_role,
        "total_media": len(all_media),
        "shared_mount": pipeline.get("shared_mount", False),
        "mount_root": pipeline.get("mount_root", ""),
        "health": pipeline.get("health", "unknown"),
        "sibling_services": sibling_services,
        "conflicts": stack_conflicts,
        "services_by_role": pipeline.get("services_by_role", {}),
        "summary": pipeline.get("summary", ""),
    }
