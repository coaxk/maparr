# Pipeline Dashboard — Design Document

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the stack-grid UI with a service-first Pipeline Dashboard that shows all media services grouped by role, supports multi-file fixes across separate compose files, and optionally redeploys containers via the Docker API.

**Architecture:** Frontend rewrite (app.js, index.html, styles.css) with targeted backend additions (multi-file apply endpoint, redeploy endpoint). The pipeline scan engine, 4-pass analysis, Image DB, fix generation, and error parser carry forward unchanged.

**Tech Stack:** Same as current — Python/FastAPI backend, vanilla JS frontend, no new dependencies.

---

## Problem

MapArr's current UI assumes one compose file = one stack. Users select a stack, analyze it, fix it. But most real-world Docker deployments (Dockhand, Portainer, DockSTARTer) use **one service per folder** — sonarr in `sonarr/`, qbittorrent in `qbittorrent/`, each with their own compose file.

The pipeline engine already handles this — it discovers services across all subfolders and detects cross-folder conflicts. But the UI can't express it. Apply Fix patches one file, when the fix often spans three or four. The user is left copying shell commands and manually coordinating.

This isn't a missing feature — it's a fundamental mismatch between the UI model (file-first) and reality (service-first).

## Design

### 1. Pipeline Dashboard Layout

The stack-grid landing page is replaced with a **Pipeline Dashboard** — a single unified view of all media services, grouped by role.

**Layout (top to bottom):**

| Element | Description |
|---------|-------------|
| **Header** | `MapArr | 📂 /stacks ▾ | ● N services` — path selector always visible |
| **Health Banner** | Overall status: `● Healthy` or `⚠ N issues found` + `Fix All` CTA |
| **Role Groups** | Services grouped by: Arr Apps, Download Clients, Media Servers, Request Apps, Other |
| **Service Rows** | Per service: name, family badge, mount summary, source file, health dot |
| **Error Paste Bar** | Persistent at bottom, always visible, gated until pipeline is ready |

**What's removed:**
- Browse Mode / Fix Mode toggle
- Stack card grid
- Stack selection step

**What's preserved:**
- Error paste (repositioned, same parsing logic)
- All analysis results (same 4-pass engine)
- Health dot indicators (same severity model)

### 2. Service Drill-Down

Clicking a service row **expands it inline** (accordion, not page navigation):

- Image: full image string + tag
- Family: name + UID/GID env var convention (from Image DB)
- Permissions: UID, GID, UMASK values
- File: path to compose file (e.g., `sonarr/docker-compose.yml`)
- Volumes: each mount with health indicator (`● shared root` or `⚠ isolated`)
- Pipeline context: "shares mount root with N siblings" or "isolated from pipeline"

Clicking a second service collapses the first (single-expand accordion).

### 3. Conflict Cards

When the pipeline detects issues, **conflict cards** appear between the role groups, visually connecting affected services:

