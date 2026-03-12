#!/usr/bin/env python3
"""
seed_test_stacks_v2.py — Generate all 42 MapArr test stacks for manual testing.

Creates a comprehensive test matrix covering all 20 conflict types across 4 categories:
  A = Path conflicts, B = Permissions, C = Infrastructure, D = Observations
Plus multi-category combos, image family variants, edge cases, clusters, roots, and paste scenarios.

Directory structure:
  test-stacks/
  ├── single/           ← Each subfolder is a stack to load
  ├── clusters/         ← Each subfolder is a root to load (scans children)
  ├── roots/            ← Load THIS folder directly
  ├── paste/            ← Each subfolder is a stack + paste _PASTE.txt
  └── _INDEX.txt

Usage:
  python tools/seed_test_stacks_v2.py
"""

import os
import shutil
from pathlib import Path

ROOT = Path(__file__).parent.parent / "test-stacks"


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════

def write_stack(path, compose_content, expect_content, env_content=None,
                paste_content=None, override_content=None):
    """Write a test stack's files."""
    path.mkdir(parents=True, exist_ok=True)
    (path / "docker-compose.yml").write_text(compose_content, encoding="utf-8")
    (path / "_EXPECT.txt").write_text(expect_content, encoding="utf-8")
    if env_content:
        (path / ".env").write_text(env_content, encoding="utf-8")
    if paste_content:
        (path / "_PASTE.txt").write_text(paste_content, encoding="utf-8")
    if override_content:
        (path / "docker-compose.override.yml").write_text(override_content, encoding="utf-8")


def write_cluster_service(cluster_path, service_name, compose_content, expect_on_cluster=False):
    """Write a single-service compose file inside a cluster subfolder."""
    svc_path = cluster_path / service_name
    svc_path.mkdir(parents=True, exist_ok=True)
    (svc_path / "docker-compose.yml").write_text(compose_content, encoding="utf-8")


# ═══════════════════════════════════════════════════════════════
# single/ — Category A (Path Conflicts)
# ═══════════════════════════════════════════════════════════════

def generate_single_a():
    base = ROOT / "single"

    # A01-separate-mount-trees
    write_stack(
        base / "A01-separate-mount-trees",
        compose_content="""\
version: "3"
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr
    volumes:
      - /mnt/media/tv:/tv
      - /config/sonarr:/config
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent
    volumes:
      - /mnt/downloads:/downloads
      - /config/qbit:/config
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
""",
        expect_content="""\
DESCRIPTION: Sonarr and qBittorrent use completely separate mount trees — no shared root
CONFLICTS: no_shared_mount (high)
HEALTH: red
TABS: Path Fix
APPLY_FIX: yes
RPM_WIZARD: yes
OBSERVATIONS: None
NOTES: The classic broken setup. /mnt/media and /mnt/downloads have no common ancestor mount.
""")

    # A02-different-host-paths
    write_stack(
        base / "A02-different-host-paths",
        compose_content="""\
version: "3"
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr
    volumes:
      - /home/user/tv:/data/media
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
  radarr:
    image: lscr.io/linuxserver/radarr
    volumes:
      - /home/user/movies:/data/media
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent
    volumes:
      - /home/user/downloads:/data/downloads
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
""",
        expect_content="""\
DESCRIPTION: Three services each mount different host subdirs — same container /data but different host paths
CONFLICTS: different_host_paths (medium)
HEALTH: yellow
TABS: Path Fix
APPLY_FIX: yes
RPM_WIZARD: no
OBSERVATIONS: None
NOTES: Container paths look unified (/data) but host paths diverge (/home/user/tv vs /home/user/movies vs /home/user/downloads).
""")

    # A03-named-volumes
    write_stack(
        base / "A03-named-volumes",
        compose_content="""\
version: "3"
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr
    volumes:
      - media_data:/data/media
      - sonarr_config:/config
    environment:
      - PUID=1000
      - PGID=1000
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent
    volumes:
      - downloads_data:/data/downloads
      - qbit_config:/config
    environment:
      - PUID=1000
      - PGID=1000
volumes:
  media_data:
  downloads_data:
  sonarr_config:
  qbit_config:
""",
        expect_content="""\
DESCRIPTION: Named Docker volumes prevent hardlinks — data lives in Docker-managed volumes, not bind mounts
CONFLICTS: named_volume_data (medium)
HEALTH: yellow
TABS: Path Fix
APPLY_FIX: yes
RPM_WIZARD: no
OBSERVATIONS: None
NOTES: Named volumes are isolated — hardlinks between media_data and downloads_data are impossible.
""")

    # A04-unreachable-path
    write_stack(
        base / "A04-unreachable-path",
        compose_content="""\
version: "3"
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr
    volumes:
      - /srv/media:/tv
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent
    volumes:
      - /srv/downloads:/downloads
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
""",
        expect_content="""\
DESCRIPTION: Sonarr mounts to /tv instead of /data — container path not under expected /data tree
CONFLICTS: path_unreachable (high), no_shared_mount (high)
HEALTH: red
TABS: Path Fix
APPLY_FIX: yes
RPM_WIZARD: yes
OBSERVATIONS: None
NOTES: Sonarr uses /tv and qbit uses /downloads — neither uses /data, and they share no common mount.
""")

    # A05-partial-overlap
    write_stack(
        base / "A05-partial-overlap",
        compose_content="""\
version: "3"
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr
    volumes:
      - /srv/data:/data
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
  radarr:
    image: lscr.io/linuxserver/radarr
    volumes:
      - /srv/data:/data
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent
    volumes:
      - /mnt/fast-ssd/downloads:/downloads
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
""",
        expect_content="""\
DESCRIPTION: Sonarr and Radarr share /srv/data but qBittorrent is on a separate SSD mount
CONFLICTS: no_shared_mount (high)
HEALTH: red
TABS: Path Fix
APPLY_FIX: yes
RPM_WIZARD: yes
OBSERVATIONS: None
NOTES: Arr apps share a mount, but the download client is on a completely different device. Hardlinks impossible between them.
""")


# ═══════════════════════════════════════════════════════════════
# single/ — Category B (Permissions)
# ═══════════════════════════════════════════════════════════════

