// agents.js — agent configuration, status, steps, workflows

function setAgentStatus(text, tone = "dim") {
    const node = document.getElementById("cfg-agent-status");
    if (!node) return;
    node.textContent = text;
    node.className = tone;
}

function agentFieldMap() {
    return {
        existing: document.getElementById("cfg-agent-existing"),
        preset: document.getElementById("cfg-agent-preset"),
        name: document.getElementById("cfg-agent-name"),
        provider: document.getElementById("cfg-agent-provider"),
        model: document.getElementById("cfg-agent-model"),
        base_url: document.getElementById("cfg-agent-base-url"),
        wire_api: document.getElementById("cfg-agent-wire-api"),
        sandbox: document.getElementById("cfg-agent-sandbox"),
        token_budget: document.getElementById("cfg-agent-token-budget"),
        daily_token_budget: document.getElementById("cfg-agent-daily-budget"),
        api_key: document.getElementById("cfg-agent-api-key"),
    };
}

// Reflect host Docker capability without rewriting persisted "docker" values.
// When unavailable: hide the option for new agents, but show it as
// "docker (unavailable on this host)" disabled when a saved agent already
// has sandbox=docker so the user sees the truth instead of a silent flip.
function applyDockerCapability(currentValue) {
    const select = document.getElementById("cfg-agent-sandbox");
    if (!select) return;
    const option = select.querySelector('option[value="docker"]');
    if (!option) return;
    const supported = !!state.dockerSupported;
    if (supported) {
        option.hidden = false;
        option.disabled = false;
        option.textContent = "docker";
    } else if (currentValue === "docker") {
        option.hidden = false;
        option.disabled = true;
        option.textContent = "docker (unavailable on this host)";
    } else {
        option.hidden = true;
        option.disabled = true;
        option.textContent = "docker";
    }
}

function agentSummaryText() {
    const bits = [];
    if (state.agents.length) bits.push(`configured: ${state.agents.map(r => r.name).join(", ")}`);
    else bits.push("configured: none");
    if (state.agentPaths.agents) bits.push(state.agentPaths.agents);
    return bits.join(" · ");
}

function conversationSessionName(agentName) {
    return AGENT_CONVERSATION_PREFIX + (agentName || "").trim().toLowerCase();
}

function workflowEntry(agentName) {
    const name = (agentName || "").trim().toLowerCase();
    if (!state.workflowConfig.agents[name]) {
        state.workflowConfig.agents[name] = {
            enabled: false,
            workflows: Array.from({ length: 8 }, () => ({ name: "", prompt: "", interval_seconds: 300 })),
        };
    }
    return state.workflowConfig.agents[name];
}

function agentStatusLabel(status) {
    const labels = {
        running: "Running",
        idle: "Idle",
        error: "Error",
        rate_limited: "Rate Limited",
        workflow_paused: "Paused",
    };
    return labels[status] || status || "unknown";
}

function agentLastActivity(ts) {
    if (!ts) return "";
    return fmtAgeSeconds(Math.max(0, Math.floor(Date.now() / 1000) - ts)) + " ago";
}

// Count how many conductor rules match a given agent (specific or wildcard).
function conductorRulesForAgent(agentName) {
    const rules = state.conductorRules || [];
    return rules.filter((r) => {
        const when = r.when || {};
        const target = when.agent || "*";
        return target === "*" || target === agentName;
    }).length;
}

