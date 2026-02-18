/**
 * MapArr v1.0 — Frontend Application
 *
 * Plain JavaScript. No framework, no build step.
 * Three flows: parse error → discover stacks → select stack.
 *
 * XSS safety: All user-derived content uses textContent, never innerHTML.
 * Container clearing uses replaceChildren() instead of innerHTML.
 */

"use strict";

// ─── State ───

const state = {
    parsedError: null,   // Result from /api/parse-error
    stacks: [],          // Result from /api/discover-stacks
    selectedStack: null, // User's chosen stack path
};

// ─── Init ───

document.addEventListener("DOMContentLoaded", () => {
    checkHealth();

    // Ctrl+Enter in textarea triggers parse
    const textarea = document.getElementById("error-input");
    textarea.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
            e.preventDefault();
            parseError();
        }
    });
});

// ─── Health Check ───

async function checkHealth() {
    const el = document.getElementById("health-status");
    try {
        const resp = await fetch("/api/health");
        if (resp.ok) {
            const data = await resp.json();
            el.textContent = "Connected · v" + (data.version || "1.0.0");
            el.className = "header-status connected";
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
        showStackSelection();
    } catch (err) {
        alert("Could not reach the backend. Is MapArr running?");
    } finally {
        btn.disabled = false;
        btn.textContent = "Analyze Error";
    }
}

// ─── Skip to Stacks ───

function skipToStacks() {
    state.parsedError = null;
    document.getElementById("step-parse-result").classList.add("hidden");
    showStackSelection();
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
        if (!resp.ok) {
            throw new Error("Discovery failed");
        }

        const data = await resp.json();
        state.stacks = data.stacks || [];

        loading.classList.add("hidden");

        if (state.stacks.length === 0) {
            empty.classList.remove("hidden");
        } else {
            renderStacks(state.stacks);
            list.classList.remove("hidden");
        }

        if (data.search_note) {
            note.textContent = data.search_note;
        }

        // Update connection status with stack count
        const healthEl = document.getElementById("health-status");
        if (healthEl.classList.contains("connected") && state.stacks.length > 0) {
            healthEl.textContent = "Connected · " + state.stacks.length + " stacks";
        }
    } catch (err) {
        loading.classList.add("hidden");
        empty.classList.remove("hidden");
        const emptyP = empty.querySelector("p");
        if (emptyP) {
            emptyP.textContent = "Could not scan for stacks. Is the backend running?";
        }
    }
}

// ─── Render Stacks ───

// Known service names for classification (must match backend)
const _ARR_APPS = ["sonarr", "radarr", "lidarr", "readarr", "whisparr", "prowlarr", "bazarr"];
const _DL_CLIENTS = ["qbittorrent", "sabnzbd", "nzbget", "transmission", "deluge", "rtorrent", "jdownloader"];
const _MEDIA_SERVERS = ["plex", "jellyfin", "emby"];

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

    // Sort within each group alphabetically
    Object.values(groups).forEach((g) =>
        g.sort((a, b) => extractDirName(a.path).localeCompare(extractDirName(b.path)))
    );

    // Total count
    const total = document.createElement("div");
    total.className = "stacks-total";
    total.textContent = stacks.length + " stack" + (stacks.length !== 1 ? "s" : "") + " detected";
    list.appendChild(total);

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

    const source = document.createElement("span");
    source.className = "stack-source";
    source.textContent = stack.source;
    meta.appendChild(source);

    item.appendChild(meta);
    return item;
}

// ─── Select Stack → Analyze ───

