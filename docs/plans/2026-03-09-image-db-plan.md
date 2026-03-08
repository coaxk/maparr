# Image DB Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace hardcoded service classification lists with a JSON-based Image DB, seeded from the LSIO fleet API and hand-curated entries, with user override support.

**Architecture:** `ImageRegistry` class loads `data/images.json` on boot, builds lookup indexes, provides `classify()` and `get_family()` methods. Seed script pulls from LSIO API, merges with manual entries. Existing hardcoded sets and `IMAGE_FAMILIES` are deleted and replaced with registry calls.

**Tech Stack:** Python 3.11, stdlib only (json, pathlib, urllib). No new dependencies.

**Key files to know before starting:**
- `backend/analyzer.py:43-141` — hardcoded sets (`ARR_APPS`, `DOWNLOAD_CLIENTS`, etc.) and `IMAGE_FAMILIES` to delete
- `backend/analyzer.py:726-746` — `_classify_service()` to replace
- `backend/analyzer.py:1308-1379` — `_identify_image_family()` and `_build_permission_profile()` to rewire
- `backend/parser.py:28-42` — duplicate service lists to delete
- `backend/pipeline.py:25` — imports `_classify_service` from analyzer
- `backend/main.py` — app startup, needs registry initialization
- `tests/conftest.py` — shared test fixtures

**Tests command:** `python -m pytest tests/ -v -p no:capture` (always use `-p no:capture` on Windows)

---

### Task 1: Create manual_entries.json (seed data)

This is the hand-curated source of truth for families and non-LSIO images. The seed script merges this with LSIO fleet data.

**Files:**
- Create: `scripts/manual_entries.json`

**Step 1: Create the manual entries file**

