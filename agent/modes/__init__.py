"""Agent-level long-running modes: cycle, work, drive.

Each mode is a thin orchestrator above :func:`agent_runner.run_agent`.
Modes do not duplicate scheduler / run-index / conversation
infrastructure — they reuse it by calling ``run_agent`` and letting the
existing pipelines record runs, costs, and status.

"Mode" here is agent-level and distinct from the REPL-level
``observe | act | watch`` *stance* managed by
:mod:`agent_repl_context`. The two are orthogonal and both can apply
simultaneously.
"""
