# Pipeline Dashboard UX Restoration Design

**Date:** 2026-03-09
**Branch:** `feature/pipeline-dashboard`
**Approach:** Hybrid — inline summaries on dashboard + deep drill-down via existing analysis cards

## Problem

The pipeline dashboard pivot preserved the structural improvement (service-first layout, role grouping, paste bar) but stripped away all user guidance, descriptions, tooltips, educational content, and the bridge to the full analysis flow. The result is a skeleton that works mechanically but feels bare and confusing to new users.

## Design

### 1. Layout Reorder

```
Header (scan path picker + browse button)
Health Banner (with severity breakdown)
Two-Action Fork (paste error | explore pipeline)
Conflict Summary Bar (severity counts)
Conflict Cards (collapsible, scroll-constrained)
Service Groups (with health legend, scroll-constrained)
```

- Paste bar moves from bottom to top as part of a two-action fork
- Conflicts render above service groups (issues first)
- Browse button added to header path picker

### 2. Guidance & Educational Content

**Dashboard welcome text** — contextual sentence that changes based on state:
- First scan: "MapArr scanned your Docker root and found N media services across M stacks."
- Issues found: "N path issues detected that may break hardlinks or cause import failures."
- All healthy: "Your media pipeline looks good — all services share consistent mount paths."

**Two-action fork descriptions:**
- Paste: "Got an error from Sonarr, Radarr, or another app? Paste it here and MapArr will trace it to the root cause."
- Explore: "Browse your full media pipeline — see how services connect, where volumes map, and what needs attention."

**Conflict card enrichment:**
- Collapsible "Why does this matter?" with plain-English explanation + TRaSH link
- "See Full Analysis →" link to deep drill-down

**Service detail panel enrichment:**
- Conflict summary for that service (if any)
- "Analyze Stack →" button for full analysis
- One-liner about what the service role means

### 3. Tooltips

Short, one-sentence tooltips on:
- Health dots (per-state explanation)
- Severity badges (what CRITICAL/HIGH/MEDIUM/LOW mean)
- Family names (what the image family means for permissions)
- Volume paths (why host paths matter for hardlinks)
- Pipeline context (what "siblings share mount root" means)
- Fix plan checkboxes (what selecting does)
- "Awaiting" state (fix written, restart needed)
- Scan path input (what directory to point at)
- Browse button (open folder picker)

### 4. Conflict Cards — Taming the Wall

- **Summary bar** at top: colored severity counts before the wall
- **Collapsed by default** — severity badge + one-line desc + affected count
- **Click to expand** — full description, services, "Why?", fix plan, "Full Analysis →"
- **YAML previews** — max-height 300px with scroll overflow
- **Group related conflicts** — same issue type across services → one card
- **Max 5 visible** — "Show N more issues" button

### 5. Service Groups — Scroll Containment

- Max-height with vertical scroll if >6 services per group
- Health legend row above first group (horizontal strip, all dot colors explained)
- Detail panel enriched with conflict summary + Analyze Stack button

### 6. Deep Drill-Down (Hybrid Bridge)

- "See Full Analysis →" and "Analyze Stack →" call existing `runAnalysis(stack)`
- Full flow: terminal animation → Problem → Solution (RPM Wizard) → Why → Next Steps → TRaSH
- "Back to Dashboard" returns to pipeline dashboard (already wired)
- No new analysis code needed — just doorways from dashboard

### 7. Traffic Light System (Full)

- Green (solid) — healthy internally AND pipeline-aligned
- Yellow (blinking) — internally fine, misaligned with broader pipeline
- Yellow (solid) — single service / can't fully determine
- Red (solid) — broken, internal conflicts
- Blue (pulsing) — fix applied, awaiting redeploy
- Grey — scanning or not applicable
