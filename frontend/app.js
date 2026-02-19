/**
 * MapArr v1.0 — Frontend Application
 *
 * Two-mode UI:
 *   FIX MODE  — paste error → auto-match stack → focused analysis
 *   BROWSE MODE — browse all stacks → pick one → analyze
 *
 * XSS safety: All user-derived content uses textContent, never innerHTML.
 * Container clearing uses replaceChildren() instead of innerHTML.
 */

"use strict";

// ─── State ───

const state = {
    mode: null,          // "fix" or "browse"
    parsedError: null,   // Result from /api/parse-error
    stacks: [],          // Result from /api/discover-stacks
    selectedStack: null, // User's chosen stack path
    allDetectedDirs: [], // Original detected dirs [{path, count}] — persists across rescans
    customDirs: [],      // User-added dirs via manual entry — persisted to localStorage
    activeScanPath: "",  // Currently active scan path
};

// Load persisted custom dirs from localStorage
try {
    const saved = localStorage.getItem("maparr_custom_dirs");
    if (saved) state.customDirs = JSON.parse(saved);
} catch {}

function saveCustomDirs() {
    try { localStorage.setItem("maparr_custom_dirs", JSON.stringify(state.customDirs)); } catch {}
}

function addCustomDir(path, count) {
    const norm = path.replace(/\\/g, "/");
    if (!state.customDirs.some((d) => d.path.replace(/\\/g, "/") === norm)) {
        state.customDirs.push({ path, count });
        saveCustomDirs();
    } else {
        // Update count
        const existing = state.customDirs.find((d) => d.path.replace(/\\/g, "/") === norm);
        if (existing) existing.count = count;
        saveCustomDirs();
    }
}

function removeCustomDir(path) {
    const norm = path.replace(/\\/g, "/");
    state.customDirs = state.customDirs.filter((d) => d.path.replace(/\\/g, "/") !== norm);
    saveCustomDirs();
}

// ─── Init ───

document.addEventListener("DOMContentLoaded", () => {
    checkHealth();

    // Ctrl+Enter in textarea triggers parse
    const textarea = document.getElementById("error-input");
    if (textarea) {
        textarea.addEventListener("keydown", (e) => {
            if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
                e.preventDefault();
                parseError();
            }
        });

        // Live preview — debounced client-side pattern detection
        let previewTimer = null;
        textarea.addEventListener("input", () => {
            clearTimeout(previewTimer);
            previewTimer = setTimeout(() => {
                updateLivePreview(textarea.value.trim());
            }, 300);
        });
    }

    // Enter in path input triggers scan
    const pathInput = document.getElementById("custom-path-input");
    if (pathInput) {
        pathInput.addEventListener("keydown", (e) => {
            if (e.key === "Enter") {
                e.preventDefault();
                changeStacksPath();
            }
        });
    }
});

// ─── Mode Management ───

function enterFixMode() {
    state.mode = "fix";
    document.body.setAttribute("data-mode", "fix");
    document.getElementById("step-mode").classList.add("hidden");
    document.getElementById("step-error").classList.remove("hidden");
    // Force-hide all browse mode elements (kills any in-flight showStackSelection)
    document.getElementById("step-stacks").classList.add("hidden");
    document.getElementById("stacks-loading").classList.add("hidden");
    document.getElementById("stacks-list").classList.add("hidden");
    document.getElementById("error-input").focus();
}

function enterBrowseMode() {
    state.mode = "browse";
    document.body.setAttribute("data-mode", "browse");
    document.getElementById("step-mode").classList.add("hidden");
    document.getElementById("step-error").classList.add("hidden");
    document.getElementById("step-parse-result").classList.add("hidden");
    document.getElementById("step-fix-match").classList.add("hidden");
    document.getElementById("step-stacks").classList.remove("hidden");
    showStackSelection();
}

function switchToFixMode() {
    clearAnalysisResults();
    document.getElementById("step-stacks").classList.add("hidden");
    enterFixMode();
}

function switchToBrowseMode() {
    clearAnalysisResults();
    document.getElementById("step-error").classList.add("hidden");
    document.getElementById("step-parse-result").classList.add("hidden");
    document.getElementById("step-fix-match").classList.add("hidden");
    enterBrowseMode();
}

function startOver() {
    clearAnalysisResults();
    state.mode = null;
    document.body.removeAttribute("data-mode");
    state.parsedError = null;
    state.selectedStack = null;
    // Hide everything, show mode selector
    ["step-error", "step-parse-result", "step-fix-match", "step-stacks"].forEach((id) => {
        document.getElementById(id).classList.add("hidden");
    });
    // Restore full stack list if collapsed
    const stackList = document.getElementById("stacks-list");
    if (stackList) stackList.classList.remove("hidden");
    const summary = document.getElementById("selected-stack-summary");
    if (summary) summary.remove();
    // Clear textarea and filter
    const textarea = document.getElementById("error-input");
    if (textarea) textarea.value = "";
    const filterInput = document.getElementById("stack-filter-input");
    if (filterInput) filterInput.value = "";
    const filterDiv = document.getElementById("stack-filter");
    if (filterDiv) filterDiv.classList.add("hidden");
    // Show mode selector
    document.getElementById("step-mode").classList.remove("hidden");
    document.getElementById("step-mode").scrollIntoView({ behavior: "smooth", block: "start" });
}

// ─── Health Check ───

async function checkHealth() {
    const el = document.getElementById("health-status");
    try {
        const resp = await fetch("/api/health");
        if (resp.ok) {
            const healthData = await resp.json();
            const runningVersion = healthData.version || "1.0.0";

            el.className = "header-status connected";

            // Populate footer version from backend
            updateFooterVersion(runningVersion);

            // Check GitHub for newer release and star count (non-blocking)
            checkForUpdates(runningVersion);
            fetchStarCount();

            // Reset custom path so initial scan finds everything
            try { await fetch("/api/change-stacks-path", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ path: "" }),
            }); } catch {}
            // Fetch full stack summary for the header display
            try {
                const discResp = await fetch("/api/discover-stacks");
                if (discResp.ok) {
                    const discData = await discResp.json();
                    state.stacks = discData.stacks || [];
                    // Store original detected dirs on first load
                    if (state.allDetectedDirs.length === 0) {
                        const dirCounts = {};
                        state.stacks.forEach((s) => {
                            const parent = s.path.replace(/\\/g, "/").replace(/\/[^/]+$/, "");
                            dirCounts[parent] = (dirCounts[parent] || 0) + 1;
                        });
                        state.allDetectedDirs = Object.entries(dirCounts)
                            .sort((a, b) => b[1] - a[1])
                            .map(([path, count]) => ({ path, count }));
                    }
                    state.activeScanPath = (discData.scan_path || "").replace(/\\/g, "/");
                    updateConnectionStatus(discData);
                } else {
                    el.textContent = "Connected";
                }
            } catch {
                el.textContent = "Connected";
            }
        } else {
            el.textContent = "Backend error";
            el.className = "header-status disconnected";
        }
    } catch {
        el.textContent = "Offline";
        el.className = "header-status disconnected";
    }
}

// ─── Parse Error ───

async function parseError() {
    const textarea = document.getElementById("error-input");
    const text = textarea.value.trim();

    if (!text) {
        textarea.focus();
        return;
    }

    // Clear any previous analysis results
    clearAnalysisResults();
    document.getElementById("step-fix-match").classList.add("hidden");

    const btn = document.getElementById("btn-parse");
    btn.disabled = true;
    btn.textContent = "Analyzing...";

    try {
        const resp = await fetch("/api/parse-error", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ error_text: text }),
        });

        if (!resp.ok) {
            const err = await resp.json().catch(() => ({ error: "Request failed" }));
            alert(err.error || "Parse request failed");
            return;
        }

        state.parsedError = await resp.json();
        showParseResult(state.parsedError);

        // Auto-match: find stacks containing the detected service
        await autoMatchStacks(state.parsedError);
    } catch (err) {
        alert("Could not reach the backend. Is MapArr running?");
    } finally {
        btn.disabled = false;
        btn.textContent = "Analyze Error";
    }
}

// ─── Auto-Match Stacks (Fix Mode) ───

async function autoMatchStacks(parsed) {
    // Ensure we have stacks loaded
    if (state.stacks.length === 0) {
        try {
            const resp = await fetch("/api/discover-stacks");
            if (resp.ok) {
                const data = await resp.json();
                state.stacks = data.stacks || [];
            }
        } catch {}
    }

    const detectedService = (parsed.service || "").toLowerCase();
    const section = document.getElementById("step-fix-match");
    const heading = document.getElementById("fix-match-heading");
    const desc = document.getElementById("fix-match-desc");
    const list = document.getElementById("fix-match-list");
    const empty = document.getElementById("fix-match-empty");

    list.replaceChildren();
    empty.classList.add("hidden");

    // Filter stacks that contain the detected service
    let matches = [];
    if (detectedService) {
        matches = state.stacks.filter((s) =>
            (s.services || []).some((svc) => svc.toLowerCase().includes(detectedService))
        );
    }

    if (matches.length === 1) {
        // Single match — go straight to analysis, no ambiguity
        selectStack(matches[0], {});
    } else if (matches.length > 1) {
        // Multiple matches — ask the backend to figure out which one
        // most likely produced this error (volume layout analysis, path
        // reachability, conflict correlation — the full smart-match).
        try {
            const resp = await fetch("/api/smart-match", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    parsed_error: parsed,
                    candidate_paths: matches.map((s) => s.path),
                }),
            });

            if (resp.ok) {
                const result = await resp.json();

                if (result.confidence === "high" && result.best) {
                    // High confidence — auto-select, go straight to analysis
                    const bestStack = matches.find(
                        (s) => s.path.replace(/\\/g, "/") === result.best.path.replace(/\\/g, "/")
                    ) || matches[0];
                    selectStack(bestStack, {});
                    return;
                }

                if (result.confidence === "medium" && result.best) {
                    // Medium confidence — auto-select but show "wrong stack?" fallback
                    const bestStack = matches.find(
                        (s) => s.path.replace(/\\/g, "/") === result.best.path.replace(/\\/g, "/")
                    ) || matches[0];
                    // Store alternatives for the "wrong stack?" link
                    state.fixAlternatives = matches.filter(
                        (s) => s.path.replace(/\\/g, "/") !== bestStack.path.replace(/\\/g, "/")
                    );
                    state.fixDetectedService = detectedService;
                    selectStack(bestStack, {});
                    return;
                }
            }
        } catch {
            // Smart-match failed — fall through to pill picker
        }

        // Low confidence or smart-match unavailable — compact pill picker
        heading.textContent = "Which Stack Has the Problem?";
        desc.textContent = "We found " + detectedService + " in " +
            matches.length + " stacks. Pick the one throwing the error:";
        renderFixMatchPills(list, matches, detectedService);
        section.classList.remove("hidden");
        section.scrollIntoView({ behavior: "smooth", block: "nearest" });
    } else {
        // No match — prompt user instead
        heading.textContent = "Stack Not Found";
        if (detectedService && state.stacks.length > 0) {
            desc.textContent = "Couldn't find a stack containing " + detectedService + ". Load the right directory or switch to Analyze mode.";
        } else if (state.stacks.length === 0) {
            desc.textContent = "No stacks loaded yet. Use the scan path in the header to add your stacks directory.";
        } else {
            desc.textContent = "Couldn't identify the service from this error. Try Analyze mode to pick a stack manually.";
        }
        empty.classList.remove("hidden");
        section.classList.remove("hidden");
        section.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
}

