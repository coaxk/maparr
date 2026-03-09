"""
mounts.py — Mount type classification for MapArr.

Classifies host paths from compose volume declarations to detect:
  - Remote filesystems (NFS, CIFS/SMB) where hardlinks don't work
  - Windows paths and drive letters
  - WSL2 translated paths (/mnt/c → C:\)
  - Named Docker volumes (not host paths)
  - Relative paths (./config)
  - Standard local paths

This is pattern-based analysis — no subprocess calls, no filesystem
probing, no Docker inspect. Works everywhere, runs instantly, and
correctly identifies the cases that matter:

  1. Remote mounts (NFS/CIFS) → hardlinks impossible
  2. Cross-mount patterns → hardlinks require same filesystem
  3. WSL2 quirks → path translation awareness

The user never sees "mount type: ext4." They see "hardlinks won't work
because your data is on a network share." That's the value.
"""

import os
import re
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class MountClassification:
    """Classification result for a single host path."""
    path: str
    mount_type: str         # "nfs", "cifs", "windows", "wsl2", "local", "named_volume", "relative"
    is_remote: bool         # True for NFS, CIFS, SMB
    hardlink_compatible: bool  # False for remote FS, True for local
    detail: Optional[str] = None  # e.g., "NFS server: 192.168.1.10"
    warning: Optional[str] = None  # e.g., "Hardlinks don't work over NFS"

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "mount_type": self.mount_type,
            "is_remote": self.is_remote,
            "hardlink_compatible": self.hardlink_compatible,
            "detail": self.detail,
            "warning": self.warning,
        }


def classify_path(path: str) -> MountClassification:
    """
    Classify a volume source path by its mount type.

    Checks patterns in priority order — most specific first.
    """
    if not path:
        return MountClassification(
            path=path, mount_type="unknown", is_remote=False,
            hardlink_compatible=True,
        )

    # Named Docker volume (no slashes, no drive letter, no dots)
    if _is_named_volume(path):
        return MountClassification(
            path=path,
            mount_type="named_volume",
            is_remote=False,
            hardlink_compatible=False,
            detail="Docker named volume (not a host path)",
            warning="Named volumes are managed by Docker. Hardlinks between named volumes and bind mounts don't work.",
        )

    # UNC path: \\server\share or //server/share
    unc = _check_unc(path)
    if unc:
        return unc

    # NFS-style: server:/export/path or nfs://server/path
    nfs = _check_nfs(path)
    if nfs:
        return nfs

    # Windows drive letter: C:\path or C:/path
    win = _check_windows(path)
    if win:
        return win

    # WSL2 translated: /mnt/c/Users/... or /mnt/d/...
    wsl = _check_wsl2(path)
    if wsl:
        return wsl

    # Relative path: ./config, ../data
    if path.startswith("./") or path.startswith("../"):
        return MountClassification(
            path=path,
            mount_type="relative",
            is_remote=False,
            hardlink_compatible=True,
            detail="Relative to compose file directory",
        )

    # Standard absolute path: /data, /mnt/media, /home/user/docker
    return MountClassification(
        path=path,
        mount_type="local",
        is_remote=False,
        hardlink_compatible=True,
    )


def classify_volume_sources(
    sources: List[str],
) -> List[MountClassification]:
    """Classify a list of volume source paths."""
    return [classify_path(s) for s in sources]