function renderAgentsPane() {
    const wrap = document.getElementById("agents-wrap");
    const count = document.getElementById("agents-count");
    const root = document.getElementById("agents-pane");
    if (!wrap || !count || !root) return;
    count.textContent = String(state.agents.length);
    // Hidden unless the user explicitly enables the pane in Config.
    // Previously we showed it automatically when any agent existed, but
    // most users never need the agent surface and the pane is a bulky
    // interruption in the Config column.
    const enabled = !!(state.config && state.config.show_agents_pane);
    wrap.hidden = !enabled || state.agents.length === 0;
    root.innerHTML = "";
    for (const row of state.agents) {
        const sessionName = conversationSessionName(row.name);
        const live = state.sessions.find((s) => s.name === sessionName);
        const st = row.status || "idle";
        const reason = row.status_reason || "";
        const lastTs = row.last_activity_ts || 0;
        const statusLine = [];
        if (reason) statusLine.push(reason);
        const lastAct = agentLastActivity(lastTs);
        if (lastAct) statusLine.push(lastAct);
        if (live) statusLine.push(`session ${sessionName} on port ${live.port || "—"}`);

        root.append(el("section", { class: "agent-card" },
            el("div", { class: "agent-card-head" },
                el("div", {},
                    el("div", { class: "agent-card-title" }, row.name),
                    el("div", { class: "dim agent-card-meta" }, `${row.provider || "custom"} · ${row.model || "no model"}`),
                ),
                el("div", { class: "agent-card-actions" },
                    el("button", {
                        class: "btn blue",
                        type: "button",
                        onclick: () => openAgentSteps(row.name),
                    }, "Steps"),
                    el("a", {
                        class: "btn",
                        target: "_blank",
                        rel: "noopener",
                        href: `/api/agent-log?name=${encodeURIComponent(row.name)}`,
                    }, "Log"),
                    el("button", {
                        class: "btn green",
                        type: "button",
                        onclick: () => openAgentConversation(row.name),
                    }, live ? "Open REPL" : "Start REPL"),
                    el("button", {
                        class: "btn",
                        type: "button",
                        onclick: () => forkAgentConversation(row.name),
                    }, "Fork REPL"),
                    el("button", {
                        class: "btn",
                        type: "button",
                        onclick: () => openAgentContext(row.name),
                        title: "View REPL context (exec target, observed panes, KB)",
                    }, "Context"),
                    el("button", {
                        class: "btn blue",
                        type: "button",
                        onclick: () => startAgentCycle(row.name),
                        title: "Run one plan + execute cycle",
                    }, "Cycle"),
                    el("button", {
                        class: "btn blue",
                        type: "button",
                        onclick: () => startAgentWork(row.name),
                        title: "Run tasks from a file, one at a time",
                    }, "Work"),
                ),
            ),
            el("div", { class: "agent-card-status" },
                el("span", { class: `agent-status-badge s-${st}` }, agentStatusLabel(st)),
                row.mode
                    ? el("span", {
                        class: "agent-status-badge s-idle",
                        title: "mode detected from most recent run's origin",
                      }, row.mode_phase
                          ? `${row.mode} / ${row.mode_phase}`
                          : row.mode)
                    : el("span"),
                row.budget_status === "warn"
                    ? el("span", { class: "agent-status-badge s-rate_limited" }, "Budget Warning")
                    : row.budget_status === "stop"
                    ? el("span", { class: "agent-status-badge s-error" }, "Budget Exceeded")
                    : el("span"),
                conductorRulesForAgent(row.name) > 0
                    ? el("span", {
                        class: "agent-status-badge s-idle",
                        style: "cursor:pointer",
                        onclick: () => openConductorActivity(row.name),
                        title: "recent conductor decisions for this agent",
                      }, `Conductor: ${conductorRulesForAgent(row.name)}`)
                    : el("span"),
                el("span", {}, statusLine.join(" · ")),
            ),
        ));
    }
}

// --- Agent modes (cycle / work) ---

async function startAgentCycle(agentName) {
    const goalText = prompt(
        `Cycle "${agentName}": optional goal text (leave blank to use the `
        + `stored goal file or let the agent propose one)`);
    if (goalText === null) return;  // canceled
    const body = { name: agentName };
    if (goalText.trim()) body.goal_text = goalText.trim();
    const r = await api("POST", "/api/agent-cycle", body);
    if (!r.ok) {
        alert("cycle failed: " + (r.error || "unknown"));
        return;
    }
    if (r.plan) {
        alert(`[${agentName} cycle]\n\nPlan:\n${r.plan}\n\n`
              + `Execute phase is running in the background; watch the `
              + `Runs section for the result.`);
    } else {
        alert(`[${agentName} cycle] started in background. `
              + `Watch the Runs section.`);
    }
    await searchRuns();
}

