# Changelog

## 0.7.3-agent — 2026-04-28

CLI agent breadth (K-phase). Adds a second surface alongside the
existing wire-API catalog: a registry of external CLI agents
(claude-code, codex, opencode, plus seven more) spawned in tmux
sessions tagged `agent-cli-<binary>-<uid>`. CLI sessions share the
dashboard's tmux plumbing — idle alerts, hot buttons, send-bar — and
get their status from either settings.json hooks (claude/cursor/gemini)
or per-CLI tmux pane parsing.

Pinned against `tmux-browse >= 0.7.1.3` (no core changes required;
core's `min_tmux_browse` field is unchanged from 0.7.2-agent).

### New

- `agent.cli_registry` — `CliAgentDef` dataclass + 10 built-in entries
  (claude, opencode, codex, vibe, gemini, cursor, copilot, pi, droid,
  settl). User overrides via `~/.tmux-browse/agent-cli-registry.json`.
- `agent.cli_detect` — per-CLI status detectors (`running`/`waiting`/
  `idle`) ported from agent-of-empires patterns. Dispatcher strips
  ANSI before matching. `detect_for_session(name)` reads
  `/tmp/tba-hooks/<id>/status` first and falls back to capture-pane.
- `agent.cli_launch` — `launch(name)` spawns the binary in a fresh
  tmux session, returning `(session, instance_id, ...)` or
  `install_required` with the install hint when the binary is missing.
  Codex's 120ms paste-burst window is honored by an Enter delay.
- `agent.cli_hooks` — idempotent installer for status hooks in the
  agent's settings.json. Marker substring `tba-hooks` identifies our
  entries; user-defined hooks alongside ours are preserved.
- HTTP: `GET /api/agent-cli`, `POST /api/agent-cli/launch`,
  `POST /api/agent-cli/install-hooks`, `POST /api/agent-cli/uninstall-hooks`.
- CLI: `tb agent launch <name> [--cwd] [--instruction] [--yolo]`.
- Dashboard: Launch CLI Agent config card with installed-CLIs dropdown
  plus install-hint surface for missing ones.

### Changed

- `agent.runtime.agent_name_from_session` now also recognises the
  `agent-cli-` prefix and resolves the canonical registry name.

### Tests

- 97 new unit tests (registry shape, override merge, all 10 detectors,
  launch flow, hooks round-trip, runtime prefix, server route shape,
  ANSI dispatch). Total at 418.

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
