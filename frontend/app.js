/**
 * MapArr v2.0 — Pipeline Dashboard
 *
 * Service-first UI: scans entire stacks directory, groups services by role,
 * shows conflicts with multi-file fix plans, supports Docker redeploy.
 *
 * XSS safety: All user-derived content uses textContent, never innerHTML.
 * Container clearing uses replaceChildren() instead of innerHTML.
 */

"use strict";

// ─── State ───

const state = {
    // Directory
    stacksPath: "",              // Current stacks root
    pathConfigured: false,       // Whether a path is set

    // Pipeline (primary data source)
    pipeline: null,              // Full pipeline scan result
    services: [],                // Flattened media services from pipeline
    servicesByRole: {},          // {arr: [...], download_client: [...], ...}

    // Interaction
    expandedService: null,       // Currently expanded service name
    expandedConflict: null,      // Currently expanded conflict index
    fixProgress: {},             // {compose_file_path: "pending"|"applied"|"failed"}
    fixPlans: {},                // stack_path → analysis result with corrected YAML

    // Error paste
    pastedError: null,           // Parsed error result
    highlightedServices: [],     // Service names to highlight

    // Scan state
    scanning: false,
    bootComplete: false,
    bootPhase: "idle",           // "idle" | "scanning" | "done" | "failed"

    // Legacy (carried forward for analysis detail cards)
    mode: null,
    parsedError: null,
    stacks: [],
    selectedStack: null,
    allDetectedDirs: [],
    customDirs: [],
    activeScanPath: "",
    verifiedStacks: new Set(),
    preflightOverridden: false,
    _analysisInFlight: false,
    lastAnalyzed: {},
};

// ─── Conflict Handrails — plain-English explanations for each conflict type ───
// Voice: knowledgeable friend explaining the root cause simply.
const CONFLICT_HANDRAILS = {
    // Category A: Path Conflicts
    no_shared_mount: "Your download client saves files to one folder, but your *arr app is looking in a different folder. They can't see each other's files.",
    different_host_paths: "These services think they're sharing the same folder, but on the host they're actually pointing at different directories.",
    named_volume_data: "Docker named volumes are isolated from each other. Files in one volume are invisible to services using a different volume.",
    path_unreachable: "The error path doesn't match any mount in your compose \u2014 the app can't reach the file it's looking for.",
    // Category B: Permission Conflicts
    puid_pgid_mismatch: "Your services run as different Linux users. Files created by one app can't be read by another.",
    missing_puid_pgid: "Without explicit PUID/PGID, these containers default to an internal user that probably doesn't match your other services.",
    root_execution: "Running as root (UID 0) means files are owned by root. Other services running as a normal user can't modify them \u2014 and it's a security risk.",
    umask_inconsistent: "UMASK controls who can access newly created files. Different values mean some apps can't read files created by others.",
    umask_restrictive: "Your UMASK is more restrictive than usual. Files created by this service may not be readable by others.",
    tz_mismatch: "Services in different timezones will schedule grabs at unexpected times and show confusing timestamps in logs.",
    cross_stack_puid_mismatch: "This service runs as a different Linux user than services in other stacks. Files won't be accessible across your setup.",
    // Category C: Infrastructure
    wsl2_performance: "Your media data lives on a Windows drive accessed through WSL2's filesystem bridge. This works but is significantly slower than native Linux storage.",
    remote_filesystem: "Your data is on a network share. Hardlinks don't work across network boundaries.",
    mixed_mount_types: "Some services use local storage, others use network storage. Hardlinks can't cross that boundary.",
    windows_path_in_compose: "Windows-style paths work but forward slashes and native Linux paths perform better in Docker.",
    // Pipeline/cross-stack conflict types (emitted as raw dicts, not Conflict dataclass)
    pipeline_mount_mismatch: "This service mounts data from a different location than the majority of your other services. Hardlinks and atomic moves won't work across different mount roots.",
    pipeline_permission_mismatch: "This service runs as a different Linux user than services in other stacks. Cross-stack file sharing may not work.",
    cross_stack_mount_mismatch: "This service's host mounts don't overlap with services in other stacks. Files saved by one can't be reached by the other.",
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
    const pController = new AbortController();
    const pTimeout = setTimeout(() => pController.abort(), 30000);
    try {
        const pipelineResp = await fetch("/api/pipeline-scan", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ scan_dir: discData.scan_path }),
            signal: pController.signal,
        });
        clearTimeout(pTimeout);
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
        clearTimeout(pTimeout);
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
    if (!bootScreen) return;

    // Don't transition twice
    if (state.bootComplete) return;

    bootScreen.classList.add("boot-done");
    bootScreen.addEventListener("animationend", () => {
        bootScreen.classList.add("hidden");
        bootScreen.classList.remove("boot-done");

        if (stackCount > 0 && state.activeScanPath) {
            // Pipeline Dashboard flow — scan and render
            state.stacksPath = state.activeScanPath;
            state.pathConfigured = true;
            updateHeaderPath(state.stacksPath);
            runPipelineScan();
        } else if (stackCount > 0) {
            // Stacks found but no explicit path — show dashboard with discovery
            state.stacksPath = state.allDetectedDirs[0]?.path || "";
            state.pathConfigured = !!state.stacksPath;
            if (state.pathConfigured) {
                updateHeaderPath(state.stacksPath);
                runPipelineScan();
            } else {
                showFirstLaunch();
            }
        } else {
            // No stacks — first launch or no-stacks
            showFirstLaunch();
        }
    }, { once: true });

    state.bootComplete = true;
}

/**
 * Transition from boot to the zero-stacks path input.
 */
function transitionBootToNoStacks() {
    const bootScreen = document.getElementById("boot-screen");
    if (!bootScreen) return;

    bootScreen.classList.add("boot-done");
    bootScreen.addEventListener("animationend", () => {
        bootScreen.classList.add("hidden");
        bootScreen.classList.remove("boot-done");
        showFirstLaunch();
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

// ═══════════════════════════════════════════════════════════════
// PIPELINE DASHBOARD — service-first rendering
// ═══════════════════════════════════════════════════════════════

// ─── Visibility helpers ───

function show(id) {
    const el = document.getElementById(id);
    if (el) el.classList.remove("hidden");
}

function hide(id) {
    const el = document.getElementById(id);
    if (el) el.classList.add("hidden");
}

// ─── First Launch ───

function showFirstLaunch() {
    hide("pipeline-dashboard");
    hide("boot-no-stacks");
    show("first-launch");

    const input = document.getElementById("first-launch-path");
    const btn = document.getElementById("first-launch-scan");
    if (!input || !btn) return;

    // Prefill with preferred path if available
    const preferred = getPreferredPath();
    if (preferred) input.value = preferred;

    btn.addEventListener("click", async () => {
        const path = input.value.trim();
        if (!path) { input.focus(); return; }
        await changeStacksPathTo(path);
    }, { once: true });

    input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
            e.preventDefault();
            btn.click();
        }
    });

    // Browse button on first-launch screen
    const browseBtn = document.getElementById("first-launch-browse");
    if (browseBtn) {
        browseBtn.addEventListener("click", () => {
            openDirectoryBrowser(input.value.trim() || "");
        });
    }

    setTimeout(() => input.focus(), 100);
}

// ─── Pipeline Scan ───

async function runPipelineScan() {
    state.scanning = true;
    show("pipeline-dashboard");
    hide("first-launch");
    hide("boot-no-stacks");

    // Show scanning state in health banner
    const bannerText = document.getElementById("health-banner-text");
    const bannerIcon = document.getElementById("health-banner-icon");
    if (bannerText) bannerText.textContent = "Scanning...";
    if (bannerIcon) bannerIcon.className = "health-banner-icon";

    // Disable paste bar during scan
    const pasteInput = document.getElementById("paste-error-input");
    if (pasteInput) {
        pasteInput.disabled = true;
        pasteInput.placeholder = "Scanning...";
    }

    try {
        const resp = await fetch("/api/pipeline-scan", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ scan_dir: state.stacksPath }),
            signal: AbortSignal.timeout(30000),
        });
        const data = await resp.json();
        state.pipeline = data;
        state.services = data.media_services || [];

        // Group services by role
        state.servicesByRole = {};
        for (const svc of state.services) {
            const role = svc.role || "other";
            if (!state.servicesByRole[role]) state.servicesByRole[role] = [];
            state.servicesByRole[role].push(svc);
        }

        state.scanning = false;
        renderDashboard();

    } catch (err) {
        state.scanning = false;
        if (bannerText) bannerText.textContent = "Scan failed: " + (err.message || "unknown error");
        if (bannerIcon) bannerIcon.className = "health-banner-icon health-problem";
    }
}

// ─── Dashboard Rendering ───

function renderDashboard() {
    updateHeaderPath(state.stacksPath);
    updateServiceCount(state.services.length);
    renderHealthBanner(state.pipeline);
    renderWelcomeText(state.pipeline);
    wireActionFork();
    const conflicts = state.pipeline.conflicts || [];
    renderConflictSummary(conflicts);
    renderConflictCards(conflicts);
    renderServiceGroups(state.servicesByRole);
    renderNonMediaStacks(state.pipeline.non_media_stacks || []);
    enablePasteBar();
    show("pipeline-dashboard");
    hideAnalysisCards();
}

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

