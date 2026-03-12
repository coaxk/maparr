# MapArr UI/UX Improvements — Final Deliverable

**Audit Date:** 2026-03-10 | **Version:** v1.5.0 | **Auditor:** Claude Opus 4.6
**Method:** Two-pass review — Playwright observation + axe-core scan + 6-lens expert review

---

## Top 5 Highest Priority

| # | Issue | Impact | Effort | Why First |
|---|-------|--------|--------|-----------|
| 1 | **Apply Fix has no loading state** | Users think click didn't register on slow networks | Low (1 line change) | Critical user confidence issue during destructive operation |
| 2 | **38 color contrast failures (WCAG AA)** | Muted text unreadable for low-vision users; fails automated audits | Low (2 CSS vars) | Legal/compliance risk; single root cause (`#5c6370` too dark) |
| 3 | **No `prefers-reduced-motion` support** | 40+ smooth scrolls + 8 animations can't be disabled; vestibular disorder trigger | Medium (CSS media query + JS check) | WCAG 2.1 failure; affects ~5% of users |
| 4 | **12 missing service icons** | tdarr, kometa, unmanic, cross-seed, etc. render as generic box | Low (download SVGs + add to map) | Visual recognition broken for popular services |
| 5 | **No icon 404 fallback** | Broken image icon (small X) instead of graceful degradation | Low (1 onerror handler) | 2 confirmed 404s already; any icon deletion causes visible breakage |

---

## Quick Wins (< 30 minutes each)

### QW-1: Apply Fix loading state
**File:** `app.js:1416`
**What:** Change button text to "Applying..." and add `disabled` during fetch. Restore on resolve/reject.
**Why:** Users on slow networks (NAS, VPN) see 5-10s of nothing after clicking a destructive operation.

