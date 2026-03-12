# Phase 1+2 Polish — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Ship 6 remaining UX improvements to make MapArr beta-ready — verbose logging, *arr-specific language, multi-error support, and boot/browse clarity.

**Architecture:** All changes are frontend (app.js, index.html) and backend (parser.py, analyzer.py). No new files. Multi-error parsing extends the existing parser with a splitter + new API response shape. Log verbosity adds more `logger.info()` calls to the analyzer. Everything else is HTML/JS text and attribute changes.

**Tech Stack:** Vanilla JS frontend, Python FastAPI backend, SSE for log streaming.

---

## Task 1: Log Panel — Auto-Open on Analysis Start

**Files:**
- Modify: `frontend/app.js` — `runAnalysis()` at line 1729, `addLogEntry()` at line 5922

**Step 1: Add auto-open trigger in runAnalysis()**

In `runAnalysis()` (line 1729), after `setTerminalDots("running")` (line 1735), add a call to pulse/open the log panel so users see backend logs flowing during analysis:

```javascript
// After line 1735: setTerminalDots("running");
// Auto-expand log panel during analysis so users see detailed backend logs.
// Uses a gentle approach: if panel is closed, pulse the badge to draw attention
// rather than forcing the panel open (which could be jarring).
if (!_logState.panelOpen) {
    const badge = document.getElementById("log-badge");
    const toggle = document.getElementById("footer-log-toggle");
    if (toggle) {
        toggle.classList.add("log-toggle-pulse");
        // Auto-remove pulse after analysis completes or user opens panel
        setTimeout(() => toggle.classList.remove("log-toggle-pulse"), 15000);
    }
}
```

**Step 2: Add pulse CSS animation**

In `frontend/styles.css`, after the `.log-badge` styles (find the log badge section near line 3100+), add:

```css
/* Pulse animation to draw attention to log panel during analysis */
.log-toggle-pulse {
    animation: log-pulse 1.5s ease-in-out 3;
}

@keyframes log-pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.4; transform: scale(1.15); }
}
```

**Step 3: Clear pulse when panel opens**

In `openLogPanel()` (app.js line 5720), add after the panel is shown:

```javascript
// Clear any analysis-triggered pulse
const toggle = document.getElementById("footer-log-toggle");
if (toggle) toggle.classList.remove("log-toggle-pulse");
```

**Step 4: Test manually**

1. Open MapArr in browser
2. Click a stack to analyze
3. Observe: footer log button should pulse 3 times
4. Click it — panel opens, pulse stops, live logs visible

**Step 5: Commit**

```
feat(logs): pulse log panel toggle during analysis to draw attention
```

---

## Task 2: Log Panel — More Verbose Backend Logging

**Files:**
- Modify: `backend/analyzer.py` — the `analyze()` function starting at line 370

**Step 1: Add verbose INFO logs at each analysis phase**

The analyzer already logs key events, but the logs are terse. Add human-readable detail at each phase boundary. Insert these logger calls at the indicated positions in `analyzer.py`:

After line 376 (`logger.info("Starting analysis of %s...")`), add:
```python
logger.info("Step 1/6: Reading compose file and identifying services...")
```

After line 394 (`steps.append(...Found N services...)`) add:
```python
for svc_info in services:
    svc_name = svc_info.get("name", "unknown")
    vol_count = len(svc_info.get("volumes", []))
    logger.info("  → %s: %d volume mount%s", svc_name, vol_count, "s" if vol_count != 1 else "")
    for v in svc_info.get("volumes", []):
        logger.info("    %s → %s", v.get("source", "?"), v.get("target", "?"))
```

After line 405 (`steps.append(...Scanned N volume mounts...)`) add:
```python
logger.info("Step 2/6: Checking for path conflicts between services...")
```

After line 415/426 (conflict/permission result) add:
```python
logger.info("Step 3/6: Running permission checks (PUID/PGID/UMASK)...")
```

After line 438 (mount classification) add:
```python
logger.info("Step 4/6: Classifying mount types (data vs config vs remote)...")
for mc in mount_classifications:
    logger.info("  → %s (%s) — %s", mc.get("source", "?"), mc.get("classification", "?"), mc.get("target", "?"))
```

After line 454 (platform check) add:
```python
logger.info("Step 5/6: Running platform compatibility checks...")
```

Before line 501 (fix generation) add:
```python
logger.info("Step 6/6: Generating fix recommendations...")
```

After line 663 (final) add or enhance the existing completion log:
```python
logger.info("Analysis complete for %s: %s — %d service%s, %d conflict%s, %d permission issue%s",
            stack_name, final_status,
            len(services), "s" if len(services) != 1 else "",
            len(conflicts), "s" if len(conflicts) != 1 else "",
            len(perm_conflicts), "s" if len(perm_conflicts) != 1 else "")
```

