"""LLM-backed ``tb agent`` execution loop."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import (
    budgets as agent_budgets,
    costs as agent_costs,
    hooks as agent_hooks,
    logs as agent_logs,
    providers as agent_providers,
    run_index as agent_run_index,
    tool_registry as agent_tool_registry,
)
from lib import docker_sandbox
from .runs import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_RATE_LIMITED,
    STATUS_STARTED,
    new_run_id,
)
from lib.errors import TmuxFailed, UsageError


SYSTEM_PROMPT = """You are a tmux operations agent embedded in tb.py.

You do not have shell access directly. You have exactly one tool:
`tb_command`.

Use it to run non-interactive tb.py commands and inspect their results.
Prefer JSON output by asking for commands that can sensibly include `--json`.

Important rules:
- Never ask for confirmation. Keep working until the task is complete or blocked.
- Never recurse into `tb agent`.
- Start narrow: prefer `ls`, `show`, `describe`, and targeted `capture` before `snapshot`.
- Use `snapshot` only when you truly need whole-machine state across many sessions.
- Keep captures tight: ask for the fewest lines that answer the question.
- For commands inside panes, use `tb exec TARGET --timeout ... -- <command>`.
- Keep command count efficient. Read state first, then act, then verify.
- When the task is complete, return a concise final message describing what you did and any remaining issue.

You must respond with JSON only, one object per turn, in one of these shapes:
{"type":"tool","tool":"tb_command","args":["snapshot","--json"],"stdin":""}
{"type":"final","message":"done"}
"""


DOCKER_SANDBOX_PROMPT = """

---

You are operating inside an isolated Docker sandbox.
The only tmux target available is `sandbox:`.
Do not reference host session names.
Examples:
  - tb exec sandbox: -- ls /workspace
  - tb read sandbox:
  - tb capture sandbox:
"""


def _preview(text: str, head: int = 160, tail: int = 80) -> str:
    raw = text.strip()
    if len(raw) <= head + tail + 5:
        return raw
    return raw[:head] + " ... " + raw[-tail:]


def _trim_text(text: str, limit: int = 2400) -> str:
    raw = (text or "").strip()
    if len(raw) <= limit:
        return raw
    head = max(200, limit // 2)
    tail = max(120, limit - head - 32)
    return raw[:head] + "\n... [truncated] ...\n" + raw[-tail:]


def _compact_json_envelope(parsed: Any) -> Any:
    if not isinstance(parsed, dict):
        return parsed
    payload = parsed.get("data", parsed)
    if not isinstance(payload, dict):
        return payload

    if {"sessions", "panes", "ttyd", "dashboard"} <= set(payload):
        sessions = payload.get("sessions") or []
        panes = payload.get("panes") or []
        ttyd = payload.get("ttyd") or {}
        running = ttyd.get("running") or []
        return {
            "kind": "snapshot-summary",
            "session_count": len(sessions),
            "pane_count": len(panes),
            "sessions": [
                {
                    "name": row.get("name"),
                    "windows": row.get("windows"),
                    "attached": row.get("attached"),
                }
                for row in sessions[:8]
                if isinstance(row, dict)
            ],
            "running_ttyd": sum(1 for row in running if isinstance(row, dict) and row.get("running")),
            "dashboard": payload.get("dashboard"),
        }

    if "session" in payload and "panes" in payload:
        panes = payload.get("panes") or []
        session = payload.get("session") or {}
        return {
            "kind": "show-summary",
            "session": {
                "name": session.get("name"),
                "windows": session.get("windows"),
                "attached": session.get("attached"),
            } if isinstance(session, dict) else session,
            "pane_count": len(panes),
            "panes": [
                {
                    "window": row.get("window"),
                    "pane": row.get("pane"),
                    "command": row.get("command"),
                    "cwd": row.get("cwd"),
                    "active": row.get("active"),
                }
                for row in panes[:6]
                if isinstance(row, dict)
            ],
        }

    if "content" in payload and isinstance(payload.get("content"), str):
        return {
            "kind": "content-preview",
            "target": payload.get("target"),
            "lines": payload.get("lines"),
            "content_preview": _trim_text(payload.get("content") or "", limit=1600),
        }

    if "output" in payload and isinstance(payload.get("output"), str):
        return {
            "kind": "exec-result",
            "strategy": payload.get("strategy"),
            "exit_status": payload.get("exit_status"),
            "duration": payload.get("duration"),
            "output_preview": _trim_text(payload.get("output") or "", limit=1600),
        }

    return payload


def _compact_tool_payload(result: ToolResult) -> dict[str, Any]:
    return {
        "ok": result.ok,
        "exit_code": result.exit_code,
        "stdout_preview": _trim_text(result.stdout, limit=1800),
        "stderr_preview": _trim_text(result.stderr, limit=700),
        "json_summary": _compact_json_envelope(result.json_data),
    }


def _extract_json(text: str) -> dict[str, Any]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:].strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end < start:
        raise UsageError(f"agent returned non-JSON output (preview): {_preview(text)!r}")
    try:
        data = json.loads(raw[start:end + 1])
    except ValueError as e:
        raise UsageError(f"agent returned invalid JSON: {e}; preview={_preview(raw[start:end + 1])!r}")
    if not isinstance(data, dict):
        raise UsageError("agent response must be a JSON object")
    return data


# Provider dispatch lives in agent_providers — one adapter per wire API.


@dataclass
class ToolResult:
    ok: bool
    command: list[str]
    exit_code: int
    stdout: str
    stderr: str
    json_data: Any = None


def _run_tb_command(repo_root: Path, args: list[str], stdin_text: str | None) -> ToolResult:
    if not args:
        raise UsageError("tb_command args must not be empty")
    verb = args[0]
    if verb in {"agent", "attach", "watch"}:
        raise UsageError(f"`tb {verb}` is not allowed from tb agent")
    cmd = [sys.executable, str(repo_root / "tb.py"), verb, "--json", *args[1:]]
    proc = subprocess.run(
        cmd,
        input=stdin_text if stdin_text is not None else None,
        text=True,
        capture_output=True,
        cwd=str(repo_root),
    )
    parsed = None
    stdout = proc.stdout or ""
    if stdout.strip():
        try:
            parsed = json.loads(stdout)
        except ValueError:
            parsed = None
    return ToolResult(
        ok=proc.returncode == 0,
        command=cmd,
        exit_code=proc.returncode,
        stdout=stdout,
        stderr=proc.stderr or "",
        json_data=parsed,
    )


def _detect_rate_limit(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "429" in msg or "rate limit" in msg or "rate_limit" in msg


def run_agent(agent: dict[str, Any], prompt: str, *,
              repo_root: Path,
              max_steps: int = 20,
              request_timeout: float = 90.0,
              origin: str = "cli",
              run_id: str | None = None,
              conversation_messages: list[dict[str, str]] | None = None,
              sandbox_spec: dict[str, Any] | None = None,
              repl_context: dict[str, Any] | None = None,
              ) -> dict[str, Any]:
    if not prompt.strip():
        raise UsageError("missing agent prompt")
    if run_id is None:
        run_id = new_run_id()

    system_prompt = SYSTEM_PROMPT
    if sandbox_spec and sandbox_spec.get("mode") == "docker":
        system_prompt = system_prompt + DOCKER_SANDBOX_PROMPT

    # REPL context + knowledge base prefixes. Both come straight from
    # static files / a JSON blob; neither reaches the tool loop, only
    # the model's system prompt.
    if repl_context:
        try:
            from . import repl_context
            system_prompt = system_prompt + agent_repl_context.render_block(repl_context)
        except Exception:
            pass
    agent_name = agent.get("name") or ""
    if agent_name:
        try:
            from . import kb
            system_prompt = system_prompt + agent_kb.render_block(agent_name)
        except Exception:
            pass

    # Enabled tool set. Default ["tb_command"] keeps old agents bit-identical.
    enabled_tools = agent_tool_registry.tool_names_for_agent(agent)
    if enabled_tools != ["tb_command"]:
        system_prompt = system_prompt + "\n---\n\n" \
            + agent_tool_registry.tool_prompt_block(enabled_tools)

    messages: list[dict[str, str]] = [
        {"role": "system", "content": system_prompt},
    ]
    # Inject prior conversation context before the current prompt.
    if conversation_messages:
        messages.extend(conversation_messages)
    messages.append({"role": "user", "content": prompt.strip()})

    transcript: list[dict[str, Any]] = []
    cumulative_usage: dict[str, int] = {}
    started_ts = int(time.time())

    agent_logs.append_entry(agent["name"], {
        "run_id": run_id,
        "status": STATUS_STARTED,
        "origin": origin,
        "prompt": prompt.strip(),
        "model": agent.get("model"),
        "provider": agent.get("provider"),
        "wire_api": agent.get("wire_api"),
    })

    sandbox = None
    try:
        if sandbox_spec and sandbox_spec.get("mode") == "docker":
            sandbox = docker_sandbox.Sandbox(
                agent_name=agent["name"],
                run_id=run_id,
                workspace=Path(sandbox_spec.get("workspace") or repo_root),
                repo_root=repo_root,
            )
            sandbox.create()

        for step in range(1, max_steps + 1):
            result = agent_providers.complete(agent, messages, timeout=request_timeout)
            raw = result.content
            if result.usage:
                for key, val in result.usage.items():
                    if isinstance(val, (int, float)):
                        cumulative_usage[key] = cumulative_usage.get(key, 0) + int(val)
            # Per-run budget enforcement
            run_budget = int(agent.get("token_budget") or 0)
            if run_budget > 0:
                budget_check = agent_budgets.check_run_budget(
                    agent["name"], cumulative_usage, run_budget)
                if budget_check["action"] == agent_budgets.ACTION_STOP:
                    agent_hooks.execute(
                        "budget_exceeded", agent["name"],
                        run_id=run_id, prompt=prompt,
                        error=budget_check["reason"])
                    raise TmuxFailed(budget_check["reason"])
            try:
                action = _extract_json(raw)
            except UsageError as e:
                transcript.append({"step": step, "model": raw, "parse_error": e.message})
                messages.append({"role": "assistant", "content": raw})
                messages.append({
                    "role": "user",
                    "content": (
                        "Your previous response did not follow the required protocol. "
                        "Respond again with JSON only, using exactly one object in one of the allowed shapes. "
                        "Do not include prose, markdown fences, or <think> tags."
                    ),
                })
                continue
            transcript.append({"step": step, "model": raw, "action": action})
            if action.get("type") == "final":
                out = {
                    "agent": agent["name"],
                    "model": agent["model"],
                    "run_id": run_id,
                    "steps": step,
                    "message": str(action.get("message") or "").strip(),
                    "transcript": transcript,
                    "usage": cumulative_usage,
                }
                agent_logs.append_entry(agent["name"], {
                    "run_id": run_id,
                    "origin": origin,
                    "status": STATUS_COMPLETED,
                    "prompt": prompt.strip(),
                    "message": out["message"],
                    "steps": step,
                    "model": agent.get("model"),
                    "transcript": transcript,
                    "usage": cumulative_usage,
                })
                agent_run_index.append(
                    run_id=run_id, agent=agent["name"],
                    status=STATUS_COMPLETED, started_ts=started_ts,
                    steps=step, prompt=prompt, message=out["message"],
                    origin=origin, model=agent.get("model", ""),
                    transcript=transcript,
                )
                if cumulative_usage:
                    agent_costs.record(
                        run_id=run_id, agent=agent["name"],
                        model=agent.get("model", ""),
                        usage=cumulative_usage, origin=origin,
                    )
                agent_hooks.execute(
                    "run_completed", agent["name"],
                    run_id=run_id, prompt=prompt)
                return out
            if action.get("type") != "tool":
                raise UsageError("agent must return either a final action or a tool action")
            tool_name = action.get("tool") or ""
            if tool_name not in enabled_tools:
                raise UsageError(
                    f"tool {tool_name!r} is not enabled for this agent; "
                    f"enabled: {enabled_tools}")
            tool_args = action.get("args")
            stdin_text = action.get("stdin")
            if stdin_text is not None and not isinstance(stdin_text, str):
                raise UsageError("tool stdin must be a string when present")
            if tool_name == "tb_command":
                # Historical contract: list-of-strings args.
                if not isinstance(tool_args, list) or not all(isinstance(x, str) for x in tool_args):
                    raise UsageError("tb_command args must be a list of strings")
                if sandbox is not None:
                    tool_result = sandbox.exec_tb(tool_args, stdin_text, timeout=60)
                else:
                    tool_result = _run_tb_command(repo_root, tool_args, stdin_text)
            else:
                # Registry dispatch for non-tb_command tools.
                spec = agent_tool_registry.TOOLS.get(tool_name)
                if spec is None:
                    raise UsageError(f"unknown tool: {tool_name!r}")
                if sandbox is not None:
                    if spec.run_sandbox is None:
                        raise UsageError(
                            f"tool {tool_name!r} is not available in Docker mode")
                    tool_result = spec.run_sandbox(sandbox, tool_args, stdin_text)
                else:
                    tool_result = spec.run_host(repo_root, tool_args, stdin_text)
            tool_payload = _compact_tool_payload(tool_result)
            transcript[-1]["tool_result"] = tool_payload
            messages.append({"role": "assistant", "content": json.dumps(action)})
            messages.append({
                "role": "user",
                "content": f"Tool result for {tool_name}:\n" + json.dumps(tool_payload, ensure_ascii=True),
            })
        raise TmuxFailed(f"agent exceeded max steps ({max_steps})")
    except Exception as e:
        status = STATUS_RATE_LIMITED if _detect_rate_limit(e) else STATUS_FAILED
        agent_logs.append_entry(agent["name"], {
            "run_id": run_id,
            "origin": origin,
            "status": status,
            "prompt": prompt.strip(),
            "error": str(e),
            "model": agent.get("model"),
            "transcript": transcript,
            "usage": cumulative_usage,
        })
        agent_run_index.append(
            run_id=run_id, agent=agent["name"],
            status=status, started_ts=started_ts,
            steps=len(transcript), prompt=prompt, error=str(e),
            origin=origin, model=agent.get("model", ""),
            transcript=transcript,
        )
        if cumulative_usage:
            agent_costs.record(
                run_id=run_id, agent=agent["name"],
                model=agent.get("model", ""),
                usage=cumulative_usage, origin=origin,
            )
        event_type = "run_rate_limited" if status == STATUS_RATE_LIMITED else "run_failed"
        hook_actions = agent_hooks.execute(
            event_type, agent["name"],
            run_id=run_id, prompt=prompt, error=str(e))
        if "retry" in hook_actions and origin != "retry":
            import threading
            def _delayed_retry():
                time.sleep(30)
                try:
                    run_agent(agent, prompt, repo_root=repo_root,
                              max_steps=max_steps, request_timeout=request_timeout,
                              origin="retry")
                except Exception:
                    pass
            threading.Thread(target=_delayed_retry, daemon=True).start()
        raise
    finally:
        if sandbox is not None:
            sandbox.close()
