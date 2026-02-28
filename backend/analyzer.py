"""
analyzer.py — The core analysis engine for MapArr.

This is where MapArr earns its value. Given a resolved compose file
and (optionally) the user's error context, it:

  1. Extracts volume mounts per service
  2. Classifies services (arr app, download client, media server)
  3. Identifies service relationships (sonarr needs qbittorrent's paths)
  4. Traces paths through volume mounts
  5. Detects conflicts (hardlink-breaking mount structures)
  6. Generates specific, actionable fixes

The #1 problem this solves:
  Sonarr mounts /host/tv:/data/tv
  qBittorrent mounts /host/downloads:/downloads
  → Hardlinks/atomic moves CANNOT work because they're separate mount trees.
  → Fix: Both need a unified parent mount like /host/data:/data

This is the TRaSH Guides pattern, and MapArr's job is to detect when
a user's setup violates it and tell them exactly what to change.
"""

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, Dict, List, Optional, Set, Tuple

import yaml

from backend.mounts import classify_path, check_hardlink_compatibility, MountClassification

logger = logging.getLogger("maparr.analyzer")


# ─── Known Services ───

# These lists drive service classification and relationship detection.
# Order doesn't matter — they're used for membership testing.

ARR_APPS = {
    "sonarr", "radarr", "lidarr", "readarr", "whisparr",
    "prowlarr", "bazarr",
}

DOWNLOAD_CLIENTS = {
    "qbittorrent", "sabnzbd", "nzbget", "transmission",
    "deluge", "rtorrent", "jdownloader",
    "aria2", "flood", "rdtclient",
}

MEDIA_SERVERS = {
    "plex", "jellyfin", "emby",
}

REQUEST_APPS = {
    "overseerr", "jellyseerr", "ombi",
}

# Services that need to share filesystem paths for hardlinks/atomic moves.
# An *arr app imports from a download client, then hardlinks/moves to media.
# All three MUST share a common parent volume for hardlinks to work.
HARDLINK_PARTICIPANTS = ARR_APPS | DOWNLOAD_CLIENTS | MEDIA_SERVERS


# ─── Image Family Intelligence ───
#
# Docker image families handle user identity differently. LinuxServer.io uses
# PUID/PGID env vars (defaulting to internal UID 911), Hotio uses the same
# names but defaults to 1000, jlesage uses USER_ID/GROUP_ID, and official
# images often use their own conventions or the compose `user:` directive.
#
# This registry lets the permissions analysis understand each family's
# mechanism without hardcoding image-specific logic in every check function.

@dataclass
class ImageFamily:
    """Intelligence about a Docker image family's user identity mechanism."""
    name: str                                   # Human-readable family name
    image_patterns: List[str]                   # Substrings to match in image string
    uid_env: Optional[str] = None               # Env var for UID (e.g., "PUID")
    gid_env: Optional[str] = None               # Env var for GID (e.g., "PGID")
    umask_env: Optional[str] = None             # Env var for UMASK
    default_uid: Optional[str] = None           # Default UID if env var not set
    default_gid: Optional[str] = None           # Default GID if env var not set
    needs_puid: bool = True                     # Whether this image expects explicit UID/GID


IMAGE_FAMILIES: List[ImageFamily] = [
    ImageFamily(
        name="LinuxServer.io",
        image_patterns=["lscr.io/linuxserver/", "linuxserver/", "ghcr.io/linuxserver/"],
        uid_env="PUID", gid_env="PGID", umask_env="UMASK",
        default_uid="911", default_gid="911",
        needs_puid=True,
    ),
    ImageFamily(
        name="Hotio",
        image_patterns=["hotio/", "cr.hotio.dev/hotio/", "ghcr.io/hotio/"],
        uid_env="PUID", gid_env="PGID", umask_env="UMASK",
        default_uid="1000", default_gid="1000",
        needs_puid=True,
    ),
    ImageFamily(
        name="jlesage",
        image_patterns=["jlesage/"],
        uid_env="USER_ID", gid_env="GROUP_ID", umask_env="UMASK",
        default_uid="1000", default_gid="1000",
        needs_puid=True,
    ),
    ImageFamily(
        name="Binhex",
        image_patterns=["binhex/"],
        uid_env="PUID", gid_env="PGID", umask_env="UMASK",
        default_uid="99", default_gid="100",
        needs_puid=True,
    ),
    ImageFamily(
        name="Official Plex",
        image_patterns=["plexinc/pms-docker"],
        uid_env="PLEX_UID", gid_env="PLEX_GID", umask_env=None,
        default_uid=None, default_gid=None,
        needs_puid=True,
    ),
    ImageFamily(
        name="Official Jellyfin",
        image_patterns=["jellyfin/jellyfin"],
        uid_env=None, gid_env=None, umask_env=None,
        default_uid=None, default_gid=None,
        needs_puid=False,  # Typically uses compose user: directive
    ),
    ImageFamily(
        name="Seerr",
        image_patterns=["sctx/overseerr", "fallenbagel/jellyseerr"],
        uid_env=None, gid_env=None, umask_env=None,
        default_uid=None, default_gid=None,
        needs_puid=False,  # Uses compose user: directive
    ),
]


@dataclass
class PermissionProfile:
    """Unified permission configuration for a single service.

    Resolves UID/GID from multiple possible sources (compose user: directive,
    image-family-specific env vars, family defaults) into a single profile
    that the permission checks can compare uniformly.
    """
    service_name: str
    image: str
    image_family: Optional[str] = None      # Matched family name or None
    uid: Optional[str] = None               # Resolved UID
    gid: Optional[str] = None               # Resolved GID
    umask: Optional[str] = None             # Resolved UMASK value
    uid_source: str = "unset"               # "env_PUID", "env_USER_ID", "compose_user", "default", "unset"
    gid_source: str = "unset"               # Same source types
    compose_user: Optional[str] = None      # Raw value of compose user: directive
    needs_explicit_id: bool = False         # Whether this image family expects explicit UID/GID
    is_root: bool = False                   # True if effective UID is 0

    def to_dict(self) -> dict:
        return {
            "service_name": self.service_name,
            "image": self.image,
            "image_family": self.image_family,
            "uid": self.uid,
            "gid": self.gid,
            "umask": self.umask,
            "uid_source": self.uid_source,
            "gid_source": self.gid_source,
            "is_root": self.is_root,
        }


# ─── Data Structures ───

@dataclass
class VolumeMount:
    """A single volume mount for a service."""
    raw: str                           # Original declaration from compose
    source: str                        # Host path (or named volume)
    target: str                        # Container path
    read_only: bool = False            # :ro flag
    is_named_volume: bool = False      # True if source is a named volume, not a path
    is_bind_mount: bool = True         # True if host path → container path

    def to_dict(self) -> dict:
        return {
            "raw": self.raw,
            "source": self.source,
            "target": self.target,
            "read_only": self.read_only,
            "is_named_volume": self.is_named_volume,
            "is_bind_mount": self.is_bind_mount,
        }


@dataclass
class ServiceInfo:
    """Analyzed information about a single service."""
    name: str
    image: str = ""
    role: str = "other"                # "arr", "download_client", "media_server", "request", "other"
    volumes: List[VolumeMount] = field(default_factory=list)
    environment: Dict[str, str] = field(default_factory=dict)
    data_paths: List[str] = field(default_factory=list)  # Container paths used for media/downloads
    compose_user: Optional[str] = None  # Compose `user:` directive (e.g., "1000:1000")

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "image": self.image,
            "role": self.role,
            "volumes": [v.to_dict() for v in self.volumes],
            "data_paths": self.data_paths,
            "compose_user": self.compose_user,
        }


@dataclass
class Conflict:
    """A detected path mapping conflict."""
    conflict_type: str       # "no_shared_mount", "different_host_paths", "hardlink_impossible", "path_unreachable"
    severity: str            # "critical", "high", "medium", "low"
    services: List[str]      # Affected service names
    description: str         # Human-readable explanation
    detail: str = ""         # Technical detail
    fix: Optional[str] = None  # Suggested fix (set by fix generator)
    rpm_hint: Optional[dict] = None  # If set, this conflict is an RPM scenario

    def to_dict(self) -> dict:
        d = {
            "type": self.conflict_type,
            "severity": self.severity,
            "services": self.services,
            "description": self.description,
            "detail": self.detail,
            "fix": self.fix,
        }
        if self.rpm_hint:
            d["rpm_hint"] = self.rpm_hint
        return d


@dataclass
class AnalysisResult:
    """Complete analysis result."""
    stack_path: str
    compose_file: str
    resolution_method: str          # "docker" or "manual"
    services: List[ServiceInfo]
    conflicts: List[Conflict]
    fix_summary: Optional[str] = None  # Overall fix recommendation
    solution_yaml: Optional[str] = None  # Copy-pasteable YAML fix
    original_corrected_yaml: Optional[str] = None  # User's compose with corrected volumes only
    solution_changed_lines: List[int] = field(default_factory=list)  # 1-indexed changed lines in solution_yaml
    original_changed_lines: List[int] = field(default_factory=list)  # 1-indexed changed lines in original_corrected_yaml
    mount_warnings: List[str] = field(default_factory=list)  # Remote FS / hardlink warnings
    mount_info: List[dict] = field(default_factory=list)  # Mount classifications
    warnings: List[str] = field(default_factory=list)
    steps: List[dict] = field(default_factory=list)  # Analysis step log for terminal UI

    incomplete_stack: bool = False  # True if missing arr or download client
    cross_stack: Optional[dict] = None  # Cross-stack analysis result (when incomplete + siblings found)
    pipeline: Optional[dict] = None  # Pipeline context (role, health, sibling awareness)
    rpm_mappings: List[dict] = field(default_factory=list)  # RPM calculator output for wizard

    def to_dict(self) -> dict:
        # Determine status: pipeline-aware > conflicts > cross-stack > incomplete > healthy
        if self.conflicts:
            status = "conflicts_found"
        elif self.pipeline:
            # Pipeline-aware status: this stack has full directory context.
            # Check if THIS stack specifically has pipeline conflicts, not just
            # the global pipeline health.  A stack that's aligned with the
            # majority shouldn't show "pipeline_conflict" just because other
            # stacks are misaligned.
            p_conflicts = self.pipeline.get("conflicts", [])
            stack_name = os.path.basename(self.stack_path) if self.stack_path else ""
            this_stack_has_conflict = any(
                c.get("stack_name") == stack_name for c in p_conflicts
            )
            if this_stack_has_conflict:
                status = "pipeline_conflict"
            elif self.incomplete_stack and self.cross_stack:
                status = "healthy_pipeline"
            else:
                status = "healthy_pipeline"
        elif self.incomplete_stack and self.cross_stack:
            cs = self.cross_stack
            if cs.get("missing_roles_filled") and cs.get("shared_mount"):
                status = "healthy_cross_stack"
            elif cs.get("conflicts"):
                status = "cross_stack_conflict"
            elif cs.get("missing_roles_filled"):
                status = "healthy_cross_stack"
            else:
                status = "incomplete"
        elif self.incomplete_stack:
            status = "incomplete"
        else:
            status = "healthy"

        return {
            "stack_path": self.stack_path,
            "stack_name": os.path.basename(self.stack_path),
            "compose_file": os.path.basename(self.compose_file),
            "compose_file_path": self.compose_file,
            "resolution_method": self.resolution_method,
            "services": [s.to_dict() for s in self.services],
            "service_count": len(self.services),
            "conflicts": [c.to_dict() for c in self.conflicts],
            "conflict_count": len(self.conflicts),
            "fix_summary": self.fix_summary,
            "solution_yaml": self.solution_yaml,
            "original_corrected_yaml": self.original_corrected_yaml,
            "solution_changed_lines": self.solution_changed_lines,
            "original_changed_lines": self.original_changed_lines,
            "mount_warnings": self.mount_warnings,
            "mount_info": self.mount_info,
            "warnings": self.warnings,
            "steps": self.steps,
            "status": status,
            "incomplete_stack": self.incomplete_stack,
            "cross_stack": self.cross_stack,
            "pipeline": self.pipeline,
            "rpm_mappings": self.rpm_mappings,
            "permission_profiles": [
                _build_permission_profile(s).to_dict()
                for s in self.services
                if s.role in ("arr", "download_client", "media_server")
            ],
        }