function wireActionFork() {
    const forkPaste = document.getElementById("fork-paste");
    const forkExplore = document.getElementById("fork-explore");
    const pasteArea = document.getElementById("paste-area");
    const pasteClose = document.getElementById("paste-area-close");

    if (forkPaste) {
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

function renderConflictSummary(conflicts) {
    const el = document.getElementById("conflict-summary");
    if (!el) return;
    el.replaceChildren();
    if (conflicts.length === 0) { el.classList.add("hidden"); return; }

    // Category-aware summary: group by category for more meaningful labels
    const catCounts = { A: 0, B: 0, C: 0 };
    for (const c of conflicts) {
        const cat = (c.category || "").toUpperCase();
        if (cat === "A") catCounts.A++;
        else if (cat === "B") catCounts.B++;
        else if (cat === "C") catCounts.C++;
        else {
            // Fallback: infer category from severity for backward compatibility
            const sev = (c.severity || "high").toLowerCase();
            if (sev === "critical" || sev === "high") catCounts.A++;
            else catCounts.B++;
        }
    }

    const categories = [
        { key: "A", label: "path issue", plural: "path issues", cls: "summary-high" },
        { key: "B", label: "permission mismatch", plural: "permission mismatches", cls: "summary-medium" },
        { key: "C", label: "infrastructure note", plural: "infrastructure notes", cls: "summary-low" },
    ];

    let first = true;
    for (const { key, label, plural, cls } of categories) {
        if (catCounts[key] === 0) continue;
        if (!first) {
            const sep = document.createElement("span");
            sep.className = "conflict-summary-separator";
            sep.textContent = "\u00B7";
            el.appendChild(sep);
        }
        const item = document.createElement("span");
        item.className = "conflict-summary-count " + cls;
        item.textContent = catCounts[key] + " " + (catCounts[key] === 1 ? label : plural);
        el.appendChild(item);
        first = false;
    }

    const allServices = new Set();
    for (const c of conflicts) {
        for (const s of (c.services || [])) allServices.add(s);
        if (c.service_name && (!c.services || c.services.length === 0)) {
            allServices.add(c.service_name);
        }
    }
    const total = document.createElement("span");
    total.className = "conflict-summary-total";
    total.textContent = allServices.size + " service" + (allServices.size !== 1 ? "s" : "") + " affected";
    el.appendChild(total);
    el.classList.remove("hidden");
}

function updateHeaderPath(path) {
    const el = document.getElementById("header-path-text");
    if (el) {
        const display = path.length > 40 ? "..." + path.slice(-37) : path;
        el.textContent = display || "No directory selected";
    }
}

function updateServiceCount(count) {
    const el = document.getElementById("service-count");
    if (el) {
        el.textContent = count > 0 ? count + " services" : "";
    }
}

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
        // Category-aware severity: Cat A = red, Cat B = yellow, Cat C/D = yellow
        const hasCatA = conflicts.some(c => (c.category || "").toUpperCase() === "A");
        // Fallback for conflicts without category field
        const hasSevere = hasCatA || conflicts.some(c =>
            !c.category && ["critical", "high"].includes((c.severity || "high").toLowerCase())
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

const ROLE_DESCRIPTIONS = {
    arr: "Media management apps \u2014 Sonarr, Radarr, Lidarr, etc.",
    download_client: "Download clients \u2014 qBittorrent, SABnzbd, NZBGet, etc.",
    media_server: "Media servers \u2014 Plex, Jellyfin, Emby",
    request: "Request apps \u2014 Overseerr, Ombi, Petio",
    other: "Other media-related services",
};

function renderServiceGroups(servicesByRole) {
    const container = document.getElementById("service-groups");
    if (!container) return;
    container.replaceChildren();

    const roleOrder = [
        { key: "arr", label: "Arr Apps", cssClass: "service-group-arr" },
        { key: "download_client", label: "Download Clients", cssClass: "service-group-download" },
        { key: "media_server", label: "Media Servers", cssClass: "service-group-media" },
        { key: "request", label: "Request Apps", cssClass: "service-group-request" },
        { key: "other", label: "Other Services", cssClass: "service-group-other" },
    ];

    for (const { key, label, cssClass } of roleOrder) {
        const services = servicesByRole[key] || [];
        if (services.length === 0) continue;

        const group = document.createElement("div");
        group.className = "service-group " + cssClass;

        const header = document.createElement("div");
        header.className = "service-group-header";
        header.textContent = label + " (" + services.length + ")";
        header.setAttribute("data-tooltip", ROLE_DESCRIPTIONS[key] || "");
        group.appendChild(header);

        const list = document.createElement("div");
        list.className = "service-group-items";

        for (const svc of services) {
            list.appendChild(renderServiceRow(svc));
        }

        group.appendChild(list);
        if (services.length > 6) {
            list.classList.add("scrollable");
        }
        container.appendChild(group);
    }
}

function renderNonMediaStacks(stacks) {
    const container = document.getElementById("service-groups");
    if (!container || stacks.length === 0) return;

    const section = document.createElement("div");
    section.className = "non-media-stacks-section";

    const header = document.createElement("div");
    header.className = "service-group-header non-media-header";
    header.textContent = "Other Stacks (" + stacks.length + ")";
    header.setAttribute("data-tooltip",
        "Stacks without media services — not analyzed for path conflicts but shown for awareness");
    section.appendChild(header);

    const note = document.createElement("p");
    note.className = "non-media-stacks-note";
    note.textContent =
        "These stacks don\u2019t contain arr apps, download clients, or media servers, " +
        "so they\u2019re not part of the media pipeline analysis.";
    section.appendChild(note);

    const list = document.createElement("div");
    list.className = "non-media-stacks-list";

    for (const stack of stacks) {
        const chip = document.createElement("div");
        chip.className = "non-media-stack-chip";

        // Use first service name for icon, fall back to stack name
        const iconName = (stack.services && stack.services[0]) || stack.name;
        const icon = document.createElement("img");
        icon.className = "service-icon";
        icon.src = getServiceIconUrl(iconName);
        icon.alt = "";
        icon.width = 16;
        icon.height = 16;
        icon.loading = "lazy";
        chip.appendChild(icon);

        const name = document.createElement("span");
        name.textContent = stack.name;
        chip.appendChild(name);

        if (stack.services && stack.services.length > 1) {
            const count = document.createElement("span");
            count.className = "non-media-stack-count";
            count.textContent = stack.services.length + " svc";
            chip.appendChild(count);
        }

        chip.title = (stack.services || []).join(", ");
        list.appendChild(chip);
    }

    section.appendChild(list);
    container.appendChild(section);
}

// Map service names to bundled icon filenames.
// Icons sourced from dashboard-icons (CC-BY-4.0). Services without a
// dedicated icon get the generic fallback.
const SERVICE_ICONS = {
    // Media management (arr suite)
    sonarr: "sonarr", radarr: "radarr", lidarr: "lidarr",
    readarr: "readarr", prowlarr: "prowlarr", bazarr: "bazarr",
    whisparr: "whisparr", recyclarr: "recyclarr", notifiarr: "notifiarr",
    requestrr: "requestrr", doplarr: "doplarr", autobrr: "autobrr",
    kapowarr: "kapowarr", lazylibrarian: "lazylibrarian.png",
    mylar3: "mylar3.png", mylar: "mylar3.png",
    // Download clients
    qbittorrent: "qbittorrent", deluge: "deluge", transmission: "transmission",
    sabnzbd: "sabnzbd", nzbget: "nzbget", flood: "flood",
    jdownloader: "jdownloader", jdownloader2: "jdownloader", jd2: "jdownloader",
    aria2: "aria2", ariang: "aria2",
    pyload: "pyload", "pyload-ng": "pyload",
    rdtclient: "rdtclient", "rdt-client": "rdtclient",
    decypharr: "decypharr.png", blackhole: "decypharr.png",
    vuze: "vuze.png", biglybt: "vuze.png",
    zurg: "zurg",
    rutorrent: "rutorrent", nzbhydra2: "nzbhydra2", jackett: "jackett",
    // Media servers & players
    plex: "plex", jellyfin: "jellyfin", emby: "emby",
    overseerr: "overseerr", jellyseerr: "jellyseerr", seerr: "seerr", ombi: "ombi",
    recommendarr: "recommendarr",
    tautulli: "tautulli", navidrome: "navidrome", funkwhale: "funkwhale",
    audiobookshelf: "audiobookshelf", stash: "stash", kavita: "kavita",
    // Media tools & subtitle services
    filebot: "filebot", handbrake: "handbrake", metube: "metube",
    tdarr: "tdarr.png", unmanic: "unmanic.png",
    "cross-seed": "cross-seed", crossseed: "cross-seed",
    subgen: "subgen.png", subgentest: "subgentest.png",
    subsyncarrplus: "subsyncarrplus", "subsyncarr-plus": "subsyncarrplus",
    suggestarr: "suggestarr.ico", huntarr: "huntarr.png",
    agregarr: "agregarr", imageprotector: "imageprotector",
    kometa: "kometa", "plex-meta-manager": "kometa",
    organizr: "organizr", makemkv: "makemkv",
    tubearchivist: "tubearchivist", "tube-archivist": "tubearchivist",
    termix: "termix",
    // Photo & documents
    immich: "immich", photoprism: "photoprism",
    "paperless-ngx": "paperless-ngx", "stirling-pdf": "stirling-pdf",
    "calibre-web": "calibre-web",
    // Networking & reverse proxy
    nginx: "nginx", traefik: "traefik", caddy: "caddy", swag: "swag",
    "nginx-proxy-manager": "nginx-proxy-manager",
    wireguard: "wireguard", tailscale: "tailscale", gluetun: "gluetun",
    flaresolverr: "flaresolverr", cloudflare: "cloudflare",
    ddclient: "ddclient", duckdns: "duckdns",
    zerotier: "zerotier", headscale: "headscale", netbird: "netbird",
    crowdsec: "crowdsec",
    // Databases & admin
    mariadb: "mariadb", postgres: "postgres", redis: "redis",
    mongodb: "mongodb", mysql: "mysql", minio: "minio", pgadmin: "pgadmin",
    adminer: "adminer", phpmyadmin: "phpmyadmin",
    // Monitoring & logging
    grafana: "grafana", prometheus: "prometheus", "uptime-kuma": "uptime-kuma",
    loki: "loki", netdata: "netdata", vector: "vector",
    glances: "glances", dozzle: "dozzle", scrutiny: "scrutiny", seq: "seq",
    meshmonitor: "meshmonitor",
    healthchecks: "healthchecks", librespeed: "librespeed",
    plausible: "plausible", umami: "umami", matomo: "matomo",
    gatus: "gatus", openspeedtest: "openspeedtest", netbox: "netbox",
    "speedtest-tracker": "speedtest-tracker",
    // Auth & security
    authelia: "authelia", authentik: "authentik", vaultwarden: "vaultwarden",
    bitwarden: "bitwarden", vault: "vault", consul: "consul",
    // Docker management
    portainer: "portainer", watchtower: "watchtower", dockge: "dockge",
    dockhand: "dockhand", socketproxy: "socketproxy", "docker-socket-proxy": "socketproxy",
    // Backup & sync
    backrest: "backrest", duplicati: "duplicati", syncthing: "syncthing",
    "resilio-sync": "resilio-sync",
    // Home & productivity
    "actual-budget": "actual-budget", mealie: "mealie", grocy: "grocy",
    nextcloud: "nextcloud", gitea: "gitea", homarr: "homarr", homer: "homer",
    filebrowser: "filebrowser", "yt-dlp": "yt-dlp",
    changedetection: "changedetection", bookstack: "bookstack",
    "wiki-js": "wiki-js", wordpress: "wordpress", obsidian: "obsidian",
    trilium: "trilium", excalidraw: "excalidraw", hedgedoc: "hedgedoc",
    privatebin: "privatebin", drawio: "drawio", etherpad: "etherpad",
    // Notifications & messaging
    gotify: "gotify", ntfy: "ntfy", mosquitto: "mosquitto",
    signal: "signal", signalapi: "signalapi", mattermost: "mattermost",
    discord: "discord", slack: "slack",
    rocketchat: "rocketchat", element: "element", matrix: "matrix",
    // Automation & CI/CD
    "node-red": "node-red", n8n: "n8n", jenkins: "jenkins",
    semaphore: "semaphore", drone: "drone", huginn: "huginn",
    ansible: "ansible", terraform: "terraform",
    // Development
    "code-server": "code-server",
    // Home automation
    "home-assistant": "home-assistant", homeassistant: "home-assistant",
    // Ad blocking & DNS
    "adguard-home": "adguard-home", pihole: "pihole",
    // RSS & news
    miniflux: "miniflux", freshrss: "freshrss",
    // Search
    searxng: "searxng",
    // Email
    mailcow: "mailcow",
    // Recipes
    tandoor: "tandoor",
    // Torrents (alternate names)
    rtorrent: "rtorrent",
};

function getServiceIconUrl(serviceName) {
    const lower = (serviceName || "").toLowerCase();
    const match = SERVICE_ICONS[lower];
    if (match) return "/static/img/services/" + match + (match.includes(".") ? "" : ".svg");
    // Check partial matches (e.g. "nzbhydra" matches "nzbhydra2")
    for (const [key, file] of Object.entries(SERVICE_ICONS)) {
        if (lower.includes(key) || key.includes(lower))
            return "/static/img/services/" + file + (file.includes(".") ? "" : ".svg");
    }
    return "/static/img/services/generic.svg";
}

function renderServiceRow(svc) {
    const row = document.createElement("div");
    row.className = "service-row";
    row.setAttribute("data-service", svc.service_name);

    // Health dot
    const dot = document.createElement("span");
    dot.className = "health-dot " + getServiceHealth(svc);
    dot.setAttribute("data-tooltip", _healthDotTooltip(getServiceHealth(svc)));
    row.appendChild(dot);

    // Service icon
    const icon = document.createElement("img");
    icon.className = "service-icon";
    icon.src = getServiceIconUrl(svc.service_name);
    icon.alt = "";
    icon.width = 20;
    icon.height = 20;
    icon.loading = "lazy";
    row.appendChild(icon);

    // Service info
    const info = document.createElement("div");
    info.className = "service-info";

    const name = document.createElement("span");
    name.className = "service-name";
    name.textContent = svc.service_name;
    info.appendChild(name);

    const meta = document.createElement("span");
    meta.className = "service-meta";
    const family = svc.family_name || "Unknown";
    const sources = (svc.host_sources || []).slice(0, 2).join(", ") || "no data mounts";
    meta.textContent = family + " \u00B7 " + sources;
    const familyTip = _familyTooltip(svc.family_name);
    if (familyTip) meta.setAttribute("data-tooltip", familyTip);
    info.appendChild(meta);

    row.appendChild(info);

    // File location
    const file = document.createElement("span");
    file.className = "service-file";
    file.textContent = (svc.stack_name || "") + "/";
    row.appendChild(file);

    // Click to expand
    row.addEventListener("click", () => toggleServiceDetail(svc));

    return row;
}

function getServiceHealth(svc) {
    // Check if fix has been applied for this service's compose file
    const composePath = svc.compose_file || "";
    if (state.fixProgress[composePath] === "applied") return "awaiting";

    // Check if service is involved in any conflict — match via services array or service_name.
    // Category-aware: Cat A = problem (red), Cat B = issue (yellow), Cat C/D = healthy (no dot change).
    const conflicts = (state.pipeline && state.pipeline.conflicts) || [];
    const matchedCategories = new Set();
    for (const c of conflicts) {
        const services = c.services || [];
        const matchesByName = c.service_name === svc.service_name;
        if (services.includes(svc.service_name) || matchesByName) {
            const cat = (c.category || "").toUpperCase();
            if (cat) matchedCategories.add(cat);
            // Fallback for conflicts without category: use severity
            if (!cat) {
                const sev = (c.severity || "high").toLowerCase();
                if (sev === "critical" || sev === "high") matchedCategories.add("A");
                else matchedCategories.add("B");
            }
        }
    }
    if (matchedCategories.has("A")) return "problem";     // Red — path conflicts
    if (matchedCategories.has("B")) return "issue";        // Yellow — permission issues
    // Cat C/D don't affect health dot
    if (matchedCategories.size > 0) return "healthy";

    return "healthy";
}

function toggleServiceDetail(svc) {
    const existing = document.querySelector(".service-detail-panel");
    const row = document.querySelector('[data-service="' + svc.service_name + '"]');

    // Collapse if already expanded
    if (state.expandedService === svc.service_name) {
        if (existing) existing.remove();
        if (row) row.classList.remove("expanded");
        state.expandedService = null;
        return;
    }

    // Collapse previous
    if (existing) existing.remove();
    document.querySelectorAll(".service-row.expanded").forEach(r => r.classList.remove("expanded"));

    // Build detail panel
    const panel = document.createElement("div");
    panel.className = "service-detail-panel";

    // Image
    addDetailRow(panel, "Image", svc.image || "unknown");
    addDetailRow(panel, "Family", svc.family_name || "Unknown");

    // Permissions
    if (svc.environment) {
        const uid = svc.environment.PUID || svc.environment.USER_ID || "\u2014";
        const gid = svc.environment.PGID || svc.environment.GROUP_ID || "\u2014";
        addDetailRow(panel, "UID:GID", uid + ":" + gid);
    }

    // Compose file
    addDetailRow(panel, "File", svc.compose_file || (svc.stack_name + "/docker-compose.yml"));

    // Volumes
    if (svc.volume_mounts && svc.volume_mounts.length > 0) {
        const volHeader = document.createElement("div");
        volHeader.className = "detail-section-header";
        volHeader.textContent = "Volumes";
        panel.appendChild(volHeader);

        for (const mount of svc.volume_mounts) {
            const line = document.createElement("div");
            line.className = "detail-volume";
            line.textContent = (mount.source || mount.host || "") + " : " + (mount.target || mount.container || "");
            panel.appendChild(line);
        }
    }

    // Pipeline context
    let siblings = [];
    if (svc.host_sources && svc.host_sources.length > 0) {
        const ctxHeader = document.createElement("div");
        ctxHeader.className = "detail-section-header";
        ctxHeader.textContent = "Pipeline";
        panel.appendChild(ctxHeader);

        siblings = state.services.filter(s =>
            s.service_name !== svc.service_name &&
            s.host_sources && svc.host_sources &&
            s.host_sources.some(h => svc.host_sources.includes(h))
        );
        const ctxLine = document.createElement("div");
        ctxLine.className = "detail-volume";
        ctxLine.textContent = siblings.length > 0
            ? "Shares mount root with " + siblings.length + " sibling" + (siblings.length !== 1 ? "s" : "")
            : "Isolated from pipeline";
        ctxLine.setAttribute("data-tooltip", siblings.length > 0
            ? "Services sharing a mount root can hardlink files instead of copying"
            : "This service doesn't share data directories with other media services");
        panel.appendChild(ctxLine);
    }

    // Conflict summary for this service
    const svcConflicts = (state.pipeline && state.pipeline.conflicts || []).filter(c =>
        (c.services || []).includes(svc.service_name) || c.service_name === svc.service_name
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
            const idx = findConflictForService(svc.service_name);
            if (idx !== null) scrollToConflict(idx);
        });
        actionRow.appendChild(viewIssue);
    }

    panel.appendChild(actionRow);

    // Insert after the row
    if (row) {
        row.after(panel);
        row.classList.add("expanded");
    }
    state.expandedService = svc.service_name;
}

function addDetailRow(panel, label, value) {
    const row = document.createElement("div");
    row.className = "detail-row";
    const lbl = document.createElement("span");
    lbl.className = "detail-label";
    lbl.textContent = label;
    const val = document.createElement("span");
    val.className = "detail-value";
    val.textContent = value;
    row.appendChild(lbl);
    row.appendChild(val);
    panel.appendChild(row);
}

// ─── Conflict Cards ───

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

    generateFixPlans(conflicts);
}

