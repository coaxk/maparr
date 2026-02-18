#!/usr/bin/env python3
"""
seed_test_stacks.py — Generate broken compose stacks for MapArr testing.

Creates a directory of 12 Docker Compose stacks, each with a specific
seeded path mapping problem. Each stack includes a _TEST.txt file with:
  - The scenario description
  - The exact error message to paste into MapArr (from real *arr apps)
  - What MapArr should detect and recommend

Usage:
  python tools/seed_test_stacks.py                    # Generate in ./test-stacks/
  python tools/seed_test_stacks.py --output /tmp/test # Custom output directory
  python tools/seed_test_stacks.py --reset             # Wipe and regenerate

Then point MapArr at it:
  MAPARR_STACKS_PATH=./test-stacks python -m uvicorn backend.main:app --port 9494

For beta testers:
  1. Run this script
  2. Start MapArr pointed at the output directory
  3. Open each _TEST.txt, paste the error, verify MapArr's analysis
  4. Check all 12 scenarios — report any misses
"""

import argparse
import os
import shutil
import sys
from pathlib import Path


# ═══════════════════════════════════════════════════════════════
# Scenario definitions
# Each scenario is a dict with:
#   name:     directory name
#   desc:     human description of what's wrong
#   compose:  the docker-compose.yml content (broken on purpose)
#   error:    the exact error message a user would see in their *arr app
#   expect:   what MapArr should detect
#   env:      optional .env file content
# ═══════════════════════════════════════════════════════════════