def check_hardlink_compatibility(
    classifications: List[MountClassification],
) -> List[str]:
    """
    Check if a set of classified paths are hardlink-compatible with each other.

    Returns a list of warning strings. Empty list = all compatible.
    """
    warnings = []

    remote = [c for c in classifications if c.is_remote]
    local = [c for c in classifications if not c.is_remote and c.mount_type != "named_volume"]
    named = [c for c in classifications if c.mount_type == "named_volume"]

    if remote and local:
        remote_paths = ", ".join(c.path for c in remote)
        warnings.append(
            f"Mix of remote ({remote_paths}) and local mounts. "
            f"Hardlinks cannot cross filesystem boundaries."
        )

    if remote:
        for c in remote:
            if c.mount_type == "cifs":
                warnings.append(
                    f"CIFS/SMB mount ({c.path}): Hardlinks are not supported "
                    f"on SMB/CIFS shares. Consider using a local mount or NFS."
                )
            elif c.mount_type == "nfs":
                warnings.append(
                    f"NFS mount ({c.path}): Hardlinks work within a single NFS export "
                    f"but not across different exports or between NFS and local storage."
                )

    if named and (local or remote):
        warnings.append(
            "Mix of named volumes and bind mounts. "
            "Hardlinks don't work between named volumes and bind-mounted paths."
        )

    return warnings


# ─── Pattern Matchers ───

def _is_named_volume(path: str) -> bool:
    """Check if path is a Docker named volume (not a filesystem path)."""
    # Named volumes: simple names like "mydata", "pg_data"
    # NOT paths: /data, ./config, C:\, //server
    if "/" in path or "\\" in path:
        return False
    if path.startswith("."):
        return False
    if len(path) >= 2 and path[1] == ":":
        return False
    # Must look like a name (alphanumeric + underscore + hyphen)
    return bool(re.match(r'^[a-zA-Z][a-zA-Z0-9_.-]*$', path))


def _check_unc(path: str) -> Optional[MountClassification]:
    """Check for UNC/SMB paths: \\\\server\\share or //server/share."""
    match = re.match(r'^[/\\]{2}([^/\\]+)[/\\](.+)', path)
    if match:
        server = match.group(1)
        return MountClassification(
            path=path,
            mount_type="cifs",
            is_remote=True,
            hardlink_compatible=False,
            detail=f"SMB/CIFS share on {server}",
            warning=f"Network share ({server}): Hardlinks are not supported on CIFS/SMB.",
        )
    return None


def _check_nfs(path: str) -> Optional[MountClassification]:
    """Check for NFS-style paths: server:/export or nfs://server/path."""
    # nfs:// URL
    nfs_url = re.match(r'^nfs://([^/]+)(/.+)', path, re.IGNORECASE)
    if nfs_url:
        server = nfs_url.group(1)
        return MountClassification(
            path=path,
            mount_type="nfs",
            is_remote=True,
            hardlink_compatible=False,
            detail=f"NFS mount from {server}",
            warning=f"NFS mount ({server}): Hardlinks only work within a single NFS export.",
        )

    # server:/path (but not C:\path which has a single letter before :)
    nfs_colon = re.match(r'^([a-zA-Z0-9._-]{2,}):(/.+)', path)
    if nfs_colon:
        server = nfs_colon.group(1)
        return MountClassification(
            path=path,
            mount_type="nfs",
            is_remote=True,
            hardlink_compatible=False,
            detail=f"NFS mount from {server}",
            warning=f"NFS mount ({server}): Hardlinks only work within a single NFS export.",
        )

    return None


def _check_windows(path: str) -> Optional[MountClassification]:
    """Check for Windows drive letter paths: C:\\path or C:/path."""
    if len(path) >= 2 and path[0].isalpha() and path[1] == ":":
        return MountClassification(
            path=path,
            mount_type="windows",
            is_remote=False,
            hardlink_compatible=True,
            detail=f"Windows local path (drive {path[0].upper()}:)",
        )
    return None


def _check_wsl2(path: str) -> Optional[MountClassification]:
    """Check for WSL2 translated paths: /mnt/c/Users/..."""
    match = re.match(r'^/mnt/([c-zC-Z])(/.+)$', path)
    if match:
        drive = match.group(1).upper()
        return MountClassification(
            path=path,
            mount_type="wsl2",
            is_remote=False,
            hardlink_compatible=True,
            detail=f"WSL2 mount of Windows drive {drive}:",
            warning=(
                f"WSL2 mount ({drive}:): Performance may be slower than native Linux paths. "
                f"Consider storing Docker data under /home or /opt for better performance."
            ) if path.startswith("/mnt/") else None,
        )
    return None