function renderConflictCard(conflict, index) {
    const card = document.createElement("div");
    card.className = "conflict-card conflict-" + (conflict.severity || "high") + " collapsed";
    card.setAttribute("data-conflict-index", index);

    // Header: severity/category badge + description + affected count + chevron
    const header = document.createElement("div");
    header.className = "conflict-card-header";

    const cat = (conflict.category || "").toUpperCase();
    const badge = document.createElement("span");
    if (cat === "C") {
        // Category C uses info badge instead of severity badge
        badge.className = "badge-info";
        badge.textContent = "INFO";
        badge.setAttribute("data-tooltip", "Infrastructure recommendation \u2014 not fixable via compose YAML");
    } else {
        badge.className = "conflict-severity severity-" + (conflict.severity || "high");
        badge.textContent = (conflict.severity || "HIGH").toUpperCase();
        badge.setAttribute("data-tooltip", _severityTooltip(conflict.severity));
    }
    header.appendChild(badge);

    const desc = document.createElement("span");
    desc.className = "conflict-card-desc";
    desc.textContent = conflict.description || conflict.type || "Mount conflict";
    header.appendChild(desc);

    const affectedServices = (conflict.services && conflict.services.length > 0)
        ? conflict.services
        : (conflict.service_name ? [conflict.service_name] : []);

    if (affectedServices.length > 0) {
        const count = document.createElement("span");
        count.className = "conflict-card-affected-count";
        count.textContent = affectedServices.length + " service" + (affectedServices.length !== 1 ? "s" : "");
        header.appendChild(count);
    }

    const chevron = document.createElement("span");
    chevron.className = "conflict-card-chevron";
    chevron.textContent = "\u25BC";
    header.appendChild(chevron);

    header.addEventListener("click", () => { card.classList.toggle("collapsed"); });
    card.appendChild(header);

    // Body (hidden when collapsed via CSS)
    const body = document.createElement("div");
    body.className = "conflict-card-body";

    if (affectedServices.length > 0) {
        const affected = document.createElement("div");
        affected.className = "conflict-affected";
        affected.textContent = "Affects: " + affectedServices.join(", ");
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

    // Handrail: plain-English explanation for the conflict type
    const handrail = CONFLICT_HANDRAILS[conflict.type];
    if (handrail) {
        const handrailEl = document.createElement("p");
        handrailEl.className = "conflict-handrail";
        handrailEl.textContent = handrail;
        body.appendChild(handrailEl);
    }

    // Fix plan container (populated async)
    const fixPlan = document.createElement("div");
    fixPlan.className = "fix-plan";
    fixPlan.id = "fix-plan-" + index;
    body.appendChild(fixPlan);

    // "See Full Analysis →" link
    const drillLink = document.createElement("span");
    drillLink.className = "conflict-drill-link";
    drillLink.textContent = "See Full Analysis \u2192";
    drillLink.setAttribute("data-tooltip", "Open detailed analysis with solution YAML, RPM wizard, and step-by-step guidance");
    drillLink.addEventListener("click", (e) => {
        e.stopPropagation();
        drillIntoConflict(conflict);
    });
    body.appendChild(drillLink);

    card.appendChild(body);
    return card;
}

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
    const svcName = (conflict.services || [])[0] || conflict.service_name || "";
    const svc = state.services.find(s => s.service_name === svcName);
    if (!svc) {
        showSimpleToast("Could not find service '" + svcName + "' in pipeline data", "error");
        return;
    }
    const stack = {
        path: svc.stack_path || "",
        compose_file: svc.compose_file || "docker-compose.yml",
        services: conflict.services && conflict.services.length > 0 ? conflict.services : [svcName],
    };
    hide("pipeline-dashboard");
    state.parsedError = state.pastedError;
    runAnalysis(stack);
}

function scrollToConflicts() {
    const container = document.getElementById("conflict-cards");
    if (container && container.firstChild) {
        container.firstChild.scrollIntoView({ behavior: "smooth", block: "start" });
    }
}

function scrollToConflict(index) {
    const card = document.querySelector("#conflict-cards .conflict-card:nth-child(" + (index + 1) + ")");
    if (card) card.scrollIntoView({ behavior: "smooth", block: "center" });
}

// ─── Fix Plans ───

async function generateFixPlans(conflicts) {
    // Prefer fix_plans from analysis response (multi-file aware)
    if (state.analysis && state.analysis.fix_plans && state.analysis.fix_plans.length > 0) {
        const plans = {};
        for (const plan of state.analysis.fix_plans) {
            plans[plan.compose_file_path] = {
                compose_file_path: plan.compose_file_path,
                original_corrected_yaml: plan.corrected_yaml,
                original_changed_lines: plan.changed_lines || [],
                stack_name: plan.compose_file_path.replace(/\\/g, "/").split("/").slice(-2, -1)[0] || "",
                changed_services: plan.changed_services || [],
                change_summary: plan.change_summary || "",
                category: plan.category || "A",
            };
        }
        state.fixPlans = plans;
        for (let i = 0; i < conflicts.length; i++) {
            renderFixPlan(i, plans);
        }
        return;
    }

    // Fallback: per-stack analysis approach
    const stackPaths = new Set();
    for (const conflict of conflicts) {
        for (const svc of state.services) {
            if ((conflict.services || []).includes(svc.service_name)) {
                stackPaths.add(svc.stack_path || "");
            }
        }
    }

    const plans = {};
    for (const stackPath of stackPaths) {
        if (!stackPath) continue;
        try {
            const resp = await fetch("/api/analyze", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ stack_path: stackPath }),
                signal: AbortSignal.timeout(15000),
            });
            const result = await resp.json();
            if (result && result.original_corrected_yaml) {
                plans[stackPath] = {
                    compose_file_path: result.compose_file_path || "",
                    original_corrected_yaml: result.original_corrected_yaml,
                    original_changed_lines: result.original_changed_lines || [],
                    stack_name: (stackPath.replace(/\\/g, "/").split("/").pop()) || "",
                };
            }
        } catch (e) {
            // Analysis failed for this stack — skip
        }
    }

    state.fixPlans = plans;

    // Render fix plans in each conflict card
    for (let i = 0; i < conflicts.length; i++) {
        const conflict = conflicts[i];
        const relevantPlans = {};
        for (const svc of state.services) {
            if ((conflict.services || []).includes(svc.service_name)) {
                const sp = svc.stack_path || "";
                if (plans[sp]) relevantPlans[sp] = plans[sp];
            }
        }
        renderFixPlan(i, relevantPlans);
    }
}

function renderFixPlan(conflictIndex, plans) {
    const container = document.getElementById("fix-plan-" + conflictIndex);
    if (!container) return;
    container.replaceChildren();

    const entries = Object.entries(plans);
    if (entries.length === 0) return;

    for (const [stackPath, plan] of entries) {
        const row = document.createElement("div");
        row.className = "fix-plan-row";
        row.setAttribute("data-stack-path", plan.compose_file_path || stackPath);

        const checkbox = document.createElement("span");
        checkbox.className = "fix-plan-check";
        checkbox.textContent = "\u2610"; // empty ballot box
        row.appendChild(checkbox);

        const label = document.createElement("span");
        label.className = "fix-plan-label";
        label.textContent = plan.stack_name + "/" + (plan.compose_file_path.replace(/\\/g, "/").split("/").pop() || "docker-compose.yml");
        row.appendChild(label);

        if (plan.change_summary) {
            const summary = document.createElement("span");
            summary.className = "fix-plan-summary";
            summary.textContent = plan.change_summary;
            row.appendChild(summary);
        }

        if (plan.original_changed_lines.length > 0) {
            const changes = document.createElement("span");
            changes.className = "fix-plan-changes";
            changes.textContent = plan.original_changed_lines.length + " lines";
            row.appendChild(changes);

            const applyBtn = document.createElement("button");
            applyBtn.className = "btn btn-primary btn-sm";
            applyBtn.textContent = "Apply";
            applyBtn.addEventListener("click", (e) => {
                e.stopPropagation();
                applySingleFix(plan);
            });
            row.appendChild(applyBtn);
        } else {
            const noChange = document.createElement("span");
            noChange.className = "fix-plan-no-change";
            noChange.textContent = "no change needed";
            row.appendChild(noChange);
        }

        // Click row to toggle preview
        row.addEventListener("click", () => toggleFixPreview(row, plan));

        container.appendChild(row);
    }

    // Apply All button
    const fixableCount = entries.filter(([_, p]) => p.original_changed_lines.length > 0).length;
    if (fixableCount > 1) {
        const applyAll = document.createElement("button");
        applyAll.className = "btn btn-primary fix-plan-apply-all";
        applyAll.textContent = fixableCount === 1 ? "Apply Fix" : "Apply All Fixes (" + fixableCount + " files)";
        applyAll.addEventListener("click", () => applyAllFixes(plans));
        container.appendChild(applyAll);
    }
}