**Step 2: Verify log output**

1. Open MapArr, open log panel, set filter to "All levels"
2. Analyze a stack
3. Verify: ~15-25 log lines should appear with step-by-step detail
4. Each volume mount, each service, each classification should be visible

**Step 3: Commit**

```
feat(logs): add verbose step-by-step logging during analysis for user transparency
```

---

## Task 3: Error Input Language — *arr-Specific Terminology

**Files:**
- Modify: `frontend/index.html` — lines 89-113
- Modify: `frontend/app.js` — lines 886-893 (`_ERROR_KEYWORDS`), lines 952-962 (`fillExample`)

**Step 1: Update intro text in index.html**

Replace lines 89-91:
```html
<p class="step-desc">
    Paste the error from your *arr app's Activity > Queue page or System > Status health check.
    MapArr will extract the service, path, and error type automatically.
</p>
```

With:
```html
<p class="step-desc">
    Paste the error from Sonarr, Radarr, or any *arr app — check Activity &gt; Queue
    or System &gt; Status. MapArr detects the service, path, and error type automatically.
</p>
```

**Step 2: Add hardlink example pill**

Replace lines 101-106 in index.html:
```html
<div class="example-errors" id="example-errors">
    <span class="example-label">Try an example:</span>
    <button class="example-pill" onclick="fillExample('import')">Import failed</button>
    <button class="example-pill" onclick="fillExample('remote')">Remote path mapping</button>
    <button class="example-pill" onclick="fillExample('hardlink')">Hardlink / atomic move</button>
    <button class="example-pill" onclick="fillExample('permission')">Permission denied</button>
</div>
```

**Step 3: Add hardlink example + polish existing examples in app.js**

Replace the `examples` object in `fillExample()` (app.js ~952-957):
```javascript
const examples = {
    import: "Import failed, path does not exist or is not accessible by Sonarr: /data/tv/Show Name/Season 01/Episode.mkv. Ensure the path exists and the user running Sonarr has the correct permissions to access this file.",
    remote: "Download client qBittorrent places downloads in /downloads/tv but this directory does not appear to exist inside the container. You may need a Remote Path Mapping in Radarr (Settings > Download Clients > Remote Path Mappings).",
    hardlink: "Invalid cross-device link: rename '/downloads/complete/Movie.Name.2024.mkv' -> '/data/media/movies/Movie Name (2024)/Movie.Name.2024.mkv'. Sonarr cannot create hardlinks across different mount points.",
    permission: "Access to the path '/data/media/movies/Movie Name (2024)' is denied. Radarr does not have permission to write to this directory. Check PUID/PGID match between containers.",
};
```

**Step 4: Add "hardlink" to _ERROR_KEYWORDS in app.js**

After line 890 (`"cross-device link"` entry), add:
```javascript
"hardlink failure": /\b(hardlink|hard\s*link|atomic\s+move)\b/i,
```

**Step 5: Update the browse button text (P2-2)**

Replace line 111-113 in index.html:
```html
<button class="btn btn-subtle" onclick="switchToBrowseMode()" title="Skip error diagnosis — browse all your stacks and check for path issues directly">
    <span class="btn-icon">&#128269;</span> Skip — browse stacks instead
</button>
```

**Step 6: Test manually**

1. Open MapArr → Fix mode
2. Verify intro text mentions Sonarr/Radarr
3. Click each example pill — verify realistic *arr errors fill in
4. Click "Skip — browse stacks instead" — verify tooltip on hover
5. Type "hardlink" in textarea — verify live preview shows "hardlink failure" pill

**Step 7: Commit**

```
feat(ux): use *arr-specific terminology in error input and examples
```

---

## Task 4: Boot Path Visibility (P2-1 + P2-6)

**Files:**
- Modify: `frontend/app.js` — `runBootSequence()` at line 99

**Step 1: Show scan path in boot terminal**

The boot sequence already shows directory paths at line 148 (`await bootAddLine("ok", displayPath + ...)`), but the FIRST line just says "Scanning for Docker stacks..." (line 124) without naming where.

Replace line 124:
```javascript
const scanPath = (discData.scan_path || "").replace(/\\/g, "/");
const displayScanPath = scanPath.length > 40 ? "..." + scanPath.slice(-37) : scanPath;
await bootAddLine("run", "Scanning " + (displayScanPath || "default locations") + "...", 400);
```

This replaces the generic "Scanning for Docker stacks..." with "Scanning /path/to/stacks..." so the user immediately knows where MapArr is looking.

**Step 2: Test manually**

1. Reload MapArr
2. Watch boot terminal
3. Verify: first scanning line shows the actual path, not generic text

**Step 3: Commit**

```
feat(boot): show scan path in boot terminal for immediate clarity
```

---

## Task 5: Multiple Errors — Backend Parser

