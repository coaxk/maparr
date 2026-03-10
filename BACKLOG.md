# MapArr Development Backlog

## BACKLOG-1: Paste Result Alternatives (v1.6 candidate)
**Origin:** Rank 22 from IMPROVEMENTS.md -- skipped because it requires backend changes
**What:** After paste analysis returns results, show alternative resolution approaches
below the primary recommendation when multiple valid options exist.

**Backend investigation (2026-03-11):**
- `parser.py` already computes `confidence` scores (high/medium/low/none) on parsed errors
- `smart_match.py` scores candidate stacks but returns only the best match
- `analyzer.py` does NOT compute alternative fixes -- each conflict gets ONE fix string
- RPM Wizard already serves as an alternative to Fix Paths for Cat A issues
- **Conclusion:** Would need new analysis logic to generate ranked alternatives per conflict
  (e.g., "restructure mounts" vs "add RPM" vs "use named volumes"). The RPM Wizard
  partially addresses this but only for Cat A path conflicts.

**Value:** High -- gives experienced users options rather than a single prescribed path
**Effort estimate:** Medium-High (new analyzer logic + frontend card + API change)
**Tag:** [UX] [Backend] [v1.6]

## BACKLOG-2: Light Mode / prefers-color-scheme (v1.6 candidate)
**Origin:** Rank 24 from IMPROVEMENTS.md -- skipped due to scope (4+ hour effort)
**What:** Full light mode color scheme via @media (prefers-color-scheme: light)
CSS block mapping all :root dark variables to light equivalents.

**Note:** The current dark theme is well-executed -- light mode must match that
quality bar, not be an afterthought. Do not rush this.

**Value:** Medium-High -- will be requested by users, especially on macOS where
system light mode is common
**Effort estimate:** High (complete second color scheme + thorough testing)
**Tag:** [Visual] [Accessibility] [v1.6]

## BACKLOG-3: Input Size Limits (v1.5.1 candidate)
**Origin:** Pre-release security audit (2026-03-11)
**What:** Add payload size limits to write and parse endpoints:
- `/api/parse-error`: max 100KB error_text
- `/api/apply-fix[es]`: max 1MB corrected_yaml per file
- `/api/list-directories`: expand system directory blocklist

**Value:** High -- prevents DoS via large payloads
**Effort estimate:** Low (30 minutes, 5 locations)
**Tag:** [Security] [v1.5.1]

## BACKLOG-4: Error Message Improvements (v1.5.1 candidate)
**Origin:** Pre-release error message audit (2026-03-11)
**What:** Fix 4 CRITICAL error messages:
- "Invalid JSON" on batch endpoints (add line:column from JSONDecodeError)
- Exception traceback leak in analysis (categorize, log, return safe message)
- Missing scan directory (add MAPARR_STACKS_PATH guidance)
- Compose filename not listed (show valid filenames in error)

**Value:** High -- users hit these errors and can't self-diagnose
**Effort estimate:** Low (1 hour, all in main.py + apply_multi.py)
**Tag:** [UX] [Backend] [v1.5.1]

## BACKLOG-5: Update E2E Tests for Paste Auto-Drill (v1.5.1)
**Origin:** Pre-release cross-browser testing (2026-03-11)
**What:** Two journey tests fail because paste auto-drill navigates away from paste area:
- `test_paste_error_matches_service`: close button "not stable" (element moved)
- `test_change_path_reloads_dashboard`: service-count not populated (timing)
Both fail identically on Chromium, Firefox, and WebKit -- not browser-specific.

**Value:** Medium -- test coverage gap on paste flow
**Effort estimate:** Low (30 minutes)
**Tag:** [Tests] [v1.5.1]