# ─── Main Entry Point ───

def analyze_stack(
    resolved_compose: Dict[str, Any],
    stack_path: str,
    compose_file: str,
    resolution_method: str,
    error_service: Optional[str] = None,
    error_path: Optional[str] = None,
    raw_compose_content: Optional[str] = None,
    scan_dir: Optional[str] = None,
    pipeline_context: Optional[Dict] = None,
) -> AnalysisResult:
    """
    Analyze a resolved compose file for path mapping issues.

    Args:
        resolved_compose: Output from resolver.resolve_compose()
        stack_path: Path to the stack directory
        compose_file: Path to the compose file
        resolution_method: "docker" or "manual"
        error_service: Service from parsed error (optional, for prioritization)
        error_path: Path from parsed error (optional, for tracing)
        raw_compose_content: Raw compose file content for patching
        scan_dir: Parent directory for cross-stack scanning (fallback if no pipeline)
        pipeline_context: Pre-computed pipeline context from run_pipeline_scan().
            When present, provides full awareness of all media services in the
            root directory — their roles, mount paths, and relationships. This
            replaces the old per-stack cross-stack scan with true pipeline intelligence.

    Returns:
        AnalysisResult with services, conflicts, and fix recommendations.
    """
    warnings = resolved_compose.get("_warnings", [])
    steps: List[dict] = []

    stack_name = os.path.basename(stack_path)
    logger.info("Starting analysis of %s (via %s)", stack_name, resolution_method)
    if error_service:
        logger.info("Error context: service=%s path=%s", error_service, error_path or "none")

    # Step 1: Resolve compose file (already done by caller, log it)
    steps.append({"icon": "ok", "text": f"Resolved {os.path.basename(compose_file)} via {resolution_method}"})

    # Step 2: Extract and classify services
    services = _extract_services(resolved_compose)
    for svc in services:
        vol_summary = ", ".join(f"{v.source}→{v.target}" for v in svc.volumes if v.is_bind_mount and not _is_config_mount(v.target))
        logger.info("Service: %s → role=%s, %d volumes%s",
                     svc.name, svc.role, len(svc.volumes),
                     f" [{vol_summary}]" if vol_summary else "")
    participants = [s for s in services if s.role in ("arr", "download_client", "media_server")]
    arr_names = [s.name for s in services if s.role == "arr"]
    dl_names = [s.name for s in services if s.role == "download_client"]
    ms_names = [s.name for s in services if s.role == "media_server"]
    steps.append({"icon": "ok", "text": f"Found {len(services)} services ({len(participants)} media-related)"})
    if arr_names:
        steps.append({"icon": "info", "text": f"*arr apps: {', '.join(arr_names)}"})
    if dl_names:
        steps.append({"icon": "info", "text": f"Download clients: {', '.join(dl_names)}"})
    if ms_names:
        steps.append({"icon": "info", "text": f"Media servers: {', '.join(ms_names)}"})

    # Step 3: Check volume mounts
    total_vols = sum(len(s.volumes) for s in services)
    data_vols = sum(len(s.data_paths) for s in services)
    steps.append({"icon": "ok", "text": f"Scanned {total_vols} volume mounts ({data_vols} data paths)"})

    # Step 4: Detect conflicts
    conflicts = _detect_conflicts(services, error_service, error_path, pipeline_context)
    for c in conflicts:
        logger.warning("Conflict [%s/%s]: %s — %s",
                       c.conflict_type, c.severity, ", ".join(c.services), c.description)
    if conflicts:
        steps.append({"icon": "warn", "text": f"Detected {len(conflicts)} path conflict{'s' if len(conflicts) != 1 else ''}"})
    else:
        steps.append({"icon": "ok", "text": "No path conflicts detected"})

    # Step 4b: Permissions analysis
    perm_conflicts = _check_permissions(services, pipeline_context)
    for c in perm_conflicts:
        logger.warning("Permission [%s/%s]: %s — %s",
                       c.conflict_type, c.severity, ", ".join(c.services), c.description)
    conflicts.extend(perm_conflicts)
    if perm_conflicts:
        steps.append({"icon": "warn", "text": f"Detected {len(perm_conflicts)} permission issue{'s' if len(perm_conflicts) != 1 else ''}"})
    else:
        steps.append({"icon": "ok", "text": "Permissions check passed"})

    # Step 5: Mount intelligence
    mount_classifications, mount_warnings = _analyze_mounts(services)
    mount_info = [mc.to_dict() for mc in mount_classifications]
    for mc in mount_classifications:
        logger.info("Mount: %s → %s%s",
                     mc.path, mc.mount_type or "local",
                     " (REMOTE)" if mc.is_remote else "")
    for mw in mount_warnings:
        logger.warning("Mount warning: %s", mw)
    if mount_classifications:
        steps.append({"icon": "ok", "text": f"Classified {len(mount_classifications)} host mount{'s' if len(mount_classifications) != 1 else ''}"})
    if mount_warnings:
        steps.append({"icon": "warn", "text": f"{len(mount_warnings)} filesystem warning{'s' if len(mount_warnings) != 1 else ''}"})

    # Promote remote-FS warnings to conflicts
    _add_mount_conflicts(conflicts, mount_classifications, services)

    # Inject pipeline mount mismatch as a real conflict so fix generation picks it up.
    # The pipeline knows this stack's mounts differ from the majority — e.g. this stack
    # uses /host/data but 6 other services use /srv/data.  Without this, the intra-stack
    # checks see no problem (both services share /host/data) and no fix is generated.
    #
    # Always capture pipeline_host_root when available — even if within-stack conflicts
    # already exist. This way the fix targets the pipeline majority root, resolving both
    # within-stack AND cross-stack issues in a single Apply Fix. Without this, the fix
    # only unifies mounts within the stack (e.g. /home/user) but the user still sees red
    # because the pipeline majority is /srv/data.
    pipeline_host_root = None  # Override for _detect_host_data_root when pipeline provides majority
    if pipeline_context:
        p_conflicts = pipeline_context.get("conflicts", [])
        stack_p_conflicts = [
            c for c in p_conflicts
            if c.get("stack_name") == os.path.basename(stack_path)
        ]
        if stack_p_conflicts:
            majority_root = stack_p_conflicts[0].get("majority_root", "")
            if majority_root:
                pipeline_host_root = majority_root
                # Only add pipeline conflict if there are no within-stack conflicts
                # already covering the issue — avoids duplicate/confusing messages.
                # But always set pipeline_host_root so fixes target the right root.
                if not conflicts:
                    affected = [s.name for s in services if s.role in ("arr", "download_client", "media_server")]
                    if affected:
                        conflicts.append(Conflict(
                            conflict_type="no_shared_mount",
                            severity="critical",
                            services=affected,
                            description=(
                                f"This stack's host mounts differ from the rest of your pipeline. "
                                f"Most services use {majority_root}."
                            ),
                            detail=f"Pipeline majority root: {majority_root}",
                        ))

    # Step 6: Generate fixes
    _generate_fixes(conflicts, services)
    fix_summary = _build_fix_summary(conflicts, services, error_service, pipeline_context)
    solution_yaml, solution_changed_lines = _generate_solution_yaml(
        conflicts, services, host_root_override=pipeline_host_root
    )
    if solution_yaml:
        logger.info("Generated solution YAML (%d lines, %d changed)",
                     solution_yaml.count("\n") + 1, len(solution_changed_lines))
        steps.append({"icon": "ok", "text": "Generated fix recommendation"})

    # Step 7: Generate corrected version of user's original compose
    original_corrected_yaml = None
    original_changed_lines: List[int] = []
    if solution_yaml and raw_compose_content:
        original_corrected_yaml, original_changed_lines = _patch_original_yaml(
            raw_compose_content, conflicts, services,
            host_root_override=pipeline_host_root,
        )
        if original_corrected_yaml:
            steps.append({"icon": "ok", "text": "Generated corrected version of your compose file"})

    # Check for incomplete stack (has some media services but missing key roles)
    incomplete_stack = False
    has_arr = any(s.role == "arr" for s in services)
    has_dl = any(s.role == "download_client" for s in services)
    has_media = any(s.role == "media_server" for s in services)
    has_any_media_role = has_arr or has_dl or has_media

    if has_any_media_role and not (has_arr and has_dl):
        incomplete_stack = True
        missing = []
        if not has_arr:
            missing.append("*arr apps")
        if not has_dl:
            missing.append("download clients")
        logger.info("Single-service stack: no %s in this compose file (has arr=%s dl=%s media=%s)",
                     " or ".join(missing), has_arr, has_dl, has_media)

    # Pipeline-aware analysis: use pre-computed pipeline context when available.
    # This is the "actually smart" path — we already scanned ALL stacks on boot,
    # so we know exactly what role this stack plays in the full media pipeline.
    cross_stack_result = None
    pipeline_data = None

    if pipeline_context and pipeline_context.get("total_media", 0) > 0:
        # Pipeline mode — full directory awareness
        pipeline_role = pipeline_context.get("role")
        total_media = pipeline_context.get("total_media", 0)
        pipeline_health = pipeline_context.get("health", "unknown")
        shared_mount = pipeline_context.get("shared_mount", False)
        mount_root = pipeline_context.get("mount_root", "")
        pipeline_conflicts = pipeline_context.get("conflicts", [])
        sibling_services = pipeline_context.get("sibling_services", [])
        services_by_role = pipeline_context.get("services_by_role", {})

        # Build role summary from pipeline
        arr_count = len(services_by_role.get("arr", []))
        dl_count = len(services_by_role.get("download_client", []))
        ms_count = len(services_by_role.get("media_server", []))

        # Show this stack's role in the pipeline
        role_label = {"arr": "*arr app", "download_client": "download client", "media_server": "media server"}.get(pipeline_role, "service")
        if pipeline_role:
            steps.append({"icon": "info", "text": f"{stack_name} | pipeline role: {role_label}"})

        # Show pipeline scope
        role_parts = []
        if arr_count:
            role_parts.append(f"{arr_count} *arr")
        if dl_count:
            role_parts.append(f"{dl_count} download")
        if ms_count:
            role_parts.append(f"{ms_count} media")
        pipeline_summary_text = f"Your pipeline: {', '.join(role_parts)} — {total_media} services total"
        steps.append({"icon": "info", "text": pipeline_summary_text})

        # Mount consistency from pipeline
        if shared_mount and mount_root:
            steps.append({"icon": "ok", "text": f"Mount consistency: all {total_media} services share {mount_root}"})
        elif pipeline_conflicts:
            # Show conflicts specific to this stack
            stack_conflicts = [c for c in pipeline_conflicts if c.get("stack_name") == os.path.basename(stack_path)]
            if stack_conflicts:
                for c in stack_conflicts:
                    steps.append({"icon": "warn", "text": c.get("description", "Mount conflict detected")})
            else:
                steps.append({"icon": "ok", "text": f"This stack's mounts align with the pipeline"})

        # Build cross_stack result from pipeline data for backward compat
        if incomplete_stack and sibling_services:
            # Stack is single-service but pipeline knows about siblings
            if not incomplete_stack:
                pass  # Not incomplete — pipeline covers it
            else:
                steps.append({"icon": "info", "text": f"Single-service stack — {len(sibling_services)} media siblings in pipeline"})

            cross_stack_result = {
                "siblings": sibling_services,
                "missing_roles_filled": sorted(set(
                    s.get("role", "") for s in sibling_services
                    if s.get("role") in {r for r in ("arr", "download_client", "media_server") if r not in {svc.role for svc in services if hasattr(svc, "role")}}
                )),
                "shared_mount": shared_mount,
                "mount_root": mount_root,
                "conflicts": pipeline_conflicts,
                "summary": pipeline_context.get("summary", ""),
                "sibling_count_scanned": len(sibling_services),
                "source": "pipeline",  # Flag that this came from pipeline, not legacy scan
            }
        elif incomplete_stack:
            steps.append({"icon": "info", "text": f"Single-service stack — no {' or '.join(missing)} in this compose file"})

        # Store pipeline data on the result
        pipeline_data = {
            "role": pipeline_role,
            "total_media": total_media,
            "health": pipeline_health,
            "shared_mount": shared_mount,
            "mount_root": mount_root,
            "services_by_role": services_by_role,
            "conflicts": pipeline_conflicts,
        }

        logger.info("Pipeline-aware analysis: role=%s, %d total media, health=%s",
                     pipeline_role, total_media, pipeline_health)

    elif incomplete_stack and scan_dir:
        # Legacy fallback: no pipeline data, scan siblings directly
        steps.append({"icon": "info", "text": f"Single-service stack — no {' or '.join(missing)} in this compose file"})
        steps.append({"icon": "run", "text": "Checking sibling stacks for complementary services..."})
        try:
            from backend.cross_stack import check_cross_stack
            cs = check_cross_stack(stack_path, scan_dir, services)
            if cs and (cs.siblings_found or cs.sibling_count_scanned > 0):
                cross_stack_result = cs.to_dict()
                if cs.siblings_found:
                    sib_names = [f"{s.service_name} (../{s.stack_name})" for s in cs.siblings_found]
                    steps.append({"icon": "ok", "text": f"Found {', '.join(sib_names)}"})
                    if cs.shared_mount:
                        steps.append({"icon": "ok", "text": f"Cross-stack check: shared mount {cs.mount_root} detected"})
                    elif cs.conflicts:
                        steps.append({"icon": "warn", "text": "Cross-stack conflict: different host mount roots"})
                    else:
                        steps.append({"icon": "ok", "text": "Cross-stack: complementary services found"})
                else:
                    steps.append({"icon": "info", "text": f"Scanned {cs.sibling_count_scanned} siblings — none fill missing roles"})
        except Exception as e:
            logger.debug("Cross-stack check failed: %s", e)
            steps.append({"icon": "info", "text": "Cross-stack scan skipped"})
    elif incomplete_stack:
        steps.append({"icon": "info", "text": f"Single-service stack — no {' or '.join(missing)} in this compose file"})

    # RPM calculation: compute Remote Path Mapping entries for all
    # (download_client, arr_app) pairs.  The wizard uses these to guide users
    # through bridging container-path mismatches without restructuring mounts.
    rpm_mappings = []
    if pipeline_context:
        rpm_mappings = _calculate_rpm_mappings(services, pipeline_context, stack_path=stack_path)
        if rpm_mappings:
            possible_count = sum(1 for m in rpm_mappings if m["possible"])
            logger.info("RPM calculator: %d mappings (%d possible, %d impossible)",
                        len(rpm_mappings), possible_count, len(rpm_mappings) - possible_count)

    status_preview = "conflicts" if conflicts else ("incomplete" if incomplete_stack else "healthy")
    if pipeline_data:
        status_preview = f"pipeline ({pipeline_data.get('health', '?')})"
    elif cross_stack_result:
        status_preview = "cross-stack (%s)" % ("shared" if cross_stack_result.get("shared_mount") else "conflict")
    logger.info("Analysis complete: %s → %s (%d services, %d conflicts)",
                 stack_name, status_preview, len(services), len(conflicts))

    steps.append({"icon": "done", "text": "Analysis complete"})

    return AnalysisResult(
        stack_path=stack_path,
        compose_file=compose_file,
        resolution_method=resolution_method,
        services=services,
        conflicts=conflicts,
        fix_summary=fix_summary,
        solution_yaml=solution_yaml,
        original_corrected_yaml=original_corrected_yaml,
        solution_changed_lines=solution_changed_lines,
        original_changed_lines=original_changed_lines,
        mount_warnings=mount_warnings,
        mount_info=mount_info,
        warnings=warnings,
        steps=steps,
        incomplete_stack=incomplete_stack,
        cross_stack=cross_stack_result,
        pipeline=pipeline_data,
        rpm_mappings=rpm_mappings,
    )


