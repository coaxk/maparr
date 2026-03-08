# Image DB — Design Document

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace hardcoded service classification lists and image family definitions with a structured JSON knowledge base that can be seeded from fleet APIs, extended by users, and maintained across releases.

**Architecture:** Two-layer JSON file (families + images) loaded on boot by an `ImageRegistry` class. Seed script pulls from LSIO fleet API and merges with hand-curated entries. Optional user override file for custom/unrecognized images.

**Tech Stack:** Python stdlib only (`json`, `pathlib`). No new dependencies.

---

## Problem

MapArr currently hardcodes ~20 services across four sets in `analyzer.py` (`ARR_APPS`, `DOWNLOAD_CLIENTS`, `MEDIA_SERVERS`, `REQUEST_APPS`) and duplicates them in `parser.py`. Image family intelligence (`IMAGE_FAMILIES`) covers 7 families with UID/GID conventions. Any service or image not in these lists is invisible to role classification and permissions analysis.

This limits MapArr to recognizing a small fraction of the media service ecosystem. LSIO alone publishes 100+ images. Adding a new service requires code changes in multiple files.

## Design

### 1. Data Schema

Two-layer JSON at `data/images.json`:

```json
{
  "version": 1,
  "generated_at": "2026-03-09T12:00:00Z",
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
    "hotio": { "..." : "..." }
  },
  "images": {
    "sonarr": {
      "name": "Sonarr",
      "role": "arr",
      "family": "linuxserver",
      "patterns": ["lscr.io/linuxserver/sonarr", "linuxserver/sonarr", "hotio/sonarr"],
      "keywords": ["sonarr"],
      "hardlink_capable": true,
      "docs_url": "https://docs.linuxserver.io/images/docker-sonarr"
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

**Field definitions:**

| Field | Type | Description |
|-------|------|-------------|
| `version` | int | Schema version for future-proofing |
| `generated_at` | string | ISO timestamp of last generation |
| `families.{key}` | object | Image family defining UID/GID conventions |
| `families.{key}.name` | string | Human-readable family name |
| `families.{key}.uid_env` | string? | Env var for UID (e.g., "PUID") |
| `families.{key}.gid_env` | string? | Env var for GID (e.g., "PGID") |
| `families.{key}.umask_env` | string? | Env var for UMASK |
| `families.{key}.default_uid` | string? | Default UID if env var not set |
| `families.{key}.default_gid` | string? | Default GID if env var not set |
| `families.{key}.needs_puid` | bool | Whether PUID/PGID should be set |
| `images.{key}` | object | Image entry for a specific service |
| `images.{key}.name` | string | Human-readable display name |
| `images.{key}.role` | string | "arr", "download_client", "media_server", "request", "other" |
| `images.{key}.family` | string? | Family key, or null for independent images |
| `images.{key}.patterns` | list[str] | Substrings to match against Docker image string |
| `images.{key}.keywords` | list[str] | Substrings to match against service name (fuzzy fallback) |
| `images.{key}.hardlink_capable` | bool | Participates in hardlink analysis |
| `images.{key}.docs_url` | string? | Link to container documentation |

**Display convention:** When `family` is null, the UI shows "Independent" (not "null" or "unknown"). When `docs_url` is present, render as a clickable link.

### 2. Architecture

**New file:** `backend/image_registry.py`

Single class `ImageRegistry` with these methods:

| Method | Returns | Replaces |
|--------|---------|----------|
| `load(data_dir)` | None (populates internal state) | Module-level set definitions |
| `classify(service_name, image)` | `{role, family_name, name, hardlink_capable, docs_url}` | `_classify_service()` |
| `get_family(image)` | Family dict or None | `_identify_image_family()` |
| `known_keywords()` | `set[str]` (all keywords) | `ALL_KNOWN_SERVICES` in parser |
| `known_by_role(role)` | `set[str]` (keywords for role) | `ARR_APPS`, `DOWNLOAD_CLIENTS`, etc. |

**Matching logic (ordered):**

1. Match image string against `patterns` (substring, case-insensitive) — precise identification
2. Fall back to service name against `keywords` (substring, case-insensitive) — fuzzy classification
3. No match → `{role: "other", family_name: None, name: None, hardlink_capable: False}`

**Internal indexes built on load:**

- `_by_pattern: list[tuple[str, dict]]` — (pattern, image_entry) sorted longest-first for greedy matching
- `_by_keyword: list[tuple[str, dict]]` — (keyword, image_entry) for service name fallback
- `_families: dict[str, dict]` — family key → family data
- `_all_keywords: set[str]` — union of all keywords for parser
- `_keywords_by_role: dict[str, set[str]]` — role → keyword set

**Integration points:**

| File | Change |
|------|--------|
| `analyzer.py` | Delete `ARR_APPS`, `DOWNLOAD_CLIENTS`, `MEDIA_SERVERS`, `REQUEST_APPS`, `IMAGE_FAMILIES`, `_identify_image_family()`. Import registry instance. Update `_classify_service()` to delegate to `registry.classify()`. Update `_build_permission_profile()` to use `registry.get_family()`. Derive `HARDLINK_PARTICIPANTS` dynamically. |
| `parser.py` | Delete `ARR_APPS`, `DOWNLOAD_CLIENTS`, `MEDIA_SERVERS`, `ALL_KNOWN_SERVICES`. Import registry for `known_keywords()`. |
| `pipeline.py` | No direct changes — imports `_classify_service` from analyzer, follows automatically. |
| `main.py` | Initialize `ImageRegistry` on boot. Log stats. Pass to modules or use as singleton. |

### 3. Seed Script

**New file:** `scripts/seed_images.py`

**Workflow:**

1. Pull LSIO fleet manifest from `https://fleet.linuxserver.io/api/v1/images`
2. Classify each LSIO image by name (substring match against known role keywords)
3. Auto-generate `docs_url` for LSIO images (`https://docs.linuxserver.io/images/docker-{name}`)
4. Read `scripts/manual_entries.json` — hand-curated entries for non-LSIO images (Hotio, jlesage, Binhex, official Plex/Jellyfin, independents)
5. Merge: manual entries override auto-generated ones (manual is source of truth for non-LSIO)
6. Write `data/images.json`
7. Print stats: "Generated 156 images (112 LSIO, 44 manual) across 7 families"