### QW-2: Fix muted text contrast
**File:** `styles.css` — CSS custom property or direct color values
**What:** Replace `#5c6370` with `#8b949e` (GitHub's muted text color, 5.2:1 on `#181b23`). Fix `.btn-primary` by darkening `#4a90d9` to `#3570b5` or adding `font-weight: 600`.
**Why:** 38 axe-core violations. Affects `.service-meta`, `.service-file`, `.fork-card-desc`, `.conflict-card-affected-count`, `.legend-item`, `#footer-version`, `.non-media-header`, `.conflict-handrail`.

### QW-3: Icon onerror fallback
**File:** `app.js:746` (and lines 909, 3158 — all icon img creation sites)
**What:** Add `icon.onerror = () => { icon.src = '/static/img/services/generic.svg'; icon.onerror = null; };`
**Why:** 2 confirmed 404s (jdownloader.svg, suggestarr.ico) show broken image icons. Any future icon deletion cascades.

### QW-4: Fix heading order
**File:** `index.html:134` (or `app.js` where fork card titles are created)
**What:** Change `<h3 class="fork-card-title">` to `<h2>` — or add an `<h2>` heading above the action fork section.
**Why:** axe-core violation: heading jumps from h1 to h3.

### QW-5: Escape key closes paste bar
**File:** `app.js` — add keydown listener in paste bar open handler
**What:** `document.addEventListener('keydown', e => { if (e.key === 'Escape') closePasteBar(); });`
**Why:** Escape works for directory browser modal but not paste bar — inconsistent.

### QW-6: aria-live on toast container
**File:** `index.html:383` (toast-container div)
**What:** Add `aria-live="polite" aria-atomic="true"` to `#toast-container`.
**Why:** Screen readers don't announce success/error toasts.

---

## High Impact Changes (1-4 hours each)

### HI-1: prefers-reduced-motion support
**Files:** `styles.css` (new media query), `app.js` (40+ `scrollIntoView` calls)
**What:**
- CSS: `@media (prefers-reduced-motion: reduce) { *, *::before, *::after { animation-duration: 0.01ms !important; transition-duration: 0.01ms !important; } }`
- JS: Replace `behavior: "smooth"` with `behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? "auto" : "smooth"`
- Wrap in a helper: `function scrollOpts() { return { behavior: prefersReducedMotion ? 'auto' : 'smooth', block: 'nearest' }; }`
**Why:** WCAG 2.1 Level AAA. Boot animation, pillFade, headerNudgeGlow, 40+ smooth scrolls currently can't be disabled.

### HI-2: Missing service icons
**Files:** `frontend/img/services/` (new SVGs), `app.js:774-880` (SERVICE_ICONS map)
**What:** Source and add SVGs for: tdarr, kometa, unmanic, cross-seed, organizr, swag, makemkv, seq, jdownloader (correct filename), aria2, mylar3, tubearchivist.
**Why:** 12 popular services render as generic grey box. Users can't visually scan their pipeline.

### HI-3: Keyboard shortcuts for power users
**Files:** `app.js` (new global keydown handler)
**What:**
- `Cmd/Ctrl+Enter` in paste textarea → submit analysis
- `Escape` → close any open overlay (paste bar, modal, directory browser)
- `Cmd/Ctrl+K` → focus path input (quick-switch)
- `?` → show keyboard shortcut overlay
**Why:** Power users (sysadmins managing 20+ stacks) need speed. Current UI is click-only.

### HI-4: Collapsible cards need visual affordance
**Files:** `styles.css` (new ::after pseudo-element), `app.js:1193` (aria-expanded)
**What:**
- Add chevron indicator (▸/▾) to `.conflict-card-header` via CSS `::after`
- Toggle `aria-expanded="true/false"` when card expands/collapses
- Same treatment for service rows and "Why This Works" section
**Why:** No visual cue that cards are expandable. Users must guess or hover.

### HI-5: Modal focus trap
**Files:** `app.js` (directory browser open/close handlers, apply confirm modal)
**What:** When modal opens, save previously focused element, move focus to first focusable child, trap Tab/Shift+Tab within modal, restore focus on close.
**Why:** Tab key escapes modals into hidden content. Screen reader users get lost.

---

## Design System Foundations

### DS-1: CSS custom property for muted text
**Current:** `#5c6370` hardcoded in 20+ rules
**Proposed:** `--text-muted: #8b949e;` in `:root`, replace all instances
**Benefit:** Single-point contrast fix; enables light mode variant later.

### DS-2: Icon sizing scale
**Current:** 16px in chips, 20px in rows, mixed in other contexts
**Proposed:** `--icon-sm: 14px; --icon-md: 18px; --icon-lg: 24px;` — use `--icon-md` everywhere except chips (`--icon-sm`) and hero contexts (`--icon-lg`).
**Benefit:** Consistent visual weight; easier to adjust globally.

### DS-3: Spacing scale documentation
**Current:** Implicit quarter-rem scale (0.25rem, 0.5rem, 0.75rem, 1rem, 1.5rem, 2rem)
**Proposed:** Document as `--space-1` through `--space-8` in `:root` comment block
**Benefit:** Future contributors maintain consistency without reverse-engineering.

### DS-4: Button hierarchy
**Current:** `.btn-primary` (blue fill), `.btn-ghost` (outline), `.btn-subtle` (text-only)
**Issue:** Copy and Apply buttons on same row have mismatched visual weight (text-only vs. filled)
**Proposed:** Promote Copy to `.btn-ghost` (outline) so both actions are visually present.

### DS-5: Health dot naming convention
**Current:** `.healthy`, `.health-caution`, `.issue`, `.problem`, `.awaiting`, `.health-unknown`
**Proposed:** Normalize to `health-{level}`: `.health-ok`, `.health-warn`, `.health-issue`, `.health-critical`, `.health-applied`, `.health-unknown`
**Benefit:** Predictable naming; less cognitive load for contributors.

---

## Accessibility Fixes

### A11Y-1: Color contrast (CRITICAL — 38 nodes)
See QW-2. Root cause: `#5c6370` muted text on dark backgrounds.

| Context | Current Ratio | Fix Color | Fixed Ratio |
|---------|-------------|-----------|-------------|
| `.service-meta` on `#181b23` | 2.84:1 | `#8b949e` | 5.21:1 |
| `.legend-item` on `#0f1117` | 3.12:1 | `#8b949e` | 5.58:1 |
| `.expanded` row on `#1a2a3a` | 2.41:1 | `#8b949e` | 4.51:1 |
| `.btn-primary` white on `#4a90d9` | 3.34:1 | darken to `#3570b5` | 4.62:1 |

### A11Y-2: Heading order (MODERATE — 1 node)
See QW-4. Fork card titles jump h1 → h3.

### A11Y-3: aria-expanded on collapsibles
**Files:** `app.js:1193` (conflict cards), service row toggle, "Why This Works"
**What:** Set `aria-expanded="false"` initially; toggle on click.

### A11Y-4: Form labels
**Files:** `index.html:29-33, 72-74, 101-104`
**What:** Add `<label for="...">` elements (can be `sr-only` class if visually hidden).

### A11Y-5: Image alt text
**Files:** `app.js:746, 909, 3158`
**What:** Change `alt=""` to `alt="${serviceName} icon"` for service icons (they convey identity, not purely decorative).

### A11Y-6: Motion reduction
See HI-1. 40+ animations + smooth scrolls need `prefers-reduced-motion` gate.

---

## Keyboard Enhancements

| Shortcut | Action | Context |
|----------|--------|---------|
| `Escape` | Close any overlay (paste bar, modal, log panel) | Global |
| `Cmd/Ctrl+Enter` | Submit paste error for analysis | Paste textarea focused |
| `Cmd/Ctrl+K` | Focus path input / quick-switch | Global |
| `Enter` on service row | Toggle expand/collapse | Service row focused |
| `Enter` on conflict card | Toggle expand/collapse | Card focused |
| `Tab` / `Shift+Tab` | Navigate focusable elements | Within modals (trapped) |
| `?` | Show keyboard shortcuts help | Global (when no input focused) |

**Implementation:** Single `document.addEventListener('keydown', ...)` handler with context checks.
**File:** `app.js` — new function `handleGlobalKeydown()` wired in DOMContentLoaded.

---

## Full Ranked List

| Rank | ID | Category | Issue | Severity | Effort | Lens |
|------|-----|----------|-------|----------|--------|------|
| 1 | QW-1 | Feedback | Apply Fix missing loading state | Significant | 15 min | 3 |
| 2 | QW-2 | A11Y | 38 color contrast failures | Serious | 20 min | 4,6 |
| 3 | HI-1 | A11Y | No prefers-reduced-motion | Moderate | 2 hr | 6 |
| 4 | HI-2 | Icons | 12 missing service icons | Moderate | 1 hr | 5 |
| 5 | QW-3 | Icons | No icon 404 fallback | Moderate | 10 min | 5 |
| 6 | QW-6 | A11Y | No aria-live on toasts | Minor | 5 min | 6 |
| 7 | QW-4 | A11Y | Heading order violation | Minor | 5 min | 6 |
| 8 | QW-5 | Keyboard | Escape doesn't close paste bar | Minor | 10 min | 1 |
| 9 | HI-3 | Keyboard | No keyboard shortcuts | Moderate | 2 hr | 1 |
| 10 | HI-4 | Visual | No chevron on collapsibles | Minor | 30 min | 4 |
| 11 | HI-5 | A11Y | Modal focus not trapped | Minor | 1 hr | 6 |
| 12 | A11Y-3 | A11Y | aria-expanded missing | Minor | 30 min | 6 |
| 13 | A11Y-4 | A11Y | Form labels missing | Minor | 30 min | 6 |
| 14 | A11Y-5 | A11Y | Icon alt text empty | Minor | 15 min | 5,6 |
| 15 | DS-1 | Design | CSS var for muted text | Cosmetic | 15 min | 4 |
| 16 | DS-2 | Design | Icon sizing scale | Cosmetic | 30 min | 4,5 |
| 17 | DS-4 | Visual | Copy/Apply button weight mismatch | Cosmetic | 15 min | 4 |
| 18 | DS-5 | Design | Health dot naming convention | Cosmetic | 30 min | 4 |
| 19 | — | Density | Dashboard scroll depth excessive | Moderate | 3 hr | 2 |
| 20 | — | Density | Mobile 600px breakpoint minimal | Minor | 2 hr | 2 |
| 21 | — | Feedback | Stale data indicator not rendered | Minor | 30 min | 3 |
| 22 | — | Feedback | Paste result lacks alternatives | Cosmetic | 30 min | 3 |
| 23 | — | Feedback | Network error messages generic | Minor | 1 hr | 3 |
| 24 | — | Best Practice | No prefers-color-scheme (light mode) | Cosmetic | 4 hr | 6 |
| 25 | — | Best Practice | No build step / minification | Cosmetic | 2 hr | 6 |
| 26 | — | Icons | Icon path hardcoded (no base URL) | Minor | 15 min | 5 |
| 27 | DS-3 | Design | Spacing scale undocumented | Cosmetic | 15 min | 4 |

---

## What's Exceptional (Don't Change)

1. **CONFLICT_HANDRAILS constant** (`app.js:56-79`) — Plain-English explanations for all 17 conflict types. Best-in-class UX writing. Every developer tool should steal this pattern.
2. **Boot → discovery → fork → analysis flow** — Progressive disclosure manages cognitive load perfectly. Users never see complexity they don't need yet.
3. **Terminal aesthetic** — The analysis terminal is engaging, informative, and appropriately restrained. It communicates "I'm working" without being cute.
4. **Copy feedback** — 2-second "Copied!" state change is industry-standard done right.
5. **Action fork design** — Two clear entry points (Paste an Error / Explore Pipeline) eliminate the "what do I do first?" problem.
6. **TRaSH Guides integration** — Deep links to authoritative documentation. Respects the ecosystem.
7. **Service icon coverage** — 145 bundled SVGs is impressive for a v1.5 tool. The fuzzy matching fallback is thoughtful.
8. **Health dot system** — Traffic-light visual scanning at a glance. Users can triage 34 stacks in 2 seconds.

---

## Console Errors Observed

| Error | Source | Fix |
|-------|--------|-----|
| 404: `/static/img/services/jdownloader.svg` | Icon file missing or wrong name | Add file or fix SERVICE_ICONS mapping |
| 404: `/static/img/services/suggestarr.ico` | Wrong extension (.ico instead of .svg) | Fix extension in SERVICE_ICONS map |
| 403: `api.github.com` rate limit | GitHub stars badge fetch | Add cache or graceful fallback |