# ─── Step 1: Extract Services ───

def _extract_services(compose: Dict[str, Any]) -> List[ServiceInfo]:
    """Extract and classify all services from resolved compose data."""
    services = []
    raw_services = compose.get("services", {})

    for name, config in raw_services.items():
        if not isinstance(config, dict):
            continue

        info = ServiceInfo(name=name)
        info.image = config.get("image", "")
        info.role = _classify_service(name, info.image)
        info.volumes = _parse_volumes(config.get("volumes", []))
        info.environment = _extract_env(config.get("environment", {}))
        info.compose_user = str(config["user"]) if "user" in config else None
        info.data_paths = _identify_data_paths(info)

        services.append(info)

    return services


def _classify_service(name: str, image: str) -> str:
    """Classify a service by its role in the media stack."""
    name_lower = name.lower()
    image_lower = image.lower()

    # Check name first (most reliable for user-named services)
    for check in (name_lower, image_lower):
        for app in ARR_APPS:
            if app in check:
                return "arr"
        for client in DOWNLOAD_CLIENTS:
            if client in check:
                return "download_client"
        for server in MEDIA_SERVERS:
            if server in check:
                return "media_server"
        for req in REQUEST_APPS:
            if req in check:
                return "request"

    return "other"


def _parse_volumes(volumes_raw: list) -> List[VolumeMount]:
    """Parse volume declarations (short and long syntax)."""
    mounts = []

    for vol in volumes_raw:
        if isinstance(vol, str):
            mount = _parse_short_volume(vol)
            if mount:
                mounts.append(mount)
        elif isinstance(vol, dict):
            mount = _parse_long_volume(vol)
            if mount:
                mounts.append(mount)

    return mounts


def _parse_short_volume(vol_str: str) -> Optional[VolumeMount]:
    """
    Parse short-syntax volume: source:target[:ro]

    Examples:
      /host/path:/container/path
      /host/path:/container/path:ro
      named_volume:/container/path
      ./relative:/container/path
    """
    parts = vol_str.split(":")

    if len(parts) < 2:
        # Single path — anonymous volume or just target
        return VolumeMount(
            raw=vol_str,
            source="",
            target=vol_str,
            is_named_volume=False,
            is_bind_mount=False,
        )

    # Handle Windows paths (C:\path → has a colon but it's a drive letter)
    if len(parts) >= 3 and len(parts[0]) == 1 and parts[0].isalpha():
        # Windows path: C:\host\path:/container/path[:ro]
        source = parts[0] + ":" + parts[1]
        target = parts[2]
        read_only = len(parts) > 3 and parts[3].strip().lower() == "ro"
    elif len(parts) >= 3 and "/" in parts[1]:
        # NFS syntax: nfs-server:/remote/path:/container/path[:ro]
        # The second part contains "/" so it's a remote path, not a container path.
        source = parts[0] + ":" + parts[1]
        target = parts[2]
        read_only = len(parts) > 3 and parts[3].strip().lower() == "ro"
    else:
        source = parts[0]
        target = parts[1]
        read_only = len(parts) > 2 and parts[2].strip().lower() == "ro"

    # Detect if this is a bind mount (host path) vs a named Docker volume.
    # Named volumes are simple identifiers like "mydata". Everything else is a path.
    is_named = not (
        source.startswith("/")
        or source.startswith("./")
        or source.startswith("../")
        or source.startswith("~")
        or source.startswith("//")                    # UNC/SMB path
        or (len(source) >= 2 and source[1] == ":")    # Windows drive letter (C:)
        or (":" in source and "/" in source)           # NFS remote (server:/path)
    )

    return VolumeMount(
        raw=vol_str,
        source=source,
        target=target,
        read_only=read_only,
        is_named_volume=is_named,
        is_bind_mount=not is_named,
    )


def _parse_long_volume(vol_dict: dict) -> Optional[VolumeMount]:
    """Parse long-syntax volume: {type, source, target, read_only, ...}"""
    source = vol_dict.get("source", "")
    target = vol_dict.get("target", "")
    vol_type = vol_dict.get("type", "volume")
    read_only = vol_dict.get("read_only", False)

    if not target:
        return None

    return VolumeMount(
        raw=f"{source}:{target}" if source else target,
        source=source,
        target=target,
        read_only=read_only,
        is_named_volume=(vol_type == "volume"),
        is_bind_mount=(vol_type == "bind"),
    )


def _extract_env(env_raw) -> Dict[str, str]:
    """Extract environment variables (handles list and dict formats)."""
    if isinstance(env_raw, dict):
        return {k: str(v) for k, v in env_raw.items()}
    if isinstance(env_raw, list):
        result = {}
        for item in env_raw:
            if "=" in str(item):
                key, _, val = str(item).partition("=")
                result[key] = val
        return result
    return {}


def _identify_data_paths(service: ServiceInfo) -> List[str]:
    """
    Identify which container paths are data paths (media, downloads, etc).

    Data paths are the ones that matter for hardlinks and atomic moves.
    Config paths (/config, /app) are not relevant.
    """
    data_paths = []
    skip_targets = {"/config", "/app", "/etc", "/var", "/tmp", "/run", "/dev"}

    for vol in service.volumes:
        target = vol.target

        # Skip config/system mounts
        if any(target == s or target.startswith(s + "/") for s in skip_targets):
            continue

        # Skip named volumes (usually config)
        if vol.is_named_volume:
            continue

        data_paths.append(target)

    return data_paths


def _is_config_mount(target: str) -> bool:
    """Check if a container path is a config/system mount (not data)."""
    config_targets = {"/config", "/app", "/etc", "/var", "/tmp", "/run", "/dev"}
    target = target.rstrip("/")
    return any(target == s or target.startswith(s + "/") for s in config_targets)


# ─── Step 2: Conflict Detection ───

def _detect_conflicts(
    services: List[ServiceInfo],
    error_service: Optional[str],
    error_path: Optional[str],
    pipeline_context: Optional[Dict] = None,
) -> List[Conflict]:
    """
    Detect path mapping conflicts.

    Checks:
    1. Hardlink-participant services without a shared parent mount
    2. Same container path backed by different host paths across services
    3. Error path unreachable by the error service (RPM-aware)
    """
    conflicts: List[Conflict] = []

    # Get hardlink participants from this stack
    participants = [s for s in services if s.role in ("arr", "download_client", "media_server")]

    # Check 0: Named volumes used for data paths (hardlinks impossible)
    conflicts.extend(_check_named_volume_data(participants))

    if len(participants) >= 2:
        # Check 1: No shared parent mount (the #1 *arr problem)
        conflicts.extend(_check_shared_mount(participants))

        # Check 2: Inconsistent host path mapping
        conflicts.extend(_check_host_path_consistency(participants))

    # Check 3: Error path unreachable (RPM-aware — detects DC path patterns)
    if error_service and error_path:
        conflicts.extend(
            _check_error_path_reachable(
                services, error_service, error_path, pipeline_context
            )
        )

    # Deduplicate and sort by severity
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    conflicts.sort(key=lambda c: severity_order.get(c.severity, 99))

    return conflicts