/**
 * Render fix-mode matches as compact clickable pills.
 * Tight, focused — no full card layout. Health dot + name + service count.
 * Sorted: problem stacks first (most likely the culprit), then alphabetical.
 */
function renderFixMatchPills(container, matches, detectedService) {
    container.replaceChildren();

    // Sort: problem stacks first, then alphabetical
    const sorted = [...matches].sort((a, b) => {
        const healthOrder = { problem: 0, warning: 1, ok: 2, unknown: 3 };
        const ha = healthOrder[a.health] ?? 3;
        const hb = healthOrder[b.health] ?? 3;
        if (ha !== hb) return ha - hb;
        return extractDirName(a.path).toLowerCase().localeCompare(
            extractDirName(b.path).toLowerCase()
        );
    });

    const pillWrap = document.createElement("div");
    pillWrap.className = "fix-match-pills";

    sorted.forEach((stack) => {
        const pill = document.createElement("button");
        pill.className = "fix-match-pill";
        pill.title = stack.path.replace(/\\/g, "/");

        // Health dot
        const dot = document.createElement("span");
        dot.className = "health-dot health-" + (stack.health || "unknown");
        pill.appendChild(dot);

        // Stack name
        const name = document.createElement("span");
        name.className = "fix-pill-name";
        name.textContent = extractDirName(stack.path);
        pill.appendChild(name);

        // Service count hint
        const count = document.createElement("span");
        count.className = "fix-pill-count";
        count.textContent = stack.service_count + " svc";
        pill.appendChild(count);

        pill.addEventListener("click", () => selectStack(stack, {}));
        pillWrap.appendChild(pill);
    });

    container.appendChild(pillWrap);

    // Guidance hint below pills
    const hint = document.createElement("p");
    hint.className = "fix-match-hint";
    hint.textContent = "Not sure? Pick the stack where " + detectedService +
        " runs. Red dots indicate stacks with known volume issues.";
    container.appendChild(hint);
}

// ─── Show Parse Result ───

function showParseResult(parsed) {
    const container = document.getElementById("step-parse-result");
    const details = document.getElementById("parse-details");

    // Clear previous results safely
    details.replaceChildren();

    // Confidence badge
    const confRow = document.createElement("div");
    confRow.className = "parse-field";
    const confLabel = document.createElement("span");
    confLabel.className = "parse-label";
    confLabel.textContent = "Confidence";
    confRow.appendChild(confLabel);
    const badge = document.createElement("span");
    badge.className = "confidence-badge confidence-" + parsed.confidence;
    badge.textContent = parsed.confidence;
    confRow.appendChild(badge);
    details.appendChild(confRow);

    // Service
    details.appendChild(makeParseField("Service", parsed.service));

    // Path
    details.appendChild(makeParseField("Path", parsed.path));

    // Error Type
    details.appendChild(
        makeParseField("Error Type", parsed.error_type
            ? parsed.error_type.replace(/_/g, " ")
            : null)
    );

    // Suggestions
    if (parsed.suggestions && parsed.suggestions.length > 0) {
        const sugBox = document.createElement("div");
        sugBox.className = "parse-suggestions";
        parsed.suggestions.forEach((s) => {
            const p = document.createElement("p");
            p.textContent = s;
            sugBox.appendChild(p);
        });
        details.appendChild(sugBox);
    }

    container.classList.remove("hidden");
}

/**
 * Build a parse field row. Uses textContent for the value (XSS safe).
 */
function makeParseField(label, value) {
    const row = document.createElement("div");
    row.className = "parse-field";

    const lbl = document.createElement("span");
    lbl.className = "parse-label";
    lbl.textContent = label;
    row.appendChild(lbl);

    const val = document.createElement("span");
    val.className = "parse-value" + (value ? "" : " none");
    val.textContent = value || "not detected";
    row.appendChild(val);

    return row;
}

// ─── Service Name Constants ───
// Shared across live preview, stack classification, and category detection.
// Must be declared before any code that references them.

const _ARR_APPS = ["sonarr", "radarr", "lidarr", "readarr", "whisparr", "prowlarr", "bazarr"];
const _DL_CLIENTS = ["qbittorrent", "sabnzbd", "nzbget", "transmission", "deluge", "rtorrent", "jdownloader"];
const _MEDIA_SERVERS = ["plex", "jellyfin", "emby"];

// ─── Live Error Preview ───

/**
 * Client-side pattern detection for the error textarea.
 * Mirrors the backend parser's known service lists and path patterns.
 * This is a "sneak peek" — the real parse happens server-side on submit.
 */
const _ALL_SERVICES = [
    ..._ARR_APPS, "overseerr", "jellyseerr",
    ..._DL_CLIENTS,
    ..._MEDIA_SERVERS,
];

const _ERROR_KEYWORDS = {
    "import failure": /\b(import\s+(failed|failure|error)|failed.*import)\b/i,
    "path not found": /\b(path\s+(does\s+)?not\s+exist|no\s+such\s+file|not\s+found|not\s+accessible)\b/i,
    "permission denied": /\b(permission\s+denied|access\s+denied|EACCES)\b/i,
    "cross-device link": /\b(cross[- ]device\s+link|EXDEV|rename\s+.*across)\b/i,
    "remote path mapping": /\b(remote\s+path\s+mapp?ing)\b/i,
    "disk space": /\b(no\s+space|disk\s+full|ENOSPC)\b/i,
};

function updateLivePreview(text) {
    const preview = document.getElementById("error-live-preview");
    if (!preview) return;

    preview.replaceChildren();
    if (!text) return;

    const lower = text.toLowerCase();
    const pills = [];

    // Detect service
    for (const svc of _ALL_SERVICES) {
        if (lower.includes(svc)) {
            pills.push({ type: "service", icon: "\uD83D\uDD0D", text: svc, tooltip: "Detected service — MapArr will look for this in your stacks" });
            break;
        }
    }

    // Detect path (Unix or Windows style)
    const pathMatch = text.match(/(?:\/[\w.-]+){2,}[\w./-]*/);
    const winPathMatch = text.match(/[A-Z]:\\[\w.\\-]+/);
    const detectedPath = pathMatch ? pathMatch[0] : (winPathMatch ? winPathMatch[0] : null);
    if (detectedPath) {
        // Truncate long paths
        const display = detectedPath.length > 50
            ? detectedPath.slice(0, 47) + "..."
            : detectedPath;
        pills.push({ type: "path", icon: "\uD83D\uDCC1", text: display, tooltip: "Detected container path — MapArr will check if this is reachable via volume mounts" });
    }

    // Detect error type
    for (const [label, regex] of Object.entries(_ERROR_KEYWORDS)) {
        if (regex.test(text)) {
            pills.push({ type: "error", icon: "\u26A0", text: label, tooltip: "Detected error type — helps MapArr prioritize the right fix" });
            break;
        }
    }

    if (pills.length === 0) return;

    pills.forEach((p) => {
        const pill = document.createElement("span");
        pill.className = "preview-pill pill-" + p.type;
        if (p.tooltip) pill.title = p.tooltip;
        const icon = document.createElement("span");
        icon.className = "preview-pill-icon";
        icon.textContent = p.icon;
        pill.appendChild(icon);
        const txt = document.createElement("span");
        txt.textContent = p.text;
        pill.appendChild(txt);
        preview.appendChild(pill);
    });
}

// ─── Example Error Fill ───

function fillExample(type) {
    const examples = {
        import: "Import failed, path does not exist or is not accessible by Sonarr: /data/tv/Show Name/Season 01/Episode.mkv",
        remote: "Download client qBittorrent places downloads in /downloads/tv but this directory is not reachable from Radarr. Remote path mapping may be needed.",
        permission: "Access to the path '/data/media/movies/Movie Name (2024)' is denied. Sonarr does not have permission.",
    };
    const textarea = document.getElementById("error-input");
    if (!textarea || !examples[type]) return;
    textarea.value = examples[type];
    textarea.focus();
    updateLivePreview(textarea.value.trim());
}

// ─── Stack Selection ───

async function showStackSelection() {
    const section = document.getElementById("step-stacks");
    const loading = document.getElementById("stacks-loading");
    const list = document.getElementById("stacks-list");
    const empty = document.getElementById("stacks-empty");
    const note = document.getElementById("stacks-search-note");

    section.classList.remove("hidden");
    loading.classList.remove("hidden");
    list.classList.add("hidden");
    empty.classList.add("hidden");

    try {
        const resp = await fetch("/api/discover-stacks");
        // Bail out if user navigated away during fetch
        if (state.mode !== "browse") { loading.classList.add("hidden"); return; }
        if (!resp.ok) {
            throw new Error("Discovery failed");
        }

        const data = await resp.json();
        state.stacks = data.stacks || [];

        loading.classList.add("hidden");

        // Double-check mode — user may have switched during await
        if (state.mode !== "browse") return;

        if (state.stacks.length === 0) {
            empty.classList.remove("hidden");
        } else {
            renderStacks(state.stacks);
            list.classList.remove("hidden");
            // Show filter for large stack lists
            showStackFilter(state.stacks.length);
        }

        if (data.search_note) {
            note.textContent = data.search_note;
        }

        // Update connection status with scan path and stack count
        updateConnectionStatus(data);

        // Populate detected directories for quick-select
        populateDetectedDirs(state.stacks);
    } catch (err) {
        console.error("showStackSelection error:", err);
        if (state.mode !== "browse") return;
        loading.classList.add("hidden");
        empty.classList.remove("hidden");
        const emptyP = empty.querySelector("p");
        if (emptyP) {
            emptyP.textContent = "Could not scan for stacks. Is the backend running?";
        }
    }
}

// ─── Stack Filter ───

function showStackFilter(stackCount) {
    const filter = document.getElementById("stack-filter");
    const input = document.getElementById("stack-filter-input");
    if (!filter || !input) return;

    // Only show filter when there are enough stacks to warrant it
    if (stackCount >= 6) {
        filter.classList.remove("hidden");
        // Attach listener once
        if (!input._filterBound) {
            input.addEventListener("input", () => filterStacks(input.value));
            input._filterBound = true;
        }
    } else {
        filter.classList.add("hidden");
    }
}

function filterStacks(query) {
    const q = query.trim().toLowerCase();
    const list = document.getElementById("stacks-list");
    if (!list) return;

    if (!q) {
        // Show all — re-render full list
        renderStacks(state.stacks);
        return;
    }

    // Filter stacks by name or service match
    const filtered = state.stacks.filter((stack) => {
        const name = extractDirName(stack.path).toLowerCase();
        if (name.includes(q)) return true;
        return (stack.services || []).some((svc) => svc.toLowerCase().includes(q));
    });

    renderStacks(filtered);
}

// ─── Render Stacks ───