function toggleFixPreview(row, plan) {
    const existing = row.nextElementSibling;
    if (existing && existing.classList.contains("fix-preview")) {
        existing.remove();
        return;
    }

    if (!plan.original_corrected_yaml) return;

    const preview = document.createElement("pre");
    preview.className = "fix-preview";

    // Show the corrected YAML with changed lines highlighted
    const lines = plan.original_corrected_yaml.split("\n");
    const changedSet = new Set(plan.original_changed_lines || []);
    for (let i = 0; i < lines.length; i++) {
        const span = document.createElement("span");
        if (changedSet.has(i + 1)) {
            span.className = "line-added";
        }
        span.textContent = lines[i] + "\n";
        preview.appendChild(span);
    }

    row.after(preview);
}

// ─── Apply Fixes ───

async function applySingleFix(plan) {
    try {
        const resp = await fetch("/api/apply-fixes", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                fixes: [{
                    compose_file_path: plan.compose_file_path,
                    corrected_yaml: plan.original_corrected_yaml,
                }],
            }),
        });
        const data = await resp.json();
        if (data.status === "applied") {
            markFixApplied(plan.compose_file_path);
            const fileName = plan.compose_file_path.replace(/\\/g, "/").split("/").pop() || "compose file";
            showSimpleToast("Fixed " + plan.stack_name + "/" + fileName, "success");
        } else {
            const errMsg = (data.errors && data.errors[0]) ? data.errors[0].error : "unknown error";
            showSimpleToast("Failed: " + errMsg, "error");
        }
    } catch (e) {
        showSimpleToast("Apply failed: " + e.message, "error");
    }
}

async function applyAllFixes(plans) {
    const fixes = Object.entries(plans)
        .filter(([_, p]) => p.original_changed_lines.length > 0)
        .map(([_, p]) => ({
            compose_file_path: p.compose_file_path,
            corrected_yaml: p.original_corrected_yaml,
        }));

    if (fixes.length === 0) return;

    try {
        const resp = await fetch("/api/apply-fixes", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ fixes }),
        });
        const data = await resp.json();

        if (data.status === "applied") {
            for (const r of data.results) {
                markFixApplied(r.compose_file_path);
            }
            showSimpleToast("All " + data.applied_count + " files fixed — rescanning...", "success");
            // Trigger pipeline rescan
            try {
                await fetch("/api/pipeline-scan", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({}),
                    signal: AbortSignal.timeout(30000),
                });
            } catch (e) {
                // Rescan failed silently — user can refresh
            }
            showRedeployPrompt(fixes);
        } else if (data.status === "partial") {
            for (const r of (data.results || [])) {
                if (r.status === "applied") markFixApplied(r.compose_file_path);
            }
            showSimpleToast(data.applied_count + " applied, " + data.failed_count + " failed", "warning");
        } else {
            const errMsg = (data.errors && data.errors[0]) ? data.errors[0].error : "unknown error";
            showSimpleToast("Fix failed: " + errMsg, "error");
        }
    } catch (e) {
        showSimpleToast("Apply failed: " + e.message, "error");
    }
}

function markFixApplied(composePath) {
    state.fixProgress[composePath] = "applied";
    // Update checkbox in fix plan
    const row = document.querySelector('[data-stack-path="' + composePath + '"]');
    if (row) {
        const check = row.querySelector(".fix-plan-check");
        if (check) check.textContent = "\u2611"; // checked ballot box
        row.classList.add("fix-applied");
        // Disable apply button
        const btn = row.querySelector(".btn");
        if (btn) { btn.disabled = true; btn.textContent = "Applied"; }
    }
    // Update service health dots
    updateHealthDotsAfterFix();
    updateHealthBannerAfterFix();
}

function updateHealthDotsAfterFix() {
    for (const svc of state.services) {
        const row = document.querySelector('[data-service="' + svc.service_name + '"]');
        if (!row) continue;
        const dot = row.querySelector(".health-dot");
        if (!dot) continue;
        dot.className = "health-dot " + getServiceHealth(svc);
    }
}

function updateHealthBannerAfterFix() {
    const conflicts = (state.pipeline && state.pipeline.conflicts) || [];
    // Count unfixed conflicts
    let unfixed = 0;
    for (const c of conflicts) {
        const allFixed = (c.services || []).every(svcName => {
            const svc = state.services.find(s => s.service_name === svcName);
            const composePath = svc ? (svc.compose_file || "") : "";
            return state.fixProgress[composePath] === "applied";
        });
        if (!allFixed) unfixed++;
    }

    const icon = document.getElementById("health-banner-icon");
    const text = document.getElementById("health-banner-text");
    if (!icon || !text) return;

    if (unfixed === 0 && conflicts.length > 0) {
        icon.className = "health-banner-icon health-ok";
        text.textContent = "All fixes applied \u2014 rescan to verify";
    }
}

// ─── Redeploy ───

const ROLE_WARNINGS = {
    "arr": "will stop monitoring and importing",
    "download_client": "active downloads will be interrupted",
    "media_server": "active streams will disconnect",
    "request": "request UI will be briefly unavailable",
    "other": "service will restart",
};

function showRedeployPrompt(appliedFixes) {
    const stacks = [];
    for (const fix of appliedFixes) {
        const svc = state.services.find(s =>
            fix.compose_file_path && fix.compose_file_path.includes(s.stack_name)
        );
        if (svc) {
            stacks.push({
                stack_path: svc.stack_path || "",
                stack_name: svc.stack_name || "",
                service_name: svc.service_name,
                role: svc.role || "other",
                warning: ROLE_WARNINGS[svc.role] || ROLE_WARNINGS.other,
            });
        }
    }

    if (stacks.length === 0) return;

    const container = document.getElementById("conflict-cards");
    if (!container) return;

    const prompt = document.createElement("div");
    prompt.className = "card redeploy-prompt";

    // Header
    const header = document.createElement("div");
    header.className = "step-header";
    const icon = document.createElement("span");
    icon.className = "step-number info-icon";
    icon.textContent = "\u21BB";
    header.appendChild(icon);
    const h2 = document.createElement("h2");
    h2.textContent = "Redeploy";
    header.appendChild(h2);
    prompt.appendChild(header);

    // Backup reminder
    const backup = document.createElement("p");
    backup.className = "step-desc";
    backup.textContent = "Backups saved alongside each file (.bak). To undo: rename .bak back to docker-compose.yml.";
    prompt.appendChild(backup);

    // Warnings per service
    const warnings = document.createElement("div");
    warnings.className = "redeploy-warnings";
    for (const s of stacks) {
        const line = document.createElement("div");
        line.className = "redeploy-warning-line";
        line.textContent = "\u2022 " + s.service_name + " \u2014 " + s.warning;
        warnings.appendChild(line);
    }
    prompt.appendChild(warnings);

    // Reassurance
    const reassure = document.createElement("p");
    reassure.className = "step-desc";
    reassure.textContent = "Services restart in seconds. No data is lost.";
    prompt.appendChild(reassure);

    // Action buttons
    const actions = document.createElement("div");
    actions.className = "redeploy-actions";

    const deployBtn = document.createElement("button");
    deployBtn.className = "btn btn-primary";
    deployBtn.textContent = "Redeploy " + stacks.length + " Service" + (stacks.length !== 1 ? "s" : "");
    deployBtn.addEventListener("click", () => doRedeploy(stacks, prompt));
    actions.appendChild(deployBtn);

    const manualBtn = document.createElement("button");
    manualBtn.className = "btn btn-secondary";
    manualBtn.textContent = "I'll do it myself";
    manualBtn.addEventListener("click", () => showManualRedeploy(stacks, prompt));
    actions.appendChild(manualBtn);

    prompt.appendChild(actions);
    container.appendChild(prompt);
    prompt.scrollIntoView({ behavior: "smooth" });
}

async function doRedeploy(stacks, promptEl) {
    const body = {
        stacks: stacks.map(s => ({ stack_path: s.stack_path, action: "up" })),
    };

    showSimpleToast("Redeploying...", "info");

    // Replace buttons with spinner
    const actions = promptEl.querySelector(".redeploy-actions");
    if (actions) {
        actions.replaceChildren();
        const spinner = document.createElement("span");
        spinner.textContent = "Redeploying...";
        spinner.className = "step-desc";
        actions.appendChild(spinner);
    }

    try {
        const resp = await fetch("/api/redeploy", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
            signal: AbortSignal.timeout(120000),
        });
        const data = await resp.json();

        if (data.status === "success") {
            showSimpleToast("All " + stacks.length + " services redeployed", "success");
            // Show result in prompt
            if (actions) {
                actions.replaceChildren();
                const result = document.createElement("p");
                result.className = "step-desc";
                result.textContent = "All services redeployed successfully. Rescanning pipeline...";
                actions.appendChild(result);
            }
            // Auto-rescan
            await runPipelineScan();
        } else if (data.status === "partial") {
            showSimpleToast(data.summary || "Some services failed", "warning");
            await runPipelineScan();
        } else {
            const firstErr = (data.results || []).find(r => r.status === "error");
            showSimpleToast("Redeploy failed: " + (firstErr ? firstErr.error : "unknown"), "error");
            showManualRedeploy(stacks, promptEl);
        }
    } catch (e) {
        showSimpleToast("Redeploy failed: " + e.message, "error");
        showManualRedeploy(stacks, promptEl);
    }
}

function showManualRedeploy(stacks, promptEl) {
    const actions = promptEl.querySelector(".redeploy-actions");
    if (!actions) return;
    actions.replaceChildren();

    const label = document.createElement("p");
    label.className = "step-desc";
    label.textContent = "Run these commands to restart your services:";
    actions.appendChild(label);

    const commands = document.createElement("div");
    commands.className = "manual-commands";

    const cmdText = stacks.map(s =>
        "cd " + s.stack_name + " && docker compose up -d"
    ).join("\n");
    commands.textContent = cmdText;

    const copyBtn = document.createElement("button");
    copyBtn.className = "copy-btn";
    copyBtn.textContent = "Copy";
    copyBtn.addEventListener("click", () => {
        navigator.clipboard.writeText(cmdText).then(() => {
            copyBtn.textContent = "Copied!";
            setTimeout(() => { copyBtn.textContent = "Copy"; }, 2000);
        });
    });
    commands.appendChild(copyBtn);

    actions.appendChild(commands);
}

// ─── Error Paste Bar ───

function enablePasteBar() {
    const input = document.getElementById("paste-error-input");
    const btn = document.getElementById("paste-error-go");
    if (!input || !btn) return;

    input.disabled = false;
    input.placeholder = "Paste an error from your *arr app \u2014 Sonarr, Radarr, Lidarr, etc.";

    // Enable button based on input content
    input.addEventListener("input", () => {
        const goBtn = document.getElementById("paste-error-go");
        if (goBtn) goBtn.disabled = !input.value.trim();
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
            const goBtn = document.getElementById("paste-error-go");
            if (goBtn) goBtn.disabled = false;
        });
    });
}

function getPasteExample(type) {
    const examples = {
        import: "Import failed, path does not exist or is not accessible by Sonarr: /data/tv/Show Name/Season 01/Episode.mkv",
        hardlink: "Couldn't create hardlink for /data/downloads/movie.mkv, copying instead",
        permission: "Permission denied: '/data/media/tv/show.mkv'",
        remote: "Remote path mapping required for Sonarr/Radarr to access download client files",
    };
    return examples[type] || "";
}

async function handlePasteError() {
    const input = document.getElementById("paste-error-input");
    if (!input) return;
    const text = input.value.trim();
    if (!text) return;

    // Parse the error via API
    try {
        const resp = await fetch("/api/parse-error", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ error_text: text }),
        });
        const parsed = await resp.json();

        if (!parsed || !parsed.service) {
            showPasteResult("Could not identify a service in this error.", "error");
            return;
        }

        state.pastedError = parsed;

        // Find matching service(s) in pipeline
        const matched = state.services.filter(s =>
            s.service_name.toLowerCase().includes(parsed.service.toLowerCase()) ||
            parsed.service.toLowerCase().includes(s.service_name.toLowerCase())
        );

        if (matched.length > 0) {
            // Highlight matched services
            highlightServices(matched.map(s => s.service_name));

            // Find relevant conflict
            const relevantIdx = findConflictForService(parsed.service);
            if (relevantIdx !== null) {
                scrollToConflict(relevantIdx);
                showPasteResult(parsed.service + " \u2014 " + (parsed.error_type || "mount conflict") + " detected");
            } else {
                showPasteResult(parsed.service + " \u2014 no conflicts found. Your setup looks correct.", "healthy");
            }
        } else {
            showPasteResult('Service "' + parsed.service + '" not found in your pipeline. Check the stacks directory.', "error");
        }
    } catch (e) {
        showPasteResult("Parse failed: " + e.message, "error");
    }
}