```json
{
  "version": 1,
  "families": {
    "linuxserver": {
      "name": "LinuxServer.io",
      "uid_env": "PUID",
      "gid_env": "PGID",
      "umask_env": "UMASK",
      "default_uid": "911",
      "default_gid": "911",
      "needs_puid": true
    },
    "hotio": {
      "name": "Hotio",
      "uid_env": "PUID",
      "gid_env": "PGID",
      "umask_env": "UMASK",
      "default_uid": "1000",
      "default_gid": "1000",
      "needs_puid": true
    },
    "jlesage": {
      "name": "jlesage",
      "uid_env": "USER_ID",
      "gid_env": "GROUP_ID",
      "umask_env": "UMASK",
      "default_uid": "1000",
      "default_gid": "1000",
      "needs_puid": true
    },
    "binhex": {
      "name": "Binhex",
      "uid_env": "PUID",
      "gid_env": "PGID",
      "umask_env": "UMASK",
      "default_uid": "99",
      "default_gid": "100",
      "needs_puid": true
    },
    "official_plex": {
      "name": "Official Plex",
      "uid_env": "PLEX_UID",
      "gid_env": "PLEX_GID",
      "umask_env": null,
      "default_uid": null,
      "default_gid": null,
      "needs_puid": true
    },
    "official_jellyfin": {
      "name": "Official Jellyfin",
      "uid_env": null,
      "gid_env": null,
      "umask_env": null,
      "default_uid": null,
      "default_gid": null,
      "needs_puid": false
    },
    "seerr": {
      "name": "Seerr",
      "uid_env": null,
      "gid_env": null,
      "umask_env": null,
      "default_uid": null,
      "default_gid": null,
      "needs_puid": false
    }
  },
  "images": {
    "sonarr": {
      "name": "Sonarr",
      "role": "arr",
      "family": "linuxserver",
      "patterns": ["hotio/sonarr", "ghcr.io/hotio/sonarr"],
      "keywords": ["sonarr"],
      "hardlink_capable": true,
      "docs_url": "https://wiki.servarr.com/sonarr"
    },
    "radarr": {
      "name": "Radarr",
      "role": "arr",
      "family": "linuxserver",
      "patterns": ["hotio/radarr", "ghcr.io/hotio/radarr"],
      "keywords": ["radarr"],
      "hardlink_capable": true,
      "docs_url": "https://wiki.servarr.com/radarr"
    },
    "lidarr": {
      "name": "Lidarr",
      "role": "arr",
      "family": "linuxserver",
      "patterns": ["hotio/lidarr", "ghcr.io/hotio/lidarr"],
      "keywords": ["lidarr"],
      "hardlink_capable": true,
      "docs_url": "https://wiki.servarr.com/lidarr"
    },
    "readarr": {
      "name": "Readarr",
      "role": "arr",
      "family": "linuxserver",
      "patterns": ["hotio/readarr", "ghcr.io/hotio/readarr"],
      "keywords": ["readarr"],
      "hardlink_capable": true,
      "docs_url": "https://wiki.servarr.com/readarr"
    },
    "whisparr": {
      "name": "Whisparr",
      "role": "arr",
      "family": "linuxserver",
      "patterns": ["hotio/whisparr", "ghcr.io/hotio/whisparr"],
      "keywords": ["whisparr"],
      "hardlink_capable": true,
      "docs_url": "https://wiki.servarr.com/whisparr"
    },
    "prowlarr": {
      "name": "Prowlarr",
      "role": "arr",
      "family": "linuxserver",
      "patterns": ["hotio/prowlarr", "ghcr.io/hotio/prowlarr"],
      "keywords": ["prowlarr"],
      "hardlink_capable": false,
      "docs_url": "https://wiki.servarr.com/prowlarr"
    },
    "bazarr": {
      "name": "Bazarr",
      "role": "arr",
      "family": "linuxserver",
      "patterns": ["hotio/bazarr", "ghcr.io/hotio/bazarr"],
      "keywords": ["bazarr"],
      "hardlink_capable": false,
      "docs_url": "https://www.bazarr.media/"
    },
    "overseerr": {
      "name": "Overseerr",
      "role": "request",
      "family": "seerr",
      "patterns": ["sctx/overseerr"],
      "keywords": ["overseerr"],
      "hardlink_capable": false,
      "docs_url": "https://docs.overseerr.dev/"
    },
    "jellyseerr": {
      "name": "Jellyseerr",
      "role": "request",
      "family": "seerr",
      "patterns": ["fallenbagel/jellyseerr"],
      "keywords": ["jellyseerr"],
      "hardlink_capable": false,
      "docs_url": "https://github.com/Fallenbagel/jellyseerr"
    },
    "ombi": {
      "name": "Ombi",
      "role": "request",
      "family": "linuxserver",
      "patterns": [],
      "keywords": ["ombi"],
      "hardlink_capable": false,
      "docs_url": "https://docs.ombi.app/"
    },
    "plex": {
      "name": "Plex",
      "role": "media_server",
      "family": "official_plex",
      "patterns": ["plexinc/pms-docker"],
      "keywords": ["plex"],
      "hardlink_capable": true,
      "docs_url": "https://support.plex.tv/"
    },
    "jellyfin": {
      "name": "Jellyfin",
      "role": "media_server",
      "family": "official_jellyfin",
      "patterns": ["jellyfin/jellyfin"],
      "keywords": ["jellyfin"],
      "hardlink_capable": true,
      "docs_url": "https://jellyfin.org/docs/"
    },
    "emby": {
      "name": "Emby",
      "role": "media_server",
      "family": null,
      "patterns": ["emby/embyserver"],
      "keywords": ["emby"],
      "hardlink_capable": true,
      "docs_url": "https://emby.media/support/articles/"
    },
    "qbittorrent": {
      "name": "qBittorrent",
      "role": "download_client",
      "family": "linuxserver",
      "patterns": ["hotio/qbittorrent", "ghcr.io/hotio/qbittorrent"],
      "keywords": ["qbittorrent", "qbit"],
      "hardlink_capable": true,
      "docs_url": "https://github.com/qbittorrent/qBittorrent/wiki"
    },
    "sabnzbd": {
      "name": "SABnzbd",
      "role": "download_client",
      "family": "linuxserver",
      "patterns": ["hotio/sabnzbd", "ghcr.io/hotio/sabnzbd"],
      "keywords": ["sabnzbd", "sab", "nzb"],
      "hardlink_capable": true,
      "docs_url": "https://sabnzbd.org/wiki/"
    },
    "nzbget": {
      "name": "NZBGet",
      "role": "download_client",
      "family": "linuxserver",
      "patterns": ["hotio/nzbget", "ghcr.io/hotio/nzbget"],
      "keywords": ["nzbget"],
      "hardlink_capable": true,
      "docs_url": "https://nzbget.com/documentation/"
    },
    "transmission": {
      "name": "Transmission",
      "role": "download_client",
      "family": "linuxserver",
      "patterns": ["hotio/transmission", "ghcr.io/hotio/transmission"],
      "keywords": ["transmission"],
      "hardlink_capable": true,
      "docs_url": "https://transmissionbt.com/"
    },
    "deluge": {
      "name": "Deluge",
      "role": "download_client",
      "family": "linuxserver",
      "patterns": ["hotio/deluge", "ghcr.io/hotio/deluge"],
      "keywords": ["deluge"],
      "hardlink_capable": true,
      "docs_url": "https://deluge-torrent.org/"
    },
    "rtorrent": {
      "name": "rTorrent",
      "role": "download_client",
      "family": null,
      "patterns": [],
      "keywords": ["rtorrent"],
      "hardlink_capable": true,
      "docs_url": "https://github.com/rakshasa/rtorrent/wiki"
    },
    "jdownloader": {
      "name": "JDownloader",
      "role": "download_client",
      "family": "jlesage",
      "patterns": ["jlesage/jdownloader-2"],
      "keywords": ["jdownloader", "jdown", "jd2"],
      "hardlink_capable": true,
      "docs_url": "https://jdownloader.org/"
    },
    "aria2": {
      "name": "aria2",
      "role": "download_client",
      "family": null,
      "patterns": ["p3terx/aria2-pro"],
      "keywords": ["aria2"],
      "hardlink_capable": true,
      "docs_url": "https://aria2.github.io/"
    },
    "flood": {
      "name": "Flood",
      "role": "download_client",
      "family": null,
      "patterns": ["jesec/flood"],
      "keywords": ["flood"],
      "hardlink_capable": true,
      "docs_url": "https://github.com/jesec/flood"
    },
    "rdtclient": {
      "name": "RDTClient",
      "role": "download_client",
      "family": null,
      "patterns": ["rogerfar/rdtclient"],
      "keywords": ["rdtclient"],
      "hardlink_capable": true,
      "docs_url": "https://github.com/rogerfar/rdt-client"
    }
  }
}
```

Note: LSIO patterns (like `lscr.io/linuxserver/sonarr`) are NOT included here — the seed script auto-generates those from the fleet API. Only non-LSIO patterns go in manual entries.

**Step 2: Commit**

```bash
git add scripts/manual_entries.json
git commit -m "feat: add manual image entries for Image DB seed data"
```

---

### Task 2: Create the seed script

Pulls from LSIO fleet API, merges with manual entries, writes `data/images.json`.

**Files:**
- Create: `scripts/seed_images.py`
- Create: `data/images.json` (output)

**Step 1: Write the seed script**

```python
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

# LSIO images that participate in hardlink analysis.
# Most arr + download_client + media_server images do, but indexers
# (prowlarr) and subtitle tools (bazarr) don't move files.
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
        github_url = img.get("github_url", "")

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
```