async function startAgentWork(agentName) {
    const tasks = prompt(
        `Work "${agentName}": path to tasks file (one task per line)`);
    if (!tasks || !tasks.trim()) return;
    const body = { name: agentName, tasks: tasks.trim() };
    const r = await api("POST", "/api/agent-work", body);
    if (!r.ok) {
        alert("work failed to start: " + (r.error || "unknown"));
        return;
    }
    alert(`[${agentName} work] started. Each task becomes a run with `
          + `origin="work" in the Runs section. Click Work again and the `
          + `stop-endpoint is hit.`);
}

// Lazy-loaded REPL context view. Called when the user clicks the
// "Context" badge on an agent card.
async function openAgentContext(agentName) {
    const r = await api("GET",
        `/api/agent-repl-context?name=${encodeURIComponent(agentName)}`);
    if (!r.ok) {
        alert("failed to load context: " + (r.error || "unknown"));
        return;
    }
    const ctx = r.context || {};
    const kb = r.kb || [];
    const lines = [
        `REPL context · ${agentName}`,
        "",
        `  exec target:    ${ctx.exec_target || "(unset)"}`,
        `  observed panes: ${(ctx.observed_panes || []).join(", ") || "(none)"}`,
        `  mode:           ${ctx.mode || "observe"}`,
        `  tick_sec:       ${ctx.tick_sec || 10}`,
        "",
        `  KB files: ${kb.length} (${r.kb_total_bytes || 0} / ${r.kb_cap_bytes} bytes)`,
    ];
    for (const f of kb) lines.push(`    ${f.name}  ${f.size} bytes`);
    lines.push("", "Edit via `tb agent repl " + agentName + "` (/exec, /watch, /kb, etc.)");
    alert(lines.join("\n"));
}

// Simple lazy-loaded activity view: fetch the decision log filtered to
// this agent and alert() the result. A proper modal is a follow-up.
async function openConductorActivity(agentName) {
    const r = await api("GET",
        `/api/agent-conductor-events?agent=${encodeURIComponent(agentName)}&limit=25`);
    if (!r.ok) {
        alert("failed to load conductor activity: " + (r.error || "unknown"));
        return;
    }
    const decisions = r.decisions || [];
    if (!decisions.length) {
        alert(`No conductor decisions for ${agentName}.`);
        return;
    }
    const lines = decisions.slice(0, 25).map((d) => {
        const ts = new Date((d.ts || 0) * 1000).toLocaleString();
        const actions = (d.actions || []).map((a) => a.action).join(",");
        const reason = d.reason ? ` [${d.reason}]` : "";
        return `${ts}  ${d.rule_id}  on ${d.event}  → ${actions}${reason}`;
    });
    alert(`Conductor activity · ${agentName}\n\n` + lines.join("\n"));
}


// --- Costs ---

async function loadCostSummary() {
    const node = document.getElementById("cost-summary");
    if (!node) return;
    const r = await api("GET", "/api/agent-costs");
    if (!r.ok) { node.textContent = ""; return; }
    const agents = r.per_agent || {};
    const names = Object.keys(agents);
    if (!names.length) { node.textContent = ""; return; }
    const parts = names.map((name) => {
        const a = agents[name];
        return `${name}: ${(a.total_tokens || 0).toLocaleString()} tokens (${a.runs} runs)`;
    });
    node.textContent = "Usage: " + parts.join(" · ");
}


function _stepBlock(label, text, cls = "agent-step-block") {
    return el("div", { class: cls },
        el("div", { class: "agent-step-label" }, label),
        el("pre", { class: "agent-step-pre" }, text),
    );
}