**Files:**
- Modify: `backend/parser.py` — add `parse_errors()` (plural) function
- Modify: `backend/main.py` — update `/api/parse-error` endpoint

**Step 1: Add error splitter to parser.py**

Add a new function `split_errors()` before the existing `parse_error()` (after line 64):

```python
# ─── Multi-Error Splitting ───

# Delimiters that indicate separate errors in user-pasted text.
# Users often paste multiple log lines, Activity > Queue entries, or
# System > Status health checks in one block.
_ERROR_SPLIT_PATTERNS = [
    r'\n\s*\n',                          # Double newline (paragraph break)
    r'\n(?=\[(?:WARN|ERROR|INFO)\])',     # Log-style lines: [WARN] ... [ERROR] ...
    r'\n(?=(?:Import|Download)\s+(?:failed|error))',  # Repeated error prefixes
]

_SPLIT_REGEX = re.compile('|'.join(_ERROR_SPLIT_PATTERNS), re.IGNORECASE)


def split_errors(text: str) -> list[str]:
    """
    Split user input into individual error blocks.

    Returns a list of non-empty stripped strings. If no split points are
    found, returns the original text as a single-element list.
    """
    if not text or not text.strip():
        return []

    chunks = _SPLIT_REGEX.split(text.strip())
    # Filter out empty/whitespace-only chunks and very short fragments
    result = [c.strip() for c in chunks if c and c.strip() and len(c.strip()) > 10]

    return result if result else [text.strip()]


def parse_errors(text: str) -> list[dict]:
    """
    Parse potentially multiple errors from user input.

    Returns a list of ParsedError dicts. Each has an additional 'index'
    field (0-based) and 'excerpt' field (first 80 chars for UI display).
    """
    chunks = split_errors(text)
    results = []

    for i, chunk in enumerate(chunks):
        parsed = parse_error(chunk)
        d = parsed.to_dict()
        d["index"] = i
        d["excerpt"] = chunk[:80] + ("..." if len(chunk) > 80 else "")
        results.append(d)

    logger.info("Multi-error parse: %d chunk%s from %d chars input",
                len(results), "s" if len(results) != 1 else "", len(text))

    return results
```

**Step 2: Update /api/parse-error endpoint in main.py**

Replace the endpoint (lines 133-165) to return both the primary result AND the full list when multiple errors are detected:

Find this section in main.py:
```python
    # Parse — always succeeds, returns confidence level
    result = parse_error(error_text)
    _session["parsed_error"] = result.to_dict()
```

Replace with:
```python
    # Parse — check for multiple errors first
    from backend.parser import parse_errors
    all_results = parse_errors(error_text)

    # Primary result is the first (or only) error
    result = parse_error(error_text)
    primary = result.to_dict()
    _session["parsed_error"] = primary

    # Include multi-error data when >1 error detected
    if len(all_results) > 1:
        primary["multiple_errors"] = all_results
        primary["error_count"] = len(all_results)
```

Keep the rest of the endpoint unchanged (the logger.info and return).

**Step 3: Test backend**

Run: `curl -X POST http://localhost:9494/api/parse-error -H "Content-Type: application/json" -d '{"error_text": "Import failed, path does not exist by Sonarr: /data/tv/Show\n\nDownload client qBittorrent places downloads in /downloads/tv but not reachable from Radarr. Remote path mapping needed."}'`

Expected: Response includes `"multiple_errors": [...]` with 2 entries and `"error_count": 2`.

**Step 4: Commit**

```
feat(parser): split multi-error input into individual parseable chunks
```

---

## Task 6: Multiple Errors — Frontend Selection UI

**Files:**
- Modify: `frontend/app.js` — `parseError()` at line 572, `showParseResult()` at line 797
- Modify: `frontend/styles.css` — add multi-error picker styles

**Step 1: Update parseError() to handle multiple errors**

Replace the success handler in `parseError()` (app.js lines 606-610):

```javascript
// Old:
// state.parsedError = await resp.json();
// showParseResult(state.parsedError);
// await autoMatchStacks(state.parsedError);

// New:
const data = await resp.json();

if (data.multiple_errors && data.multiple_errors.length > 1) {
    // Multiple errors detected — let user pick which to analyze
    showMultiErrorPicker(data.multiple_errors);
} else {
    state.parsedError = data;
    showParseResult(data);
    await autoMatchStacks(data);
}
```

**Step 2: Add showMultiErrorPicker() function**

Add after `showParseResult()` (after line 863):

