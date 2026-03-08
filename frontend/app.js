/**
 * MapArr v1.5 — Frontend Application
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
    bootComplete: false, // Has boot sequence finished?
    bootPhase: "idle",   // "idle" | "scanning" | "done" | "failed"
    pipeline: null,      // Cached PipelineResult from /api/pipeline-scan
    verifiedStacks: new Set(), // Stacks analyzed/fixed this session — skip caution override
    preflightOverridden: false, // User bypassed a pre-flight warning for this analysis
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

// ─── Boot Sequence ───

// Preferred scan path — remembered across sessions via localStorage
function getPreferredPath() {
    try { return localStorage.getItem("maparr_default_scan_path") || ""; } catch { return ""; }
}
function setPreferredPath(path) {
    try { if (path) localStorage.setItem("maparr_default_scan_path", path); } catch {}
}

/**
 * Write a terminal line into the boot screen with a staggered delay.
 * Reuses existing .terminal-line / .terminal-icon classes.
 */
function bootAddLine(iconClass, text, delay) {
    return new Promise((resolve) => {
        setTimeout(() => {
            const body = document.getElementById("boot-terminal-body");
            if (!body) { resolve(); return; }
            const line = document.createElement("div");
            line.className = "terminal-line";
            const icon = document.createElement("span");
            icon.className = "terminal-icon " + iconClass;
            const span = document.createElement("span");
            span.textContent = text;
            line.appendChild(icon);
            line.appendChild(span);
            body.appendChild(line);
            body.scrollTop = body.scrollHeight;
            resolve();
        }, delay);
    });
}

/**
 * Run the boot discovery sequence. Called from checkHealth() after
 * health + discovery complete. Animates terminal lines showing what
 * was found, then crossfades to the enriched mode selector.
 */
async function runBootSequence(backendOnline, discData) {
    const bootScreen = document.getElementById("boot-screen");
    if (!bootScreen) return;

    state.bootPhase = "scanning";

    // Safety valve — auto-complete if boot takes too long
    const bootTimeout = setTimeout(() => {
        if (state.bootPhase === "scanning") {
            state.bootPhase = "done";
            transitionBootToFork(state.stacks.length);
        }
    }, 5000);

    if (!backendOnline) {
        await bootAddLine("fail", "Backend unreachable", 0);
        await bootAddLine("info", "Starting in offline mode...", 500);
        state.bootPhase = "failed";
        clearTimeout(bootTimeout);
        setTimeout(() => transitionBootToFork(0), 1000);
        return;
    }

    // Backend online — let each line land before the next
    await bootAddLine("ok", "Backend connected", 0);
    const scanPath = (discData.scan_path || "").replace(/\\/g, "/");
    const displayScanPath = scanPath.length > 40 ? "..." + scanPath.slice(-37) : scanPath;
    await bootAddLine("run", "Scanning " + (displayScanPath || "default locations") + "...", 400);

    if (!discData || !discData.stacks || discData.stacks.length === 0) {
        await bootAddLine("warn", "No compose stacks found in common locations", 600);
        state.bootPhase = "done";
        clearTimeout(bootTimeout);
        setTimeout(() => transitionBootToNoStacks(), 800);
        return;
    }

    // Stacks found — show directory summary lines
    const stacks = discData.stacks;
    const dirs = state.allDetectedDirs;

    let lineDelay = 500;
    for (const dir of dirs.slice(0, 5)) {
        const mediaCount = countMediaServicesInDir(stacks, dir.path);
        let detail = dir.count + " stack" + (dir.count !== 1 ? "s" : "");
        if (mediaCount > 0) {
            detail += " (" + mediaCount + " media)";
        }
        const displayPath = dir.path.length > 45
            ? "..." + dir.path.slice(-42)
            : dir.path;
        await bootAddLine("ok", displayPath + " \u2192 " + detail, lineDelay);
        lineDelay += 150;
    }

    // Summary line
    const totalCount = stacks.length;
    await bootAddLine("ok",
        totalCount + " stack" + (totalCount !== 1 ? "s" : "") + " found",
        lineDelay
    );

    // Pipeline scan — build the unified media service map on boot.
    // This is what makes MapArr "actually smart" — full directory awareness
    // before any per-stack analysis happens.
    lineDelay += 400;
    await bootAddLine("run", "Analyzing media pipeline\u2026", lineDelay);
    try {
        const pipelineResp = await fetch("/api/pipeline-scan", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ scan_dir: discData.scan_path }),
        });
        if (pipelineResp.ok) {
            state.pipeline = await pipelineResp.json();
            const p = state.pipeline;
            const mediaCount = p.media_service_count || 0;
            if (mediaCount > 0) {
                let pipelineText = mediaCount + " media service" + (mediaCount !== 1 ? "s" : "");
                if (p.shared_mount && p.mount_root) {
                    pipelineText += " | shared mount: " + p.mount_root + " | hardlinks OK";
                } else if ((p.conflicts || []).length > 0) {
                    pipelineText += " | " + p.conflicts.length + " mount conflict" + (p.conflicts.length !== 1 ? "s" : "");
                }
                await bootAddLine("ok", pipelineText, 400);
            } else {
                await bootAddLine("info", "No media services detected in pipeline", 400);
            }
        }
    } catch (e) {
        console.warn("Pipeline scan failed during boot:", e);
        // Non-fatal — pipeline is optional enhancement
    }

    // Final "launching" line — signals the page is about to transition
    await bootAddLine("run", "Launching MapArr\u2026", 400);

    if (discData.scan_path) setPreferredPath(discData.scan_path);

    // Pause — let users read the last line before the crossfade
    state.bootPhase = "done";
    clearTimeout(bootTimeout);
    setTimeout(() => transitionBootToFork(totalCount), 1000);
}

/**
 * Count media-related services in stacks under a given directory.
 */
function countMediaServicesInDir(stacks, dirPath) {
    const norm = dirPath.replace(/\\/g, "/");
    let count = 0;
    stacks.forEach((s) => {
        const stackParent = s.path.replace(/\\/g, "/").replace(/\/[^/]+$/, "");
        if (stackParent === norm || stackParent.startsWith(norm + "/")) {
            (s.services || []).forEach((svc) => {
                const lower = svc.toLowerCase();
                if (_ALL_SERVICES.some((known) => lower.includes(known))) count++;
            });
        }
    });
    return count;
}

/**
 * Crossfade from boot screen to the enriched mode selector.
 */
function transitionBootToFork(stackCount) {
    const bootScreen = document.getElementById("boot-screen");
    const modeSelector = document.getElementById("step-mode");
    if (!bootScreen || !modeSelector) return;

    // Don't transition twice
    if (state.bootComplete) return;

    if (stackCount > 0) enrichModeSelector(stackCount);

    bootScreen.classList.add("boot-done");
    bootScreen.addEventListener("animationend", () => {
        bootScreen.classList.add("hidden");
        bootScreen.classList.remove("boot-done");
        modeSelector.classList.remove("hidden");
        modeSelector.classList.add("boot-reveal");
        modeSelector.addEventListener("animationend", () => {
            modeSelector.classList.remove("boot-reveal");
        }, { once: true });
        // Delayed nudge — let users land on the fork page and orient before
        // the header scanner catches their eye in the periphery
        setTimeout(nudgeHeaderScanner, 1200);
    }, { once: true });

    state.bootComplete = true;
}

/**
 * Transition from boot to the zero-stacks path input.
 */
function transitionBootToNoStacks() {
    const bootScreen = document.getElementById("boot-screen");
    const noStacks = document.getElementById("boot-no-stacks");
    if (!bootScreen || !noStacks) return;

    bootScreen.classList.add("boot-done");
    bootScreen.addEventListener("animationend", () => {
        bootScreen.classList.add("hidden");
        bootScreen.classList.remove("boot-done");
        noStacks.classList.remove("hidden");
        noStacks.classList.add("boot-reveal");
        noStacks.addEventListener("animationend", () => {
            noStacks.classList.remove("boot-reveal");
        }, { once: true });
        const input = document.getElementById("boot-path-input");
        if (input) input.focus();
    }, { once: true });

    state.bootComplete = true;
}

/**
 * Enrich mode selector buttons with discovered stack count.
 */
function enrichModeSelector(stackCount) {
    const browseTitle = document.getElementById("mode-browse-title");
    const contextLine = document.getElementById("mode-context");

    if (browseTitle && stackCount > 0) {
        browseTitle.textContent = "Analyze Your Stacks (" + stackCount + " found)";
    }
    if (contextLine) {
        // Pipeline-aware context line — show media service summary if available
        const p = state.pipeline;
        if (p && (p.media_service_count || 0) > 0) {
            let text = p.media_service_count + " media services";
            if (p.shared_mount && p.mount_root) {
                text += " \u2014 all mounts aligned";
            } else if ((p.conflicts || []).length > 0) {
                text += " \u2014 " + p.conflicts.length + " mount conflict" + (p.conflicts.length !== 1 ? "s" : "") + " detected";
            }
            contextLine.textContent = text;
            contextLine.classList.remove("hidden");
        } else {
            const dirs = state.allDetectedDirs;
            if (dirs.length === 1) {
                contextLine.textContent = "Scanning " + dirs[0].path;
            } else if (dirs.length > 1) {
                contextLine.textContent = dirs.length + " source directories, " + stackCount + " stacks total";
            }
            if (dirs.length > 0) contextLine.classList.remove("hidden");
        }
    }
}

/**
 * Subtle one-shot glow on the header scan path area.
 * Draws attention so users register "that's where I change directories."
 * Fires once after boot transition, then never again.
 */
function nudgeHeaderScanner() {
    const header = document.getElementById("health-status");
    if (!header) return;
    header.classList.add("header-nudge");
    header.addEventListener("animationend", () => {
        header.classList.remove("header-nudge");
    }, { once: true });
    // Fallback removal in case animationend doesn't fire (no scan path visible)
    setTimeout(() => header.classList.remove("header-nudge"), 3500);
}

/**
 * Zero-stacks: user typed a custom path and hit Scan.
 */
async function bootScanCustomPath() {
    const input = document.getElementById("boot-path-input");
    if (!input) return;
    const path = input.value.trim();
    if (!path) { input.focus(); return; }

    try {
        await fetch("/api/change-stacks-path", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ path }),
        });
        const resp = await fetch("/api/discover-stacks");
        if (resp.ok) {
            const data = await resp.json();
            state.stacks = data.stacks || [];
            state.activeScanPath = (data.scan_path || "").replace(/\\/g, "/");
            state.verifiedStacks.clear();
            updateConnectionStatus(data);
            if (state.stacks.length > 0) {
                const dirCounts = {};
                state.stacks.forEach((s) => {
                    const parent = s.path.replace(/\\/g, "/").replace(/\/[^/]+$/, "");
                    dirCounts[parent] = (dirCounts[parent] || 0) + 1;
                });
                state.allDetectedDirs = Object.entries(dirCounts)
                    .sort((a, b) => b[1] - a[1])
                    .map(([p, c]) => ({ path: p, count: c }));
                addCustomDir(path, state.stacks.length);
                setPreferredPath(path);
                // Run pipeline scan for the new path
                try {
                    const pResp = await fetch("/api/pipeline-scan", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ scan_dir: path }),
                    });
                    if (pResp.ok) state.pipeline = await pResp.json();
                } catch {}
                document.getElementById("boot-no-stacks").classList.add("hidden");
                state.bootComplete = false; // Allow transition
                transitionBootToFork(state.stacks.length);
            } else {
                input.style.borderColor = "var(--error)";
                setTimeout(() => { input.style.borderColor = ""; }, 2000);
            }
        }
    } catch {
        input.style.borderColor = "var(--error)";
        setTimeout(() => { input.style.borderColor = ""; }, 2000);
    }
}

// ─── Init ───

document.addEventListener("DOMContentLoaded", () => {
    checkHealth();
    initLogSystem(); // Connect SSE immediately so logs flow during boot

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

    // Enter in boot path input triggers scan (zero-stacks fallback)
    const bootPathInput = document.getElementById("boot-path-input");
    if (bootPathInput) {
        bootPathInput.addEventListener("keydown", (e) => {
            if (e.key === "Enter") { e.preventDefault(); bootScanCustomPath(); }
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
    // Clear stale fix-mode intermediate state (pills, parse result) from prior sessions
    document.getElementById("step-parse-result").classList.add("hidden");
    document.getElementById("step-fix-match").classList.add("hidden");
    const fixMatchList = document.getElementById("fix-match-list");
    if (fixMatchList) fixMatchList.replaceChildren();
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
    // Hide boot screens — don't re-run boot on return
    const bootScreen = document.getElementById("boot-screen");
    if (bootScreen) bootScreen.classList.add("hidden");
    const noStacks = document.getElementById("boot-no-stacks");
    if (noStacks) noStacks.classList.add("hidden");
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
    // Show mode selector with current stack counts
    enrichModeSelector(state.stacks.length);
    document.getElementById("step-mode").classList.remove("hidden");
    document.getElementById("step-mode").scrollIntoView({ behavior: "smooth", block: "start" });
}

// ─── Health Check ───

async function checkHealth() {
    const el = document.getElementById("health-status");
    let backendOnline = false;
    let discData = null;

    try {
        const resp = await fetch("/api/health");
        if (resp.ok) {
            backendOnline = true;
            const healthData = await resp.json();
            const runningVersion = healthData.version || "1.3.0";

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
                    discData = await discResp.json();
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

    // Run boot sequence — surfaces discovery results before the mode fork
    runBootSequence(backendOnline, discData);
}

// ─── Parse Error ───

async function parseError() {
    const textarea = document.getElementById("error-input");
    const text = textarea.value.trim();

    if (!text) {
        textarea.focus();
        return;
    }

    // Clear any previous analysis results and stale verified-healthy state.
    // Browse mode may have marked stacks green, but the user is now bringing
    // an error — don't let stale green dots mislead them about which stack
    // has the problem.
    clearAnalysisResults();
    state.verifiedStacks.clear();
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

        const data = await resp.json();

        if (data.multiple_errors && data.multiple_errors.length > 1) {
            // Multiple errors detected — let user pick which to analyze
            showMultiErrorPicker(data.multiple_errors);
        } else {
            state.parsedError = data;
            showParseResult(data);
            await autoMatchStacks(data);
        }
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
                state.activeScanPath = (data.scan_path || "").replace(/\\/g, "/");
            }
        } catch {}
    }

    // Ensure pipeline is available for Fix mode analysis
    if (!state.pipeline && state.stacks.length > 0) {
        const scanPath = state.activeScanPath || getPreferredPath();
        if (scanPath) {
            try {
                const pResp = await fetch("/api/pipeline-scan", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ scan_dir: scanPath }),
                });
                if (pResp.ok) state.pipeline = await pResp.json();
            } catch {}
        }
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

        // Health dot (pipeline-aware)
        const dot = document.createElement("span");
        dot.className = "health-dot health-" + _effectiveHealth(stack);
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

        // Error number
        const num = document.createElement("span");
        num.className = "multi-error-num";
        num.textContent = "#" + (i + 1);
        item.appendChild(num);

        const body = document.createElement("div");
        body.className = "multi-error-body";

        // Top line: service + error type badges
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

        item.addEventListener("click", () => selectError(err));
        list.appendChild(item);
    });

    details.appendChild(list);
    container.classList.remove("hidden");
}