SCENARIOS = [
    # ─── 1. The Classic: Separate Mount Trees ───
    {
        "name": "01-separate-mount-trees",
        "desc": (
            "THE #1 PROBLEM. Sonarr and qBittorrent mount completely different\n"
            "host directories. Hardlinks and atomic moves are impossible because\n"
            "Docker treats each bind mount as a separate filesystem.\n"
            "\n"
            "Sonarr mounts:  /mnt/media/tv:/data/tv\n"
            "qBittorrent:    /mnt/downloads:/downloads\n"
            "\n"
            "These are separate mount trees — no shared parent."
        ),
        "compose": """\
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr:latest
    container_name: sonarr
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
    volumes:
      - ./config/sonarr:/config
      - /mnt/media/tv:/data/tv
    ports:
      - "8989:8989"
    restart: unless-stopped

  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent:latest
    container_name: qbittorrent
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
    volumes:
      - ./config/qbittorrent:/config
      - /mnt/downloads:/downloads
    ports:
      - "8080:8080"
    restart: unless-stopped
""",
        "error": (
            "Import failed, path does not exist or is not accessible by Sonarr: "
            "/downloads/tv-sonarr/Breaking.Bad.S01E01.720p.BluRay.x264/Breaking.Bad.S01E01.mkv"
        ),
        "expect": (
            "MapArr should detect:\n"
            "  - CRITICAL: no_shared_mount conflict\n"
            "  - Sonarr uses /mnt/media host root, qBittorrent uses /mnt/downloads\n"
            "  - Recommendation: unified /data mount for both services\n"
            "  - Solution YAML with TRaSH Guides pattern"
        ),
    },

    # ─── 2. Different Host Paths, Same Container Path ───
    {
        "name": "02-different-host-paths",
        "desc": (
            "Sonarr and Radarr both mount /data/media inside the container,\n"
            "but they point to DIFFERENT host directories. They think they're\n"
            "sharing data, but they're not.\n"
            "\n"
            "Sonarr:  /home/user/tv:/data/media\n"
            "Radarr:  /home/user/movies:/data/media"
        ),
        "compose": """\
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr:latest
    container_name: sonarr
    volumes:
      - ./config/sonarr:/config
      - /home/user/tv:/data/media
    ports:
      - "8989:8989"
    restart: unless-stopped

  radarr:
    image: lscr.io/linuxserver/radarr:latest
    container_name: radarr
    volumes:
      - ./config/radarr:/config
      - /home/user/movies:/data/media
    ports:
      - "7878:7878"
    restart: unless-stopped

  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent:latest
    container_name: qbittorrent
    volumes:
      - ./config/qbit:/config
      - /home/user/downloads:/downloads
    ports:
      - "8080:8080"
    restart: unless-stopped
""",
        "error": (
            "Import failed, path does not exist or is not accessible by Radarr: "
            "/downloads/movies/The.Matrix.1999.2160p.UHD.BluRay/The.Matrix.mkv"
        ),
        "expect": (
            "MapArr should detect:\n"
            "  - CRITICAL: no_shared_mount (three different host roots)\n"
            "  - HIGH: different_host_paths (/data/media backed by two host dirs)\n"
            "  - Solution: unified mount structure"
        ),
    },

    # ─── 3. Unreachable Error Path ───
    {
        "name": "03-unreachable-path",
        "desc": (
            "Sonarr has a volume mount, but the path in the error message\n"
            "doesn't match ANY of its mounted paths. The container path\n"
            "/downloads doesn't exist because Sonarr only has /data/tv mounted.\n"
            "\n"
            "This happens when qBittorrent reports its download path and\n"
            "Sonarr tries to access it, but Sonarr has no volume covering /downloads."
        ),
        "compose": """\
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr:latest
    container_name: sonarr
    volumes:
      - ./config/sonarr:/config
      - /mnt/data/tv:/data/tv
    ports:
      - "8989:8989"
    restart: unless-stopped

  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent:latest
    container_name: qbittorrent
    volumes:
      - ./config/qbit:/config
      - /mnt/data/downloads:/downloads
    ports:
      - "8080:8080"
    restart: unless-stopped
""",
        "error": (
            "Import failed, path does not exist or is not accessible by Sonarr: "
            "/downloads/tv-sonarr/The.Expanse.S06E06.1080p.AMZN.WEB-DL/The.Expanse.S06E06.mkv"
        ),
        "expect": (
            "MapArr should detect:\n"
            "  - CRITICAL: path_unreachable — Sonarr can't access /downloads\n"
            "  - CRITICAL: no_shared_mount — separate mount trees\n"
            "  - Available mounts for Sonarr: /config, /data/tv"
        ),
    },

    # ─── 4. NFS Remote Filesystem ───
    {
        "name": "04-nfs-remote-mount",
        "desc": (
            "All services mount from an NFS share. Volume mounts look correct\n"
            "(shared parent), but hardlinks don't work over NFS between\n"
            "different exports. MapArr should warn about the remote filesystem."
        ),
        "compose": """\
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr:latest
    container_name: sonarr
    volumes:
      - ./config/sonarr:/config
      - nfs-server:/mnt/nas/media/tv:/data/tv
    ports:
      - "8989:8989"
    restart: unless-stopped

  radarr:
    image: lscr.io/linuxserver/radarr:latest
    container_name: radarr
    volumes:
      - ./config/radarr:/config
      - nfs-server:/mnt/nas/media/movies:/data/movies
    ports:
      - "7878:7878"
    restart: unless-stopped

  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent:latest
    container_name: qbittorrent
    volumes:
      - ./config/qbit:/config
      - nfs-server:/mnt/nas/downloads:/downloads
    ports:
      - "8080:8080"
    restart: unless-stopped
""",
        "error": (
            "Hardlink '/downloads/tv-sonarr/Severance.S02E01/Severance.S02E01.mkv' to "
            "'/data/tv/Severance/Season 02/Severance.S02E01.mkv' failed.\n"
            "Mono.Unix.UnixIOException: Invalid cross-device link [EXDEV]"
        ),
        "expect": (
            "MapArr should detect:\n"
            "  - CRITICAL: no_shared_mount (separate host roots)\n"
            "  - HIGH: remote_filesystem warning on NFS paths\n"
            "  - Mount warnings about NFS/hardlink incompatibility"
        ),
    },

    # ─── 5. SMB/CIFS Windows UNC Paths ───
    {
        "name": "05-smb-windows-paths",
        "desc": (
            "Download client on Windows reports UNC paths (\\\\server\\share).\n"
            "The *arr app runs in a Linux container and can't access Windows paths.\n"
            "This triggers a Remote Path Mapping error."
        ),
        "compose": """\
services:
  radarr:
    image: lscr.io/linuxserver/radarr:latest
    container_name: radarr
    volumes:
      - ./config/radarr:/config
      - /mnt/media:/data/media
    ports:
      - "7878:7878"
    restart: unless-stopped

  sabnzbd:
    image: lscr.io/linuxserver/sabnzbd:latest
    container_name: sabnzbd
    volumes:
      - ./config/sabnzbd:/config
      - //MediaNAS/Downloads:/downloads
    ports:
      - "8085:8085"
    restart: unless-stopped
""",
        "error": (
            "The.Shawshank.Redemption.1994.REMASTERED.2160p.UHD.BluRay "
            "[\\\\MediaNAS\\Downloads\\complete\\movies\\The.Shawshank.Redemption\\] "
            "is not a valid local path. You may need a Remote Path Mapping."
        ),
        "expect": (
            "MapArr should detect:\n"
            "  - CRITICAL: no_shared_mount\n"
            "  - HIGH: remote_filesystem on CIFS/SMB path\n"
            "  - Parser should extract: remote_path_mapping error type\n"
            "  - Mount warning about CIFS paths and hardlinks"
        ),
    },

    # ─── 6. Named Volumes Instead of Bind Mounts ───
    {
        "name": "06-named-volumes-wrong",
        "desc": (
            "Services use Docker named volumes instead of bind mounts for data.\n"
            "Named volumes are isolated — each service gets its own copy of the data.\n"
            "Hardlinks between named volumes are impossible.\n"
            "\n"
            "This is a common beginner mistake from following generic Docker tutorials\n"
            "instead of *arr-specific guides."
        ),
        "compose": """\
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr:latest
    container_name: sonarr
    volumes:
      - sonarr_config:/config
      - tv_data:/data/tv
    ports:
      - "8989:8989"
    restart: unless-stopped

  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent:latest
    container_name: qbittorrent
    volumes:
      - qbit_config:/config
      - download_data:/downloads
    ports:
      - "8080:8080"
    restart: unless-stopped

volumes:
  sonarr_config:
  tv_data:
  qbit_config:
  download_data:
""",
        "error": (
            "Import failed, path does not exist or is not accessible by Sonarr: "
            "/downloads/tv-sonarr/Fallout.S01E01.1080p.AMZN.WEB-DL/Fallout.S01E01.mkv"
        ),
        "expect": (
            "MapArr should detect:\n"
            "  - Named volumes for data paths (no bind mounts)\n"
            "  - path_unreachable: Sonarr can't see /downloads\n"
            "  - Recommendation: switch to bind mounts with shared parent"
        ),
    },

    # ─── 7. Single Service (No Download Client) ───
    {
        "name": "07-single-service-only",
        "desc": (
            "Only Sonarr is in this stack. No download client.\n"
            "MapArr can't detect cross-service conflicts with only one service.\n"
            "Should report limited analysis and suggest checking the download\n"
            "client stack separately."
        ),
        "compose": """\
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr:latest
    container_name: sonarr
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
    volumes:
      - ./config:/config
      - /mnt/data/tv:/tv
    ports:
      - "8989:8989"
    restart: unless-stopped
""",
        "error": (
            "Import failed, path does not exist or is not accessible by Sonarr: "
            "/downloads/tv-sonarr/Reacher.S03E01.720p.AMZN.WEB-DL/Reacher.S03E01.mkv"
        ),
        "expect": (
            "MapArr should detect:\n"
            "  - Only one media-related service found\n"
            "  - path_unreachable: /downloads not mounted\n"
            "  - Summary: limited analysis with single service\n"
            "  - No category advisory (no download client detected)"
        ),
    },

    # ─── 8. Healthy Setup (Control Case) ───
    {
        "name": "08-healthy-trash-pattern",
        "desc": (
            "CORRECT SETUP following the TRaSH Guides pattern.\n"
            "All services share one host parent directory mounted as /data.\n"
            "Hardlinks and atomic moves work correctly.\n"
            "\n"
            "MapArr should report this as healthy with no conflicts.\n"
            "This is the control case — verify MapArr doesn't false-positive."
        ),
        "compose": """\
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr:latest
    container_name: sonarr
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
    volumes:
      - ./config/sonarr:/config
      - /srv/data:/data
    ports:
      - "8989:8989"
    restart: unless-stopped

  radarr:
    image: lscr.io/linuxserver/radarr:latest
    container_name: radarr
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
    volumes:
      - ./config/radarr:/config
      - /srv/data:/data
    ports:
      - "7878:7878"
    restart: unless-stopped

  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent:latest
    container_name: qbittorrent
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
    volumes:
      - ./config/qbittorrent:/config
      - /srv/data:/data
    ports:
      - "8080:8080"
    restart: unless-stopped

  plex:
    image: lscr.io/linuxserver/plex:latest
    container_name: plex
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
    volumes:
      - ./config/plex:/config
      - /srv/data/media:/data/media:ro
    ports:
      - "32400:32400"
    restart: unless-stopped
""",
        "error": (
            "Import failed, path does not exist or is not accessible by Sonarr: "
            "/data/torrents/tv-sonarr/Shogun.S01E01.1080p.DSNP.WEB-DL/Shogun.S01E01.mkv"
        ),
        "expect": (
            "MapArr should detect:\n"
            "  - STATUS: healthy — no path conflicts\n"
            "  - All services share /srv/data host root\n"
            "  - Category advisory should appear (has *arr + download client)\n"
            "  - Healthy guidance with actionable checklist\n"
            "  - If error path pasted: may flag path_unreachable for /data/torrents\n"
            "    depending on whether Sonarr's /data mount covers it (it should)"
        ),
    },

    # ─── 9. Partial Overlap ───
    {
        "name": "09-partial-overlap",
        "desc": (
            "Some services share a mount, others don't. Sonarr and Radarr\n"
            "share /srv/data:/data, but qBittorrent mounts a completely\n"
            "different path. The *arr apps can see each other's files but\n"
            "can't hardlink from the download client."
        ),
        "compose": """\
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr:latest
    container_name: sonarr
    volumes:
      - ./config/sonarr:/config
      - /srv/data:/data
    ports:
      - "8989:8989"
    restart: unless-stopped

  radarr:
    image: lscr.io/linuxserver/radarr:latest
    container_name: radarr
    volumes:
      - ./config/radarr:/config
      - /srv/data:/data
    ports:
      - "7878:7878"
    restart: unless-stopped

  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent:latest
    container_name: qbittorrent
    volumes:
      - ./config/qbit:/config
      - /mnt/fast-ssd/downloads:/downloads
    ports:
      - "8080:8080"
    restart: unless-stopped
""",
        "error": (
            "You are using docker; download client qBittorrent places downloads in "
            "/downloads/tv-sonarr/ but this directory does not appear to exist inside "
            "the container. Review your remote path mappings and container volume settings."
        ),
        "expect": (
            "MapArr should detect:\n"
            "  - CRITICAL: no_shared_mount — qBittorrent on different host root\n"
            "  - Sonarr+Radarr share /srv/data, qBit uses /mnt/fast-ssd\n"
            "  - Solution: move qBit's download dir under /srv/data"
        ),
    },

    # ─── 10. Mixed Remote + Local Storage ───
    {
        "name": "10-mixed-remote-local",
        "desc": (
            "Download client saves to local SSD, but media library is on NFS.\n"
            "Even if paths are structured correctly, hardlinks can't cross\n"
            "from local storage to network storage — different filesystems."
        ),
        "compose": """\
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr:latest
    container_name: sonarr
    volumes:
      - ./config/sonarr:/config
      - /mnt/nas/media/tv:/data/tv
      - /mnt/nas/downloads:/data/downloads
    ports:
      - "8989:8989"
    restart: unless-stopped

  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent:latest
    container_name: qbittorrent
    volumes:
      - ./config/qbit:/config
      - /home/user/fast-downloads:/data/downloads
      - /mnt/nas/media:/data/media
    ports:
      - "8080:8080"
    restart: unless-stopped
""",
        "error": (
            "Hardlink '/data/downloads/tv-sonarr/Andor.S02E01/Andor.S02E01.1080p.DSNP.WEB-DL.mkv' to "
            "'/data/tv/Andor/Season 02/Andor.S02E01.1080p.DSNP.WEB-DL.mkv' failed.\n"
            "Mono.Unix.UnixIOException: Invalid cross-device link [EXDEV]"
        ),
        "expect": (
            "MapArr should detect:\n"
            "  - HIGH: different_host_paths — /data/downloads maps to different hosts\n"
            "  - HIGH: remote_filesystem on /mnt/nas paths\n"
            "  - Mount warning: mixed local + NFS prevents hardlinks\n"
            "  - Parser: hardlink_failed error type"
        ),
    },

    # ─── 11. Windows-Style Paths ───
    {
        "name": "11-windows-docker-desktop",
        "desc": (
            "Windows Docker Desktop setup with Windows-style host paths.\n"
            "Uses C:\\ drive paths. Common for users running Docker Desktop\n"
            "on Windows with WSL2 backend."
        ),
        "compose": """\
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr:latest
    container_name: sonarr
    volumes:
      - C:\\DockerContainers\\sonarr\\config:/config
      - C:\\Media\\TV:/tv
    ports:
      - "8989:8989"
    restart: unless-stopped

  radarr:
    image: lscr.io/linuxserver/radarr:latest
    container_name: radarr
    volumes:
      - C:\\DockerContainers\\radarr\\config:/config
      - D:\\Media\\Movies:/movies
    ports:
      - "7878:7878"
    restart: unless-stopped

  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent:latest
    container_name: qbittorrent
    volumes:
      - C:\\DockerContainers\\qbit\\config:/config
      - E:\\Downloads:/downloads
    ports:
      - "8080:8080"
    restart: unless-stopped
""",
        "error": (
            "Import failed, path does not exist or is not accessible by Sonarr: "
            "/downloads/tv-sonarr/The.Last.of.Us.S02E01.1080p.MAX.WEB-DL/The.Last.of.Us.S02E01.mkv"
        ),
        "expect": (
            "MapArr should detect:\n"
            "  - CRITICAL: no_shared_mount — three different drive letters\n"
            "  - Sonarr on C:\\, Radarr on D:\\, qBit on E:\\\n"
            "  - Solution: single shared directory on one drive"
        ),
    },

    # ─── 12. Mega Stack (Multiple Problems) ───
    {
        "name": "12-mega-stack-chaos",
        "desc": (
            "STRESS TEST. Large stack with 8 services and multiple overlapping\n"
            "problems: separate mounts, NFS paths, inconsistent host paths.\n"
            "MapArr should find multiple conflicts and prioritize them."
        ),
        "compose": """\
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr:latest
    container_name: sonarr
    volumes:
      - ./config/sonarr:/config
      - /mnt/nas/tv:/tv
    ports:
      - "8989:8989"
    restart: unless-stopped

  radarr:
    image: lscr.io/linuxserver/radarr:latest
    container_name: radarr
    volumes:
      - ./config/radarr:/config
      - /mnt/nas/movies:/movies
    ports:
      - "7878:7878"
    restart: unless-stopped

  lidarr:
    image: lscr.io/linuxserver/lidarr:latest
    container_name: lidarr
    volumes:
      - ./config/lidarr:/config
      - /home/user/music:/music
    ports:
      - "8686:8686"
    restart: unless-stopped

  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent:latest
    container_name: qbittorrent
    volumes:
      - ./config/qbit:/config
      - /opt/downloads:/downloads
    ports:
      - "8080:8080"
    restart: unless-stopped

  sabnzbd:
    image: lscr.io/linuxserver/sabnzbd:latest
    container_name: sabnzbd
    volumes:
      - ./config/sabnzbd:/config
      - /opt/usenet:/usenet
    ports:
      - "8085:8085"
    restart: unless-stopped

  plex:
    image: lscr.io/linuxserver/plex:latest
    container_name: plex
    volumes:
      - ./config/plex:/config
      - /mnt/nas/tv:/data/tv:ro
      - /mnt/nas/movies:/data/movies:ro
      - /home/user/music:/data/music:ro
    ports:
      - "32400:32400"
    restart: unless-stopped

  overseerr:
    image: lscr.io/linuxserver/overseerr:latest
    container_name: overseerr
    volumes:
      - ./config/overseerr:/config
    ports:
      - "5055:5055"
    restart: unless-stopped

  prowlarr:
    image: lscr.io/linuxserver/prowlarr:latest
    container_name: prowlarr
    volumes:
      - ./config/prowlarr:/config
    ports:
      - "9696:9696"
    restart: unless-stopped
""",
        "error": (
            "Import failed, path does not exist or is not accessible by Sonarr: "
            "/downloads/tv-sonarr/Dune.Prophecy.S01E01.1080p.MAX.WEB-DL/Dune.Prophecy.S01E01.mkv"
        ),
        "expect": (
            "MapArr should detect:\n"
            "  - CRITICAL: no_shared_mount — at least 4 different host roots\n"
            "    /mnt/nas, /home/user, /opt/downloads, /opt/usenet\n"
            "  - HIGH: remote_filesystem on /mnt/nas (NFS-pattern path)\n"
            "  - 8 services found, 6 media-related\n"
            "  - Category advisory (has *arr apps + download clients)\n"
            "  - Solution YAML covering sonarr, radarr, lidarr, qbit, sabnzbd, plex"
        ),
    },
]


