#!/usr/bin/env python3
"""
Seed script for MapArr Image DB.

Pulls the LinuxServer.io fleet manifest, classifies each image by role,
merges with hand-curated entries from manual_entries.json, and writes
the final data/images.json.

Run: python scripts/seed_images.py
Output: data/images.json

This is a dev-time tool. MapArr makes zero runtime API calls.
"""

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
MANUAL_ENTRIES = SCRIPT_DIR / "manual_entries.json"
OUTPUT_FILE = PROJECT_ROOT / "data" / "images.json"

LSIO_API = "https://api.linuxserver.io/api/v1/images"

# Role classification keywords for LSIO images.
# The LSIO fleet API doesn't include role data, so we classify by name.
ROLE_KEYWORDS = {
    "arr": [
        "sonarr", "radarr", "lidarr", "readarr", "whisparr",
        "prowlarr", "bazarr", "mylar3", "kapowarr",
    ],
    "download_client": [
        "qbittorrent", "sabnzbd", "nzbget", "transmission", "deluge",
        "rtorrent", "jdownloader", "aria2", "flood",
    ],
    "media_server": [
        "plex", "jellyfin", "emby",
    ],
    "request": [
        "overseerr", "jellyseerr", "ombi", "petio",
    ],
}

# LSIO images that do NOT participate in hardlink analysis.
NON_HARDLINK = {"prowlarr", "bazarr", "mylar3", "kapowarr", "overseerr", "jellyseerr", "ombi", "petio"}


def classify_lsio_image(name: str) -> str:
    """Classify an LSIO image name into a role."""
    name_lower = name.lower()
    for role, keywords in ROLE_KEYWORDS.items():
        for kw in keywords:
            if kw in name_lower:
                return role
    return "other"


def fetch_lsio_fleet() -> list[dict]:
    """Fetch the LSIO fleet manifest from their API."""
    print(f"Fetching LSIO fleet from {LSIO_API}...")
    try:
        req = urllib.request.Request(LSIO_API, headers={"User-Agent": "MapArr-Seed/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"ERROR: Failed to fetch LSIO fleet: {e}")
        print("Continuing with manual entries only.")
        return []

    repos = data.get("data", {}).get("repositories", {})
    images = repos.get("linuxserver", [])
    # Filter out deprecated images
    active = [img for img in images if not img.get("deprecated", False)]
    print(f"  Found {len(active)} active LSIO images ({len(images) - len(active)} deprecated, skipped)")
    return active


def build_lsio_entries(fleet: list[dict]) -> dict:
    """Convert LSIO fleet data into Image DB entries."""
    entries = {}
    for img in fleet:
        name = img.get("name", "").lower().strip()
        if not name:
            continue

        role = classify_lsio_image(name)
        is_hardlink = role in ("arr", "download_client", "media_server") and name not in NON_HARDLINK

        docs_url = f"https://docs.linuxserver.io/images/docker-{name}"

        entries[name] = {
            "name": name.replace("-", " ").title(),
            "role": role,
            "family": "linuxserver",
            "patterns": [
                f"lscr.io/linuxserver/{name}",
                f"linuxserver/{name}",
                f"ghcr.io/linuxserver/{name}",
            ],
            "keywords": [name],
            "hardlink_capable": is_hardlink,
            "docs_url": docs_url,
        }

    return entries


def load_manual_entries() -> dict:
    """Load hand-curated entries from manual_entries.json."""
    if not MANUAL_ENTRIES.exists():
        print(f"WARNING: {MANUAL_ENTRIES} not found, using LSIO data only")
        return {"families": {}, "images": {}}

    with open(MANUAL_ENTRIES, "r", encoding="utf-8") as f:
        data = json.load(f)

    print(f"  Loaded {len(data.get('images', {}))} manual image entries, "
          f"{len(data.get('families', {}))} families")
    return data


def merge_entries(lsio: dict, manual: dict) -> dict:
    """Merge LSIO auto-generated entries with manual entries.

    Manual entries win on conflict (they're hand-curated and more precise).
    For LSIO images that also have manual entries, merge the patterns lists
    so both LSIO and non-LSIO patterns are included.
    """
    merged = dict(lsio)

    for key, entry in manual.items():
        if key in merged:
            # Merge patterns: LSIO patterns + manual patterns, deduplicated
            existing_patterns = set(merged[key].get("patterns", []))
            manual_patterns = set(entry.get("patterns", []))
            all_patterns = sorted(existing_patterns | manual_patterns)

            # Manual entry wins for all other fields
            merged[key] = dict(entry)
            merged[key]["patterns"] = all_patterns
        else:
            merged[key] = entry

    return merged


def main():
    print("MapArr Image DB Seed Script")
    print("=" * 40)

    # Step 1: Fetch LSIO fleet
    fleet = fetch_lsio_fleet()
    lsio_entries = build_lsio_entries(fleet)

    # Step 2: Load manual entries
    manual = load_manual_entries()

    # Step 3: Merge (manual wins)
    merged_images = merge_entries(lsio_entries, manual.get("images", {}))

    # Step 4: Build output
    output = {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "families": manual.get("families", {}),
        "images": dict(sorted(merged_images.items())),
    }

    # Step 5: Write output
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8", newline="") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
        f.write("\n")

    # Stats
    role_counts = {}
    for entry in merged_images.values():
        role = entry.get("role", "other")
        role_counts[role] = role_counts.get(role, 0) + 1

    lsio_only = len(lsio_entries) - len(set(lsio_entries) & set(manual.get("images", {})))
    manual_only = len(manual.get("images", {}))

    print()
    print(f"Generated {OUTPUT_FILE}")
    print(f"  Total: {len(merged_images)} images across {len(manual.get('families', {}))} families")
    print(f"  Sources: {lsio_only} LSIO-only, {manual_only} manual/merged")
    print(f"  Roles: {', '.join(f'{role}={count}' for role, count in sorted(role_counts.items()))}")


if __name__ == "__main__":
    main()