def _check_named_volume_data(participants: List[ServiceInfo]) -> List[Conflict]:
    """
    Detect named Docker volumes used for data paths.

    Named volumes (e.g. `tv_data:/data/tv`) are managed by Docker and stored
    in Docker's internal volume directory. Each named volume is a separate
    filesystem — hardlinks CANNOT cross between them. Data volumes must be
    bind mounts from a shared host directory for hardlinks to work.

    This is a common mistake: users declare volumes in the `volumes:` section
    thinking it's cleaner, but it silently breaks hardlinks and atomic moves.
    """
    conflicts = []
    affected_services = []

    for svc in participants:
        named_data_vols = []
        for vol in svc.volumes:
            if vol.is_named_volume and not _is_config_mount(vol.target):
                named_data_vols.append(vol)
        if named_data_vols:
            affected_services.append((svc.name, named_data_vols))

    if not affected_services:
        return conflicts

    detail_lines = []
    svc_names = []
    for svc_name, vols in affected_services:
        svc_names.append(svc_name)
        for vol in vols:
            detail_lines.append(f"  {svc_name}: {vol.source}:{vol.target} (named volume)")

    conflicts.append(Conflict(
        conflict_type="named_volume_data",
        severity="critical",
        services=svc_names,
        description=(
            "Data volumes use Docker named volumes instead of bind mounts. "
            "Each named volume is a separate filesystem — hardlinks and atomic "
            "moves CANNOT work across them. Switch to bind mounts from a "
            "shared host directory."
        ),
        detail="Named volumes used for data:\n" + "\n".join(detail_lines),
    ))

    logger.warning(
        "Conflict [named_volume_data/critical]: %s — Named volumes used for data paths",
        ", ".join(svc_names),
    )

    return conflicts


def _check_shared_mount(participants: List[ServiceInfo]) -> List[Conflict]:
    """
    Check if hardlink participants share a common parent mount.

    For hardlinks to work, ALL services need to see data through the SAME
    host directory. If sonarr mounts /host/tv:/data/tv and qbittorrent
    mounts /host/downloads:/downloads, hardlinks fail because they're
    separate bind mounts (different filesystems from Docker's perspective).

    The fix: Mount a common parent like /host/data:/data for all services.
    """
    conflicts = []

    # Collect bind mount sources (host paths) for DATA volumes only.
    # Config mounts (/config, ./config) are irrelevant for hardlinks.
    service_host_roots: Dict[str, set] = {}
    for svc in participants:
        roots = set()
        for vol in svc.volumes:
            if not vol.is_bind_mount or not vol.source:
                continue
            # Skip config/system targets — they don't affect hardlinks
            if _is_config_mount(vol.target):
                continue
            root = _get_path_root(vol.source)
            if root:
                roots.add(root)
        if roots:
            service_host_roots[svc.name] = roots

    if len(service_host_roots) < 2:
        return conflicts

    # Check if all services share at least one common host root
    all_roots = list(service_host_roots.values())
    common_roots = all_roots[0]
    for roots in all_roots[1:]:
        common_roots = common_roots & roots

    if not common_roots:
        # No shared root — this is the #1 problem
        detail_lines = []
        for svc_name, roots in service_host_roots.items():
            detail_lines.append(f"  {svc_name}: {', '.join(sorted(roots))}")

        conflicts.append(Conflict(
            conflict_type="no_shared_mount",
            severity="critical",
            services=list(service_host_roots.keys()),
            description=(
                "Your services mount different host directories. "
                "Hardlinks and atomic moves CANNOT work across separate bind mounts."
            ),
            detail="Host path roots per service:\n" + "\n".join(detail_lines),
        ))

    return conflicts


def _check_host_path_consistency(participants: List[ServiceInfo]) -> List[Conflict]:
    """
    Check for services that map the same container path to different host paths.

    Example conflict:
      sonarr:       /host/media/tv:/data/tv
      radarr:       /different/tv:/data/tv
    Both see /data/tv but it points to different host directories.
    """
    conflicts = []

    # Build target → [(service, source)] mapping (data volumes only)
    target_sources: Dict[str, List[Tuple[str, str]]] = {}
    for svc in participants:
        for vol in svc.volumes:
            if not vol.is_bind_mount or not vol.source:
                continue
            if _is_config_mount(vol.target):
                continue
            key = vol.target
            if key not in target_sources:
                target_sources[key] = []
            target_sources[key].append((svc.name, vol.source))

    for target, entries in target_sources.items():
        sources = set(src for _, src in entries)
        if len(sources) > 1:
            svc_names = [svc for svc, _ in entries]
            detail_lines = [f"  {svc}: {src}" for svc, src in entries]

            conflicts.append(Conflict(
                conflict_type="different_host_paths",
                severity="high",
                services=svc_names,
                description=(
                    f"Multiple services mount {target} but from different host paths. "
                    f"They think they're sharing data, but they're not."
                ),
                detail="Mappings:\n" + "\n".join(detail_lines),
            ))

    return conflicts


def _check_error_path_reachable(
    services: List[ServiceInfo],
    error_service: str,
    error_path: str,
    pipeline_context: Optional[Dict] = None,
) -> List[Conflict]:
    """
    Check if the path from the error message is reachable by the service.

    If the user's error says "Sonarr can't find /data/downloads/file.mkv",
    check whether Sonarr has a volume mount that covers /data/downloads.

    RPM-aware: if the error path matches a download client's container path,
    this is an RPM scenario — the DC reported a path using its own container
    namespace, and the arr app doesn't have it mounted. We flag this so the
    frontend can offer the RPM wizard.
    """
    conflicts = []

    # Find the service
    target_svc = None
    for svc in services:
        if error_service.lower() in svc.name.lower():
            target_svc = svc
            break

    if not target_svc:
        return conflicts

    # Check if any volume mount covers the error path
    error_path_posix = error_path.replace("\\", "/")
    reachable = False

    for vol in target_svc.volumes:
        target = vol.target.rstrip("/")
        if error_path_posix == target or error_path_posix.startswith(target + "/"):
            reachable = True
            break

    if not reachable:
        # Find the closest mount that COULD cover it
        closest = _find_closest_mount(target_svc, error_path_posix)

        # RPM detection: check if the error path looks like it came from a
        # download client's container namespace. If a DC's container mount
        # covers this path, the DC told the arr app "file is at X" but the
        # arr app can't see X — classic RPM scenario.
        rpm_hint = _detect_rpm_scenario(
            error_path_posix, target_svc, services, pipeline_context
        )

        conflict = Conflict(
            conflict_type="path_unreachable",
            severity="critical",
            services=[target_svc.name],
            description=(
                f"{target_svc.name} cannot access {error_path} — "
                f"no volume mount covers this path inside the container."
            ),
            detail=(
                f"Available mounts: {', '.join(v.target for v in target_svc.volumes)}"
                + (f"\nClosest match: {closest}" if closest else "")
            ),
            rpm_hint=rpm_hint,
        )
        conflicts.append(conflict)

    return conflicts


def _detect_rpm_scenario(
    error_path: str,
    arr_svc: ServiceInfo,
    services: List[ServiceInfo],
    pipeline_context: Optional[Dict],
) -> Optional[dict]:
    """
    Detect if an unreachable error path matches a download client's container path.

    When qBittorrent tells Sonarr "file at /downloads/tv/show.mkv", and sonarr
    doesn't have /downloads mounted, the error path /downloads/tv/show.mkv matches
    qBittorrent's container path /downloads. This is a Remote Path Mapping scenario.

    Returns an rpm_hint dict with the matching DC info, or None if no match.
    """
    # Collect DC services from this stack and pipeline siblings
    dc_candidates = []

    # From this stack's services
    for svc in services:
        if svc.role == "download_client":
            container_paths = [
                v.target.rstrip("/") for v in svc.volumes
                if v.is_bind_mount and not _is_config_mount(v.target)
            ]
            dc_candidates.append({
                "name": svc.name,
                "stack": "",  # same stack
                "container_paths": container_paths,
                "volumes": [
                    {"source": v.source.replace("\\", "/"), "target": v.target}
                    for v in svc.volumes
                    if v.is_bind_mount and not _is_config_mount(v.target)
                ],
            })

    # From pipeline siblings
    if pipeline_context:
        for sib in pipeline_context.get("sibling_services", []):
            if sib.get("role") == "download_client":
                mounts = sib.get("volume_mounts", [])
                container_paths = [m.get("target", "").rstrip("/") for m in mounts]
                dc_candidates.append({
                    "name": sib.get("service_name", ""),
                    "stack": sib.get("stack_name", ""),
                    "container_paths": container_paths,
                    "volumes": mounts,
                })

    if not dc_candidates:
        return None

    # Check if the error path matches any DC's container path
    for dc in dc_candidates:
        for cp in dc["container_paths"]:
            if not cp:
                continue
            if error_path == cp or error_path.startswith(cp + "/"):
                logger.info(
                    "RPM hint: error path %s matches DC %s container path %s",
                    error_path, dc["name"], cp,
                )
                return {
                    "dc_name": dc["name"],
                    "dc_stack": dc["stack"],
                    "dc_container_path": cp,
                    "arr_name": arr_svc.name,
                    "description": (
                        f"This path looks like it came from {dc['name']}'s "
                        f"container ({cp}). {arr_svc.name} can't see it because "
                        f"the mount structures differ. Remote Path Mapping can "
                        f"bridge this without restructuring your mounts."
                    ),
                }

    return None


def _find_closest_mount(service: ServiceInfo, path: str) -> Optional[str]:
    """Find the volume mount target that most closely matches the given path."""
    best = None
    best_len = 0

    for vol in service.volumes:
        target = vol.target.rstrip("/")
        # Check if they share a common prefix
        common = os.path.commonpath([path, target]) if target else ""
        if len(common) > best_len and common != "/":
            best = vol.target
            best_len = len(common)

    return best


def _get_path_root(path: str) -> Optional[str]:
    """
    Get the meaningful root of a host path.

    /host/data/tv → /host/data
    /mnt/nas/media → /mnt/nas
    ./data → ./data

    We go 2 levels deep for absolute paths, 1 for relative.
    """
    original = path
    path = path.replace("\\", "/").rstrip("/")

    if not path:
        # "/" strips to "" — that's root. But "" input is None.
        return "/" if original else None

    parts = path.split("/")

    # Absolute paths: use first 3 components (/host/data)
    if path.startswith("/"):
        meaningful = [p for p in parts if p]
        if len(meaningful) >= 2:
            return "/" + "/".join(meaningful[:2])
        elif meaningful:
            return "/" + meaningful[0]
        return "/"

    # Relative or Windows paths: use first 2 components
    if len(parts) >= 2:
        return "/".join(parts[:2])
    return parts[0] if parts else None


# ─── Step 2b: Permissions Analysis ───
#
# Pass 3 of the analysis engine. Detects UID/GID/UMASK issues across services
# that share data volumes. Complements the mount topology checks in
# _detect_conflicts() which handle path mapping (Passes 1-2).
#
# Resolution priority for user identity:
#   1. compose `user:` directive — Docker enforces this regardless of env vars
#   2. Image-family env vars (PUID, USER_ID, PLEX_UID, etc.)
#   3. Image family defaults (e.g., LinuxServer.io → UID 911)
#   4. Unknown (unrecognized image, no user: directive)


def _identify_image_family(image: str) -> Optional[ImageFamily]:
    """Identify which image family a Docker image belongs to.

    Matches the image string against known patterns in IMAGE_FAMILIES.
    Returns the first matching family, or None for unrecognized images.
    """
    image_lower = image.lower()
    for family in IMAGE_FAMILIES:
        for pattern in family.image_patterns:
            if pattern.lower() in image_lower:
                return family
    return None


