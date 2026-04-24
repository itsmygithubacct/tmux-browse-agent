"""CLI defaults and overrides for ``tb agent``."""

import argparse
import sys
import unittest
from pathlib import Path
from unittest import mock

_REPO = Path(__file__).resolve().parents[3]
_EXT = _REPO / "extensions" / "agent"
for _p in (_REPO, _EXT):
    _s = str(_p)
    if _s not in sys.path:
        sys.path.insert(0, _s)

from tb_cmds import agent as agent_cmd  # noqa: E402


class AgentRunConfigTests(unittest.TestCase):

    def _args(self, *rest: str) -> argparse.Namespace:
        return argparse.Namespace(
            mode="minimax",
            rest=list(rest),
            json=False,
            quiet=True,
            no_header=False,
        )

    def test_uses_dashboard_default_steps_when_flag_omitted(self):
        with mock.patch("tb_cmds.agent.dashboard_config.load", return_value={"agent_max_steps": 20}), mock.patch(
            "tb_cmds.agent.agent_store.get_agent",
            return_value={"name": "minimax", "model": "MiniMax-M2.7", "api_key": "k", "wire_api": "openai-chat"},
        ), mock.patch(
            "tb_cmds.agent.agent_runner.run_agent",
            return_value={"message": "ok"},
        ) as run_agent:
            rc = agent_cmd.cmd_agent(self._args("check", "panes"))
        self.assertEqual(rc, 0)
        self.assertEqual(run_agent.call_args.kwargs["max_steps"], 20)

    def test_steps_flag_overrides_dashboard_default(self):
        with mock.patch("tb_cmds.agent.dashboard_config.load", return_value={"agent_max_steps": 20}), mock.patch(
            "tb_cmds.agent.agent_store.get_agent",
            return_value={"name": "minimax", "model": "MiniMax-M2.7", "api_key": "k", "wire_api": "openai-chat"},
        ), mock.patch(
            "tb_cmds.agent.agent_runner.run_agent",
            return_value={"message": "ok"},
        ) as run_agent:
            rc = agent_cmd.cmd_agent(self._args("--steps", "7", "check", "panes"))
        self.assertEqual(rc, 0)
        self.assertEqual(run_agent.call_args.kwargs["max_steps"], 7)


if __name__ == "__main__":
    unittest.main()