function classifyStack(stack) {
    const names = (stack.services || []).map((s) => s.toLowerCase());
    if (names.some((n) => _ARR_APPS.some((a) => n.includes(a)))) return "arr";
    if (names.some((n) => _DL_CLIENTS.some((d) => n.includes(d)))) return "download";
    if (names.some((n) => _MEDIA_SERVERS.some((m) => n.includes(m)))) return "media";
    return "other";
}

function renderStacks(stacks) {
    const list = document.getElementById("stacks-list");
    list.replaceChildren();

    const detectedService = state.parsedError?.service?.toLowerCase() || "";

    // Group stacks by role
    const groups = { arr: [], download: [], media: [], other: [] };
    stacks.forEach((stack) => {
        const role = classifyStack(stack);
        groups[role].push(stack);
    });

    // Sort within each group alphabetically (case-insensitive)
    Object.values(groups).forEach((g) =>
        g.sort((a, b) =>
            extractDirName(a.path).toLowerCase().localeCompare(
                extractDirName(b.path).toLowerCase()
            )
        )
    );

    // Total count + health legend
    const total = document.createElement("div");
    total.className = "stacks-total";
    total.textContent = stacks.length + " stack" + (stacks.length !== 1 ? "s" : "") + " detected";
    list.appendChild(total);

    // Count health statuses for legend — show all categories as a full summary
    const healthCounts = { ok: 0, warning: 0, problem: 0, unknown: 0 };
    stacks.forEach((s) => {
        const h = s.health || "unknown";
        if (h in healthCounts) healthCounts[h]++;
        else healthCounts.unknown++;
    });

    const legend = document.createElement("div");
    legend.className = "health-legend";
    legend.appendChild(_legendDot("ok", healthCounts.ok + " healthy"));
    legend.appendChild(_legendDot("warning", healthCounts.warning + " need review"));
    legend.appendChild(_legendDot("problem", healthCounts.problem + " with issues"));
    legend.appendChild(_legendDot("unknown", healthCounts.unknown + " not applicable"));
    list.appendChild(legend);

    // Render each group
    const groupMeta = [
        { key: "arr", label: "*arr Apps", items: groups.arr },
        { key: "download", label: "Download Clients", items: groups.download },
        { key: "media", label: "Media Servers", items: groups.media },
        { key: "other", label: "Infrastructure & Other", items: groups.other },
    ];

    groupMeta.forEach(({ label, items }) => {
        if (items.length === 0) return;

        const header = document.createElement("div");
        header.className = "stack-group-header";
        const headerText = document.createElement("span");
        headerText.textContent = label;
        header.appendChild(headerText);
        const headerCount = document.createElement("span");
        headerCount.className = "stack-group-count";
        headerCount.textContent = "(" + items.length + ")";
        header.appendChild(headerCount);
        list.appendChild(header);

        items.forEach((stack) => {
            list.appendChild(renderStackItem(stack, detectedService));
        });
    });
}

function renderStackItem(stack, detectedService) {
    const item = document.createElement("div");
    item.className = "stack-item";
    item.addEventListener("click", (e) => selectStack(stack, e));

    // Health indicator (traffic light) — always show for consistent alignment
    const dot = document.createElement("span");
    dot.className = "health-dot health-" + (stack.health || "unknown");
    dot.title = _healthTooltip(stack.health || "unknown", stack.health_hint);
    item.appendChild(dot);

    // Left: info
    const info = document.createElement("div");
    info.className = "stack-info";

    const name = document.createElement("div");
    name.className = "stack-name";
    const dirName = extractDirName(stack.path);
    name.textContent = dirName;
    info.appendChild(name);

    const path = document.createElement("div");
    path.className = "stack-path";
    path.textContent = stack.path;
    info.appendChild(path);

    // Service tags
    if (stack.services && stack.services.length > 0) {
        const tags = document.createElement("div");
        tags.className = "stack-services";
        stack.services.forEach((svc) => {
            const tag = document.createElement("span");
            tag.className = "service-tag";
            if (detectedService && svc.toLowerCase().includes(detectedService)) {
                tag.className += " highlight";
            }
            tag.textContent = svc;
            tags.appendChild(tag);
        });
        info.appendChild(tags);
    }

    if (stack.health_hint && stack.health === "problem") {
        const hint = document.createElement("div");
        hint.className = "stack-health-hint problem";
        hint.textContent = stack.health_hint;
        info.appendChild(hint);
    }

    if (stack.error) {
        const err = document.createElement("div");
        err.className = "stack-error";
        err.textContent = stack.error;
        info.appendChild(err);
    }

    item.appendChild(info);

    // Right: meta
    const meta = document.createElement("div");
    meta.className = "stack-meta";

    const count = document.createElement("span");
    count.className = "stack-count";
    count.textContent = stack.service_count + " service" + (stack.service_count !== 1 ? "s" : "");
    meta.appendChild(count);

    if (stack.health && stack.health !== "unknown") {
        const healthLabel = document.createElement("span");
        healthLabel.className = "stack-health-label health-label-" + stack.health;
        healthLabel.textContent = stack.health === "ok" ? "healthy" :
            stack.health === "warning" ? "check" : "issues found";
        meta.appendChild(healthLabel);
    }

    item.appendChild(meta);
    return item;
}

// ─── Select Stack → Analyze ───

async function selectStack(stack, clickEvent) {
    // Clear previous results before new analysis
    clearAnalysisResults();

    // Visual selection
    document.querySelectorAll(".stack-item").forEach((el) =>
        el.classList.remove("selected")
    );
    const target = clickEvent && clickEvent.currentTarget;
    if (target) {
        target.classList.add("selected");
    }

    state.selectedStack = stack.path;

    // In browse mode, collapse the stack list to show selected summary
    if (state.mode === "browse") {
        const stackSection = document.getElementById("step-stacks");
        const stackList = document.getElementById("stacks-list");
        if (stackList) {
            stackList.classList.add("hidden");
            let selectedSummary = document.getElementById("selected-stack-summary");
            if (!selectedSummary) {
                selectedSummary = document.createElement("div");
                selectedSummary.id = "selected-stack-summary";
                selectedSummary.style.cssText = "display: flex; align-items: center; justify-content: space-between; padding: 0.5rem 0;";
                stackSection.appendChild(selectedSummary);
            }
            selectedSummary.replaceChildren();

            const summaryLeft = document.createElement("div");
            summaryLeft.style.cssText = "display: flex; align-items: center; gap: 0.5rem;";
            const dot = document.createElement("span");
            dot.className = "health-dot health-" + (stack.health || "unknown");
            summaryLeft.appendChild(dot);
            const nameSpan = document.createElement("span");
            nameSpan.style.cssText = "font-weight: 600; font-size: 0.9rem;";
            nameSpan.textContent = extractDirName(stack.path);
            summaryLeft.appendChild(nameSpan);
            const countSpan = document.createElement("span");
            countSpan.style.cssText = "font-size: 0.82rem; color: var(--text-muted);";
            countSpan.textContent = " — " + stack.service_count + " services";
            summaryLeft.appendChild(countSpan);
            selectedSummary.appendChild(summaryLeft);

            const changeBtn = document.createElement("button");
            changeBtn.className = "btn btn-subtle btn-sm";
            const changeIcon = document.createElement("span");
            changeIcon.className = "btn-icon";
            changeIcon.textContent = "\u21C4";
            changeBtn.appendChild(changeIcon);
            const changeText = document.createTextNode(" Change Stack");
            changeBtn.appendChild(changeText);
            changeBtn.addEventListener("click", () => {
                selectedSummary.remove();
                stackList.classList.remove("hidden");
                clearAnalysisResults();
            });
            selectedSummary.appendChild(changeBtn);
        }
    }

    // Show terminal with initial message including compose file
    const termSection = document.getElementById("step-analyzing");
    const termOutput = document.getElementById("terminal-output");
    termOutput.replaceChildren();
    setTerminalDots("running");
    const composeFile = stack.compose_file || "docker-compose.yml";
    const composeFileName = composeFile.split(/[/\\]/).pop();
    const stackDirName = extractDirName(stack.path);

    // Update terminal title to show compose file location
    const termTitle = document.querySelector(".terminal-title");
    if (termTitle) termTitle.textContent = stack.path.replace(/\\/g, "/") + "/" + composeFileName;

    // Update card heading to include stack name
    const cardHeading = termSection.querySelector("h2");
    if (cardHeading) cardHeading.textContent = "Analyzing: " + stackDirName;

    addTerminalLine("run", "Resolving " + stackDirName + "/" + composeFileName + "...");
    termSection.classList.remove("hidden");
    termSection.scrollIntoView({ behavior: "smooth", block: "nearest" });

    try {
        const resp = await fetch("/api/analyze", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                stack_path: stack.path,
                error: state.parsedError,
            }),
        });

        if (!resp.ok) {
            const err = await resp.json().catch(() => ({ error: "Analysis failed" }));
            addTerminalLine("fail", err.error || "Analysis request failed");
            setTerminalDots("error");
            showAnalysisError(err.error || "Analysis request failed");
            return;
        }

        const data = await resp.json();
        state.analysis = data;

        // Render terminal steps with staggered animation
        await renderTerminalSteps(data.steps || []);

        if (data.status === "error") {
            setTerminalDots("error");
            showAnalysisError(data.error, data.stage);
        } else if (data.status === "healthy") {
            setTerminalDots("done");
            showHealthyResult(data);
        } else if (data.status === "incomplete") {
            setTerminalDots("warning");
            showIncompleteResult(data);
        } else {
            // Conflicts found — show warning dots, not green
            const hasCritical = (data.conflicts || []).some((c) => c.severity === "critical");
            setTerminalDots(hasCritical ? "error" : "warning");
            showAnalysisResult(data);
        }
    } catch {
        setTerminalDots("error");
        addTerminalLine("fail", "Could not reach the backend. Is MapArr running?");
        showAnalysisError("Could not reach the backend. Is MapArr running?");
    }
}

// ─── Terminal Rendering ───

function setTerminalDots(state) {
    const red = document.getElementById("dot-red");
    const yellow = document.getElementById("dot-yellow");
    const green = document.getElementById("dot-green");
    // Reset all
    red.className = "terminal-dot";
    yellow.className = "terminal-dot";
    green.className = "terminal-dot";

    if (state === "running") {
        yellow.classList.add("active-yellow");
    } else if (state === "done") {
        green.classList.add("active-green");
    } else if (state === "warning") {
        yellow.classList.add("active-yellow");
        red.classList.add("active-red");
    } else if (state === "error") {
        red.classList.add("active-red");
    }
}

function addTerminalLine(icon, text) {
    const termOutput = document.getElementById("terminal-output");
    const line = document.createElement("div");
    line.className = "terminal-line" + (icon === "done" ? " done-line" : "");
    const iconSpan = document.createElement("span");
    iconSpan.className = "terminal-icon " + icon;
    line.appendChild(iconSpan);
    const textSpan = document.createElement("span");
    textSpan.textContent = text;
    line.appendChild(textSpan);
    termOutput.appendChild(line);
    termOutput.scrollTop = termOutput.scrollHeight;
    return line;
}

