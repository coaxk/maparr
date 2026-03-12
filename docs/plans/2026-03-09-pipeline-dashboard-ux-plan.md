# Pipeline Dashboard UX Restoration — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Restore user-friendly guidance, tooltips, scroll containment, and drill-down bridges to the pipeline dashboard, transforming it from a skeleton into a polished, educational product.

**Architecture:** All changes are frontend-only (HTML + JS + CSS). No backend changes needed. The existing analysis flow (`runAnalysis()` → detail cards) is fully functional and just needs doorways from the new dashboard. We restructure the HTML layout order (paste bar moves up, conflicts above services), add guidance text and tooltips throughout, and add scroll containment to prevent wall-of-text rendering.

**Tech Stack:** Vanilla HTML/CSS/JS (no framework, no build step)

---

### Task 1: Restructure HTML layout — two-action fork + reorder sections

**Files:**
- Modify: `frontend/index.html:108-142`

**Step 1: Restructure the pipeline-dashboard section**

Replace the pipeline-dashboard section (lines 108-142) with the new layout order:

```html
<!-- ═══════════════════════════════════════════════ -->
<!-- PIPELINE DASHBOARD — service-first view         -->
<!-- ═══════════════════════════════════════════════ -->
<section class="hidden" id="pipeline-dashboard">

    <!-- Health Banner -->
    <div class="health-banner" id="health-banner">
        <div class="health-banner-status">
            <span class="health-banner-icon" id="health-banner-icon"></span>
            <span class="health-banner-text" id="health-banner-text"></span>
        </div>
        <div class="health-banner-actions" id="health-banner-actions"></div>
    </div>

    <!-- Dashboard Welcome -->
    <p class="dashboard-welcome" id="dashboard-welcome"></p>

    <!-- Two-Action Fork -->
    <div class="action-fork" id="action-fork">
        <div class="fork-card fork-paste" id="fork-paste">
            <div class="fork-card-icon">&#128203;</div>
            <h3 class="fork-card-title">Paste an Error</h3>
            <p class="fork-card-desc">Got an error from Sonarr, Radarr, or another app? Paste it here and MapArr will trace it to the root cause.</p>
        </div>
        <div class="fork-card fork-explore" id="fork-explore">
            <div class="fork-card-icon">&#128269;</div>
            <h3 class="fork-card-title">Explore Pipeline</h3>
            <p class="fork-card-desc">Browse your full media pipeline — see how services connect, where volumes map, and what needs attention.</p>
        </div>
    </div>

    <!-- Error Paste Area (initially hidden, shown when fork-paste clicked) -->
    <div class="paste-area hidden" id="paste-area">
        <div class="paste-area-header">
            <h3>Paste Your Error</h3>
            <button class="btn btn-ghost btn-sm" id="paste-area-close" aria-label="Close paste area">&times;</button>
        </div>
        <div class="paste-bar-input">
            <textarea id="paste-error-input" rows="3"
                      placeholder="Paste an error from your *arr app — Sonarr, Radarr, Lidarr, etc."
                      aria-label="Paste error text from your media app"></textarea>
            <button id="paste-error-go" class="btn btn-primary" disabled>Analyze</button>
        </div>
        <div class="paste-bar-examples" id="paste-bar-examples">
            <span class="example-label">Try an example:</span>
            <button class="example-pill paste-pill" data-example="import" data-tooltip="Sonarr/Radarr can't find a downloaded file">Import failed</button>
            <button class="example-pill paste-pill" data-example="hardlink" data-tooltip="App is copying instead of hardlinking — wastes disk space">Hardlink issue</button>
            <button class="example-pill paste-pill" data-example="permission" data-tooltip="App can't read or write a file due to UID/GID mismatch">Permission denied</button>
            <button class="example-pill paste-pill" data-example="remote" data-tooltip="Download client and *arr app see different paths for the same files">Remote path</button>
        </div>
        <div class="paste-bar-result hidden" id="paste-bar-result"></div>
    </div>

    <!-- Conflict Summary Bar (severity counts at a glance) -->
    <div class="conflict-summary hidden" id="conflict-summary"></div>

    <!-- Conflict Cards (populated by JS when issues detected) -->
    <div id="conflict-cards"></div>

    <!-- Health Legend -->
    <div class="health-legend" id="health-legend">
        <span class="legend-label">Health:</span>
        <span class="legend-item"><span class="health-dot healthy"></span> Healthy</span>
        <span class="legend-item"><span class="health-dot health-caution"></span> Caution</span>
        <span class="legend-item"><span class="health-dot issue"></span> Problem</span>
        <span class="legend-item"><span class="health-dot awaiting"></span> Fix applied</span>
        <span class="legend-item"><span class="health-dot health-unknown"></span> Scanning</span>
    </div>

    <!-- Service Groups (populated by JS — one group per role) -->
    <div id="service-groups"></div>

</section>
```

