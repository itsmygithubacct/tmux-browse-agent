"""Agent runner JSON extraction."""

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

from agent import runner as agent_runner  # noqa: E402
from agent.providers import ProviderResult  # noqa: E402
from lib.errors import UsageError  # noqa: E402


class ExtractJsonTests(unittest.TestCase):

    def test_extracts_json_after_think_preamble(self):
        text = (
            "<think>I should inspect tmux state first.</think>\n\n"
            '{"type":"tool","tool":"tb_command","args":["snapshot","--json"],"stdin":""}'
        )
        data = agent_runner._extract_json(text)
        self.assertEqual(data["type"], "tool")
        self.assertEqual(data["tool"], "tb_command")

    def test_non_json_error_uses_preview_label(self):
        with self.assertRaises(UsageError) as ctx:
            agent_runner._extract_json("hello there")
        self.assertIn("preview", str(ctx.exception))

    def test_run_agent_repairs_non_json_reply(self):
        replies = iter([
            ProviderResult(content="Here's what I found in the panes."),
            ProviderResult(content='{"type":"final","message":"done"}'),
        ])
        with mock.patch("agent.runner.agent_providers.complete", side_effect=lambda *a, **k: next(replies)), mock.patch(
            "agent.runner.agent_logs.append_entry",
        ) as append_entry:
            result = agent_runner.run_agent(
                {"name": "minimax", "model": "MiniMax-M2.7", "wire_api": "openai-chat"},
                "check panes",
                repo_root=Path("/tmp"),
                max_steps=3,
                request_timeout=1.0,
            )
        self.assertEqual(result["message"], "done")
        self.assertEqual(result["steps"], 2)
        self.assertIn("run_id", result)
        self.assertIn("parse_error", result["transcript"][0])
        # 3 log entries: run_started, run_completed (parse_error step doesn't get its own entry)
        self.assertEqual(append_entry.call_count, 2)
        self.assertEqual(append_entry.call_args_list[0].args[0], "minimax")
        self.assertEqual(append_entry.call_args_list[0].args[1]["status"], "run_started")
        self.assertEqual(append_entry.call_args_list[1].args[1]["status"], "run_completed")

    def test_logs_error_run(self):
        with mock.patch("agent.runner.agent_providers.complete", side_effect=UsageError("bad response")), mock.patch(
            "agent.runner.agent_logs.append_entry",
        ) as append_entry:
            with self.assertRaises(UsageError):
                agent_runner.run_agent(
                    {"name": "gpt", "model": "gpt-5.4", "wire_api": "openai-chat"},
                    "check panes",
                    repo_root=Path("/tmp"),
                    max_steps=3,
                    request_timeout=1.0,
                )
        # 2 log entries: run_started, then run_failed
        self.assertEqual(append_entry.call_count, 2)
        self.assertEqual(append_entry.call_args_list[0].args[1]["status"], "run_started")
        self.assertEqual(append_entry.call_args_list[1].args[0], "gpt")
        self.assertEqual(append_entry.call_args_list[1].args[1]["status"], "run_failed")

    def test_compact_snapshot_payload(self):
        payload = agent_runner._compact_json_envelope({
            "ok": True,
            "data": {
                "sessions": [{"name": "a", "windows": 1, "attached": 0}] * 10,
                "panes": [{"session": "a"}] * 20,
                "ttyd": {"running": [{"running": True}, {"running": False}]},
                "dashboard": {"listening": True},
            },
        })
        self.assertEqual(payload["kind"], "snapshot-summary")
        self.assertEqual(payload["session_count"], 10)
        self.assertEqual(len(payload["sessions"]), 8)

    def test_compact_content_payload(self):
        payload = agent_runner._compact_json_envelope({
            "ok": True,
            "data": {
                "target": "work",
                "lines": 2000,
                "content": "x" * 3000,
            },
        })
        self.assertEqual(payload["kind"], "content-preview")
        self.assertIn("[truncated]", payload["content_preview"])