def _build_permission_profile(service: ServiceInfo) -> PermissionProfile:
    """Build a unified permission profile from a service's configuration.

    Resolves UID/GID from multiple possible sources into a single profile,
    following the resolution priority: compose user: > env vars > defaults.
    """
    family = _identify_image_family(service.image)
    profile = PermissionProfile(
        service_name=service.name,
        image=service.image,
        image_family=family.name if family else None,
    )

    # Source 1: compose user: directive (takes precedence — Docker enforces it)
    if service.compose_user:
        profile.compose_user = service.compose_user
        user_str = str(service.compose_user).strip()
        parts = user_str.split(":")
        profile.uid = parts[0].strip()
        profile.gid = parts[1].strip() if len(parts) > 1 else parts[0].strip()
        profile.uid_source = "compose_user"
        profile.gid_source = "compose_user"

    # Source 2: image-family-specific env vars
    if family:
        profile.needs_explicit_id = family.needs_puid

        # UID from env var (only if compose user: didn't already set it)
        if family.uid_env and family.uid_env in service.environment:
            env_uid = service.environment[family.uid_env].strip()
            if not profile.uid:
                profile.uid = env_uid
                profile.uid_source = f"env_{family.uid_env}"

        # GID from env var
        if family.gid_env and family.gid_env in service.environment:
            env_gid = service.environment[family.gid_env].strip()
            if not profile.gid:
                profile.gid = env_gid
                profile.gid_source = f"env_{family.gid_env}"

        # UMASK from env var
        if family.umask_env and family.umask_env in service.environment:
            profile.umask = service.environment[family.umask_env].strip()

        # Source 3: fall back to family defaults if env vars not set
        if not profile.uid and family.default_uid:
            profile.uid = family.default_uid
            profile.uid_source = "default"
        if not profile.gid and family.default_gid:
            profile.gid = family.default_gid
            profile.gid_source = "default"

    # Detect root execution
    if profile.uid in ("0", "root"):
        profile.is_root = True

    return profile


def _normalize_umask(value: str) -> str:
    """Normalize UMASK to 3-digit octal string.

    '0002' → '002', '002' → '002', '22' → '022'.
    Handles quoted values and leading zeros.
    """
    value = value.strip().strip("'\"")
    try:
        as_int = int(value, 8)
        return format(as_int, "03o")
    except ValueError:
        return value  # Can't parse — return as-is for error reporting


def _check_permissions(
    services: List[ServiceInfo],
    pipeline_context: Optional[Dict] = None,
) -> List[Conflict]:
    """Permissions analysis pass — detect UID/GID/UMASK issues.

    This is Pass 3 of the analysis engine. It checks:
      1. PUID/PGID mismatch across services sharing data (high)
      2. Missing PUID/PGID on images that need it (medium)
      3. Root execution warning (medium)
      4. UMASK inconsistency / restrictive values (low)
      5. Cross-stack PUID/PGID mismatch via pipeline (high)
    """
    conflicts: List[Conflict] = []

    # Only analyze hardlink participants — they share data volumes
    participants = [
        s for s in services
        if s.role in ("arr", "download_client", "media_server")
    ]

    if not participants:
        return conflicts

    # Check 1: PUID/PGID mismatch (high — causes real file access failures)
    conflicts.extend(_check_puid_pgid_mismatch(participants))

    # Check 2: Missing PUID/PGID on images that need it (medium)
    conflicts.extend(_check_missing_puid_pgid(participants))

    # Check 3: Root execution (medium — security + ownership concerns)
    conflicts.extend(_check_root_execution(participants))

    # Check 4: UMASK inconsistency (low — hygiene / best practice)
    conflicts.extend(_check_umask_consistency(participants))

    # Check 5: Cross-stack PUID/PGID mismatch (when pipeline available)
    if pipeline_context:
        conflicts.extend(
            _check_cross_stack_permissions(participants, pipeline_context)
        )

    return conflicts


def _check_puid_pgid_mismatch(participants: List[ServiceInfo]) -> List[Conflict]:
    """Detect PUID/PGID mismatches across services sharing data volumes.

    The #1 permissions issue: Sonarr writes files as UID 1000 but Plex reads
    them as UID 911 and gets 'permission denied'. All services sharing the
    same data directory must run as the same UID:GID.
    """
    profiles = [_build_permission_profile(svc) for svc in participants]

    # Only consider profiles where we actually know the UID
    known_profiles = [p for p in profiles if p.uid is not None]
    if len(known_profiles) < 2:
        return []

    # Group by (uid, gid) pair
    identity_groups: Dict[Tuple[str, str], List[PermissionProfile]] = {}
    for p in known_profiles:
        key = (p.uid, p.gid or p.uid)  # If no GID, assume same as UID
        identity_groups.setdefault(key, []).append(p)

    if len(identity_groups) <= 1:
        return []  # All consistent

    # Find the majority identity for fix recommendations
    majority_key = max(identity_groups, key=lambda k: len(identity_groups[k]))
    majority_uid, majority_gid = majority_key

    outlier_names = []
    for key, group_profiles in identity_groups.items():
        if key != majority_key:
            for p in group_profiles:
                outlier_names.append(p.service_name)

    majority_names = [p.service_name for p in identity_groups[majority_key]]

    detail_lines = []
    for p in known_profiles:
        source_label = p.uid_source
        if source_label.startswith("env_"):
            source_label = source_label[4:]  # "env_PUID" → "PUID"
        detail_lines.append(
            f"  {p.service_name}: UID={p.uid} GID={p.gid or '?'} (via {source_label})"
        )

    return [Conflict(
        conflict_type="puid_pgid_mismatch",
        severity="high",
        services=outlier_names + majority_names,
        description=(
            f"Services run as different users — "
            f"{', '.join(majority_names)} use UID:GID {majority_uid}:{majority_gid}, "
            f"but {', '.join(outlier_names)} {'use' if len(outlier_names) > 1 else 'uses'} "
            f"different values. Files created by one service may be unreadable by others."
        ),
        detail="User identity per service:\n" + "\n".join(detail_lines),
    )]


def _check_missing_puid_pgid(participants: List[ServiceInfo]) -> List[Conflict]:
    """Detect services that should have PUID/PGID set but don't.

    LinuxServer.io images default to UID 911 if PUID is not set. This is
    almost never what the user wants — their arr apps and download clients
    should all run as the same user.
    """
    missing_services = []

    for svc in participants:
        family = _identify_image_family(svc.image)
        if not family or not family.needs_puid:
            continue  # Unknown image or doesn't need explicit UID
        if svc.compose_user:
            continue  # Has user: directive — identity is set

        has_uid = family.uid_env and family.uid_env in svc.environment
        has_gid = family.gid_env and family.gid_env in svc.environment

        if not has_uid or not has_gid:
            missing_parts = []
            if not has_uid and family.uid_env:
                missing_parts.append(f"{family.uid_env} (defaults to {family.default_uid})")
            if not has_gid and family.gid_env:
                missing_parts.append(f"{family.gid_env} (defaults to {family.default_gid})")
            missing_services.append((svc.name, family.name, missing_parts))

    if not missing_services:
        return []

    svc_names = [name for name, _, _ in missing_services]
    detail_lines = [
        f"  {name} ({family}): missing {', '.join(parts)}"
        for name, family, parts in missing_services
    ]

    return [Conflict(
        conflict_type="missing_puid_pgid",
        severity="medium",
        services=svc_names,
        description=(
            f"{len(svc_names)} service{'s' if len(svc_names) != 1 else ''} "
            f"{'are' if len(svc_names) != 1 else 'is'} missing explicit user identity. "
            f"Without PUID/PGID, the container falls back to internal defaults "
            f"that may not match your other services."
        ),
        detail="Missing configuration:\n" + "\n".join(detail_lines),
    )]


def _check_root_execution(participants: List[ServiceInfo]) -> List[Conflict]:
    """Warn when media services run as root (PUID=0 or user: 0).

    Running as root means all files are owned by root. Other services
    running as non-root may not be able to modify those files. It's also
    a security concern — container breakouts as root are more dangerous.
    """
    root_services = []

    for svc in participants:
        profile = _build_permission_profile(svc)
        if profile.is_root:
            root_services.append((svc.name, profile.uid_source))

    if not root_services:
        return []

    svc_names = [name for name, _ in root_services]
    detail_lines = [f"  {name} (via {source})" for name, source in root_services]

    return [Conflict(
        conflict_type="root_execution",
        severity="medium",
        services=svc_names,
        description=(
            f"{', '.join(svc_names)} {'run' if len(svc_names) > 1 else 'runs'} "
            f"as root (UID 0). Files will be owned by root — other non-root "
            f"services may not be able to modify them, and container breakouts "
            f"as root are a security risk."
        ),
        detail="Root services:\n" + "\n".join(detail_lines),
    )]


def _check_umask_consistency(participants: List[ServiceInfo]) -> List[Conflict]:
    """Detect inconsistent or problematic UMASK values across services."""
    conflicts: List[Conflict] = []
    umask_map: Dict[str, List[str]] = {}  # normalized_umask → [service_names]

    for svc in participants:
        profile = _build_permission_profile(svc)
        if profile.umask:
            norm = _normalize_umask(profile.umask)
            umask_map.setdefault(norm, []).append(svc.name)

    if not umask_map:
        return conflicts  # No UMASK set anywhere — nothing to compare

    # Check for inconsistency across services
    if len(umask_map) > 1:
        detail_lines = [
            f"  {svc}: UMASK={umask}"
            for umask, svcs in umask_map.items() for svc in svcs
        ]
        all_names = [svc for svcs in umask_map.values() for svc in svcs]

        conflicts.append(Conflict(
            conflict_type="umask_inconsistent",
            severity="low",
            services=all_names,
            description=(
                "Services use different UMASK values. Files created by each "
                "service will have different permissions, which can cause "
                "access issues between services."
            ),
            detail="UMASK values:\n" + "\n".join(detail_lines),
        ))

    # Check for overly restrictive UMASK (077+ blocks group/other access)
    for umask_val, svc_names in umask_map.items():
        try:
            umask_int = int(umask_val, 8)
            if umask_int >= 0o070:
                conflicts.append(Conflict(
                    conflict_type="umask_restrictive",
                    severity="low",
                    services=svc_names,
                    description=(
                        f"UMASK {umask_val} is very restrictive — group and other "
                        f"users get no access to new files. If services run as "
                        f"different users, they won't be able to read each other's files."
                    ),
                    detail=f"Services with restrictive UMASK: {', '.join(svc_names)}",
                ))
        except ValueError:
            pass

    return conflicts


def _check_cross_stack_permissions(
    current_participants: List[ServiceInfo],
    pipeline_context: Dict,
) -> List[Conflict]:
    """Check for PUID/PGID mismatches between this stack and pipeline siblings.

    When services live in separate stacks (common with Komodo/Portainer),
    the within-stack check won't catch cross-stack UID mismatches. This
    check uses pipeline data to compare UIDs across the entire media pipeline.
    """
    # Build profiles for current stack's participants
    current_profiles = {}
    for svc in current_participants:
        profile = _build_permission_profile(svc)
        if profile.uid is not None:
            current_profiles[svc.name] = profile

    if not current_profiles:
        return []

    # Find this stack's majority UID:GID
    uid_counts: Dict[Tuple[str, str], int] = {}
    for profile in current_profiles.values():
        key = (profile.uid, profile.gid or profile.uid)
        uid_counts[key] = uid_counts.get(key, 0) + 1

    current_majority = max(uid_counts, key=uid_counts.get)
    current_uid, current_gid = current_majority

    # Build profiles for pipeline siblings
    sibling_services = pipeline_context.get("media_services", [])
    if not sibling_services:
        return []

    # Get current stack name for filtering
    stack_path = pipeline_context.get("scan_dir", "")
    mismatched_siblings = []

    for sib in sibling_services:
        sib_image = sib.get("image", "")
        sib_env = sib.get("environment", {})
        sib_user = sib.get("compose_user")
        sib_name = sib.get("service_name", "")
        sib_stack = sib.get("stack_name", "")

        # Skip services in the current stack (already checked within-stack)
        if sib_name in current_profiles:
            continue

        # Resolve sibling's UID
        sib_uid = None
        sib_gid = None

        if sib_user:
            parts = str(sib_user).split(":")
            sib_uid = parts[0].strip()
            sib_gid = parts[1].strip() if len(parts) > 1 else sib_uid
        else:
            family = _identify_image_family(sib_image)
            if family:
                if family.uid_env and family.uid_env in sib_env:
                    sib_uid = sib_env[family.uid_env]
                elif family.default_uid:
                    sib_uid = family.default_uid
                if family.gid_env and family.gid_env in sib_env:
                    sib_gid = sib_env[family.gid_env]
                elif family.default_gid:
                    sib_gid = family.default_gid

        if sib_uid and (sib_uid, sib_gid or sib_uid) != current_majority:
            mismatched_siblings.append(
                f"  {sib_name} ({sib_stack}): UID={sib_uid} GID={sib_gid or '?'}"
            )

    if not mismatched_siblings:
        return []

    return [Conflict(
        conflict_type="cross_stack_puid_mismatch",
        severity="high",
        services=[name for name in current_profiles],
        description=(
            f"Cross-stack permission mismatch: this stack's services run as "
            f"UID:GID {current_uid}:{current_gid}, but "
            f"{len(mismatched_siblings)} sibling "
            f"service{'s' if len(mismatched_siblings) != 1 else ''} "
            f"in your pipeline use different values."
        ),
        detail=(
            f"This stack: UID:GID {current_uid}:{current_gid}\n"
            f"Mismatched siblings:\n" + "\n".join(mismatched_siblings)
        ),
    )]