async function renderTerminalSteps(steps) {
    const termOutput = document.getElementById("terminal-output");
    // Clear the initial "Resolving..." line
    termOutput.replaceChildren();

    for (let i = 0; i < steps.length; i++) {
        const step = steps[i];
        addTerminalLine(step.icon || "info", step.text);
        // Stagger delay — faster for info lines, slight pause for results
        const delay = step.icon === "info" ? 60 : 120;
        await new Promise((r) => setTimeout(r, delay));
    }
}

// ─── Show Analysis Result (conflicts found) ───

function showAnalysisResult(data) {
    // Problem first — users want the punchline before the evidence table
    showProblem(data);
    showCurrentSetup(data);
    showSolution(data);
    showWhyItWorks(data);
    showNextSteps(data);
    showTrashAdvisory(data);
    showAgainButton();
}

// ─── Current Setup ───

function showCurrentSetup(data) {
    const section = document.getElementById("step-current-setup");
    const details = document.getElementById("current-setup-details");
    details.replaceChildren();

    const table = document.createElement("table");
    table.className = "service-volume-table";

    // Header
    const thead = document.createElement("thead");
    const headerRow = document.createElement("tr");
    ["Service", "Role", "Volume Mapping"].forEach((text) => {
        const th = document.createElement("th");
        th.textContent = text;
        headerRow.appendChild(th);
    });
    thead.appendChild(headerRow);
    table.appendChild(thead);

    // Body — show ALL volumes, dim config mounts for transparency
    const tbody = document.createElement("tbody");
    (data.services || []).forEach((svc) => {
        const allVols = svc.volumes || [];

        if (allVols.length === 0 && svc.role === "other") return;

        const row = document.createElement("tr");

        const nameCell = document.createElement("td");
        nameCell.className = "svc-name";
        nameCell.textContent = svc.name;
        row.appendChild(nameCell);

        const roleCell = document.createElement("td");
        roleCell.className = "svc-role";
        roleCell.textContent = formatRole(svc.role);
        row.appendChild(roleCell);

        const volCell = document.createElement("td");
        volCell.className = "vol-path";
        if (allVols.length > 0) {
            allVols.forEach((v, i) => {
                if (i > 0) volCell.appendChild(document.createElement("br"));
                const span = document.createElement("span");
                span.textContent = v.source + " : " + v.target;
                span.className = isConfigVolume(v.target) ? "vol-config" : "vol-data";
                volCell.appendChild(span);
            });
        } else {
            volCell.textContent = "(no volumes)";
        }
        row.appendChild(volCell);

        tbody.appendChild(row);
    });
    table.appendChild(tbody);
    details.appendChild(table);

    section.classList.remove("hidden");
    section.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

// ─── Problem ───

function showProblem(data) {
    const section = document.getElementById("step-problem");
    const details = document.getElementById("problem-details");
    details.replaceChildren();

    if (data.fix_summary) {
        const summary = document.createElement("p");
        summary.textContent = data.fix_summary;
        details.appendChild(summary);
    }

    (data.conflicts || []).forEach((conflict) => {
        const item = document.createElement("div");
        item.className = "conflict-item conflict-" + conflict.severity;

        const badge = document.createElement("span");
        badge.className = "conflict-severity severity-" + conflict.severity;
        badge.textContent = conflict.severity;
        item.appendChild(badge);

        const desc = document.createElement("div");
        desc.className = "conflict-description";
        desc.textContent = conflict.description;
        item.appendChild(desc);

        if (conflict.detail) {
            const detail = document.createElement("div");
            detail.className = "conflict-detail";
            detail.textContent = conflict.detail;
            item.appendChild(detail);
        }

        details.appendChild(item);
    });

    // Mount warnings rendered inline in the Problem card
    renderMountWarningsInto(details, data);

    section.classList.remove("hidden");
}

// ─── Mount Warnings (inline — rendered into the calling card's container) ───

/**
 * Render mount warnings inline into a given container element.
 * No longer a standalone card — merged into Problem or Healthy card.
 */
function renderMountWarningsInto(container, data) {
    const warnings = data.mount_warnings || [];
    if (warnings.length === 0) return;

    warnings.forEach((text) => {
        const item = document.createElement("div");
        item.className = "callout callout-warning";
        item.style.marginTop = "0.75rem";
        item.textContent = text;
        container.appendChild(item);
    });
}

// ─── Solution ───

function showSolution(data) {
    const section = document.getElementById("step-solution");
    const summaryEl = document.getElementById("solution-summary");
    const yamlEl = document.getElementById("solution-yaml");
    const originalYamlEl = document.getElementById("solution-yaml-original");
    const originalTab = document.querySelector('.solution-tab[data-tab="original"]');
    const originalBlock = document.getElementById("solution-block-original");
    const recommendedBlock = document.getElementById("solution-block-recommended");

    summaryEl.textContent =
        "Replace the services section in your docker-compose.yml with the corrected configuration below. " +
        "This includes all services with corrected volume mounts.";

    if (data.solution_yaml) {
        renderYamlWithHighlights(yamlEl, data.solution_yaml, data.solution_changed_lines || []);
    } else {
        // Fallback: show the fix text from the first conflict
        const firstFix = data.conflicts?.find((c) => c.fix);
        yamlEl.textContent = firstFix?.fix || "No specific YAML changes generated.";
    }

    // Populate "Your Config (Corrected)" tab if backend generated it
    if (data.original_corrected_yaml && originalTab && originalYamlEl) {
        renderYamlWithHighlights(originalYamlEl, data.original_corrected_yaml, data.original_changed_lines || []);
        originalTab.classList.remove("hidden");
    } else if (originalTab) {
        originalTab.classList.add("hidden");
    }

    // Ensure recommended tab is active by default
    switchSolutionTab("recommended");

    section.classList.remove("hidden");
}

// ─── Why It Works ───

function showWhyItWorks(data) {
    const section = document.getElementById("step-why");
    const details = document.getElementById("why-details");
    details.replaceChildren();

    const hasNoSharedMount = (data.conflicts || []).some(
        (c) => c.type === "no_shared_mount"
    );
    const hasUnreachable = (data.conflicts || []).some(
        (c) => c.type === "path_unreachable"
    );

    const points = [];

    if (hasNoSharedMount) {
        points.push(
            "Docker bind mounts create isolated filesystem views. " +
            "When two services mount different host directories, they can't " +
            "hardlink or atomically move files between them — Docker treats " +
            "each bind mount as a separate filesystem."
        );
        points.push(
            "By mounting one shared host directory (like /host/data:/data) " +
            "into all services, they all see the same files on the same " +
            "filesystem. Hardlinks work. Atomic moves work. Imports succeed."
        );
    }

    if (hasUnreachable) {
        points.push(
            "Your service tried to access a path inside its container that " +
            "isn't backed by any volume mount. The path simply doesn't exist " +
            "from the container's perspective."
        );
        points.push(
            "Adding the right volume mount makes the path accessible. " +
            "Make sure the container path matches what you configured in the app."
        );
    }

    if (points.length === 0) {
        points.push(
            "The recommended changes ensure all your media services share " +
            "a consistent view of the filesystem, enabling hardlinks and " +
            "atomic moves between download clients and *arr apps."
        );
    }

    // Toggle button — collapsed by default
    const toggle = document.createElement("button");
    toggle.className = "why-toggle";
    toggle.id = "why-toggle";
    const arrow = document.createElement("span");
    arrow.className = "why-toggle-arrow";
    arrow.textContent = "\u25B8";
    toggle.appendChild(arrow);
    const toggleText = document.createTextNode(" Learn why");
    toggle.appendChild(toggleText);
    toggle.addEventListener("click", () => toggleWhyCard());
    details.appendChild(toggle);

    // Collapsible content wrapper
    const content = document.createElement("div");
    content.className = "why-card-content";
    content.id = "why-card-content";

    points.forEach((text) => {
        const p = document.createElement("p");
        p.textContent = text;
        p.style.cssText = "margin-bottom: 0.75rem; font-size: 0.88rem; color: var(--text-secondary);";
        content.appendChild(p);
    });

    details.appendChild(content);
    section.classList.remove("hidden");
}

function toggleWhyCard() {
    const content = document.getElementById("why-card-content");
    const toggle = document.getElementById("why-toggle");
    if (!content || !toggle) return;

    const isExpanded = content.classList.toggle("expanded");
    toggle.classList.toggle("expanded", isExpanded);

    // Update button text
    const arrow = toggle.querySelector(".why-toggle-arrow");
    // Clear text nodes (keep arrow span)
    Array.from(toggle.childNodes).forEach((n) => {
        if (n.nodeType === Node.TEXT_NODE) toggle.removeChild(n);
    });
    toggle.appendChild(document.createTextNode(isExpanded ? " Hide" : " Learn why"));
}

// ─── Next Steps ───

function showNextSteps(data) {
    const section = document.getElementById("step-next");
    const container = document.getElementById("next-steps-checklist");
    container.replaceChildren();

    // Category advisory inline at the top of Next Steps
    renderCategoryAdvisoryInto(container, data);

    const steps = [
        "Copy the corrected YAML above into your docker-compose.yml",
        "Create the host directory structure if it doesn't exist",
        "Restart your stack with one of the commands below",
        "Check your *arr app — the error should be gone",
    ];

    const ol = document.createElement("ol");
    ol.style.cssText = "margin: 0; padding-left: 1.5rem; font-size: 0.88rem; color: var(--text-secondary); display: flex; flex-direction: column; gap: 0.4rem;";
    steps.forEach((text) => {
        const li = document.createElement("li");
        li.textContent = text;
        ol.appendChild(li);
    });
    container.appendChild(ol);

    // Docker restart command block with variants
    const cmdBlock = document.createElement("div");
    cmdBlock.className = "code-block code-block-small";
    cmdBlock.style.marginTop = "0.75rem";
    const cmdPre = document.createElement("pre");
    cmdPre.textContent =
        "# Docker Compose v2 (recommended)\n" +
        "docker compose down && docker compose up -d\n\n" +
        "# Docker Compose v1 (legacy)\n" +
        "docker-compose down && docker-compose up -d\n\n" +
        "# Portainer / Komodo / Dockge users:\n" +
        "# Use the \"Recreate\" or \"Redeploy\" button in your dashboard";
    cmdBlock.appendChild(cmdPre);
    container.appendChild(cmdBlock);

    const retryNote = document.createElement("p");
    retryNote.style.cssText = "margin-top: 0.75rem; font-size: 0.82rem; color: var(--text-muted);";
    retryNote.textContent = "If the error persists after restarting, paste your new error message and analyze again.";
    container.appendChild(retryNote);

    section.classList.remove("hidden");
}

// ─── Category Path Advisory (inline — rendered into Next Steps) ───

/**
 * Render category advisory inline into a container.
 * Returns true if content was rendered, false if not applicable.
 */
function renderCategoryAdvisoryInto(container, data) {
    const services = data.services || [];
    const hasArr = services.some((s) => s.role === "arr");
    const hasDl = services.some((s) => s.role === "download_client");

    if (!hasArr || !hasDl) return false;

    const callout = document.createElement("div");
    callout.className = "callout callout-category";
    callout.style.marginBottom = "1rem";

    const title = document.createElement("strong");
    title.style.cssText = "display: block; margin-bottom: 0.4rem; font-size: 0.9rem;";
    title.textContent = "Also check: download client category save paths";
    callout.appendChild(title);

    const arrNames = services.filter((s) => s.role === "arr").map((s) => s.name);
    const dlNames = services.filter((s) => s.role === "download_client").map((s) => s.name);

    const dlName = dlNames[0] || "your download client";
    const arrName = arrNames[0] || "your *arr app";
    const isQbit = dlName.toLowerCase().includes("qbit") || dlName.toLowerCase().includes("torrent");
    const isSab = dlName.toLowerCase().includes("sab") || dlName.toLowerCase().includes("nzb");

    const example = document.createElement("p");
    example.style.cssText = "margin: 0.5rem 0; font-size: 0.88rem; color: var(--text-secondary);";

    if (isQbit) {
        example.textContent =
            "In qBittorrent: Options > Downloads — check Default Save Path AND each category's save path. " +
            "These must point inside a volume mount that " + arrName + " can also see.";
    } else if (isSab) {
        example.textContent =
            "In SABnzbd: Config > Folders — check the Completed Download Folder and category output folders. " +
            "These must point inside a volume mount that " + arrName + " can also see.";
    } else {
        example.textContent =
            "In " + dlName + ": check the download save path / category output folders. " +
            "These must point inside a volume mount that " + arrName + " can also see.";
    }
    callout.appendChild(example);

    const why = document.createElement("p");
    why.style.cssText = "margin: 0.5rem 0 0; font-size: 0.82rem; color: var(--text-muted);";
    why.textContent =
        "This is the #1 cause of import failures that survives a correct volume setup. " +
        "If " + dlName + "'s category path isn't under a shared mount, imports fail even with perfect volumes.";
    callout.appendChild(why);

    container.appendChild(callout);
    return true;
}

// ─── TRaSH Advisory (Contextual) ───

/**
 * Detect TRaSH compliance level from volume data.
 * Returns "compliant", "close", or "non-compliant".
 */
function detectTrashCompliance(data) {
    const services = data.services || [];
    const participants = services.filter(
        (s) => s.role === "arr" || s.role === "download_client" || s.role === "media_server"
    );
    if (participants.length === 0) return "non-compliant";

    // Check if data volumes use a unified /data/ root
    let unifiedCount = 0;
    let separateCount = 0;
    let totalDataVols = 0;

    participants.forEach((svc) => {
        (svc.volumes || []).forEach((v) => {
            const target = v.target || "";
            // Skip config mounts
            if (isConfigVolume(target)) return;
            totalDataVols++;
            if (target === "/data" || target.startsWith("/data/")) {
                unifiedCount++;
            } else {
                separateCount++;
            }
        });
    });

    if (totalDataVols === 0) return "non-compliant";
    if (separateCount === 0 && unifiedCount > 0) return "compliant";
    if (unifiedCount > 0 && separateCount > 0) return "close";
    return "non-compliant";
}

function showTrashAdvisory(data) {
    const section = document.getElementById("step-trash");
    const details = document.getElementById("trash-details");
    details.replaceChildren();

    let compliance = data ? detectTrashCompliance(data) : "non-compliant";

    // Don't show "compliant" when there are active conflicts — that's misleading.
    // The folder structure might follow TRaSH conventions, but the stack still has issues.
    const hasConflicts = (data.conflicts || []).length > 0;
    if (compliance === "compliant" && hasConflicts) {
        compliance = "close";
    }

    // ─── Tier 1: Compliant ───
    if (compliance === "compliant") {
        const heading = section.querySelector("h2");
        const stepNum = section.querySelector(".step-number");
        if (heading) heading.textContent = "TRaSH Guides Compliant";
        if (stepNum) {
            stepNum.className = "step-number ok";
            stepNum.textContent = "\u2713";
        }

        const msg = document.createElement("p");
        msg.style.cssText = "color: var(--success); font-weight: 600; margin-bottom: 0.5rem;";
        msg.textContent = "Your setup follows the TRaSH Guides recommendation. Nice work!";
        details.appendChild(msg);

        const note = document.createElement("p");
        note.style.cssText = "font-size: 0.88rem; color: var(--text-secondary);";
        note.textContent =
            "All your media services mount under /data — the unified structure that makes " +
            "hardlinks and atomic moves work reliably. You're running the gold standard.";
        details.appendChild(note);

        section.classList.remove("hidden");
        return;
    }

    // ─── Tier 2: Close ───
    if (compliance === "close") {
        const heading = section.querySelector("h2");
        const stepNum = section.querySelector(".step-number");
        if (heading) heading.textContent = "Almost TRaSH Compliant";
        if (stepNum) {
            stepNum.className = "step-number gold-icon";
            stepNum.textContent = "\u2192";
        }

        const msg = document.createElement("p");
        msg.style.cssText = "color: var(--warning); font-weight: 600; margin-bottom: 0.5rem;";
        msg.textContent = "You're close to the TRaSH Guides structure, but some paths don't use the unified /data root.";
        details.appendChild(msg);

        const note = document.createElement("p");
        note.style.cssText = "font-size: 0.88rem; color: var(--text-secondary); margin-bottom: 0.75rem;";
        note.textContent =
            "Some of your services already use /data mounts, which is great. " +
            "Moving the remaining services to the same /data structure will enable " +
            "hardlinks and atomic moves across your entire stack.";
        details.appendChild(note);
    } else {
        // ─── Tier 3: Non-compliant ───
        const heading = section.querySelector("h2");
        const stepNum = section.querySelector(".step-number");
        if (heading) heading.textContent = "Gold Standard Setup (Optional)";
        if (stepNum) {
            stepNum.className = "step-number gold-icon";
            stepNum.textContent = "\u2605";
        }

        const intro = document.createElement("p");
        intro.style.cssText = "font-size: 0.88rem; color: var(--text-secondary); margin-bottom: 0.75rem;";
        intro.textContent =
            "The solution above fixes your immediate problem. " +
            "For the cleanest long-term setup, consider the TRaSH Guides structure:";
        details.appendChild(intro);
    }

    // Show the structure diagram for close + non-compliant
    const structure = document.createElement("div");
    structure.className = "trash-structure";
    structure.textContent =
        "/data/\n" +
        "  media/\n" +
        "    tv/         Sonarr manages\n" +
        "    movies/     Radarr manages\n" +
        "    music/      Lidarr manages\n" +
        "  torrents/     Download client saves here\n" +
        "  usenet/       Usenet client saves here";
    details.appendChild(structure);

    const structNote = document.createElement("p");
    structNote.style.cssText = "font-size: 0.88rem; color: var(--text-secondary); margin-top: 0.5rem;";
    structNote.textContent =
        "This eliminates path confusion entirely — everything lives under /data " +
        "and all containers see the same structure.";
    details.appendChild(structNote);

    const callout = document.createElement("div");
    callout.className = "callout callout-success";
    const link = document.createElement("a");
    link.href = "https://trash-guides.info/File-and-Folder-Structure/How-to-set-up/Docker/";
    link.target = "_blank";
    link.rel = "noopener";
    link.textContent = "TRaSH Guides: Docker Hardlinks & Atomic Moves";
    callout.appendChild(link);
    const linkNote = document.createElement("p");
    linkNote.textContent =
        "This guide walks through the exact migration steps. " +
        "If you hit issues, come back and paste your new error.";
    linkNote.style.marginTop = "0.5rem";
    callout.appendChild(linkNote);
    details.appendChild(callout);

    section.classList.remove("hidden");
}

// ─── Healthy Result (no conflicts) ───

function showHealthyResult(data) {
    const section = document.getElementById("step-healthy");
    const details = document.getElementById("healthy-details");
    details.replaceChildren();

    // Ensure card heading is green (reset from possible incomplete state)
    const stepNum = section.querySelector(".step-number");
    const heading = section.querySelector("h2");
    if (stepNum) {
        stepNum.className = "step-number ok";
        stepNum.textContent = "\u2713";
    }
    if (heading) heading.textContent = "Your Setup Looks Good";

    // ─── One-line summary ───
    const serviceCount = (data.services || []).length;
    const msg = document.createElement("p");
    msg.className = "healthy-message";
    msg.textContent = "No path conflicts detected across " + serviceCount + " service" + (serviceCount !== 1 ? "s" : "") + ".";
    details.appendChild(msg);

    if (data.fix_summary) {
        const detail = document.createElement("p");
        detail.className = "healthy-detail";
        detail.textContent = data.fix_summary;
        details.appendChild(detail);
    }

    // ─── Mount warnings inline (compact) ───
    renderMountWarningsInto(details, data);

    // ─── TRaSH compliance badge (one line) ───
    const compliance = detectTrashCompliance(data);
    const trashBadge = document.createElement("div");
    trashBadge.className = "healthy-trash-badge " + compliance;
    const trashLabels = {
        compliant: "\u2713 TRaSH Guides compliant — gold standard setup",
        close: "\u2192 Almost TRaSH compliant — some paths don't use /data root",
        "non-compliant": "\u2605 Consider the TRaSH Guides folder structure for best results",
    };
    trashBadge.textContent = trashLabels[compliance];
    details.appendChild(trashBadge);

    // ─── Collapsible setup table ───
    const setupToggle = document.createElement("button");
    setupToggle.className = "why-toggle";
    setupToggle.style.marginTop = "1rem";
    const setupArrow = document.createElement("span");
    setupArrow.className = "why-toggle-arrow";
    setupArrow.textContent = "\u25B8";
    setupToggle.appendChild(setupArrow);
    setupToggle.appendChild(document.createTextNode(" View full setup"));
    details.appendChild(setupToggle);

    const setupContent = document.createElement("div");
    setupContent.className = "healthy-setup-content";

    // Build setup table inline (same logic as showCurrentSetup)
    const table = document.createElement("table");
    table.className = "service-volume-table";
    const thead = document.createElement("thead");
    const headerRow = document.createElement("tr");
    ["Service", "Role", "Volume Mapping"].forEach((text) => {
        const th = document.createElement("th");
        th.textContent = text;
        headerRow.appendChild(th);
    });
    thead.appendChild(headerRow);
    table.appendChild(thead);

    const tbody = document.createElement("tbody");
    (data.services || []).forEach((svc) => {
        const allVols = svc.volumes || [];
        if (allVols.length === 0 && svc.role === "other") return;
        const row = document.createElement("tr");
        const nameCell = document.createElement("td");
        nameCell.className = "svc-name";
        nameCell.textContent = svc.name;
        row.appendChild(nameCell);
        const roleCell = document.createElement("td");
        roleCell.className = "svc-role";
        roleCell.textContent = formatRole(svc.role);
        row.appendChild(roleCell);
        const volCell = document.createElement("td");
        volCell.className = "vol-path";
        if (allVols.length > 0) {
            allVols.forEach((v, i) => {
                if (i > 0) volCell.appendChild(document.createElement("br"));
                const span = document.createElement("span");
                span.textContent = v.source + " : " + v.target;
                span.className = isConfigVolume(v.target) ? "vol-config" : "vol-data";
                volCell.appendChild(span);
            });
        } else {
            volCell.textContent = "(no volumes)";
        }
        row.appendChild(volCell);
        tbody.appendChild(row);
    });
    table.appendChild(tbody);
    setupContent.appendChild(table);

    // Category advisory inline in the setup details
    renderCategoryAdvisoryInto(setupContent, data);

    // Compact troubleshooting hints
    const troubleHint = document.createElement("p");
    troubleHint.style.cssText = "font-size: 0.82rem; color: var(--text-muted); margin-top: 0.5rem;";
    troubleHint.textContent =
        "Still seeing errors? Check your Root Folder settings (Settings > Media Management) " +
        "and ensure all containers share the same PUID/PGID for file permissions.";
    setupContent.appendChild(troubleHint);

    // TRaSH link
    const trashLink = document.createElement("div");
    trashLink.className = "callout";
    trashLink.style.marginTop = "0.5rem";
    const link = document.createElement("a");
    link.href = "https://trash-guides.info/File-and-Folder-Structure/How-to-set-up/Docker/";
    link.target = "_blank";
    link.rel = "noopener";
    link.textContent = "TRaSH Guides: Docker Hardlinks & Atomic Moves";
    trashLink.appendChild(link);
    setupContent.appendChild(trashLink);

    details.appendChild(setupContent);

    // Toggle handler
    setupToggle.addEventListener("click", () => {
        const isExpanded = setupContent.classList.toggle("expanded");
        setupToggle.classList.toggle("expanded", isExpanded);
        Array.from(setupToggle.childNodes).forEach((n) => {
            if (n.nodeType === Node.TEXT_NODE) setupToggle.removeChild(n);
        });
        setupToggle.appendChild(document.createTextNode(isExpanded ? " Hide setup" : " View full setup"));
    });

    // ─── Inline actions (Copy Diagnostic + Analyze Another) ───
    const actions = document.createElement("div");
    actions.className = "healthy-inline-actions";

    const analyzeBtn = document.createElement("button");
    analyzeBtn.className = "btn btn-primary";
    analyzeBtn.textContent = "Analyze Another Stack";
    analyzeBtn.addEventListener("click", () => analyzeAnother());
    actions.appendChild(analyzeBtn);

    const diagBtn = document.createElement("button");
    diagBtn.className = "btn btn-subtle";
    const diagIcon = document.createElement("span");
    diagIcon.className = "btn-icon";
    diagIcon.textContent = "\uD83D\uDCCB";
    diagBtn.appendChild(diagIcon);
    diagBtn.appendChild(document.createTextNode(" Copy Diagnostic"));
    diagBtn.addEventListener("click", () => copyDiagnosticSummary());
    actions.appendChild(diagBtn);

    const startOverBtn = document.createElement("button");
    startOverBtn.className = "btn btn-subtle";
    const soIcon = document.createElement("span");
    soIcon.className = "btn-icon";
    soIcon.textContent = "\u21BA";
    startOverBtn.appendChild(soIcon);
    startOverBtn.appendChild(document.createTextNode(" Start Over"));
    startOverBtn.addEventListener("click", () => startOver());
    actions.appendChild(startOverBtn);

    details.appendChild(actions);

    // Don't show separate setup, trash, or again cards for healthy results
    section.classList.remove("hidden");
    section.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

// ─── Incomplete Stack Result (yellow — missing arr or download client) ───

function showIncompleteResult(data) {
    const section = document.getElementById("step-healthy");
    const details = document.getElementById("healthy-details");
    details.replaceChildren();

    // Override the card heading to reflect yellow/warning state
    const stepNum = section.querySelector(".step-number");
    const heading = section.querySelector("h2");
    if (stepNum) {
        stepNum.className = "step-number gold-icon";
        stepNum.textContent = "!";
    }
    if (heading) heading.textContent = "Incomplete Stack";

    const msg = document.createElement("p");
    msg.className = "healthy-message";
    msg.style.color = "var(--warning)";
    msg.textContent = "Incomplete media stack";
    details.appendChild(msg);

    // Determine what's missing
    const services = data.services || [];
    const hasArr = services.some((s) => s.role === "arr");
    const hasDl = services.some((s) => s.role === "download_client");
    const missing = [];
    if (!hasArr) missing.push("*arr app (Sonarr, Radarr, etc.)");
    if (!hasDl) missing.push("download client (qBittorrent, SABnzbd, etc.)");

    const detail = document.createElement("p");
    detail.className = "healthy-detail";
    detail.textContent =
        "This stack has media services but is missing: " + missing.join(" and ") + ". " +
        "MapArr can't fully analyze hardlink compatibility without both an *arr app and a download client in the same stack.";
    details.appendChild(detail);

    // Show what IS in the stack
    showCurrentSetup(data);
    renderMountWarningsInto(details, data);

    const callout = document.createElement("div");
    callout.className = "callout callout-warning";
    callout.textContent =
        "This isn't necessarily a problem — many setups split services across separate compose stacks. " +
        "If your download client is in a different stack, analyze that stack too and ensure both share " +
        "the same host mount paths.";
    details.appendChild(callout);

    if (data.fix_summary) {
        const summary = document.createElement("p");
        summary.className = "healthy-detail";
        summary.style.marginTop = "0.5rem";
        summary.textContent = data.fix_summary;
        details.appendChild(summary);
    }

    section.classList.remove("hidden");
    showAgainButton();
    section.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

// ─── Analysis Error ───

function showAnalysisError(error, stage) {
    const section = document.getElementById("step-analysis-error");
    const details = document.getElementById("analysis-error-details");
    details.replaceChildren();

    const msg = document.createElement("p");
    msg.textContent = error;
    details.appendChild(msg);

    if (stage === "resolution") {
        const hint = document.createElement("div");
        hint.className = "callout callout-warning";
        hint.textContent =
            "Could not parse the compose file. Check that it's valid YAML " +
            "and contains a 'services' key.";
        details.appendChild(hint);
    }

    section.classList.remove("hidden");
    showAgainButton();
    section.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

// ─── Show "Analyze Another" Button ───

function showAgainButton() {
    const section = document.getElementById("step-again");
    section.classList.remove("hidden");

    // If smart-match auto-selected with alternatives, show "wrong stack?" link
    if (state.mode === "fix" && state.fixAlternatives && state.fixAlternatives.length > 0) {
        let wrongLink = document.getElementById("fix-wrong-stack");
        if (!wrongLink) {
            wrongLink = document.createElement("div");
            wrongLink.id = "fix-wrong-stack";
            wrongLink.className = "fix-wrong-stack";
            section.appendChild(wrongLink);
        }
        wrongLink.replaceChildren();

        const text = document.createElement("span");
        text.textContent = "Not the right stack? ";
        wrongLink.appendChild(text);

        const btn = document.createElement("button");
        btn.className = "btn btn-ghost btn-sm";
        btn.textContent = "Pick a different one";
        btn.addEventListener("click", () => {
            clearAnalysisResults();
            const fixSection = document.getElementById("step-fix-match");
            const heading = document.getElementById("fix-match-heading");
            const desc = document.getElementById("fix-match-desc");
            const list = document.getElementById("fix-match-list");
            heading.textContent = "Which Stack Has the Problem?";
            desc.textContent = "Pick the stack where " + (state.fixDetectedService || "the service") + " is throwing the error:";
            // Include ALL matches (current + alternatives)
            const allMatches = [...state.fixAlternatives];
            // Re-add current if it was removed
            renderFixMatchPills(list, state.stacks.filter((s) =>
                (s.services || []).some((svc) => svc.toLowerCase().includes(state.fixDetectedService || ""))
            ), state.fixDetectedService || "");
            fixSection.classList.remove("hidden");
            fixSection.scrollIntoView({ behavior: "smooth", block: "nearest" });
        });
        wrongLink.appendChild(btn);
    }
}

// ─── Analyze Another (return to stack list) ───

function clearAnalysisResults() {
    // Hide ALL result sections — called before any new analysis cycle
    const resultSections = [
        "step-analyzing", "step-current-setup", "step-problem",
        "step-solution", "step-why",
        "step-next", "step-trash",
        "step-healthy", "step-analysis-error", "step-again",
    ];
    resultSections.forEach((id) => {
        const el = document.getElementById(id);
        if (el) el.classList.add("hidden");
    });

    // Deselect any selected stack
    document.querySelectorAll(".stack-item").forEach((el) =>
        el.classList.remove("selected")
    );

    // Clean up collapsed summary
    const summary = document.getElementById("selected-stack-summary");
    if (summary) summary.remove();
}

function analyzeAnother() {
    clearAnalysisResults();

    if (state.mode === "browse") {
        // Restore full stack list
        const stackList = document.getElementById("stacks-list");
        if (stackList) stackList.classList.remove("hidden");
        const summary = document.getElementById("selected-stack-summary");
        if (summary) summary.remove();
        const stackSection = document.getElementById("step-stacks");
        stackSection.scrollIntoView({ behavior: "smooth", block: "start" });
    } else if (state.mode === "fix") {
        // In fix mode, scroll back to the fix-match section
        const fixMatch = document.getElementById("step-fix-match");
        if (!fixMatch.classList.contains("hidden")) {
            fixMatch.scrollIntoView({ behavior: "smooth", block: "start" });
        } else {
            // Go back to error input
            const errorSection = document.getElementById("step-error");
            errorSection.scrollIntoView({ behavior: "smooth", block: "start" });
        }
    }
}

// ─── Visual Diff Highlighting ───

/**
 * Render YAML into a <pre> element with highlighted changed lines.
 * Changed lines get a green left-border via the .diff-changed class.
 *
 * @param {HTMLElement} preEl — the <pre> element to render into
 * @param {string} yamlText — the YAML content
 * @param {number[]} changedLines — 1-indexed line numbers that changed
 */
function renderYamlWithHighlights(preEl, yamlText, changedLines) {
    preEl.replaceChildren();

    if (!changedLines || changedLines.length === 0) {
        // No diff data — fall back to plain text
        preEl.textContent = yamlText;
        return;
    }

    const changedSet = new Set(changedLines);
    const lines = yamlText.split("\n");

    lines.forEach((line, idx) => {
        const lineNum = idx + 1; // 1-indexed
        const span = document.createElement("span");
        span.className = "yaml-line" + (changedSet.has(lineNum) ? " diff-changed" : "");
        span.textContent = line;
        preEl.appendChild(span);
        // Don't add newline after last line
        if (idx < lines.length - 1) {
            preEl.appendChild(document.createTextNode("\n"));
        }
    });
}

// ─── Solution Tab Switching ───

function switchSolutionTab(tab) {
    const tabs = document.querySelectorAll(".solution-tab");
    tabs.forEach((t) => t.classList.toggle("active", t.dataset.tab === tab));

    const recBlock = document.getElementById("solution-block-recommended");
    const origBlock = document.getElementById("solution-block-original");
    if (tab === "recommended") {
        recBlock.classList.remove("hidden");
        origBlock.classList.add("hidden");
    } else {
        recBlock.classList.add("hidden");
        origBlock.classList.remove("hidden");
    }
}

// ─── Copy Solution YAML ───

function copySolutionYaml() {
    // Copy from whichever tab is currently active
    const activeTab = document.querySelector(".solution-tab.active");
    const isOriginal = activeTab && activeTab.dataset.tab === "original";
    const yamlText = isOriginal
        ? document.getElementById("solution-yaml-original").textContent
        : document.getElementById("solution-yaml").textContent;
    const btn = isOriginal
        ? document.getElementById("btn-copy-original")
        : document.getElementById("btn-copy");

    navigator.clipboard.writeText(yamlText).then(() => {
        btn.textContent = "Copied!";
        btn.classList.add("copied");
        setTimeout(() => {
            btn.textContent = "Copy to Clipboard";
            btn.classList.remove("copied");
        }, 2000);
    }).catch(() => {
        // Fallback for non-HTTPS contexts
        const textarea = document.createElement("textarea");
        textarea.value = yamlText;
        textarea.style.position = "fixed";
        textarea.style.opacity = "0";
        document.body.appendChild(textarea);
        textarea.select();
        try {
            document.execCommand("copy");
            btn.textContent = "Copied!";
            btn.classList.add("copied");
            setTimeout(() => {
                btn.textContent = "Copy to Clipboard";
                btn.classList.remove("copied");
            }, 2000);
        } catch {
            btn.textContent = "Copy failed";
        }
        document.body.removeChild(textarea);
    });
}

// ─── Helpers ───

function showToast(message, type) {
    // type: "success" or "error"
    let toast = document.getElementById("maparr-toast");
    if (!toast) {
        toast = document.createElement("div");
        toast.id = "maparr-toast";
        toast.className = "toast";
        document.body.appendChild(toast);
    }
    toast.textContent = message;
    toast.className = "toast toast-" + (type || "success");
    // Trigger reflow then show
    void toast.offsetWidth;
    toast.classList.add("toast-visible");
    clearTimeout(toast._timer);
    toast._timer = setTimeout(() => {
        toast.classList.remove("toast-visible");
    }, 3000);
}

function extractDirName(path) {
    const parts = path.replace(/\\/g, "/").split("/").filter(Boolean);
    return parts[parts.length - 1] || path;
}

function formatRole(role) {
    const roles = {
        arr: "*arr app",
        download_client: "Download Client",
        media_server: "Media Server",
        request: "Request Manager",
        other: "Other",
    };
    return roles[role] || role;
}

function _legendDot(health, label) {
    const span = document.createElement("span");
    span.className = "legend-item";
    const dot = document.createElement("span");
    dot.className = "health-dot health-" + health;
    span.appendChild(dot);
    const text = document.createElement("span");
    text.textContent = label;
    span.appendChild(text);
    return span;
}

function _healthTooltip(health, hint) {
    const criteria = {
        ok: "GREEN: All media services share a common host mount path. Hardlinks and atomic moves should work.",
        warning: "YELLOW: Only one media service found, or unable to fully determine. Click to run full analysis.",
        problem: "RED: Media services mount different host directories. Hardlinks cannot work across separate bind mounts.",
        unknown: "GREY: No media services detected in this stack. Not applicable for hardlink analysis.",
    };
    const base = criteria[health] || "";
    return hint ? base + "\n\n" + hint : base;
}

function isConfigVolume(target) {
    const configPaths = ["/config", "/app", "/etc", "/var", "/tmp", "/run", "/dev"];
    return configPaths.some(
        (p) => target === p || target.startsWith(p + "/")
    );
}

function updateConnectionStatus(data) {
    const el = document.getElementById("health-status");
    if (!el.classList.contains("connected")) return;

    const count = (data && data.total) || state.stacks.length;
    const scanPath = (data && data.scan_path) || (data && data.path) || "";

    // Track active scan path
    if (scanPath) state.activeScanPath = scanPath.replace(/\\/g, "/");

    el.replaceChildren();

    // Line 1: green dot + "Connected" + count badge
    const line1 = document.createElement("span");
    line1.className = "header-status-line";

    const dot = document.createElement("span");
    dot.className = "header-status-dot online";
    line1.appendChild(dot);

    const text = document.createElement("span");
    text.textContent = "Connected";
    line1.appendChild(text);

    if (count > 0) {
        const badge = document.createElement("span");
        badge.className = "header-count-badge";
        badge.textContent = count + " stacks";
        line1.appendChild(badge);
    }

    el.appendChild(line1);

    // Line 2: clickable scan path
    const displayPath = state.activeScanPath || scanPath;
    if (displayPath) {
        const line2 = document.createElement("span");
        line2.className = "header-scan-path";
        line2.title = "Click to change scan location";
        const pathText = document.createTextNode(displayPath);
        line2.appendChild(pathText);
        const chevron = document.createElement("span");
        chevron.className = "header-path-chevron";
        chevron.textContent = "\u25BE";
        line2.appendChild(chevron);
        line2.style.cursor = "pointer";
        line2.addEventListener("click", (e) => {
            e.stopPropagation();
            toggleHeaderPathDropdown();
        });
        el.appendChild(line2);
    }
}

// ─── Header Path Dropdown ───

function toggleHeaderPathDropdown() {
    let dropdown = document.getElementById("header-path-dropdown");

    // Toggle off if already open
    if (dropdown && !dropdown.classList.contains("hidden")) {
        dropdown.classList.add("hidden");
        return;
    }

    // Create dropdown if it doesn't exist
    if (!dropdown) {
        dropdown = document.createElement("div");
        dropdown.id = "header-path-dropdown";
        dropdown.className = "header-path-dropdown";
        document.querySelector(".header").appendChild(dropdown);

        // Close on outside click
        document.addEventListener("click", (e) => {
            if (!dropdown.contains(e.target) && !e.target.classList.contains("header-scan-path")) {
                dropdown.classList.add("hidden");
            }
        });
    }

    dropdown.replaceChildren();

    const normActive = (state.activeScanPath || "").replace(/\\/g, "/");

    function makeDirButton(dir, count, removable) {
        const wrapper = document.createElement("div");
        wrapper.className = "dropdown-dir-row";

        const btn = document.createElement("button");
        const normDir = dir.replace(/\\/g, "/");
        const isActive = normActive === normDir;
        btn.className = "dropdown-dir-btn" + (isActive ? " dropdown-dir-active" : "");
        btn.innerHTML = '<span class="dropdown-dir-path">' + dir + '</span>' +
            '<span class="dropdown-dir-meta">' +
                (isActive ? '<span class="dropdown-dir-current">current</span>' : '') +
                '<span class="dropdown-dir-count">' + count + ' stacks</span>' +
            '</span>';
        btn.addEventListener("click", () => {
            document.getElementById("custom-path-input").value = dir;
            dropdown.classList.add("hidden");
            state.activeScanPath = normDir;
            // Preload stacks silently — only navigate if already in browse mode
            changeStacksPath();
            if (state.mode === "browse") {
                document.getElementById("step-stacks").classList.remove("hidden");
                setTimeout(() => {
                    document.getElementById("step-stacks").scrollIntoView({ behavior: "smooth", block: "start" });
                }, 100);
            }
        });
        wrapper.appendChild(btn);

        if (removable) {
            const removeBtn = document.createElement("button");
            removeBtn.className = "dropdown-dir-remove";
            removeBtn.title = "Remove from saved locations";
            removeBtn.textContent = "\u00d7";
            removeBtn.addEventListener("click", (e) => {
                e.stopPropagation();
                removeCustomDir(dir);
                wrapper.remove();
            });
            wrapper.appendChild(removeBtn);
        }

        return wrapper;
    }

    // Detected directories — auto-discovered
    const dirs = state.allDetectedDirs;
    if (dirs.length > 0) {
        const label = document.createElement("div");
        label.className = "dropdown-label";
        label.textContent = "Detected locations (" + dirs.length + ")";
        dropdown.appendChild(label);

        dirs.forEach(({ path: dir, count }) => {
            dropdown.appendChild(makeDirButton(dir, count, false));
        });
    }

    // Custom directories — user-added, persisted to localStorage
    const customDirs = state.customDirs.filter((c) => {
        const normC = c.path.replace(/\\/g, "/");
        return !state.allDetectedDirs.some((d) => d.path.replace(/\\/g, "/") === normC);
    });
    if (customDirs.length > 0) {
        const label = document.createElement("div");
        label.className = "dropdown-label";
        label.textContent = "Saved locations (" + customDirs.length + ")";
        dropdown.appendChild(label);

        customDirs.forEach(({ path: dir, count }) => {
            dropdown.appendChild(makeDirButton(dir, count, true));
        });
    }

    // Manual entry
    const manualLabel = document.createElement("div");
    manualLabel.className = "dropdown-label";
    manualLabel.textContent = "Manual entry";
    dropdown.appendChild(manualLabel);

    const manualRow = document.createElement("div");
    manualRow.className = "dropdown-manual-row";

    const input = document.createElement("input");
    input.type = "text";
    input.className = "path-input dropdown-path-input";
    input.placeholder = "/path/to/your/stacks";
    input.spellcheck = false;
    input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
            document.getElementById("custom-path-input").value = input.value;
            dropdown.classList.add("hidden");
            changeStacksPath();
            if (state.mode === "browse") {
                document.getElementById("step-stacks").classList.remove("hidden");
                setTimeout(() => {
                    document.getElementById("step-stacks").scrollIntoView({ behavior: "smooth", block: "start" });
                }, 100);
            }
        }
    });
    manualRow.appendChild(input);

    const scanBtn = document.createElement("button");
    scanBtn.className = "btn btn-primary btn-sm";
    scanBtn.textContent = "Scan";
    scanBtn.addEventListener("click", () => {
        document.getElementById("custom-path-input").value = input.value;
        dropdown.classList.add("hidden");
        changeStacksPath();
        if (state.mode === "browse") {
            document.getElementById("step-stacks").classList.remove("hidden");
            setTimeout(() => {
                document.getElementById("step-stacks").scrollIntoView({ behavior: "smooth", block: "start" });
            }, 100);
        }
    });
    manualRow.appendChild(scanBtn);

    dropdown.appendChild(manualRow);
    dropdown.classList.remove("hidden");

    // Focus manual input
    setTimeout(() => input.focus(), 50);
}

// ─── Detected Directories ───

function populateDetectedDirs(stacks) {
    const container = document.getElementById("detected-dirs");
    const list = document.getElementById("detected-dirs-list");
    if (!container || !list) return;

    // Get distinct parent directories with stack counts
    const dirCounts = {};
    stacks.forEach((s) => {
        const parent = s.path.replace(/\\/g, "/").replace(/\/[^/]+$/, "");
        dirCounts[parent] = (dirCounts[parent] || 0) + 1;
    });

    const dirs = Object.entries(dirCounts).sort((a, b) => b[1] - a[1]);
    if (dirs.length === 0) {
        container.classList.add("hidden");
        return;
    }

    list.replaceChildren();
    dirs.forEach(([dir, count]) => {
        const btn = document.createElement("button");
        btn.className = "btn btn-ghost btn-sm detected-dir-btn";
        btn.textContent = dir + " (" + count + ")";
        btn.title = "Scan only " + dir;
        btn.addEventListener("click", () => {
            document.getElementById("custom-path-input").value = dir;
            changeStacksPath();
        });
        list.appendChild(btn);
    });

    container.classList.remove("hidden");
}

// ─── Path Change ───

function togglePathInput() {
    const row = document.getElementById("path-input-row");
    row.classList.toggle("hidden");
    if (!row.classList.contains("hidden")) {
        document.getElementById("custom-path-input").focus();
    }
}

async function changeStacksPath() {
    const input = document.getElementById("custom-path-input");
    const newPath = input.value.trim();
    if (!newPath) {
        input.focus();
        return;
    }

    const inBrowseMode = state.mode === "browse";
    const loading = document.getElementById("stacks-loading");
    const list = document.getElementById("stacks-list");
    const empty = document.getElementById("stacks-empty");

    if (inBrowseMode) {
        list.classList.add("hidden");
        empty.classList.add("hidden");
        loading.classList.remove("hidden");
    }

    try {
        const resp = await fetch("/api/change-stacks-path", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ path: newPath }),
        });

        const data = await resp.json();
        if (inBrowseMode) loading.classList.add("hidden");

        if (!resp.ok || data.error) {
            if (inBrowseMode) {
                empty.classList.remove("hidden");
                const emptyP = empty.querySelector("p");
                if (emptyP) emptyP.textContent = data.error || "Failed to scan path.";
            } else {
                showToast(data.error || "Failed to scan path.", "error");
            }
            return;
        }

        state.stacks = data.stacks || [];
        const note = document.getElementById("stacks-search-note");
        if (data.search_note) note.textContent = data.search_note;

        if (inBrowseMode) {
            if (state.stacks.length === 0) {
                empty.classList.remove("hidden");
                const emptyP = empty.querySelector("p");
                if (emptyP) emptyP.textContent = "No compose stacks found in " + newPath;
            } else {
                renderStacks(state.stacks);
                list.classList.remove("hidden");
            }
        } else {
            // Not in browse mode — show toast confirmation
            if (state.stacks.length > 0) {
                showToast(state.stacks.length + " stacks loaded from " + extractDirName(newPath), "success");
            } else {
                showToast("No compose stacks found in " + extractDirName(newPath), "error");
            }
        }

        // Persist manual entry if it found stacks and isn't already a detected dir
        if (state.stacks.length > 0) {
            const normNew = newPath.replace(/\\/g, "/");
            const isDetected = state.allDetectedDirs.some(
                (d) => d.path.replace(/\\/g, "/") === normNew
            );
            if (!isDetected) {
                addCustomDir(newPath, state.stacks.length);
            }
        }

        updateConnectionStatus(data);
    } catch {
        if (inBrowseMode) {
            loading.classList.add("hidden");
            empty.classList.remove("hidden");
            const emptyP = empty.querySelector("p");
            if (emptyP) emptyP.textContent = "Could not reach the backend.";
        }
    }
}