def generate_single_b():
    base = ROOT / "single"

    # B01-puid-mismatch
    write_stack(
        base / "B01-puid-mismatch",
        compose_content="""\
version: "3"
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr
    volumes:
      - /srv/data:/data
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
  radarr:
    image: lscr.io/linuxserver/radarr
    volumes:
      - /srv/data:/data
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent
    volumes:
      - /srv/data:/data
    environment:
      - PUID=911
      - PGID=911
      - TZ=America/New_York
""",
        expect_content="""\
DESCRIPTION: qBittorrent runs as UID 911 while arr apps run as UID 1000 — permission conflict
CONFLICTS: puid_pgid_mismatch (high)
HEALTH: yellow
TABS: Fix Permissions
APPLY_FIX: yes
RPM_WIZARD: no
OBSERVATIONS: None
NOTES: Paths are correct (shared /srv/data) but PUID/PGID mismatch means files created by qbit may not be readable by sonarr/radarr.
""")

    # B02-missing-puid
    write_stack(
        base / "B02-missing-puid",
        compose_content="""\
version: "3"
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr
    volumes:
      - /srv/data:/data
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent
    volumes:
      - /srv/data:/data
    environment:
      - TZ=America/New_York
""",
        expect_content="""\
DESCRIPTION: qBittorrent has no PUID/PGID set — will use image default (often root or 911)
CONFLICTS: missing_puid_pgid (medium)
HEALTH: yellow
TABS: Fix Permissions
APPLY_FIX: no
RPM_WIZARD: no
OBSERVATIONS: None
NOTES: Missing PUID/PGID means the service uses the image's built-in default, which may differ from other services.
""")

    # B03-root-execution
    write_stack(
        base / "B03-root-execution",
        compose_content="""\
version: "3"
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr
    volumes:
      - /srv/data:/data
    environment:
      - PUID=0
      - PGID=0
      - TZ=America/New_York
  radarr:
    image: lscr.io/linuxserver/radarr
    volumes:
      - /srv/data:/data
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent
    volumes:
      - /srv/data:/data
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
""",
        expect_content="""\
DESCRIPTION: Sonarr runs as root (PUID=0) — security risk and permission mismatch with other services
CONFLICTS: root_execution (medium), puid_pgid_mismatch (high)
HEALTH: yellow
TABS: Fix Permissions
APPLY_FIX: yes
RPM_WIZARD: no
OBSERVATIONS: None
NOTES: Running as root is a security risk. Also creates PUID mismatch (0 vs 1000).
""")

    # B04-umask-inconsistent
    write_stack(
        base / "B04-umask-inconsistent",
        compose_content="""\
version: "3"
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr
    volumes:
      - /srv/data:/data
    environment:
      - PUID=1000
      - PGID=1000
      - UMASK=022
      - TZ=America/New_York
  radarr:
    image: lscr.io/linuxserver/radarr
    volumes:
      - /srv/data:/data
    environment:
      - PUID=1000
      - PGID=1000
      - UMASK=002
      - TZ=America/New_York
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent
    volumes:
      - /srv/data:/data
    environment:
      - PUID=1000
      - PGID=1000
      - UMASK=002
      - TZ=America/New_York
""",
        expect_content="""\
DESCRIPTION: Sonarr uses UMASK 022 while others use 002 — files created by sonarr won't be group-writable
CONFLICTS: umask_inconsistent (low)
HEALTH: yellow
TABS: Fix Permissions
APPLY_FIX: yes
RPM_WIZARD: no
OBSERVATIONS: None
NOTES: UMASK 022 strips group write bit. 002 is the recommended value for media server setups.
""")

    # B05-umask-restrictive
    write_stack(
        base / "B05-umask-restrictive",
        compose_content="""\
version: "3"
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr
    volumes:
      - /srv/data:/data
    environment:
      - PUID=1000
      - PGID=1000
      - UMASK=077
      - TZ=America/New_York
  radarr:
    image: lscr.io/linuxserver/radarr
    volumes:
      - /srv/data:/data
    environment:
      - PUID=1000
      - PGID=1000
      - UMASK=077
      - TZ=America/New_York
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent
    volumes:
      - /srv/data:/data
    environment:
      - PUID=1000
      - PGID=1000
      - UMASK=077
      - TZ=America/New_York
""",
        expect_content="""\
DESCRIPTION: All services use UMASK 077 — files are owner-only, no group or world access
CONFLICTS: umask_restrictive (low)
HEALTH: yellow
TABS: Fix Permissions
APPLY_FIX: yes
RPM_WIZARD: no
OBSERVATIONS: None
NOTES: UMASK 077 means only the owner can read/write files. Even with matching PUID, this is overly restrictive for media sharing.
""")

    # B06-tz-mismatch
    write_stack(
        base / "B06-tz-mismatch",
        compose_content="""\
version: "3"
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr
    volumes:
      - /srv/data:/data
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
  radarr:
    image: lscr.io/linuxserver/radarr
    volumes:
      - /srv/data:/data
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=Europe/London
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent
    volumes:
      - /srv/data:/data
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=Asia/Tokyo
""",
        expect_content="""\
DESCRIPTION: Three different timezones across services — logs and scheduled tasks will be out of sync
CONFLICTS: tz_mismatch (low)
HEALTH: yellow
TABS: Fix Permissions
APPLY_FIX: yes
RPM_WIZARD: no
OBSERVATIONS: None
NOTES: TZ mismatch causes confusing log timestamps and can affect scheduled task timing.
""")

    # B07-missing-tz
    write_stack(
        base / "B07-missing-tz",
        compose_content="""\
version: "3"
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr
    volumes:
      - /srv/data:/data
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent
    volumes:
      - /srv/data:/data
    environment:
      - PUID=1000
      - PGID=1000
""",
        expect_content="""\
DESCRIPTION: qBittorrent is missing TZ env var — will default to UTC
CONFLICTS: None
HEALTH: green
TABS: Observations
APPLY_FIX: no
RPM_WIZARD: no
OBSERVATIONS: missing_tz
NOTES: Cat D observation only — does not affect health score. Service defaults to UTC when TZ is missing.
""")


# ═══════════════════════════════════════════════════════════════
# single/ — Category C (Infrastructure)
# ═══════════════════════════════════════════════════════════════

