"""
analyzer.py — The core analysis engine for MapArr v1.0.

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
from typing import Any, Dict, List, Optional, Tuple

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

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "image": self.image,
            "role": self.role,
            "volumes": [v.to_dict() for v in self.volumes],
            "data_paths": self.data_paths,
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

    def to_dict(self) -> dict:
        return {
            "type": self.conflict_type,
            "severity": self.severity,
            "services": self.services,
            "description": self.description,
            "detail": self.detail,
            "fix": self.fix,
        }


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

    def to_dict(self) -> dict:
        # Determine status: conflicts > cross-stack > incomplete > healthy
        if self.conflicts:
            status = "conflicts_found"
        elif self.incomplete_stack and self.cross_stack:
            cs = self.cross_stack
            if cs.get("missing_roles_filled") and cs.get("shared_mount"):
                status = "healthy_cross_stack"
            elif cs.get("conflicts"):
                status = "cross_stack_conflict"
            elif cs.get("missing_roles_filled"):
                # Siblings found but can't confirm shared mount (no data volumes)
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

    Returns:
        AnalysisResult with services, conflicts, and fix recommendations.
    """
    warnings = resolved_compose.get("_warnings", [])
    steps: List[dict] = []

    # Step 1: Resolve compose file (already done by caller, log it)
    steps.append({"icon": "ok", "text": f"Resolved {os.path.basename(compose_file)} via {resolution_method}"})

    # Step 2: Extract and classify services
    services = _extract_services(resolved_compose)
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
    conflicts = _detect_conflicts(services, error_service, error_path)
    if conflicts:
        steps.append({"icon": "warn", "text": f"Detected {len(conflicts)} path conflict{'s' if len(conflicts) != 1 else ''}"})
    else:
        steps.append({"icon": "ok", "text": "No path conflicts detected"})

    # Step 5: Mount intelligence
    mount_classifications, mount_warnings = _analyze_mounts(services)
    mount_info = [mc.to_dict() for mc in mount_classifications]
    if mount_classifications:
        steps.append({"icon": "ok", "text": f"Classified {len(mount_classifications)} host mount{'s' if len(mount_classifications) != 1 else ''}"})
    if mount_warnings:
        steps.append({"icon": "warn", "text": f"{len(mount_warnings)} filesystem warning{'s' if len(mount_warnings) != 1 else ''}"})

    # Promote remote-FS warnings to conflicts
    _add_mount_conflicts(conflicts, mount_classifications, services)

    # Step 6: Generate fixes
    _generate_fixes(conflicts, services)
    fix_summary = _build_fix_summary(conflicts, services, error_service)
    solution_yaml, solution_changed_lines = _generate_solution_yaml(conflicts, services)
    if solution_yaml:
        steps.append({"icon": "ok", "text": "Generated fix recommendation"})

    # Step 7: Generate corrected version of user's original compose
    original_corrected_yaml = None
    original_changed_lines: List[int] = []
    if solution_yaml and raw_compose_content:
        original_corrected_yaml, original_changed_lines = _patch_original_yaml(
            raw_compose_content, conflicts, services
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
            missing.append("*arr app")
        if not has_dl:
            missing.append("download client")
        steps.append({"icon": "warn", "text": f"Incomplete media stack — no {' or '.join(missing)} detected"})

    # Cross-stack analysis: scan sibling directories when stack is incomplete
    cross_stack_result = None
    if incomplete_stack and scan_dir:
        steps.append({"icon": "run", "text": "Scanning sibling stacks for complementary services..."})
        try:
            # Lazy import to avoid circular dependency (cross_stack imports from analyzer)
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
    else:
        source = parts[0]
        target = parts[1]
        read_only = len(parts) > 2 and parts[2].strip().lower() == "ro"

    is_named = not (
        source.startswith("/")
        or source.startswith("./")
        or source.startswith("../")
        or source.startswith("~")
        or (len(source) >= 2 and source[1] == ":")  # Windows drive
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
) -> List[Conflict]:
    """
    Detect path mapping conflicts.

    Checks:
    1. Hardlink-participant services without a shared parent mount
    2. Same container path backed by different host paths across services
    3. Error path unreachable by the error service
    """
    conflicts: List[Conflict] = []

    # Get hardlink participants from this stack
    participants = [s for s in services if s.role in ("arr", "download_client", "media_server")]

    if len(participants) >= 2:
        # Check 1: No shared parent mount (the #1 *arr problem)
        conflicts.extend(_check_shared_mount(participants))

        # Check 2: Inconsistent host path mapping
        conflicts.extend(_check_host_path_consistency(participants))

    # Check 3: Error path unreachable
    if error_service and error_path:
        conflicts.extend(
            _check_error_path_reachable(services, error_service, error_path)
        )

    # Deduplicate and sort by severity
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    conflicts.sort(key=lambda c: severity_order.get(c.severity, 99))

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
) -> List[Conflict]:
    """
    Check if the path from the error message is reachable by the service.

    If the user's error says "Sonarr can't find /data/downloads/file.mkv",
    check whether Sonarr has a volume mount that covers /data/downloads.
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

        conflicts.append(Conflict(
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
        ))

    return conflicts


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

        elif conflict.conflict_type == "path_unreachable":
            conflict.fix = _fix_path_unreachable(conflict, services)


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


# ─── Step 4: Summary ───

def _build_fix_summary(
    conflicts: List[Conflict],
    services: List[ServiceInfo],
    error_service: Optional[str],
) -> Optional[str]:
    """Build an overall human-readable summary."""
    if not conflicts:
        participant_count = sum(
            1 for s in services
            if s.role in ("arr", "download_client", "media_server")
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
    "plex": "/data/media",
    "jellyfin": "/data/media",
    "emby": "/data/media",
}


def _generate_solution_yaml(
    conflicts: List[Conflict], services: List[ServiceInfo]
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

    # Try to detect the host data root from existing mounts
    host_data_root = _detect_host_data_root(services)

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
    common = os.path.commonpath([s.replace("\\", "/") for s in data_sources])
    common = common.replace("\\", "/")

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

    host_data_root = _detect_host_data_root(services)
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