function renderAgentStepsModal() {
    const title = document.getElementById("agent-steps-modal-title");
    const list = document.getElementById("agent-steps-list");
    const detail = document.getElementById("agent-steps-detail");
    const entries = state.stepViewer.entries || [];
    const agent = state.stepViewer.agent || "";
    title.textContent = `Agent Steps · ${agent}`;
    list.textContent = "";
    detail.textContent = "";
    if (!entries.length) {
        list.append(el("div", { class: "dim split-empty" }, `No logged runs for ${agent}.`));
        return;
    }
    const selected = Math.max(0, Math.min(state.stepViewer.selected, entries.length - 1));
    state.stepViewer.selected = selected;
    entries.forEach((entry, idx) => {
        const steps = Array.isArray(entry.transcript) ? entry.transcript.length : 0;
        const prompt = (entry.prompt || "").trim() || "(no prompt)";
        list.append(el("button", {
            class: idx === selected ? "hot-slot-item active" : "hot-slot-item",
            type: "button",
            onclick: () => {
                state.stepViewer.selected = idx;
                renderAgentStepsModal();
            },
        },
        el("span", { class: "hot-slot-kicker" }, `${entry.origin || "-"} · ${entry.status || "-"} · ${steps} steps`),
        el("span", { class: "hot-slot-name" }, prompt.length > 72 ? `${prompt.slice(0, 72)}...` : prompt),
        el("span", { class: "hot-slot-command" }, entry.message || entry.error || `ts ${entry.ts || 0}`),
        ));
    });
    const entry = entries[selected] || {};
    detail.append(
        el("div", { class: "agent-step-meta" },
            el("div", {}, `origin: ${entry.origin || "-"}`),
            el("div", {}, `status: ${entry.status || "-"}`),
            el("div", {}, `steps: ${Array.isArray(entry.transcript) ? entry.transcript.length : 0}`),
            el("div", {}, `log: ${state.stepViewer.path || "-"}`),
        ),
    );
    if (entry.prompt) detail.append(_stepBlock("Prompt", entry.prompt));
    if (entry.message) detail.append(_stepBlock("Final Message", entry.message));
    if (entry.error) detail.append(_stepBlock("Error", entry.error, "agent-step-block err"));
    if (Array.isArray(entry.transcript)) {
        for (const item of entry.transcript) {
            const step = item && item.step !== undefined ? item.step : "?";
            const wrap = el("div", { class: "agent-step-block" },
                el("div", { class: "agent-step-label" }, `Step ${step}`),
            );
            if (item.action) wrap.append(el("pre", { class: "agent-step-pre" }, JSON.stringify(item.action, null, 2)));
            if (item.parse_error) wrap.append(el("pre", { class: "agent-step-pre err" }, String(item.parse_error)));
            if (item.tool_result) wrap.append(el("pre", { class: "agent-step-pre" }, JSON.stringify(item.tool_result, null, 2)));
            detail.append(wrap);
        }
    }
}

async function openAgentSteps(agentName) {
    const name = (agentName || "").trim().toLowerCase();
    const r = await api("GET", `/api/agent-log-json?name=${encodeURIComponent(name)}&limit=20`);
    if (!r.ok) {
        setAgentStatus("error loading agent steps: " + (r.error || "unknown"), "err");
        return;
    }
    state.stepViewer.open = true;
    state.stepViewer.agent = name;
    state.stepViewer.entries = Array.isArray(r.entries) ? [...r.entries].reverse() : [];
    state.stepViewer.selected = 0;
    state.stepViewer.path = r.path || "";
    renderAgentStepsModal();
    document.getElementById("agent-steps-modal").hidden = false;
    syncModalChrome();
}

function closeAgentSteps() {
    state.stepViewer.open = false;
    document.getElementById("agent-steps-modal").hidden = true;
    syncModalChrome();
}