def generate_single_c():
    base = ROOT / "single"

    # C01-wsl2-paths
    write_stack(
        base / "C01-wsl2-paths",
        compose_content="""\
version: "3"
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr
    volumes:
      - /mnt/c/media/tv:/tv
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent
    volumes:
      - /mnt/c/downloads:/downloads
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
""",
        expect_content="""\
DESCRIPTION: WSL2 /mnt/c paths cause severe I/O performance issues — data should be on Linux filesystem
CONFLICTS: wsl2_performance (medium), no_shared_mount (high)
HEALTH: red
TABS: Path Fix
APPLY_FIX: yes
RPM_WIZARD: no
OBSERVATIONS: None
NOTES: /mnt/c is a 9P mount in WSL2 with ~10x slower I/O than native ext4.
""")

    # C02-mixed-mounts
    write_stack(
        base / "C02-mixed-mounts",
        compose_content="""\
version: "3"
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr
    volumes:
      - /srv/data/media:/media
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent
    volumes:
      - //192.168.1.100/downloads:/downloads
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
""",
        expect_content="""\
DESCRIPTION: Sonarr uses a local path while qBittorrent uses a network SMB share — mixed mount types
CONFLICTS: mixed_mount_types (medium), no_shared_mount (high)
HEALTH: red
TABS: Path Fix
APPLY_FIX: yes
RPM_WIZARD: no
OBSERVATIONS: None
NOTES: Mixing local bind mounts with network shares breaks hardlinks and adds latency.
""")

    # C03-windows-paths
    write_stack(
        base / "C03-windows-paths",
        compose_content="""\
version: "3"
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr
    volumes:
      - "C:\\\\Users\\\\media\\\\tv:/tv"
    environment:
      - PUID=1000
      - PGID=1000
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent
    volumes:
      - "D:\\\\Downloads:/downloads"
    environment:
      - PUID=1000
      - PGID=1000
""",
        expect_content="""\
DESCRIPTION: Windows-style paths in compose — these won't work correctly in Linux containers
CONFLICTS: windows_path_in_compose (low), no_shared_mount (high)
HEALTH: red
TABS: Path Fix
APPLY_FIX: yes
RPM_WIZARD: no
OBSERVATIONS: None
NOTES: Windows drive letter paths need to be converted to Linux paths (e.g., /c/Users/media/tv).
""")

    # C04-remote-filesystem
    write_stack(
        base / "C04-remote-filesystem",
        compose_content="""\
version: "3"
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr
    volumes:
      - //nas.local/media/tv:/tv
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
  radarr:
    image: lscr.io/linuxserver/radarr
    volumes:
      - //nas.local/media/movies:/movies
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent
    volumes:
      - //nas.local/downloads:/downloads
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
""",
        expect_content="""\
DESCRIPTION: All services mount from a NAS via SMB/CIFS — remote filesystem detected
CONFLICTS: remote_filesystem (medium)
HEALTH: yellow
TABS: Path Fix
APPLY_FIX: no
RPM_WIZARD: no
OBSERVATIONS: None
NOTES: Remote filesystems don't support hardlinks. Copies will be made instead of instant moves.
""")

    # C05-smb-unc-paths
    write_stack(
        base / "C05-smb-unc-paths",
        compose_content="""\
version: "3"
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr
    volumes:
      - "\\\\\\\\MediaNAS\\\\media\\\\tv:/tv"
    environment:
      - PUID=1000
      - PGID=1000
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent
    volumes:
      - "\\\\\\\\MediaNAS\\\\downloads:/downloads"
    environment:
      - PUID=1000
      - PGID=1000
""",
        expect_content="""\
DESCRIPTION: UNC-style SMB paths (\\\\MediaNAS\\share) — Windows network share syntax in compose
CONFLICTS: remote_filesystem (medium), no_shared_mount (high)
HEALTH: red
TABS: Path Fix
APPLY_FIX: no
RPM_WIZARD: no
OBSERVATIONS: None
NOTES: UNC paths are Windows-specific network share paths. Should use Linux CIFS mount points instead.
""")


# ═══════════════════════════════════════════════════════════════
# single/ — Category D (Observations)
# ═══════════════════════════════════════════════════════════════

def generate_single_d():
    base = ROOT / "single"

    # D01-observations-kitchen-sink
    write_stack(
        base / "D01-observations-kitchen-sink",
        compose_content="""\
version: "3"
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr:latest
    privileged: true
    volumes:
      - /srv/data:/data
    environment:
      - PUID=1000
      - PGID=1000
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent:latest
    volumes:
      - /srv/data:/data
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
""",
        expect_content="""\
DESCRIPTION: Kitchen sink of Cat D observations — latest tags, privileged, no restart, no TZ, no healthcheck
CONFLICTS: None
HEALTH: green
TABS: Observations
APPLY_FIX: no
RPM_WIZARD: no
OBSERVATIONS: latest_tag_usage, privileged_mode, missing_restart_policy, missing_tz, no_healthcheck
NOTES: Cat D items are informational only — they don't affect the health score. All observations should render in the Observations tab.
""")


# ═══════════════════════════════════════════════════════════════
# single/ — Multi-Category Combos
# ═══════════════════════════════════════════════════════════════

