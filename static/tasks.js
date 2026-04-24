// tasks.js — task CRUD

// --- Tasks ---

async function loadTasks() {
    const r = await api("GET", "/api/tasks");
    if (!r.ok) return;
    renderTasksPane(r.tasks || []);
}

function renderTasksPane(tasks) {
    const count = document.getElementById("tasks-count");
    const root = document.getElementById("tasks-pane");
    if (!root) return;
    if (count) count.textContent = String(tasks.length);
    root.innerHTML = "";
    if (!tasks.length) {
        root.append(el("div", { class: "dim" }, "(no tasks)"));
        return;
    }
    for (const t of tasks) {
        const statusCls = `task-status-${t.status || "open"}`;
        root.append(el("div", { class: "run-row" },
            el("span", { class: statusCls, style: "font-weight:700;font-size:0.82rem" },
                (t.status || "open").toUpperCase()),
            el("div", {},
                el("div", {}, `${t.title || "untitled"}`),
                el("div", { class: "run-row-meta" },
                    [
                        t.agent ? `agent: ${t.agent}` : null,
                        t.worktree_path ? `worktree` : null,
                        t.repo_path || null,
                    ].filter(Boolean).join(" · "),
                ),
            ),
            el("div", { class: "agent-card-actions" },
                t.agent && t.status === "open"
                    ? el("button", { class: "btn green", type: "button",
                          onclick: () => launchTask(t.id) }, "Launch")
                    : el("span"),
                t.status === "open"
                    ? el("button", { class: "btn", type: "button",
                          onclick: () => markTaskDone(t.id) }, "Done")
                    : el("span"),
            ),
        ));
    }
}

async function createTask() {
    const title = document.getElementById("task-title").value.trim();
    const repo = document.getElementById("task-repo").value.trim();
    const agent = document.getElementById("task-agent").value;
    if (!title || !repo) return;
    const r = await api("POST", "/api/tasks", { title, repo_path: repo, agent: agent || null });
    if (r.ok) {
        document.getElementById("task-title").value = "";
        document.getElementById("task-repo").value = "";
        await loadTasks();
    }
}

async function launchTask(id) {
    const r = await api("POST", "/api/tasks/launch", { id });
    if (r.ok && r.port) window.open(ttydUrl(r.port), "_blank", "noopener");
    await refresh();
}

async function markTaskDone(id) {
    await api("POST", "/api/tasks/update", { id, status: "done" });
    await loadTasks();
}

function populateTaskAgentSelect() {
    const sel = document.getElementById("task-agent");
    if (!sel) return;
    const current = sel.value;
    sel.innerHTML = "";
    sel.append(el("option", { value: "" }, "No agent"));
    for (const row of state.agents) {
        sel.append(el("option", { value: row.name }, row.name));
    }
    sel.value = current;
}

