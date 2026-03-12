# MapArr UI/UX Audit — Pass 1: Unguided Observation

**Date:** 2026-03-10
**Version:** v1.5.0
**Auditor:** Claude Opus 4.6 via Playwright MCP + axe-core 4.7.2

---

## 1A. Codebase Read Summary

### Files Reviewed
- `frontend/index.html` — 392 lines
- `frontend/styles.css` — ~3500 lines
- `frontend/app.js` — ~6800 lines

### Architecture
- Vanilla JS SPA, no framework, no build step
- Single global `state` object (~25 properties)
- Dark theme via CSS custom properties on `:root`
- 145+ bundled SVG service icons
- EventListener pattern throughout (CSP-ready)
- XSS-safe: all user content via `textContent`, container clearing via `replaceChildren()`

---

## 1B. Playwright Observation — Screen States Captured

### Screenshots Taken

| # | File | Screen State |
|---|------|-------------|
| 01 | `audit-01-boot-screen.png` | Boot terminal mid-scan animation |
| 02 | `audit-02-pipeline-dashboard.png` | Full pipeline dashboard (34 stacks, 10 services, 2 issues) |
| 03 | `audit-03-service-detail-expanded.png` | jdownloader2 service detail panel expanded |
| 04 | `audit-04-analysis-full-page.png` | Full analysis view for jdownloader2 (all sections) |
| 05 | `audit-05-paste-bar.jpg` | Paste Your Error bar open with example pills |
| 06 | `audit-06-paste-example-filled.jpg` | Example pill clicked — "Import failed" text filled with tooltip |
| 07 | `audit-07-conflict-card-expanded.jpg` | Conflict card expanded showing handrail text + "See Full Analysis" link |
| 08 | `audit-08-directory-browser.jpg` | Directory browser modal (server-side folder listing) |
| 09 | `audit-09-log-panel.jpg` | Application log panel with level filter + controls |
| 10 | `audit-10-sonarr-analysis-full.jpg` | Sonarr analysis — "Almost TRaSH Compliant" variant |

### Interactive States Exercised
- [x] Boot terminal animation → dashboard crossfade
- [x] Service row click → detail panel expand/collapse
- [x] Conflict card expand → handrail text + drill link
- [x] "Paste an Error" action fork → paste bar with pills
- [x] Example pill click → text fill + tooltip
- [x] "Analyze Stack →" → terminal animation → result cards
- [x] Path editor in header → text input + Scan/Browse
- [x] Directory browser modal → folder list + Up/Cancel/Select
- [x] Log panel toggle → log entries + level filter + download/clear/close
- [x] "Start Over" → return to dashboard
- [x] "Why This Works" collapsible → expand/hide toggle
- [x] Observations collapsible → `:latest` tag notice + ComposeArr link
- [x] Healthy stack analysis (sonarr) — "Almost TRaSH Compliant" section

### States NOT Captured (inaccessible without specific data conditions)
- [ ] First launch screen (requires no prior scan)
- [ ] RPM Wizard gates (requires Category A path conflicts — this environment only has Cat B permission issues)
- [ ] Apply Fix confirmation modal (requires Cat A/B with `original_corrected_yaml`)
- [ ] Toast notifications (transient — would need timed capture)
- [ ] Redeploy prompt (requires Docker connection + applied fix)
- [ ] Quick-switch combobox (requires multiple stacks analyzed in session)

---

## 1C. Accessibility Scan (axe-core 4.7.2)

### Summary
- **Violations:** 2 rules
- **Passes:** 35 rules
- **Incomplete:** 1 rule
- **Inapplicable:** 53 rules

### Violation 1: `color-contrast` (SERIOUS) — 38 nodes

The dominant issue. Three distinct color pairings fail WCAG 2 AA (4.5:1 minimum):

| Foreground | Background | Ratio | Required | Affected Elements |
|-----------|-----------|-------|----------|-------------------|
| `#5c6370` | `#181b23` | 2.84:1 | 4.5:1 | `.service-meta`, `.service-file`, `.fork-card-desc`, `.conflict-card-affected-count`, `.conflict-handrail` |
| `#5c6370` | `#0f1117` | 3.12:1 | 4.5:1 | `.legend-label`, `.legend-item`, `.non-media-header`, `.non-media-stacks-note`, `.non-media-stack-count`, `#footer-version` |
| `#5c6370` | `#1a2a3a` | 2.41:1 | 4.5:1 | `.expanded .service-meta`, `.expanded .service-file` |
| `#ffffff` | `#4a90d9` | 3.34:1 | 4.5:1 | `.btn-primary` (Scan, View Issues, Analyze Stack) |

**Root cause:** `#5c6370` is used as a "muted text" color but is too dark for these dark backgrounds. The `.btn-primary` blue is too light for white text at small sizes.

**Fix approach:**
- Raise `#5c6370` to `#8b949e` (GitHub's muted text) or `#7d8590` — both pass 4.5:1 on dark backgrounds
- Darken `.btn-primary` from `#4a90d9` to `#3a7bc8` or add `font-weight: 600`

### Violation 2: `heading-order` (MODERATE) — 1 node

`#fork-paste > .fork-card-title` is an `<h3>` but follows no `<h2>` in the DOM order. The health banner has no heading, so the action fork cards jump from `<h1>` (MapArr) to `<h3>`.

**Fix:** Either add an `<h2>` for the dashboard section or change fork card titles to `<h2>`.

---

## 1D. Console Errors Observed

| Error | Source | Impact |
|-------|--------|--------|
| 404: `/static/img/services/jdownloader.svg` | Missing icon file | Broken image fallback on service row |
| 404: `/static/img/services/suggestarr.ico` | Wrong extension (should be .svg or .png) | Broken image fallback on chip |
| 403: `api.github.com/repos/coaxk/maparr/releases/latest` | GitHub API rate limit | Stars badge shows stale/no data |

---

## Raw Observations (for Pass 2)

### Visual Impressions
- Dark theme is cohesive and well-executed
- Service icons add significant visual richness
- Health dot system is intuitive at a glance
- Boot terminal animation is delightful but non-skippable
- Analysis result cards are dense but well-structured
- Code blocks (YAML) in dark-on-dark are legible
- Footer is information-dense but organized

### Interaction Impressions
- Service row expand/collapse is responsive
- Conflict card expand reveals useful context
- Paste bar example pills are discoverable and helpful
- Directory browser modal works but feels basic
- Log panel resize handle exists but isn't visually obvious
- No keyboard navigation observed (Tab order untested beyond axe)
- No loading spinners visible during analysis (terminal animation serves this role)

### Information Architecture
- Dashboard → service groups → detail → analysis is a clear drill-down
- Two entry points (Paste/Explore) give good initial orientation
- Conflict cards duplicate some info from service rows (intentional summary)
- "Other Stacks" chip layout is space-efficient
- Analysis page sections flow logically: Problem → Setup → Solution → Why → Apply → Gold Standard
- Observations section collapsed by default — appropriate for low-severity items
