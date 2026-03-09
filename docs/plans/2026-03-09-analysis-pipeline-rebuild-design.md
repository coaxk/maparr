# Analysis Pipeline Rebuild Design

**Date:** 2026-03-09
**Branch:** `feature/pipeline-dashboard`
**Approach:** Category-aware solution generation with baked-in guidance

## Problem

The analysis pipeline was architected for path/mount analysis (Pass 1-2) and later extended with permission checks (Pass 3) and platform recommendations (Pass 4). Detection works correctly across all passes, but the solution generation layer was never extended — it always produces volume restructure YAML regardless of conflict type.

**Runtime-verified evidence (16 test stacks, 19 classification tests):**
- 6 CRITICAL disconnects: permission-only stacks generate irrelevant volume YAML
- 5 HIGH disconnects: RPM wizard shown for non-path issues
- 2 classification failures: single-letter `/mnt/` paths misclassified as WSL2
- Dashboard blind to permission/platform issues (green→red whiplash on drill-down)

## Design

### 1. Conflict Category System

Every conflict type is assigned to one of four categories. All downstream decisions — solution YAML, RPM wizard, frontend tabs, dashboard health, problem/solution card content — key off this category.

```
CATEGORY A: Path Conflicts (fixable by YAML volume changes)
  - no_shared_mount         (critical)
  - different_host_paths    (high)
  - named_volume_data       (critical)
  - path_unreachable        (varies)

CATEGORY B: Permission & Environment (fixable by YAML environment changes)
  - puid_pgid_mismatch      (high)
  - missing_puid_pgid       (medium)
  - root_execution          (medium)
  - umask_inconsistent      (low)
  - umask_restrictive       (low)
  - cross_stack_puid_mismatch (high)
  - tz_mismatch             (low) — NEW

CATEGORY C: Infrastructure Advisories (NOT fixable by YAML — guidance only)
  - wsl2_performance        (medium)
  - mixed_mount_types       (medium)
  - windows_path_in_compose (low)
  - remote_filesystem       (high)

CATEGORY D: Observations (noticed, not actioned — informational only)
  - missing_restart_policy
  - latest_tag_usage
  - missing_tz (when no TZ set at all, vs mismatch)
  - privileged_mode
  - no_healthcheck
```

**Health signal mapping:**
- Category A → Red (problem) — "Your paths are broken"
- Category B → Yellow (issue) — "Your permissions need attention"
- Category C → Blue/info (advisory) — shown in drill-down only
- Category D → Grey (observation) — no health impact

Defined as a single constant dict in the backend, referenced everywhere.

### 2. Solution Generation Engine

**Current (broken):**
```
Any conflict → _generate_solution_yaml() → Always rewrites volumes → One YAML output
```

**Rebuilt:**
```
Conflicts → categorize → route:
  Category A (paths)       → _generate_volume_solution()   → Corrected volumes YAML
  Category B (permissions) → _generate_env_solution()      → Corrected environment YAML
  Category C (infra)       → None                          → Guidance text only
  Mixed A+B                → Both generators               → Complete corrected YAML
```

**Backend changes (`analyzer.py`):**

1. `_generate_solution_yaml()` gets a conflict-type filter — only processes Category A. If no Category A conflicts exist, no volume YAML is generated.