async function resetStacksPath() {
    document.getElementById("custom-path-input").value = "";

    try {
        await fetch("/api/change-stacks-path", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ path: "" }),
        });
    } catch {
        // Ignore — we'll re-scan with defaults
    }

    // Re-run default discovery only if still in browse mode
    if (state.mode === "browse") {
        showStackSelection();
    }
    document.getElementById("path-input-row").classList.add("hidden");
}

// ─── Diagnostic Export ───

/**
 * Generate a clean markdown summary of the current analysis result.
 * Works for both conflict and healthy results.
 */
function generateDiagnosticMarkdown() {
    const data = state.analysis;
    if (!data) return null;

    const stackName = data.stack_name || extractDirName(data.stack_path || "unknown");
    const serviceCount = data.service_count || (data.services || []).length;
    const conflicts = data.conflicts || [];
    const isHealthy = data.status === "healthy";
    const isIncomplete = data.status === "incomplete";

    const lines = [];
    lines.push("## MapArr Diagnostic \u2014 " + stackName);
    lines.push("");

    if (isHealthy) {
        lines.push("**Status:** \u2705 Healthy | **Services:** " + serviceCount);
        lines.push("");
        lines.push("No path mapping issues detected. All volume mounts look correct.");
    } else if (isIncomplete) {
        lines.push("**Status:** \u26A0\uFE0F Incomplete Stack | **Services:** " + serviceCount);
        lines.push("");
        lines.push(data.fix_summary || "Incomplete media stack \u2014 missing key roles for full analysis.");
    } else {
        const criticalCount = conflicts.filter((c) => c.severity === "critical").length;
        const highCount = conflicts.filter((c) => c.severity === "high").length;
        lines.push("**Status:** \u274C Issues Found | **Services:** " + serviceCount +
            " | **Conflicts:** " + conflicts.length +
            (criticalCount ? " (" + criticalCount + " critical)" : ""));
        lines.push("");

        if (data.fix_summary) {
            lines.push(data.fix_summary);
            lines.push("");
        }

        lines.push("### Issues");
        lines.push("");
        conflicts.forEach((c) => {
            const icon = c.severity === "critical" ? "\uD83D\uDED1" : "\u26A0\uFE0F";
            lines.push("- " + icon + " **" + c.severity.toUpperCase() + ":** " + c.description);
        });

        if (data.solution_yaml) {
            lines.push("");
            lines.push("### Recommended Fix");
            lines.push("");
            lines.push("```yaml");
            lines.push(data.solution_yaml);
            lines.push("```");
        }
    }

    // Services table
    const services = data.services || [];
    if (services.length > 0) {
        lines.push("");
        lines.push("### Services");
        lines.push("");
        lines.push("| Service | Role | Volumes |");
        lines.push("|---------|------|---------|");
        services.forEach((svc) => {
            const vols = (svc.volumes || [])
                .map((v) => "`" + v.source + ":" + v.target + "`")
                .join(", ") || "(none)";
            lines.push("| " + svc.name + " | " + formatRole(svc.role) + " | " + vols + " |");
        });
    }

    lines.push("");
    lines.push("---");
    lines.push("*Generated by [MapArr](https://github.com/coaxk/maparr)*");

    return lines.join("\n");
}