class SandboxIntegrationTests(unittest.TestCase):
    """run_agent owns sandbox lifecycle and routes Docker tool calls."""

    def _stub_agent(self):
        return {"name": "opus", "model": "claude-opus-4-7", "wire_api": "openai-chat"}

    def test_docker_mode_appends_prompt_suffix(self):
        captured = {}

        def fake_complete(agent, messages, **_):
            captured["system"] = messages[0]["content"]
            return ProviderResult(content='{"type":"final","message":"done"}')

        fake_sandbox = mock.Mock()
        fake_sandbox.exec_tb.return_value = mock.Mock(
            ok=True, exit_code=0, stdout="", stderr="", json_data=None,
        )
        with mock.patch("agent.runner.agent_providers.complete",
                        side_effect=fake_complete), \
             mock.patch("agent.runner.agent_logs.append_entry"), \
             mock.patch("agent.runner.docker_sandbox.Sandbox",
                        return_value=fake_sandbox):
            agent_runner.run_agent(
                self._stub_agent(), "do work",
                repo_root=Path("/tmp"), max_steps=2, request_timeout=1.0,
                sandbox_spec={"mode": "docker", "workspace": "/tmp"},
            )
        self.assertIn("Docker sandbox", captured["system"])
        self.assertIn("sandbox:", captured["system"])

    def test_host_mode_does_not_append_docker_prompt(self):
        captured = {}

        def fake_complete(agent, messages, **_):
            captured["system"] = messages[0]["content"]
            return ProviderResult(content='{"type":"final","message":"done"}')

        with mock.patch("agent.runner.agent_providers.complete",
                        side_effect=fake_complete), \
             mock.patch("agent.runner.agent_logs.append_entry"):
            agent_runner.run_agent(
                self._stub_agent(), "do work",
                repo_root=Path("/tmp"), max_steps=2, request_timeout=1.0,
                sandbox_spec=None,
            )
        self.assertNotIn("Docker sandbox", captured["system"])

    def test_docker_mode_routes_tool_calls_through_exec_tb(self):
        replies = iter([
            ProviderResult(content='{"type":"tool","tool":"tb_command","args":["snapshot","--json"],"stdin":""}'),
            ProviderResult(content='{"type":"final","message":"done"}'),
        ])
        fake_sandbox = mock.Mock()
        fake_sandbox.exec_tb.return_value = mock.Mock(
            ok=True, exit_code=0, stdout='{"ok":true}', stderr="", json_data={"ok": True},
        )
        with mock.patch("agent.runner.agent_providers.complete",
                        side_effect=lambda *a, **k: next(replies)), \
             mock.patch("agent.runner.agent_logs.append_entry"), \
             mock.patch("agent.runner.docker_sandbox.Sandbox",
                        return_value=fake_sandbox), \
             mock.patch("agent.runner._run_tb_command") as host_run:
            agent_runner.run_agent(
                self._stub_agent(), "do work",
                repo_root=Path("/tmp"), max_steps=3, request_timeout=1.0,
                sandbox_spec={"mode": "docker", "workspace": "/tmp"},
            )
        fake_sandbox.create.assert_called_once()
        fake_sandbox.exec_tb.assert_called_once()
        fake_sandbox.close.assert_called_once()
        host_run.assert_not_called()

    def test_host_mode_uses_run_tb_command(self):
        replies = iter([
            ProviderResult(content='{"type":"tool","tool":"tb_command","args":["snapshot","--json"],"stdin":""}'),
            ProviderResult(content='{"type":"final","message":"done"}'),
        ])
        host_result = mock.Mock(ok=True, exit_code=0, stdout='{}', stderr="", json_data={})
        with mock.patch("agent.runner.agent_providers.complete",
                        side_effect=lambda *a, **k: next(replies)), \
             mock.patch("agent.runner.agent_logs.append_entry"), \
             mock.patch("agent.runner._run_tb_command",
                        return_value=host_result) as host_run, \
             mock.patch("agent.runner.docker_sandbox.Sandbox") as sandbox_cls:
            agent_runner.run_agent(
                self._stub_agent(), "do work",
                repo_root=Path("/tmp"), max_steps=3, request_timeout=1.0,
            )
        host_run.assert_called_once()
        sandbox_cls.assert_not_called()

    def test_sandbox_closes_on_loop_exception(self):
        fake_sandbox = mock.Mock()
        with mock.patch("agent.runner.agent_providers.complete",
                        side_effect=UsageError("boom")), \
             mock.patch("agent.runner.agent_logs.append_entry"), \
             mock.patch("agent.runner.docker_sandbox.Sandbox",
                        return_value=fake_sandbox):
            with self.assertRaises(UsageError):
                agent_runner.run_agent(
                    self._stub_agent(), "do work",
                    repo_root=Path("/tmp"), max_steps=2, request_timeout=1.0,
                    sandbox_spec={"mode": "docker", "workspace": "/tmp"},
                )
        fake_sandbox.close.assert_called_once()

    def test_sandbox_creation_failure_records_failed_run(self):
        fake_sandbox = mock.Mock()
        fake_sandbox.create.side_effect = RuntimeError("docker daemon down")
        with mock.patch("agent.runner.agent_logs.append_entry") as append, \
             mock.patch("agent.runner.docker_sandbox.Sandbox",
                        return_value=fake_sandbox):
            with self.assertRaises(RuntimeError):
                agent_runner.run_agent(
                    self._stub_agent(), "do work",
                    repo_root=Path("/tmp"), max_steps=2, request_timeout=1.0,
                    sandbox_spec={"mode": "docker", "workspace": "/tmp"},
                )
        # close still called even when create() raised, because finally runs
        fake_sandbox.close.assert_called_once()
        # And a failed-run log entry was written (no fallback to host)
        statuses = [c.args[1]["status"] for c in append.call_args_list]
        self.assertIn("run_started", statuses)
        self.assertIn("run_failed", statuses)