function renderAgentSummary() {
    const node = document.getElementById("cfg-agent-summary");
    if (!node) return;
    node.textContent = agentSummaryText();
}


function populateSelect(node, rows, firstLabel, labelFn) {
    if (!node) return;
    node.innerHTML = "";
    node.append(el("option", { value: "" }, firstLabel));
    for (const row of rows) {
        node.append(el("option", { value: row.name }, labelFn(row)));
    }
}

function renderAgentSelectors(selectedName = "", selectedPreset = "") {
    const fields = agentFieldMap();
    populateSelect(fields.existing, state.agents, "New agent", (row) =>
        `${row.name} (${row.provider || "custom"} · ${row.model || "no model"})`,
    );
    populateSelect(fields.preset, state.agentDefaults, "Custom / manual", (row) =>
        `${row.label || row.name} (${row.name})`,
    );
    fields.existing.value = selectedName || "";
    fields.preset.value = selectedPreset || "";
    renderAgentSummary();
}

function findAgentRow(name) {
    return state.agents.find((row) => row.name === name) || null;
}

function findAgentDefault(name) {
    return state.agentDefaults.find((row) => row.name === name) || null;
}

function currentAgentConstraint() {
    const fields = agentFieldMap();
    const name = (fields.name.value || fields.existing.value || fields.preset.value || "").trim().toLowerCase();
    if (name === "kimi") {
        return {
            provider: "kimi",
            wire_api: "anthropic-messages",
            message: "kimi is locked to the Kimi coding endpoint and Anthropic wire format",
        };
    }
    return null;
}

function enforceAgentConstraint() {
    const fields = agentFieldMap();
    const constraint = currentAgentConstraint();
    const locked = !!constraint;
    if (constraint) {
        fields.provider.value = constraint.provider;
        fields.wire_api.value = constraint.wire_api;
    }
    fields.provider.readOnly = locked;
    fields.wire_api.disabled = locked;
    return constraint;
}

function fillAgentForm(row, opts = {}) {
    const fields = agentFieldMap();
    const preset = opts.presetName !== undefined ? opts.presetName : (findAgentDefault(row.name) ? row.name : "");
    fields.name.value = row.name || "";
    fields.provider.value = row.provider || "";
    fields.model.value = row.model || "";
    fields.base_url.value = row.base_url || "";
    fields.wire_api.value = row.wire_api || "openai-chat";
    const sandboxValue = row.sandbox || "host";
    applyDockerCapability(sandboxValue);
    fields.sandbox.value = sandboxValue;
    fields.token_budget.value = row.token_budget || 0;
    fields.daily_token_budget.value = row.daily_token_budget || 0;
    fields.api_key.value = "";
    fields.existing.value = opts.existingName !== undefined ? opts.existingName : (row.name || "");
    fields.preset.value = preset;
    enforceAgentConstraint();
}

function clearAgentForm() {
    fillAgentForm({
        name: "",
        provider: "",
        model: "",
        base_url: "",
        wire_api: "openai-chat",
        sandbox: "host",
    }, { existingName: "", presetName: "" });
}

function loadExistingAgentIntoForm() {
    const fields = agentFieldMap();
    const row = findAgentRow(fields.existing.value);
    if (!row) {
        clearAgentForm();
        setAgentStatus("editing a new agent", "dim");
        return;
    }
    fillAgentForm(row);
    const constraint = enforceAgentConstraint();
    setAgentStatus(constraint
        ? constraint.message
        : row.has_api_key
        ? `loaded ${row.name}; leave API key blank to keep the stored secret`
        : `loaded ${row.name}; add an API key before saving`, constraint ? "dim" : (row.has_api_key ? "dim" : "err"));
}