**Step 2: Run the seed script**

Run: `python scripts/seed_images.py`
Expected: Creates `data/images.json` with ~150+ entries, prints stats.

**Step 3: Verify the output**

Run: `python -c "import json; d=json.load(open('data/images.json')); print(f'{len(d[\"images\"])} images, {len(d[\"families\"])} families'); print('sonarr role:', d['images']['sonarr']['role'])"`
Expected: Shows image count, families count, and sonarr classified as "arr".

**Step 4: Commit**

```bash
git add scripts/seed_images.py data/images.json
git commit -m "feat: seed script + initial Image DB from LSIO fleet API"
```

---

### Task 3: Create ImageRegistry class

The core class that loads the JSON and provides lookup methods.

**Files:**
- Create: `backend/image_registry.py`
- Create: `tests/test_image_registry.py`

**Step 1: Write the failing tests**

```python
"""
Tests for MapArr Image DB — ImageRegistry.

Covers: loading, image classification, family lookup, keyword sets,
custom overrides, error handling, multi-instance support.
"""

import json
import os

import pytest


# Minimal valid DB for testing (avoids depending on real data/images.json)
MINIMAL_DB = {
    "version": 1,
    "generated_at": "2026-01-01T00:00:00Z",
    "families": {
        "linuxserver": {
            "name": "LinuxServer.io",
            "uid_env": "PUID",
            "gid_env": "PGID",
            "umask_env": "UMASK",
            "default_uid": "911",
            "default_gid": "911",
            "needs_puid": True,
        },
        "jlesage": {
            "name": "jlesage",
            "uid_env": "USER_ID",
            "gid_env": "GROUP_ID",
            "umask_env": "UMASK",
            "default_uid": "1000",
            "default_gid": "1000",
            "needs_puid": True,
        },
    },
    "images": {
        "sonarr": {
            "name": "Sonarr",
            "role": "arr",
            "family": "linuxserver",
            "patterns": ["lscr.io/linuxserver/sonarr", "linuxserver/sonarr"],
            "keywords": ["sonarr"],
            "hardlink_capable": True,
            "docs_url": "https://docs.linuxserver.io/images/docker-sonarr",
        },
        "qbittorrent": {
            "name": "qBittorrent",
            "role": "download_client",
            "family": "linuxserver",
            "patterns": ["lscr.io/linuxserver/qbittorrent"],
            "keywords": ["qbittorrent", "qbit"],
            "hardlink_capable": True,
            "docs_url": None,
        },
        "plex": {
            "name": "Plex",
            "role": "media_server",
            "family": None,
            "patterns": ["plexinc/pms-docker"],
            "keywords": ["plex"],
            "hardlink_capable": True,
            "docs_url": "https://support.plex.tv/",
        },
        "jdownloader": {
            "name": "JDownloader",
            "role": "download_client",
            "family": "jlesage",
            "patterns": ["jlesage/jdownloader-2"],
            "keywords": ["jdownloader", "jdown", "jd2"],
            "hardlink_capable": True,
            "docs_url": None,
        },
        "prowlarr": {
            "name": "Prowlarr",
            "role": "arr",
            "family": "linuxserver",
            "patterns": ["lscr.io/linuxserver/prowlarr"],
            "keywords": ["prowlarr"],
            "hardlink_capable": False,
            "docs_url": None,
        },
        "overseerr": {
            "name": "Overseerr",
            "role": "request",
            "family": None,
            "patterns": ["sctx/overseerr"],
            "keywords": ["overseerr"],
            "hardlink_capable": False,
            "docs_url": None,
        },
    },
}


@pytest.fixture
def db_dir(tmp_path):
    """Create a temp data dir with the minimal DB written to images.json."""
    db_file = tmp_path / "images.json"
    db_file.write_text(json.dumps(MINIMAL_DB), encoding="utf-8")
    return tmp_path


@pytest.fixture
def registry(db_dir):
    """Return a loaded ImageRegistry from the minimal DB."""
    from backend.image_registry import ImageRegistry
    reg = ImageRegistry()
    reg.load(db_dir)
    return reg


# ─── Loading ───

class TestLoading:
    def test_load_counts(self, registry):
        assert registry.image_count >= 6
        assert registry.family_count >= 2

    def test_load_missing_file_uses_fallback(self, tmp_path):
        from backend.image_registry import ImageRegistry
        reg = ImageRegistry()
        reg.load(tmp_path / "nonexistent")
        # Should fall back to hardcoded safety net
        result = reg.classify("sonarr", "linuxserver/sonarr")
        assert result["role"] == "arr"

    def test_load_malformed_json(self, tmp_path):
        bad = tmp_path / "images.json"
        bad.write_text("NOT JSON {{{", encoding="utf-8")
        from backend.image_registry import ImageRegistry
        reg = ImageRegistry()
        reg.load(tmp_path)
        # Should fall back to safety net
        assert reg.image_count > 0


# ─── Image Classification ───

class TestClassify:
    def test_classify_by_image_pattern(self, registry):
        result = registry.classify("my-sonarr", "lscr.io/linuxserver/sonarr:latest")
        assert result["role"] == "arr"
        assert result["name"] == "Sonarr"
        assert result["hardlink_capable"] is True

    def test_classify_by_keyword_fallback(self, registry):
        result = registry.classify("sonarr", "some-custom-registry/sonarr-fork:v4")
        assert result["role"] == "arr"

    def test_classify_case_insensitive(self, registry):
        result = registry.classify("SONARR", "LSCR.IO/LINUXSERVER/SONARR:LATEST")
        assert result["role"] == "arr"

    def test_classify_unknown_image(self, registry):
        result = registry.classify("watchtower", "containrrr/watchtower:latest")
        assert result["role"] == "other"
        assert result["family_name"] is None
        assert result["hardlink_capable"] is False

    def test_classify_download_client(self, registry):
        result = registry.classify("qbit", "lscr.io/linuxserver/qbittorrent:latest")
        assert result["role"] == "download_client"

    def test_classify_media_server(self, registry):
        result = registry.classify("plex", "plexinc/pms-docker:latest")
        assert result["role"] == "media_server"

    def test_classify_request_app(self, registry):
        result = registry.classify("overseerr", "sctx/overseerr:latest")
        assert result["role"] == "request"

    def test_classify_jlesage_family(self, registry):
        result = registry.classify("jd2", "jlesage/jdownloader-2:latest")
        assert result["role"] == "download_client"
        assert result["family_name"] == "jlesage"

    def test_classify_abbreviation_keyword(self, registry):
        """Keywords like 'qbit' should match qbittorrent."""
        result = registry.classify("qbit-downloads", "custom-image:latest")
        assert result["role"] == "download_client"

    def test_classify_returns_docs_url(self, registry):
        result = registry.classify("sonarr", "lscr.io/linuxserver/sonarr:latest")
        assert result["docs_url"] == "https://docs.linuxserver.io/images/docker-sonarr"

    def test_classify_null_docs_url(self, registry):
        result = registry.classify("qbit", "lscr.io/linuxserver/qbittorrent:latest")
        assert result["docs_url"] is None

    def test_multi_instance_same_image(self, registry):
        """Two services using the same image both classify correctly."""
        r1 = registry.classify("sonarr-anime", "lscr.io/linuxserver/sonarr:latest")
        r2 = registry.classify("sonarr-tv", "lscr.io/linuxserver/sonarr:latest")
        assert r1["role"] == "arr"
        assert r2["role"] == "arr"


# ─── Family Lookup ───

class TestFamilyLookup:
    def test_get_family_by_image(self, registry):
        family = registry.get_family("lscr.io/linuxserver/sonarr:latest")
        assert family is not None
        assert family["name"] == "LinuxServer.io"
        assert family["uid_env"] == "PUID"

    def test_get_family_independent(self, registry):
        family = registry.get_family("plexinc/pms-docker:latest")
        # Plex has family=None in our test data
        assert family is None

    def test_get_family_unknown_image(self, registry):
        family = registry.get_family("containrrr/watchtower:latest")
        assert family is None

    def test_get_family_jlesage(self, registry):
        family = registry.get_family("jlesage/jdownloader-2:latest")
        assert family is not None
        assert family["uid_env"] == "USER_ID"
        assert family["gid_env"] == "GROUP_ID"


# ─── Keyword Sets ───

class TestKeywordSets:
    def test_known_keywords_complete(self, registry):
        kw = registry.known_keywords()
        assert "sonarr" in kw
        assert "qbittorrent" in kw
        assert "plex" in kw
        assert "jdownloader" in kw
        # Abbreviations too
        assert "qbit" in kw
        assert "jd2" in kw

    def test_known_by_role_arr(self, registry):
        arr = registry.known_by_role("arr")
        assert "sonarr" in arr
        assert "prowlarr" in arr
        assert "qbittorrent" not in arr

    def test_known_by_role_download(self, registry):
        dl = registry.known_by_role("download_client")
        assert "qbittorrent" in dl
        assert "sonarr" not in dl

    def test_known_by_role_nonexistent(self, registry):
        result = registry.known_by_role("nonexistent_role")
        assert result == set()

    def test_hardlink_participants(self, registry):
        participants = registry.hardlink_participants()
        assert "sonarr" in participants
        assert "qbittorrent" in participants
        assert "plex" in participants
        # prowlarr is NOT a hardlink participant
        assert "prowlarr" not in participants


# ─── Custom Overrides ───

class TestCustomOverrides:
    def test_custom_override_replaces_entry(self, db_dir):
        custom = {
            "version": 1,
            "families": {},
            "images": {
                "sonarr": {
                    "name": "Sonarr Custom",
                    "role": "arr",
                    "family": "linuxserver",
                    "patterns": ["my-registry/sonarr"],
                    "keywords": ["sonarr"],
                    "hardlink_capable": True,
                    "docs_url": "https://custom.example.com",
                },
            },
        }
        (db_dir / "custom-images.json").write_text(json.dumps(custom), encoding="utf-8")

        from backend.image_registry import ImageRegistry
        reg = ImageRegistry()
        reg.load(db_dir)

        result = reg.classify("sonarr", "my-registry/sonarr:latest")
        assert result["name"] == "Sonarr Custom"
        assert result["docs_url"] == "https://custom.example.com"

    def test_custom_adds_new_entry(self, db_dir):
        custom = {
            "version": 1,
            "families": {},
            "images": {
                "myapp": {
                    "name": "My Custom App",
                    "role": "arr",
                    "family": None,
                    "patterns": ["myregistry/myapp"],
                    "keywords": ["myapp"],
                    "hardlink_capable": True,
                    "docs_url": None,
                },
            },
        }
        (db_dir / "custom-images.json").write_text(json.dumps(custom), encoding="utf-8")

        from backend.image_registry import ImageRegistry
        reg = ImageRegistry()
        reg.load(db_dir)

        result = reg.classify("myapp", "myregistry/myapp:latest")
        assert result["role"] == "arr"
        assert result["name"] == "My Custom App"

    def test_custom_adds_new_family(self, db_dir):
        custom = {
            "version": 1,
            "families": {
                "custom_family": {
                    "name": "Custom Family",
                    "uid_env": "MY_UID",
                    "gid_env": "MY_GID",
                    "umask_env": None,
                    "default_uid": "500",
                    "default_gid": "500",
                    "needs_puid": True,
                },
            },
            "images": {
                "myapp": {
                    "name": "My App",
                    "role": "arr",
                    "family": "custom_family",
                    "patterns": ["myregistry/myapp"],
                    "keywords": ["myapp"],
                    "hardlink_capable": True,
                    "docs_url": None,
                },
            },
        }
        (db_dir / "custom-images.json").write_text(json.dumps(custom), encoding="utf-8")

        from backend.image_registry import ImageRegistry
        reg = ImageRegistry()
        reg.load(db_dir)

        family = reg.get_family("myregistry/myapp:latest")
        assert family is not None
        assert family["uid_env"] == "MY_UID"

    def test_malformed_custom_file_skipped(self, db_dir):
        (db_dir / "custom-images.json").write_text("NOT JSON", encoding="utf-8")

        from backend.image_registry import ImageRegistry
        reg = ImageRegistry()
        reg.load(db_dir)

        # Should still work with baked-in data
        result = reg.classify("sonarr", "lscr.io/linuxserver/sonarr:latest")
        assert result["role"] == "arr"

    def test_missing_custom_file_silent(self, db_dir):
        """No custom-images.json should not log errors or fail."""
        from backend.image_registry import ImageRegistry
        reg = ImageRegistry()
        reg.load(db_dir)
        assert reg.image_count >= 6


# ─── Display Labels ───

class TestDisplayLabels:
    def test_family_name_for_known(self, registry):
        result = registry.classify("sonarr", "lscr.io/linuxserver/sonarr:latest")
        assert result["family_name"] == "LinuxServer.io"

    def test_family_name_independent(self, registry):
        result = registry.classify("watchtower", "containrrr/watchtower:latest")
        assert result["family_name"] is None
        # Display code should render None as "Independent"
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_image_registry.py -v -p no:capture`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.image_registry'`

