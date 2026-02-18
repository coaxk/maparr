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
            el.textContent = "Connected";
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

function renderStacks(stacks) {
    const list = document.getElementById("stacks-list");
    list.replaceChildren();

    const detectedService = state.parsedError?.service?.toLowerCase() || "";

    stacks.forEach((stack) => {
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
        list.appendChild(item);
    });
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

    // Show analyzing spinner
    document.getElementById("step-analyzing").classList.remove("hidden");
    document.getElementById("step-analyzing").scrollIntoView({
        behavior: "smooth", block: "nearest",
    });

    try {
        const resp = await fetch("/api/analyze", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                stack_path: stack.path,
                error: state.parsedError,
            }),
        });

        document.getElementById("step-analyzing").classList.add("hidden");

        if (!resp.ok) {
            const err = await resp.json().catch(() => ({ error: "Analysis failed" }));
            showAnalysisError(err.error || "Analysis request failed");
            return;
        }

        const data = await resp.json();
        state.analysis = data;

        if (data.status === "error") {
            showAnalysisError(data.error, data.stage);
        } else if (data.status === "healthy") {
            showHealthyResult(data);
        } else {
            showAnalysisResult(data);
        }
    } catch {
        document.getElementById("step-analyzing").classList.add("hidden");
        showAnalysisError("Could not reach the backend. Is MapArr running?");
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

    // Body
    const tbody = document.createElement("tbody");
    (data.services || []).forEach((svc) => {
        const dataVols = (svc.volumes || []).filter(
            (v) => !isConfigVolume(v.target)
        );

        if (dataVols.length === 0 && svc.role === "other") return;

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
        if (dataVols.length > 0) {
            dataVols.forEach((v, i) => {
                if (i > 0) volCell.appendChild(document.createElement("br"));
                const span = document.createElement("span");
                span.textContent = v.source + " : " + v.target;
                volCell.appendChild(span);
            });
        } else {
            volCell.textContent = "(no data volumes)";
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
        "If you're still experiencing errors, the issue may be with app configuration " +
        "(root folder settings) rather than Docker path mapping.";
    details.appendChild(callout);

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
    document.getElementById("step-again").classList.remove("hidden");
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