function applyAgentPreset() {
    const fields = agentFieldMap();
    const preset = findAgentDefault(fields.preset.value);
    if (!preset) return;
    if (!fields.name.value.trim() || fields.existing.value !== fields.name.value.trim()) {
        fields.name.value = preset.name;
    }
    fields.provider.value = preset.provider || "";
    fields.model.value = preset.model || "";
    fields.base_url.value = preset.base_url || "";
    fields.wire_api.value = preset.wire_api || "openai-chat";
    if (fields.existing.value && fields.existing.value !== fields.name.value.trim()) {
        fields.existing.value = "";
    }
    const constraint = enforceAgentConstraint();
    setAgentStatus(constraint ? constraint.message : `loaded preset ${preset.name}`, "dim");
}

async function loadAgents(selectedName = "") {
    const current = selectedName || agentFieldMap().existing.value || agentFieldMap().name.value.trim();
    const r = await api("GET", "/api/agents");
    if (!r.ok) {
        setAgentStatus("error loading agents: " + (r.error || "unknown"), "err");
        return false;
    }
    state.agents = Array.isArray(r.agents) ? r.agents : [];
    state.agentDefaults = Array.isArray(r.defaults) ? r.defaults : [];
    state.agentPaths = r.paths || { agents: "", secrets: "" };
    state.dockerSupported = !!r.docker_supported;
    const selectedRow = findAgentRow(current);
    renderAgentSelectors(selectedRow ? selectedRow.name : "", findAgentDefault(current) ? current : "");
    if (selectedRow) fillAgentForm(selectedRow);
    else if (!current && state.agents.length) {
        renderAgentSelectors(state.agents[0].name, findAgentDefault(state.agents[0].name) ? state.agents[0].name : "");
        fillAgentForm(state.agents[0]);
    } else if (!current) {
        clearAgentForm();
    } else {
        enforceAgentConstraint();
    }
    renderAgentsPane();
    populateRunAgentFilter();
    populateTaskAgentSelect();
    return true;
}

async function loadAgentWorkflows(showStatus = false) {
    const r = await api("GET", "/api/agent-workflows");
    if (!r.ok) {
        if (showStatus) setAgentStatus("error loading workflows: " + (r.error || "unknown"), "err");
        return false;
    }
    state.workflowConfig = normalizeAgentWorkflowConfig(r.config);
    state.workflowPath = r.path || "";
    scheduleWorkflowLoop();
    return true;
}

async function saveAgentWorkflows(showStatus = false) {
    const r = await api("POST", "/api/agent-workflows", { config: state.workflowConfig });
    if (!r.ok) {
        if (showStatus) setAgentStatus("error saving workflows: " + (r.error || "unknown"), "err");
        return false;
    }
    state.workflowConfig = normalizeAgentWorkflowConfig(r.config);
    state.workflowPath = r.path || "";
    scheduleWorkflowLoop();
    return true;
}

async function openAgentConversation(name) {
    const r = await api("POST", "/api/agent-conversation", { name });
    if (!r.ok) {
        setAgentStatus("error opening conversation mode: " + (r.error || "unknown"), "err");
        return;
    }
    await refresh();
    window.open(ttydUrl(r.port), "_blank", "noopener");
}

async function forkAgentConversation(name) {
    const r = await api("POST", "/api/agent-conversation-fork", { name });
    if (!r.ok) {
        setAgentStatus("error forking conversation: " + (r.error || "unknown"), "err");
        return;
    }
    setAgentStatus(`forked ${name} conversation into ${r.session}`, "ok");
    await refresh();
    if (r.port) window.open(ttydUrl(r.port), "_blank", "noopener");
}

function readAgentForm() {
    const fields = agentFieldMap();
    const payload = {
        name: fields.name.value.trim(),
        provider: fields.provider.value.trim(),
        model: fields.model.value.trim(),
        base_url: fields.base_url.value.trim(),
        wire_api: fields.wire_api.value,
        sandbox: fields.sandbox.value,
        token_budget: parseInt(fields.token_budget.value) || 0,
        daily_token_budget: parseInt(fields.daily_token_budget.value) || 0,
    };
    const constraint = currentAgentConstraint();
    if (constraint) {
        payload.provider = constraint.provider;
        payload.wire_api = constraint.wire_api;
    }
    const apiKey = fields.api_key.value.trim();
    if (apiKey) payload.api_key = apiKey;
    return payload;
}