**Step 3: Write the ImageRegistry implementation**

```python
"""
image_registry.py — MapArr Image DB.

Loads a JSON knowledge base of Docker images and provides lookup methods
for role classification, image family identification, and keyword sets.

Two-layer data model:
  - Families: UID/GID conventions (LinuxServer.io, Hotio, jlesage, etc.)
  - Images: role + family + matching patterns + keywords

Data sources:
  - data/images.json — baked-in, generated by scripts/seed_images.py
  - data/custom-images.json — optional user overrides (mounted via compose)

This module replaces the hardcoded ARR_APPS, DOWNLOAD_CLIENTS, MEDIA_SERVERS
sets and IMAGE_FAMILIES list that were previously in analyzer.py.
"""

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("maparr.registry")

# Hardcoded safety net — if images.json is missing or corrupt, MapArr
# can still classify the most common services. This is the minimum viable
# set, not the full DB. Users should never hit this in normal operation.
_FALLBACK_IMAGES = {
    "sonarr": {"name": "Sonarr", "role": "arr", "keywords": ["sonarr"]},
    "radarr": {"name": "Radarr", "role": "arr", "keywords": ["radarr"]},
    "lidarr": {"name": "Lidarr", "role": "arr", "keywords": ["lidarr"]},
    "readarr": {"name": "Readarr", "role": "arr", "keywords": ["readarr"]},
    "whisparr": {"name": "Whisparr", "role": "arr", "keywords": ["whisparr"]},
    "prowlarr": {"name": "Prowlarr", "role": "arr", "keywords": ["prowlarr"]},
    "bazarr": {"name": "Bazarr", "role": "arr", "keywords": ["bazarr"]},
    "overseerr": {"name": "Overseerr", "role": "request", "keywords": ["overseerr"]},
    "jellyseerr": {"name": "Jellyseerr", "role": "request", "keywords": ["jellyseerr"]},
    "ombi": {"name": "Ombi", "role": "request", "keywords": ["ombi"]},
    "qbittorrent": {"name": "qBittorrent", "role": "download_client", "keywords": ["qbittorrent", "qbit"]},
    "sabnzbd": {"name": "SABnzbd", "role": "download_client", "keywords": ["sabnzbd", "sab", "nzb"]},
    "nzbget": {"name": "NZBGet", "role": "download_client", "keywords": ["nzbget"]},
    "transmission": {"name": "Transmission", "role": "download_client", "keywords": ["transmission"]},
    "deluge": {"name": "Deluge", "role": "download_client", "keywords": ["deluge"]},
    "rtorrent": {"name": "rTorrent", "role": "download_client", "keywords": ["rtorrent"]},
    "jdownloader": {"name": "JDownloader", "role": "download_client", "keywords": ["jdownloader", "jdown", "jd2"]},
    "aria2": {"name": "aria2", "role": "download_client", "keywords": ["aria2"]},
    "flood": {"name": "Flood", "role": "download_client", "keywords": ["flood"]},
    "rdtclient": {"name": "RDTClient", "role": "download_client", "keywords": ["rdtclient"]},
    "plex": {"name": "Plex", "role": "media_server", "keywords": ["plex"]},
    "jellyfin": {"name": "Jellyfin", "role": "media_server", "keywords": ["jellyfin"]},
    "emby": {"name": "Emby", "role": "media_server", "keywords": ["emby"]},
}

# Roles that participate in hardlink analysis (for fallback mode)
_HARDLINK_ROLES = {"arr", "download_client", "media_server"}
_NON_HARDLINK_FALLBACK = {"prowlarr", "bazarr"}


class ImageRegistry:
    """Docker image knowledge base with role classification and family lookup."""

    def __init__(self):
        self._families: dict[str, dict] = {}
        self._images: dict[str, dict] = {}
        # Indexes built on load
        self._by_pattern: list[tuple[str, dict]] = []
        self._by_keyword: list[tuple[str, dict]] = []
        self._all_keywords: set[str] = set()
        self._keywords_by_role: dict[str, set[str]] = {}
        self._hardlink_keywords: set[str] = set()
        self._loaded = False

    @property
    def image_count(self) -> int:
        return len(self._images)

    @property
    def family_count(self) -> int:
        return len(self._families)

    def load(self, data_dir: Path | str) -> None:
        """Load the Image DB from data_dir/images.json.

        Optionally merges data_dir/custom-images.json if it exists.
        Falls back to hardcoded safety net if the main file is missing or corrupt.
        """
        data_dir = Path(data_dir)
        main_file = data_dir / "images.json"
        custom_file = data_dir / "custom-images.json"

        # Load main DB
        data = self._read_json(main_file)
        if data is None:
            logger.warning("Image DB not found at %s — using fallback data", main_file)
            self._load_fallback()
            return

        self._families = data.get("families", {})
        self._images = data.get("images", {})

        # Merge custom overrides
        if custom_file.exists():
            custom = self._read_json(custom_file)
            if custom:
                custom_families = custom.get("families", {})
                custom_images = custom.get("images", {})
                self._families.update(custom_families)
                self._images.update(custom_images)
                total_custom = len(custom_families) + len(custom_images)
                logger.info("ImageRegistry: merged %d custom entries from custom-images.json",
                            total_custom)

        self._build_indexes()
        self._loaded = True
        logger.info("ImageRegistry: loaded %d images, %d families",
                     self.image_count, self.family_count)

    def _read_json(self, path: Path) -> Optional[dict]:
        """Read and parse a JSON file. Returns None on any error."""
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                logger.warning("Image DB at %s is not a JSON object", path)
                return None
            return data
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to read Image DB at %s: %s", path, e)
            return None

    def _load_fallback(self) -> None:
        """Load the hardcoded safety net when images.json is unavailable."""
        self._families = {}
        self._images = {}
        for key, entry in _FALLBACK_IMAGES.items():
            self._images[key] = {
                "name": entry["name"],
                "role": entry["role"],
                "family": None,
                "patterns": [],
                "keywords": entry["keywords"],
                "hardlink_capable": (
                    entry["role"] in _HARDLINK_ROLES and key not in _NON_HARDLINK_FALLBACK
                ),
                "docs_url": None,
            }
        self._build_indexes()
        self._loaded = True
        logger.info("ImageRegistry: fallback mode — %d images, 0 families", self.image_count)

    def _build_indexes(self) -> None:
        """Build lookup indexes from loaded data."""
        self._by_pattern = []
        self._by_keyword = []
        self._all_keywords = set()
        self._keywords_by_role = {}
        self._hardlink_keywords = set()

        for _key, entry in self._images.items():
            role = entry.get("role", "other")
            keywords = entry.get("keywords", [])
            hardlink = entry.get("hardlink_capable", False)

            # Pattern index (sorted longest-first for greedy matching)
            for pattern in entry.get("patterns", []):
                self._by_pattern.append((pattern.lower(), entry))

            # Keyword index
            for kw in keywords:
                kw_lower = kw.lower()
                self._by_keyword.append((kw_lower, entry))
                self._all_keywords.add(kw_lower)

                # Keywords by role
                if role not in self._keywords_by_role:
                    self._keywords_by_role[role] = set()
                self._keywords_by_role[role].add(kw_lower)

                # Hardlink participants
                if hardlink:
                    self._hardlink_keywords.add(kw_lower)

        # Sort patterns longest-first so more specific patterns match first
        self._by_pattern.sort(key=lambda x: -len(x[0]))

    def classify(self, service_name: str, image: str) -> dict:
        """Classify a Docker service by its role in the media stack.

        Tries image string pattern matching first (precise), then falls
        back to service name keyword matching (fuzzy).

        Returns a dict with: role, family_name, name, hardlink_capable, docs_url
        """
        image_lower = image.lower() if image else ""
        name_lower = service_name.lower() if service_name else ""

        # Pass 1: match image string against patterns
        for pattern, entry in self._by_pattern:
            if pattern in image_lower:
                return self._make_result(entry)

        # Pass 2: match service name against keywords
        for kw, entry in self._by_keyword:
            if kw in name_lower:
                return self._make_result(entry)

        # Pass 3: match image string against keywords (catches custom registries)
        for kw, entry in self._by_keyword:
            if kw in image_lower:
                return self._make_result(entry)

        # No match
        return {
            "role": "other",
            "family_name": None,
            "name": None,
            "hardlink_capable": False,
            "docs_url": None,
        }

    def _make_result(self, entry: dict) -> dict:
        """Build a classification result dict from an image entry."""
        family_key = entry.get("family")
        family = self._families.get(family_key) if family_key else None
        return {
            "role": entry.get("role", "other"),
            "family_name": family["name"] if family else None,
            "name": entry.get("name"),
            "hardlink_capable": entry.get("hardlink_capable", False),
            "docs_url": entry.get("docs_url"),
        }

    def get_family(self, image: str) -> Optional[dict]:
        """Look up the image family for a Docker image string.

        Returns the family dict (with uid_env, gid_env, etc.) or None.
        """
        image_lower = image.lower() if image else ""

        # Match image against patterns to find the entry
        for pattern, entry in self._by_pattern:
            if pattern in image_lower:
                family_key = entry.get("family")
                if family_key:
                    return self._families.get(family_key)
                return None

        return None

    def known_keywords(self) -> set[str]:
        """Return all known service keywords (for parser recognition)."""
        return set(self._all_keywords)

    def known_by_role(self, role: str) -> set[str]:
        """Return keywords for a specific role."""
        return set(self._keywords_by_role.get(role, set()))

    def hardlink_participants(self) -> set[str]:
        """Return keywords for services that participate in hardlink analysis."""
        return set(self._hardlink_keywords)
```