**Step 2: Remove the old paste-bar from the HTML** (it's now integrated into the paste-area above)

Delete the old `#paste-bar` div that was at the bottom.

**Step 3: Verify the page loads without JS errors**

Open `http://localhost:9494` and check browser console.

**Step 4: Commit**

```bash
git add frontend/index.html
git commit -m "feat: restructure dashboard layout — fork cards, paste area, conflict summary, health legend"
```

---

### Task 2: Add browse button to header path picker

**Files:**
- Modify: `frontend/index.html:21-33` (path-selector)
- Modify: `frontend/app.js:1326-1359` (setupHeaderPath)
- Modify: `frontend/styles.css` (path-selector styles)

**Step 1: Add browse button to HTML**

After the `path-editor` div (line 32), add:

```html
<button class="btn btn-ghost btn-sm path-browse" id="header-path-browse"
        data-tooltip="Open folder picker" aria-label="Browse for stacks directory">&#128194;</button>
```

**Step 2: Wire browse button in setupHeaderPath()**

In `setupHeaderPath()` (app.js ~line 1326), add after the existing event listeners:

```javascript
// Browse button — uses the directory picker API when available
const browseBtn = document.getElementById("header-path-browse");
if (browseBtn) {
    browseBtn.addEventListener("click", async () => {
        if (window.showDirectoryPicker) {
            try {
                const handle = await window.showDirectoryPicker({ mode: "read" });
                // Build path from handle — limited by browser security, show name only
                const input = document.getElementById("header-path-input");
                const editor = document.getElementById("path-editor");
                if (input) input.value = handle.name;
                if (editor) editor.classList.remove("hidden");
                input.focus();
                showSimpleToast("Selected: " + handle.name + " — enter the full server path and click Scan", "info");
            } catch (e) {
                // User cancelled
            }
        } else {
            // Fallback: just open the path editor
            const editor = document.getElementById("path-editor");
            const input = document.getElementById("header-path-input");
            if (editor) editor.classList.remove("hidden");
            if (input) { input.value = state.stacksPath; input.focus(); input.select(); }
            showSimpleToast("Enter the full path to your Docker stacks directory", "info");
        }
    });
}
```

**Step 3: Add CSS for browse button**

```css
.path-browse {
    font-size: 1.1rem;
    padding: 0.25rem 0.4rem;
    line-height: 1;
}
```

**Step 4: Commit**

```bash
git add frontend/index.html frontend/app.js frontend/styles.css
git commit -m "feat: add browse button to header path picker"
```

---

### Task 3: Add CSS for new dashboard components

**Files:**
- Modify: `frontend/styles.css` (append new rules)

**Step 1: Add styles for all new components**

Append to styles.css (after the existing pipeline dashboard styles):

```css
/* ─── Dashboard Welcome ─── */

.dashboard-welcome {
    font-size: 0.88rem;
    color: var(--text-secondary);
    margin: 0 0 1rem 0;
    line-height: 1.5;
}

/* ─── Two-Action Fork ─── */

.action-fork {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 0.75rem;
    margin-bottom: 1rem;
}

.fork-card {
    padding: 1.25rem;
    border-radius: var(--radius);
    background: var(--bg-card);
    border: 1px solid var(--border);
    cursor: pointer;
    transition: border-color 0.15s, background 0.15s, transform 0.1s;
}

.fork-card:hover {
    border-color: var(--accent);
    background: var(--bg-card-hover);
    transform: translateY(-1px);
}

.fork-card-icon {
    font-size: 1.5rem;
    margin-bottom: 0.4rem;
}

.fork-card-title {
    font-size: 0.95rem;
    font-weight: 600;
    margin: 0 0 0.35rem 0;
}

.fork-card-desc {
    font-size: 0.8rem;
    color: var(--text-muted);
    line-height: 1.45;
    margin: 0;
}

@media (max-width: 600px) {
    .action-fork {
        grid-template-columns: 1fr;
    }
}

/* ─── Paste Area (expanded from fork) ─── */

.paste-area {
    margin-bottom: 1rem;
    padding: 1rem 1.25rem;
    border-radius: var(--radius);
    background: var(--bg-card);
    border: 1px solid var(--accent);
    animation: slideDown 0.2s ease-out;
}

.paste-area-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 0.75rem;
}

.paste-area-header h3 {
    font-size: 0.95rem;
    font-weight: 600;
    margin: 0;
}

/* ─── Conflict Summary Bar ─── */

.conflict-summary {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    padding: 0.6rem 1rem;
    margin-bottom: 0.75rem;
    border-radius: var(--radius);
    background: var(--bg-card);
    border: 1px solid var(--border);
    font-size: 0.82rem;
    color: var(--text-secondary);
    flex-wrap: wrap;
}

.conflict-summary-count {
    display: flex;
    align-items: center;
    gap: 0.3rem;
}

.conflict-summary-count.summary-critical { color: var(--error); }
.conflict-summary-count.summary-high { color: var(--warning); }
.conflict-summary-count.summary-medium { color: var(--accent); }
.conflict-summary-count.summary-low { color: var(--text-muted); }

.conflict-summary-separator {
    color: var(--border);
}

.conflict-summary-total {
    margin-left: auto;
    font-weight: 500;
    color: var(--text-primary);
}

/* ─── Health Legend ─── */

.health-legend {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    padding: 0.5rem 1rem;
    margin-bottom: 0.75rem;
    font-size: 0.72rem;
    color: var(--text-muted);
    flex-wrap: wrap;
}

.legend-label {
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.4px;
}

.legend-item {
    display: flex;
    align-items: center;
    gap: 0.3rem;
}

/* ─── Conflict Card Collapsed State ─── */

.conflict-card.collapsed .conflict-card-body {
    display: none;
}

.conflict-card-header {
    cursor: pointer;
}

.conflict-card-chevron {
    margin-left: auto;
    font-size: 0.7rem;
    color: var(--text-muted);
    transition: transform 0.15s;
    flex-shrink: 0;
}

.conflict-card.collapsed .conflict-card-chevron {
    transform: rotate(-90deg);
}

.conflict-card-affected-count {
    font-size: 0.72rem;
    color: var(--text-muted);
    margin-left: 0.5rem;
    flex-shrink: 0;
}

/* ─── Conflict Card "Why" Section ─── */

.conflict-why {
    margin-top: 0.5rem;
    padding: 0.6rem 0.75rem;
    background: rgba(74, 144, 217, 0.05);
    border-radius: var(--radius-sm);
    border: 1px solid rgba(74, 144, 217, 0.1);
    font-size: 0.8rem;
    color: var(--text-secondary);
    line-height: 1.5;
}

.conflict-why-toggle {
    font-size: 0.75rem;
    color: var(--accent);
    cursor: pointer;
    margin-top: 0.35rem;
    display: inline-block;
}

.conflict-why-toggle:hover {
    text-decoration: underline;
}

.conflict-drill-link {
    display: inline-block;
    margin-top: 0.5rem;
    font-size: 0.8rem;
    color: var(--accent);
    cursor: pointer;
    font-weight: 500;
}

.conflict-drill-link:hover {
    text-decoration: underline;
}

/* ─── Show More Conflicts ─── */

.conflicts-show-more {
    text-align: center;
    padding: 0.75rem;
}

.conflicts-show-more button {
    font-size: 0.82rem;
    color: var(--accent);
    background: none;
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 0.4rem 1rem;
    cursor: pointer;
    transition: border-color 0.15s;
}

.conflicts-show-more button:hover {
    border-color: var(--accent);
}

/* ─── Service Group Scroll Containment ─── */

.service-group-items.scrollable {
    max-height: 300px;
    overflow-y: auto;
}

/* ─── Service Detail Enrichment ─── */

.detail-conflicts {
    margin-top: 0.5rem;
    padding: 0.4rem 0.6rem;
    background: rgba(210, 153, 34, 0.06);
    border-radius: var(--radius-sm);
    border: 1px solid rgba(210, 153, 34, 0.12);
    font-size: 0.78rem;
    color: var(--warning);
}

.detail-action-row {
    display: flex;
    gap: 0.5rem;
    margin-top: 0.5rem;
}

/* ─── Tooltips ─── */

[data-tooltip] {
    position: relative;
}

[data-tooltip]::after {
    content: attr(data-tooltip);
    position: absolute;
    bottom: calc(100% + 6px);
    left: 50%;
    transform: translateX(-50%);
    padding: 0.35rem 0.6rem;
    border-radius: 4px;
    background: var(--bg-tooltip, #1c2333);
    color: var(--text-primary);
    font-size: 0.72rem;
    font-weight: 400;
    white-space: nowrap;
    max-width: 280px;
    white-space: normal;
    line-height: 1.35;
    pointer-events: none;
    opacity: 0;
    transition: opacity 0.15s;
    z-index: 100;
    border: 1px solid var(--border);
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
}

[data-tooltip]:hover::after {
    opacity: 1;
}

/* Position tooltip below when near top of viewport */
[data-tooltip-pos="below"]::after {
    bottom: auto;
    top: calc(100% + 6px);
}

/* ─── btn-secondary (missing style) ─── */

.btn-secondary {
    background: var(--bg-input);
    color: var(--text-primary);
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    padding: 0.4rem 0.8rem;
    font-size: 0.82rem;
    cursor: pointer;
    transition: border-color 0.15s, background 0.15s;
}

.btn-secondary:hover {
    border-color: var(--accent);
    background: var(--bg-card-hover);
}

/* ─── Manual Commands (missing style) ─── */

.manual-commands {
    background: var(--bg-input);
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    padding: 0.75rem;
    font-family: var(--font-mono);
    font-size: 0.78rem;
    line-height: 1.6;
    white-space: pre-wrap;
    position: relative;
    margin-top: 0.5rem;
}

.manual-commands .copy-btn {
    position: absolute;
    top: 0.35rem;
    right: 0.35rem;
}
```

**Step 2: Commit**

```bash
git add frontend/styles.css
git commit -m "feat: add CSS for fork cards, paste area, conflict summary, health legend, tooltips, scroll containment"
```

---

### Task 4: Rewrite renderDashboard() and renderHealthBanner() with guidance text

**Files:**
- Modify: `frontend/app.js:456-515`

**Step 1: Rewrite renderDashboard()**

Replace the function at line 456:

```javascript
function renderDashboard() {
    // Update header
    updateHeaderPath(state.stacksPath);
    updateServiceCount(state.services.length);

    // Health banner
    renderHealthBanner(state.pipeline);

    // Welcome text
    renderWelcomeText(state.pipeline);

    // Two-action fork
    wireActionFork();

    // Conflict summary bar + cards (above service groups)
    const conflicts = state.pipeline.conflicts || [];
    renderConflictSummary(conflicts);
    renderConflictCards(conflicts);

    // Service groups by role (with health legend above)
    renderServiceGroups(state.servicesByRole);

    // Enable paste area (wire up if user clicks fork-paste)
    enablePasteBar();

    // Show dashboard, hide analysis detail cards
    show("pipeline-dashboard");
    hideAnalysisCards();
}
```

**Step 2: Add renderWelcomeText() after renderDashboard()**

```javascript
function renderWelcomeText(pipeline) {
    const el = document.getElementById("dashboard-welcome");
    if (!el) return;

    const svcCount = pipeline.media_service_count || state.services.length;
    const stackCount = pipeline.stacks_scanned || 0;
    const conflicts = pipeline.conflicts || [];

    if (conflicts.length === 0) {
        el.textContent = "Your media pipeline looks good \u2014 all " + svcCount +
            " services across " + stackCount + " stacks share consistent mount paths.";
    } else {
        el.textContent = "MapArr scanned " + stackCount + " stacks and found " +
            svcCount + " media services. " + conflicts.length +
            " path issue" + (conflicts.length !== 1 ? "s" : "") +
            " detected that may break hardlinks or cause import failures.";
    }
}
```

**Step 3: Add wireActionFork() function**

```javascript
function wireActionFork() {
    const forkPaste = document.getElementById("fork-paste");
    const forkExplore = document.getElementById("fork-explore");
    const pasteArea = document.getElementById("paste-area");
    const pasteClose = document.getElementById("paste-area-close");

    if (forkPaste) {
        // Remove old listeners by cloning
        const newPaste = forkPaste.cloneNode(true);
        forkPaste.parentNode.replaceChild(newPaste, forkPaste);
        newPaste.addEventListener("click", () => {
            if (pasteArea) {
                pasteArea.classList.remove("hidden");
                const input = document.getElementById("paste-error-input");
                if (input) { input.disabled = false; input.focus(); }
            }
        });
    }

    if (forkExplore) {
        const newExplore = forkExplore.cloneNode(true);
        forkExplore.parentNode.replaceChild(newExplore, forkExplore);
        newExplore.addEventListener("click", () => {
            const groups = document.getElementById("service-groups");
            if (groups) groups.scrollIntoView({ behavior: "smooth", block: "start" });
        });
    }

    if (pasteClose) {
        const newClose = pasteClose.cloneNode(true);
        pasteClose.parentNode.replaceChild(newClose, pasteClose);
        newClose.addEventListener("click", () => {
            if (pasteArea) pasteArea.classList.add("hidden");
        });
    }
}
```

**Step 4: Add renderConflictSummary() function**

```javascript
function renderConflictSummary(conflicts) {
    const el = document.getElementById("conflict-summary");
    if (!el) return;
    el.replaceChildren();

    if (conflicts.length === 0) {
        el.classList.add("hidden");
        return;
    }

    // Count by severity
    const counts = { critical: 0, high: 0, medium: 0, low: 0 };
    for (const c of conflicts) {
        const sev = (c.severity || "high").toLowerCase();
        if (counts[sev] !== undefined) counts[sev]++;
        else counts.high++;
    }

    const severities = [
        { key: "critical", label: "critical", cls: "summary-critical" },
        { key: "high", label: "high", cls: "summary-high" },
        { key: "medium", label: "medium", cls: "summary-medium" },
        { key: "low", label: "low", cls: "summary-low" },
    ];

    let first = true;
    for (const { key, label, cls } of severities) {
        if (counts[key] === 0) continue;
        if (!first) {
            const sep = document.createElement("span");
            sep.className = "conflict-summary-separator";
            sep.textContent = "\u00B7";
            el.appendChild(sep);
        }
        const item = document.createElement("span");
        item.className = "conflict-summary-count " + cls;
        item.textContent = counts[key] + " " + label;
        el.appendChild(item);
        first = false;
    }

    // Total affected services
    const allServices = new Set();
    for (const c of conflicts) {
        for (const s of (c.services || [])) allServices.add(s);
    }
    const total = document.createElement("span");
    total.className = "conflict-summary-total";
    total.textContent = allServices.size + " service" + (allServices.size !== 1 ? "s" : "") + " affected";
    el.appendChild(total);

    el.classList.remove("hidden");
}
```

**Step 5: Update renderHealthBanner() to include severity nuance**

Replace the existing function:

```javascript
function renderHealthBanner(pipeline) {
    const icon = document.getElementById("health-banner-icon");
    const text = document.getElementById("health-banner-text");
    const actions = document.getElementById("health-banner-actions");
    if (!icon || !text || !actions) return;
    actions.replaceChildren();

    const conflicts = pipeline.conflicts || [];
    const svcCount = pipeline.media_service_count || state.services.length;

    if (conflicts.length === 0) {
        icon.className = "health-banner-icon health-ok";
        text.textContent = "All " + svcCount + " services healthy";
    } else {
        // Use error color only for critical/high, warning for medium/low
        const hasSevere = conflicts.some(c =>
            ["critical", "high"].includes((c.severity || "high").toLowerCase())
        );
        icon.className = "health-banner-icon " + (hasSevere ? "health-problem" : "health-caution-banner");
        text.textContent = conflicts.length + " issue" + (conflicts.length !== 1 ? "s" : "") +
            " found across " + svcCount + " services";

        const fixAll = document.createElement("button");
        fixAll.className = "btn btn-primary btn-sm";
        fixAll.textContent = "View Issues";
        fixAll.setAttribute("data-tooltip", "Scroll to the conflict details below");
        fixAll.addEventListener("click", () => scrollToConflicts());
        actions.appendChild(fixAll);
    }
}
```

**Step 6: Commit**

```bash
git add frontend/app.js
git commit -m "feat: add welcome text, action fork, conflict summary bar, severity-aware health banner"
```

---

### Task 5: Rewrite conflict cards — collapsible, "Why?", drill-down link, show-more

**Files:**
- Modify: `frontend/app.js:699-904` (renderConflictCards, renderConflictCard, toggleFixPreview)

**Step 1: Rewrite renderConflictCards()**

Replace at line 701:

```javascript
function renderConflictCards(conflicts) {
    const container = document.getElementById("conflict-cards");
    if (!container) return;
    container.replaceChildren();

    if (conflicts.length === 0) return;

    const MAX_VISIBLE = 5;

    for (let i = 0; i < conflicts.length; i++) {
        const card = renderConflictCard(conflicts[i], i);
        if (i >= MAX_VISIBLE) card.classList.add("hidden");
        container.appendChild(card);
    }

    // "Show more" button
    if (conflicts.length > MAX_VISIBLE) {
        const more = document.createElement("div");
        more.className = "conflicts-show-more";
        more.id = "conflicts-show-more";
        const btn = document.createElement("button");
        btn.textContent = "Show " + (conflicts.length - MAX_VISIBLE) + " more issues";
        btn.addEventListener("click", () => {
            container.querySelectorAll(".conflict-card.hidden").forEach(c => c.classList.remove("hidden"));
            more.remove();
        });
        more.appendChild(btn);
        container.appendChild(more);
    }

    // Generate fix plans for all conflicts
    generateFixPlans(conflicts);
}
```

**Step 2: Rewrite renderConflictCard() with collapse, "Why?", drill-down**

Replace at line 716:

```javascript
function renderConflictCard(conflict, index) {
    const card = document.createElement("div");
    card.className = "conflict-card conflict-" + (conflict.severity || "high") + " collapsed";
    card.setAttribute("data-conflict-index", index);

    // Header: severity badge + description + affected count + chevron
    const header = document.createElement("div");
    header.className = "conflict-card-header";

    const badge = document.createElement("span");
    badge.className = "conflict-severity severity-" + (conflict.severity || "high");
    badge.textContent = (conflict.severity || "HIGH").toUpperCase();
    badge.setAttribute("data-tooltip", _severityTooltip(conflict.severity));
    header.appendChild(badge);

    const desc = document.createElement("span");
    desc.className = "conflict-card-desc";
    desc.textContent = conflict.description || conflict.type || "Mount conflict";
    header.appendChild(desc);

    if (conflict.services && conflict.services.length > 0) {
        const count = document.createElement("span");
        count.className = "conflict-card-affected-count";
        count.textContent = conflict.services.length + " service" + (conflict.services.length !== 1 ? "s" : "");
        header.appendChild(count);
    }

    const chevron = document.createElement("span");
    chevron.className = "conflict-card-chevron";
    chevron.textContent = "\u25BC";
    header.appendChild(chevron);

    // Click header to expand/collapse
    header.addEventListener("click", () => {
        card.classList.toggle("collapsed");
    });

    card.appendChild(header);

    // Body (hidden when collapsed)
    const body = document.createElement("div");
    body.className = "conflict-card-body";

    // Affected services
    if (conflict.services && conflict.services.length > 0) {
        const affected = document.createElement("div");
        affected.className = "conflict-affected";
        affected.textContent = "Affects: " + conflict.services.join(", ");
        body.appendChild(affected);
    }

    // "Why does this matter?" section
    const whyText = _conflictWhyText(conflict);
    if (whyText) {
        const why = document.createElement("div");
        why.className = "conflict-why";
        why.textContent = whyText;
        body.appendChild(why);
    }

    // Fix plan container (populated async by generateFixPlans)
    const fixPlan = document.createElement("div");
    fixPlan.className = "fix-plan";
    fixPlan.id = "fix-plan-" + index;
    body.appendChild(fixPlan);

    // "See Full Analysis →" link
    const drillLink = document.createElement("span");
    drillLink.className = "conflict-drill-link";
    drillLink.textContent = "See Full Analysis \u2192";
    drillLink.setAttribute("data-tooltip", "Open the detailed analysis with solution YAML, RPM wizard, and step-by-step guidance");
    drillLink.addEventListener("click", (e) => {
        e.stopPropagation();
        drillIntoConflict(conflict);
    });
    body.appendChild(drillLink);

    card.appendChild(body);
    return card;
}
```

**Step 3: Add helper functions**

```javascript
function _severityTooltip(severity) {
    const tips = {
        critical: "Critical issues break imports and hardlinks. Fix these first.",
        high: "High severity \u2014 likely causing failed imports or wasted disk space.",
        medium: "Medium severity \u2014 may cause issues in some configurations.",
        low: "Low severity \u2014 a best-practice recommendation.",
    };
    return tips[(severity || "high").toLowerCase()] || tips.high;
}

function _conflictWhyText(conflict) {
    const type = (conflict.type || "").toLowerCase();
    if (type.includes("mount") || type.includes("path")) {
        return "When services use different host paths for the same data, hardlinks can't be created between them. " +
            "This forces the system to copy files instead, using double the disk space and slowing imports.";
    }
    if (type.includes("permission") || type.includes("puid") || type.includes("pgid")) {
        return "Services running as different users can't read each other's files. " +
            "Aligning PUID/PGID ensures all apps can access shared media and download directories.";
    }
    if (type.includes("remote") || type.includes("mapping")) {
        return "The download client and *arr app see the downloaded file at different paths. " +
            "A remote path mapping or shared volume mount resolves this mismatch.";
    }
    return "This configuration may cause import failures or prevent hardlinks between services. " +
        "See the full analysis for a detailed explanation and fix.";
}

function drillIntoConflict(conflict) {
    // Find the stack for the first affected service
    const svcName = (conflict.services || [])[0];
    const svc = state.services.find(s => s.service_name === svcName);
    if (!svc) {
        showSimpleToast("Could not find stack for " + svcName, "error");
        return;
    }

    // Build a stack object compatible with runAnalysis()
    const stack = {
        path: svc.stack_path || "",
        compose_file: svc.compose_file || "docker-compose.yml",
        services: (conflict.services || []),
    };

    // Hide dashboard, show analysis
    hide("pipeline-dashboard");
    state.parsedError = state.pastedError; // pass through any pasted error context
    runAnalysis(stack);
}
```

**Step 4: Commit**

```bash
git add frontend/app.js
git commit -m "feat: collapsible conflict cards with severity tooltips, Why section, drill-down links"
```

---

### Task 6: Enrich service groups — scroll containment, tooltips, detail panel enrichment

**Files:**
- Modify: `frontend/app.js:517-697` (renderServiceGroups, renderServiceRow, toggleServiceDetail)

**Step 1: Add scroll containment to renderServiceGroups()**

In the `renderServiceGroups()` function, after `group.appendChild(list)` and before `container.appendChild(group)`, add:

```javascript
// Scroll containment for groups with many services
if (services.length > 6) {
    list.classList.add("scrollable");
}
```

**Step 2: Add role descriptions to group headers**

Add a role description map and update the header rendering:

```javascript
const ROLE_DESCRIPTIONS = {
    arr: "Media management apps — Sonarr, Radarr, Lidarr, etc.",
    download_client: "Download clients — qBittorrent, SABnzbd, NZBGet, etc.",
    media_server: "Media servers — Plex, Jellyfin, Emby",
    request: "Request apps — Overseerr, Ombi, Petio",
    other: "Other media-related services",
};
```

Update the group header to include a tooltip:

```javascript
header.setAttribute("data-tooltip", ROLE_DESCRIPTIONS[key] || "");
```

**Step 3: Add tooltips to service rows**

In `renderServiceRow()`, add tooltips to the health dot and family name:

```javascript
// Health dot tooltip
dot.setAttribute("data-tooltip", _healthDotTooltip(getServiceHealth(svc)));
```

For the family meta text:

```javascript
meta.setAttribute("data-tooltip", _familyTooltip(svc.family_name));
```

Add helper functions:

```javascript
function _healthDotTooltip(health) {
    const tips = {
        healthy: "No issues detected \u2014 mount paths are consistent",
        issue: "This service has a path conflict \u2014 click to see details",
        awaiting: "Fix has been applied \u2014 restart the container to take effect",
        "health-caution": "Internally OK but misaligned with your broader pipeline",
    };
    return tips[health] || "Status unknown";
}

function _familyTooltip(family) {
    const tips = {
        "LinuxServer.io": "Uses PUID/PGID environment variables for permissions",
        "Hotio": "Uses PUID/PGID environment variables for permissions",
        "jlesage": "Uses USER_ID/GROUP_ID environment variables",
        "Binhex": "Uses PUID/PGID with additional VPN support",
        "Official Plex": "Official Plex image \u2014 uses PLEX_UID/PLEX_GID",
        "Jellyfin": "Official Jellyfin image \u2014 uses PUID/PGID or --user flag",
    };
    return tips[family] || "";
}
```

**Step 4: Enrich toggleServiceDetail() with conflict summary + Analyze button**

After the Pipeline section in `toggleServiceDetail()`, add:

```javascript
// Conflict summary for this service
const svcConflicts = (state.pipeline.conflicts || []).filter(c =>
    (c.services || []).includes(svc.service_name)
);
if (svcConflicts.length > 0) {
    const conflictDiv = document.createElement("div");
    conflictDiv.className = "detail-conflicts";
    conflictDiv.textContent = "\u26A0 " + svcConflicts.length + " issue" +
        (svcConflicts.length !== 1 ? "s" : "") + " affecting this service";
    panel.appendChild(conflictDiv);
}

// Action buttons
const actionRow = document.createElement("div");
actionRow.className = "detail-action-row";

const analyzeBtn = document.createElement("button");
analyzeBtn.className = "btn btn-primary btn-sm";
analyzeBtn.textContent = "Analyze Stack \u2192";
analyzeBtn.setAttribute("data-tooltip", "Open full analysis with solution YAML, RPM wizard, and fix guidance");
analyzeBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    const stack = {
        path: svc.stack_path || "",
        compose_file: svc.compose_file || "docker-compose.yml",
        services: [svc.service_name],
    };
    hide("pipeline-dashboard");
    runAnalysis(stack);
});
actionRow.appendChild(analyzeBtn);

if (svcConflicts.length > 0) {
    const viewIssue = document.createElement("button");
    viewIssue.className = "btn btn-ghost btn-sm";
    viewIssue.textContent = "View Issues";
    viewIssue.addEventListener("click", (e) => {
        e.stopPropagation();
        // Find and scroll to the first conflict card for this service
        const idx = findConflictForService(svc.service_name);
        if (idx !== null) scrollToConflict(idx);
    });
    actionRow.appendChild(viewIssue);
}

panel.appendChild(actionRow);
```

Also add tooltips to volume lines in the detail panel:

```javascript
// Add tooltip to each volume line
line.setAttribute("data-tooltip", "Host path mapped into the container. Services need matching host paths for hardlinks to work.");
line.setAttribute("data-tooltip-pos", "below");
```

And to pipeline context:

```javascript
ctxLine.setAttribute("data-tooltip", siblings.length > 0
    ? "Services sharing a mount root can hardlink files between each other instead of copying"
    : "This service doesn't share data directories with other media services");
```

**Step 5: Commit**

```bash
git add frontend/app.js
git commit -m "feat: scroll containment, tooltips, conflict summary, and analyze button in service details"
```

---

### Task 7: Update enablePasteBar() for new paste-area structure

**Files:**
- Modify: `frontend/app.js:1193-1322` (enablePasteBar and related)

**Step 1: Update enablePasteBar()**

The paste bar elements moved from `#paste-bar` to `#paste-area`. Update element IDs referenced:

```javascript
function enablePasteBar() {
    const input = document.getElementById("paste-error-input");
    const btn = document.getElementById("paste-error-go");
    if (!input || !btn) return;

    // Enable the button once text is entered
    input.addEventListener("input", () => {
        btn.disabled = !input.value.trim();
    });

    // Wire up Analyze button (remove old listeners by cloning)
    const newBtn = btn.cloneNode(true);
    btn.parentNode.replaceChild(newBtn, btn);
    newBtn.disabled = !input.value.trim();
    newBtn.addEventListener("click", handlePasteError);

    // Enter to submit
    input.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            handlePasteError();
        }
    });

    // Wire example pills
    document.querySelectorAll(".paste-pill").forEach(pill => {
        const newPill = pill.cloneNode(true);
        pill.parentNode.replaceChild(newPill, pill);
        newPill.addEventListener("click", () => {
            const type = newPill.getAttribute("data-example");
            input.value = getPasteExample(type);
            input.focus();
            // Enable the button
            const goBtn = document.getElementById("paste-error-go");
            if (goBtn) goBtn.disabled = false;
        });
    });
}
```

**Step 2: Commit**

```bash
git add frontend/app.js
git commit -m "feat: update paste bar wiring for new paste-area structure"
```

---

### Task 8: Add tooltip to scan path input + health-caution-banner CSS

**Files:**
- Modify: `frontend/index.html` (scan path input tooltip)
- Modify: `frontend/styles.css` (health-caution-banner class)

**Step 1: Add tooltip to scan path input**

In `index.html`, add `data-tooltip` to the path input (line 28):

```html
<input type="text" id="header-path-input" class="path-input"
       placeholder="/path/to/stacks" spellcheck="false"
       data-tooltip="The root directory containing your Docker Compose stacks"
       data-tooltip-pos="below"
       aria-label="Enter new stacks path" />
```

**Step 2: Add health-caution-banner CSS**

```css
.health-banner-icon.health-caution-banner {
    background: var(--warning);
    box-shadow: 0 0 6px rgba(210, 153, 34, 0.5);
}
```

**Step 3: Commit**

```bash
git add frontend/index.html frontend/styles.css
git commit -m "feat: scan path tooltip, health-caution-banner styling"
```

---

### Task 9: Integration testing and polish

**Step 1: Start the server and verify the full flow**

```bash
cd /c/Projects/maparr && python -m uvicorn backend.main:app --host 0.0.0.0 --port 9494
```

**Step 2: Test checklist**

- [ ] Boot sequence completes with no JS errors
- [ ] Dashboard shows welcome text with correct counts
- [ ] Two-action fork cards render and respond to clicks
- [ ] Clicking "Paste an Error" opens the paste area with focus on textarea
- [ ] Clicking "Explore Pipeline" scrolls to service groups
- [ ] Paste area close button works
- [ ] Example pills fill the textarea and enable the Analyze button
- [ ] Conflict summary bar shows correct severity counts
- [ ] Conflict cards render collapsed with severity badge, description, affected count
- [ ] Clicking conflict header expands/collapses the card body
- [ ] "Why does this matter?" text appears in expanded cards
- [ ] "See Full Analysis →" link navigates to deep analysis view
- [ ] "Back to Dashboard" returns to the pipeline dashboard
- [ ] Max 5 conflict cards visible, "Show N more" button works
- [ ] Service groups have scroll containment when >6 services
- [ ] Health legend renders with all 5 dot states
- [ ] Health dot tooltips show on hover
- [ ] Severity badge tooltips show on hover
- [ ] Family name tooltips show on hover
- [ ] Service detail panel shows conflict summary + "Analyze Stack →" button
- [ ] "Analyze Stack →" opens full analysis flow
- [ ] Browse button in header opens path editor (or directory picker if available)
- [ ] No console errors throughout

**Step 3: Fix any issues discovered during testing**

**Step 4: Final commit**

```bash
git add -A
git commit -m "fix: polish and integration fixes from testing"
```