async function saveAgentConfig() {
    const payload = readAgentForm();
    const r = await api("POST", "/api/agents", { agent: payload });
    if (!r.ok) {
        setAgentStatus("error saving agent: " + (r.error || "unknown"), "err");
        return;
    }
    await loadAgents(r.agent && r.agent.name ? r.agent.name : payload.name);
    setAgentStatus(`saved agent ${r.agent.name}`, "ok");
}

async function removeAgentConfig() {
    const fields = agentFieldMap();
    const name = fields.name.value.trim() || fields.existing.value;
    if (!name) {
        setAgentStatus("enter or load an agent name before removing", "err");
        return;
    }
    const r = await api("POST", "/api/agents/remove", { name });
    if (!r.ok) {
        setAgentStatus("error removing agent: " + (r.error || "unknown"), "err");
        return;
    }
    await loadAgents("");
    clearAgentForm();
    setAgentStatus(r.removed ? `removed agent ${name}` : `agent ${name} was not configured`, r.removed ? "ok" : "dim");
}

// --- Event hooks ---

async function loadHooks() {
    const r = await api("GET", "/api/agent-hooks");
    if (r.ok) {
        // Cache the hook config for QR/link share. This is purely a read
        // cache — the source of truth stays on disk via /api/agent-hooks.
        state.agentHooksForShare = r.hooks || {};
        renderHooksEditor(r.hooks || {});
    }
}

function renderHooksEditor(hooks) {
    const root = document.getElementById("hooks-editor");
    if (!root) return;
    root.innerHTML = "";
    const events = [
        ["run_completed", "Run Completed"],
        ["run_failed", "Run Failed"],
        ["run_rate_limited", "Rate Limited"],
        ["budget_exceeded", "Budget Exceeded"],
        ["workflow_skipped", "Workflow Skipped"],
    ];
    const actions = ["log", "retry", "pause_workflow", "notify"];
    for (const [event, label] of events) {
        const current = hooks[event] || ["log"];
        const row = el("div", { style: "margin-bottom:0.4rem" },
            el("span", { style: "font-size:0.82rem;font-weight:600" }, label + ": "),
            ...actions.map((action) =>
                el("label", { style: "font-size:0.8rem;margin-left:0.4rem" },
                    el("input", {
                        type: "checkbox",
                        "data-event": event,
                        "data-action": action,
                        checked: current.includes(action) ? "checked" : undefined,
                    }),
                    " " + action,
                ),
            ),
        );
        root.append(row);
    }
}

function readHooksForm() {
    const root = document.getElementById("hooks-editor");
    if (!root) return {};
    const hooks = {};
    for (const input of root.querySelectorAll("input[data-event]")) {
        const event = input.dataset.event;
        const action = input.dataset.action;
        if (!hooks[event]) hooks[event] = [];
        if (input.checked) hooks[event].push(action);
    }
    return hooks;
}

async function saveHooks() {
    const hooks = readHooksForm();
    const r = await api("POST", "/api/agent-hooks", { hooks });
    const status = document.getElementById("hooks-status");
    if (status) status.textContent = r.ok ? "saved" : "error";
}

async function resetHooks() {
    const r = await api("POST", "/api/agent-hooks", {});
    if (r.ok) renderHooksEditor(r.hooks || {});
    const status = document.getElementById("hooks-status");
    if (status) status.textContent = r.ok ? "reset to defaults" : "error";
}

// --- Conductor editor ---
//
// JSON textarea for now; a proper form UI can come later. The textarea
// maps directly to the POST /api/agent-conductor payload — this keeps
// the server as the schema authority and lets rule shapes evolve
// without a UI change.