**Step 4: Run tests**

Run: `python -m pytest tests/test_image_registry.py -v -p no:capture`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add backend/image_registry.py tests/test_image_registry.py
git commit -m "feat: ImageRegistry class with full test coverage"
```

---

### Task 4: Integrate registry into analyzer.py

Replace hardcoded sets and IMAGE_FAMILIES with registry calls.

**Files:**
- Modify: `backend/analyzer.py:43-141` (delete hardcoded data)
- Modify: `backend/analyzer.py:726-746` (`_classify_service`)
- Modify: `backend/analyzer.py:1308-1379` (`_identify_image_family`, `_build_permission_profile`)
- Modify: `backend/main.py` (initialize registry)

**Step 1: Initialize registry in main.py**

Add after the `VERSION` line (~line 93 in main.py):

```python
from backend.image_registry import ImageRegistry

# ─── Image Registry ───
# Load the Image DB on startup. This replaces the hardcoded service
# classification sets and IMAGE_FAMILIES list in analyzer.py.
DATA_DIR = Path(__file__).parent.parent / "data"
registry = ImageRegistry()
registry.load(DATA_DIR)
```

**Step 2: Rewire analyzer.py**

Delete lines 43-141 (the hardcoded sets, `ImageFamily` dataclass, and `IMAGE_FAMILIES` list).

Replace them with:

```python
# ─── Image Registry ───
# Service classification and image family intelligence is provided by the
# ImageRegistry, loaded from data/images.json on boot. See image_registry.py.
# The registry instance is initialized in main.py and accessed here via import.