- Severity badge (CRITICAL / HIGH / MEDIUM / LOW)
- Plain-English description of the problem
- List of affected services
- **Fix Plan**: per-file diffs showing what changes in each compose file
- Checkbox per file: ticks as fixes are applied
- "No change" rows for files already correct (greyed out — full picture, not just what's broken)
- **Apply** per-file or **Apply All Changes** button

**Checkbox progress tracking:**
- As fixes are applied: checkbox ticks, service health dot updates, health banner count decrements
- Three-state per service: `● healthy` | `⚠ issue` | `🔄 fix applied, awaiting rescan`

### 4. Multi-File Apply Fix

The core feature gap this design solves.

**Current:** `_patch_original_yaml()` patches one compose file. `POST /api/apply-fix` writes one file.

**New:** The fix plan identifies all affected compose files. Each file gets its own patched YAML. The API supports applying fixes to multiple files in one request.

**Backend changes:**
- New endpoint: `POST /api/apply-fixes` (plural) — accepts a list of `{compose_file_path, corrected_yaml}` pairs
- Each file gets a `.bak` backup before writing
- Atomic-ish: if any file fails validation, none are written (validate all first, then write all)
- Returns per-file success/failure status
- Same security: all paths validated within stacks root, all filenames in COMPOSE_FILENAMES whitelist

**Frontend changes:**
- Fix plan UI generates patched YAML per file using existing `_patch_original_yaml()` logic
- "Apply" button sends single file, "Apply All" sends the batch
- Progress updates per file as responses come back

### 5. Redeploy via Docker API

After Apply Fix, offer optional Docker-managed restart.

**Two buttons, clear choice:**
- **[Redeploy Now]** — runs `docker compose up -d` for affected services via Docker socket
- **[I'll do it myself]** — shows copy-paste commands for manual redeploy

**Risk awareness (per-service hints based on role):**

| Role | Warning |
|------|---------|
| download_client | "Active downloads will be interrupted" |
| arr | "Will stop monitoring and importing" |
| media_server | "Active streams will disconnect" |
| request | "Request UI will be briefly unavailable" |
| other | "Service will restart" |

**Safety messaging:**
- "Services restart in seconds. No data is lost."
- "Backups saved alongside each file (.bak). To undo: rename .bak back to docker-compose.yml"
- Backup location and undo instructions in plain English, not shell commands

**Graceful degradation:**
- Docker socket not available → hide Redeploy button, show manual commands only
- `docker compose` command fails → catch error, display it, fall back to manual commands
- Socket proxy (read-only) → detect and fall back gracefully

**After successful redeploy:** auto-rescan pipeline (we triggered the change, we should verify it). Dashboard refreshes with updated health status.

### 6. Error Paste Integration

The paste bar is **persistent at the bottom** of the dashboard — always visible, first-class citizen.

**Paste bar states:**

| State | Appearance |
|-------|------------|
| No directory loaded | Disabled/dimmed: "Select a stacks directory to get started" |
| Scan in progress | Disabled: "Scanning..." with spinner |
| Pipeline ready | Active: "Paste an error from your \*arr app..." with example pills |

**On paste:**
1. Parser runs (same extraction logic — service, path, error type)
2. Dashboard stays on same page — no navigation
3. Matched service(s) pulse/highlight with brief animation
4. Relevant conflict card auto-expands
5. View scrolls to the conflict
6. Paste bar shows parsed summary: "Sonarr can't access /downloads — mount conflict with qbittorrent"
7. If pipeline is healthy and error doesn't match: source-of-truth treatment ("Your setup looks correct")

**Key shift:** Pipeline is already analyzed. Paste becomes a search/highlight into a complete picture. Near-instant response — no analysis chain needed.

### 7. Directory Selection

The directory is the steering wheel — everything flows from it.

**First launch (no directory configured, no `MAPARR_STACKS_PATH`):**
- Full-screen welcome prompt: "Where are your Docker stacks?"
- Single text input + Scan button
- Platform-appropriate examples: `/opt/docker`, `C:\DockerContainers`

**After first scan:**
- Dashboard loads, directory moves to header: `📂 /opt/docker ▾`
- Header path is clickable — dropdown to change or type new path
- Changing path triggers full rescan, dashboard clears and repopulates

**When `MAPARR_STACKS_PATH` is set (Docker deployment):**
- Skip first-launch prompt, scan on load
- Header still shows path and allows changing (same as today's "Change Path")

**On directory change:**
- Pipeline rescan triggers automatically
- Dashboard clears → scan progress → new services populate
- Previous paste result cleared (belonged to old directory)
- Paste bar resets to ready state

### 8. Backend Changes Summary

| Area | Change |
|------|--------|
| `main.py` | New `POST /api/apply-fixes` (batch), new `POST /api/redeploy` endpoint |
| `analyzer.py` | `_patch_original_yaml` extended to accept pipeline context for multi-file patching |
| `pipeline.py` | Add per-service compose file path to pipeline result (already partially there) |
| `frontend/` | Significant rewrite: new dashboard layout, service groups, conflict cards, fix plan UI, paste bar |
| `index.html` | New page structure: header, health banner, role groups, paste bar |

**What carries forward unchanged:**
- Pipeline scan engine (`pipeline.py` core logic)
- 4-pass analysis engine (`analyzer.py` analysis functions)
- Image DB & classification (`image_registry.py`)
- Fix generation logic (`_generate_fixes`, `_fix_no_shared_mount`, etc.)
- Error parser (`parser.py`)
- Security measures (path validation, YAML safe load, rate limiting, compose filename whitelist)

### 9. Testing

| Test Area | Coverage |
|-----------|----------|
| Multi-file apply | Batch endpoint validates all before writing, per-file backup, rollback on failure |
| Redeploy | Socket available/unavailable, compose command success/failure, graceful degradation |
| Dashboard rendering | All role groups populated, service counts, health banner states |
| Error paste integration | Parse → highlight correct service, healthy pipeline + error = source-of-truth |
| Directory change | Rescan triggers, dashboard clears, paste resets |
| Cluster test stacks | Scenarios 18-20 exercise cross-folder detection + multi-file fix |
| Existing tests | All 546 current tests continue passing (backend logic unchanged) |

## What This Does NOT Include

- **Drag-and-drop reordering** of services — services are grouped by role, not user-sorted
- **Live container status** — we show compose config health, not runtime state (that's Portainer's job)
- **Auto-discovery of remote Docker hosts** — single directory, single host
- **Undo via UI** — backups are file-based (.bak), undo is manual rename. A UI undo button is future scope.
- **Compose file editor** — we show diffs and apply fixes, not a general-purpose YAML editor

## Migration Path

This is a frontend pivot, not a backend rewrite. The analysis engine, pipeline, Image DB, and fix generation are unchanged. The pre-pivot codebase is tagged as `v1.5.0-pre-pivot` for reference.

The stack grid UX is replaced, not deprecated. No backward-compatibility shim needed — the pipeline dashboard serves the same users better. The only user-visible regression is the loss of Browse/Fix mode split, which is intentional (unified flow is better).
