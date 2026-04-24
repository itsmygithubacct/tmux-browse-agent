# Changelog

## Unreleased

- Extracted from `tmux-browse` core at v0.7.0.4 into its own
  repository. Pre-split history lives in the [core
  repo](https://github.com/itsmygithubacct/tmux-browse) under
  `lib/agent_*.py` — `git log` in this repo reaches back to the
  E1 phase where the relocation landed.

## v0.7.0.4-agent — 2026-04-24

Initial carve. Matches `tmux-browse` core v0.7.0.4. Contents are
the E1 end-state from core:

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

Compatible with `tmux-browse >= 0.7.0.4`. Declared in
`manifest.json` as `min_tmux_browse`.