function findConflictForService(serviceName) {
    const conflicts = (state.pipeline && state.pipeline.conflicts) || [];
    for (let i = 0; i < conflicts.length; i++) {
        const c = conflicts[i];
        const services = c.services || [];
        const matchArray = services.some(s =>
            s.toLowerCase().includes(serviceName.toLowerCase()) ||
            serviceName.toLowerCase().includes(s.toLowerCase())
        );
        const matchName = c.service_name &&
            (c.service_name.toLowerCase().includes(serviceName.toLowerCase()) ||
             serviceName.toLowerCase().includes(c.service_name.toLowerCase()));
        if (matchArray || matchName) return i;
    }
    return null;
}

function highlightServices(serviceNames) {
    // Remove previous highlights
    document.querySelectorAll(".service-row.highlighted").forEach(el =>
        el.classList.remove("highlighted")
    );

    // Add highlights with animation
    for (const name of serviceNames) {
        const row = document.querySelector('[data-service="' + name + '"]');
        if (row) {
            row.classList.add("highlighted");
            row.scrollIntoView({ behavior: "smooth", block: "center" });
        }
    }

    state.highlightedServices = serviceNames;
}

function showPasteResult(message, type) {
    const el = document.getElementById("paste-bar-result");
    if (!el) return;
    el.textContent = message;
    el.className = "paste-bar-result" + (type ? " paste-" + type : "");
    el.classList.remove("hidden");
}

// ─── Directory Selection ───

function setupHeaderPath() {
    const btn = document.getElementById("header-path");
    const editor = document.getElementById("path-editor");
    const input = document.getElementById("header-path-input");
    const goBtn = document.getElementById("header-path-go");

    if (!btn || !editor || !input || !goBtn) return;

    btn.addEventListener("click", () => {
        editor.classList.toggle("hidden");
        if (!editor.classList.contains("hidden")) {
            input.value = state.stacksPath;
            input.focus();
            input.select();
        }
    });

    goBtn.addEventListener("click", async () => {
        const newPath = input.value.trim();
        if (newPath && newPath !== state.stacksPath) {
            await changeStacksPathTo(newPath);
        }
        editor.classList.add("hidden");
    });

    input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
            e.preventDefault();
            goBtn.click();
        } else if (e.key === "Escape") {
            editor.classList.add("hidden");
        }
    });

    // Browse button — opens server-side directory browser modal
    const browseBtn = document.getElementById("header-path-browse");
    if (browseBtn) {
        browseBtn.addEventListener("click", () => {
            openDirectoryBrowser(state.stacksPath || "");
        });
    }
}


// ─── Directory Browser Modal ───

/**
 * Open a server-powered directory browser modal.
 * Lists directories from the backend filesystem so the user can navigate
 * and pick a stacks directory without typing paths manually.
 */
function openDirectoryBrowser(startPath) {
    // Remove any existing browser
    const existing = document.getElementById("dir-browser-overlay");
    if (existing) existing.remove();

    // Create overlay
    const overlay = document.createElement("div");
    overlay.className = "dir-browser-overlay";
    overlay.id = "dir-browser-overlay";

    const browser = document.createElement("div");
    browser.className = "dir-browser";

    // Header: up button + current path
    const header = document.createElement("div");
    header.className = "dir-browser-header";

    const upBtn = document.createElement("button");
    upBtn.className = "dir-browser-up";
    upBtn.textContent = "\u2191 Up";
    upBtn.setAttribute("aria-label", "Go to parent directory");
    header.appendChild(upBtn);

    const pathDisplay = document.createElement("span");
    pathDisplay.className = "dir-browser-path";
    pathDisplay.textContent = startPath || "Loading...";
    header.appendChild(pathDisplay);

    browser.appendChild(header);

    // List container
    const list = document.createElement("div");
    list.className = "dir-browser-list";
    list.setAttribute("role", "listbox");
    list.setAttribute("aria-label", "Directory listing");
    browser.appendChild(list);

    // Footer: Cancel + Select
    const footer = document.createElement("div");
    footer.className = "dir-browser-footer";

    const cancelBtn = document.createElement("button");
    cancelBtn.className = "btn btn-ghost btn-sm";
    cancelBtn.textContent = "Cancel";
    cancelBtn.addEventListener("click", () => overlay.remove());

    const selectBtn = document.createElement("button");
    selectBtn.className = "btn btn-primary btn-sm";
    selectBtn.textContent = "Select This Directory";
    footer.appendChild(cancelBtn);
    footer.appendChild(selectBtn);
    browser.appendChild(footer);

    overlay.appendChild(browser);

    // Close on overlay click (not browser click)
    overlay.addEventListener("click", (e) => {
        if (e.target === overlay) overlay.remove();
    });

    // Close on Escape
    const onKeydown = (e) => {
        if (e.key === "Escape") {
            overlay.remove();
            document.removeEventListener("keydown", onKeydown);
        }
    };
    document.addEventListener("keydown", onKeydown);

    // Track current browsing path
    let currentPath = startPath;

    // Select button — use current path
    selectBtn.addEventListener("click", () => {
        if (currentPath) {
            const input = document.getElementById("header-path-input");
            const editor = document.getElementById("path-editor");
            if (input) input.value = currentPath;
            if (editor) editor.classList.remove("hidden");
            overlay.remove();
            // Auto-trigger scan
            changeStacksPathTo(currentPath);
        }
    });

    async function loadDirectory(path) {
        list.replaceChildren();
        const loading = document.createElement("div");
        loading.className = "dir-browser-loading";
        loading.textContent = "Loading...";
        list.appendChild(loading);

        try {
            const resp = await fetch("/api/list-directories", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ path: path }),
            });
            const data = await resp.json();

            if (data.error) {
                list.replaceChildren();
                const err = document.createElement("div");
                err.className = "dir-browser-empty";
                err.textContent = data.error;
                list.appendChild(err);
                return;
            }

            currentPath = data.path || path;
            pathDisplay.textContent = currentPath || "Drives";

            // Up button
            upBtn.disabled = data.parent === null || data.parent === undefined;
            upBtn.onclick = () => {
                if (data.parent !== null && data.parent !== undefined) {
                    loadDirectory(data.parent);
                }
            };

            list.replaceChildren();

            if (data.directories.length === 0) {
                const empty = document.createElement("div");
                empty.className = "dir-browser-empty";
                empty.textContent = "No subdirectories";
                list.appendChild(empty);
                return;
            }

            for (const dir of data.directories) {
                const item = document.createElement("button");
                item.className = "dir-browser-item" + (dir.locked ? " locked" : "");
                item.setAttribute("role", "option");

                const icon = document.createElement("span");
                icon.className = "folder-icon";
                icon.textContent = dir.locked ? "\uD83D\uDD12" : "\uD83D\uDCC1";

                const name = document.createElement("span");
                name.className = "folder-name";
                name.textContent = dir.name;

                item.appendChild(icon);
                item.appendChild(name);

                if (!dir.locked) {
                    item.addEventListener("click", () => loadDirectory(dir.path));
                }

                list.appendChild(item);
            }
        } catch (err) {
            list.replaceChildren();
            const errEl = document.createElement("div");
            errEl.className = "dir-browser-empty";
            errEl.textContent = "Failed to load directory";
            list.appendChild(errEl);
        }
    }

    document.body.appendChild(overlay);
    loadDirectory(startPath);
}

async function changeStacksPathTo(newPath) {
    try {
        const resp = await fetch("/api/change-stacks-path", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ path: newPath }),
        });
        const data = await resp.json();

        if (data.error) {
            showSimpleToast("Invalid path: " + data.error, "error");
            return;
        }

        state.stacksPath = newPath;
        state.pathConfigured = true;
        state.pipeline = null;
        state.services = [];
        state.servicesByRole = {};
        state.pastedError = null;
        state.fixProgress = {};
        state.fixPlans = {};
        state.expandedService = null;

        // Save as preferred path
        setPreferredPath(newPath);

        // Clear paste bar result
        const pasteResult = document.getElementById("paste-bar-result");
        if (pasteResult) pasteResult.classList.add("hidden");

        await runPipelineScan();
    } catch (e) {
        showSimpleToast("Path change failed: " + e.message, "error");
    }
}

// ─── Analysis Detail Cards (for service drill-down) ───

function hideAnalysisCards() {
    const cards = [
        "step-analyzing", "step-problem", "step-current-setup", "step-solution",
        "step-why", "step-next", "step-trash", "step-healthy",
        "step-analysis-error", "step-again",
    ];
    for (const id of cards) hide(id);
}

function backToDashboard() {
    hideAnalysisCards();
    show("pipeline-dashboard");
}

// ═══════════════════════════════════════════════════════════════
// END PIPELINE DASHBOARD
// ═══════════════════════════════════════════════════════════════

/**
 * Zero-stacks: user typed a custom path and hit Scan.
 */
async function bootScanCustomPath() {
    const input = document.getElementById("boot-path-input");
    if (!input) return;
    const path = input.value.trim();
    if (!path) { input.focus(); return; }

    const cpController = new AbortController();
    const cpTimeout = setTimeout(() => cpController.abort(), 10000);
    try {
        await fetch("/api/change-stacks-path", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ path }),
            signal: cpController.signal,
        });
        clearTimeout(cpTimeout);
        const dsController = new AbortController();
        const dsTimeout = setTimeout(() => dsController.abort(), 10000);
        const resp = await fetch("/api/discover-stacks", { signal: dsController.signal });
        clearTimeout(dsTimeout);
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
                const pController = new AbortController();
                const pTimeout = setTimeout(() => pController.abort(), 30000);
                try {
                    const pResp = await fetch("/api/pipeline-scan", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ scan_dir: path }),
                        signal: pController.signal,
                    });
                    clearTimeout(pTimeout);
                    if (pResp.ok) state.pipeline = await pResp.json();
                } catch { clearTimeout(pTimeout); }
                document.getElementById("boot-no-stacks").classList.add("hidden");
                state.bootComplete = false; // Allow transition
                transitionBootToFork(state.stacks.length);
            } else {
                input.style.borderColor = "var(--error)";
                setTimeout(() => { input.style.borderColor = ""; }, 2000);
            }
        }
    } catch {
        clearTimeout(cpTimeout);
        input.style.borderColor = "var(--error)";
        setTimeout(() => { input.style.borderColor = ""; }, 2000);
    }
}

// ─── Init ───

document.addEventListener("DOMContentLoaded", () => {
    checkHealth();
    initLogSystem(); // Connect SSE immediately so logs flow during boot

    // ─── Pipeline Dashboard wiring ───
    setupHeaderPath();

    // Header brand — back to dashboard
    const brandLink = document.getElementById("header-brand-link");
    if (brandLink) {
        brandLink.addEventListener("click", (e) => {
            e.preventDefault();
            if (state.pathConfigured) {
                backToDashboard();
            }
        });
    }

    // Boot — zero-stacks scan
    const bootScanBtn = document.getElementById("btn-boot-scan");
    if (bootScanBtn) bootScanBtn.addEventListener("click", () => bootScanCustomPath());

    // Enter in boot path input triggers scan
    const bootPathInput = document.getElementById("boot-path-input");
    if (bootPathInput) {
        bootPathInput.addEventListener("keydown", (e) => {
            if (e.key === "Enter") { e.preventDefault(); bootScanCustomPath(); }
        });
    }

    // Solution tabs and actions (carried forward for analysis detail cards)
    const tabRec = document.getElementById("tab-recommended");
    if (tabRec) tabRec.addEventListener("click", () => switchSolutionTab("recommended"));
    const tabOrig = document.getElementById("tab-original");
    if (tabOrig) tabOrig.addEventListener("click", () => switchSolutionTab("original"));
    const btnCopy = document.getElementById("btn-copy");
    if (btnCopy) btnCopy.addEventListener("click", () => copySolutionYaml());
    const btnCopyOrig = document.getElementById("btn-copy-original");
    if (btnCopyOrig) btnCopyOrig.addEventListener("click", () => copySolutionYaml());
    const btnApply = document.getElementById("btn-apply-fix");
    if (btnApply) btnApply.addEventListener("click", () => applyFix());
    const btnCancel = document.getElementById("btn-cancel-apply");
    if (btnCancel) btnCancel.addEventListener("click", () => cancelApplyFix());

    // Bottom actions
    const btnBack = document.getElementById("btn-back-to-dashboard");
    if (btnBack) btnBack.addEventListener("click", () => backToDashboard());
    const btnDiag = document.getElementById("btn-copy-diagnostic");
    if (btnDiag) btnDiag.addEventListener("click", () => copyDiagnosticSummary());
});