# ═══════════════════════════════════════════════════════════════
# Generator
# ═══════════════════════════════════════════════════════════════

def generate_stacks(output_dir: str, reset: bool = False) -> None:
    """Generate all test stacks in the output directory."""
    output_path = Path(output_dir)

    if reset and output_path.exists():
        shutil.rmtree(output_path)
        print(f"Wiped {output_path}")

    output_path.mkdir(parents=True, exist_ok=True)

    for scenario in SCENARIOS:
        stack_dir = output_path / scenario["name"]
        stack_dir.mkdir(parents=True, exist_ok=True)

        # Write docker-compose.yml
        compose_path = stack_dir / "docker-compose.yml"
        compose_path.write_text(scenario["compose"], encoding="utf-8")

        # Write _TEST.txt
        test_doc = _build_test_doc(scenario)
        test_path = stack_dir / "_TEST.txt"
        test_path.write_text(test_doc, encoding="utf-8")

        # Write .env if specified
        if "env" in scenario:
            env_path = stack_dir / ".env"
            env_path.write_text(scenario["env"], encoding="utf-8")

        print(f"  Created: {scenario['name']}/")

    # Write master index
    index = _build_index()
    (output_path / "_INDEX.txt").write_text(index, encoding="utf-8")

    print(f"\nGenerated {len(SCENARIOS)} test stacks in {output_path}/")
    print(f"Master index: {output_path}/_INDEX.txt")
    print(f"\nTo use with MapArr:")
    print(f"  MAPARR_STACKS_PATH={output_path} python -m uvicorn backend.main:app --port 9494")


