# Changelog

## 0.7.1-agent — 2026-04-24

First release with a real compatibility contract. Pinned against
`tmux-browse >= 0.7.1` (the core release that lands the extension
loader, the submodule hook at `extensions/agent/`, and the opt-in
default). Earlier carves targeting v0.7.0.4 were cut before core had
`lib.extensions`; don't use them.

Contents match core's E1 end-state (no functional change from the
v0.7.0.4-agent carve):

- Agent CRUD + secrets store (host, worktree, docker sandboxes).
- Agent modes: `cycle` (plan-then-execute) and `work` (file-backed
  task queue). `drive` is deferred.
- Workflow scheduler (cron-like, single-process locked).
- Event hooks + conductor rule engine for cross-agent routing.
- Tool registry with `tb_command` and `read_file`, dispatched per
  agent's sandbox.
- HTTP surface at `/api/agent-*` (14 GET + 10 POST handlers).
- CLI verb `tb agent ...` (add, remove, repl, run, cycle, work,
  configs, REPL context, KB).
- Dashboard UI: Agent Settings config card, Agents / Runs / Tasks
  sections, transcript + workflow modals.

### CI

GitHub Actions checks out `itsmygithubacct/tmux-browse` at tag
`v0.7.1` into a sibling directory and runs the extension test
suite with both trees on `PYTHONPATH`. That's the same setup the
`README.md` walks users through for local runs.

### Prior history

`git log` in this repo reaches back through the core repo's E1
phases (the relocation of `lib/agent_*.py` into `extensions/agent/`).
Anything older than those commits — the original
`lib/agent_*.py` authoring — lives in the core repo's history under
`git log --follow lib/agent_*.py` up to its v0.7.0.4 tag.
