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

from backend.analyzer import analyze_stack
from backend.image_registry import get_registry
from backend.resolver import resolve_compose
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
    family_name: str = ""
    environment: Dict[str, str] = field(default_factory=dict)
    compose_user: Optional[str] = None
    has_named_volumes: bool = False  # True if data mounts use named volumes instead of bind mounts
    # Per-service health from full analysis (replaces lightweight guessing)
    health: str = "unknown"          # "ok", "warning", "problem"
    conflict_counts: Dict[str, int] = field(default_factory=dict)  # {category: count}
    health_details: List[str] = field(default_factory=list)  # Short conflict descriptions

    def to_dict(self) -> dict:
        return {
            "stack_path": self.stack_path,
            "stack_name": self.stack_name,
            "service_name": self.service_name,
            "role": self.role,
            "host_sources": sorted(self.host_sources),
            "compose_file": os.path.basename(self.compose_file),
            "compose_file_full": self.compose_file,  # Full path for multi-file fix plans
            "volume_mounts": self.volume_mounts,
            "image": self.image,
            "family_name": self.family_name,
            "environment": self.environment,
            "compose_user": self.compose_user,
            "health": self.health,
            "conflict_counts": self.conflict_counts,
            "health_details": self.health_details,
        }


@dataclass
class PipelineResult:
    """Complete pipeline scan of a root directory."""
    scan_dir: str
    scanned_at: float                                    # Unix timestamp
    stacks_scanned: int
    media_services: List[PipelineService] = field(default_factory=list)
    non_media_stacks: List[dict] = field(default_factory=list)  # [{name, path, services}]

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
    health_tier: str = "unknown"     # "excellent", "good", "fair", "poor"
    health_message: str = ""         # Human-friendly summary (e.g. "Looking great...")
    summary: str = ""                # Human-readable one-liner
    steps: List[dict] = field(default_factory=list)  # Terminal lines for UI
    per_stack_conflicts: Dict[str, List[dict]] = field(default_factory=dict)  # stack_name → conflicts

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
            "non_media_stacks": self.non_media_stacks,
            "health": self.health,
            "health_tier": self.health_tier,
            "health_message": self.health_message,
            "summary": self.summary,
            "steps": self.steps,
            "per_stack_conflicts": self.per_stack_conflicts,
        }


# ─── Helpers ───