def _build_test_doc(scenario: dict) -> str:
    """Build the _TEST.txt document for a scenario."""
    lines = [
        "=" * 60,
        f"MAPARR TEST SCENARIO: {scenario['name']}",
        "=" * 60,
        "",
        "WHAT'S WRONG:",
        scenario["desc"],
        "",
        "-" * 60,
        "",
        "ERROR MESSAGE TO PASTE INTO MAPARR:",
        "(Copy everything between the --- lines)",
        "",
        "---",
        scenario["error"],
        "---",
        "",
        "-" * 60,
        "",
        "EXPECTED MAPARR OUTPUT:",
        scenario["expect"],
        "",
        "=" * 60,
    ]
    return "\n".join(lines)


def _build_index() -> str:
    """Build the master _INDEX.txt document."""
    lines = [
        "=" * 60,
        "MAPARR TEST STACKS — MASTER INDEX",
        "=" * 60,
        "",
        f"Total scenarios: {len(SCENARIOS)}",
        "",
        "HOW TO USE:",
        "  1. Start MapArr pointed at this directory",
        "  2. Open each scenario's _TEST.txt",
        "  3. Copy the error message and paste it into MapArr",
        "  4. Select the scenario's stack",
        "  5. Verify MapArr's output matches the expected results",
        "",
        "-" * 60,
        "",
    ]

    for i, scenario in enumerate(SCENARIOS, 1):
        lines.append(f"{i:2d}. {scenario['name']}")
        # First line of description only
        first_line = scenario["desc"].split("\n")[0]
        lines.append(f"    {first_line}")
        lines.append("")

    lines.extend([
        "-" * 60,
        "",
        "DIFFICULTY GUIDE:",
        "  Easy:    01, 03, 06, 07, 08     (clear single problems)",
        "  Medium:  02, 04, 05, 09, 10, 11 (compound or subtle issues)",
        "  Hard:    12                      (chaos stack, multiple problems)",
        "",
        "SCORING:",
        "  For each scenario, check:",
        "  [ ] MapArr detected the correct conflict type(s)",
        "  [ ] Severity level is appropriate",
        "  [ ] Fix recommendation is actionable",
        "  [ ] Solution YAML is copy-pasteable",
        "  [ ] Category advisory appears (when *arr + dl client present)",
        "  [ ] Terminal log shows correct analysis steps",
        "",
        "=" * 60,
    ])
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Generate broken compose stacks for MapArr testing",
    )
    parser.add_argument(
        "--output", "-o",
        default="./test-stacks",
        help="Output directory (default: ./test-stacks)",
    )
    parser.add_argument(
        "--reset", "-r",
        action="store_true",
        help="Wipe and regenerate the output directory",
    )
    args = parser.parse_args()

    print("MapArr Test Stack Generator")
    print("-" * 40)
    generate_stacks(args.output, args.reset)


if __name__ == "__main__":
    main()