async function selectStack(stack, clickEvent) {
    // Visual selection
    document.querySelectorAll(".stack-item").forEach((el) =>
        el.classList.remove("selected")
    );
    const target = clickEvent.currentTarget;
    if (target) {
        target.classList.add("selected");
    }

    state.selectedStack = stack.path;

    // Show terminal with initial message
    const termSection = document.getElementById("step-analyzing");
    const termOutput = document.getElementById("terminal-output");
    termOutput.replaceChildren();
    setTerminalDots("running");
    addTerminalLine("run", "Resolving compose for " + extractDirName(stack.path) + "...");
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
        } else {
            setTerminalDots("done");
            if (data.status === "healthy") {
                showHealthyResult(data);
            } else {
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
    showCurrentSetup(data);
    showProblem(data);
    showMountWarnings(data);
    showSolution(data);
    showWhyItWorks(data);
    showNextSteps();
    showCategoryAdvisory(data);
    showTrashAdvisory();
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

    section.classList.remove("hidden");
}

// ─── Mount Warnings ───

function showMountWarnings(data) {
    const section = document.getElementById("step-mount-warnings");
    if (!section) return;

    const warnings = data.mount_warnings || [];
    if (warnings.length === 0) {
        section.classList.add("hidden");
        return;
    }

    const details = document.getElementById("mount-warning-details");
    details.replaceChildren();

    warnings.forEach((text) => {
        const item = document.createElement("div");
        item.className = "callout callout-warning";
        item.textContent = text;
        details.appendChild(item);
    });

    section.classList.remove("hidden");
}

// ─── Solution ───

function showSolution(data) {
    const section = document.getElementById("step-solution");
    const summaryEl = document.getElementById("solution-summary");
    const yamlEl = document.getElementById("solution-yaml");

    summaryEl.textContent =
        "Update your docker-compose.yml with the volume configuration below. " +
        "Replace /host/data with your actual host data directory.";

    if (data.solution_yaml) {
        yamlEl.textContent = data.solution_yaml;
    } else {
        // Fallback: show the fix text from the first conflict
        const firstFix = data.conflicts?.find((c) => c.fix);
        yamlEl.textContent = firstFix?.fix || "No specific YAML changes generated.";
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

    points.forEach((text) => {
        const p = document.createElement("p");
        p.textContent = text;
        p.style.marginBottom = "0.75rem";
        details.appendChild(p);
    });

    section.classList.remove("hidden");
}

// ─── Next Steps ───

function showNextSteps() {
    const section = document.getElementById("step-next");
    const checklist = document.getElementById("next-steps-checklist");
    checklist.replaceChildren();

    const steps = [
        "Edit your docker-compose.yml with the changes above",
        "Create the host directory structure if it doesn't exist",
        "Run: docker compose down && docker compose up -d",
        "Check your *arr app — the error should be gone",
        "If issues persist, paste your new error and analyze again",
    ];

    steps.forEach((text, i) => {
        const item = document.createElement("div");
        item.className = "checklist-item";

        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.id = "step-check-" + i;
        item.appendChild(checkbox);

        const label = document.createElement("label");
        label.htmlFor = "step-check-" + i;

        // Make code-like text stand out
        if (text.includes("docker compose")) {
            const parts = text.split("docker compose down && docker compose up -d");
            if (parts.length === 2) {
                label.appendChild(document.createTextNode(parts[0]));
                const code = document.createElement("code");
                code.textContent = "docker compose down && docker compose up -d";
                label.appendChild(code);
                label.appendChild(document.createTextNode(parts[1]));
            } else {
                label.textContent = text;
            }
        } else {
            label.textContent = text;
        }

        item.appendChild(label);
        checklist.appendChild(item);
    });

    section.classList.remove("hidden");
}

// ─── Category Path Advisory ───

function showCategoryAdvisory(data) {
    const section = document.getElementById("step-category-warning");
    if (!section) return;

    // Only show when we detect both an *arr app and a download client
    const services = data.services || [];
    const hasArr = services.some((s) => s.role === "arr");
    const hasDl = services.some((s) => s.role === "download_client");

    if (!hasArr || !hasDl) {
        section.classList.add("hidden");
        return;
    }

    const details = document.getElementById("category-warning-details");
    details.replaceChildren();

    const intro = document.createElement("p");
    intro.className = "category-intro";
    intro.textContent =
        "MapArr analyzes your Docker volume mounts — but there's one layer it can't see. " +
        "This is the #1 cause of import failures that survives a correct volume setup.";
    details.appendChild(intro);

    const callout = document.createElement("div");
    callout.className = "callout callout-category";

    const title = document.createElement("strong");
    title.style.cssText = "display: block; margin-bottom: 0.4rem; font-size: 0.95rem;";
    title.textContent = "Your download client's category save path must match your volume mounts.";
    callout.appendChild(title);

    // Build specific guidance based on detected services
    const arrNames = services.filter((s) => s.role === "arr").map((s) => s.name);
    const dlNames = services.filter((s) => s.role === "download_client").map((s) => s.name);

    const example = document.createElement("p");
    example.style.cssText = "margin: 0.5rem 0; font-size: 0.85rem; color: var(--text-secondary);";

    const dlName = dlNames[0] || "your download client";
    const arrName = arrNames[0] || "your *arr app";
    const isQbit = dlName.toLowerCase().includes("qbit") || dlName.toLowerCase().includes("torrent");
    const isSab = dlName.toLowerCase().includes("sab") || dlName.toLowerCase().includes("nzb");

    if (isQbit) {
        example.textContent =
            "In qBittorrent: go to Options > Downloads. Check the Default Save Path " +
            "AND each category's save path (right-click a category > Edit). " +
            "These must point to a directory inside a volume mount that " +
            arrName + " can also see. Example: /data/torrents/tv-sonarr";
    } else if (isSab) {
        example.textContent =
            "In SABnzbd: go to Config > Folders. Check the Completed Download Folder " +
            "and any category-specific output folders. " +
            "These must point to a directory inside a volume mount that " +
            arrName + " can also see. Example: /data/usenet/tv-sonarr";
    } else {
        example.textContent =
            "In " + dlName + ": find the download save path / category output folder settings. " +
            "These must point to a directory inside a volume mount that " +
            arrName + " can also see.";
    }
    callout.appendChild(example);

    const why = document.createElement("p");
    why.style.cssText = "margin: 0.5rem 0 0; font-size: 0.8rem; color: var(--text-muted);";
    why.textContent =
        "Why this matters: when " + arrName + " tries to import a completed download, " +
        "it looks at the path " + dlName + " reports. If that path isn't under a " +
        "shared volume mount, the import fails — even if your compose volumes are perfect.";
    callout.appendChild(why);

    details.appendChild(callout);
    section.classList.remove("hidden");
}

// ─── TRaSH Advisory ───

function showTrashAdvisory() {
    const section = document.getElementById("step-trash");
    const details = document.getElementById("trash-details");
    details.replaceChildren();

    const intro = document.createElement("p");
    intro.textContent =
        "The solution above fixes your immediate problem. " +
        "For the cleanest long-term setup, consider the TRaSH Guides structure:";
    details.appendChild(intro);

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

    const note = document.createElement("p");
    note.textContent =
        "This eliminates path confusion entirely — everything lives under /data " +
        "and all containers see the same structure.";
    details.appendChild(note);

    const callout = document.createElement("div");
    callout.className = "callout callout-success";
    const link = document.createElement("a");
    link.href = "https://trash-guides.info/Hardlinks/Docker/";
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

    const msg = document.createElement("p");
    msg.className = "healthy-message";
    msg.textContent = "No path conflicts detected in your setup.";
    details.appendChild(msg);

    if (data.fix_summary) {
        const detail = document.createElement("p");
        detail.className = "healthy-detail";
        detail.textContent = data.fix_summary;
        details.appendChild(detail);
    }

    // Still show current setup
    showCurrentSetup(data);

    // Show mount warnings even for healthy stacks (e.g., NFS detected)
    showMountWarnings(data);

    const callout = document.createElement("div");
    callout.className = "callout callout-success";
    callout.textContent =
        "Your volume mounts look correctly structured for hardlinks and atomic moves. " +
        "If you're still experiencing errors, the issue is likely app configuration, not Docker.";
    details.appendChild(callout);

    // Actionable next steps when setup is healthy
    const guidance = document.createElement("div");
    guidance.className = "healthy-guidance";

    const guidanceTitle = document.createElement("h3");
    guidanceTitle.textContent = "If you still have errors, check:";
    guidanceTitle.style.cssText = "font-size: 0.9rem; margin: 1rem 0 0.5rem; color: var(--text-primary);";
    guidance.appendChild(guidanceTitle);

    // Secret sauce callout — download client categories
    const categoryCallout = document.createElement("div");
    categoryCallout.className = "callout callout-category";

    const catTitle = document.createElement("strong");
    catTitle.textContent = "Most likely cause: Download client category paths";
    catTitle.style.cssText = "display: block; margin-bottom: 0.4rem; color: var(--text-primary); font-size: 0.9rem;";
    categoryCallout.appendChild(catTitle);

    const catDesc = document.createElement("p");
    catDesc.style.cssText = "margin: 0 0 0.5rem; color: var(--text-secondary); font-size: 0.85rem;";
    catDesc.textContent =
        "This is the #1 overlooked cause of import failures. Your Docker volumes can be perfect " +
        "and imports will still fail if your download client's category save path doesn't match " +
        "a path your *arr app can see.";
    categoryCallout.appendChild(catDesc);

    const catHow = document.createElement("p");
    catHow.style.cssText = "margin: 0; color: var(--text-secondary); font-size: 0.85rem;";
    catHow.textContent =
        "In qBittorrent: Options > Downloads > Default Save Path (and per-category paths). " +
        "In SABnzbd: Config > Folders > Completed Download Folder. " +
        "These paths must be under a directory that your *arr app's volume mounts also cover. " +
        "Example: if Sonarr mounts /data, qBittorrent's category path for 'tv-sonarr' must save to /data/torrents/tv-sonarr.";
    categoryCallout.appendChild(catHow);
    guidance.appendChild(categoryCallout);

    const checks = [
        {
            label: "Root Folder settings",
            detail: "In your *arr app, go to Settings > Media Management > Root Folders. " +
                "Make sure the root folder path matches a mounted container path (e.g., /data/media/tv)."
        },
        {
            label: "File permissions (PUID/PGID)",
            detail: "Ensure all containers run with the same PUID/PGID. " +
                "Check that the user has read/write access to the data directories on the host."
        },
    ];

    checks.forEach((check) => {
        const item = document.createElement("div");
        item.style.cssText = "margin-bottom: 0.6rem;";
        const strong = document.createElement("strong");
        strong.textContent = check.label;
        strong.style.cssText = "color: var(--text-primary); font-size: 0.85rem;";
        item.appendChild(strong);
        const p = document.createElement("p");
        p.textContent = check.detail;
        p.style.cssText = "color: var(--text-muted); font-size: 0.8rem; margin: 0.2rem 0 0;";
        item.appendChild(p);
        guidance.appendChild(item);
    });

    // RPM note — reframed, not circular
    const rpmNote = document.createElement("div");
    rpmNote.style.cssText = "margin-top: 0.75rem; padding: 0.6rem 0.75rem; background: rgba(74,144,217,0.06); border-radius: 4px; font-size: 0.8rem; color: var(--text-muted);";
    rpmNote.textContent =
        "About Remote Path Mappings: MapArr has already analyzed the Docker volume layer that " +
        "RPMs sit on top of. If your volumes are correct (as shown above), you likely don't need " +
        "Remote Path Mappings at all — they're a workaround for mismatched mounts, not a fix. " +
        "Correct volume mounts eliminate the need for RPMs entirely.";
    guidance.appendChild(rpmNote);

    const trashLink = document.createElement("div");
    trashLink.className = "callout";
    const link = document.createElement("a");
    link.href = "https://trash-guides.info/Hardlinks/Docker/";
    link.target = "_blank";
    link.rel = "noopener";
    link.textContent = "TRaSH Guides: Docker Hardlinks & Atomic Moves — the full walkthrough";
    trashLink.appendChild(link);
    guidance.appendChild(trashLink);

    details.appendChild(guidance);

    section.classList.remove("hidden");
    showCategoryAdvisory(data);
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
    document.getElementById("step-again").classList.remove("hidden");
}

// ─── Analyze Another (return to stack list) ───

function analyzeAnother() {
    // Hide all result sections
    const resultSections = [
        "step-analyzing", "step-current-setup", "step-problem",
        "step-mount-warnings", "step-solution", "step-why",
        "step-next", "step-category-warning", "step-trash",
        "step-healthy", "step-analysis-error", "step-again",
    ];
    resultSections.forEach((id) => {
        document.getElementById(id).classList.add("hidden");
    });

    // Deselect any selected stack
    document.querySelectorAll(".stack-item").forEach((el) =>
        el.classList.remove("selected")
    );

    // Scroll back to stack list
    const stackSection = document.getElementById("step-stacks");
    stackSection.scrollIntoView({ behavior: "smooth", block: "start" });
}

// ─── Copy Solution YAML ───

function copySolutionYaml() {
    const yamlText = document.getElementById("solution-yaml").textContent;
    const btn = document.getElementById("btn-copy");

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

function isConfigVolume(target) {
    const configPaths = ["/config", "/app", "/etc", "/var", "/tmp", "/run", "/dev"];
    return configPaths.some(
        (p) => target === p || target.startsWith(p + "/")
    );
}