// ─── Mode Management (legacy — removed in pipeline dashboard pivot) ───
// Old mode-based functions (enterFixMode, enterBrowseMode, switchToFixMode,
// switchToBrowseMode, startOver) removed. Navigation now goes through
// backToDashboard() and the pipeline dashboard rendering functions.

// ─── Health Check ───

async function checkHealth() {
    const el = document.getElementById("health-status");
    let backendOnline = false;
    let discData = null;

    const hController = new AbortController();
    const hTimeout = setTimeout(() => hController.abort(), 10000);
    try {
        const resp = await fetch("/api/health", { signal: hController.signal });
        clearTimeout(hTimeout);
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
            const cpController = new AbortController();
            const cpTimeout = setTimeout(() => cpController.abort(), 10000);
            try { await fetch("/api/change-stacks-path", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ path: "" }),
                signal: cpController.signal,
            }); clearTimeout(cpTimeout); } catch { clearTimeout(cpTimeout); }
            // Fetch full stack summary for the header display
            const dsController = new AbortController();
            const dsTimeout = setTimeout(() => dsController.abort(), 10000);
            try {
                const discResp = await fetch("/api/discover-stacks", { signal: dsController.signal });
                clearTimeout(dsTimeout);
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
                clearTimeout(dsTimeout);
                el.textContent = "Connected";
            }
        } else {
            el.textContent = "Backend error";
            el.className = "header-status disconnected";
        }
    } catch {
        clearTimeout(hTimeout);
        el.textContent = "Offline";
        el.className = "header-status disconnected";
    }

    // Run boot sequence — surfaces discovery results before the mode fork
    runBootSequence(backendOnline, discData);
}

// ─── Parse Error (legacy — removed in pipeline dashboard pivot) ───
// Old functions (parseError, autoMatchStacks, renderFixMatchPills,
// showParseResult, makeParseField, showMultiErrorPicker, selectError)
// removed. Error paste now handled by handlePasteError() in the
// pipeline dashboard paste bar.

// Placeholder so doApplyFix's re-parse path doesn't crash
// ─── Service Name Constants ───
// Shared across pipeline dashboard role classification and paste bar detection.
// Must be declared before any code that references them.

const _ARR_APPS = ["sonarr", "radarr", "lidarr", "readarr", "whisparr", "prowlarr", "bazarr"];
const _DL_CLIENTS = ["qbittorrent", "sabnzbd", "nzbget", "transmission", "deluge", "rtorrent", "jdownloader"];
const _MEDIA_SERVERS = ["plex", "jellyfin", "emby"];

// All known media services — used by boot sequence and paste bar detection.
const _ALL_SERVICES = [
    ..._ARR_APPS, "overseerr", "jellyseerr",
    ..._DL_CLIENTS,
    ..._MEDIA_SERVERS,
];

// ─── Live Error Preview (legacy — removed) ───
// Old functions (updateLivePreview, fillExample) and the _ERROR_KEYWORDS
// constant removed. Paste bar in pipeline dashboard handles error detection.