def _get_registry():
    """Get the global ImageRegistry instance.

    Imported lazily to avoid circular imports (main.py imports analyzer,
    analyzer needs registry which is initialized in main.py).
    """
    from backend.main import registry
    return registry


# Convenience accessors — used by pipeline.py and other modules that
# previously imported the hardcoded sets directly.
def _classify_service(name: str, image: str) -> str:
    """Classify a service by its role in the media stack."""
    result = _get_registry().classify(name, image)
    return result["role"]


def _is_hardlink_participant(name: str, image: str) -> bool:
    """Check if a service participates in hardlink analysis."""
    result = _get_registry().classify(name, image)
    return result["hardlink_capable"]
```

Replace the `HARDLINK_PARTICIPANTS` usage throughout analyzer.py. Find where it's used:

```python
# Old: if role in ("arr", "download_client", "media_server"):
#      or: if name_lower in HARDLINK_PARTICIPANTS
# New: if _is_hardlink_participant(name, image):
```

Replace `_identify_image_family()` (delete lines 1308-1319) with:

```python
def _identify_image_family(image: str):
    """Identify which image family a Docker image belongs to.

    Returns a family-like object with uid_env, gid_env, etc. fields,
    or None for unrecognized/independent images.
    """
    family_dict = _get_registry().get_family(image)
    if family_dict is None:
        return None
    # Return a SimpleNamespace so existing code using family.uid_env still works
    from types import SimpleNamespace
    return SimpleNamespace(**family_dict)