def _list_service_names(compose_file: str) -> List[str]:
    """Return service names from a compose file without full parsing."""
    try:
        import yaml
        with open(compose_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if isinstance(data, dict) and isinstance(data.get("services"), dict):
            return sorted(data["services"].keys())
    except Exception:
        pass
    return []


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
    registry = get_registry()
    all_services: List[PipelineService] = []
    non_media_stacks: List[dict] = []
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

    # Check if scan_dir itself is a single stack (has a compose file directly).
    # This allows users to point at a specific stack folder, not just a parent.
    own_compose = _find_compose_file(scan_dir)
    if own_compose:
        scan_entry = Path(scan_dir)
        parsed = _parse_sibling_services(own_compose)
        for svc_name, svc_info in parsed.items():
            svc_image = svc_info.get("image", "")
            classification = registry.classify(svc_name, svc_image)
            all_services.append(PipelineService(
                stack_path=scan_dir,
                stack_name=scan_entry.name,
                service_name=svc_name,
                role=svc_info["role"],
                host_sources=svc_info["host_sources"],
                compose_file=own_compose,
                volume_mounts=svc_info.get("volume_mounts", []),
                image=svc_image,
                family_name=classification.get("family_name") or "",
                environment=svc_info.get("environment", {}),
                compose_user=svc_info.get("compose_user"),
            ))
        if not parsed:
            svc_names = _list_service_names(own_compose)
            if svc_names:
                non_media_stacks.append({
                    "name": scan_entry.name,
                    "path": scan_dir,
                    "services": svc_names,
                })
        stacks_scanned += 1
        logger.info("Pipeline scan: single-stack mode — %s has compose file with %d services",
                    scan_entry.name, len(parsed))

    dirs_checked = 0
    for entry in entries:
        if not entry.is_dir() or entry.name.startswith("."):
            continue

        compose_file = _find_compose_file(str(entry))

        if compose_file:
            # Normal stack: compose file at this level
            compose_files_to_scan = [(entry, compose_file)]
        else:
            # No compose at this level — check for cluster layout
            # (one service per subfolder, e.g. Dockhand/Portainer/DockSTARTer)
            compose_files_to_scan = []
            try:
                for sub_entry in sorted(entry.iterdir()):
                    if not sub_entry.is_dir() or sub_entry.name.startswith("."):
                        continue
                    sub_compose = _find_compose_file(str(sub_entry))
                    if sub_compose:
                        compose_files_to_scan.append((sub_entry, sub_compose))
            except PermissionError:
                logger.debug("Pipeline: permission denied scanning cluster %s", entry.name)

            if not compose_files_to_scan:
                dirs_checked += 1
                continue
            logger.info("Pipeline: cluster layout detected in %s (%d compose files)",
                       entry.name, len(compose_files_to_scan))

        stacks_scanned += len(compose_files_to_scan)
        dirs_checked += 1

        for svc_entry, svc_compose in compose_files_to_scan:
            # Parse this stack's media services (lightweight YAML only)
            parsed = _parse_sibling_services(svc_compose)
            for svc_name, svc_info in parsed.items():
                svc_image = svc_info.get("image", "")
                classification = registry.classify(svc_name, svc_image)
                all_services.append(PipelineService(
                    stack_path=str(svc_entry),
                    stack_name=svc_entry.name,
                    service_name=svc_name,
                    role=svc_info["role"],
                    host_sources=svc_info["host_sources"],
                    compose_file=svc_compose,
                    volume_mounts=svc_info.get("volume_mounts", []),
                    image=svc_image,
                    family_name=classification.get("family_name") or "",
                    environment=svc_info.get("environment", {}),
                    compose_user=svc_info.get("compose_user"),
                ))
                logger.debug("Pipeline: %s/%s → role=%s, mounts=%s",
                            svc_entry.name, svc_name, svc_info["role"],
                            sorted(svc_info["host_sources"]) if svc_info["host_sources"] else "none")

            # Track stacks with no media services for dashboard visibility
            if not parsed:
                svc_names = _list_service_names(svc_compose)
                if svc_names:
                    non_media_stacks.append({
                        "name": svc_entry.name,
                        "path": str(svc_entry),
                        "services": svc_names,
                    })

    result.stacks_scanned = stacks_scanned
    result.media_services = all_services
    result.non_media_stacks = sorted(non_media_stacks, key=lambda s: s["name"])

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

    # Permission consistency check across all media services
    _check_pipeline_permissions(result, all_services, steps)

    # ─── Full Per-Stack Analysis ───
    # Run the real 4-pass analyzer on each compose file to get true conflict data.
    # This replaces the old lightweight check that only caught 2 of 20 conflict types.
    steps.append({"icon": "run", "text": "Running deep analysis on each stack..."})
    _run_per_stack_analysis(result, all_services, steps)

    # Determine overall health from real analysis data
    _compute_pipeline_health(result, steps)
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
                "category": "A",
                "severity": "critical",
                "service_name": svc.service_name,
                "stack_name": svc.stack_name,
                "services": [svc.service_name],
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


# ─── Permission Consistency Detection ───

def _check_pipeline_permissions(
    result: PipelineResult,
    all_services: List[PipelineService],
    steps: List[dict],
) -> None:
    """
    Lightweight permission check: flag when PUID/PGID groups diverge across pipeline.

    Groups services by their effective PUID:PGID pair. Services without explicit
    PUID/PGID are resolved via their image family defaults (e.g. LinuxServer.io
    images default to 911:911). If more than one group exists, the minority groups
    are flagged as conflicts with 'high' severity.
    """
    registry = get_registry()

    # Build a lookup from service_name → stack_name for conflict reporting
    svc_to_stack: Dict[str, str] = {}
    for svc in all_services:
        svc_to_stack[svc.service_name] = svc.stack_name

    puid_groups: Dict[str, List[str]] = {}
    for svc in all_services:
        family = registry.get_family(svc.image) if svc.image else None

        # Check standard PUID/PGID first, then family-specific env var names
        puid = svc.environment.get("PUID", "")
        pgid = svc.environment.get("PGID", "")
        if family and (not puid or not pgid):
            uid_env = family.get("uid_env", "PUID")
            gid_env = family.get("gid_env", "PGID")
            if not puid and uid_env != "PUID":
                puid = svc.environment.get(uid_env, "")
            if not pgid and gid_env != "PGID":
                pgid = svc.environment.get(gid_env, "")

        # Fall back to family defaults for services without any explicit UID/GID
        if family and (not puid or not pgid):
            if not puid:
                puid = str(family.get("default_uid", ""))
            if not pgid:
                pgid = str(family.get("default_gid", ""))

        if puid and pgid:
            key = f"{puid}:{pgid}"
            puid_groups.setdefault(key, []).append(svc.service_name)

    if len(puid_groups) <= 1:
        return

    majority_key = max(puid_groups, key=lambda k: len(puid_groups[k]))
    for key, svc_names in puid_groups.items():
        if key != majority_key:
            for name in svc_names:
                result.conflicts.append({
                    "type": "pipeline_permission_mismatch",
                    "category": "B",
                    "severity": "high",
                    "service_name": name,
                    "stack_name": svc_to_stack.get(name, ""),
                    "services": [name],
                    "description": f"{name} runs as {key} but majority use {majority_key}",
                })
    steps.append({
        "icon": "warn",
        "text": f"Permission mismatch: {len(puid_groups)} different PUID:PGID groups",
    })


# ─── Helpers for Analyzer Integration ───

# ─── Full Per-Stack Analysis ───

def _run_per_stack_analysis(
    result: PipelineResult,
    all_services: List[PipelineService],
    steps: List[dict],
) -> None:
    """
    Run the real 4-pass analyzer on each unique compose file in the pipeline.

    This replaces the old lightweight health check that only detected mount
    mismatches and cross-stack PUID. Now every service gets the full treatment:
    path conflicts, hardlink breakage, permissions, platform recommendations,
    and observations. Results are stored per-service on PipelineService.health
    and aggregated into PipelineResult.per_stack_conflicts.

    Uses force_manual=True on the resolver to skip Docker CLI overhead,
    keeping the pipeline scan fast (~5ms/stack).
    """
    # Group services by compose file (one compose file may have multiple services)
    compose_to_services: Dict[str, List[PipelineService]] = {}
    for svc in all_services:
        compose_to_services.setdefault(svc.compose_file, []).append(svc)

    analyzed_count = 0
    error_count = 0

    for compose_file, services in compose_to_services.items():
        stack_path = services[0].stack_path
        stack_name = services[0].stack_name

        try:
            # Resolve compose (manual only — fast, no Docker CLI)
            resolved = resolve_compose(
                stack_path,
                compose_file=os.path.basename(compose_file),
                force_manual=True,
            )

            # Read raw content for fix plan generation
            raw_content = None
            try:
                with open(compose_file, "r", encoding="utf-8") as f:
                    raw_content = f.read()
            except Exception:
                pass

            # Run the real 4-pass analysis (no pipeline_context to avoid recursion)
            analysis = analyze_stack(
                resolved_compose=resolved,
                stack_path=stack_path,
                compose_file=compose_file,
                resolution_method="manual",
                raw_compose_content=raw_content,
            )

            # Map conflicts back to services
            stack_conflicts = []
            for conflict in analysis.conflicts:
                conflict_dict = conflict.to_dict() if hasattr(conflict, "to_dict") else conflict
                stack_conflicts.append(conflict_dict)

            # Add observations as Cat D (informational only — no health impact)
            for obs in analysis.observations:
                obs_with_cat = dict(obs)
                obs_with_cat["category"] = "D"
                obs_with_cat.setdefault("services", [obs.get("service", "")])
                obs_with_cat.setdefault("description", obs.get("message", ""))
                stack_conflicts.append(obs_with_cat)

            result.per_stack_conflicts[stack_name] = stack_conflicts

            # Assign per-service health based on real conflicts
            _assign_service_health(services, stack_conflicts, analysis)
            analyzed_count += 1

        except Exception as e:
            logger.warning("Pipeline analysis failed for %s: %s", stack_name, e)
            error_count += 1
            # Mark services as unknown health on analysis failure
            for svc in services:
                svc.health = "unknown"

    logger.info("Pipeline deep analysis: %d stacks analyzed, %d errors", analyzed_count, error_count)
    if error_count:
        steps.append({"icon": "warn", "text": f"Deep analysis failed for {error_count} stack(s)"})
    else:
        steps.append({"icon": "ok", "text": f"Deep analysis complete — {analyzed_count} stacks checked"})


def _assign_service_health(
    services: List[PipelineService],
    stack_conflicts: List[dict],
    analysis,
) -> None:
    """
    Set per-service health and conflict details from the full analysis results.

    Health is determined by the worst conflict category affecting the service:
      - Cat A (path conflicts) → "problem"
      - Cat B (permissions) → "warning"
      - Cat C (infrastructure) → "warning"
      - Cat D (observations) → "ok" (informational only)
      - No conflicts → "ok"
    """
    # Build a set of service names from the analysis
    analyzed_service_names = {s.name for s in analysis.services} if analysis.services else set()

    for svc in services:
        # Find conflicts that affect this service
        svc_conflicts = []
        worst_category = None

        for conflict in stack_conflicts:
            affected = conflict.get("services", [])
            # Match by service name in the conflict's services list
            if svc.service_name in affected or not affected:
                svc_conflicts.append(conflict)
                cat = conflict.get("category", "D")
                if cat == "A":
                    worst_category = "A"
                elif cat == "B" and worst_category not in ("A",):
                    worst_category = "B"
                elif cat == "C" and worst_category not in ("A", "B"):
                    worst_category = "C"

        # Count conflicts by category
        counts: Dict[str, int] = {}
        details: List[str] = []
        for c in svc_conflicts:
            cat = c.get("category", "D")
            counts[cat] = counts.get(cat, 0) + 1
            desc = c.get("description", "")
            if desc and cat in ("A", "B", "C"):
                details.append(desc)

        svc.conflict_counts = counts
        svc.health_details = details[:5]  # Cap at 5 most relevant

        if worst_category == "A":
            svc.health = "problem"
        elif worst_category in ("B", "C"):
            svc.health = "warning"
        else:
            svc.health = "ok"


# ─── Health Tier Classification ───

def _compute_pipeline_health(
    result: PipelineResult,
    steps: List[dict],
) -> None:
    """
    Compute the overall pipeline health tier from per-service analysis results.

    Tiers:
      "excellent" → All services healthy. No conflicts detected.
      "good"      → No path issues. Minor permission/infra tweaks possible.
      "fair"      → Paths are correct, but permissions need attention.
      "poor"      → Path issues detected. Hardlinks won't work.

    Also sets result.health (ok/warning/problem) for backward compat.
    """
    # Collect all per-service categories across the pipeline
    cat_a_count = 0
    cat_b_count = 0
    cat_c_count = 0
    total_services = len(result.media_services)
    services_with_issues = 0

    for svc in result.media_services:
        counts = svc.conflict_counts
        a = counts.get("A", 0)
        b = counts.get("B", 0)
        c = counts.get("C", 0)
        cat_a_count += a
        cat_b_count += b
        cat_c_count += c
        if a or b or c:
            services_with_issues += 1

    # Also count pipeline-level conflicts (cross-stack mount/perm)
    for conflict in result.conflicts:
        cat = conflict.get("category", "")
        if cat == "A":
            cat_a_count += 1
        elif cat == "B":
            cat_b_count += 1

    # Determine tier
    if cat_a_count > 0:
        result.health_tier = "poor"
        result.health = "problem"
        if cat_a_count == 1:
            result.health_message = (
                "Path issue detected — one service has a broken mount configuration. "
                "Hardlinks won't work until this is fixed."
            )
        else:
            result.health_message = (
                f"{cat_a_count} path issues detected across your pipeline. "
                "Hardlinks can't work with separate mount trees. Click a service to see the fix."
            )
    elif cat_b_count > 0:
        result.health_tier = "fair"
        result.health = "warning"
        result.health_message = (
            "Paths look correct — hardlinks should work. "
            f"However, {cat_b_count} permission/environment "
            f"{'issue needs' if cat_b_count == 1 else 'issues need'} attention."
        )
    elif cat_c_count > 0:
        result.health_tier = "good"
        result.health = "ok"
        result.health_message = (
            "Looking good — paths and permissions are consistent. "
            f"{cat_c_count} minor infrastructure "
            f"{'suggestion is' if cat_c_count == 1 else 'suggestions are'} available."
        )
    elif result.roles_missing:
        result.health_tier = "good"
        result.health = "warning"
        missing_names = sorted(result.roles_missing)
        missing_labels = [
            {"arr": "*arr app", "download_client": "download client",
             "media_server": "media server"}.get(r, r)
            for r in missing_names
        ]
        result.health_message = (
            f"Services are configured correctly, but no {' or '.join(missing_labels)} was found. "
            "Add the missing service to enable full pipeline analysis."
        )
    else:
        result.health_tier = "excellent"
        result.health = "ok"
        if total_services == 0:
            result.health_message = "No media services found to analyze."
        elif total_services == 1:
            result.health_message = "Single service looks properly configured."
        else:
            result.health_message = (
                f"Looking great — all {total_services} media services are properly configured. "
                "Paths, permissions, and infrastructure all check out."
            )

    # Build summary
    mount_status = ""
    if result.shared_mount and result.mount_root:
        mount_status = f" | shared mount: {result.mount_root}"
    elif result.conflicts:
        mount_count = len([c for c in result.conflicts if c.get("type") == "pipeline_mount_mismatch"])
        perm_count = len([c for c in result.conflicts if c.get("type") == "pipeline_permission_mismatch"])
        parts = []
        if mount_count:
            parts.append(f"{mount_count} mount conflict{'s' if mount_count != 1 else ''}")
        if perm_count:
            parts.append(f"{perm_count} permission mismatch{'es' if perm_count != 1 else ''}")
        mount_status = f" | {', '.join(parts)}" if parts else ""

    by_stack = result.services_by_stack
    result.summary = f"{len(result.media_services)} media services across {len(by_stack)} stacks{mount_status}"
    logger.info("Pipeline scan complete: %s (health=%s, tier=%s)",
                result.summary, result.health, result.health_tier)

    steps.append({"icon": "done", "text": f"Pipeline scan complete — {result.health_tier}"})
    result.steps = steps


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