// ─── Stack Selection (legacy — removed) ───
// Old functions (showStackSelection, showStackFilter, classifyStack,
// _stackListKeydownHandler, renderStacks, renderStackItem, formatRelativeTime)
// removed. Pipeline dashboard renders services directly via renderServiceGroups().

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
            searchInput.setAttribute("aria-label", "Search stacks");

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
            showAllBtn.addEventListener("click", () => backToDashboard());
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
    // Guard against double-submit: if an analysis is already in-flight,
    // ignore subsequent calls (e.g. rapid stack clicks, double-click).
    if (state._analysisInFlight) return;
    state._analysisInFlight = true;

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

    // Abort controller: cancel a stale request if the user navigates away
    // or if a new analysis starts. 60s timeout covers slow compose resolution
    // through socket proxies or large stacks.
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 60000);
    state._analysisAbort = controller;

    try {
        const resp = await fetch("/api/analyze", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                stack_path: stack.path,
                error: state.parsedError,
            }),
            signal: controller.signal,
        });
        clearTimeout(timeout);

        if (!resp.ok) {
            const err = await resp.json().catch(() => ({ error: "Analysis failed" }));
            addTerminalLine("fail", err.error || "Analysis request failed");
            setTerminalDots("error");
            showAnalysisError(err.error || "Analysis request failed");
            return;
        }

        const data = await resp.json();
        state.analysis = data;

        // Record when this stack was last analyzed — shown on stack cards
        state.lastAnalyzed[stack.path.replace(/\\/g, "/")] = Date.now();

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
    } catch (err) {
        if (err.name === "AbortError") {
            // Request was cancelled (timeout or user navigated away) — don't
            // show an error since the user has already moved on.
            setTerminalDots("warning");
            addTerminalLine("info", "Analysis cancelled.");
        } else {
            setTerminalDots("error");
            addTerminalLine("fail", "Could not reach the backend. Is MapArr running?");
            showAnalysisError("Could not reach the backend. Is MapArr running?");
        }
    } finally {
        state._analysisInFlight = false;
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
    renderObservations(data);
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
        const svcIcon = document.createElement("img");
        svcIcon.className = "service-icon";
        svcIcon.src = getServiceIconUrl(svc.name || svc.service_name);
        svcIcon.alt = "";
        svcIcon.width = 16;
        svcIcon.height = 16;
        svcIcon.loading = "lazy";
        nameCell.appendChild(svcIcon);
        nameCell.appendChild(document.createTextNode(" " + svc.name));
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

    // Non-media services section — always visible with icons and explanation
    const nonMediaServices = (data.services || []).filter(
        (s) => s.role === "other"
    );
    if (nonMediaServices.length > 0) {
        const otherSection = document.createElement("div");
        otherSection.className = "other-services-section visible";

        const heading = document.createElement("div");
        heading.className = "other-services-heading";
        heading.textContent = "Other Services (" + nonMediaServices.length + ")";
        otherSection.appendChild(heading);

        const note = document.createElement("p");
        note.className = "other-services-note";
        note.textContent =
            "These services are part of this stack but aren\u2019t involved in the media pipeline. " +
            "MapArr focuses on arr apps, download clients, and media servers \u2014 " +
            "so these won\u2019t appear in conflict analysis.";
        otherSection.appendChild(note);

        const list = document.createElement("div");
        list.className = "other-services-list";
        for (const svc of nonMediaServices) {
            const item = document.createElement("div");
            item.className = "other-service-item";
            const icon = document.createElement("img");
            icon.className = "service-icon";
            icon.src = getServiceIconUrl(svc.name || svc.service_name);
            icon.alt = "";
            icon.width = 20;
            icon.height = 20;
            icon.loading = "lazy";
            item.appendChild(icon);
            const name = document.createElement("span");
            name.className = "other-service-name";
            name.textContent = svc.name || svc.service_name;
            item.appendChild(name);
            list.appendChild(item);
        }
        otherSection.appendChild(list);
        details.appendChild(otherSection);
    }

    section.classList.remove("hidden");
    section.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

// ─── Problem ───

function showProblem(data) {
    const section = document.getElementById("step-problem");
    const details = document.getElementById("problem-details");
    details.replaceChildren();

    // Determine which categories are present
    const conflicts = data.conflicts || [];
    const categories = new Set(conflicts.map(c => (c.category || "").toUpperCase()));
    const hasCatA = categories.has("A");
    const hasCatB = categories.has("B");
    const hasCatC = categories.has("C");
    const catCOnly = hasCatC && !hasCatA && !hasCatB;

    // Category C only: change header to "Recommendation" with info-style badge
    const headerEl = section.querySelector(".step-header h2");
    const iconEl = section.querySelector(".step-number");
    if (catCOnly) {
        if (headerEl) headerEl.textContent = "Recommendation";
        if (iconEl) {
            iconEl.className = "step-number info-icon";
            iconEl.textContent = "i";
        }
    } else {
        // Reset to default for non-C-only cases
        if (headerEl) headerEl.textContent = "The Problem";
        if (iconEl) {
            iconEl.className = "step-number problem-icon";
            iconEl.textContent = "!";
        }
    }

    if (data.fix_summary) {
        const summary = document.createElement("p");
        summary.className = "step-desc";
        summary.textContent = data.fix_summary;
        details.appendChild(summary);
    }

    conflicts.forEach((conflict) => {
        const cat = (conflict.category || "").toUpperCase();
        const isCatC = cat === "C";

        const item = document.createElement("div");
        item.className = "conflict-item conflict-" + conflict.severity;

        // Category C uses info badge instead of severity badge
        const badge = document.createElement("span");
        if (isCatC) {
            badge.className = "badge-info";
            badge.textContent = "INFO";
        } else {
            badge.className = "conflict-severity severity-" + conflict.severity;
            badge.textContent = conflict.severity;
        }
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

        // Handrail: plain-English explanation below technical description
        const handrail = CONFLICT_HANDRAILS[conflict.type];
        if (handrail) {
            const handrailEl = document.createElement("p");
            handrailEl.className = "conflict-handrail";
            handrailEl.textContent = handrail;
            item.appendChild(handrailEl);
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
    section.querySelectorAll(".solution-tracks, .track-content-quick, .track-content-proper, .infra-warning, .cat-c-guidance, .env-solution-block, .solution-tab-env").forEach((el) => el.remove());

    // Determine which categories are present
    const conflicts = data.conflicts || [];
    const categories = new Set(conflicts.map(c => (c.category || "").toUpperCase()));
    const hasCatA = categories.has("A");
    const hasCatB = categories.has("B");
    const hasCatC = categories.has("C");
    const catCOnly = hasCatC && !hasCatA && !hasCatB;

    // Detect infrastructure-level conflicts that YAML changes alone cannot fix.
    const infraTypes = ["remote_filesystem", "mixed_mount_types", "wsl2_performance"];
    const infraConflicts = conflicts.filter((c) => infraTypes.includes(c.type));
    const hasOnlyInfra = infraConflicts.length > 0 && conflicts.every((c) => infraTypes.includes(c.type));

    // Types that are YAML-fixable but require manual follow-up actions afterward
    const postFixTypes = ["root_execution", "cross_stack_puid_mismatch"];
    const postFixConflicts = conflicts.filter((c) => postFixTypes.includes(c.type));

    // ────────────────────────────────────────────────────────────────────────
    // Category C only: replace solution section with "What You Can Do" card
    // ────────────────────────────────────────────────────────────────────────
    if (catCOnly) {
        // Update header to match the advisory tone
        const headerEl = section.querySelector(".step-header h2");
        const iconEl = section.querySelector(".step-number");
        if (headerEl) headerEl.textContent = "What You Can Do";
        if (iconEl) {
            iconEl.className = "step-number info-icon";
            iconEl.textContent = "i";
        }

        summaryEl.textContent = "This isn't something MapArr can fix in your compose file \u2014 it's about where your data lives.";

        // Hide the YAML tabs and blocks — not relevant for Cat C
        const solutionTabs = document.getElementById("solution-tabs");
        if (solutionTabs) solutionTabs.classList.add("hidden");
        if (recommendedBlock) recommendedBlock.classList.add("hidden");
        if (originalBlock) originalBlock.classList.add("hidden");

        // Render each Cat C conflict's fix text as a guidance card
        const guidanceWrap = document.createElement("div");
        guidanceWrap.className = "cat-c-guidance";

        for (const c of conflicts) {
            const card = document.createElement("div");
            card.className = "recommendation-card";

            const fixText = c.fix || c.detail || c.description || "";
            const p = document.createElement("p");
            p.className = "guidance-text";
            p.textContent = fixText;
            card.appendChild(p);

            guidanceWrap.appendChild(card);
        }

        summaryEl.after(guidanceWrap);
        section.classList.remove("hidden");
        return;
    }

    // ────────────────────────────────────────────────────────────────────────
    // Reset header for non-C-only cases (may have been changed by previous render)
    // ────────────────────────────────────────────────────────────────────────
    const headerEl = section.querySelector(".step-header h2");
    const iconEl = section.querySelector(".step-number");
    if (headerEl) headerEl.textContent = "The Solution";
    if (iconEl) {
        iconEl.className = "step-number ok";
        iconEl.textContent = "\u2713";
    }

    // Show YAML tabs again (may have been hidden by Cat C render)
    const solutionTabsEl = document.getElementById("solution-tabs");
    if (solutionTabsEl) solutionTabsEl.classList.remove("hidden");

    // ────────────────────────────────────────────────────────────────────────
    // Infrastructure warnings (Cat C mixed with A/B)
    // ────────────────────────────────────────────────────────────────────────
    if (infraConflicts.length > 0) {
        const warning = document.createElement("div");
        warning.className = "infra-warning callout callout-warning";

        const title = document.createElement("strong");
        title.textContent = hasOnlyInfra
            ? "This issue requires infrastructure changes \u2014 not just YAML edits"
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
        } else if (infraConflicts.some((c) => c.type === "wsl2_performance")) {
            explain.textContent =
                "Your media data is stored on Windows drives accessed through WSL2's 9P bridge (/mnt/c/, /mnt/d/). This works " +
                "but is significantly slower than native Linux storage for large media libraries. For best performance, store " +
                "media data on a native Linux partition or ext4 virtual disk within WSL2.";
        } else {
            explain.textContent =
                "Some of your services use different storage types (local vs remote). Hardlinks cannot cross filesystem " +
                "boundaries. The YAML fix below helps with path alignment, but full hardlink support requires all services " +
                "to share the same storage type.";
        }
        warning.appendChild(explain);

        summaryEl.after(warning);
    }

    // Show post-fix action notes for conflicts that need manual follow-up
    if (postFixConflicts.length > 0 && infraConflicts.length === 0) {
        const note = document.createElement("div");
        note.className = "infra-warning callout callout-info";

        const title = document.createElement("strong");
        title.textContent = "After applying the YAML fix, you'll need to take additional steps:";
        note.appendChild(title);

        const list = document.createElement("ul");
        list.style.cssText = "margin: 0.4rem 0 0; padding-left: 1.2rem; font-size: 0.85rem;";

        if (postFixConflicts.some((c) => c.type === "root_execution")) {
            const li = document.createElement("li");
            li.textContent = "After changing PUID/PGID from root, fix ownership of existing files: chown -R <PUID>:<PGID> /path/to/data";
            list.appendChild(li);
        }
        if (postFixConflicts.some((c) => c.type === "cross_stack_puid_mismatch")) {
            const li = document.createElement("li");
            li.textContent = "This fix only applies to the current stack. Other stacks sharing these paths also need their PUID/PGID updated to match.";
            list.appendChild(li);
        }

        note.appendChild(list);
        summaryEl.after(note);
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

    // ────────────────────────────────────────────────────────────────────────
    // "Fix Permissions" tab — shown when Cat B AND env_solution_yaml exists
    // ────────────────────────────────────────────────────────────────────────
    const envTab = document.querySelector('.solution-tab-env');
    if (envTab) envTab.remove();
    const envBlock = document.querySelector('.env-solution-block');
    if (envBlock) envBlock.remove();

    if (hasCatB && data.env_solution_yaml) {
        // Add "Fix Permissions" tab button
        const tabBar = document.getElementById("solution-tabs");
        if (tabBar) {
            const envTabBtn = document.createElement("button");
            envTabBtn.className = "solution-tab solution-tab-env";
            envTabBtn.dataset.tab = "env";
            envTabBtn.textContent = "Fix Permissions";
            // Insert before the "Your Config" tab if it exists, otherwise at end
            const origTabBtn = tabBar.querySelector('[data-tab="original"]');
            if (origTabBtn) {
                tabBar.insertBefore(envTabBtn, origTabBtn);
            } else {
                tabBar.appendChild(envTabBtn);
            }

            envTabBtn.addEventListener("click", () => switchSolutionTab("env"));
        }

        // Add env solution code block
        const envSolutionBlock = document.createElement("div");
        envSolutionBlock.className = "code-block hidden env-solution-block";
        envSolutionBlock.id = "solution-block-env";

        const envIntro = document.createElement("p");
        envIntro.className = "env-solution-intro";
        // Check if this is a cross-stack issue where current stack is already correct
        const isCrossStack = (data.conflicts || []).some(
            (c) => c.type === "cross_stack_puid_mismatch"
        );
        const hasLocalChanges = data.original_corrected_yaml && data.env_solution_changed_lines && data.env_solution_changed_lines.length > 0;
        if (isCrossStack && !hasLocalChanges) {
            envIntro.textContent = "This stack's permissions are already correct. Other services in your pipeline use different PUID/PGID values \u2014 update them to match. Copy the target values below.";
        } else {
            envIntro.textContent = "These environment variable changes align your services to the same user identity.";
        }
        envSolutionBlock.appendChild(envIntro);

        const envPre = document.createElement("pre");
        envPre.id = "solution-yaml-env";
        renderYamlWithHighlights(envPre, data.env_solution_yaml, data.env_solution_changed_lines || []);
        envSolutionBlock.appendChild(envPre);

        const envActions = document.createElement("div");
        envActions.className = "solution-actions";

        const envCopyBtn = document.createElement("button");
        envCopyBtn.className = "copy-btn";
        envCopyBtn.textContent = "Copy to Clipboard";
        envCopyBtn.addEventListener("click", () => {
            navigator.clipboard.writeText(data.env_solution_yaml).then(() => {
                envCopyBtn.textContent = "Copied!";
                setTimeout(() => { envCopyBtn.textContent = "Copy to Clipboard"; }, 2000);
            });
        });
        envActions.appendChild(envCopyBtn);

        // Apply Fix button — multi-file (fix_plans) or single-file (original_corrected_yaml)
        const envFixPlans = (data.fix_plans || []).filter((p) => p.category === "B" || p.category === "A+B");
        if (envFixPlans.length > 0) {
            const envApplyBtn = document.createElement("button");
            envApplyBtn.className = "apply-btn";
            envApplyBtn.id = "btn-apply-env-fix";
            envApplyBtn.textContent = envFixPlans.length === 1 ? "Apply Fix" : "Apply All Fixes (" + envFixPlans.length + " files)";
            envApplyBtn.addEventListener("click", () => applyAllFixes(envFixPlans));
            envActions.appendChild(envApplyBtn);
        } else if (data.original_corrected_yaml && data.compose_file_path) {
            const envApplyBtn = document.createElement("button");
            envApplyBtn.className = "apply-btn";
            envApplyBtn.id = "btn-apply-env-fix";
            envApplyBtn.textContent = "Apply Fix";
            envApplyBtn.addEventListener("click", () => applyFix());
            envActions.appendChild(envApplyBtn);
        }

        envSolutionBlock.appendChild(envActions);

        // Insert after originalBlock (or recommendedBlock if originalBlock missing)
        const insertAfter = originalBlock || recommendedBlock;
        if (insertAfter) {
            insertAfter.after(envSolutionBlock);
        } else {
            section.appendChild(envSolutionBlock);
        }
    }

    // ────────────────────────────────────────────────────────────────────────
    // Tab visibility gates based on category
    // ────────────────────────────────────────────────────────────────────────
    const recTab = document.getElementById("tab-recommended");

    // "Recommended Fix" tab: only show when Cat A AND solution_yaml exists
    if (!hasCatA || !data.solution_yaml) {
        if (recTab) recTab.classList.add("hidden");
        if (recommendedBlock) recommendedBlock.classList.add("hidden");
    } else {
        if (recTab) recTab.classList.remove("hidden");
        if (recommendedBlock) recommendedBlock.classList.remove("hidden");
    }

    // "Your Config (Corrected)" tab: show when original_corrected_yaml exists
    if (data.original_corrected_yaml && originalTab && originalYamlEl) {
        originalTab.classList.remove("hidden");
    } else if (originalTab) {
        originalTab.classList.add("hidden");
    }

    // Determine default active tab
    let defaultTab = "recommended";
    if (!hasCatA || !data.solution_yaml) {
        if (hasCatB && data.env_solution_yaml) {
            defaultTab = "env";
        } else if (data.original_corrected_yaml) {
            defaultTab = "original";
        }
    }

    // ────────────────────────────────────────────────────────────────────────
    // RPM Wizard — only show when Cat A AND RPM mappings exist with possible: true
    // ────────────────────────────────────────────────────────────────────────
    const rpmMappings = data.rpm_mappings || [];
    const hasPossibleRpm = rpmMappings.some((m) => m.possible);
    const hasRpmHint = (data.conflicts || []).some((c) => c.rpm_hint);

    if (hasCatA && (hasPossibleRpm || hasRpmHint)) {
        // Show track selector: Quick Fix (RPM) vs Proper Fix (YAML restructure)
        summaryEl.textContent = "Two fix approaches available \u2014 Quick Fix keeps your current mounts and bridges the gaps with Remote Path Mappings. Proper Fix restructures your volumes to eliminate the problem permanently.";

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
        const solutionTabs2 = document.getElementById("solution-tabs");
        if (solutionTabs2) properContent.appendChild(solutionTabs2);
        if (recommendedBlock) properContent.appendChild(recommendedBlock);
        if (originalBlock) properContent.appendChild(originalBlock);
        // Move env solution block into Proper Fix track too
        const envSolBlock = document.querySelector(".env-solution-block");
        if (envSolBlock) properContent.appendChild(envSolBlock);
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
        switchSolutionTab(defaultTab);
    } else {
        // No RPM available — show standard solution tabs
        if (hasCatB && !hasCatA && data.env_solution_yaml) {
            summaryEl.textContent =
                "The Fix Permissions tab shows the environment variable changes needed to align your services. " +
                "Switch to Your Config (Corrected) to see your full docker-compose.yml with the fixes applied.";
        } else if (hasCatA) {
            summaryEl.textContent =
                "The Recommended Fix tab shows the ideal volume mount snippet for your services. " +
                "Switch to Your Config (Corrected) to see your full docker-compose.yml with the fixes applied \u2014 that's the version you can apply directly to your stack.";
        } else {
            summaryEl.textContent =
                "Review the suggested changes below and apply them to your compose configuration.";
        }

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

        switchSolutionTab(defaultTab);
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

// ─── Observations (Category D — informational, collapsed by default) ───

function renderObservations(data) {
    const observations = data.observations;
    const container = document.getElementById("observations-container");
    if (!container) return;
    container.replaceChildren();

    if (!observations || observations.length === 0) return;

    // Build the <details> element with DOM APIs (textContent for XSS safety)
    const details = document.createElement("details");
    details.className = "observations-section";

    const summary = document.createElement("summary");
    summary.className = "observations-summary";
    summary.textContent = "A few other things we noticed (" + observations.length + ")";
    details.appendChild(summary);

    const list = document.createElement("ul");
    list.className = "observations-list";
    for (const obs of observations) {
        const li = document.createElement("li");
        li.textContent = obs.message;
        list.appendChild(li);
    }
    details.appendChild(list);

    const footer = document.createElement("p");
    footer.className = "observations-footer";
    footer.textContent = "For full compose hygiene analysis, check out ";
    const link = document.createElement("a");
    link.href = "https://github.com/coaxk/composearr";
    link.target = "_blank";
    link.rel = "noopener";
    link.textContent = "ComposeArr";
    footer.appendChild(link);
    details.appendChild(footer);

    container.appendChild(details);
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
    renderObservations(data);
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
    renderObservations(data);
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
    renderObservations(data);
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
            const afController = new AbortController();
            const afTimeout = setTimeout(() => afController.abort(), 30000);
            try {
                const resp = await fetch("/api/apply-fix", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        compose_file_path: data.compose_file_path,
                        corrected_yaml: data.original_corrected_yaml,
                    }),
                    signal: afController.signal,
                });
                clearTimeout(afTimeout);
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
                clearTimeout(afTimeout);
                resultDiv.className = "apply-result apply-result-error";
                resultDiv.textContent = err.name === "AbortError"
                    ? "Error: request timed out — is the backend responding?"
                    : "Error: " + (err?.message || "could not reach backend");
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
    // Abort any in-flight analysis request — prevents stale responses from
    // rendering after the user has moved on to a different stack or mode.
    if (state._analysisAbort) {
        state._analysisAbort.abort();
        state._analysisAbort = null;
    }

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

    // Clear observations (Category D) from previous analysis
    const obsContainer = document.getElementById("observations-container");
    if (obsContainer) obsContainer.replaceChildren();

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
        searchInput.setAttribute("aria-label", "Search stacks");

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
        backBtn.addEventListener("click", () => backToDashboard());
        buttons.appendChild(backBtn);
    }

    if (state.mode !== "browse" || state.stacks.length <= 1) {
        const analyzeBtn = document.createElement("button");
        analyzeBtn.className = "btn btn-primary";
        analyzeBtn.textContent = "Analyze Another Stack";
        analyzeBtn.addEventListener("click", () => backToDashboard());
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
    startOverBtn.addEventListener("click", () => backToDashboard());
    buttons.appendChild(startOverBtn);

    wrapper.appendChild(buttons);
    container.appendChild(wrapper);
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
    const envBlock = document.getElementById("solution-block-env");

    // Hide all blocks first
    if (recBlock) recBlock.classList.add("hidden");
    if (origBlock) origBlock.classList.add("hidden");
    if (envBlock) envBlock.classList.add("hidden");

    // Show the selected block
    if (tab === "recommended" && recBlock) {
        recBlock.classList.remove("hidden");
    } else if (tab === "original" && origBlock) {
        origBlock.classList.remove("hidden");
    } else if (tab === "env" && envBlock) {
        envBlock.classList.remove("hidden");
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

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 30000);
    try {
        const resp = await fetch("/api/apply-fix", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                compose_file_path: _lastAnalysisForApply.compose_file_path,
                corrected_yaml: _lastAnalysisForApply.original_corrected_yaml,
            }),
            signal: controller.signal,
        });
        clearTimeout(timeout);
        const data = await resp.json();

        if (resp.ok && data.status === "applied") {
            resultEl.className = "apply-result apply-result-success";
            resultEl.textContent = (data.message || "Fix applied.") + " Your compose file has been updated but your stack has NOT been restarted. Run 'docker compose up -d' in your stack directory (or restart via your Docker manager) to apply the changes.";
            resultEl.classList.remove("hidden");
            showSimpleToast("Fix applied successfully!", "success");

            // Update all apply buttons (Cat A + Cat B tabs)
            for (const id of ["btn-apply-fix", "btn-apply-env-fix"]) {
                const btn = document.getElementById(id);
                if (btn) {
                    btn.textContent = "Applied";
                    btn.disabled = true;
                    btn.classList.add("applied");
                }
            }

            // Show "Analyze Another Stack" button for quick navigation
            const nextBtn = document.createElement("button");
            nextBtn.className = "btn btn-ghost btn-analyze-another";
            nextBtn.textContent = "Analyze Another Stack";
            nextBtn.style.marginTop = "0.75rem";
            nextBtn.addEventListener("click", () => {
                backToDashboard();
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
        clearTimeout(timeout);
        console.error("Apply fix error:", err, err?.message, err?.stack);
        if (resultEl) {
            resultEl.className = "apply-result apply-result-error";
            resultEl.textContent = err.name === "AbortError"
                ? "Error: request timed out — is the backend responding?"
                : "Error: " + (err?.message || "could not reach backend");
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

        const discController = new AbortController();
        const discTimeout = setTimeout(() => discController.abort(), 10000);
        const discResp = await fetch("/api/discover-stacks", { signal: discController.signal });
        clearTimeout(discTimeout);
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
        const pController = new AbortController();
        const pTimeout = setTimeout(() => pController.abort(), 30000);
        const pResp = await fetch("/api/pipeline-scan", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ scan_dir: scanPath }),
            signal: pController.signal,
        });
        clearTimeout(pTimeout);
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

function _healthDotTooltip(health) {
    const tips = {
        healthy: "No issues detected \u2014 mount paths are consistent",
        "health-caution": "Internally OK but misaligned with your broader pipeline",
        issue: "This service has a configuration concern \u2014 click to see details",
        problem: "This service has broken mount paths \u2014 hardlinks will not work",
        awaiting: "Fix has been applied \u2014 restart the container to take effect",
        "health-unknown": "Scanning or not applicable",
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

    // Only upgrade ok → something worse. Don't touch warning/problem/unknown.
    if (base !== "ok") return base;

    // If this stack was analyzed or fixed this session, trust the deep result.
    // Deep per-stack analysis is more authoritative than the broad pipeline scan.
    const stackName = extractDirName(stack.path);
    if (state.verifiedStacks.has(stackName)) return base;

    // Check if pipeline has conflicts mentioning this stack
    const p = state.pipeline;
    if (!p || !p.conflicts || p.conflicts.length === 0) return base;

    // Category-aware: Cat A = problem (red), Cat B = warning (yellow)
    let worstCategory = null;
    for (const c of p.conflicts) {
        if (c.stack_name === stackName) {
            const cat = (c.category || "").toUpperCase();
            if (cat === "A") { worstCategory = "A"; break; }
            if (cat === "B" && worstCategory !== "A") worstCategory = "B";
        }
    }

    if (worstCategory === "A") return "problem";
    if (worstCategory === "B") return "warning";
    return base;
}

function _healthTooltip(health, hint) {
    const criteria = {
        ok: "GREEN: All media services share a common host mount path. Hardlinks and atomic moves should work.",
        caution: "BLINKING YELLOW: This stack is internally healthy, but its mount paths differ from the rest of your pipeline. Click to see details.",
        warning: "YELLOW: Permission mismatch or incomplete setup detected. Click to run full analysis.",
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
            removeBtn.setAttribute("aria-label", "Remove from saved locations");
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
    input.setAttribute("aria-label", "Manual stacks directory path");
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
    scanBtn.setAttribute("aria-label", "Scan custom directory");
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

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    try {
        const resp = await fetch("/api/change-stacks-path", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ path: newPath }),
            signal: controller.signal,
        });
        clearTimeout(timeout);

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
            const pController = new AbortController();
            const pTimeout = setTimeout(() => pController.abort(), 30000);
            try {
                const pResp = await fetch("/api/pipeline-scan", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ scan_dir: newPath }),
                    signal: pController.signal,
                });
                clearTimeout(pTimeout);
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
                clearTimeout(pTimeout);
                console.warn("Pipeline re-scan failed:", e);
            }
        }
    } catch {
        clearTimeout(timeout);
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

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    try {
        await fetch("/api/change-stacks-path", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ path: "" }),
            signal: controller.signal,
        });
        clearTimeout(timeout);
    } catch {
        clearTimeout(timeout);
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

    // ── Full pipeline context ──
    const pipe = state.pipeline || data.pipeline;
    if (pipe) {
        lines.push("");
        lines.push("### Pipeline Overview");
        lines.push("");
        lines.push("| Field | Value |");
        lines.push("|-------|-------|");
        if (pipe.scan_dir) lines.push("| Scan directory | `" + pipe.scan_dir + "` |");
        if (pipe.stacks_scanned != null) lines.push("| Stacks scanned | " + pipe.stacks_scanned + " |");
        const mediaCount = pipe.media_service_count || pipe.total_media || 0;
        lines.push("| Media services | " + mediaCount + " |");
        lines.push("| Health | " + pipe.health + " |");
        lines.push("| Shared mount | " + (pipe.shared_mount ? "Yes" + (pipe.mount_root ? " (`" + pipe.mount_root + "`)" : "") : "No") + " |");
        lines.push("");

        // Role breakdown table — all media services grouped by role
        const svcByRole = pipe.services_by_role;
        if (svcByRole && Object.keys(svcByRole).length > 0) {
            lines.push("### Pipeline \u2014 Media Services");
            lines.push("");
            lines.push("| Service | Stack | Role | Host Mounts |");
            lines.push("|---------|-------|------|-------------|");
            for (const [role, svcs] of Object.entries(svcByRole)) {
                (svcs || []).forEach((s) => {
                    const mounts = (s.host_sources || s.volume_mounts || [])
                        .map((m) => typeof m === "string" ? "`" + m + "`" : "`" + (m.source || m) + "`")
                        .join(", ") || "(none)";
                    lines.push("| " + (s.service_name || s.name || "?") + " | " + (s.stack_name || "?") + " | " + role + " | " + mounts + " |");
                });
            }
            lines.push("");
        }

        // Roles present and missing
        if (pipe.roles_present && pipe.roles_present.length > 0) {
            lines.push("**Roles present:** " + pipe.roles_present.join(", "));
        }
        if (pipe.roles_missing && pipe.roles_missing.length > 0) {
            lines.push("**Missing roles:** " + pipe.roles_missing.join(", "));
        }
        if ((pipe.roles_present && pipe.roles_present.length > 0) ||
            (pipe.roles_missing && pipe.roles_missing.length > 0)) {
            lines.push("");
        }

        // Pipeline-level conflicts
        if (pipe.conflicts && pipe.conflicts.length > 0) {
            lines.push("### Pipeline Conflicts");
            lines.push("");
            pipe.conflicts.forEach((c) => {
                const sev = (c.severity || "unknown").toUpperCase();
                lines.push("- **" + sev + ":** " + (c.description || c.message || JSON.stringify(c)));
            });
            lines.push("");
        }
    }

    // Cross-stack sibling context from analysis data
    const crossStack = (data.cross_stack || (data.pipeline_context && data.pipeline_context.cross_stack));
    if (crossStack && crossStack.siblings && crossStack.siblings.length > 0) {
        lines.push("");
        lines.push("### Cross-Stack Siblings");
        lines.push("");
        crossStack.siblings.forEach((sib) => {
            const sibMounts = (sib.host_sources || sib.volume_mounts || [])
                .map((m) => typeof m === "string" ? "`" + m + "`" : "`" + (m.source || m) + "`")
                .join(", ") || "(none)";
            lines.push("- **" + (sib.service_name || sib.name || "?") + "** (" + (sib.stack_name || "?") + ", " + (sib.role || "?") + ") \u2014 " + sibMounts);
        });
        lines.push("");
    }

    // Pipeline role for this stack
    if (data.pipeline_role) {
        lines.push("**This stack\u2019s pipeline role:** " + data.pipeline_role);
        lines.push("");
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

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
        const resp = await fetch("https://api.github.com/repos/" + REPO + "/releases/latest", { signal: controller.signal });
        clearTimeout(timeout);
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
        clearTimeout(timeout);
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

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
        const resp = await fetch("https://api.github.com/repos/" + REPO, { signal: controller.signal });
        clearTimeout(timeout);
        if (!resp.ok) {
            try { sessionStorage.setItem(CACHE_KEY, JSON.stringify({ checked: Date.now(), stars: null })); } catch {}
            return;
        }
        const data = await resp.json();
        const stars = data.stargazers_count || 0;
        try { sessionStorage.setItem(CACHE_KEY, JSON.stringify({ checked: Date.now(), stars })); } catch {}
        showStarsBadge(stars);
    } catch {
        clearTimeout(timeout);
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

// Clean up SSE connection on page unload to prevent server-side connection leak.
// Without this, navigating away or closing the tab leaves the EventSource open
// and the backend keeps the SSE generator alive until it times out.
window.addEventListener("beforeunload", () => {
    if (_logState.sseSource) {
        _logState.sseSource.close();
        _logState.sseSource = null;
    }
});

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
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    try {
        const resp = await fetch("/api/logs?limit=200", { signal: controller.signal });
        clearTimeout(timeout);
        if (!resp.ok) return;
        const data = await resp.json();
        _logState.entries = (data.entries || []).reverse(); // API returns newest first, we want oldest first
        if (_logState.entries.length > 0) {
            _logState.lastFetchTs = _logState.entries[_logState.entries.length - 1].ts;
        }
        renderLogEntries();
    } catch {
        clearTimeout(timeout);
        // Backend not ready yet — will get entries via SSE
    }
}

// ─── SSE Live Stream ───

// Exponential backoff for SSE reconnection: 5s → 10s → 20s → 40s → 60s cap.
// Resets to 5s on successful connection. Prevents hammering a down backend.
let _sseReconnectDelay = 5000;
const _SSE_MAX_DELAY = 60000;

function connectLogStream() {
    if (_logState.sseSource) {
        _logState.sseSource.close();
    }

    try {
        const es = new EventSource("/api/logs/stream");
        _logState.sseSource = es;

        es.addEventListener("connected", () => {
            // Connected successfully — reset backoff
            _sseReconnectDelay = 5000;
            // Backfill any entries we missed during the disconnection gap.
            _backfillMissedLogs();
        });

        es.addEventListener("log", (event) => {
            try {
                const entry = JSON.parse(event.data);
                addLogEntry(entry);
            } catch {}
        });

        es.addEventListener("error", () => {
            es.close();
            _logState.sseSource = null;
            // Exponential backoff with cap
            setTimeout(connectLogStream, _sseReconnectDelay);
            _sseReconnectDelay = Math.min(_sseReconnectDelay * 2, _SSE_MAX_DELAY);
        });
    } catch {
        // SSE not supported — fall back to polling (once only)
        if (!_logState._pollingFallback) {
            _logState._pollingFallback = true;
            setInterval(fetchLogs, 10000);
        }
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
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    try {
        const resp = await fetch("/api/logs?limit=200", { signal: controller.signal });
        clearTimeout(timeout);
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
        clearTimeout(timeout);
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

    // Live progress: update terminal title during analysis with step info.
    // Backend logs "Step X/6: ..." — we surface this as inline progress so
    // users see activity even without opening the log panel.
    if (state._analysisInFlight && entry.message) {
        const stepMatch = entry.message.match(/^Step (\d+)\/(\d+):\s*(.+)/);
        if (stepMatch) {
            const termTitle = document.querySelector(".terminal-title");
            if (termTitle) {
                termTitle.textContent = "Step " + stepMatch[1] + "/" + stepMatch[2] + ": " + stepMatch[3];
            }
        }
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
    copyBtn.setAttribute("aria-label", "Copy log entry to clipboard");
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
