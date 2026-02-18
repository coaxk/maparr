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

// ─── Select Stack ───

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

    try {
        const resp = await fetch("/api/select-stack", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ stack_path: stack.path }),
        });

        if (!resp.ok) {
            const err = await resp.json().catch(() => ({ error: "Selection failed" }));
            alert(err.error || "Stack selection failed");
            return;
        }

        const data = await resp.json();
        showReady(data, stack);
    } catch {
        alert("Could not reach the backend.");
    }
}

// ─── Show Ready ───

function showReady(data, stack) {
    const section = document.getElementById("step-ready");
    const details = document.getElementById("ready-details");
    details.replaceChildren();

    const dirName = extractDirName(stack.path);

    details.appendChild(makeParseField("Stack", dirName));
    details.appendChild(
        makeParseField("Services", stack.services?.join(", ") || "unknown")
    );

    if (state.parsedError) {
        details.appendChild(
            makeParseField("Detected Service", state.parsedError.service)
        );
        details.appendChild(
            makeParseField("Detected Path", state.parsedError.path)
        );
    }

    const next = document.createElement("div");
    next.className = "ready-next";
    next.textContent =
        "Stack selected. The analysis engine (Work Order 2) will perform " +
        "deep resolution via docker compose config, cross-reference volume " +
        "mounts, and identify path mapping conflicts.";
    details.appendChild(next);

    section.classList.remove("hidden");
    section.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

// ─── Helpers ───

function extractDirName(path) {
    const parts = path.replace(/\\/g, "/").split("/").filter(Boolean);
    return parts[parts.length - 1] || path;
}