```javascript
// ─── Multi-Error Picker ───

function showMultiErrorPicker(errors) {
    const container = document.getElementById("step-parse-result");
    const details = document.getElementById("parse-details");
    details.replaceChildren();

    // Header
    const header = document.createElement("div");
    header.className = "multi-error-header";
    header.textContent = "We found " + errors.length + " errors in your input. Select which to diagnose:";
    details.appendChild(header);

    // Error list
    const list = document.createElement("div");
    list.className = "multi-error-list";

    errors.forEach((err, i) => {
        const item = document.createElement("button");
        item.className = "multi-error-item";
        item.type = "button";

        // Error number + service badge
        const num = document.createElement("span");
        num.className = "multi-error-num";
        num.textContent = "#" + (i + 1);
        item.appendChild(num);

        const body = document.createElement("div");
        body.className = "multi-error-body";

        // Top line: service + error type
        const meta = document.createElement("div");
        meta.className = "multi-error-meta";
        if (err.service) {
            const svcBadge = document.createElement("span");
            svcBadge.className = "multi-error-svc";
            svcBadge.textContent = err.service;
            meta.appendChild(svcBadge);
        }
        if (err.error_type) {
            const typeBadge = document.createElement("span");
            typeBadge.className = "multi-error-type";
            typeBadge.textContent = err.error_type.replace(/_/g, " ");
            meta.appendChild(typeBadge);
        }
        if (!err.service && !err.error_type) {
            const unknown = document.createElement("span");
            unknown.className = "multi-error-type";
            unknown.textContent = "unrecognized";
            meta.appendChild(unknown);
        }
        body.appendChild(meta);

        // Excerpt line
        const excerpt = document.createElement("div");
        excerpt.className = "multi-error-excerpt";
        excerpt.textContent = err.excerpt;
        body.appendChild(excerpt);

        item.appendChild(body);

        // Confidence indicator
        const conf = document.createElement("span");
        conf.className = "confidence-badge confidence-" + err.confidence;
        conf.textContent = err.confidence;
        item.appendChild(conf);

        item.addEventListener("click", () => selectError(err, i));
        list.appendChild(item);
    });

    details.appendChild(list);
    container.classList.remove("hidden");
}

async function selectError(err, index) {
    // Re-parse just this error's raw input to get full result
    state.parsedError = err;
    showParseResult(err);
    await autoMatchStacks(err);
}
```

**Step 3: Add CSS for multi-error picker**

In `frontend/styles.css`, add after the `.parse-suggestions` styles:

```css
/* ─── Multi-Error Picker ─── */

.multi-error-header {
    color: var(--text-secondary);
    font-size: 0.9rem;
    margin-bottom: 0.75rem;
    line-height: 1.5;
}

.multi-error-list {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
}

.multi-error-item {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    padding: 0.65rem 0.85rem;
    background: var(--bg-input);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    cursor: pointer;
    text-align: left;
    color: var(--text);
    transition: border-color 0.15s, background 0.15s;
    width: 100%;
}

.multi-error-item:hover {
    border-color: var(--accent);
    background: rgba(var(--accent-rgb, 99, 102, 241), 0.06);
}

.multi-error-num {
    font-size: 0.75rem;
    font-weight: 700;
    color: var(--text-muted);
    min-width: 1.5rem;
    flex-shrink: 0;
}

.multi-error-body {
    flex: 1;
    min-width: 0;
    overflow: hidden;
}

.multi-error-meta {
    display: flex;
    gap: 0.4rem;
    margin-bottom: 0.25rem;
}

.multi-error-svc {
    font-size: 0.75rem;
    font-weight: 600;
    color: var(--accent);
    text-transform: capitalize;
}

.multi-error-type {
    font-size: 0.75rem;
    color: var(--text-muted);
}

.multi-error-excerpt {
    font-size: 0.8rem;
    color: var(--text-secondary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}
```

**Step 4: Test manually**

1. Open MapArr → Fix mode
2. Paste two errors separated by a blank line:
   ```
   Import failed, path does not exist or is not accessible by Sonarr: /data/tv/Show Name/Season 01/Episode.mkv

   Download client qBittorrent places downloads in /downloads/tv but this directory is not reachable from Radarr. Remote path mapping may be needed.
   ```
3. Click "Analyze Error"
4. Verify: picker shows 2 errors with service badges (sonarr, radarr)
5. Click one — should show parse result + auto-match stacks

**Step 5: Test single error still works**

1. Clear textarea, paste single error
2. Click "Analyze Error"
3. Verify: goes straight to parse result (no picker)

**Step 6: Commit**

```
feat(multi-error): detect and let users select from multiple pasted errors
```

---

## Execution Order

Tasks 1-4 are independent and can be parallelized.
Task 5 must complete before Task 6 (backend before frontend).

```
Task 1 (log pulse)     ─┐
Task 2 (log verbosity) ─┤
Task 3 (error language) ─┼─→ Task 5 (multi-error backend) → Task 6 (multi-error frontend)
Task 4 (boot path)     ─┘
```

Total: ~6 tasks, estimated 45-60 minutes execution.