async function selectError(err) {
    // Update textarea to selected error so re-clicking "Analyze" is consistent
    const textarea = document.getElementById("error-input");
    if (textarea && err.raw_input) textarea.value = err.raw_input;

    state.parsedError = err;
    showParseResult(err);
    await autoMatchStacks(err);
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
    "hardlink failure": /\b(hardlink|hard\s*link|atomic\s+move)\b/i,
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
        import: "Import failed, path does not exist or is not accessible by Sonarr: /data/tv/Show Name/Season 01/Episode.mkv. Ensure the path exists and the user running Sonarr has the correct permissions to access this file.",
        remote: "Download client qBittorrent places downloads in /downloads/tv but this directory does not appear to exist inside the container. You may need a Remote Path Mapping in Radarr (Settings > Download Clients > Remote Path Mappings).",
        hardlink: "Invalid cross-device link: rename '/downloads/complete/Movie.Name.2024.mkv' -> '/data/media/movies/Movie Name (2024)/Movie.Name.2024.mkv'. Sonarr cannot create hardlinks across different mount points.",
        permission: "Access to the path '/data/media/movies/Movie Name (2024)' is denied. Radarr does not have permission to write to this directory. Check PUID/PGID match between containers.",
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

// ─── Stack Filter (Smart) ───
// Mirrors the quick-switch UX: type-to-search with health dots, service
// names, and click-to-analyze. Also filters the grid below for visual
// context so users can scan everything at once.

function showStackFilter(stackCount) {
    const filter = document.getElementById("stack-filter");
    const input = document.getElementById("stack-filter-input");
    if (!filter || !input) return;

    // Only show filter when there are enough stacks to warrant it
    if (stackCount >= 6) {
        filter.classList.remove("hidden");
        input.placeholder = "Search or click to browse stacks...";
        // Attach smart filter listeners once
        if (!input._filterBound) {
            const results = document.getElementById("stack-filter-results");

            wireQuickSwitchCombobox(input, results, {
                limit: 8,
                onSelect: (matchStack) => {
                    input.value = "";
                    results.classList.add("hidden");
                    selectStack(matchStack, null);
                },
            });

            // Also filter the grid below as user types
            let gridTimer = null;
            input.addEventListener("input", () => {
                clearTimeout(gridTimer);
                gridTimer = setTimeout(() => {
                    const q = input.value.trim().toLowerCase();
                    if (!q) {
                        renderStacks(state.stacks);
                    } else {
                        const filtered = state.stacks.filter((s) => {
                            const name = extractDirName(s.path).toLowerCase();
                            if (name.includes(q)) return true;
                            return (s.services || []).some((svc) => svc.toLowerCase().includes(q));
                        });
                        renderStacks(filtered);
                    }
                }, 150);
            });

            input._filterBound = true;
        }
    } else {
        filter.classList.add("hidden");
    }
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

    // Pipeline banner — shows media pipeline health above the stack list.
    // This is the first thing users see in Browse mode: their full pipeline
    // at a glance, before drilling into individual stacks.
    const p = state.pipeline;
    if (p && (p.media_service_count || 0) > 0) {
        const banner = document.createElement("div");
        banner.className = "pipeline-banner";

        const healthIcon = p.health === "ok" ? "\u2713"
            : p.health === "warning" ? "\u26A0"
            : p.health === "problem" ? "\u2717" : "\u2022";
        const healthClass = "pipeline-health-" + (p.health || "unknown");

        // Header row: health + service count
        const header = document.createElement("div");
        header.className = "pipeline-header " + healthClass;
        const headerIcon = document.createElement("span");
        headerIcon.className = "pipeline-health-icon";
        headerIcon.textContent = healthIcon;
        header.appendChild(headerIcon);
        const headerText = document.createElement("span");
        const healthLabel = p.health === "ok" ? "Healthy" : p.health === "warning" ? "Warning" : p.health === "problem" ? "Issue Detected" : "Unknown";
        headerText.textContent = "Pipeline: " + healthLabel + " \u2014 " + p.media_service_count + " media services across " + (Object.keys(p.services_by_role || {}).reduce((sum, role) => {
            const svcs = (p.services_by_role[role] || []);
            const stacks = new Set(svcs.map(s => s.stack_name));
            return sum + stacks.size;
        }, 0)) + " stacks";
        header.appendChild(headerText);
        // Explain what the pipeline health means
        const explain = document.createElement("p");
        explain.className = "pipeline-explain";
        if (p.health === "ok") {
            explain.textContent = "All your media services share a common mount root — hardlinks and atomic moves will work correctly across services.";
        } else if (p.health === "warning") {
            explain.textContent = "Some mount configurations may cause issues. Select a stack below to see specific recommendations.";
        } else if (p.health === "problem") {
            explain.textContent = "Your services use different mount roots, which prevents hardlinks and atomic moves. Select a stack below for a detailed diagnosis and fix.";
        } else {
            explain.textContent = "Select a stack below to run a detailed analysis of its volume mount configuration.";
        }
        banner.appendChild(explain);
        banner.appendChild(header);

        // Role breakdown
        const roles = document.createElement("div");
        roles.className = "pipeline-roles";
        const byRole = p.services_by_role || {};
        ["arr", "download_client", "media_server"].forEach((role) => {
            const svcs = byRole[role] || [];
            if (svcs.length === 0) return;
            const roleLabel = role === "arr" ? "arr" : role === "download_client" ? "download" : "media";
            const names = svcs.map(s => s.service_name).join(", ");
            const tag = document.createElement("span");
            tag.className = "pipeline-role-tag pipeline-role-" + roleLabel;
            tag.textContent = names + " (" + roleLabel + ")";
            roles.appendChild(tag);
        });
        banner.appendChild(roles);

        // Mount status
        const mountLine = document.createElement("div");
        mountLine.className = "pipeline-mount";
        if (p.shared_mount && p.mount_root) {
            mountLine.className += " pipeline-mount-ok";
            mountLine.textContent = "Shared mount: " + p.mount_root + " \u2014 hardlinks will work across all services";
        } else if ((p.conflicts || []).length > 0) {
            mountLine.className += " pipeline-mount-conflict";
            const firstConflict = p.conflicts[0];
            mountLine.textContent = (firstConflict.description || "Mount conflict detected");
        }
        if (mountLine.textContent) banner.appendChild(mountLine);

        list.appendChild(banner);
    }

    // Total count + health legend
    const total = document.createElement("div");
    total.className = "stacks-total";
    total.textContent = stacks.length + " stack" + (stacks.length !== 1 ? "s" : "") + " detected";
    list.appendChild(total);

    // Single-service guidance callout — reassure users with many single-service stacks.
    // Updated to reflect pipeline awareness when available.
    const singleServiceCount = stacks.filter((s) => s.service_count === 1).length;
    if (stacks.length >= 6 && singleServiceCount / stacks.length > 0.5) {
        const guidance = document.createElement("div");
        guidance.className = "cross-stack-guidance";
        guidance.textContent = p && p.media_service_count > 0
            ? "MapArr scanned your entire directory and built a unified pipeline map. " +
              "Each stack below is analyzed with full awareness of all " + p.media_service_count + " media services."
            : "Most of your stacks contain a single service. MapArr will automatically check " +
              "sibling stacks for complementary services during analysis.";
        list.appendChild(guidance);
    }

    // Count health statuses for legend — uses effective health (pipeline-aware)
    const healthCounts = { ok: 0, caution: 0, warning: 0, problem: 0, unknown: 0 };
    stacks.forEach((s) => {
        const h = _effectiveHealth(s);
        if (h in healthCounts) healthCounts[h]++;
        else healthCounts.unknown++;
    });

    const legend = document.createElement("div");
    legend.className = "health-legend";

    // Each legend dot is clickable — filters the stack list to that health category.
    // Clicking the active filter again (or clicking the total count) resets to show all.
    let activeFilter = null;
    const allDots = [];

    function applyHealthFilter(health) {
        if (activeFilter === health) {
            // Toggle off — show all
            activeFilter = null;
            allDots.forEach((d) => d.classList.remove("legend-active", "legend-dimmed"));
            document.querySelectorAll(".stack-item").forEach((el) => el.classList.remove("hidden"));
            total.textContent = stacks.length + " stack" + (stacks.length !== 1 ? "s" : "") + " detected";
        } else {
            // Filter to this health
            activeFilter = health;
            allDots.forEach((d) => {
                const h = d.getAttribute("data-health");
                d.classList.toggle("legend-active", h === health);
                d.classList.toggle("legend-dimmed", h !== health);
            });
            let shown = 0;
            document.querySelectorAll(".stack-item").forEach((el) => {
                const path = el.getAttribute("data-stack-path");
                const stack = stacks.find((s) => s.path.replace(/\\/g, "/") === (path || "").replace(/\\/g, "/"));
                const h = stack ? _effectiveHealth(stack) : "unknown";
                const visible = h === health;
                el.classList.toggle("hidden", !visible);
                if (visible) shown++;
            });
            total.textContent = shown + " of " + stacks.length + " stacks (filtered)";
        }
    }

    function addFilterableDot(health, label, count) {
        const dot = _legendDot(health, label);
        dot.setAttribute("data-health", health);
        dot.style.cursor = count > 0 ? "pointer" : "default";
        if (count > 0) {
            dot.addEventListener("click", () => applyHealthFilter(health));
        }
        allDots.push(dot);
        legend.appendChild(dot);
    }

    addFilterableDot("ok", healthCounts.ok + " healthy", healthCounts.ok);
    if (healthCounts.caution > 0) {
        addFilterableDot("caution", healthCounts.caution + " caution", healthCounts.caution);
    }
    addFilterableDot("warning", healthCounts.warning + " need review", healthCounts.warning);
    addFilterableDot("problem", healthCounts.problem + " with issues", healthCounts.problem);
    if (healthCounts.unknown > 0) {
        addFilterableDot("unknown", healthCounts.unknown + " not applicable", healthCounts.unknown);
    }

    // Make the total count clickable to reset filter
    total.style.cursor = "pointer";
    total.addEventListener("click", () => {
        if (activeFilter) applyHealthFilter(activeFilter); // toggle off
    });

    list.appendChild(legend);

    // Traffic light reference — helps users understand what each indicator means
    const guide = document.createElement("details");
    guide.className = "traffic-light-guide";
    const guideSummary = document.createElement("summary");
    guideSummary.textContent = "What do the indicators mean?";
    guide.appendChild(guideSummary);

    const guideBody = document.createElement("div");
    guideBody.className = "traffic-light-guide-body";

    const guideEntries = [
        ["ok", "Green", "Healthy. Services share a common mount path. Hardlinks and atomic moves will work."],
        ["caution", "Blinking Yellow", "Caution. This stack is internally fine, but its mount paths differ from the rest of your pipeline. Worth investigating."],
        ["warning", "Yellow", "Needs review. Single media service or can\u2019t fully determine health from quick scan alone."],
        ["problem", "Red", "Issues found. Services mount different host directories. Hardlinks and atomic moves will fail."],
        ["unknown", "Grey", "Not applicable. No media services detected (infrastructure stack)."],
    ];

    guideEntries.forEach(([health, title, desc]) => {
        const row = document.createElement("div");
        row.className = "tl-row";
        const rowDot = document.createElement("span");
        rowDot.className = "health-dot health-" + health;
        row.appendChild(rowDot);
        const rowText = document.createElement("div");
        const strong = document.createElement("strong");
        strong.textContent = title;
        rowText.appendChild(strong);
        rowText.appendChild(document.createTextNode(" \u2014 " + desc));
        row.appendChild(rowText);
        guideBody.appendChild(row);
    });

    guide.appendChild(guideBody);
    list.appendChild(guide);

    // Render each group as a compact scrollable box.
    // Each role gets its own container with a fixed max-height so users
    // see all groups at once without a long page scroll.
    const groupMeta = [
        { key: "arr", label: "*arr Apps", items: groups.arr },
        { key: "download", label: "Download Clients", items: groups.download },
        { key: "media", label: "Media Servers", items: groups.media },
        { key: "other", label: "Infrastructure & Other", items: groups.other },
    ];

    groupMeta.forEach(({ key, label, items }) => {
        if (items.length === 0) return;

        // Group container wraps header + scrollable items
        const groupBox = document.createElement("div");
        groupBox.className = "stack-group stack-group-" + key;

        const header = document.createElement("div");
        header.className = "stack-group-header";
        const headerText = document.createElement("span");
        headerText.textContent = label;
        header.appendChild(headerText);
        const headerCount = document.createElement("span");
        headerCount.className = "stack-group-count";
        headerCount.textContent = "(" + items.length + ")";
        header.appendChild(headerCount);
        groupBox.appendChild(header);

        // Scrollable items container — holds the stack cards
        const itemsContainer = document.createElement("div");
        itemsContainer.className = "stack-group-items";
        items.forEach((stack) => {
            itemsContainer.appendChild(renderStackItem(stack, detectedService));
        });
        groupBox.appendChild(itemsContainer);

        list.appendChild(groupBox);
    });
}

function renderStackItem(stack, detectedService) {
    const item = document.createElement("div");
    item.className = "stack-item";
    item.setAttribute("data-stack-path", stack.path.replace(/\\/g, "/"));
    item.addEventListener("click", (e) => selectStack(stack, e));

    // Health indicator (traffic light) — uses effective health which factors
    // in pipeline alignment. Green = all good. Blinking yellow = internally OK
    // but doesn't fit the broader pipeline. Red = broken.
    const effectiveH = _effectiveHealth(stack);
    const dot = document.createElement("span");
    dot.className = "health-dot health-" + effectiveH;
    dot.title = _healthTooltip(effectiveH, stack.health_hint);
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
    // Reset pre-flight override flag — fresh analysis starts clean
    state.preflightOverridden = false;

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

    // Pre-flight check (Browse mode): does this stack have any media services?
    // If the pipeline shows 0 media-relevant services, there's nothing useful
    // to analyze for path mapping. Show a note but let the user proceed.
    if (state.mode === "browse" && state.pipeline) {
        const stackName = extractDirName(stack.path);
        const mediaInStack = (state.pipeline.media_services || [])
            .filter((s) => s.stack_name === stackName);
        if (mediaInStack.length === 0) {
            const svcList = (stack.services || []).join(", ") || "none detected";
            const proceed = await showBrowsePreflightWarning(stack, svcList);
            if (!proceed) return;
        }
    }

    // In browse mode, collapse the stack list to show quick-switch bar.
    // Users can type to search and instantly jump to another stack without
    // looping back through the full list. The bar shows what's currently
    // analyzed and lets you switch in one action.
    if (state.mode === "browse") {
        const stackSection = document.getElementById("step-stacks");
        const stackList = document.getElementById("stacks-list");
        const filterDiv = document.getElementById("stack-filter");
        if (filterDiv) filterDiv.classList.add("hidden");
        if (stackList) {
            stackList.classList.add("hidden");
            let selectedSummary = document.getElementById("selected-stack-summary");
            if (!selectedSummary) {
                selectedSummary = document.createElement("div");
                selectedSummary.id = "selected-stack-summary";
                selectedSummary.className = "quick-switch-bar";
                stackSection.appendChild(selectedSummary);
            }
            selectedSummary.replaceChildren();

            // Current stack indicator
            const currentRow = document.createElement("div");
            currentRow.className = "quick-switch-current";
            const dot = document.createElement("span");
            dot.className = "health-dot health-" + _effectiveHealth(stack);
            currentRow.appendChild(dot);
            const nameSpan = document.createElement("span");
            nameSpan.className = "quick-switch-name";
            nameSpan.textContent = extractDirName(stack.path);
            currentRow.appendChild(nameSpan);
            const countSpan = document.createElement("span");
            countSpan.className = "quick-switch-detail";
            countSpan.textContent = stack.service_count + " service" + (stack.service_count !== 1 ? "s" : "");
            currentRow.appendChild(countSpan);
            selectedSummary.appendChild(currentRow);

            // Quick-switch search row
            const switchRow = document.createElement("div");
            switchRow.className = "quick-switch-search";
            const searchInput = document.createElement("input");
            searchInput.type = "text";
            searchInput.className = "path-input quick-switch-input";
            searchInput.placeholder = "Search or click to browse...";
            searchInput.spellcheck = false;

            const resultsDropdown = document.createElement("div");
            resultsDropdown.className = "quick-switch-results hidden";

            wireQuickSwitchCombobox(searchInput, resultsDropdown, {
                currentPath: stack.path,
                limit: 8,
                onSelect: (matchStack) => {
                    searchInput.value = "";
                    resultsDropdown.classList.add("hidden");
                    selectStack(matchStack, null);
                },
            });

            switchRow.appendChild(searchInput);
            switchRow.appendChild(resultsDropdown);
            selectedSummary.appendChild(switchRow);

            // "Back to stack list" link — same as the bottom button
            const showAllBtn = document.createElement("button");
            showAllBtn.className = "btn btn-ghost btn-sm quick-switch-showall";
            showAllBtn.textContent = "\u2190 Back to stack list (" + state.stacks.length + " stacks)";
            showAllBtn.addEventListener("click", () => backToStackList());
            selectedSummary.appendChild(showAllBtn);
        }
    }

    // Pre-flight check 1 (Fix mode): does the error service exist in this stack?
    // If the error says "sonarr" but the stack only has radarr + qbittorrent,
    // the user probably clicked the wrong stack.
    if (state.mode === "fix" && state.parsedError && state.parsedError.service) {
        const errorSvc = state.parsedError.service.toLowerCase();
        const stackSvcs = (stack.services || []).map((s) => s.toLowerCase());
        const svcFound = stackSvcs.some((s) => s.includes(errorSvc) || errorSvc.includes(s));

        if (!svcFound && stackSvcs.length > 0) {
            const proceed = await showPreflightWarning(
                stack,
                null, // no path mismatch — this is a service mismatch
                null,
                {
                    title: "Service not found in this stack",
                    message:
                        "Your error mentions " + state.parsedError.service +
                        " but this stack only contains: " + stack.services.join(", ") + ". " +
                        "Are you sure this is the right stack?",
                }
            );
            if (!proceed) return;
            state.preflightOverridden = true;
        }
    }

    // Pre-flight check 2 (Fix mode): does the error path match any mount
    // in the selected stack? If the error path doesn't correspond to any
    // container mount in this stack, the error probably isn't from here.
    // This prevents confusing results where a healthy stack gets flagged
    // with a "path_unreachable" conflict from an unrelated error.
    if (state.mode === "fix" && state.parsedError && state.parsedError.path && state.pipeline) {
        const errorPath = (state.parsedError.path || "").replace(/\\/g, "/");
        const stackName = extractDirName(stack.path);

        // Gather container mount targets for this stack from pipeline data
        const stackServices = (state.pipeline.media_services || [])
            .filter((s) => s.stack_name === stackName);
        const containerTargets = [];
        stackServices.forEach((svc) => {
            (svc.volume_mounts || []).forEach((m) => {
                if (m.target) containerTargets.push(m.target.replace(/\/$/, ""));
            });
        });

        // Check if any container target covers the error path
        const pathCovered = containerTargets.some((t) =>
            errorPath === t || errorPath.startsWith(t + "/")
        );

        if (!pathCovered && containerTargets.length > 0) {
            // Show a warning interstitial — let user decide
            const proceed = await showPreflightWarning(stack, errorPath, containerTargets);
            if (!proceed) return; // User chose to go back
            state.preflightOverridden = true;
        }
    }

    await runAnalysis(stack);
}


/**
 * Show a pre-flight warning when something doesn't add up between the
 * user's error and the selected stack. Returns true if user wants to
 * proceed anyway, false if they want to pick a different stack.
 *
 * Two modes:
 *   1. Path mismatch: errorPath + containerTargets provided
 *   2. Custom message: override.title + override.message provided
 */
function showPreflightWarning(stack, errorPath, containerTargets, override) {
    return new Promise((resolve) => {
        const section = document.getElementById("step-fix-match");
        const existing = section.querySelector(".preflight-warning");
        if (existing) existing.remove();

        const warn = document.createElement("div");
        warn.className = "preflight-warning callout callout-warning";
        warn.style.marginTop = "0.75rem";

        const titleEl = document.createElement("p");
        titleEl.style.fontWeight = "600";
        titleEl.style.marginBottom = "0.25rem";
        titleEl.textContent = (override && override.title) || "This error might not be from this stack";
        warn.appendChild(titleEl);

        const desc = document.createElement("p");
        desc.style.fontSize = "0.82rem";
        desc.style.marginBottom = "0.5rem";

        if (override && override.message) {
            desc.textContent = override.message;
        } else {
            const pathBase = errorPath.split("/").slice(0, 3).join("/");
            desc.textContent =
                "The error path " + pathBase + "/... doesn't match any data mount " +
                "in " + extractDirName(stack.path) + ". " +
                "This stack's mounts are: " + containerTargets.join(", ") + ". " +
                "The error may be from a different configuration or an older setup.";
        }
        warn.appendChild(desc);

        const btnRow = document.createElement("div");
        btnRow.style.display = "flex";
        btnRow.style.gap = "0.5rem";

        const proceedBtn = document.createElement("button");
        proceedBtn.className = "gate-next";
        proceedBtn.style.fontSize = "0.8rem";
        proceedBtn.textContent = "Analyze anyway";
        proceedBtn.addEventListener("click", () => {
            warn.remove();
            resolve(true);
        });
        btnRow.appendChild(proceedBtn);

        const backBtn = document.createElement("button");
        backBtn.style.fontSize = "0.8rem";
        backBtn.textContent = "Pick a different stack";
        backBtn.addEventListener("click", () => {
            warn.remove();
            resolve(false);
        });
        btnRow.appendChild(backBtn);

        warn.appendChild(btnRow);
        section.appendChild(warn);
        warn.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });
}


/**
 * Browse mode pre-flight: stack has no media services.
 * Shows warning in the stacks list area. Returns true to proceed, false to cancel.
 */
function showBrowsePreflightWarning(stack, serviceList) {
    return new Promise((resolve) => {
        const section = document.getElementById("step-stacks");
        const existing = section.querySelector(".preflight-warning");
        if (existing) existing.remove();

        const warn = document.createElement("div");
        warn.className = "preflight-warning callout callout-warning";
        warn.style.marginTop = "0.75rem";

        const titleEl = document.createElement("p");
        titleEl.style.fontWeight = "600";
        titleEl.style.marginBottom = "0.25rem";
        titleEl.textContent = "No media services in this stack";
        warn.appendChild(titleEl);

        const desc = document.createElement("p");
        desc.style.fontSize = "0.82rem";
        desc.style.marginBottom = "0.5rem";
        desc.textContent =
            extractDirName(stack.path) + " has no *arr apps, download clients, or media servers. " +
            "Services found: " + serviceList + ". " +
            "MapArr analyzes path mappings between media services — there's nothing to check here.";
        warn.appendChild(desc);

        const btnRow = document.createElement("div");
        btnRow.style.display = "flex";
        btnRow.style.gap = "0.5rem";

        const proceedBtn = document.createElement("button");
        proceedBtn.className = "gate-next";
        proceedBtn.style.fontSize = "0.8rem";
        proceedBtn.textContent = "Analyze anyway";
        proceedBtn.addEventListener("click", () => {
            warn.remove();
            resolve(true);
        });
        btnRow.appendChild(proceedBtn);

        const backBtn = document.createElement("button");
        backBtn.style.fontSize = "0.8rem";
        backBtn.textContent = "Pick a different stack";
        backBtn.addEventListener("click", () => {
            warn.remove();
            resolve(false);
        });
        btnRow.appendChild(backBtn);

        warn.appendChild(btnRow);
        section.appendChild(warn);
        warn.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });
}


/**
 * Run analysis on a stack and render the results.
 *
 * Extracted so both selectStack() and post-Apply-Fix re-analysis
 * can share the same logic. Clears previous results, hits the
 * backend, renders terminal steps, and shows the appropriate
 * result card based on status.
 */
async function runAnalysis(stack) {
    clearAnalysisResults();

    const termSection = document.getElementById("step-analyzing");
    const termOutput = document.getElementById("terminal-output");
    termOutput.replaceChildren();
    setTerminalDots("running");

    // Auto-expand log panel during analysis so users see detailed backend logs.
    if (!_logState.panelOpen) {
        const toggle = document.getElementById("footer-log-toggle");
        if (toggle) {
            toggle.classList.add("log-toggle-pulse");
            setTimeout(() => toggle.classList.remove("log-toggle-pulse"), 15000);
        }
    }

    const composeFile = stack.compose_file || "docker-compose.yml";
    const composeFileName = composeFile.split(/[/\\]/).pop();
    const stackDirName = extractDirName(stack.path);

    const termTitle = document.querySelector(".terminal-title");
    if (termTitle) termTitle.textContent = stack.path.replace(/\\/g, "/") + "/" + composeFileName;

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

        // Mark this stack as verified — deep analysis is authoritative.
        // Healthy stacks skip the blinking-yellow caution override.
        const verifiedName = extractDirName(stack.path);
        if (["healthy", "healthy_pipeline", "healthy_cross_stack"].includes(data.status)) {
            state.verifiedStacks.add(verifiedName);
        }

        // Render terminal steps with staggered animation
        await renderTerminalSteps(data.steps || []);

        if (data.status === "error") {
            setTerminalDots("error");
            showAnalysisError(data.error, data.stage);
        } else if (data.status === "healthy_pipeline") {
            setTerminalDots("done");
            showHealthyResult(data);
        } else if (data.status === "healthy") {
            setTerminalDots("done");
            showHealthyResult(data);
        } else if (data.status === "healthy_cross_stack") {
            setTerminalDots("done");
            showCrossStackHealthy(data);
        } else if (data.status === "pipeline_conflict") {
            setTerminalDots("error");
            showCrossStackConflict(data);
        } else if (data.status === "cross_stack_conflict") {
            setTerminalDots("error");
            showCrossStackConflict(data);
        } else if (data.status === "incomplete") {
            setTerminalDots("warning");
            showIncompleteResult(data);
        } else {
            // Check if the user overrode a pre-flight warning and the ONLY
            // conflicts are error-path-related (not real stack issues).
            // If so, the stack is actually healthy — don't lie about it.
            const conflicts = data.conflicts || [];
            const allErrorPathOnly = state.preflightOverridden && conflicts.length > 0 &&
                conflicts.every((c) => c.type === "path_unreachable");


            if (allErrorPathOnly) {
                // Stack is actually healthy — show green with context.
                // The backend reported "path conflicts" because the pasted error
                // triggered detection, but those conflicts don't belong to this
                // stack. We need to visually CORRECT the terminal so the user
                // doesn't see yellow warnings and think something is wrong.

                // 1. Retroactively dim all yellow warning lines — they're misleading
                const termOutput = document.getElementById("terminal-output");
                termOutput.querySelectorAll(".terminal-line").forEach((line) => {
                    const icon = line.querySelector(".terminal-icon.warn");
                    if (icon) {
                        line.classList.add("terminal-line-overridden");
                        // Keep the yellow ! icon — it baits the eye, then
                        // the strikethrough resolves the tension and guides
                        // the reader down to the green RESULT line.
                    }
                });

                // 2. Add a prominent green banner that visually dominates
                setTerminalDots("done");
                addTerminalLine("ok", "────────────────────────────────────────");
                addTerminalLine("ok", "RESULT: Stack is healthy — no real issues found");
                addTerminalLine("info", "The path conflict above came from your pasted error, not this stack");
                // Update the terminal card heading to match reality
                const termHeading = document.querySelector("#step-analyzing h2");
                if (termHeading) termHeading.textContent = "Analyzed: " + extractDirName(stack.path) + " (healthy)";
                showPreflightOverrideResult(data);
            } else {
                // Genuine conflicts — show warning dots
                const hasCritical = conflicts.some((c) => c.severity === "critical");
                setTerminalDots(hasCritical ? "error" : "warning");
                showAnalysisResult(data);
            }
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
        // Stagger delay — match boot terminal pacing so both feel consistent
        const delay = step.icon === "done" ? 500 : step.icon === "info" ? 350 : 400;
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
    // Store analysis data for the auto-apply feature
    setAnalysisForApply(data);
}

/**
 * Show truthful results when user overrode a pre-flight warning and the stack
 * is actually healthy. Instead of lying ("Found 1 critical issue"), we show:
 * 1. The stack's real health — green, healthy
 * 2. An informational callout explaining why the pasted error doesn't apply
 * 3. A clear action to go back and pick the right stack
 */
function showPreflightOverrideResult(data) {
    // Show the healthy result card — because the stack IS healthy
    const section = document.getElementById("step-healthy");
    const details = document.getElementById("healthy-details");
    details.replaceChildren();

    const stepNum = section.querySelector(".step-number");
    const heading = section.querySelector("h2");
    if (stepNum) {
        stepNum.className = "step-number ok";
        stepNum.textContent = "\u2713";
    }
    if (heading) heading.textContent = "This Stack Is Healthy";

    // Summary
    const serviceCount = (data.services || []).length;
    const msg = document.createElement("p");
    msg.className = "healthy-message";
    msg.textContent = "No real issues found across " + serviceCount +
        " service" + (serviceCount !== 1 ? "s" : "") +
        " in this stack.";
    details.appendChild(msg);

    // Informational callout — explain why the error doesn't apply
    const callout = document.createElement("div");
    callout.className = "callout callout-info";
    callout.style.marginTop = "0.75rem";

    const calloutTitle = document.createElement("p");
    calloutTitle.style.fontWeight = "600";
    calloutTitle.style.marginBottom = "0.25rem";
    calloutTitle.textContent = "About the error you pasted";
    callout.appendChild(calloutTitle);

    // Pull the error path from the conflict detail for context
    const errorConflict = (data.conflicts || []).find((c) => c.type === "path_unreachable");
    if (errorConflict) {
        const explainText = document.createElement("p");
        explainText.style.margin = "0";
        explainText.textContent = errorConflict.description;
        callout.appendChild(explainText);

        // If there's an RPM hint, show it as additional context
        if (errorConflict.rpm_hint) {
            const rpmText = document.createElement("p");
            rpmText.style.margin = "0.5rem 0 0 0";
            rpmText.style.fontStyle = "italic";
            rpmText.textContent = errorConflict.rpm_hint.description;
            callout.appendChild(rpmText);
        }
    }

    const conclusion = document.createElement("p");
    conclusion.style.margin = "0.5rem 0 0 0";
    conclusion.style.fontWeight = "500";
    conclusion.textContent = "This error likely belongs to a different stack. " +
        "We warned you about this — no harm done.";
    callout.appendChild(conclusion);

    details.appendChild(callout);

    // Action: go back to pick the right stack
    const actions = document.createElement("div");
    actions.style.marginTop = "1rem";
    actions.style.display = "flex";
    actions.style.gap = "0.75rem";

    const backBtn = document.createElement("button");
    backBtn.className = "btn btn-primary";
    backBtn.textContent = "\u2190 Pick a different stack";
    backBtn.addEventListener("click", () => {
        clearAnalysisResults();
        const fixMatch = document.getElementById("step-fix-match");
        if (fixMatch && !fixMatch.classList.contains("hidden")) {
            fixMatch.scrollIntoView({ behavior: "smooth", block: "start" });
        }
    });
    actions.appendChild(backBtn);

    details.appendChild(actions);

    section.classList.remove("hidden");
    section.scrollIntoView({ behavior: "smooth", block: "nearest" });

    // Reset the flag
    state.preflightOverridden = false;
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

        // RPM hint: if this conflict is an RPM scenario, show an insight callout
        if (conflict.rpm_hint) {
            const hint = document.createElement("div");
            hint.className = "callout callout-info";
            hint.style.marginTop = "0.5rem";
            hint.textContent = conflict.rpm_hint.description;
            item.appendChild(hint);
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

/**
 * Render a one-line permission summary when all services have matching UID:GID.
 * Shows nothing when profiles are absent or when there are permission conflicts
 * (those are already rendered in the conflicts section).
 */
function renderPermissionSummaryInto(container, data) {
    const profiles = data.permission_profiles || [];
    if (profiles.length === 0) return;

    // Check if any permission conflicts exist — if so, skip the summary
    // (the conflicts section already shows the detailed issues)
    const permTypes = new Set([
        "puid_pgid_mismatch", "missing_puid_pgid", "root_execution",
        "umask_inconsistent", "umask_restrictive", "cross_stack_puid_mismatch",
    ]);
    const hasPermConflict = (data.conflicts || []).some((c) => permTypes.has(c.type));
    if (hasPermConflict) return;

    // Build a summary of UIDs found
    const knownProfiles = profiles.filter((p) => p.uid);
    if (knownProfiles.length === 0) return;

    const uids = new Set(knownProfiles.map((p) => p.uid + ":" + (p.gid || p.uid)));
    const badge = document.createElement("div");
    badge.className = "callout callout-success";
    badge.style.marginTop = "0.75rem";

    if (uids.size === 1) {
        const pair = [...uids][0];
        badge.textContent =
            "\u2713 Permissions: all " + knownProfiles.length + " media service" +
            (knownProfiles.length !== 1 ? "s" : "") + " running as UID:GID " + pair;
    } else {
        badge.textContent =
            "\u2713 Permissions: " + knownProfiles.length + " media services profiled";
    }

    container.appendChild(badge);
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

    // Clean up any previously injected elements from a prior render
    section.querySelectorAll(".solution-tracks, .track-content-quick, .track-content-proper, .infra-warning").forEach((el) => el.remove());

    // Detect infrastructure-level conflicts that YAML changes alone cannot fix.
    // Remote filesystem (SMB/CIFS/NFS) and mixed mount types require the user
    // to change their actual storage setup, not just edit compose files.
    const infraTypes = ["remote_filesystem", "mixed_mount_types", "wsl2_cross_fs"];
    const conflicts = data.conflicts || [];
    const infraConflicts = conflicts.filter((c) => infraTypes.includes(c.type));
    const hasOnlyInfra = infraConflicts.length > 0 && conflicts.every((c) => infraTypes.includes(c.type));

    if (infraConflicts.length > 0) {
        const warning = document.createElement("div");
        warning.className = "infra-warning callout callout-warning";

        const title = document.createElement("strong");
        title.textContent = hasOnlyInfra
            ? "This issue requires infrastructure changes — not just YAML edits"
            : "Some issues require infrastructure changes beyond YAML edits";
        warning.appendChild(title);

        const explain = document.createElement("p");
        explain.style.cssText = "margin: 0.4rem 0 0; font-size: 0.85rem;";
        if (infraConflicts.some((c) => c.type === "remote_filesystem")) {
            explain.textContent =
                "Your media paths are on network shares (SMB/CIFS/NFS). Hardlinks do not work across network filesystems " +
                "regardless of how your volumes are configured. You need to either move your data to local storage, or ensure ALL " +
                "services access the exact same NFS export. The YAML fix below restructures your volume paths, but the hardlink " +
                "issue will persist until the underlying storage is changed.";
        } else {
            explain.textContent =
                "Some of your services use different storage types (local vs remote). Hardlinks cannot cross filesystem " +
                "boundaries. The YAML fix below helps with path alignment, but full hardlink support requires all services " +
                "to share the same storage type.";
        }
        warning.appendChild(explain);

        summaryEl.after(warning);
    }

    // Ensure YAML blocks are back in the section (they may have been moved into properContent)
    const solutionTabs = document.getElementById("solution-tabs");
    if (solutionTabs && solutionTabs.parentElement !== section) {
        summaryEl.after(solutionTabs);
    }
    if (recommendedBlock && recommendedBlock.parentElement !== section) {
        (solutionTabs || summaryEl).after(recommendedBlock);
    }
    if (originalBlock && originalBlock.parentElement !== section) {
        recommendedBlock.after(originalBlock);
    }

    // Check if RPM wizard should be offered as an alternative.
    // Two triggers: (1) rpm_mappings with possible entries from mount overlap,
    // or (2) an rpm_hint on a conflict (error path matches a DC container path).
    const rpmMappings = data.rpm_mappings || [];
    const hasPossibleRpm = rpmMappings.some((m) => m.possible);
    const hasRpmHint = (data.conflicts || []).some((c) => c.rpm_hint);

    if (hasPossibleRpm || hasRpmHint) {
        // Show track selector: Quick Fix (RPM) vs Proper Fix (YAML restructure)
        // The RPM wizard goes above the existing YAML solution
        summaryEl.textContent = "Two fix approaches available — Quick Fix keeps your current mounts and bridges the gaps with Remote Path Mappings. Proper Fix restructures your volumes to eliminate the problem permanently.";

        const trackWrap = document.createElement("div");
        trackWrap.className = "solution-tracks";

        const trackHeader = document.createElement("div");
        trackHeader.className = "track-header";
        trackHeader.textContent = "Choose your fix approach:";
        trackWrap.appendChild(trackHeader);

        const trackOpts = document.createElement("div");
        trackOpts.className = "track-options";

        const quickBtn = document.createElement("button");
        quickBtn.className = "track-btn track-active";
        const quickTitle = document.createElement("span");
        quickTitle.textContent = "Quick Fix (RPM Wizard)";
        quickBtn.appendChild(quickTitle);
        const quickDesc = document.createElement("span");
        quickDesc.className = "track-desc";
        quickDesc.textContent = "Keep your mounts, bridge the paths step by step";
        quickBtn.appendChild(quickDesc);
        trackOpts.appendChild(quickBtn);

        const properBtn = document.createElement("button");
        properBtn.className = "track-btn";
        const properTitle = document.createElement("span");
        properTitle.textContent = "Proper Fix (Restructure)";
        properBtn.appendChild(properTitle);
        const properDesc = document.createElement("span");
        properDesc.className = "track-desc";
        properDesc.textContent = "TRaSH Guides compliance \u2014 no RPM needed after";
        properBtn.appendChild(properDesc);
        trackOpts.appendChild(properBtn);

        trackWrap.appendChild(trackOpts);

        // Quick Fix content (RPM wizard)
        const quickContent = document.createElement("div");
        quickContent.className = "track-content-quick";

        // Proper Fix content (existing YAML solution — relocated)
        const properContent = document.createElement("div");
        properContent.className = "track-content-proper hidden";

        // Insert track selector after the summary text
        summaryEl.after(trackWrap, quickContent, properContent);

        // Track toggle
        quickBtn.addEventListener("click", () => {
            quickBtn.classList.add("track-active");
            properBtn.classList.remove("track-active");
            quickContent.classList.remove("hidden");
            properContent.classList.add("hidden");
        });
        properBtn.addEventListener("click", () => {
            properBtn.classList.add("track-active");
            quickBtn.classList.remove("track-active");
            properContent.classList.remove("hidden");
            quickContent.classList.add("hidden");
        });

        // Render RPM wizard into Quick Fix track
        renderRpmWizard(data, quickContent);

        // Move existing YAML solution elements into Proper Fix track
        const solutionTabs = document.getElementById("solution-tabs");
        if (solutionTabs) properContent.appendChild(solutionTabs);
        if (recommendedBlock) properContent.appendChild(recommendedBlock);
        if (originalBlock) properContent.appendChild(originalBlock);
        // Move apply-confirm modal too
        const applyConfirm = document.getElementById("apply-confirm");
        if (applyConfirm) properContent.appendChild(applyConfirm);
        const applyResult = document.getElementById("apply-result");
        if (applyResult) properContent.appendChild(applyResult);

        // Populate YAML content as before
        if (data.solution_yaml) {
            renderYamlWithHighlights(yamlEl, data.solution_yaml, data.solution_changed_lines || []);
        } else {
            const firstFix = data.conflicts?.find((c) => c.fix);
            yamlEl.textContent = firstFix?.fix || "No specific YAML changes generated.";
        }
        if (data.original_corrected_yaml && originalTab && originalYamlEl) {
            renderYamlWithHighlights(originalYamlEl, data.original_corrected_yaml, data.original_changed_lines || []);
            originalTab.classList.remove("hidden");
        } else if (originalTab) {
            originalTab.classList.add("hidden");
        }
        switchSolutionTab("recommended");
    } else {
        // No RPM available — show standard YAML solution only
        summaryEl.textContent =
            "The Recommended Fix tab shows the ideal volume mount snippet for your services. " +
            "Switch to Your Config (Corrected) to see your full docker-compose.yml with the fixes applied — that's the version you can apply directly to your stack.";

        if (data.solution_yaml) {
            renderYamlWithHighlights(yamlEl, data.solution_yaml, data.solution_changed_lines || []);
        } else {
            const firstFix = data.conflicts?.find((c) => c.fix);
            yamlEl.textContent = firstFix?.fix || "No specific YAML changes generated.";
        }

        if (data.original_corrected_yaml && originalTab && originalYamlEl) {
            renderYamlWithHighlights(originalYamlEl, data.original_corrected_yaml, data.original_changed_lines || []);
            originalTab.classList.remove("hidden");
        } else if (originalTab) {
            originalTab.classList.add("hidden");
        }

        switchSolutionTab("recommended");
    }

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

    // Pipeline-aware: if this compose file only has one role, check the
    // pipeline for the complementary role. The category advisory applies
    // as long as the PIPELINE has both arr and download client — they don't
    // need to be in the same compose file.
    const pipeline = data.pipeline || state.pipeline;
    const pipelineByRole = pipeline ? (pipeline.services_by_role || {}) : {};
    const pipelineHasArr = hasArr || (pipelineByRole.arr || []).length > 0;
    const pipelineHasDl = hasDl || (pipelineByRole.download_client || []).length > 0;

    if (!pipelineHasArr || !pipelineHasDl) return false;

    const callout = document.createElement("div");
    callout.className = "callout callout-category";
    callout.style.cssText = "margin-bottom: 1rem; padding: 1rem 1.25rem; background: #2d2418; border-left: 4px solid #d29922; border-radius: 6px; color: #e6edf3;";

    const title = document.createElement("strong");
    title.style.cssText = "display: block; margin-bottom: 0.4rem; font-size: 0.9rem; color: #d29922;";
    title.textContent = "\u26A0 Also check: download client category save paths";
    callout.appendChild(title);

    // Gather names from both local services and pipeline
    let arrNames = services.filter((s) => s.role === "arr").map((s) => s.name);
    let dlNames = services.filter((s) => s.role === "download_client").map((s) => s.name);
    if (arrNames.length === 0 && pipelineByRole.arr) {
        arrNames = pipelineByRole.arr.map((s) => s.service_name);
    }
    if (dlNames.length === 0 && pipelineByRole.download_client) {
        dlNames = pipelineByRole.download_client.map((s) => s.service_name);
    }

    const arrName = arrNames.length > 0 ? arrNames.join(", ") : "your *arr app";

    // Deduplicate download client names (pipeline can list same service multiple times)
    const uniqueDlNames = [...new Set(dlNames.map((n) => n.toLowerCase()))];

    // Generate specific advice for each detected download client
    function dlAdvice(name) {
        const lower = name.toLowerCase();
        if (lower.includes("qbit") || lower === "qbittorrent")
            return "In qBittorrent: Options > Downloads — check Default Save Path AND each category's save path.";
        if (lower.includes("sab") || lower.includes("nzb"))
            return "In SABnzbd: Config > Folders — check the Completed Download Folder and category output folders.";
        if (lower.includes("deluge"))
            return "In Deluge: Preferences > Downloads — check the Download to path and any label/category plugin paths.";
        if (lower.includes("transmission"))
            return "In Transmission: Preferences > Downloading — check the Default download folder and incomplete directory.";
        if (lower.includes("jdownloader") || lower.includes("jdown"))
            return "In JDownloader: Settings > General — check Default Download Folder.";
        return "In " + name + ": check the download save path and any category/label output folders.";
    }

    // Render advice for each unique download client
    uniqueDlNames.forEach((dlLower) => {
        // Find the original-case name
        const originalName = dlNames.find((n) => n.toLowerCase() === dlLower) || dlLower;
        const advice = document.createElement("p");
        advice.style.cssText = "margin: 0.4rem 0; font-size: 0.88rem; color: var(--text-secondary);";
        advice.textContent = dlAdvice(originalName) +
            " These must point inside a volume mount that " + arrName + " can also see.";
        callout.appendChild(advice);
    });

    const why = document.createElement("p");
    why.style.cssText = "margin: 0.5rem 0 0; font-size: 0.82rem; color: var(--text-muted);";
    why.textContent =
        "This is the #1 cause of import failures that survives a correct volume setup. " +
        "If a download client's category path isn't under a shared mount, imports fail even with perfect volumes.";
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

    // Implementation steps
    const implSection = document.createElement("div");
    implSection.className = "trash-implementation";

    const implTitle = document.createElement("h4");
    implTitle.textContent = "How to migrate to this structure:";
    implTitle.style.cssText = "margin: 0.75rem 0 0.5rem; font-size: 0.88rem; color: var(--text);";
    implSection.appendChild(implTitle);

    const steps = [
        "Create the /data directory structure on your host (media/, torrents/, usenet/ subdirectories)",
        "Move your existing media files into the new /data/media/ subfolders",
        "Update each container's volume mounts to point to /data instead of separate paths",
        "Update download client category/save paths to use /data/torrents/ or /data/usenet/",
        "Update *arr app Root Folders (Settings > Media Management) to /data/media/tv, /data/media/movies, etc.",
        "Restart all containers and verify imports work with a test grab",
    ];

    const ol = document.createElement("ol");
    ol.style.cssText = "margin: 0; padding-left: 1.5rem; font-size: 0.85rem; color: var(--text-secondary); line-height: 1.7;";
    steps.forEach((step) => {
        const li = document.createElement("li");
        li.textContent = step;
        ol.appendChild(li);
    });
    implSection.appendChild(ol);

    const implNote = document.createElement("p");
    implNote.style.cssText = "font-size: 0.82rem; color: var(--text-muted); margin-top: 0.5rem; font-style: italic;";
    implNote.textContent = "After migrating, re-analyze your stacks in MapArr to confirm everything is aligned.";
    implSection.appendChild(implNote);

    details.appendChild(implSection);

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

    // ─── Permissions summary (one line when healthy) ───
    renderPermissionSummaryInto(details, data);

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

    // ─── Category advisory at bottom of green result ───
    // Green = "one more thing" reminder. Sits below the setup details,
    // above the action buttons. Gentle, not alarming.
    renderCategoryAdvisoryInto(details, data);

    section.classList.remove("hidden");
    showAgainButton();
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
    if (heading) heading.textContent = "Limited Analysis";

    const msg = document.createElement("p");
    msg.className = "healthy-message";
    msg.style.color = "var(--warning)";
    msg.textContent = "Single-service stack";
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
        "This compose file has media services but no " + missing.join(" or ") + " alongside them. " +
        "If they're in separate stacks nearby, try scanning the parent directory so MapArr can build a full pipeline view.";
    details.appendChild(detail);

    // Show what IS in the stack
    showCurrentSetup(data);
    renderMountWarningsInto(details, data);

    // Show cross-stack scan results if available
    const cs = data.cross_stack;
    if (cs && cs.sibling_count_scanned > 0) {
        const csCallout = document.createElement("div");
        csCallout.className = "callout callout-info";
        csCallout.textContent = cs.summary || (
            "Scanned " + cs.sibling_count_scanned + " sibling stacks but couldn't find " +
            "the missing services nearby."
        );
        details.appendChild(csCallout);
    }

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

// ─── Cross-Stack Healthy (green — siblings found, mounts aligned) ───

function showCrossStackHealthy(data) {
    const section = document.getElementById("step-healthy");
    const details = document.getElementById("healthy-details");
    details.replaceChildren();

    // Green card heading
    const stepNum = section.querySelector(".step-number");
    const heading = section.querySelector("h2");
    if (stepNum) {
        stepNum.className = "step-number ok";
        stepNum.textContent = "\u2713";
    }
    if (heading) heading.textContent = "Your Setup Looks Good (Cross-Stack)";

    const cs = data.cross_stack || {};
    const siblings = cs.siblings || [];

    // Summary message
    const msg = document.createElement("p");
    msg.className = "healthy-message";
    msg.textContent = "No path conflicts detected — complementary services found in sibling stacks.";
    details.appendChild(msg);

    // Cross-stack banner showing which siblings were found
    if (siblings.length > 0) {
        const banner = document.createElement("div");
        banner.className = "cross-stack-banner";
        const bannerIcon = document.createElement("span");
        bannerIcon.textContent = "\uD83D\uDD17 ";
        banner.appendChild(bannerIcon);
        const bannerText = document.createElement("span");
        bannerText.textContent = "Cross-stack analysis: ";
        banner.appendChild(bannerText);

        // Current stack services
        const currentServices = (data.services || [])
            .filter((s) => s.role !== "other")
            .map((s) => s.name);
        const currentPill = document.createElement("span");
        currentPill.className = "sibling-pill current";
        currentPill.textContent = currentServices.join(", ") || data.stack_name || "current";
        banner.appendChild(currentPill);

        siblings.forEach((sib) => {
            const plus = document.createTextNode(" + ");
            banner.appendChild(plus);
            const pill = document.createElement("span");
            pill.className = "sibling-pill";
            pill.textContent = sib.service_name + " (" + sib.stack_name + "/)";
            pill.title = "Found in " + sib.stack_path;
            banner.appendChild(pill);
        });

        details.appendChild(banner);
    }

    // Shared mount confirmation
    if (cs.shared_mount && cs.mount_root) {
        const mountInfo = document.createElement("div");
        mountInfo.className = "cross-stack-path-compare ok";
        mountInfo.textContent = "\u2713 Shared host path: " + cs.mount_root + " — hardlinks will work across all services.";
        details.appendChild(mountInfo);
    }

    // Mount warnings
    renderMountWarningsInto(details, data);

    // TRaSH compliance badge
    const compliance = detectTrashCompliance(data);
    const trashBadge = document.createElement("div");
    trashBadge.className = "healthy-trash-badge " + compliance;
    const trashLabels = {
        compliant: "\u2713 TRaSH Guides compliant — gold standard setup",
        close: "\u2192 Almost TRaSH compliant — some paths don't use /data root",
        "non-compliant": "\u2605 Consider the TRaSH Guides folder structure for best results",
    };
    trashBadge.textContent = trashLabels[compliance] || trashLabels["non-compliant"];
    details.appendChild(trashBadge);

    // Collapsible setup table (includes current + sibling services)
    const setupToggle = document.createElement("button");
    setupToggle.className = "why-toggle";
    setupToggle.style.marginTop = "1rem";
    const setupArrow = document.createElement("span");
    setupArrow.className = "why-toggle-arrow";
    setupArrow.textContent = "\u25B8";
    setupToggle.appendChild(setupArrow);
    setupToggle.appendChild(document.createTextNode(" View full setup (including siblings)"));
    details.appendChild(setupToggle);

    const setupContent = document.createElement("div");
    setupContent.className = "healthy-setup-content";

    // Build setup table
    const table = document.createElement("table");
    table.className = "service-volume-table";
    const thead = document.createElement("thead");
    const headerRow = document.createElement("tr");
    ["Service", "Role", "Stack", "Volume Mapping"].forEach((text) => {
        const th = document.createElement("th");
        th.textContent = text;
        headerRow.appendChild(th);
    });
    thead.appendChild(headerRow);
    table.appendChild(thead);

    const tbody = document.createElement("tbody");

    // Current stack services
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
        const stackCell = document.createElement("td");
        stackCell.className = "svc-role";
        stackCell.textContent = data.stack_name || "current";
        row.appendChild(stackCell);
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

    // Sibling services
    siblings.forEach((sib) => {
        const row = document.createElement("tr");
        row.className = "sibling-row";
        const nameCell = document.createElement("td");
        nameCell.className = "svc-name";
        nameCell.textContent = sib.service_name;
        row.appendChild(nameCell);
        const roleCell = document.createElement("td");
        roleCell.className = "svc-role";
        roleCell.textContent = formatRole(sib.role);
        row.appendChild(roleCell);
        const stackCell = document.createElement("td");
        stackCell.className = "svc-role sibling-stack-name";
        stackCell.textContent = sib.stack_name + "/";
        row.appendChild(stackCell);
        const volCell = document.createElement("td");
        volCell.className = "vol-path";
        if (sib.host_sources && sib.host_sources.length > 0) {
            sib.host_sources.forEach((src, i) => {
                if (i > 0) volCell.appendChild(document.createElement("br"));
                const span = document.createElement("span");
                span.textContent = src + " (host)";
                span.className = "vol-data";
                volCell.appendChild(span);
            });
        } else {
            volCell.textContent = "(no data volumes)";
        }
        row.appendChild(volCell);
        tbody.appendChild(row);
    });

    table.appendChild(tbody);
    setupContent.appendChild(table);

    // Category advisory
    renderCategoryAdvisoryInto(setupContent, data);

    details.appendChild(setupContent);

    // Toggle handler
    setupToggle.addEventListener("click", () => {
        const isExpanded = setupContent.classList.toggle("expanded");
        setupToggle.classList.toggle("expanded", isExpanded);
        Array.from(setupToggle.childNodes).forEach((n) => {
            if (n.nodeType === Node.TEXT_NODE) setupToggle.removeChild(n);
        });
        setupToggle.appendChild(document.createTextNode(isExpanded ? " Hide setup" : " View full setup (including siblings)"));
    });

    // Bottom quick-switch + utility actions
    renderBottomActions(details);

    section.classList.remove("hidden");
    section.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

// ─── RPM Wizard ───
// Gated wizard for Remote Path Mapping configuration.
// Each gate must be satisfied before the next unlocks. If the wizard
// completes, the RPM works. If it doesn't complete, that's on the user.

function getDcAdvice(name) {
    const lower = (name || "").toLowerCase();
    if (lower.includes("qbit") || lower === "qbittorrent")
        return {
            app: "qBittorrent",
            steps: "1. Go to Options \u2192 Downloads\n2. Note the Default Save Path\n3. Right-click each category \u2192 Edit category\n4. Note each category's Save Path",
            categories: "tv-sonarr, radarr, lidarr",
        };
    if (lower.includes("sab") || lower.includes("nzb"))
        return {
            app: "SABnzbd",
            steps: "1. Go to Config \u2192 Categories\n2. Note each category's Folder/Path value\n3. Note the default Completed Download Folder in Config \u2192 Folders",
            categories: "tv, movies, music",
        };
    if (lower.includes("nzbget"))
        return {
            app: "NZBGet",
            steps: "1. Go to Settings \u2192 Categories\n2. Note each category's DestDir value",
            categories: "tv, movies, music",
        };
    if (lower.includes("deluge"))
        return {
            app: "Deluge",
            steps: "1. Go to Preferences \u2192 Downloads\n2. Note the Download to path\n3. Check the Label plugin for per-label paths",
            categories: "tv-sonarr, radarr",
        };
    if (lower.includes("transmission"))
        return {
            app: "Transmission",
            steps: "1. Go to Preferences \u2192 Downloading\n2. Note the default download folder\n3. Note the incomplete directory if set",
            categories: "(Transmission uses one folder for all)",
        };
    if (lower.includes("rtorrent") || lower.includes("flood"))
        return {
            app: lower.includes("flood") ? "Flood" : "rTorrent",
            steps: "1. Check Settings \u2192 Downloads\n2. Note the default directory\n3. Note any per-label directory overrides",
            categories: "tv-sonarr, radarr",
        };
    if (lower.includes("jdownloader") || lower.includes("jdown"))
        return {
            app: "JDownloader",
            steps: "1. Go to Settings \u2192 General\n2. Note the Default Download Folder",
            categories: "(JDownloader uses one folder)",
        };
    if (lower.includes("aria2"))
        return {
            app: "aria2",
            steps: "1. Check your aria2.conf or RPC settings\n2. Note the dir parameter value",
            categories: "(aria2 uses one folder)",
        };
    if (lower.includes("rdtclient"))
        return {
            app: "rdtclient (Real-Debrid)",
            steps: "1. Go to Settings \u2192 Download path\n2. Note the path value (behaves like qBit API)",
            categories: "(rdtclient uses one folder)",
        };
    return {
        app: name,
        steps: "1. Open your download client settings\n2. Note the download save path\n3. Check any category/label folder overrides",
        categories: "",
    };
}

function renderRpmTable(entries, container) {
    // Group by arr_service
    const byArr = {};
    entries.forEach((m) => {
        const key = m.arr_service + " (" + m.arr_stack + ")";
        (byArr[key] = byArr[key] || []).push(m);
    });

    for (const [arrLabel, rows] of Object.entries(byArr)) {
        const header = document.createElement("div");
        header.className = "rpm-arr-header";
        header.textContent = arrLabel;
        container.appendChild(header);

        const table = document.createElement("table");
        table.className = "rpm-table";
        const thead = document.createElement("thead");
        const headerRow = document.createElement("tr");
        ["Host", "Remote Path", "Local Path"].forEach((col) => {
            const th = document.createElement("th");
            th.textContent = col;
            headerRow.appendChild(th);
        });
        thead.appendChild(headerRow);
        table.appendChild(thead);

        const tbody = document.createElement("tbody");
        rows.forEach((m) => {
            const tr = document.createElement("tr");
            [m.host, m.remote_path, m.local_path].forEach((val) => {
                const td = document.createElement("td");
                td.textContent = val;
                tr.appendChild(td);
            });
            tbody.appendChild(tr);
        });
        table.appendChild(tbody);
        container.appendChild(table);
    }
}

function renderRpmWizard(data, container) {
    const allMappings = data.rpm_mappings || [];
    // Filter to mappings relevant to this stack (current stack as DC or arr)
    const stackName = data.stack_name || "";
    const relevantMappings = allMappings.filter(
        (m) => m.dc_stack === stackName || m.arr_stack === stackName
    );
    const possibleMappings = relevantMappings.filter((m) => m.possible);
    const impossibleMappings = relevantMappings.filter((m) => !m.possible);

    if (possibleMappings.length === 0) {
        const noRpm = document.createElement("div");
        noRpm.className = "rpm-impossible";
        noRpm.textContent =
            "RPM can't help here \u2014 the host mount paths don't overlap between your " +
            "download clients and *arr apps. The data files aren't accessible across services. " +
            "Switch to the Proper Fix tab to restructure your mounts.";
        container.appendChild(noRpm);
        return;
    }

    // Unique DCs in the possible mappings
    const uniqueDcs = [];
    const seenDcs = new Set();
    possibleMappings.forEach((m) => {
        const key = m.dc_service + "|" + m.dc_stack;
        if (!seenDcs.has(key)) {
            seenDcs.add(key);
            uniqueDcs.push({ name: m.dc_service, stack: m.dc_stack });
        }
    });

    // Unique arrs in possible mappings
    const uniqueArrs = [];
    const seenArrs = new Set();
    possibleMappings.forEach((m) => {
        const key = m.arr_service + "|" + m.arr_stack;
        if (!seenArrs.has(key)) {
            seenArrs.add(key);
            uniqueArrs.push({ name: m.arr_service, stack: m.arr_stack });
        }
    });

    const wizard = document.createElement("div");
    wizard.className = "rpm-wizard";
    container.appendChild(wizard);

    let currentGate = 1;
    const gateEls = {};

    // Wizard state
    const wState = {
        usesDefaultForAll: false,
        defaultPath: "",
        categories: "",
        arrConfirmed: new Set(),
    };

    function setGateState(n, state) {
        const el = gateEls[n];
        if (!el) return;
        el.classList.remove("active", "locked", "complete");
        el.classList.add(state);
    }

    function updateGates() {
        for (let i = 1; i <= 5; i++) {
            if (i < currentGate) setGateState(i, "complete");
            else if (i === currentGate) setGateState(i, "active");
            else setGateState(i, "locked");
        }
    }

    function canAdvance(n) {
        if (n === 2) {
            return wState.usesDefaultForAll || wState.categories.trim().length > 0;
        }
        if (n === 4) {
            return wState.arrConfirmed.size >= uniqueArrs.length;
        }
        return true;
    }

    // ─── Gate 1: What We See ───
    function buildGate1() {
        const gate = document.createElement("div");
        gate.className = "wizard-gate active";
        gateEls[1] = gate;

        const header = document.createElement("div");
        header.className = "gate-header";
        const num = document.createElement("span");
        num.className = "gate-number";
        num.textContent = "1";
        header.appendChild(num);
        const title = document.createElement("span");
        title.textContent = "What MapArr Detected";
        header.appendChild(title);
        gate.appendChild(header);

        const content = document.createElement("div");
        content.className = "gate-content";

        const intro = document.createElement("p");
        intro.textContent =
            "Based on your Docker Compose volume mounts, here's what we can determine:";
        content.appendChild(intro);

        // Show each DC → arr pair
        possibleMappings.forEach((m) => {
            const detail = document.createElement("div");
            detail.className = "mount-detail";

            const pairs = [
                ["Download Client:", m.dc_service + " (" + m.dc_stack + ")"],
                ["DC mount:", m.dc_host_path + " \u2192 " + m.remote_path.replace(/\/$/, "")],
                ["*Arr App:", m.arr_service + " (" + m.arr_stack + ")"],
                ["Arr mount:", m.arr_host_path + " \u2192 " + m.local_path.replace(/\/$/, "")],
                ["Base RPM:", "Remote=" + m.remote_path + " \u2192 Local=" + m.local_path],
            ];
            pairs.forEach(([label, value]) => {
                const lbl = document.createElement("span");
                lbl.className = "mount-label";
                lbl.textContent = label;
                detail.appendChild(lbl);
                const val = document.createElement("span");
                val.className = "mount-value";
                val.textContent = value;
                detail.appendChild(val);
            });

            content.appendChild(detail);
        });

        const caveat = document.createElement("p");
        caveat.style.color = "var(--warning)";
        caveat.textContent =
            "This is based on volume mounts only. We need to verify your download client's " +
            "category settings to ensure these RPM entries are accurate.";
        content.appendChild(caveat);

        // "Missing a DC?" link
        const addDcLink = document.createElement("span");
        addDcLink.className = "add-dc-link";
        addDcLink.textContent = "+ Missing a download client? Add manually";
        const manualForm = document.createElement("div");
        manualForm.className = "manual-dc-form hidden";

        const dcNote = document.createElement("p");
        dcNote.style.fontSize = "0.78rem";
        dcNote.style.color = "var(--text-muted)";
        dcNote.textContent =
            "If a download client is in a different directory, on another machine, or not " +
            "managed by Docker Compose, we can't detect it. You can still configure RPM " +
            "manually in your *arr app's settings.";
        manualForm.appendChild(dcNote);

        addDcLink.addEventListener("click", () => {
            manualForm.classList.toggle("hidden");
        });
        content.appendChild(addDcLink);
        content.appendChild(manualForm);

        gate.appendChild(content);

        // Actions
        const actions = document.createElement("div");
        actions.className = "gate-actions";
        const nextBtn = document.createElement("button");
        nextBtn.className = "gate-next";
        nextBtn.textContent = "Next: Verify DC \u2192";
        nextBtn.addEventListener("click", () => {
            currentGate = 2;
            updateGates();
            gateEls[2].scrollIntoView({ behavior: "smooth", block: "nearest" });
        });
        actions.appendChild(nextBtn);
        gate.appendChild(actions);

        wizard.appendChild(gate);
    }

    // ─── Gate 2: Verify DC ───
    function buildGate2() {
        const gate = document.createElement("div");
        gate.className = "wizard-gate locked";
        gateEls[2] = gate;

        const header = document.createElement("div");
        header.className = "gate-header";
        const num = document.createElement("span");
        num.className = "gate-number";
        num.textContent = "2";
        header.appendChild(num);
        const title = document.createElement("span");
        title.textContent = "Verify Download Client Categories";
        header.appendChild(title);
        gate.appendChild(header);

        const content = document.createElement("div");
        content.className = "gate-content";

        // Per-DC instructions
        uniqueDcs.forEach((dc) => {
            const advice = getDcAdvice(dc.name);
            const dcSection = document.createElement("div");
            dcSection.style.marginBottom = "0.75rem";

            const dcTitle = document.createElement("p");
            dcTitle.style.fontWeight = "600";
            dcTitle.style.color = "var(--text)";
            dcTitle.textContent = "Open " + advice.app + " and check your category settings:";
            dcSection.appendChild(dcTitle);

            const stepsText = document.createElement("p");
            stepsText.style.whiteSpace = "pre-line";
            stepsText.textContent = advice.steps;
            dcSection.appendChild(stepsText);

            if (advice.categories) {
                const catHint = document.createElement("p");
                catHint.style.color = "var(--text-muted)";
                catHint.textContent = "Common categories: " + advice.categories;
                dcSection.appendChild(catHint);
            }

            content.appendChild(dcSection);
        });

        // Default path input
        const defaultLabel = document.createElement("label");
        defaultLabel.style.fontSize = "0.8rem";
        defaultLabel.style.color = "var(--text-secondary)";
        defaultLabel.style.display = "block";
        defaultLabel.style.marginTop = "0.5rem";
        defaultLabel.textContent = "Default Save Path (from your DC):";
        content.appendChild(defaultLabel);

        const defaultInput = document.createElement("input");
        defaultInput.type = "text";
        defaultInput.className = "rpm-input";
        defaultInput.placeholder = "/downloads";
        defaultInput.addEventListener("input", () => {
            wState.defaultPath = defaultInput.value;
        });
        content.appendChild(defaultInput);

        // Category input
        const catLabel = document.createElement("label");
        catLabel.style.fontSize = "0.8rem";
        catLabel.style.color = "var(--text-secondary)";
        catLabel.style.display = "block";
        catLabel.style.marginTop = "0.5rem";
        catLabel.textContent = "Categories (one per line, name:path):";
        content.appendChild(catLabel);

        const catInput = document.createElement("textarea");
        catInput.className = "rpm-input";
        catInput.rows = 3;
        catInput.placeholder = "tv-sonarr:/downloads/tv\nradarr:/downloads/movies";
        catInput.addEventListener("input", () => {
            wState.categories = catInput.value;
            nextBtn2.disabled = !canAdvance(2);
        });
        content.appendChild(catInput);

        // "Uses default for all" checkbox
        const checkRow = document.createElement("label");
        checkRow.className = "rpm-checkbox-row";
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.addEventListener("change", () => {
            wState.usesDefaultForAll = checkbox.checked;
            nextBtn2.disabled = !canAdvance(2);
            if (checkbox.checked) {
                catInput.disabled = true;
                catInput.style.opacity = "0.4";
            } else {
                catInput.disabled = false;
                catInput.style.opacity = "1";
            }
        });
        checkRow.appendChild(checkbox);
        const checkText = document.createTextNode(
            " My DC uses the default save path for all categories (no per-category paths)"
        );
        checkRow.appendChild(checkText);
        content.appendChild(checkRow);

        gate.appendChild(content);

        // Actions
        const actions = document.createElement("div");
        actions.className = "gate-actions";
        const backBtn = document.createElement("button");
        backBtn.textContent = "\u2190 Back";
        backBtn.addEventListener("click", () => {
            currentGate = 1;
            updateGates();
        });
        actions.appendChild(backBtn);

        const nextBtn2 = document.createElement("button");
        nextBtn2.className = "gate-next";
        nextBtn2.textContent = "Next: Calculate RPM \u2192";
        nextBtn2.disabled = true;
        nextBtn2.addEventListener("click", () => {
            currentGate = 3;
            updateGates();
            buildGate3Content();
            gateEls[3].scrollIntoView({ behavior: "smooth", block: "nearest" });
        });
        actions.appendChild(nextBtn2);
        gate.appendChild(actions);

        wizard.appendChild(gate);
    }

    // ─── Gate 3: Your RPM Settings ───
    let gate3Content = null;

    function buildGate3() {
        const gate = document.createElement("div");
        gate.className = "wizard-gate locked";
        gateEls[3] = gate;

        const header = document.createElement("div");
        header.className = "gate-header";
        const num = document.createElement("span");
        num.className = "gate-number";
        num.textContent = "3";
        header.appendChild(num);
        const title = document.createElement("span");
        title.textContent = "Your RPM Settings";
        header.appendChild(title);
        gate.appendChild(header);

        gate3Content = document.createElement("div");
        gate3Content.className = "gate-content";
        gate.appendChild(gate3Content);

        // Actions
        const actions = document.createElement("div");
        actions.className = "gate-actions";
        const backBtn = document.createElement("button");
        backBtn.textContent = "\u2190 Back";
        backBtn.addEventListener("click", () => {
            currentGate = 2;
            updateGates();
        });
        actions.appendChild(backBtn);

        const nextBtn3 = document.createElement("button");
        nextBtn3.className = "gate-next";
        nextBtn3.textContent = "Next: Apply in *arr \u2192";
        nextBtn3.addEventListener("click", () => {
            currentGate = 4;
            updateGates();
            gateEls[4].scrollIntoView({ behavior: "smooth", block: "nearest" });
        });
        actions.appendChild(nextBtn3);
        gate.appendChild(actions);

        wizard.appendChild(gate);
    }

    function buildGate3Content() {
        if (!gate3Content) return;
        gate3Content.replaceChildren();

        const intro = document.createElement("p");
        intro.textContent = "These are the exact RPM entries to add in your *arr apps:";
        gate3Content.appendChild(intro);

        // For now, use the base RPM mappings. Category refinement: if user entered
        // categories that are subdirs of the base container path, the base RPM already
        // handles them via prefix replacement — explain this.
        if (wState.usesDefaultForAll || wState.categories.trim().length === 0) {
            const note = document.createElement("p");
            note.style.color = "var(--text-muted)";
            note.textContent =
                "Using base RPM entries. RPM does prefix replacement, so category " +
                "subdirectories (like /downloads/tv/) are automatically handled.";
            gate3Content.appendChild(note);
        } else {
            // Parse categories and check if they require per-category RPMs
            const cats = wState.categories.trim().split("\n").filter(Boolean);
            const needsPerCat = cats.some((line) => {
                const parts = line.split(":");
                if (parts.length < 2) return false;
                const catPath = parts.slice(1).join(":").trim();
                // If category path doesn't start with the DC container base, it needs its own RPM
                return possibleMappings.some(
                    (m) => !catPath.startsWith(m.remote_path.replace(/\/$/, ""))
                );
            });

            if (needsPerCat) {
                const note = document.createElement("p");
                note.style.color = "var(--warning)";
                note.textContent =
                    "Some category paths use a different base than the default. " +
                    "Per-category RPM entries may be needed. Review the entries below.";
                gate3Content.appendChild(note);
            } else {
                const note = document.createElement("p");
                note.style.color = "var(--text-muted)";
                note.textContent =
                    "Your categories are subdirectories of the base path. The base RPM " +
                    "entry handles them all via prefix replacement.";
                gate3Content.appendChild(note);
            }
        }

        renderRpmTable(possibleMappings, gate3Content);

        if (impossibleMappings.length > 0) {
            const warn = document.createElement("div");
            warn.className = "rpm-impossible";
            const names = impossibleMappings.map(
                (m) => m.dc_service + " \u2192 " + m.arr_service
            );
            warn.textContent =
                "RPM can't bridge these pairs (host paths don't overlap): " +
                names.join(", ") +
                ". These require mount restructuring (see Proper Fix tab).";
            gate3Content.appendChild(warn);
        }
    }

    // ─── Gate 4: Apply in *Arr ───
    function buildGate4() {
        const gate = document.createElement("div");
        gate.className = "wizard-gate locked";
        gateEls[4] = gate;

        const header = document.createElement("div");
        header.className = "gate-header";
        const num = document.createElement("span");
        num.className = "gate-number";
        num.textContent = "4";
        header.appendChild(num);
        const title = document.createElement("span");
        title.textContent = "Add RPM in Your *Arr Apps";
        header.appendChild(title);
        gate.appendChild(header);

        const content = document.createElement("div");
        content.className = "gate-content";

        uniqueArrs.forEach((arr) => {
            const arrMappings = possibleMappings.filter(
                (m) => m.arr_service === arr.name && m.arr_stack === arr.stack
            );
            if (arrMappings.length === 0) return;

            const arrSection = document.createElement("div");
            arrSection.style.marginBottom = "1rem";

            const arrTitle = document.createElement("p");
            arrTitle.style.fontWeight = "600";
            arrTitle.style.color = "var(--text)";
            arrTitle.textContent = "In " + arr.name + ":";
            arrSection.appendChild(arrTitle);

            const steps = document.createElement("div");
            steps.className = "rpm-instructions";
            steps.textContent =
                "1. Open " + arr.name + " \u2192 Settings \u2192 Download Clients\n" +
                "2. Click on each download client in your list\n" +
                "3. Scroll to Remote Path Mappings\n" +
                "4. Click + to add a new mapping\n" +
                "5. Enter the Host, Remote Path, and Local Path from the table below\n" +
                "6. Click Save";
            steps.style.whiteSpace = "pre-line";
            arrSection.appendChild(steps);

            renderRpmTable(arrMappings, arrSection);

            // Confirmation checkbox
            const checkRow = document.createElement("label");
            checkRow.className = "rpm-checkbox-row";
            const cb = document.createElement("input");
            cb.type = "checkbox";
            cb.addEventListener("change", () => {
                const arrKey = arr.name + "|" + arr.stack;
                if (cb.checked) wState.arrConfirmed.add(arrKey);
                else wState.arrConfirmed.delete(arrKey);
                nextBtn4.disabled = !canAdvance(4);
            });
            checkRow.appendChild(cb);
            const cbText = document.createTextNode(
                " I've added the RPM entries in " + arr.name
            );
            checkRow.appendChild(cbText);
            arrSection.appendChild(checkRow);

            content.appendChild(arrSection);
        });

        gate.appendChild(content);

        // Actions
        const actions = document.createElement("div");
        actions.className = "gate-actions";
        const backBtn = document.createElement("button");
        backBtn.textContent = "\u2190 Back";
        backBtn.addEventListener("click", () => {
            currentGate = 3;
            updateGates();
        });
        actions.appendChild(backBtn);

        const nextBtn4 = document.createElement("button");
        nextBtn4.className = "gate-next";
        nextBtn4.textContent = "Next: Verify \u2192";
        nextBtn4.disabled = true;
        nextBtn4.addEventListener("click", () => {
            currentGate = 5;
            updateGates();
            gateEls[5].scrollIntoView({ behavior: "smooth", block: "nearest" });
        });
        actions.appendChild(nextBtn4);
        gate.appendChild(actions);

        wizard.appendChild(gate);
    }

    // ─── Gate 5: Verify ───
    function buildGate5() {
        const gate = document.createElement("div");
        gate.className = "wizard-gate locked";
        gateEls[5] = gate;

        const header = document.createElement("div");
        header.className = "gate-header";
        const num = document.createElement("span");
        num.className = "gate-number";
        num.textContent = "5";
        header.appendChild(num);
        const title = document.createElement("span");
        title.textContent = "Test Your Setup";
        header.appendChild(title);
        gate.appendChild(header);

        const content = document.createElement("div");
        content.className = "gate-content";

        const intro = document.createElement("p");
        intro.textContent = "Trigger a test download to verify RPM is working:";
        content.appendChild(intro);

        const steps = document.createElement("p");
        steps.style.whiteSpace = "pre-line";
        steps.textContent =
            "1. In your *arr app, do a manual search for any item\n" +
            "2. Grab a small file and let it download\n" +
            "3. Watch if the *arr app successfully imports it\n\n" +
            "If import succeeds \u2192 your RPM is working!\n" +
            "If import fails \u2192 go back and double-check the paths";
        content.appendChild(steps);

        gate.appendChild(content);

        // Actions
        const actions = document.createElement("div");
        actions.className = "gate-actions";
        const backBtn = document.createElement("button");
        backBtn.textContent = "\u2190 Back";
        backBtn.addEventListener("click", () => {
            currentGate = 4;
            updateGates();
        });
        actions.appendChild(backBtn);

        const brokenBtn = document.createElement("button");
        brokenBtn.textContent = "Still broken";
        brokenBtn.addEventListener("click", () => {
            // Replace gate 5 content with troubleshooting
            content.replaceChildren();
            const troubleTitle = document.createElement("p");
            troubleTitle.style.fontWeight = "600";
            troubleTitle.textContent = "Troubleshooting:";
            content.appendChild(troubleTitle);

            const tips = document.createElement("p");
            tips.style.whiteSpace = "pre-line";
            tips.textContent =
                "\u2022 Double-check the Host field matches the DC container name exactly\n" +
                "\u2022 Ensure Remote and Local paths end with a trailing slash /\n" +
                "\u2022 Restart the *arr app after adding RPM entries\n" +
                "\u2022 Check the *arr app's logs for import errors\n\n" +
                "If RPM still doesn't work, consider the Proper Fix tab \u2014 " +
                "restructuring mounts is the permanent solution.";
            content.appendChild(tips);
        });
        actions.appendChild(brokenBtn);

        const worksBtn = document.createElement("button");
        worksBtn.className = "gate-next";
        worksBtn.textContent = "It works!";
        worksBtn.addEventListener("click", () => {
            // Replace wizard with success
            wizard.replaceChildren();
            const success = document.createElement("div");
            success.className = "wizard-success";

            const icon = document.createElement("div");
            icon.className = "success-icon";
            icon.textContent = "\u2705";
            success.appendChild(icon);

            const h3 = document.createElement("h3");
            h3.textContent = "RPM Configured Successfully!";
            success.appendChild(h3);

            const desc = document.createElement("p");
            const dcNames = uniqueDcs.map((d) => d.name).join(", ");
            const arrNames = uniqueArrs.map((a) => a.name).join(", ");
            desc.textContent = dcNames + " \u2192 " + arrNames + " path mapping verified.";
            success.appendChild(desc);

            const btnRow = document.createElement("div");
            btnRow.className = "success-actions";

            const anotherBtn = document.createElement("button");
            anotherBtn.textContent = "Fix another DC";
            anotherBtn.addEventListener("click", () => {
                // Restart wizard
                wizard.replaceChildren();
                currentGate = 1;
                wState.usesDefaultForAll = false;
                wState.defaultPath = "";
                wState.categories = "";
                wState.arrConfirmed.clear();
                buildGate1();
                buildGate2();
                buildGate3();
                buildGate4();
                buildGate5();
                updateGates();
            });
            btnRow.appendChild(anotherBtn);

            const doneBtn = document.createElement("button");
            doneBtn.className = "btn-primary";
            doneBtn.textContent = "I'm all done";
            doneBtn.addEventListener("click", () => {
                showSimpleToast("RPM configuration complete!", "success");
            });
            btnRow.appendChild(doneBtn);

            success.appendChild(btnRow);
            wizard.appendChild(success);
        });
        actions.appendChild(worksBtn);
        gate.appendChild(actions);

        wizard.appendChild(gate);
    }

    // Build all gates
    buildGate1();
    buildGate2();
    buildGate3();
    buildGate4();
    buildGate5();
    updateGates();
}


// ─── Cross-Stack Conflict (red — siblings found but mounts differ) ───

function showCrossStackConflict(data) {
    const section = document.getElementById("step-healthy");
    const details = document.getElementById("healthy-details");
    details.replaceChildren();

    // Red/warning card heading
    const stepNum = section.querySelector(".step-number");
    const heading = section.querySelector("h2");
    if (stepNum) {
        stepNum.className = "step-number error-icon";
        stepNum.textContent = "\u2717";
    }
    if (heading) heading.textContent = "Cross-Stack Path Conflict";

    const cs = data.cross_stack || {};
    const siblings = cs.siblings || [];
    const conflicts = cs.conflicts || [];

    // Summary message
    const msg = document.createElement("p");
    msg.className = "healthy-message";
    msg.style.color = "var(--error)";
    msg.textContent = "Complementary services found in sibling stacks, but their host mount paths differ.";
    details.appendChild(msg);

    // Cross-stack banner showing siblings
    if (siblings.length > 0) {
        const banner = document.createElement("div");
        banner.className = "cross-stack-banner conflict";
        const bannerIcon = document.createElement("span");
        bannerIcon.textContent = "\u26A0\uFE0F ";
        banner.appendChild(bannerIcon);

        const currentServices = (data.services || [])
            .filter((s) => s.role !== "other")
            .map((s) => s.name);
        const currentPill = document.createElement("span");
        currentPill.className = "sibling-pill current";
        currentPill.textContent = currentServices.join(", ") || data.stack_name || "current";
        banner.appendChild(currentPill);

        siblings.forEach((sib) => {
            const plus = document.createTextNode(" \u2260 ");
            banner.appendChild(plus);
            const pill = document.createElement("span");
            pill.className = "sibling-pill conflict";
            pill.textContent = sib.service_name + " (" + sib.stack_name + "/)";
            banner.appendChild(pill);
        });

        details.appendChild(banner);
    }

    // Show each conflict
    conflicts.forEach((conflict) => {
        const conflictDiv = document.createElement("div");
        conflictDiv.className = "cross-stack-path-compare conflict";

        const desc = document.createElement("p");
        desc.style.fontWeight = "600";
        desc.textContent = conflict.description || "Mount path mismatch";
        conflictDiv.appendChild(desc);

        // Show the paths side by side
        if (conflict.current_sources && conflict.sibling_sources) {
            const pathCompare = document.createElement("div");
            pathCompare.className = "path-compare-grid";

            const currentLabel = document.createElement("span");
            currentLabel.className = "path-label";
            currentLabel.textContent = "Current stack:";
            pathCompare.appendChild(currentLabel);
            const currentPaths = document.createElement("code");
            currentPaths.textContent = conflict.current_sources.join(", ");
            pathCompare.appendChild(currentPaths);

            const sibLabel = document.createElement("span");
            sibLabel.className = "path-label";
            sibLabel.textContent = conflict.sibling_name + " (" + conflict.sibling_stack + "/):";
            pathCompare.appendChild(sibLabel);
            const sibPaths = document.createElement("code");
            sibPaths.textContent = conflict.sibling_sources.join(", ");
            pathCompare.appendChild(sibPaths);

            conflictDiv.appendChild(pathCompare);
        }

        details.appendChild(conflictDiv);
    });

    // Show what IS in the current stack
    showCurrentSetup(data);
    renderMountWarningsInto(details, data);

    // ─── Solution Track Selector ───
    // Two tracks: Quick Fix (RPM Wizard) and Proper Fix (Restructure Mounts)
    const rpmMappings = data.rpm_mappings || [];
    const hasPossibleRpm = rpmMappings.some((m) => m.possible);

    const trackWrap = document.createElement("div");
    trackWrap.className = "solution-tracks";

    const trackHeader = document.createElement("div");
    trackHeader.className = "track-header";
    trackHeader.textContent = "Choose your fix approach:";
    trackWrap.appendChild(trackHeader);

    const trackOpts = document.createElement("div");
    trackOpts.className = "track-options";

    const quickBtn = document.createElement("button");
    quickBtn.className = "track-btn" + (hasPossibleRpm ? " track-active" : "");
    const quickTitle = document.createElement("span");
    quickTitle.textContent = "Quick Fix (RPM Wizard)";
    quickBtn.appendChild(quickTitle);
    const quickDesc = document.createElement("span");
    quickDesc.className = "track-desc";
    quickDesc.textContent = "Keep your mounts, bridge the paths step by step";
    quickBtn.appendChild(quickDesc);
    trackOpts.appendChild(quickBtn);

    const properBtn = document.createElement("button");
    properBtn.className = "track-btn" + (!hasPossibleRpm ? " track-active" : "");
    const properTitle = document.createElement("span");
    properTitle.textContent = "Proper Fix (Restructure)";
    properBtn.appendChild(properTitle);
    const properDesc = document.createElement("span");
    properDesc.className = "track-desc";
    properDesc.textContent = "TRaSH Guides compliance \u2014 no RPM needed after";
    properBtn.appendChild(properDesc);
    trackOpts.appendChild(properBtn);

    trackWrap.appendChild(trackOpts);
    details.appendChild(trackWrap);

    // Track content containers
    const quickContent = document.createElement("div");
    quickContent.className = "track-content-quick";
    if (!hasPossibleRpm) quickContent.classList.add("hidden");
    details.appendChild(quickContent);

    const properContent = document.createElement("div");
    properContent.className = "track-content-proper";
    if (hasPossibleRpm) properContent.classList.add("hidden");
    details.appendChild(properContent);

    // Track toggle
    quickBtn.addEventListener("click", () => {
        quickBtn.classList.add("track-active");
        properBtn.classList.remove("track-active");
        quickContent.classList.remove("hidden");
        properContent.classList.add("hidden");
    });
    properBtn.addEventListener("click", () => {
        properBtn.classList.add("track-active");
        quickBtn.classList.remove("track-active");
        properContent.classList.remove("hidden");
        quickContent.classList.add("hidden");
    });

    // ─── Quick Fix Track: RPM Wizard ───
    renderRpmWizard(data, quickContent);

    // ─── Proper Fix Track: Existing Apply Fix ───
    const properIntro = document.createElement("div");
    properIntro.className = "callout callout-warning";
    properIntro.textContent =
        "Restructure your volume mounts so all media services share one root directory " +
        "(e.g. /mnt/nas:/data). After this, every service sees the same paths \u2014 " +
        "hardlinks work, imports are instant, and RPM entries become unnecessary.";
    properContent.appendChild(properIntro);

    // Apply Fix button for the Proper Fix track
    if (data && data.original_corrected_yaml && data.compose_file_path) {
        _lastAnalysisForApply = data;

        const applyWrap = document.createElement("div");
        applyWrap.className = "cross-stack-apply-wrap";
        applyWrap.style.marginTop = "1rem";

        const applyBtn = document.createElement("button");
        applyBtn.className = "apply-btn";
        applyBtn.textContent = "Apply Fix";
        applyBtn.addEventListener("click", () => {
            confirmWrap.classList.remove("hidden");
        });
        applyWrap.appendChild(applyBtn);

        const confirmWrap = document.createElement("div");
        confirmWrap.className = "apply-confirm hidden";
        confirmWrap.style.position = "relative";

        const confirmContent = document.createElement("div");
        confirmContent.className = "apply-confirm-content";

        const confirmTitle = document.createElement("p");
        confirmTitle.className = "apply-confirm-title";
        confirmTitle.textContent = "Apply fix to your compose file?";
        confirmContent.appendChild(confirmTitle);

        const confirmFile = document.createElement("p");
        confirmFile.className = "apply-confirm-detail";
        confirmFile.textContent = data.compose_file_path;
        confirmContent.appendChild(confirmFile);

        const confirmNote = document.createElement("p");
        confirmNote.className = "apply-confirm-note";
        confirmNote.textContent = "A backup (.bak) will be created before any changes are made.";
        confirmContent.appendChild(confirmNote);

        const btnRow = document.createElement("div");
        btnRow.className = "apply-confirm-buttons";

        const yesBtn = document.createElement("button");
        yesBtn.className = "apply-confirm-yes";
        yesBtn.textContent = "Apply Fix";
        yesBtn.addEventListener("click", async () => {
            yesBtn.disabled = true;
            yesBtn.textContent = "Applying...";
            try {
                const resp = await fetch("/api/apply-fix", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        compose_file_path: data.compose_file_path,
                        corrected_yaml: data.original_corrected_yaml,
                    }),
                });
                const result = await resp.json();
                if (resp.ok && result.status === "applied") {
                    resultDiv.className = "apply-result apply-result-success";
                    resultDiv.textContent = result.message;
                    resultDiv.classList.remove("hidden");
                    applyBtn.textContent = "Applied";
                    applyBtn.disabled = true;
                    applyBtn.classList.add("applied");
                    confirmWrap.classList.add("hidden");
                    showSimpleToast("Fix applied successfully!", "success");

                    _refreshHealthAfterFix().then(() => {
                        const stackPath = data.compose_file_path
                            .replace(/\\/g, "/")
                            .replace(/\/[^/]+$/, "");
                        const stackObj = state.stacks.find((s) =>
                            s.path.replace(/\\/g, "/") === stackPath
                        ) || { path: stackPath, compose_file: "docker-compose.yml" };
                        runAnalysis(stackObj);
                    });
                } else {
                    resultDiv.className = "apply-result apply-result-error";
                    resultDiv.textContent = result.error || "Failed to apply fix.";
                    resultDiv.classList.remove("hidden");
                    confirmWrap.classList.add("hidden");
                }
            } catch (err) {
                resultDiv.className = "apply-result apply-result-error";
                resultDiv.textContent = "Error: " + (err?.message || "could not reach backend");
                resultDiv.classList.remove("hidden");
                confirmWrap.classList.add("hidden");
            } finally {
                yesBtn.disabled = false;
                yesBtn.textContent = "Apply Fix";
            }
        });
        btnRow.appendChild(yesBtn);

        const noBtn = document.createElement("button");
        noBtn.className = "apply-confirm-no";
        noBtn.textContent = "Cancel";
        noBtn.addEventListener("click", () => {
            confirmWrap.classList.add("hidden");
        });
        btnRow.appendChild(noBtn);

        confirmContent.appendChild(btnRow);
        confirmWrap.appendChild(confirmContent);
        applyWrap.appendChild(confirmWrap);

        const resultDiv = document.createElement("div");
        resultDiv.className = "apply-result hidden";
        applyWrap.appendChild(resultDiv);

        properContent.appendChild(applyWrap);
    }

    // Actions
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

    // Replace the static HTML buttons with the dynamic quick-switch bar
    const staticActions = section.querySelector(".step-actions");
    if (staticActions) staticActions.classList.add("hidden");

    // Remove any previous quick-switch from this section
    const prev = section.querySelector(".bottom-quick-switch");
    if (prev) prev.remove();

    renderBottomActions(section);
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

    // Clear apply-fix state so it doesn't bleed into the next stack
    _lastAnalysisForApply = null;
    const applyBtn = document.getElementById("btn-apply-fix");
    if (applyBtn) {
        applyBtn.classList.add("hidden");
        applyBtn.disabled = false;
        applyBtn.textContent = "Apply Fix";
        applyBtn.classList.remove("applied");
    }
    const applyResult = document.getElementById("apply-result");
    if (applyResult) {
        applyResult.classList.add("hidden");
        applyResult.textContent = "";
    }
    const applyConfirm = document.getElementById("apply-confirm");
    if (applyConfirm) applyConfirm.classList.add("hidden");

    // Remove any dynamically-added "Analyze Another Stack" buttons
    document.querySelectorAll(".btn-analyze-another").forEach((el) => el.remove());
}

/**
 * Render a bottom-of-card quick-switch search + utility buttons.
 * This is the "what next?" funnel — user finishes reading results,
 * eyes land here, type to instantly analyze another stack.
 * Also includes Copy Diagnostic and Start Over as secondary actions.
 */
function renderBottomActions(container) {
    const wrapper = document.createElement("div");
    wrapper.className = "bottom-quick-switch";

    // Quick-switch search — the primary action
    if (state.mode === "browse" && state.stacks.length > 1) {
        const searchLabel = document.createElement("div");
        searchLabel.className = "bottom-switch-label";
        searchLabel.textContent = "Analyze another stack";
        wrapper.appendChild(searchLabel);

        const searchWrap = document.createElement("div");
        searchWrap.className = "quick-switch-search";
        const searchInput = document.createElement("input");
        searchInput.type = "text";
        searchInput.className = "path-input quick-switch-input";
        searchInput.placeholder = "Search or click to browse...";
        searchInput.spellcheck = false;

        const resultsDropdown = document.createElement("div");
        resultsDropdown.className = "quick-switch-results quick-switch-results-up hidden";

        wireQuickSwitchCombobox(searchInput, resultsDropdown, {
            currentPath: state.selectedStack || "",
            limit: 8,
            onSelect: (matchStack) => {
                searchInput.value = "";
                resultsDropdown.classList.add("hidden");
                selectStack(matchStack, null);
            },
        });

        searchWrap.appendChild(searchInput);
        searchWrap.appendChild(resultsDropdown);
        wrapper.appendChild(searchWrap);
    }

    // Secondary action buttons
    const buttons = document.createElement("div");
    buttons.className = "bottom-switch-buttons";

    // "Back to stack list" — primary nav action in browse mode.
    // Restores the full stack grid so users can visually pick their next target
    // without needing to remember names or search.
    if (state.mode === "browse" && state.stacks.length > 1) {
        const backBtn = document.createElement("button");
        backBtn.className = "btn btn-primary";
        const backIcon = document.createElement("span");
        backIcon.className = "btn-icon";
        backIcon.textContent = "\u2190";
        backBtn.appendChild(backIcon);
        backBtn.appendChild(document.createTextNode(" Back to Stack List"));
        backBtn.addEventListener("click", () => backToStackList());
        buttons.appendChild(backBtn);
    }

    if (state.mode !== "browse" || state.stacks.length <= 1) {
        const analyzeBtn = document.createElement("button");
        analyzeBtn.className = "btn btn-primary";
        analyzeBtn.textContent = "Analyze Another Stack";
        analyzeBtn.addEventListener("click", () => analyzeAnother());
        buttons.appendChild(analyzeBtn);
    }

    const diagBtn = document.createElement("button");
    diagBtn.className = "btn btn-subtle";
    const diagIcon = document.createElement("span");
    diagIcon.className = "btn-icon";
    diagIcon.textContent = "\uD83D\uDCCB";
    diagBtn.appendChild(diagIcon);
    diagBtn.appendChild(document.createTextNode(" Copy Diagnostic"));
    diagBtn.addEventListener("click", () => copyDiagnosticSummary());
    buttons.appendChild(diagBtn);

    const startOverBtn = document.createElement("button");
    startOverBtn.className = "btn btn-subtle";
    const soIcon = document.createElement("span");
    soIcon.className = "btn-icon";
    soIcon.textContent = "\u21BA";
    startOverBtn.appendChild(soIcon);
    startOverBtn.appendChild(document.createTextNode(" Start Over"));
    startOverBtn.addEventListener("click", () => startOver());
    buttons.appendChild(startOverBtn);

    wrapper.appendChild(buttons);
    container.appendChild(wrapper);
}

/**
 * Navigate back to the full stack list grid.
 * Clears analysis results, removes the collapsed quick-switch summary,
 * restores the full stack grid with filter bar, and scrolls to it.
 * The visual scan-and-click workflow: list → drill → fix → back to list → repeat.
 */
function backToStackList() {
    clearAnalysisResults();

    const stackSection = document.getElementById("step-stacks");

    // Remove the collapsed quick-switch summary bar
    const summary = document.getElementById("selected-stack-summary");
    if (summary) summary.remove();

    // Restore the full stack grid
    const stackList = document.getElementById("stacks-list");
    if (stackList) stackList.classList.remove("hidden");

    // Restore the filter bar if enough stacks
    const filterDiv = document.getElementById("stack-filter");
    if (filterDiv && state.stacks.length >= 6) {
        filterDiv.classList.remove("hidden");
        const filterInput = document.getElementById("stack-filter-input");
        if (filterInput) filterInput.value = "";
    }

    // Re-render stacks with fresh health data
    renderStacks(state.stacks);

    // Scroll to the stacks section
    stackSection.scrollIntoView({ behavior: "smooth", block: "start" });
}

function analyzeAnother() {
    clearAnalysisResults();

    if (state.mode === "browse") {
        // Scroll to the quick-switch bar and focus the search input.
        // Don't restore the full list — the quick-switch is faster.
        const stackSection = document.getElementById("step-stacks");
        stackSection.scrollIntoView({ behavior: "smooth", block: "start" });
        const searchInput = stackSection.querySelector(".quick-switch-input");
        if (searchInput) {
            setTimeout(() => searchInput.focus(), 400);
            return;
        }
        // Fallback if no quick-switch (shouldn't happen)
        const stackList = document.getElementById("stacks-list");
        if (stackList) stackList.classList.remove("hidden");
        const summary = document.getElementById("selected-stack-summary");
        if (summary) summary.remove();
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
        // Use non-breaking content for empty lines to preserve height
        span.textContent = line || " ";
        preEl.appendChild(span);
        // No \n text nodes — .yaml-line is display:block, which handles line breaks
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

// ─── Apply Fix ───

// Stores the last analysis result for apply-fix
let _lastAnalysisForApply = null;

function setAnalysisForApply(data) {
    _lastAnalysisForApply = data;
    // Show/hide the apply button based on whether we have a corrected original
    const applyBtn = document.getElementById("btn-apply-fix");
    if (applyBtn) {
        if (data && data.original_corrected_yaml && data.compose_file_path) {
            applyBtn.classList.remove("hidden");
        } else {
            applyBtn.classList.add("hidden");
        }
    }
}

function applyFix() {
    if (!_lastAnalysisForApply || !_lastAnalysisForApply.original_corrected_yaml) {
        showSimpleToast("No corrected configuration available to apply.", "error");
        return;
    }

    const confirm = document.getElementById("apply-confirm");
    const fileEl = document.getElementById("apply-confirm-file");
    const yesBtn = document.getElementById("apply-confirm-yes");

    fileEl.textContent = _lastAnalysisForApply.compose_file_path || _lastAnalysisForApply.compose_file || "docker-compose.yml";
    confirm.classList.remove("hidden");

    // Wire up the confirm button (replace handler to avoid stacking)
    yesBtn.onclick = () => doApplyFix();
}

function cancelApplyFix() {
    document.getElementById("apply-confirm").classList.add("hidden");
}

async function doApplyFix() {
    const confirm = document.getElementById("apply-confirm");
    const resultEl = document.getElementById("apply-result");
    const yesBtn = document.getElementById("apply-confirm-yes");

    confirm.classList.add("hidden");
    yesBtn.disabled = true;
    yesBtn.textContent = "Applying...";

    try {
        const resp = await fetch("/api/apply-fix", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                compose_file_path: _lastAnalysisForApply.compose_file_path,
                corrected_yaml: _lastAnalysisForApply.original_corrected_yaml,
            }),
        });
        const data = await resp.json();

        if (resp.ok && data.status === "applied") {
            resultEl.className = "apply-result apply-result-success";
            resultEl.textContent = (data.message || "Fix applied.") + " Your compose file has been updated but your stack has NOT been restarted. Run 'docker compose up -d' in your stack directory (or restart via your Docker manager) to apply the changes.";
            resultEl.classList.remove("hidden");
            showSimpleToast("Fix applied successfully!", "success");

            // Update the apply button
            const applyBtn = document.getElementById("btn-apply-fix");
            if (applyBtn) {
                applyBtn.textContent = "Applied";
                applyBtn.disabled = true;
                applyBtn.classList.add("applied");
            }

            // Show "Analyze Another Stack" button for quick navigation
            const nextBtn = document.createElement("button");
            nextBtn.className = "btn btn-ghost btn-analyze-another";
            nextBtn.textContent = "Analyze Another Stack";
            nextBtn.style.marginTop = "0.75rem";
            nextBtn.addEventListener("click", () => {
                switchToBrowseMode();
            });
            // Restart guidance
            const restartNote = document.createElement("p");
            restartNote.className = "apply-restart-note";
            restartNote.textContent = "Remember: restart your stack to apply the new configuration. Re-scanning will verify the YAML is correct, but the fix only takes effect after a restart.";
            resultEl.after(restartNote);
            restartNote.after(nextBtn);

            // Re-run pipeline scan (compose changed) then re-analyze.
            // This gives the user REAL proof the fix worked — fresh terminal,
            // fresh result cards, genuinely green because the analyzer read
            // the updated compose file. No cosmetic shortcuts.
            _refreshHealthAfterFix().then(() => {
                // Build a minimal stack object for runAnalysis
                const stackPath = _lastAnalysisForApply.compose_file_path
                    .replace(/\\/g, "/")
                    .replace(/\/[^/]+$/, ""); // strip compose filename
                const stackObj = state.stacks.find((s) =>
                    s.path.replace(/\\/g, "/") === stackPath
                ) || { path: stackPath, compose_file: "docker-compose.yml" };
                runAnalysis(stackObj);
            });
        } else {
            resultEl.className = "apply-result apply-result-error";
            resultEl.textContent = data.error || "Failed to apply fix.";
            resultEl.classList.remove("hidden");
            showSimpleToast(data.error || "Failed to apply fix.", "error");
        }
    } catch (err) {
        console.error("Apply fix error:", err, err?.message, err?.stack);
        if (resultEl) {
            resultEl.className = "apply-result apply-result-error";
            resultEl.textContent = "Error: " + (err?.message || "could not reach backend");
            resultEl.classList.remove("hidden");
        }
    } finally {
        yesBtn.disabled = false;
        yesBtn.textContent = "Apply Fix";
    }
}

/**
 * After Apply Fix writes a corrected compose file, re-discover stacks
 * and re-run the pipeline scan so traffic light dots reflect the new state.
 * Runs in the background — doesn't block the UI or interrupt the user.
 */
async function _refreshHealthAfterFix() {
    try {
        // Re-discover stacks to pick up changed compose file
        const scanPath = state.activeScanPath || "";
        if (!scanPath) {
            console.warn("_refreshHealthAfterFix: no activeScanPath, skipping refresh");
            return;
        }

        console.log("[post-fix] Refreshing stacks + pipeline for:", scanPath);

        const discResp = await fetch("/api/discover-stacks");
        if (discResp.ok) {
            const discData = await discResp.json();
            if (discData.stacks) {
                state.stacks = discData.stacks;
                console.log("[post-fix] Stacks refreshed:", discData.stacks.length);
            }
        } else {
            console.warn("[post-fix] discover-stacks failed:", discResp.status);
        }

        // Re-run pipeline scan — critical for fresh cross-stack analysis.
        // Without this, the re-analysis compares the fixed compose against
        // stale pipeline data and still reports conflicts.
        const pResp = await fetch("/api/pipeline-scan", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ scan_dir: scanPath }),
        });
        if (pResp.ok) {
            state.pipeline = await pResp.json();
            console.log("[post-fix] Pipeline refreshed:",
                state.pipeline.media_service_count, "services,",
                (state.pipeline.conflicts || []).length, "conflicts");
        } else {
            console.warn("[post-fix] pipeline-scan failed:", pResp.status);
        }

        // Update any visible health dots on the page (quick-switch, current stack indicator)
        document.querySelectorAll("[data-stack-path]").forEach((el) => {
            const stackPath = el.getAttribute("data-stack-path");
            const stack = state.stacks.find((s) =>
                s.path.replace(/\\/g, "/") === stackPath.replace(/\\/g, "/")
            );
            if (stack) {
                const dot = el.querySelector(".health-dot");
                if (dot) {
                    dot.className = "health-dot health-" + _effectiveHealth(stack);
                }
            }
        });

    } catch (e) {
        console.warn("Post-fix health refresh failed:", e);
    }
}

