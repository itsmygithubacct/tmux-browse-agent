# tmux-browse-agent

Optional agent platform for [tmux-browse](https://github.com/itsmygithubacct/tmux-browse).
Ships the HTTP handlers, CLI verbs (`tb agent ...`), scheduler, conductor,
and UI blocks that turn the tmux dashboard into a workbench for driving
LLM agents against the sessions it's already showing.

This lives in its own repo because most tmux-browse users don't want
agents running on their machine, and carrying the surface as dead code
in core would grow a lean stdlib-only dashboard into something it
shouldn't be. If you do want agents, this is what you want.

## What's in here

- `agent/` — the Python package (`import agent`). Modules own runs,
  costs, hooks, workflows, the conductor rule engine, agent modes
  (`cycle` / `work`), the tool registry, and the REPL/KB plumbing.
- `server/routes.py` — all `/api/agent-*` HTTP handlers, as free
  functions that the core dashboard's extension loader merges into
  its route table.
- `tb_cmds/agent.py` — the `tb agent ...` CLI verb.
- `ui_blocks.html` — agent HTML slots (Agent Settings config card,
  Agents / Runs / Tasks sections, transcript + workflow modals)
  that core's template dropins into its `<!--slot:name-->` markers.
- `static/` — agent-only JS (`agents.js`, `runs.js`, `tasks.js`).
- `startup.py` — scheduler lifecycle hooked into core via the
  extension loader protocol.
- `manifest.json` — what core reads to wire everything up.

## Installation

The expected layout is as a git submodule inside a tmux-browse
checkout:

```bash
git clone https://github.com/itsmygithubacct/tmux-browse.git
cd tmux-browse
git submodule add https://github.com/itsmygithubacct/tmux-browse-agent.git extensions/agent
git submodule update --init
```

After `git clone --recursive` does the same work in one step. The
Config pane in the running dashboard has an **Enable** button for
the Agents module — until you click it, the extension is on disk
but not wired up.

Core's version-compat pin lives in `manifest.json` as
`min_tmux_browse`. If your core is older than the pin, the loader
refuses to enable the extension and surfaces the version mismatch
in the Config pane.

## Running the tests

The test suite imports from core's `lib/` through the
`agent.core_api` boundary module. CI checks out core at the
version this extension targets and points `PYTHONPATH` at both:

```bash
git clone https://github.com/itsmygithubacct/tmux-browse.git ../core
PYTHONPATH=$(pwd)/../core:$(pwd) python3 -m unittest discover tests
```

Everything under `tests/` is agent-internal — it doesn't boot the
dashboard. Core's integration test
(`tests/test_extension_agent_lifecycle.py` in tmux-browse) is what
proves load/unload works end-to-end against the submodule.

## The `agent.core_api` boundary

Everything the extension imports from core goes through
`agent/core_api.py`. That's the contract. If core rearranges its
internals, as long as `core_api` still re-exports the same names,
this extension keeps working. When it doesn't, the version pin in
`manifest.json` is what tells the loader to refuse to run.

This is the one file to touch when a core API changes. Any
`from lib.something import X` elsewhere in the tree is a bug.

## Security note

This extension runs language models over API keys stored locally
and executes their tool calls against your tmux sessions. Sandboxing
is per-agent (`host` / `worktree` / `docker`, set per-agent in the
Config pane). Don't enable it on a machine you're not OK running
model-directed commands on. The conductor rule engine has loop
guards but the sandbox boundary is what actually protects you.

## License

Same as tmux-browse core.