def generate_single_m():
    base = ROOT / "single"

    # M01-path-plus-permissions
    write_stack(
        base / "M01-path-plus-permissions",
        compose_content="""\
version: "3"
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr
    volumes:
      - /mnt/media:/media
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent
    volumes:
      - /mnt/downloads:/downloads
    environment:
      - PUID=911
      - PGID=911
      - TZ=America/New_York
""",
        expect_content="""\
DESCRIPTION: Both path conflict (separate mount trees) AND permission mismatch (1000 vs 911)
CONFLICTS: no_shared_mount (high), puid_pgid_mismatch (high)
HEALTH: red
TABS: Path Fix, Fix Permissions
APPLY_FIX: yes
RPM_WIZARD: yes
OBSERVATIONS: None
NOTES: Classic double-whammy: broken paths AND wrong permissions. Both tabs should be present.
""")

    # M02-infra-plus-permissions
    write_stack(
        base / "M02-infra-plus-permissions",
        compose_content="""\
version: "3"
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr
    volumes:
      - //nas/media:/media
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent
    volumes:
      - //nas/downloads:/downloads
    environment:
      - PUID=911
      - PGID=911
      - TZ=Europe/London
""",
        expect_content="""\
DESCRIPTION: Remote filesystem + PUID mismatch + TZ mismatch + no shared mount — everything wrong except paths
CONFLICTS: remote_filesystem (medium), puid_pgid_mismatch (high), tz_mismatch (low), no_shared_mount (high)
HEALTH: red
TABS: Path Fix, Fix Permissions
APPLY_FIX: yes
RPM_WIZARD: no
OBSERVATIONS: None
NOTES: Infra issues (remote FS) combined with permission issues. Tests multi-tab rendering.
""")

    # M03-mega-chaos
    write_stack(
        base / "M03-mega-chaos",
        compose_content="""\
version: "3"
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr:latest
    privileged: true
    volumes:
      - /mnt/media/tv:/tv
    environment:
      - PUID=1000
      - PGID=1000
  radarr:
    image: lscr.io/linuxserver/radarr:latest
    volumes:
      - /opt/media/movies:/movies
    environment:
      - PUID=1001
      - PGID=1001
      - TZ=Europe/London
  lidarr:
    image: lscr.io/linuxserver/lidarr:latest
    volumes:
      - /srv/music:/music
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent:latest
    volumes:
      - /mnt/fast-ssd/downloads:/downloads
    environment:
      - PUID=911
      - PGID=911
  sabnzbd:
    image: lscr.io/linuxserver/sabnzbd:latest
    volumes:
      - //nas/usenet:/usenet
    environment:
      - PUID=1000
      - PGID=1000
      - UMASK=077
  plex:
    image: lscr.io/linuxserver/plex:latest
    volumes:
      - /mnt/media:/data
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=Asia/Tokyo
  overseerr:
    image: lscr.io/linuxserver/overseerr:latest
    volumes:
      - /config/overseerr:/config
    environment:
      - PUID=1000
      - PGID=1000
  prowlarr:
    image: lscr.io/linuxserver/prowlarr:latest
    volumes:
      - /config/prowlarr:/config
    environment:
      - PUID=1000
      - PGID=1000
""",
        expect_content="""\
DESCRIPTION: 8-service mega stack with every category of problem — the ultimate stress test
CONFLICTS: no_shared_mount (high), different_host_paths (medium), puid_pgid_mismatch (high), tz_mismatch (low), umask_restrictive (low), remote_filesystem (medium)
HEALTH: red
TABS: Path Fix, Fix Permissions, Observations
APPLY_FIX: yes
RPM_WIZARD: yes
OBSERVATIONS: latest_tag_usage, privileged_mode, missing_restart_policy, missing_tz
NOTES: Tests rendering with many services, many conflicts, all tabs. Overseerr and prowlarr are "other" role — shown in non-media stacks section.
""")

    # M04-all-healthy
    write_stack(
        base / "M04-all-healthy",
        compose_content="""\
version: "3"
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr
    restart: unless-stopped
    volumes:
      - /srv/data:/data
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
      - UMASK=002
  radarr:
    image: lscr.io/linuxserver/radarr
    restart: unless-stopped
    volumes:
      - /srv/data:/data
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
      - UMASK=002
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent
    restart: unless-stopped
    volumes:
      - /srv/data:/data
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
      - UMASK=002
  plex:
    image: lscr.io/linuxserver/plex
    restart: unless-stopped
    volumes:
      - /srv/data:/data
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
""",
        expect_content="""\
DESCRIPTION: Perfectly configured stack — shared /srv/data, matching PUID/PGID/TZ/UMASK, restart policies
CONFLICTS: None
HEALTH: green
TABS: None
APPLY_FIX: no
RPM_WIZARD: no
OBSERVATIONS: None
NOTES: The control case. Everything is correct. No conflicts, no observations, green health.
""")

    # M05-healthy-large
    write_stack(
        base / "M05-healthy-large",
        compose_content="""\
version: "3"
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr
    restart: unless-stopped
    volumes:
      - /srv/data:/data
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
      - UMASK=002
  radarr:
    image: lscr.io/linuxserver/radarr
    restart: unless-stopped
    volumes:
      - /srv/data:/data
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
      - UMASK=002
  lidarr:
    image: lscr.io/linuxserver/lidarr
    restart: unless-stopped
    volumes:
      - /srv/data:/data
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
      - UMASK=002
  readarr:
    image: lscr.io/linuxserver/readarr
    restart: unless-stopped
    volumes:
      - /srv/data:/data
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
      - UMASK=002
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent
    restart: unless-stopped
    volumes:
      - /srv/data:/data
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
      - UMASK=002
  plex:
    image: lscr.io/linuxserver/plex
    restart: unless-stopped
    volumes:
      - /srv/data:/data
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
""",
        expect_content="""\
DESCRIPTION: 6-service healthy stack — stress test for rendering many services with no issues
CONFLICTS: None
HEALTH: green
TABS: None
APPLY_FIX: no
RPM_WIZARD: no
OBSERVATIONS: None
NOTES: Large healthy stack. Tests that the UI renders correctly with many services and no conflict tabs.
""")


# ═══════════════════════════════════════════════════════════════
# single/ — Image Family Variants
# ═══════════════════════════════════════════════════════════════