class ToolRegistryDispatchTests(unittest.TestCase):

    def _agent(self, tools):
        return {"name": "opus", "model": "m", "wire_api": "openai-chat",
                "tools": tools}

    def test_tb_command_stays_default(self):
        replies = iter([
            ProviderResult(content=(
                '{"type":"tool","tool":"tb_command",'
                '"args":["snapshot","--json"],"stdin":""}')),
            ProviderResult(content='{"type":"final","message":"done"}'),
        ])
        tool_host = mock.Mock(return_value=mock.Mock(
            ok=True, exit_code=0, stdout='{"ok":true}', stderr="",
            json_data={"ok": True}))
        with mock.patch("agent.runner.agent_providers.complete",
                        side_effect=lambda *a, **k: next(replies)), \
             mock.patch("agent.runner.agent_logs.append_entry"), \
             mock.patch("agent.runner._run_tb_command", tool_host):
            agent_runner.run_agent(
                self._agent(["tb_command"]), "check",
                repo_root=Path("/tmp"), max_steps=3, request_timeout=1.0)
        tool_host.assert_called_once()

    def test_read_file_dispatches_through_registry(self):
        replies = iter([
            ProviderResult(content=(
                '{"type":"tool","tool":"read_file",'
                '"args":{"path":"/tmp/x","max_bytes":10},"stdin":""}')),
            ProviderResult(content='{"type":"final","message":"done"}'),
        ])
        fake_host = mock.Mock(return_value=mock.Mock(
            ok=True, exit_code=0, stdout="content", stderr="",
            json_data=None))
        with mock.patch("agent.runner.agent_providers.complete",
                        side_effect=lambda *a, **k: next(replies)), \
             mock.patch("agent.runner.agent_logs.append_entry"), \
             mock.patch.dict(
                 "agent.runner.agent_tool_registry.TOOLS",
                 {"read_file": agent_runner.agent_tool_registry.ToolSpec(
                     name="read_file", description="",
                     run_host=fake_host, run_sandbox=None)}):
            agent_runner.run_agent(
                self._agent(["tb_command", "read_file"]), "read something",
                repo_root=Path("/tmp"), max_steps=3, request_timeout=1.0)
        fake_host.assert_called_once()

    def test_disabled_tool_is_rejected(self):
        replies = iter([
            ProviderResult(content=(
                '{"type":"tool","tool":"read_file",'
                '"args":{"path":"/tmp/x"},"stdin":""}')),
            ProviderResult(content='{"type":"final","message":"done"}'),
        ])
        with mock.patch("agent.runner.agent_providers.complete",
                        side_effect=lambda *a, **k: next(replies)), \
             mock.patch("agent.runner.agent_logs.append_entry"):
            with self.assertRaises(UsageError) as ctx:
                agent_runner.run_agent(
                    self._agent(["tb_command"]), "do it",
                    repo_root=Path("/tmp"), max_steps=3, request_timeout=1.0)
        self.assertIn("not enabled", str(ctx.exception))

    def test_tools_block_appended_when_multiple_tools(self):
        captured = {}

        def fake_complete(agent, messages, **_):
            captured["system"] = messages[0]["content"]
            return ProviderResult(content='{"type":"final","message":"done"}')

        with mock.patch("agent.runner.agent_providers.complete",
                        side_effect=fake_complete), \
             mock.patch("agent.runner.agent_logs.append_entry"):
            agent_runner.run_agent(
                self._agent(["tb_command", "read_file"]), "x",
                repo_root=Path("/tmp"), max_steps=2, request_timeout=1.0)
        self.assertIn("Enabled tools:", captured["system"])
        self.assertIn("read_file", captured["system"])


if __name__ == "__main__":
    unittest.main()