# ─── Step 3: Fix Generation ───

def _generate_fixes(
    conflicts: List[Conflict], services: List[ServiceInfo]
) -> None:
    """
    Generate specific fix recommendations for each conflict.

    Mutates conflicts in-place, setting the `fix` field.
    """
    participants = [s for s in services if s.role in ("arr", "download_client", "media_server")]

    for conflict in conflicts:
        if conflict.conflict_type == "no_shared_mount":
            conflict.fix = _fix_no_shared_mount(conflict, participants)

        elif conflict.conflict_type == "different_host_paths":
            conflict.fix = _fix_different_host_paths(conflict)

        elif conflict.conflict_type == "named_volume_data":
            conflict.fix = _fix_named_volume_data(conflict, participants)

        elif conflict.conflict_type == "path_unreachable":
            conflict.fix = _fix_path_unreachable(conflict, services)

        elif conflict.conflict_type == "puid_pgid_mismatch":
            conflict.fix = _fix_puid_pgid_mismatch(conflict, participants)

        elif conflict.conflict_type == "missing_puid_pgid":
            conflict.fix = _fix_missing_puid_pgid(conflict, participants)

        elif conflict.conflict_type == "root_execution":
            conflict.fix = _fix_root_execution(conflict, participants)

        elif conflict.conflict_type in ("umask_inconsistent", "umask_restrictive"):
            conflict.fix = _fix_umask(conflict)

        elif conflict.conflict_type == "cross_stack_puid_mismatch":
            conflict.fix = _fix_cross_stack_puid(conflict, participants)


def _fix_no_shared_mount(
    conflict: Conflict, participants: List[ServiceInfo]
) -> str:
    """
    Generate fix for the #1 problem: no shared parent mount.

    Recommends the TRaSH Guides unified /data structure:
      /host/data:/data
    with subdirectories:
      /data/torrents  (download client)
      /data/usenet    (usenet client)
      /data/media/tv  (sonarr)
      /data/media/movies (radarr)
    """
    lines = [
        "RECOMMENDED FIX: Use a unified mount structure.",
        "",
        "Create a shared data directory on your host and mount it",
        "identically in all services that handle media files.",
        "",
        "Example (TRaSH Guides pattern):",
        "",
        "  Host structure:",
        "    /host/data/",
        "      torrents/     ← download client saves here",
        "      usenet/       ← usenet client saves here",
        "      media/",
        "        tv/         ← Sonarr manages",
        "        movies/     ← Radarr manages",
        "",
        "  Compose volumes (ALL services get the same mount):",
    ]

    for svc in participants:
        lines.append(f"    {svc.name}:")
        lines.append(f"      - /host/data:/data")

    lines.extend([
        "",
        "  Then configure each app:",
        "    Download client root: /data/torrents (or /data/usenet)",
        "    Sonarr root folder:   /data/media/tv",
        "    Radarr root folder:   /data/media/movies",
        "",
        "This ensures hardlinks and atomic moves work because all",
        "services see the same filesystem through the same mount.",
    ])

    return "\n".join(lines)


def _fix_named_volume_data(
    conflict: Conflict, participants: List[ServiceInfo]
) -> str:
    """
    Generate fix for named volumes used for data paths.

    Named volumes are isolated filesystems. Data must be on bind mounts
    from a shared host directory for hardlinks to work.
    """
    lines = [
        "RECOMMENDED FIX: Replace named volumes with bind mounts.",
        "",
        "Docker named volumes create separate filesystems. Hardlinks and",
        "atomic moves cannot cross filesystem boundaries.",
        "",
        "1. Create a shared data directory on your host:",
        "     mkdir -p /host/data/{torrents,media/tv,media/movies}",
        "",
        "2. Replace named volume declarations with bind mounts:",
        "",
        "   BEFORE (broken):",
    ]

    for svc in participants:
        for vol in svc.volumes:
            if vol.is_named_volume and not _is_config_mount(vol.target):
                lines.append(f"     - {vol.source}:{vol.target}")

    lines.extend([
        "",
        "   AFTER (working):",
        "     - /host/data:/data",
        "",
        "3. Remove the named volume declarations from the `volumes:` section",
        "   at the bottom of your compose file.",
        "",
        "4. Update your *arr app settings to use paths under /data/.",
    ])

    return "\n".join(lines)


def _fix_different_host_paths(conflict: Conflict) -> str:
    """Fix for services mapping same container path to different host paths."""
    lines = [
        f"PROBLEM: Services share container path but map to different host directories.",
        "",
        "Pick ONE host path and use it consistently across all services:",
        "",
    ]

    for svc in conflict.services:
        lines.append(f"  {svc}:")
        lines.append(f"    volumes:")
        lines.append(f"      - /your/chosen/host/path:{conflict.description.split()[-1] if conflict.description else '/data'}")

    return "\n".join(lines)


def _fix_path_unreachable(
    conflict: Conflict, services: List[ServiceInfo]
) -> str:
    """Fix for a service that can't reach the error path."""
    svc_name = conflict.services[0] if conflict.services else "the service"

    lines = [
        f"PROBLEM: {svc_name} has no volume mount that covers the error path.",
        "",
        "You need to add or fix a volume mount so the path is accessible.",
        "",
        "Either:",
        f"  1. Add a volume mount that covers the missing path",
        f"  2. Change your app config to use a path that IS mounted",
        "",
        "Check your compose file's volumes section for this service.",
    ]

    return "\n".join(lines)


def _fix_puid_pgid_mismatch(
    conflict: Conflict, participants: List[ServiceInfo]
) -> str:
    """Fix for services running as different UIDs.

    Identifies the majority UID:GID and tells the user to align all
    services to that value.
    """
    # Find the majority UID across participants
    uid_counts: Dict[Tuple[str, str], int] = {}
    for svc in participants:
        profile = _build_permission_profile(svc)
        if profile.uid:
            key = (profile.uid, profile.gid or profile.uid)
            uid_counts[key] = uid_counts.get(key, 0) + 1

    if not uid_counts:
        return "Set the same PUID and PGID on all media services."

    majority = max(uid_counts, key=uid_counts.get)
    rec_uid, rec_gid = majority

    lines = [
        "RECOMMENDED FIX: Align all services to the same UID:GID.",
        "",
        "All services that share data volumes must run as the same user.",
        f"Your most common identity is UID={rec_uid} GID={rec_gid}.",
        "",
        "Set these environment variables in every media service's compose:",
    ]

    for svc in participants:
        profile = _build_permission_profile(svc)
        current = f"UID={profile.uid or '?'} GID={profile.gid or '?'}"
        if profile.uid == rec_uid and (profile.gid or profile.uid) == rec_gid:
            lines.append(f"  {svc.name}: ✓ already {current}")
        else:
            family = _identify_image_family(svc.image)
            if family and family.uid_env:
                lines.append(f"  {svc.name}: set {family.uid_env}={rec_uid} {family.gid_env}={rec_gid}  (currently {current})")
            elif svc.compose_user:
                lines.append(f"  {svc.name}: set user: \"{rec_uid}:{rec_gid}\"  (currently user: \"{svc.compose_user}\")")
            else:
                lines.append(f"  {svc.name}: set PUID={rec_uid} PGID={rec_gid}  (currently {current})")

    lines.extend([
        "",
        "After changing, fix existing file ownership on your host:",
        f"  sudo chown -R {rec_uid}:{rec_gid} /path/to/your/data",
    ])

    return "\n".join(lines)


def _fix_missing_puid_pgid(
    conflict: Conflict, participants: List[ServiceInfo]
) -> str:
    """Fix for services missing PUID/PGID environment variables.

    Recommends the majority UID from the stack so the user sets a
    consistent value.
    """
    # Determine recommended UID from other services in the stack
    rec_uid, rec_gid = "1000", "1000"
    for svc in participants:
        profile = _build_permission_profile(svc)
        if profile.uid and profile.uid_source != "default":
            rec_uid = profile.uid
            rec_gid = profile.gid or profile.uid
            break

    lines = [
        "RECOMMENDED FIX: Add user identity to services missing PUID/PGID.",
        "",
    ]

    for svc_name in conflict.services:
        svc = next((s for s in participants if s.name == svc_name), None)
        if not svc:
            continue
        family = _identify_image_family(svc.image)
        if not family:
            continue

        lines.append(f"{svc.name} ({family.name} image):")
        lines.append(f"  Without {family.uid_env}/{family.gid_env}, defaults to UID {family.default_uid}")
        lines.append(f"  Add to your compose environment:")
        if family.uid_env:
            lines.append(f"    - {family.uid_env}={rec_uid}")
        if family.gid_env:
            lines.append(f"    - {family.gid_env}={rec_gid}")
        lines.append("")

    return "\n".join(lines)


def _fix_root_execution(
    conflict: Conflict, participants: List[ServiceInfo]
) -> str:
    """Fix for services running as root (UID 0)."""
    # Find a non-root UID from other services
    rec_uid = "1000"
    for svc in participants:
        profile = _build_permission_profile(svc)
        if profile.uid and not profile.is_root and profile.uid_source != "default":
            rec_uid = profile.uid
            break

    lines = [
        "RECOMMENDED FIX: Run these services as a non-root user.",
        "",
        "Running as root (UID 0) causes files to be owned by root.",
        "Other non-root services won't be able to modify those files.",
        "",
    ]

    for svc_name in conflict.services:
        svc = next((s for s in participants if s.name == svc_name), None)
        if not svc:
            continue
        family = _identify_image_family(svc.image)
        if family and family.uid_env:
            lines.append(f"  {svc.name}: change {family.uid_env} from 0 to {rec_uid}")
        else:
            lines.append(f"  {svc.name}: set user: \"{rec_uid}:{rec_uid}\" in compose")

    lines.extend([
        "",
        "After changing, fix existing file ownership:",
        f"  sudo chown -R {rec_uid}:{rec_uid} /path/to/your/data",
    ])

    return "\n".join(lines)


def _fix_umask(conflict: Conflict) -> str:
    """Fix for inconsistent or restrictive UMASK values."""
    lines = [
        "RECOMMENDED FIX: Use UMASK=002 across all services.",
        "",
        "UMASK 002 means: owner=rwx, group=rwx, others=r-x",
        "This is the TRaSH Guides recommended value — it allows",
        "group members full access while keeping others read-only.",
        "",
    ]

    for svc_name in conflict.services:
        lines.append(f"  {svc_name}: set UMASK=002")

    return "\n".join(lines)