```

Note: `_build_permission_profile()` accesses `family.uid_env`, `family.gid_env`, etc. via attribute access. Using `SimpleNamespace` preserves this interface without changing 50+ lines of permission code.

**Step 3: Run tests**

Run: `python -m pytest tests/ -v -p no:capture`
Expected: ALL 515+ tests PASS (existing behavior preserved)

**Step 4: Commit**

```bash
git add backend/analyzer.py backend/main.py
git commit -m "feat: integrate ImageRegistry into analyzer, delete hardcoded sets"
```

---

### Task 5: Integrate registry into parser.py

Replace the duplicate service lists with registry lookups.

**Files:**
- Modify: `backend/parser.py:28-42` (delete lists)
- Modify: `backend/parser.py:186-213` (`_extract_service`)

**Step 1: Replace hardcoded lists**

Delete lines 28-42 (the `ARR_APPS`, `DOWNLOAD_CLIENTS`, `MEDIA_SERVERS`, `ALL_KNOWN_SERVICES` lists).

Replace with:

```python
def _get_known_services() -> list[str]:
    """Get all known service keywords from the Image Registry.

    Lazy import to avoid circular dependency (registry initialized in main.py).
    Falls back to a minimal hardcoded list if the registry isn't loaded yet
    (e.g., during unit tests that import parser directly).
    """
    try:
        from backend.main import registry
        return sorted(registry.known_keywords())
    except (ImportError, AttributeError):
        # Fallback for tests or standalone usage
        return [
            "sonarr", "radarr", "lidarr", "readarr", "whisparr",
            "prowlarr", "bazarr", "overseerr", "jellyseerr",
            "qbittorrent", "sabnzbd", "nzbget", "transmission",
            "deluge", "rtorrent", "jdownloader", "plex", "jellyfin", "emby",
        ]
```

**Step 2: Update `_extract_service()`**

Replace the `ALL_KNOWN_SERVICES` iteration with:

```python
def _extract_service(text: str) -> Optional[str]:
    """Extract a known service name from error text."""
    text_lower = text.lower()

    # Check against all known services from the Image DB
    for service in _get_known_services():
        if service in text_lower:
            return service

    # Common abbreviations and typos (these are parsing shortcuts,
    # not service names — they map to the canonical service keyword)
    abbreviations = {
        "qbit": "qbittorrent",
        "sab": "sabnzbd",
        "nzb": "sabnzbd",
        "jdown": "jdownloader",
        "jd2": "jdownloader",
    }
    for abbrev, full_name in abbreviations.items():
        if abbrev in text_lower:
            return full_name

    return None
