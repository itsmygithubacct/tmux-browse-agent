// runs.js — agent run search and display

function runStatusLabel(status) {
    const labels = { run_completed: "OK", run_failed: "Failed", run_rate_limited: "Rate Limited" };
    return labels[status] || status || "?";
}

function runStatusClass(status) {
    if (status === "run_completed") return "s-idle";
    if (status === "run_rate_limited") return "s-rate_limited";
    if (status === "run_failed") return "s-error";
    return "";
}

async function searchRuns() {
    const q = (document.getElementById("runs-search-q").value || "").trim();
    const agent = document.getElementById("runs-filter-agent").value;
    const status = document.getElementById("runs-filter-status").value;
    const originSel = document.getElementById("runs-filter-origin");
    const origin = originSel ? originSel.value : "";
    const params = new URLSearchParams();
    if (q) params.set("q", q);
    if (agent) params.set("agent", agent);
    if (status) params.set("status", status);
    if (origin) params.set("origin", origin);
    params.set("limit", "80");
    const r = await api("GET", "/api/agent-runs?" + params.toString());
    renderRunsPane(r.ok ? (r.runs || []) : []);
}

function renderRunsPane(runs) {
    const wrap = document.getElementById("runs-wrap");
    const count = document.getElementById("runs-count");
    const root = document.getElementById("runs-pane");
    if (!root) return;
    if (count) count.textContent = String(runs.length);
    root.innerHTML = "";
    if (!runs.length) {
        root.append(el("div", { class: "dim" }, "(no matching runs)"));
        return;
    }
    for (const run of runs) {
        const dur = run.duration_s != null ? `${run.duration_s}s` : "";
        const tools = (run.tools_used || []).join(", ");
        root.append(el("div", { class: "run-row" },
            el("span", { class: `agent-status-badge ${runStatusClass(run.status)}` }, runStatusLabel(run.status)),
            el("div", {},
                el("div", {}, `${run.agent || "?"} · ${run.steps || 0} steps · ${dur}`),
                el("div", { class: "run-row-meta" }, run.prompt_preview || ""),
                run.message_preview ? el("div", { class: "run-row-meta" }, run.message_preview) : el("span"),
            ),
            el("div", { class: "run-row-meta" },
                run.finished_ts ? agentLastActivity(run.finished_ts) : "",
                tools ? ` · ${tools}` : "",
            ),
        ));
    }
}

function populateRunAgentFilter() {
    const sel = document.getElementById("runs-filter-agent");
    if (!sel) return;
    const current = sel.value;
    sel.innerHTML = "";
    sel.append(el("option", { value: "" }, "All agents"));
    for (const row of state.agents) {
        sel.append(el("option", { value: row.name }, row.name));
    }
    sel.value = current;
}