def generate_single_f():
    base = ROOT / "single"

    # F01-all-hotio
    write_stack(
        base / "F01-all-hotio",
        compose_content="""\
version: "3"
services:
  sonarr:
    image: ghcr.io/hotio/sonarr
    volumes:
      - /srv/data:/data
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
      - UMASK=002
  radarr:
    image: ghcr.io/hotio/radarr
    volumes:
      - /srv/data:/data
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
      - UMASK=002
  qbittorrent:
    image: ghcr.io/hotio/qbittorrent
    volumes:
      - /srv/data:/data
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
      - UMASK=002
""",
        expect_content="""\
DESCRIPTION: All Hotio images, healthy config — tests Hotio family detection
CONFLICTS: None
HEALTH: green
TABS: None
APPLY_FIX: no
RPM_WIZARD: no
OBSERVATIONS: None
NOTES: Validates that Hotio images are correctly identified as a family. PUID/PGID env vars work the same as LSIO.
""")

    # F02-mixed-families
    write_stack(
        base / "F02-mixed-families",
        compose_content="""\
version: "3"
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr
    volumes:
      - /srv/data:/data
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
  radarr:
    image: ghcr.io/hotio/radarr
    volumes:
      - /srv/data:/data
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
  sabnzbd:
    image: jlesage/sabnzbd
    volumes:
      - /srv/data:/data
    environment:
      - USER_ID=1000
      - GROUP_ID=1000
      - TZ=America/New_York
  qbittorrent:
    image: binhex/arch-qbittorrentvpn
    volumes:
      - /srv/data:/data
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
""",
        expect_content="""\
DESCRIPTION: Four different image families (LSIO, Hotio, jlesage, Binhex) — all resolve to UID 1000
CONFLICTS: None
HEALTH: green
TABS: None
APPLY_FIX: no
RPM_WIZARD: no
OBSERVATIONS: None
NOTES: Tests that MapArr correctly resolves UID across different image families. jlesage uses USER_ID/GROUP_ID instead of PUID/PGID.
""")

    # F03-custom-unknown-images
    write_stack(
        base / "F03-custom-unknown-images",
        compose_content="""\
version: "3"
services:
  sonarr:
    image: myregistry.local/custom-sonarr:v3
    volumes:
      - /opt/arr/media:/media
    environment:
      - PUID=1000
      - PGID=1000
  radarr:
    image: ghcr.io/someuser/radarr-fork:dev
    volumes:
      - /mnt/storage/movies:/movies
    environment:
      - PUID=1000
      - PGID=1000
  qbittorrent:
    image: registry.example.com/qbit:2.0
    volumes:
      - /data/downloads:/downloads
    environment:
      - PUID=1000
      - PGID=1000
""",
        expect_content="""\
DESCRIPTION: Unknown registries/custom images — tests keyword-based service classification fallback
CONFLICTS: no_shared_mount (high)
HEALTH: red
TABS: Path Fix
APPLY_FIX: yes
RPM_WIZARD: yes
OBSERVATIONS: None
NOTES: Services should be classified by keyword fallback (sonarr/radarr/qbittorrent in service name). Three different host roots means no shared mount.
""")


# ═══════════════════════════════════════════════════════════════
# single/ — Edge Cases
# ═══════════════════════════════════════════════════════════════

def generate_single_e():
    base = ROOT / "single"

    # E01-env-file-substitution
    write_stack(
        base / "E01-env-file-substitution",
        compose_content="""\
version: "3"
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr
    volumes:
      - ${MEDIA_PATH}:/media
    environment:
      - PUID=${PUID}
      - PGID=${PGID}
      - TZ=America/New_York
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent
    volumes:
      - ${DOWNLOADS_PATH}:/downloads
    environment:
      - PUID=${PUID}
      - PGID=${PGID}
      - TZ=America/New_York
""",
        expect_content="""\
DESCRIPTION: Compose uses .env variable interpolation for paths and PUID/PGID
CONFLICTS: Depends on resolver — if .env is interpolated, paths resolve to /srv/data/media and /srv/data/downloads
HEALTH: Depends on interpolation behavior
TABS: Depends
APPLY_FIX: no
RPM_WIZARD: no
OBSERVATIONS: None
NOTES: Tests whether MapArr resolves .env variables. If not interpolated, ${MEDIA_PATH} is treated as literal string.
""",
        env_content="""\
PUID=1000
PGID=1000
MEDIA_PATH=/srv/data/media
DOWNLOADS_PATH=/srv/data/downloads
""")

    # E02-compose-override
    write_stack(
        base / "E02-compose-override",
        compose_content="""\
version: "3"
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr
    volumes:
      - /srv/data:/data
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
""",
        expect_content="""\
DESCRIPTION: Base compose has healthy sonarr, override adds qbittorrent with conflicting path and PUID
CONFLICTS: no_shared_mount (high), puid_pgid_mismatch (high) — IF override is merged
HEALTH: red (if merged), green (if override ignored)
TABS: Path Fix, Fix Permissions (if merged)
APPLY_FIX: yes
RPM_WIZARD: no
OBSERVATIONS: None
NOTES: Tests docker-compose.override.yml merge behavior. The override adds a second service with different paths and PUID.
""",
        override_content="""\
version: "3"
services:
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent
    volumes:
      - /mnt/downloads:/downloads
    environment:
      - PUID=911
      - PGID=911
      - TZ=America/New_York
""")

    # E03-malformed-yaml
    write_stack(
        base / "E03-malformed-yaml",
        compose_content="""\
services:
  sonarr:
    image: linuxserver/sonarr
    volumes:
      - /data:/data
    environment
      - PUID=1000
""",
        expect_content="""\
DESCRIPTION: Invalid YAML — missing colon after 'environment' keyword
CONFLICTS: None (parse error)
HEALTH: N/A
TABS: None
APPLY_FIX: no
RPM_WIZARD: no
OBSERVATIONS: None
NOTES: Should show YAML parse error gracefully, NOT crash. Error message should indicate the syntax problem.
""")

    # E04-empty-compose
    write_stack(
        base / "E04-empty-compose",
        compose_content="""\
# Empty compose file
version: "3"
services: {}
""",
        expect_content="""\
DESCRIPTION: Valid YAML with no services defined — empty services block
CONFLICTS: None
HEALTH: N/A
TABS: None
APPLY_FIX: no
RPM_WIZARD: no
OBSERVATIONS: None
NOTES: Should handle gracefully — no services to analyze, no crash.
""")

    # E05-single-service-only
    write_stack(
        base / "E05-single-service-only",
        compose_content="""\
version: "3"
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr
    volumes:
      - /srv/data:/data
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
""",
        expect_content="""\
DESCRIPTION: Only one arr service — no download client to compare against
CONFLICTS: None or incomplete analysis
HEALTH: green or N/A
TABS: None
APPLY_FIX: no
RPM_WIZARD: no
OBSERVATIONS: None
NOTES: Tests behavior with incomplete stack. No download client means no path comparison is possible.
""")


# ═══════════════════════════════════════════════════════════════
# clusters/ — Multi-folder layouts
# ═══════════════════════════════════════════════════════════════