// ─── Quick-Switch Combobox ───
// Shared builder for all quick-switch dropdowns (3 locations use this).
// Supports both type-to-filter AND click-to-browse: when query is empty,
// shows all stacks instead of hiding the dropdown.

/**
 * Populate a quick-switch dropdown with matching stacks.
 * @param {string} query — filter text (empty = show all)
 * @param {HTMLElement} dropdown — the results container
 * @param {Object} opts
 * @param {string} [opts.currentPath] — path of currently selected stack (gets .current class)
 * @param {number} [opts.limit=12] — max items shown
 * @param {Function} [opts.onSelect] — callback(stack) when item clicked
 */
function populateQuickSwitch(query, dropdown, opts) {
    const q = (query || "").trim().toLowerCase();
    const limit = (opts && opts.limit) || 12;
    const currentPath = (opts && opts.currentPath) || "";
    const onSelect = (opts && opts.onSelect) || null;

    dropdown.replaceChildren();

    // Filter stacks — empty query means show all
    let matches;
    if (!q) {
        matches = state.stacks.slice(0, limit);
    } else {
        matches = state.stacks.filter((s) => {
            const name = extractDirName(s.path).toLowerCase();
            if (name.includes(q)) return true;
            return (s.services || []).some((svc) => svc.toLowerCase().includes(q));
        }).slice(0, limit);
    }

    if (matches.length === 0) {
        const none = document.createElement("div");
        none.className = "quick-switch-no-match";
        none.textContent = q ? "No matching stacks" : "No stacks available";
        dropdown.appendChild(none);
    } else {
        matches.forEach((matchStack) => {
            const item = document.createElement("div");
            item.className = "quick-switch-item";
            if (currentPath && matchStack.path === currentPath) {
                item.classList.add("current");
            }

            const dot = document.createElement("span");
            dot.className = "health-dot health-" + _effectiveHealth(matchStack);
            dot.title = _healthTooltip(matchStack.health || "unknown", matchStack.health_hint);
            item.appendChild(dot);

            const itemName = document.createElement("span");
            itemName.className = "quick-switch-item-name";
            itemName.textContent = extractDirName(matchStack.path);
            item.appendChild(itemName);

            const itemServices = document.createElement("span");
            itemServices.className = "quick-switch-item-detail";
            itemServices.textContent = (matchStack.services || []).join(", ");
            item.appendChild(itemServices);

            item.addEventListener("click", () => {
                if (onSelect) onSelect(matchStack);
            });

            dropdown.appendChild(item);
        });
    }

    dropdown.classList.remove("hidden");
}