**`scripts/manual_entries.json`** uses the same schema as `images.json`. Contains:
- All family definitions (families are always hand-curated)
- Non-LSIO images: Hotio variants, jlesage containers, binhex, official Plex/Jellyfin, Seerr, independents
- Role overrides for LSIO images the auto-classifier gets wrong (if any)

**Run:** `python scripts/seed_images.py` — idempotent, safe to run repeatedly. Output committed to repo before each release.

**No runtime API calls.** MapArr makes zero outbound connections. The seed script is a dev-time tool only.

### 4. User Override

**File:** `data/custom-images.json` (optional)

Users mount it via compose:
```yaml
volumes:
  - ./my-custom-images.json:/data/custom-images.json:ro
```

**Merge rules:**
- User `families` entries override baked-in families by key
- User `images` entries override baked-in images by key
- User can add entirely new entries for custom/self-built images
- Baked-in entries the user doesn't touch are unaffected

**Boot logging:**
```
ImageRegistry: loaded 156 images, 7 families from images.json
ImageRegistry: merged 2 custom entries from custom-images.json
```

**Error handling:**
- Missing `data/images.json` → log error, fall back to minimal hardcoded safety net (current ~20 services embedded in `ImageRegistry` as constants, so MapArr is never fully blind)
- Malformed `custom-images.json` → log warning with error detail, skip overrides, continue with baked-in data
- Missing `custom-images.json` → silent, normal operation

**Documentation:** QUICK_START.md gets a new section explaining:
- What the Image DB does (one paragraph)
- How to add custom images (example JSON snippet showing one family and one image)
- Where to mount the override file
- That overrides persist across MapArr updates

### 5. Testing

**New file:** `tests/test_image_registry.py`

| Test Area | What It Covers |
|-----------|---------------|
| Loading | JSON loads correctly, indexes built, stats accurate |
| Image matching | Exact pattern match, substring, case-insensitive |
| Keyword fallback | Service name matches when image string doesn't |
| Family lookup | Returns correct UID/GID conventions per family |
| Unknown image | Returns role "other", family None, hardlink_capable False |
| Custom overrides | User entry overrides baked-in, new entries added |
| Override errors | Malformed custom file skipped with warning, baked-in preserved |
| Keyword sets | `known_keywords()` returns complete set, `known_by_role()` correct subsets |
| Multi-instance | Two services with same image both classify correctly |
| Hardlink participants | Derived from `hardlink_capable` field across all images |
| Missing DB file | Falls back to hardcoded safety net |
| Display labels | Family null renders as "Independent" |

**Existing tests:** Must continue passing unchanged. The registry produces identical classification results to the current hardcoded sets. Run full suite after integration to confirm.

## What This Does NOT Include

- **Expected mounts per image** — mount analysis is already handled by the 4-pass engine using actual compose data. The Image DB answers "what is this?" not "what should it have?"
- **UI editor for custom images** — file-based overrides are sufficient for v1. Users who need overrides are comfortable editing JSON.
- **Community contribution workflow** — deferred to a future version. The JSON format is contribution-friendly when we're ready.
- **Runtime API calls** — the seed script runs at dev time only. MapArr makes zero outbound connections.

## Migration Path

The hardcoded sets and `IMAGE_FAMILIES` list are deleted, not deprecated. No backwards-compatibility shim needed — the registry is a drop-in replacement that returns the same data. The only user-visible change is that MapArr recognizes more services.