def generate_cluster_stacks():
    base = ROOT / "clusters"

    # CL01-cluster-broken-paths
    cl01 = base / "CL01-cluster-broken-paths"
    cl01.mkdir(parents=True, exist_ok=True)
    (cl01 / "_EXPECT.txt").write_text("""\
DESCRIPTION: Cluster layout (one service per folder) with separate mount trees — no shared root
CONFLICTS: no_shared_mount (high)
HEALTH: red
TABS: Path Fix
APPLY_FIX: yes
RPM_WIZARD: yes
OBSERVATIONS: None
NOTES: Load CL01-cluster-broken-paths as the root directory. MapArr should scan child folders and find 3 compose files.
""", encoding="utf-8")

    write_cluster_service(cl01, "sonarr", """\
version: "3"
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr
    volumes:
      - /mnt/media/tv:/tv
    environment:
      - PUID=1000
      - PGID=1000
""")
    write_cluster_service(cl01, "radarr", """\
version: "3"
services:
  radarr:
    image: lscr.io/linuxserver/radarr
    volumes:
      - /mnt/media/movies:/movies
    environment:
      - PUID=1000
      - PGID=1000
""")
    write_cluster_service(cl01, "qbittorrent", """\
version: "3"
services:
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent
    volumes:
      - /mnt/downloads:/downloads
    environment:
      - PUID=1000
      - PGID=1000
""")

    # CL02-cluster-healthy
    cl02 = base / "CL02-cluster-healthy"
    cl02.mkdir(parents=True, exist_ok=True)
    (cl02 / "_EXPECT.txt").write_text("""\
DESCRIPTION: Cluster layout, all services healthy — shared /srv/data, matching PUID/PGID/TZ
CONFLICTS: None
HEALTH: green
TABS: None
APPLY_FIX: no
RPM_WIZARD: no
OBSERVATIONS: None
NOTES: Load CL02-cluster-healthy as the root directory. All 4 services should be detected with green health.
""", encoding="utf-8")

    write_cluster_service(cl02, "sonarr", """\
version: "3"
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr
    volumes:
      - /srv/data:/data
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
""")
    write_cluster_service(cl02, "radarr", """\
version: "3"
services:
  radarr:
    image: lscr.io/linuxserver/radarr
    volumes:
      - /srv/data:/data
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
""")
    write_cluster_service(cl02, "qbittorrent", """\
version: "3"
services:
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent
    volumes:
      - /srv/data:/data
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
""")
    write_cluster_service(cl02, "plex", """\
version: "3"
services:
  plex:
    image: lscr.io/linuxserver/plex
    volumes:
      - /srv/data:/data
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
""")

    # CL03-cluster-permissions
    cl03 = base / "CL03-cluster-permissions"
    cl03.mkdir(parents=True, exist_ok=True)
    (cl03 / "_EXPECT.txt").write_text("""\
DESCRIPTION: Cluster layout with PUID mismatch — mixed LSIO and Hotio images, qbit runs as 911
CONFLICTS: puid_pgid_mismatch (high)
HEALTH: yellow
TABS: Fix Permissions
APPLY_FIX: yes
RPM_WIZARD: no
OBSERVATIONS: None
NOTES: Load CL03-cluster-permissions as the root directory. Tests cross-folder permission detection with mixed image families.
""", encoding="utf-8")

    write_cluster_service(cl03, "sonarr", """\
version: "3"
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr
    volumes:
      - /srv/data:/data
    environment:
      - PUID=1000
      - PGID=1000
""")
    write_cluster_service(cl03, "radarr", """\
version: "3"
services:
  radarr:
    image: ghcr.io/hotio/radarr
    volumes:
      - /srv/data:/data
    environment:
      - PUID=1000
      - PGID=1000
""")
    write_cluster_service(cl03, "qbittorrent", """\
version: "3"
services:
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent
    volumes:
      - /srv/data:/data
    environment:
      - PUID=911
      - PGID=911
""")


# ═══════════════════════════════════════════════════════════════
# roots/ — Full pipeline scan layout
# ═══════════════════════════════════════════════════════════════

def generate_roots():
    base = ROOT / "roots"
    base.mkdir(parents=True, exist_ok=True)

    (base / "_EXPECT.txt").write_text("""\
DESCRIPTION: Realistic root directory with 4 service folders — plex has subtle PUID mismatch (1001 vs 1000)
CONFLICTS: puid_pgid_mismatch (high)
HEALTH: yellow
TABS: Fix Permissions
APPLY_FIX: yes
RPM_WIZARD: no
OBSERVATIONS: None
NOTES: Load roots/ directly as the stacks path. Tests the "mostly fine with one subtle issue" scenario.
""", encoding="utf-8")

    write_cluster_service(base, "sonarr", """\
version: "3"
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr
    restart: unless-stopped
    volumes:
      - /srv/data:/data
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
""")
    write_cluster_service(base, "radarr", """\
version: "3"
services:
  radarr:
    image: lscr.io/linuxserver/radarr
    restart: unless-stopped
    volumes:
      - /srv/data:/data
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
""")
    write_cluster_service(base, "qbittorrent", """\
version: "3"
services:
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent
    restart: unless-stopped
    volumes:
      - /srv/data:/data
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
""")
    write_cluster_service(base, "plex", """\
version: "3"
services:
  plex:
    image: lscr.io/linuxserver/plex
    restart: unless-stopped
    volumes:
      - /srv/data:/data
    environment:
      - PUID=1001
      - PGID=1000
      - TZ=America/New_York
""")


# ═══════════════════════════════════════════════════════════════
# paste/ — Paste pathway scenarios
# ═══════════════════════════════════════════════════════════════