/**
 * Wire up a quick-switch input+dropdown pair with combobox behavior.
 * Click/focus → show all. Type → filter. Blur → close.
 * @param {HTMLInputElement} input
 * @param {HTMLElement} dropdown
 * @param {Object} opts — same as populateQuickSwitch opts
 */
function wireQuickSwitchCombobox(input, dropdown, opts) {
    let timer = null;

    // Type to filter (debounced)
    input.addEventListener("input", () => {
        clearTimeout(timer);
        timer = setTimeout(() => {
            populateQuickSwitch(input.value, dropdown, opts);
        }, 150);
    });

    // Click or focus → show all (combobox browse mode)
    input.addEventListener("focus", () => {
        populateQuickSwitch(input.value, dropdown, opts);
    });

    // Close on blur (delay lets click register first)
    input.addEventListener("blur", () => {
        setTimeout(() => dropdown.classList.add("hidden"), 200);
    });
}

// ─── Helpers ───

function showSimpleToast(message, type) {
    // type: "success" or "error" — used for Apply Fix and other simple messages.
    // NOT the same as showToast(entry) which renders structured log entries.
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

/**
 * Resolve the effective display health for a stack, factoring in pipeline conflicts.
 *
 * Discovery health only sees within-stack mounts. But the pipeline scan sees
 * the full picture — this stack's mounts vs the rest of the setup. When a stack
 * is internally healthy (ok) but pipeline-misaligned, we show "caution" (blinking
 * yellow) instead of green. This prevents the misleading green-then-warning UX.
 *
 * Traffic light system:
 *   green (solid)    — healthy internally AND pipeline-aligned
 *   yellow (blink)   — internally fine, doesn't fit your broader pipeline
 *   yellow (solid)   — single service / can't fully determine
 *   red (solid)      — broken, internal conflicts
 *   grey             — no media services, not applicable
 */
function _effectiveHealth(stack) {
    const base = stack.health || "unknown";

    // Only upgrade ok → caution. Don't touch warning/problem/unknown.
    if (base !== "ok") return base;

    // If this stack was analyzed or fixed this session, trust the deep result.
    // Deep per-stack analysis is more authoritative than the broad pipeline scan.
    const stackName = extractDirName(stack.path);
    if (state.verifiedStacks.has(stackName)) return base;

    // Check if pipeline has conflicts mentioning this stack
    const p = state.pipeline;
    if (!p || !p.conflicts || p.conflicts.length === 0) return base;

    const hasConflict = p.conflicts.some(
        (c) => c.stack_name === stackName
    );

    return hasConflict ? "caution" : base;
}

function _healthTooltip(health, hint) {
    const criteria = {
        ok: "GREEN: All media services share a common host mount path. Hardlinks and atomic moves should work.",
        caution: "BLINKING YELLOW: This stack is internally healthy, but its mount paths differ from the rest of your pipeline. Click to see details.",
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
        const pathSpan = document.createElement("span");
        pathSpan.className = "dropdown-dir-path";
        pathSpan.textContent = dir;
        const metaSpan = document.createElement("span");
        metaSpan.className = "dropdown-dir-meta";
        if (isActive) {
            const curSpan = document.createElement("span");
            curSpan.className = "dropdown-dir-current";
            curSpan.textContent = "current";
            metaSpan.appendChild(curSpan);
        }
        const countSpan = document.createElement("span");
        countSpan.className = "dropdown-dir-count";
        countSpan.textContent = count + " stacks";
        metaSpan.appendChild(countSpan);
        btn.appendChild(pathSpan);
        btn.appendChild(metaSpan);
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
                showSimpleToast(data.error || "Failed to scan path.", "error");
            }
            return;
        }

        state.stacks = data.stacks || [];
        state.activeScanPath = (data.scan_path || data.path || newPath).replace(/\\/g, "/");
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
            // Not in browse mode (fork page) — update mode selector and show toast
            enrichModeSelector(state.stacks.length);
            if (state.stacks.length > 0) {
                showSimpleToast(state.stacks.length + " stacks loaded from " + extractDirName(newPath), "success");
            } else {
                showSimpleToast("No compose stacks found in " + extractDirName(newPath), "error");
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

        // Re-run pipeline scan on directory change — pipeline was built
        // from the old path, so it's stale. Fire and forget.
        state.pipeline = null;
        state.verifiedStacks.clear();
        if (state.stacks.length > 0) {
            try {
                const pResp = await fetch("/api/pipeline-scan", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ scan_dir: newPath }),
                });
                if (pResp.ok) {
                    state.pipeline = await pResp.json();
                    // Re-render stack list with pipeline banner if in browse mode
                    if (inBrowseMode && state.stacks.length > 0) {
                        renderStacks(state.stacks);
                    }
                    // Update mode selector with new pipeline context (fork page)
                    enrichModeSelector(state.stacks.length);
                }
            } catch (e) {
                console.warn("Pipeline re-scan failed:", e);
            }
        }
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
    const isHealthy = data.status === "healthy" || data.status === "healthy_pipeline";
    const isIncomplete = data.status === "incomplete";

    const lines = [];
    lines.push("## MapArr Diagnostic \u2014 " + stackName);
    lines.push("");

    // Pipeline context in diagnostic
    if (data.pipeline) {
        const p = data.pipeline;
        lines.push("**Pipeline:** " + p.total_media + " media services | health: " + p.health +
            (p.shared_mount && p.mount_root ? " | shared mount: " + p.mount_root : ""));
        lines.push("");
    }

    if (isHealthy) {
        lines.push("**Status:** \u2705 Healthy | **Services:** " + serviceCount);
        lines.push("");
        lines.push(data.fix_summary || "No path mapping issues detected. All volume mounts look correct.");
    } else if (isIncomplete) {
        lines.push("**Status:** \u26A0\uFE0F Limited Analysis | **Services:** " + serviceCount);
        lines.push("");
        lines.push(data.fix_summary || "Single-service stack \u2014 complementary services not found nearby.");
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
        showSimpleToast("No analysis data to export", "error");
        return;
    }

    navigator.clipboard.writeText(md).then(() => {
        showSimpleToast("Diagnostic copied to clipboard", "success");
    }).catch(() => {
        // Fallback for non-HTTPS
        const textarea = document.createElement("textarea");
        textarea.value = md;
        textarea.style.cssText = "position:fixed;opacity:0";
        document.body.appendChild(textarea);
        textarea.select();
        try {
            document.execCommand("copy");
            showSimpleToast("Diagnostic copied to clipboard", "success");
        } catch {
            showSimpleToast("Copy failed \u2014 try HTTPS", "error");
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


// ═══════════════════════════════════════════════════════════════════
// LOG PANEL + TOAST SYSTEM
//
// Three-tier logging UX:
//   1. Collapsible log panel in footer — full history, filterable
//   2. Download button — exports all buffered logs as .txt
//   3. Toast notifications — WARN/ERROR pop up as dismissible toasts
//
// Discrete by default. The footer shows a tiny log icon with a badge
// count for errors. Clicking opens the full panel. Toasts auto-dismiss
// after 8 seconds but stay if hovered.
// ═══════════════════════════════════════════════════════════════════

const _logState = {
    entries: [],
    errorCount: 0,
    panelOpen: false,
    sseSource: null,
    lastFetchTs: 0,
    panelHeight: 250,  // Default panel height in px
};

// ─── Log Panel Init ───

function initLogSystem() {
    const toggleBtn = document.getElementById("footer-log-toggle");
    const closeBtn = document.getElementById("log-close-btn");
    const downloadBtn = document.getElementById("log-download-btn");
    const clearBtn = document.getElementById("log-clear-btn");
    const levelFilter = document.getElementById("log-level-filter");

    if (toggleBtn) toggleBtn.addEventListener("click", toggleLogPanel);
    if (closeBtn) closeBtn.addEventListener("click", () => closeLogPanel());
    if (downloadBtn) downloadBtn.addEventListener("click", downloadLogs);
    if (clearBtn) clearBtn.addEventListener("click", clearLogPanel);
    if (levelFilter) levelFilter.addEventListener("change", () => renderLogEntries());

    // Set up drag-to-resize handle
    initLogPanelResize();

    // Fetch initial logs
    fetchLogs();

    // Connect SSE for live updates
    connectLogStream();
}

// (initLogSystem is now called immediately in the main DOMContentLoaded handler
// so SSE connects during boot and logs flow from the start.)

// ─── Log Panel Toggle ───

function toggleLogPanel() {
    if (_logState.panelOpen) {
        closeLogPanel();
    } else {
        openLogPanel();
    }
}

function openLogPanel() {
    const panel = document.getElementById("log-panel");
    if (!panel) return;
    panel.classList.remove("hidden");
    panel.style.height = _logState.panelHeight + "px";
    _logState.panelOpen = true;

    // Flip close arrow to point down (panel is open, click to close/collapse)
    const closeBtn = document.getElementById("log-close-btn");
    if (closeBtn) closeBtn.classList.add("log-btn-flip");

    // Clear any analysis-triggered pulse
    const toggle = document.getElementById("footer-log-toggle");
    if (toggle) toggle.classList.remove("log-toggle-pulse");

    // Clear error badge when panel is opened
    _logState.errorCount = 0;
    updateLogBadge();

    // Add bottom padding to page so content isn't hidden behind panel
    _updatePagePadding();

    // Scroll log entries to bottom
    const entries = document.getElementById("log-entries");
    if (entries) entries.scrollTop = entries.scrollHeight;
}

function closeLogPanel() {
    const panel = document.getElementById("log-panel");
    if (panel) panel.classList.add("hidden");
    _logState.panelOpen = false;

    // Flip close arrow back to up
    const closeBtn = document.getElementById("log-close-btn");
    if (closeBtn) closeBtn.classList.remove("log-btn-flip");

    // Remove bottom padding
    _updatePagePadding();
}

function _updatePagePadding() {
    // Add/remove padding at the bottom of the page so the log panel
    // doesn't cover content. Users can always scroll to see buttons.
    const body = document.body;
    if (_logState.panelOpen) {
        body.style.paddingBottom = (_logState.panelHeight + 20) + "px";
    } else {
        body.style.paddingBottom = "";
    }
}

// ─── Drag-to-Resize ───

function initLogPanelResize() {
    const panel = document.getElementById("log-panel");
    if (!panel) return;

    // Create the resize handle (thin bar at the top of the panel)
    const handle = document.createElement("div");
    handle.className = "log-resize-handle";
    handle.title = "Drag to resize";
    panel.insertBefore(handle, panel.firstChild);

    let startY = 0;
    let startHeight = 0;

    function onMouseDown(e) {
        e.preventDefault();
        startY = e.clientY || (e.touches && e.touches[0].clientY) || 0;
        startHeight = panel.offsetHeight;
        document.addEventListener("mousemove", onMouseMove);
        document.addEventListener("mouseup", onMouseUp);
        document.addEventListener("touchmove", onMouseMove, { passive: false });
        document.addEventListener("touchend", onMouseUp);
        document.body.style.userSelect = "none";
        handle.classList.add("dragging");
    }

    function onMouseMove(e) {
        e.preventDefault();
        const clientY = e.clientY || (e.touches && e.touches[0].clientY) || 0;
        const delta = startY - clientY; // Dragging up = positive delta = taller
        const newHeight = Math.min(Math.max(startHeight + delta, 120), window.innerHeight * 0.7);
        _logState.panelHeight = newHeight;
        panel.style.height = newHeight + "px";
        _updatePagePadding();
    }

    function onMouseUp() {
        document.removeEventListener("mousemove", onMouseMove);
        document.removeEventListener("mouseup", onMouseUp);
        document.removeEventListener("touchmove", onMouseMove);
        document.removeEventListener("touchend", onMouseUp);
        document.body.style.userSelect = "";
        handle.classList.remove("dragging");
    }

    handle.addEventListener("mousedown", onMouseDown);
    handle.addEventListener("touchstart", onMouseDown, { passive: false });
}

// ─── Fetch Logs from API ───

async function fetchLogs() {
    try {
        const resp = await fetch("/api/logs?limit=200");
        if (!resp.ok) return;
        const data = await resp.json();
        _logState.entries = (data.entries || []).reverse(); // API returns newest first, we want oldest first
        if (_logState.entries.length > 0) {
            _logState.lastFetchTs = _logState.entries[_logState.entries.length - 1].ts;
        }
        renderLogEntries();
    } catch {
        // Backend not ready yet — will get entries via SSE
    }
}

// ─── SSE Live Stream ───

function connectLogStream() {
    if (_logState.sseSource) {
        _logState.sseSource.close();
    }

    try {
        const es = new EventSource("/api/logs/stream");
        _logState.sseSource = es;

        es.addEventListener("connected", () => {
            // SSE just (re)connected — backfill any entries we missed
            // during the disconnection gap. This handles browser tab
            // throttling, network hiccups, and backend restarts.
            _backfillMissedLogs();
        });

        es.addEventListener("log", (event) => {
            try {
                const entry = JSON.parse(event.data);
                addLogEntry(entry);
            } catch {}
        });

        es.addEventListener("error", () => {
            // Reconnect after delay
            es.close();
            _logState.sseSource = null;
            setTimeout(connectLogStream, 5000);
        });
    } catch {
        // SSE not supported or network error
        // Fall back to polling
        setInterval(fetchLogs, 10000);
    }
}

/**
 * Backfill log entries missed during SSE disconnection.
 *
 * When the SSE stream drops (tab throttled, network hiccup, backend restart),
 * entries generated during the gap are lost. This fetches the server's log
 * buffer and merges any entries newer than our last known timestamp.
 * Deduplicates by timestamp+message to avoid showing the same entry twice.
 */
async function _backfillMissedLogs() {
    try {
        const resp = await fetch("/api/logs?limit=200");
        if (!resp.ok) return;
        const data = await resp.json();
        const serverEntries = (data.entries || []).reverse(); // oldest first

        if (serverEntries.length === 0) return;

        // Find our latest timestamp
        const lastTs = _logState.entries.length > 0
            ? _logState.entries[_logState.entries.length - 1].ts
            : 0;

        // Build a quick dedup key from existing entries (last 50 for perf)
        const existing = new Set();
        const recentEntries = _logState.entries.slice(-50);
        recentEntries.forEach((e) => {
            existing.add(e.ts + "|" + (e.message || "").substring(0, 60));
        });

        // Merge entries newer than our last known timestamp
        let added = 0;
        serverEntries.forEach((entry) => {
            if (entry.ts <= lastTs) return;
            const key = entry.ts + "|" + (entry.message || "").substring(0, 60);
            if (existing.has(key)) return;
            existing.add(key);
            addLogEntry(entry);
            added++;
        });

        if (added > 0) {
            console.log("Log backfill: recovered " + added + " missed entries");
        }
    } catch {
        // Backfill is best-effort — don't break the stream
    }
}

// ─── Add Log Entry ───

function addLogEntry(entry) {
    _logState.entries.push(entry);
    // Cap at 500 entries in memory
    if (_logState.entries.length > 500) {
        _logState.entries.shift();
    }

    // Render if panel is open
    if (_logState.panelOpen) {
        appendLogEntryToDOM(entry);
    }

    // Badge + toast for significant log entries.
    // Only ERROR/CRITICAL increment the badge — WARNINGs are expected during
    // pipeline scans (mount conflicts are informational, not failures) and
    // would flood the badge with false urgency on every boot.
    if (entry.level === "ERROR" || entry.level === "CRITICAL") {
        if (!_logState.panelOpen) {
            _logState.errorCount++;
            updateLogBadge();
        }
        showToast(entry);
    }
}

// ─── Render Log Entries ───

function renderLogEntries() {
    const container = document.getElementById("log-entries");
    if (!container) return;
    container.replaceChildren();

    const levelFilter = document.getElementById("log-level-filter");
    const minLevel = levelFilter ? levelFilter.value : "";
    const levelOrder = { DEBUG: 0, INFO: 1, WARNING: 2, ERROR: 3, CRITICAL: 4 };
    const minLevelNum = minLevel ? (levelOrder[minLevel] || 0) : 0;

    _logState.entries.forEach((entry) => {
        const entryLevel = levelOrder[entry.level] || 0;
        if (entryLevel >= minLevelNum) {
            appendLogEntryToDOM(entry);
        }
    });

    // Auto-scroll to bottom
    container.scrollTop = container.scrollHeight;
}

function appendLogEntryToDOM(entry) {
    const container = document.getElementById("log-entries");
    if (!container) return;

    const levelFilter = document.getElementById("log-level-filter");
    const minLevel = levelFilter ? levelFilter.value : "";
    const levelOrder = { DEBUG: 0, INFO: 1, WARNING: 2, ERROR: 3, CRITICAL: 4 };
    if (minLevel && (levelOrder[entry.level] || 0) < (levelOrder[minLevel] || 0)) {
        return; // Filtered out
    }

    const row = document.createElement("div");
    row.className = "log-entry log-entry-" + entry.level;

    const ts = document.createElement("span");
    ts.className = "log-ts";
    const date = new Date(entry.ts * 1000);
    ts.textContent = date.toLocaleTimeString();
    row.appendChild(ts);

    const level = document.createElement("span");
    level.className = "log-level log-level-" + entry.level;
    level.textContent = entry.level.substring(0, 4);
    row.appendChild(level);

    const source = document.createElement("span");
    source.className = "log-source";
    source.textContent = entry.logger ? entry.logger.replace("maparr.", "") : "";
    row.appendChild(source);

    const msg = document.createElement("span");
    msg.className = "log-msg";
    msg.textContent = entry.message;
    msg.title = entry.message; // Full text on hover
    row.appendChild(msg);

    container.appendChild(row);

    // Auto-scroll if near bottom
    const isNearBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 60;
    if (isNearBottom) {
        container.scrollTop = container.scrollHeight;
    }
}

// ─── Log Badge ───

function updateLogBadge() {
    const badge = document.getElementById("log-badge");
    if (!badge) return;
    if (_logState.errorCount > 0) {
        badge.textContent = _logState.errorCount > 99 ? "99+" : _logState.errorCount;
        badge.classList.remove("hidden");
    } else {
        badge.classList.add("hidden");
    }
}

// ─── Download Logs ───

function downloadLogs() {
    const lines = _logState.entries.map((e) => {
        const date = new Date(e.ts * 1000);
        const ts = date.toISOString();
        return ts + " [" + e.level + "] " + (e.logger || "") + ": " + e.message;
    });

    const text = "MapArr Application Logs\n"
        + "Generated: " + new Date().toISOString() + "\n"
        + "Entries: " + lines.length + "\n"
        + "─".repeat(60) + "\n\n"
        + lines.join("\n");

    const blob = new Blob([text], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "maparr-logs-" + new Date().toISOString().slice(0, 10) + ".txt";
    a.click();
    URL.revokeObjectURL(url);
}

// ─── Clear Log Panel ───

function clearLogPanel() {
    _logState.entries = [];
    _logState.errorCount = 0;
    updateLogBadge();
    const container = document.getElementById("log-entries");
    if (container) container.replaceChildren();
}

// ─── Toast Notifications ───

function showToast(entry) {
    const container = document.getElementById("toast-container");
    if (!container) return;

    const toast = document.createElement("div");
    toast.className = "toast toast-" + (entry.level === "ERROR" || entry.level === "CRITICAL" ? "error" : "warning");

    const icon = document.createElement("span");
    icon.className = "toast-icon";
    icon.textContent = entry.level === "ERROR" || entry.level === "CRITICAL" ? "\u274C" : "\u26A0\uFE0F";
    toast.appendChild(icon);

    const body = document.createElement("div");
    body.className = "toast-body";
    const msg = document.createElement("div");
    msg.className = "toast-message";
    // Truncate long messages
    const text = entry.message.length > 120 ? entry.message.substring(0, 117) + "..." : entry.message;
    msg.textContent = text;
    body.appendChild(msg);
    const meta = document.createElement("div");
    meta.className = "toast-meta";
    meta.textContent = (entry.logger || "").replace("maparr.", "") + " \u2022 " + new Date(entry.ts * 1000).toLocaleTimeString();
    body.appendChild(meta);
    toast.appendChild(body);

    const actions = document.createElement("div");
    actions.className = "toast-actions";

    const copyBtn = document.createElement("button");
    copyBtn.className = "toast-btn";
    copyBtn.textContent = "Copy";
    copyBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        navigator.clipboard.writeText(entry.message).then(() => {
            copyBtn.textContent = "Copied";
            setTimeout(() => { copyBtn.textContent = "Copy"; }, 1500);
        });
    });
    actions.appendChild(copyBtn);
    toast.appendChild(actions);

    // Click toast body → open log panel
    toast.addEventListener("click", () => {
        dismissToast(toast);
        openLogPanel();
    });

    container.appendChild(toast);

    // Auto-dismiss after 8 seconds
    const timer = setTimeout(() => dismissToast(toast), 8000);

    // Pause on hover
    toast.addEventListener("mouseenter", () => clearTimeout(timer));
    toast.addEventListener("mouseleave", () => {
        setTimeout(() => dismissToast(toast), 3000);
    });
}

function dismissToast(toast) {
    if (!toast.parentNode) return;
    toast.classList.add("toast-dismiss");
    setTimeout(() => {
        if (toast.parentNode) toast.parentNode.removeChild(toast);
    }, 200);
}