def _fix_cross_stack_puid(
    conflict: Conflict, participants: List[ServiceInfo]
) -> str:
    """Fix for cross-stack PUID/PGID mismatches."""
    # Find this stack's UID
    rec_uid, rec_gid = "1000", "1000"
    for svc in participants:
        profile = _build_permission_profile(svc)
        if profile.uid and profile.uid_source != "default":
            rec_uid = profile.uid
            rec_gid = profile.gid or profile.uid
            break

    lines = [
        "RECOMMENDED FIX: All services across your pipeline must share the same UID:GID.",
        "",
        f"This stack uses UID:GID {rec_uid}:{rec_gid}.",
        "The sibling services listed above use different values.",
        "",
        "Update each sibling stack's compose to match:",
        f"  PUID={rec_uid}",
        f"  PGID={rec_gid}",
        "",
        "After changing, fix file ownership on your shared data:",
        f"  sudo chown -R {rec_uid}:{rec_gid} /path/to/your/data",
    ]

    return "\n".join(lines)


# ─── Step 4: Summary ───

def _build_fix_summary(
    conflicts: List[Conflict],
    services: List[ServiceInfo],
    error_service: Optional[str],
    pipeline_context: Optional[Dict] = None,
) -> Optional[str]:
    """Build an overall human-readable summary."""
    if not conflicts:
        participant_count = sum(
            1 for s in services
            if s.role in ("arr", "download_client", "media_server")
        )

        # Pipeline-aware summary: if we have pipeline context, use it
        # instead of the limited single-stack perspective
        if pipeline_context and pipeline_context.get("total_media", 0) > 1:
            total = pipeline_context["total_media"]
            shared = pipeline_context.get("shared_mount", False)
            mount_root = pipeline_context.get("mount_root", "")
            if shared and mount_root:
                return (
                    f"No path conflicts detected. This stack is part of a "
                    f"{total}-service media pipeline. All services share "
                    f"{mount_root} — hardlinks and atomic moves will work."
                )
            else:
                return (
                    f"No path conflicts in this stack. It's part of a "
                    f"{total}-service media pipeline across your directory."
                )

        if participant_count >= 2:
            return (
                "No path conflicts detected. Your volume mounts look correctly "
                "structured for hardlinks and atomic moves."
            )
        elif participant_count == 1:
            return (
                "Only one media-related service found. Path conflicts typically "
                "occur between *arr apps and download clients. Analysis is limited "
                "with a single service."
            )
        else:
            return (
                "No *arr apps, download clients, or media servers detected in "
                "this stack. MapArr is designed for media stack path analysis."
            )

    critical = [c for c in conflicts if c.severity == "critical"]
    high = [c for c in conflicts if c.severity == "high"]

    parts = []
    if critical:
        parts.append(f"{len(critical)} critical issue{'s' if len(critical) > 1 else ''}")
    if high:
        parts.append(f"{len(high)} high-severity issue{'s' if len(high) > 1 else ''}")

    summary = f"Found {', '.join(parts)}. " if parts else ""

    if any(c.conflict_type == "no_shared_mount" for c in conflicts):
        summary += (
            "Your services use separate mount trees, which prevents hardlinks "
            "and atomic moves. See the fix recommendation for the unified mount pattern."
        )
    elif any(c.conflict_type == "path_unreachable" for c in conflicts):
        svc = error_service or "your service"
        summary += (
            f"The error path is not reachable by {svc}. "
            f"Check the volume mounts in your compose file."
        )

    return summary or None


# ─── RPM Calculator ───
# Computes Remote Path Mapping entries for each (download_client, arr_app) pair.
# RPM is the *arr apps' built-in bridge for container-path mismatches.
# When qBittorrent reports "/downloads/tv/show.mkv" but Sonarr sees that same
# data at "/data/downloads/tv/show.mkv", an RPM entry translates the path.


def _find_host_overlap(dc_host: str, arr_host: str) -> Optional[str]:
    """
    Check if two host-side mount paths overlap and return the relative offset.

    RPM needs to know the path difference between where the download client
    stores data on the host and where the arr app can see it.

    Returns:
        The relative path offset if paths overlap, None otherwise.

    Examples:
        _find_host_overlap("/mnt/nas/downloads", "/mnt/nas")
          → "/downloads"  (DC is deeper, offset from arr's root)
        _find_host_overlap("/mnt/nas", "/mnt/nas/downloads")
          → ""  (arr is deeper — DC path is a parent of arr's mount)
        _find_host_overlap("/host/downloads", "/srv/media")
          → None  (no overlap, RPM impossible)
    """
    dc = dc_host.rstrip("/")
    arr = arr_host.rstrip("/")

    if dc == arr:
        # Same host path — RPM only needed if container paths differ
        return ""
    elif dc.startswith(arr + "/"):
        # DC path is deeper: /mnt/nas/downloads under /mnt/nas
        return dc[len(arr):]
    elif arr.startswith(dc + "/"):
        # Arr path is deeper: /mnt/nas/downloads under /mnt/nas
        # DC data is accessible at arr's container root minus the extra depth
        return ""
    else:
        return None


def _calculate_rpm_mappings(
    services: List[ServiceInfo],
    pipeline_context: Optional[dict],
    stack_path: str = "",
) -> List[dict]:
    """
    Compute Remote Path Mapping entries for all (download_client, arr_app) pairs.

    Gathers services from both the current stack and pipeline siblings, then
    calculates the RPM triple (Host, Remote Path, Local Path) for each pair
    based on host-side mount overlap.

    Each mapping dict contains:
        arr_service, arr_stack: Which arr app needs this RPM entry
        dc_service, dc_stack: Which download client this maps
        host: The RPM "Host" field (DC container name)
        remote_path: The RPM "Remote Path" (DC container path)
        local_path: The RPM "Local Path" (arr container path that sees the same data)
        dc_host_path, arr_host_path: Host mount sources for user context
        possible: Whether host paths actually overlap
        reason: Human-readable explanation
    """
    if not pipeline_context:
        return []

    # Gather all arr services and download clients from stack + pipeline
    arr_entries = []   # [{name, stack, volume_mounts: [{source, target}]}]
    dc_entries = []

    # Config/utility mount targets to filter out (same as discovery._CONFIG_TARGETS)
    _config_targets = {
        "/config", "/app", "/etc", "/var", "/tmp", "/run", "/dev",
        "/backup", "/backups", "/restore", "/log", "/logs",
        "/cache", "/certs", "/ssl", "/scripts",
    }

    # From this stack's services (full VolumeMount objects)
    current_stack_name = os.path.basename(stack_path) if stack_path else ""
    for svc in services:
        data_mounts = []
        for v in svc.volumes:
            if not v.is_bind_mount or not v.source:
                continue
            target_clean = v.target.rstrip("/")
            if any(target_clean == c or target_clean.startswith(c + "/")
                   for c in _config_targets):
                continue
            data_mounts.append({
                "source": v.source.replace("\\", "/").rstrip("/"),
                "target": target_clean,
            })
        entry = {
            "name": svc.name,
            "stack": current_stack_name,
            "volume_mounts": data_mounts,
        }
        if svc.role == "arr":
            arr_entries.append(entry)
        elif svc.role == "download_client":
            dc_entries.append(entry)

    # From pipeline siblings (already dict format with volume_mounts)
    for sib in pipeline_context.get("sibling_services", []):
        role = sib.get("role", "")
        mounts = sib.get("volume_mounts", [])
        entry = {
            "name": sib.get("service_name", ""),
            "stack": sib.get("stack_name", ""),
            "volume_mounts": mounts,
        }
        if role == "arr":
            arr_entries.append(entry)
        elif role == "download_client":
            dc_entries.append(entry)

    if not arr_entries or not dc_entries:
        return []

    # Only compute mappings where at least one side belongs to the current stack.
    # Without this filter, the cross-product explodes across the full pipeline
    # (e.g. 198 entries when only ~4 are relevant to the stack being analyzed).
    mappings = []

    for dc in dc_entries:
        dc_data_mounts = dc["volume_mounts"]
        if not dc_data_mounts:
            continue

        for arr in arr_entries:
            # Skip pairs where neither service is in the current stack
            if (dc["stack"] != current_stack_name and
                    arr["stack"] != current_stack_name):
                continue
            arr_data_mounts = arr["volume_mounts"]
            if not arr_data_mounts:
                continue

            # Find the best-matching mount pair (host path overlap)
            best_mapping = None

            for dc_mount in dc_data_mounts:
                dc_host = dc_mount["source"]
                dc_target = dc_mount["target"]

                for arr_mount in arr_data_mounts:
                    arr_host = arr_mount["source"]
                    arr_target = arr_mount["target"]

                    offset = _find_host_overlap(dc_host, arr_host)
                    if offset is not None:
                        # RPM possible — compute the paths
                        # Remote = DC container path (what DC reports to arr)
                        remote = dc_target.rstrip("/") + "/"
                        # Local = arr container path + offset (where arr sees DC's data)
                        if offset:
                            local = arr_target.rstrip("/") + offset + "/"
                        else:
                            # Same host path or arr is deeper — figure out the right mapping
                            if dc_host == arr_host.rstrip("/"):
                                # Identical host paths, different container paths
                                local = arr_target.rstrip("/") + "/"
                            elif arr_host.rstrip("/").startswith(dc_host.rstrip("/") + "/"):
                                # Arr is deeper: arr=/mnt/nas/downloads, dc=/mnt/nas
                                # DC's /downloads = arr's / (root of its mount)
                                arr_extra = arr_host.rstrip("/")[len(dc_host.rstrip("/")):]
                                # DC container subpath that maps to arr container root
                                remote = dc_target.rstrip("/") + arr_extra + "/"
                                local = arr_target.rstrip("/") + "/"
                            else:
                                local = arr_target.rstrip("/") + "/"

                        best_mapping = {
                            "arr_service": arr["name"],
                            "arr_stack": arr["stack"],
                            "dc_service": dc["name"],
                            "dc_stack": dc["stack"],
                            "host": dc["name"],
                            "remote_path": remote,
                            "local_path": local,
                            "dc_host_path": dc_host,
                            "arr_host_path": arr_host,
                            "possible": True,
                            "reason": f"Host paths overlap — {dc_host} is accessible from {arr_host}",
                        }
                        break  # Use first matching mount pair

                if best_mapping:
                    break

            if not best_mapping:
                # No overlap found — RPM impossible for this pair
                dc_hosts = [m["source"] for m in dc_data_mounts]
                arr_hosts = [m["source"] for m in arr_data_mounts]
                best_mapping = {
                    "arr_service": arr["name"],
                    "arr_stack": arr["stack"],
                    "dc_service": dc["name"],
                    "dc_stack": dc["stack"],
                    "host": dc["name"],
                    "remote_path": "",
                    "local_path": "",
                    "dc_host_path": dc_hosts[0] if dc_hosts else "",
                    "arr_host_path": arr_hosts[0] if arr_hosts else "",
                    "possible": False,
                    "reason": (
                        f"Host paths don't overlap — {dc['name']} uses "
                        f"{', '.join(dc_hosts)} but {arr['name']} uses "
                        f"{', '.join(arr_hosts)}. Mount restructuring required."
                    ),
                }

            mappings.append(best_mapping)

    return mappings


# ─── Step 5: Solution YAML ───