def generate_paste_stacks():
    base = ROOT / "paste"

    # P01-import-failed
    write_stack(
        base / "P01-import-failed",
        compose_content="""\
version: "3"
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr
    volumes:
      - /mnt/media/tv:/tv
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent
    volumes:
      - /mnt/downloads:/downloads
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
""",
        expect_content="""\
DESCRIPTION: Sonarr import failed — classic separate mount trees, paste pathway test
CONFLICTS: no_shared_mount (high)
HEALTH: red
TABS: Path Fix
APPLY_FIX: yes
RPM_WIZARD: yes
OBSERVATIONS: None
NOTES: Paste the _PASTE.txt content. Should auto-drill to sonarr, detect no_shared_mount.
""",
        paste_content="""\
Import failed, path does not exist or is not accessible by Sonarr: /downloads/complete/Some.Show.S01E01.720p.mkv. Ensure the path exists and the user running Sonarr has the correct permissions to access this file/folder
""")

    # P02-hardlink-exdev
    write_stack(
        base / "P02-hardlink-exdev",
        compose_content="""\
version: "3"
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr
    volumes:
      - /mnt/disk1/media:/media
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent
    volumes:
      - /mnt/disk2/downloads:/downloads
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
""",
        expect_content="""\
DESCRIPTION: Cross-device hardlink error (EXDEV) — media and downloads on different disks
CONFLICTS: no_shared_mount (high)
HEALTH: red
TABS: Path Fix
APPLY_FIX: yes
RPM_WIZARD: yes
OBSERVATIONS: None
NOTES: Paste the _PASTE.txt content. EXDEV error means hardlink across filesystems was attempted.
""",
        paste_content="""\
[Error] Import failed: [/downloads/complete/Movie.2024.1080p/Movie.2024.1080p.mkv] Import failed, error code EXDEV (18): Cross-device link : '/downloads/complete/Movie.2024.1080p/Movie.2024.1080p.mkv' -> '/media/movies/Movie (2024)/Movie.2024.1080p.mkv'
""")

    # P03-permission-denied
    write_stack(
        base / "P03-permission-denied",
        compose_content="""\
version: "3"
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr
    volumes:
      - /srv/data:/data
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent
    volumes:
      - /srv/data:/data
    environment:
      - PUID=911
      - PGID=911
      - TZ=America/New_York
""",
        expect_content="""\
DESCRIPTION: Permission denied error — sonarr (1000) can't access files created by qbit (911)
CONFLICTS: puid_pgid_mismatch (high)
HEALTH: yellow
TABS: Fix Permissions
APPLY_FIX: yes
RPM_WIZARD: no
OBSERVATIONS: None
NOTES: Paste the _PASTE.txt content. Should auto-drill to sonarr, detect PUID mismatch.
""",
        paste_content="""\
Access to the path '/data/media/tv/Some.Show/Season 01' is denied. Ensure the user running Sonarr has the correct permissions to access this file/folder
""")

    # P04-path-not-found
    write_stack(
        base / "P04-path-not-found",
        compose_content="""\
version: "3"
services:
  radarr:
    image: lscr.io/linuxserver/radarr
    volumes:
      - /srv/media:/media
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent
    volumes:
      - /srv/downloads:/downloads
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
""",
        expect_content="""\
DESCRIPTION: Radarr references /data/completed but nothing is mounted at /data — path unreachable
CONFLICTS: path_unreachable (high), no_shared_mount (high)
HEALTH: red
TABS: Path Fix
APPLY_FIX: yes
RPM_WIZARD: yes
OBSERVATIONS: None
NOTES: Paste the _PASTE.txt content. The error references /data but radarr only has /media mounted.
""",
        paste_content="""\
[Error] DownloadedMovieImportService: Import failed, path does not exist or is not accessible by Radarr: /data/completed/Movie.2024.1080p. Ensure the path exists and the user running Radarr has the correct permissions
""")

    # P05-rpm-needed
    write_stack(
        base / "P05-rpm-needed",
        compose_content="""\
version: "3"
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr
    volumes:
      - /mnt/nas/data:/data
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent
    volumes:
      - /mnt/nas/downloads:/downloads
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
""",
        expect_content="""\
DESCRIPTION: Remote Path Mapping needed — sonarr sees /data but qbit reports /downloads paths
CONFLICTS: no_shared_mount (high)
HEALTH: red
TABS: Path Fix
APPLY_FIX: yes
RPM_WIZARD: yes
OBSERVATIONS: None
NOTES: Paste the _PASTE.txt content. Error explicitly mentions Remote Path Mapping — RPM wizard should be available.
""",
        paste_content="""\
[Warn] Couldn't import episode /downloads/complete/tv/Some.Show.S02E05.mkv: Episode file path '/downloads/complete/tv/Some.Show.S02E05.mkv' is not valid. Ensure the Remote Path Mapping is configured correctly.
""")

    # P06-download-stuck
    write_stack(
        base / "P06-download-stuck",
        compose_content="""\
version: "3"
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr
    volumes:
      - /srv/data:/data
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent
    volumes:
      - /srv/data:/data
    environment:
      - PUID=1000
      - PGID=1000
      - UMASK=077
      - TZ=America/New_York
""",
        expect_content="""\
DESCRIPTION: Files stuck in downloads — qbit UMASK 077 makes files unreadable by sonarr
CONFLICTS: umask_restrictive (low)
HEALTH: yellow
TABS: Fix Permissions
APPLY_FIX: yes
RPM_WIZARD: no
OBSERVATIONS: None
NOTES: Paste the _PASTE.txt content. Paths are correct but UMASK 077 prevents sonarr from reading qbit's files.
""",
        paste_content="""\
[Warn] No files found are eligible for import in /data/downloads/complete/Some.Show.S01E01
""")

    # P07-multi-error
    write_stack(
        base / "P07-multi-error",
        compose_content="""\
version: "3"
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr
    volumes:
      - /mnt/media:/media
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
  radarr:
    image: lscr.io/linuxserver/radarr
    volumes:
      - /mnt/media:/media
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent
    volumes:
      - /mnt/downloads:/downloads
    environment:
      - PUID=911
      - PGID=911
      - TZ=America/New_York
""",
        expect_content="""\
DESCRIPTION: Multiple errors pasted at once — tests CRLF splitter and multi-service detection
CONFLICTS: no_shared_mount (high), puid_pgid_mismatch (high)
HEALTH: red
TABS: Path Fix, Fix Permissions
APPLY_FIX: yes
RPM_WIZARD: yes
OBSERVATIONS: None
NOTES: Paste ALL 3 errors from _PASTE.txt at once. Multiple services should be highlighted in paste bar.
""",
        paste_content="""\
Import failed, path does not exist or is not accessible by Sonarr: /downloads/complete/Show.S01E01.mkv

[Error] DownloadedMovieImportService: Import failed, path does not exist or is not accessible by Radarr: /downloads/complete/Movie.2024.mkv

Access to the path '/media/movies' is denied.
""")


# ═══════════════════════════════════════════════════════════════
# _INDEX.txt — Master index
# ═══════════════════════════════════════════════════════════════