/**
 * Copy the diagnostic summary to clipboard. Shows a toast on success.
 */
function copyDiagnosticSummary() {
    const md = generateDiagnosticMarkdown();
    if (!md) {
        showToast("No analysis data to export", "error");
        return;
    }

    navigator.clipboard.writeText(md).then(() => {
        showToast("Diagnostic copied to clipboard", "success");
    }).catch(() => {
        // Fallback for non-HTTPS
        const textarea = document.createElement("textarea");
        textarea.value = md;
        textarea.style.cssText = "position:fixed;opacity:0";
        document.body.appendChild(textarea);
        textarea.select();
        try {
            document.execCommand("copy");
            showToast("Diagnostic copied to clipboard", "success");
        } catch {
            showToast("Copy failed \u2014 try HTTPS", "error");
        }
        document.body.removeChild(textarea);
    });
}

// ─── Footer Version & Update Check ───

/**
 * Set the footer version text from the backend health endpoint.
 */
function updateFooterVersion(version) {
    const el = document.getElementById("footer-version");
    if (el) el.textContent = "MapArr v" + version;
}

/**
 * Check GitHub releases for a newer version. Cached in sessionStorage
 * to avoid repeat API calls on page refresh. Gracefully ignores all errors
 * (404 = no releases, 403 = rate limited, network errors).
 */