2. New `_generate_env_solution()` — For Category B, generates corrected compose with patched `environment:` block:
   - `puid_pgid_mismatch` → All services set to majority UID:GID
   - `missing_puid_pgid` → Adds PUID/PGID using majority or 1000:1000
   - `root_execution` → Changes PUID from 0 to majority
   - `umask_*` → Sets UMASK=002 across all services
   - `tz_mismatch` → Sets all to majority TZ value
   - `cross_stack_puid_mismatch` → Text guidance only (can't fix other stacks)

3. Mixed stacks (A+B) — both generators run. Output YAML has corrected volumes AND environment. One complete fixed compose.

4. `_patch_original_yaml()` becomes category-aware — patches `environment:` lines for Category B instead of `volumes:` lines. Changed lines highlighted correctly.

**RPM wizard gating:**
- Only when Category A path conflicts exist AND `rpm_mappings` has `possible: true`
- Never for permission-only or infrastructure-only stacks
- Never for healthy stacks

### 3. Frontend Solution Rendering

Each category gets distinct presentation with guidance baked in from the start.

**Category A (Path Conflicts):**

"The Problem" card:
- Severity badge + specific description (existing)
- NEW: One-sentence plain-English handrail below technical description
  - no_shared_mount: "Your download client saves files to one folder, but Sonarr is looking in a different folder. They can't see each other's files."
  - different_host_paths: "These services think they're sharing the same folder, but on the host they're actually pointing at different directories."
  - named_volume_data: "Docker named volumes are isolated from each other. Files in one volume are invisible to services using a different volume."
  - path_unreachable: "The error path doesn't match any mount in your compose — the app can't reach the file it's looking for."

"The Solution" card:
- Tab 1 "Quick Fix (RPM Wizard)" with intro: "Remote Path Mappings tell your *arr app where to find files your download client saved. This is the fastest fix but doesn't solve the root cause."
- Tab 2 "Proper Fix (Restructure)" with intro: "This reorganizes your volumes so all services share one data directory. Hardlinks and atomic moves will work after this change."
- Tab 3 "Your Config (Corrected)" — patched original with Apply Fix

**Category B (Permission Conflicts):**

"The Problem" card:
- Severity badge + specific description (existing detection text is good)
- One-sentence handrails:
  - puid_pgid_mismatch: "Your services run as different Linux users. Files created by one app can't be read by another."
  - missing_puid_pgid: "Without explicit PUID/PGID, these containers default to an internal user (UID 911) that probably doesn't match your other services."
  - root_execution: "Running as root (UID 0) means files are owned by root. Other services running as a normal user can't modify them — and it's a security risk."
  - umask_*: "UMASK controls who can access newly created files. Different values mean some apps can't read files created by others."
  - tz_mismatch: "Services in different timezones will schedule grabs at unexpected times and show confusing timestamps in logs."

"The Solution" card:
- RPM wizard tab: HIDDEN (irrelevant)
- Tab 1 "Fix Permissions" — corrected YAML showing environment changes, highlighted. Intro: "These environment variable changes align your services to the same user identity. Copy this into your compose file."
- Tab 2 "Your Config (Corrected)" — patched original with Apply Fix
- Post-fix callout when relevant: "After updating your compose, fix existing file ownership: `sudo chown -R 1000:1000 /path/to/your/data`"

**Category C (Infrastructure Advisories):**

"The Problem" card → renamed "Recommendation":
- Blue info badge (not red/yellow)
- Specific handrails:
  - wsl2_performance: "Your media data lives on a Windows drive accessed through WSL2's filesystem bridge. This works but is significantly slower than native Linux storage. Large library scans and imports will feel sluggish."
  - remote_filesystem: "Your data is on a network share (NFS/CIFS). Hardlinks don't work across network boundaries. If all services access the same single export, imports will work via copies."
  - mixed_mount_types: "Some services use local storage, others use network storage. Hardlinks can't cross that boundary."
  - windows_path_in_compose: "Windows-style paths work but forward slashes and native Linux paths perform better in Docker."

"The Solution" card → renamed "What You Can Do":
- No YAML tabs (hidden)
- Plain text guidance with specific next steps
- TRaSH Guides link
- Tone: "This isn't something MapArr can fix in your compose file — it's about where your data lives. Here are your options..."

**Category D (Observations):**

Collapsed section at bottom of analysis detail:
- Header: "A few other things we noticed"
- One line each, casual tone:
  - "3 services don't have a restart policy — they won't come back after a reboot"
  - "sonarr and radarr use the :latest tag — pinning to a version prevents surprise updates"
  - "No TZ set on 4 services — they'll default to UTC which might confuse scheduling"
  - "qbittorrent runs in privileged mode — this gives it full host access"
- Footer: "For full compose hygiene analysis, check out ComposeArr"
- No health impact, no badges, no fix buttons

### 4. Dashboard Health Awareness

**Pipeline scan extensions (`pipeline.py`):**

Lightweight permission and TZ checks during pipeline scan (no full analyze_stack call):

1. Permission check: Group pipeline services by (PUID, PGID). 2+ groups → permission warning
2. TZ check: Group by TZ value. 2+ groups → TZ note
3. Platform checks stay per-stack (only fire in drill-down)

**Dashboard health dots:**

| Scenario | Health Dot | Tooltip |
|----------|-----------|---------|
| Path conflict (Cat A) | Red (problem) | "Broken mount paths — hardlinks won't work" |
| Permission mismatch (Cat B) | Yellow (issue) | "Permission mismatch — services can't share files" |
| Infrastructure advisory (Cat C) | Green (surfaces in drill-down) | Standard healthy tooltip |
| Cat A + Cat B | Red (worst wins) | Shows worst issue |
| Clean | Green | "No issues detected" |

**Conflict summary bar:**
"3 critical path issues · 2 permission mismatches · 12 services affected"

### 5. Classification Fix

**WSL2 regex (`mounts.py`):**

Current (broken): `^/mnt/([a-zA-Z])(/.*)?$`
- Matches any single-letter dir under /mnt/

Fixed: Match `/mnt/[c-z]/` only (drives A/B are floppies) AND require at least one subdirectory component. This catches real WSL2 mounts (`/mnt/c/Users/...`) while excluding `/mnt/n` style NAS abbreviations.

```python
# Only match actual Windows drive letters (c-z) with a path after them
match = re.match(r'^/mnt/([c-zC-Z])(/.+)$', path)
```

**`different_host_paths` fix text bug:**
Fix the `.split()` token extraction that produces nonsensical paths.

### 6. Tone & Ethos

The knowledgeable friend who knows the problem inside out but never lectures. Every user-facing element follows these principles:

- **Lead with what it means for the user**, not what the system detected
- **One plain-English sentence** before any technical detail
- **Severity signals are honest** — green means green, not "green but actually there's something"
- **Recommendations are separate from problems** — blue info, not red alarm
- **Observations are casual** — "we noticed" not "WARNING"
- **Always offer a path forward** — never just say what's wrong
- **User moves at their own pace** — collapsed by default, expand to learn more
- **ComposeArr cross-reference** — for things outside our scope, point them to the right tool

### 7. Files Affected

**Backend:**
- `backend/analyzer.py` — Category constant, solution generation routing, env solution generator, category-aware patching, TZ detection, observations collection
- `backend/pipeline.py` — Permission/TZ awareness in pipeline scan
- `backend/mounts.py` — WSL2 regex fix

**Frontend:**
- `frontend/app.js` — Category-aware rendering for Problem/Solution/Observations, RPM wizard gating, dashboard health dots, conflict summary
- `frontend/styles.css` — Blue info badge, observation section styles, recommendation card styles
- `frontend/index.html` — Observation section container

### 8. Test Verification

Audit stacks in `tools/audit-stacks/` (16 scenarios) + `tools/audit_pipeline.py` provide runtime verification. After rebuild, all 11 disconnects should resolve to 0, classification should be 19/19.