def generate_index():
    index = """\
MapArr Test Stacks v2 — Master Index
=====================================
Generated by seed_test_stacks_v2.py
42 total scenarios across 5 directories

HOW TO USE:
  single/*    → Load each subfolder as a stack (Browse pathway)
  clusters/*  → Load each subfolder as a ROOT (scans child folders)
  roots/      → Load THIS folder directly as a root
  paste/*     → Load subfolder as stack, then paste _PASTE.txt content

Each stack contains:
  docker-compose.yml  — The compose file to analyze
  _EXPECT.txt         — Expected MapArr behavior
  _PASTE.txt          — Error text for paste pathway (paste/ only)
  .env                — Environment file (some edge cases only)

=====================================
CATEGORY A — Path Conflicts (5 stacks)
=====================================
single/A01-separate-mount-trees      no_shared_mount — classic broken setup, separate /media and /downloads
single/A02-different-host-paths      different_host_paths — same container /data, different host subdirs
single/A03-named-volumes             named_volume_data — Docker named volumes, no hardlinks possible
single/A04-unreachable-path          path_unreachable — container path not under /data tree
single/A05-partial-overlap           no_shared_mount (partial) — arr apps share root, download client separate

=====================================
CATEGORY B — Permissions (7 stacks)
=====================================
single/B01-puid-mismatch             puid_pgid_mismatch — qbit UID 911 vs arr UID 1000
single/B02-missing-puid              missing_puid_pgid — qbit has no PUID/PGID set
single/B03-root-execution            root_execution — sonarr runs as PUID=0
single/B04-umask-inconsistent        umask_inconsistent — sonarr 022, others 002
single/B05-umask-restrictive         umask_restrictive — all services UMASK 077
single/B06-tz-mismatch               tz_mismatch — three different timezones
single/B07-missing-tz                missing_tz — observation only, no health impact

=====================================
CATEGORY C — Infrastructure (5 stacks)
=====================================
single/C01-wsl2-paths                wsl2_performance — /mnt/c paths in WSL2
single/C02-mixed-mounts              mixed_mount_types — local + SMB network share
single/C03-windows-paths             windows_path_in_compose — C:\\ and D:\\ paths
single/C04-remote-filesystem         remote_filesystem — all NAS mounts via //nas.local/
single/C05-smb-unc-paths             remote_filesystem (UNC) — \\\\MediaNAS\\ style paths

=====================================
CATEGORY D — Observations (1 stack)
=====================================
single/D01-observations-kitchen-sink All Cat D items: latest tag, privileged, no restart, no TZ, no healthcheck

=====================================
MULTI-CATEGORY COMBOS (5 stacks)
=====================================
single/M01-path-plus-permissions     Cat A + B: separate mounts AND PUID mismatch
single/M02-infra-plus-permissions    Cat B + C: remote FS + PUID mismatch + TZ mismatch
single/M03-mega-chaos                All categories, 8 services, maximum conflict density
single/M04-all-healthy               Control case: zero issues, green health
single/M05-healthy-large             6 services, all healthy, rendering stress test

=====================================
IMAGE FAMILY VARIANTS (3 stacks)
=====================================
single/F01-all-hotio                 All Hotio images, healthy — family detection test
single/F02-mixed-families            LSIO + Hotio + jlesage + Binhex — cross-family UID resolution
single/F03-custom-unknown-images     Unknown registries — keyword fallback classification

=====================================
EDGE CASES (5 stacks)
=====================================
single/E01-env-file-substitution     .env variable interpolation — ${PUID}, ${MEDIA_PATH}
single/E02-compose-override          docker-compose.override.yml merge test
single/E03-malformed-yaml            Invalid YAML — graceful parse error handling
single/E04-empty-compose             Valid YAML, empty services block
single/E05-single-service-only       One arr service, no download client

=====================================
CLUSTERS (3 stacks)
=====================================
clusters/CL01-cluster-broken-paths   One-service-per-folder, separate mount trees
clusters/CL02-cluster-healthy        One-service-per-folder, all healthy
clusters/CL03-cluster-permissions    One-service-per-folder, PUID mismatch + mixed families

=====================================
ROOTS (1 stack)
=====================================
roots/                               Load directly — 4 service folders, plex has subtle PUID=1001

=====================================
PASTE PATHWAY (7 stacks)
=====================================
paste/P01-import-failed              Sonarr import failed — classic no_shared_mount
paste/P02-hardlink-exdev             EXDEV cross-device link error
paste/P03-permission-denied          Access denied — PUID mismatch
paste/P04-path-not-found             Radarr /data path not mounted
paste/P05-rpm-needed                 Remote Path Mapping error text
paste/P06-download-stuck             Files stuck — UMASK 077 blocks access
paste/P07-multi-error                3 errors at once — tests CRLF splitter
"""
    (ROOT / "_INDEX.txt").write_text(index, encoding="utf-8")


# ═══════════════════════════════════════════════════════════════
# Main — generate everything
# ═══════════════════════════════════════════════════════════════

def generate_single_stacks():
    """Generate all single/ stacks across all categories."""
    generate_single_a()
    generate_single_b()
    generate_single_c()
    generate_single_d()
    generate_single_m()
    generate_single_f()
    generate_single_e()


def main():
    # Remove existing test-stacks
    if ROOT.exists():
        shutil.rmtree(ROOT)
    ROOT.mkdir(parents=True)

    # Generate all stacks
    generate_single_stacks()
    generate_cluster_stacks()
    generate_roots()
    generate_paste_stacks()
    generate_index()

    # Count what was generated
    compose_count = sum(1 for _ in ROOT.rglob("docker-compose.yml"))
    expect_count = sum(1 for _ in ROOT.rglob("_EXPECT.txt"))
    paste_count = sum(1 for _ in ROOT.rglob("_PASTE.txt"))
    env_count = sum(1 for _ in ROOT.rglob(".env"))
    override_count = sum(1 for _ in ROOT.rglob("docker-compose.override.yml"))

    print(f"Generated test stacks in {ROOT}")
    print(f"  {compose_count} docker-compose.yml files")
    print(f"  {expect_count} _EXPECT.txt files")
    print(f"  {paste_count} _PASTE.txt files")
    print(f"  {env_count} .env files")
    print(f"  {override_count} docker-compose.override.yml files")
    print(f"  1 _INDEX.txt")


if __name__ == "__main__":
    main()