async function checkForUpdates(currentVersion) {
    const CACHE_KEY = "maparr_update_check";
    const REPO = "coaxk/maparr";

    // Check sessionStorage cache first
    try {
        const cached = sessionStorage.getItem(CACHE_KEY);
        if (cached) {
            const data = JSON.parse(cached);
            if (data.checked && Date.now() - data.checked < 3600000) {
                // Cache valid for 1 hour
                if (data.latest && isNewerVersion(currentVersion, data.latest)) {
                    showUpdateBadge(data.latest);
                }
                return;
            }
        }
    } catch {}

    try {
        const resp = await fetch("https://api.github.com/repos/" + REPO + "/releases/latest");
        if (!resp.ok) {
            // No releases, rate limited, or repo not public — cache the miss
            try { sessionStorage.setItem(CACHE_KEY, JSON.stringify({ checked: Date.now(), latest: null })); } catch {}
            return;
        }

        const data = await resp.json();
        const latestTag = (data.tag_name || "").replace(/^v/, "");

        // Cache the result
        try { sessionStorage.setItem(CACHE_KEY, JSON.stringify({ checked: Date.now(), latest: latestTag })); } catch {}

        if (latestTag && isNewerVersion(currentVersion, latestTag)) {
            showUpdateBadge(latestTag);
        }
    } catch {
        // Network error — silently ignore
    }
}

