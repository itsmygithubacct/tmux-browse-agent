// cli-agents.js — Launch CLI agent UX
//
// Wires the "Launch CLI Agent" config card to the agent extension's
// /api/agent-cli endpoints. On dashboard load (and after each launch),
// we GET /api/agent-cli to populate the dropdown with the registry
// entries flagged as installed; missing ones are listed below with
// install hints. The launch button POSTs /api/agent-cli/launch and
// the new tmux session appears in the main pane list via SSE refresh.

const CLI_AGENTS_REFRESH_MS = 30000;

let _cliAgentsRows = [];

async function loadCliAgents() {
    try {
        const resp = await fetch("/api/agent-cli");
        if (!resp.ok) return;
        const data = await resp.json();
        _cliAgentsRows = Array.isArray(data.agents) ? data.agents : [];
        renderCliAgents();
    } catch (e) {
        const status = document.getElementById("cli-agents-status");
        if (status) status.textContent = "failed to load CLI registry: " + e;
    }
}

function renderCliAgents() {
    const select = document.getElementById("cli-agents-select");
    const list = document.getElementById("cli-agents-list");
    if (!select || !list) return;

    const installed = _cliAgentsRows.filter(r => r.installed);
    const missing = _cliAgentsRows.filter(r => !r.installed);

    select.innerHTML = "";
    if (installed.length === 0) {
        const opt = document.createElement("option");
        opt.textContent = "(no CLI agents installed)";
        opt.disabled = true;
        select.appendChild(opt);
    } else {
        for (const row of installed) {
            const opt = document.createElement("option");
            opt.value = row.name;
            opt.textContent = row.label || row.name;
            if (row.hooks_supported) opt.textContent += " (hooks)";
            select.appendChild(opt);
        }
    }

    if (missing.length === 0) {
        list.textContent = `${installed.length} CLI agent${installed.length === 1 ? "" : "s"} ready.`;
    } else {
        const parts = [
            `${installed.length} ready`,
            `${missing.length} not on $PATH:`,
        ];
        const hints = missing
            .map(r => `${r.label || r.name}: ${r.install_hint || "(no hint)"}`)
            .join(" · ");
        list.textContent = parts.join(", ") + " " + hints;
    }
}

async function launchCliAgent() {
    const select = document.getElementById("cli-agents-select");
    const cwdInput = document.getElementById("cli-agents-cwd");
    const yoloInput = document.getElementById("cli-agents-yolo");
    const status = document.getElementById("cli-agents-status");
    if (!select || !status) return;

    const name = select.value;
    if (!name) {
        status.textContent = "pick an agent first";
        return;
    }
    const body = {
        name,
        cwd: (cwdInput && cwdInput.value.trim()) || undefined,
        yolo: !!(yoloInput && yoloInput.checked),
    };
    status.textContent = `launching ${name}...`;
    try {
        const resp = await fetch("/api/agent-cli/launch", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify(body),
        });
        const data = await resp.json();
        if (!resp.ok || !data.ok) {
            const hint = data.install_hint ? ` — install: ${data.install_hint}` : "";
            status.textContent = `launch failed: ${data.error || resp.statusText}${hint}`;
            return;
        }
        status.textContent = `launched ${data.name} in ${data.session}`;
        // The SSE session stream will pick up the new pane on the next tick.
    } catch (e) {
        status.textContent = "launch failed: " + e;
    }
}

function bindCliAgents() {
    const btn = document.getElementById("cli-agents-launch-btn");
    if (btn) btn.addEventListener("click", launchCliAgent);
    loadCliAgents();
    // Periodically refresh installed-state in case the user installs a CLI
    // in another terminal while the dashboard is open.
    if (typeof window !== "undefined" && !window._cliAgentsRefreshTimer) {
        window._cliAgentsRefreshTimer = setInterval(loadCliAgents, CLI_AGENTS_REFRESH_MS);
    }
}

if (typeof document !== "undefined") {
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", bindCliAgents);
    } else {
        bindCliAgents();
    }
}