async function loadConductor() {
    const r = await api("GET", "/api/agent-conductor");
    const ta = document.getElementById("conductor-editor");
    const status = document.getElementById("conductor-status");
    if (!ta) return;
    if (r.ok) {
        state.conductorRules = r.rules || [];
        ta.value = JSON.stringify({ rules: state.conductorRules }, null, 2);
        if (status) status.textContent = `${state.conductorRules.length} rule(s)`;
    } else {
        if (status) status.textContent = "load failed";
    }
}

async function saveConductor() {
    const ta = document.getElementById("conductor-editor");
    const status = document.getElementById("conductor-status");
    if (!ta) return;
    let parsed;
    try {
        parsed = JSON.parse(ta.value);
    } catch (e) {
        if (status) status.textContent = "invalid JSON: " + e.message;
        return;
    }
    const r = await api("POST", "/api/agent-conductor", parsed);
    if (r.ok) {
        state.conductorRules = r.rules || [];
        ta.value = JSON.stringify({ rules: state.conductorRules }, null, 2);
        if (status) status.textContent = `saved ${state.conductorRules.length} rule(s)`;
        // Refresh per-agent badges so the new rule count shows up.
        renderAgentsPane();
    } else {
        if (status) status.textContent = "error: " + (r.error || "unknown");
    }
}

async function loadNotifications() {
    const r = await api("GET", "/api/agent-notifications");
    if (!r.ok) return;
    const wrap = document.getElementById("notifications-wrap");
    const count = document.getElementById("notifications-count");
    const root = document.getElementById("notifications-pane");
    if (!root) return;
    const notes = r.notifications || [];
    if (count) count.textContent = String(notes.length);
    if (wrap) wrap.hidden = notes.length === 0;
    root.innerHTML = "";
    for (const n of notes.reverse()) {
        root.append(el("div", { class: "run-row" },
            el("span", { class: "agent-status-badge s-" +
                (n.event === "run_completed" ? "idle" : "error") },
                (n.event || "").replace("run_", "").replace("_", " ")),
            el("div", {},
                el("div", {}, n.agent || "?"),
                n.error ? el("div", { class: "run-row-meta" }, n.error) : el("span"),
            ),
            el("div", { class: "run-row-meta" },
                agentLastActivity(n.ts)),
        ));
    }
}

// --- Pane Admin ---

function renderPaneAdmin() {
    const root = document.getElementById("pane-admin-list");
    if (!root) return;
    root.innerHTML = "";
    root.style.display = "grid";
    root.style.gap = "0.4rem";
    for (const s of state.sessions) {
        const name = s.name;
        root.append(el("div", { class: "run-row" },
            el("span", { style: "font-weight:700;font-size:0.85rem;color:var(--accent)" }, name),
            el("div", { class: "run-row-meta" },
                `${s.windows}w · ${s.attached} clients · idle ${fmtAgeSeconds(s.idle_seconds)}`),
            el("div", { class: "agent-card-actions" },
                el("button", { class: "btn red", type: "button",
                    onclick: () => { killSession(name); } }, "Kill"),
                el("button", { class: "btn", type: "button",
                    onclick: () => { toggleHidden(name); } },
                    state.hidden.has(name) ? "Unhide" : "Hide"),
                el("a", { class: "btn", target: "_blank", rel: "noopener",
                    href: `/api/session/log?session=${encodeURIComponent(name)}&lines=2000` }, "Log"),
                el("button", { class: "btn blue", type: "button",
                    onclick: () => openHotButtons(name) }, "Hot Buttons"),
                el("button", { class: "btn orange", type: "button",
                    onclick: () => enterCopyMode(name) }, "Scroll"),
                s.ttyd_running
                    ? el("button", { class: "btn orange", type: "button",
                        onclick: () => stopTtyd(name) }, "Stop ttyd")
                    : el("button", { class: "btn green", type: "button",
                        onclick: () => launch(name) }, "Launch"),
            ),
        ));
    }
}