/**
 * Compare two semver strings. Returns true if latest > current.
 */
function isNewerVersion(current, latest) {
    const c = current.split(".").map(Number);
    const l = latest.split(".").map(Number);
    for (let i = 0; i < 3; i++) {
        const cv = c[i] || 0;
        const lv = l[i] || 0;
        if (lv > cv) return true;
        if (lv < cv) return false;
    }
    return false;
}

// ─── GitHub Stars Badge ───

/**
 * Fetch the star count from GitHub API. Cached in sessionStorage (1hr TTL).
 * Called on page load from checkHealth(). Gracefully ignores all errors.
 */
async function fetchStarCount() {
    const CACHE_KEY = "maparr_star_count";
    const REPO = "coaxk/maparr";

    // Check cache first
    try {
        const cached = sessionStorage.getItem(CACHE_KEY);
        if (cached) {
            const data = JSON.parse(cached);
            if (data.checked && Date.now() - data.checked < 3600000) {
                if (data.stars !== null) showStarsBadge(data.stars);
                return;
            }
        }
    } catch {}

    try {
        const resp = await fetch("https://api.github.com/repos/" + REPO);
        if (!resp.ok) {
            try { sessionStorage.setItem(CACHE_KEY, JSON.stringify({ checked: Date.now(), stars: null })); } catch {}
            return;
        }
        const data = await resp.json();
        const stars = data.stargazers_count || 0;
        try { sessionStorage.setItem(CACHE_KEY, JSON.stringify({ checked: Date.now(), stars })); } catch {}
        showStarsBadge(stars);
    } catch {
        // Network error — silently ignore
    }
}

/**
 * Render a small star count badge next to the GitHub footer icon.
 */
function showStarsBadge(count) {
    const githubLink = document.querySelector('.footer-icon[data-tooltip="GitHub"]');
    if (!githubLink) return;

    // Don't duplicate
    if (githubLink.parentElement.querySelector(".github-stars")) return;

    const badge = document.createElement("a");
    badge.className = "github-stars";
    badge.href = "https://github.com/coaxk/maparr/stargazers";
    badge.target = "_blank";
    badge.rel = "noopener";
    badge.title = count + " stars on GitHub";

    const star = document.createElement("span");
    star.className = "github-stars-icon";
    star.textContent = "\u2605";
    badge.appendChild(star);

    const num = document.createElement("span");
    num.textContent = count;
    badge.appendChild(num);

    // Insert right after the GitHub icon
    githubLink.insertAdjacentElement("afterend", badge);
}

/**
 * Show the update-available state on the footer version badge.
 */
function showUpdateBadge(latestVersion) {
    const el = document.getElementById("footer-version");
    if (!el) return;
    el.classList.add("update-available");
    el.setAttribute("data-tooltip", "Update available: v" + latestVersion + " \u2014 pull latest image");
    el.addEventListener("click", () => {
        window.open("https://github.com/coaxk/maparr/releases/latest", "_blank");
    });
}