# Role → recommended container path mapping.
# Uses the TRaSH Guides unified /data structure.
_ROLE_CONTAINER_PATHS = {
    "sonarr": "/data/media/tv",
    "radarr": "/data/media/movies",
    "lidarr": "/data/media/music",
    "readarr": "/data/media/books",
    "whisparr": "/data/media/xxx",
    "bazarr": "/data/media",
    "prowlarr": None,  # Prowlarr doesn't need data mounts
    "qbittorrent": "/data/torrents",
    "transmission": "/data/torrents",
    "deluge": "/data/torrents",
    "rtorrent": "/data/torrents",
    "sabnzbd": "/data/usenet",
    "nzbget": "/data/usenet",
    "jdownloader": "/data/downloads",
    "aria2": "/data/torrents",
    "flood": "/data/torrents",
    "rdtclient": "/data/torrents",
    "plex": "/data/media",
    "jellyfin": "/data/media",
    "emby": "/data/media",
}


def _generate_solution_yaml(
    conflicts: List[Conflict], services: List[ServiceInfo],
    host_root_override: Optional[str] = None,
) -> Tuple[Optional[str], List[int]]:
    """
    Generate full copy-pasteable YAML showing the complete services section
    with corrected volume configuration.

    Includes ALL services (not just affected ones) so the output can be
    pasted directly into docker-compose.yml. Affected services get
    corrected volumes; non-affected services keep their existing mounts.

    Returns:
        (yaml_string, changed_lines) — the YAML and 1-indexed line numbers
        of lines that differ from the original configuration.
    """
    if not conflicts:
        return None, []

    # Collect affected services
    affected_names = set()
    for conflict in conflicts:
        affected_names.update(conflict.services)

    if not affected_names:
        return None, []

    # Use pipeline majority root when available, otherwise detect from current mounts
    host_data_root = host_root_override or _detect_host_data_root(services)

    # When targeting the pipeline majority root, include ALL media services
    # so the generated YAML achieves full pipeline alignment.
    if host_root_override:
        for svc in services:
            if svc.role in ("arr", "download_client", "media_server"):
                affected_names.add(svc.name)

    lines = [
        "# Full corrected volume configuration (TRaSH Guides pattern)",
        "# All media services share one host directory mounted as /data",
        "#",
        "# Host setup required:",
        f"#   mkdir -p {host_data_root}/{{media/{{tv,movies,music}},torrents,usenet}}",
        "#",
    ]
    if host_data_root == "/host/data":
        lines.append("# Replace /host/data with your actual host path.")
        lines.append("#")
    lines.append("")
    lines.append("services:")

    changed_lines: List[int] = []

    for svc in services:
        lines.append(f"  {svc.name}:")

        if svc.name in affected_names:
            # Affected service — rewrite volumes with unified data mount
            lines.append("    volumes:")

            # Keep existing config mounts
            for vol in svc.volumes:
                if _is_config_mount(vol.target) or vol.is_named_volume:
                    lines.append(f"      - {vol.raw}")

            # Add the unified data mount — this is a CHANGED line
            container_path = _get_recommended_container_path(svc)
            if container_path:
                lines.append(f"      - {host_data_root}:{container_path}")
                changed_lines.append(len(lines))  # 1-indexed
        else:
            # Non-affected service — keep existing volumes unchanged
            if svc.volumes:
                lines.append("    volumes:")
                for vol in svc.volumes:
                    lines.append(f"      - {vol.raw}")
            else:
                lines.append("    # (no volumes)")

        lines.append("")

    return "\n".join(lines), changed_lines


def _detect_host_data_root(services: List[ServiceInfo]) -> str:
    """
    Try to detect a reasonable host data root from existing volume mounts.

    Looks for common parent directories among data (non-config) bind mounts.
    Falls back to /host/data if nothing sensible can be detected.
    """
    data_sources = []
    for svc in services:
        for vol in svc.volumes:
            if vol.is_named_volume or _is_config_mount(vol.target):
                continue
            if vol.source.startswith("/") or (len(vol.source) >= 2 and vol.source[1] == ":"):
                data_sources.append(vol.source.replace("\\", "/"))

    if not data_sources:
        return "/host/data"

    # Find the longest common path prefix
    if len(data_sources) == 1:
        # Single source — use its parent
        parts = data_sources[0].rstrip("/").rsplit("/", 1)
        return parts[0] if len(parts) > 1 else data_sources[0]

    # Multiple sources — find common prefix
    # os.path.commonpath() raises ValueError when paths have different drives
    # (e.g. /mnt/media vs //MediaNAS/Downloads, or C:\ vs D:\).
    # Fall back to generic /data root when paths can't be compared.
    try:
        common = os.path.commonpath([s.replace("\\", "/") for s in data_sources])
        common = common.replace("\\", "/")
    except ValueError:
        return "/data"

    # Don't return something too short like "/" or "C:/"
    if common in ("", "/") or (len(common) <= 3 and common[1:2] == ":"):
        return "/host/data"

    return common


def _get_recommended_container_path(service: ServiceInfo) -> Optional[str]:
    """Get the recommended container data path for a service."""
    name_lower = service.name.lower()

    # Check the lookup table by service name
    for key, path in _ROLE_CONTAINER_PATHS.items():
        if key in name_lower:
            return path

    # Fallback by role
    if service.role == "arr":
        return "/data/media"
    elif service.role == "download_client":
        return "/data/torrents"
    elif service.role == "media_server":
        return "/data/media"

    return "/data"


# ─── Step 6: Mount Intelligence ───

def _analyze_mounts(
    services: List[ServiceInfo],
) -> Tuple[List[MountClassification], List[str]]:
    """
    Classify all host paths used by services and check hardlink compatibility.

    Returns:
        (classifications, warnings) — unique mount classifications and
        any hardlink-relevant warnings (remote FS, mixed mount types, etc.)
    """
    # Collect unique host paths from bind mounts (skip config mounts)
    seen_paths: set = set()
    classifications: List[MountClassification] = []

    for svc in services:
        for vol in svc.volumes:
            if not vol.is_bind_mount or not vol.source:
                continue
            if _is_config_mount(vol.target):
                continue
            if vol.source in seen_paths:
                continue
            seen_paths.add(vol.source)
            classifications.append(classify_path(vol.source))

    # Check hardlink compatibility across all classified paths
    warnings = check_hardlink_compatibility(classifications)

    return classifications, warnings


def _add_mount_conflicts(
    conflicts: List[Conflict],
    classifications: List[MountClassification],
    services: List[ServiceInfo],
) -> None:
    """
    Add mount-related conflicts when remote filesystems are detected
    on hardlink-participant services.
    """
    participants = [s for s in services if s.role in ("arr", "download_client", "media_server")]
    if not participants:
        return

    # Collect host paths used by participants (data volumes only)
    participant_sources: set = set()
    for svc in participants:
        for vol in svc.volumes:
            if vol.is_bind_mount and vol.source and not _is_config_mount(vol.target):
                participant_sources.add(vol.source)

    # Check if any participant path is on a remote filesystem
    remote_mounts = [
        mc for mc in classifications
        if mc.is_remote and mc.path in participant_sources
    ]

    if remote_mounts:
        affected_services = set()
        for svc in participants:
            for vol in svc.volumes:
                if vol.source in {m.path for m in remote_mounts}:
                    affected_services.add(svc.name)

        remote_detail = "\n".join(
            f"  {mc.path}: {mc.detail}" for mc in remote_mounts
        )

        conflicts.append(Conflict(
            conflict_type="remote_filesystem",
            severity="high",
            services=sorted(affected_services),
            description=(
                "Remote filesystem detected on media paths. "
                "Hardlinks do not work over network shares (NFS/CIFS/SMB)."
            ),
            detail=remote_detail,
            fix=(
                "Move your media data to local storage, or ensure ALL "
                "services access the same single NFS export. Hardlinks "
                "only work within one filesystem — not across network "
                "shares and local storage."
            ),
        ))


# ─── Step 7: Patch Original YAML ───

def _patch_original_yaml(
    raw_content: str,
    conflicts: List[Conflict],
    services: List[ServiceInfo],
    host_root_override: Optional[str] = None,
) -> Tuple[Optional[str], List[int]]:
    """
    Patch the user's original compose YAML, replacing ONLY affected
    volume mounts with corrected ones. Preserves all other content
    (comments, env vars, ports, networks, labels, etc).

    Strategy: line-based parsing. Find each affected service's
    volumes: block, replace data volume lines, keep config volume lines.

    Returns:
        (patched_yaml, changed_lines) — the patched YAML and 1-indexed
        line numbers of lines that were inserted/replaced.
        Returns (None, []) if patching fails or produces invalid output.
    """
    affected_names = set()
    for c in conflicts:
        affected_names.update(c.services)

    if not affected_names:
        return None, []

    host_data_root = host_root_override or _detect_host_data_root(services)

    # When patching to match the pipeline majority root, expand to ALL media
    # services — not just those named in the specific conflict. Otherwise we fix
    # sonarr/radarr to /srv/data but leave qbittorrent at /home/user/downloads,
    # creating a new conflict immediately after apply.
    if host_root_override:
        for svc in services:
            if svc.role in ("arr", "download_client", "media_server"):
                affected_names.add(svc.name)
    svc_lookup = {s.name: s for s in services}

    lines = raw_content.splitlines()
    result = []
    changed_lines: List[int] = []
    i = 0

    # Find the services: top-level key first
    services_line_idx = None
    services_indent = None
    for idx, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("services:") and not line[0:1].isspace():
            services_line_idx = idx
            services_indent = 0
            break

    if services_line_idx is None:
        return None, []

    # Process lines
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.lstrip()
        indent = len(line) - len(stripped)

        # Are we inside the services: block? Service names are at indent 2
        if (i > services_line_idx and indent == 2
                and stripped.endswith(":") and not stripped.startswith("#")
                and not stripped.startswith("-")):

            svc_name = stripped.rstrip(":").strip().strip("'\"")

            if svc_name in affected_names:
                result.append(line)
                i += 1

                # Scan this service's block for volumes:
                while i < len(lines):
                    inner = lines[i]
                    inner_stripped = inner.lstrip()
                    inner_indent = len(inner) - len(inner_stripped)

                    # Left of or at service-level indent = new service or top-level key
                    if inner_stripped and not inner_stripped.startswith("#") and inner_indent <= 2:
                        break

                    if inner_stripped.rstrip(":").strip() == "volumes":
                        result.append(inner)
                        vol_key_indent = inner_indent
                        i += 1

                        # Skip existing volume entries
                        while i < len(lines):
                            vol_line = lines[i]
                            vol_stripped = vol_line.lstrip()
                            vol_line_indent = len(vol_line) - len(vol_stripped)

                            # Not a volume entry — we've left the volumes block
                            if not vol_stripped.startswith("-") or vol_line_indent <= vol_key_indent:
                                break

                            # Parse the volume mount to decide keep or replace
                            mount_str = vol_stripped.lstrip("- ").strip().strip("'\"")
                            parts = mount_str.split(":")
                            target = ""
                            if len(parts) >= 3 and len(parts[0]) == 1 and parts[0].isalpha():
                                target = parts[2]  # Windows C:\path:container
                            elif len(parts) >= 2:
                                target = parts[1]
                            target = target.rstrip("/").split(":")[0]  # strip :ro

                            if _is_config_mount(target):
                                result.append(vol_line)  # Keep config mounts
                            # else: skip data mounts (will be replaced below)
                            i += 1

                        # Insert corrected data mount
                        svc_info = svc_lookup.get(svc_name)
                        if svc_info:
                            container_path = _get_recommended_container_path(svc_info)
                            if container_path:
                                mount_indent = " " * (vol_key_indent + 2)
                                result.append(f"{mount_indent}- {host_data_root}:{container_path}")
                                changed_lines.append(len(result))  # 1-indexed
                        continue

                    result.append(inner)
                    i += 1
                continue

        result.append(line)
        i += 1

    patched = "\n".join(result)

    # Validate: ensure the patched YAML is still parseable
    try:
        yaml.safe_load(patched)
    except Exception:
        logger.warning("Patched YAML failed validation, discarding")
        return None, []

    return patched, changed_lines