```

Note: The abbreviations dict can stay — these are parsing shortcuts that aren't worth adding as keywords to every Image DB entry. The registry's keywords handle the primary matches; abbreviations are a parser-specific concern.

Actually, wait — we already added `qbit`, `sab`, `nzb`, `jdown`, `jd2` as keywords in the Image DB entries. So `_get_known_services()` will return them, and the abbreviations dict is now redundant. We should **remove the abbreviations dict** and let the registry handle everything. But we need to keep the `return full_name` mapping — when we match `qbit`, we should return `qbittorrent` as the canonical service name. The registry's `classify()` does this, but `_extract_service()` just returns the matched keyword string.

Better approach: keep the abbreviations dict as a **canonical name resolver**. Match from `_get_known_services()` first, then use abbreviations for the mapping:

```python
def _extract_service(text: str) -> Optional[str]:
    """Extract a known service name from error text."""
    text_lower = text.lower()

    # Canonical name map: abbreviation → primary service name.
    # When a keyword like "qbit" matches, we return "qbittorrent" so
    # downstream code sees the standard name.
    _CANONICAL = {
        "qbit": "qbittorrent",
        "sab": "sabnzbd",
        "nzb": "sabnzbd",
        "jdown": "jdownloader",
        "jd2": "jdownloader",
    }

    for service in _get_known_services():
        if service in text_lower:
            return _CANONICAL.get(service, service)

    return None
```

**Step 3: Run tests**

Run: `python -m pytest tests/ -v -p no:capture`
Expected: ALL PASS

**Step 4: Commit**

```bash
git add backend/parser.py
git commit -m "feat: parser uses ImageRegistry for service recognition"
```

---

### Task 6: Update pipeline.py imports

Pipeline imports `_classify_service` and the hardcoded sets from analyzer. After Task 4, these are still exported from analyzer (just delegating to registry), but we should clean up the set imports.

**Files:**
- Modify: `backend/pipeline.py:25`

**Step 1: Update the import**

Change line 25 from:
```python
from backend.analyzer import _classify_service, ARR_APPS, DOWNLOAD_CLIENTS, MEDIA_SERVERS
```
to:
```python
from backend.analyzer import _classify_service
```

Then find any usage of `ARR_APPS`, `DOWNLOAD_CLIENTS`, or `MEDIA_SERVERS` in pipeline.py and replace with registry calls or remove if unused.

**Step 2: Run tests**

Run: `python -m pytest tests/ -v -p no:capture`
Expected: ALL PASS

**Step 3: Commit**

```bash
git add backend/pipeline.py
git commit -m "refactor: clean up pipeline imports after Image DB migration"
```

---

### Task 7: Update QUICK_START.md with custom images documentation

**Files:**
- Modify: `QUICK_START.md`

**Step 1: Add custom images section**

Add a new section after the existing environment variables or troubleshooting section:

```markdown
## Custom Image Recognition

MapArr ships with a database of 150+ Docker images it recognizes automatically. If you run a custom or self-built image that MapArr doesn't classify correctly, you can add a custom override file.

Create a `custom-images.json` file and mount it into the container:

\`\`\`yaml
volumes:
  - ./custom-images.json:/data/custom-images.json:ro
\`\`\`

Example `custom-images.json`:

\`\`\`json
{
  "version": 1,
  "families": {},
  "images": {
    "my-custom-arr": {
      "name": "My Custom Arr App",
      "role": "arr",
      "family": "linuxserver",
      "patterns": ["myregistry/my-custom-arr"],
      "keywords": ["my-custom-arr"],
      "hardlink_capable": true,
      "docs_url": "https://github.com/me/my-custom-arr"
    }
  }
}
\`\`\`

**Fields:**
- `role`: `"arr"`, `"download_client"`, `"media_server"`, `"request"`, or `"other"`
- `family`: `"linuxserver"`, `"hotio"`, `"jlesage"`, `"binhex"`, or `null` for independent images
- `patterns`: substrings to match against the Docker image string
- `keywords`: substrings to match against the service name
- `hardlink_capable`: whether this service participates in hardlink analysis

Your custom file persists across MapArr updates — it's your file, not ours.
```

**Step 2: Commit**

```bash
git add QUICK_START.md
git commit -m "docs: add custom image override documentation to QUICK_START"
```

---

### Task 8: Update docker-compose.yml with custom images mount

**Files:**
- Modify: `docker-compose.yml`

**Step 1: Add commented-out custom images volume**

In the volumes section of docker-compose.yml, add a commented example:

```yaml
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - /path/to/your/stacks:/stacks:ro
      # Optional: custom image overrides (see QUICK_START.md)
      # - ./custom-images.json:/data/custom-images.json:ro
```

**Step 2: Commit**

```bash
git add docker-compose.yml
git commit -m "docs: add custom images mount example to docker-compose.yml"
```

---

### Task 9: Final verification

**Step 1: Run the full test suite**

Run: `python -m pytest tests/ -v -p no:capture`
Expected: ALL tests PASS (515+ existing + new image_registry tests)

**Step 2: Verify the seed script**

Run: `python scripts/seed_images.py`
Expected: Generates fresh `data/images.json` with stats printed

**Step 3: Verify the app starts**

Run: `python -m uvicorn backend.main:app --host 0.0.0.0 --port 9494`
Expected: Starts with log lines:
```
ImageRegistry: loaded N images, 7 families
MapArr v1.5.0 starting up
```

**Step 4: Final commit (if any fixups needed)**

```bash
git add -A
git commit -m "fix: final Image DB integration fixups"
```
