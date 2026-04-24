"""The ``agent`` module — tmux-browse's agent platform.

Historically lived under ``lib/agent_*`` in the core repo; relocated
here in the 0.7.0.5 line as part of the extension-split program
(see ``~/research/tmux-browse/plans/plan_split_e1_relocate.md``).

Core primitives this package depends on are re-exported through
:mod:`agent.core_api` — that boundary is the one contract we keep
stable when the module eventually moves to its own git repository.
"""
