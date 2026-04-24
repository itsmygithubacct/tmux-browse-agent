"""HTTP routes for the agent extension.

All ``/api/agent-*`` + ``/api/agent-conductor-*`` handlers that
used to live as methods on ``lib.server.Handler`` live here now,
as plain functions taking ``handler`` as their first argument.
The core loader's extension registry maps them onto incoming
requests and calls them with ``handler, parsed_url[, body]``.

Nothing in this file does its own dispatch — it is a registration
surface plus handler bodies cut verbatim from the old core. The
``register()`` entry point returns the dict the core loader
merges into ``_GET_ROUTES`` / ``_POST_ROUTES``.
"""

from __future__ import annotations

import json
import shlex
import sys
import threading
from pathlib import Path
from urllib.parse import ParseResult, parse_qs

from agent import (
    budgets as agent_budgets,
    conductor as agent_conductor,
    costs as agent_costs,
    hooks as agent_hooks,
    kb as agent_kb,
    logs as agent_logs,
    repl_context as agent_repl_context,
    run_index as agent_run_index,
    runner as agent_runner,
    runtime as agent_runtime,
    status as agent_status,
    store as agent_store,
    workflow_runs as agent_workflow_runs,
    workflows as agent_workflows,
)
from agent.modes import cycle as _cycle_mode, work as _work_mode
from lib import (
    config, docker_sandbox, sessions, ttyd,
)
from lib.errors import TBError, UsageError

from lib.extensions import Registration


def _h_agents_get(handler, _parsed: ParseResult) -> None:
    try:
        agents = agent_store.list_agents()
        statuses = agent_status.get_all_statuses()
        for row in agents:
            name = row.get("name", "")
            st = statuses.get(name)
            if st:
                row["status"] = st["status"]
                row["status_reason"] = st["reason"]
                row["last_activity_ts"] = st["last_ts"]
                row["mode"] = st.get("mode", "")
                row["mode_phase"] = st.get("mode_phase", "")
            budget = agent_budgets.get_budget_status(name)
            row["budget_status"] = budget["worst_action"]
            row["budget_daily"] = budget["daily"]
        handler._send_json({
            "ok": True,
            "agents": agents,
            "defaults": agent_store.catalog_rows(),
            "docker_supported": docker_sandbox.SUPPORTED,
            "paths": {
                "agents": str(agent_store.AGENTS_FILE),
                "secrets": str(agent_store.SECRETS_FILE),
                "logs": str(config.AGENT_LOG_DIR),
                "workflows": str(config.AGENT_WORKFLOWS_FILE),
            },
        })
    except TBError as e:
        handler._send_tb_error(e)


def _h_agent_log(handler, parsed: ParseResult) -> None:
    query = parse_qs(parsed.query)
    name = (query.get("name", [""])[0] or "").strip().lower()
    try:
        limit = int(query.get("limit", ["200"])[0])
    except ValueError:
        limit = 200
    limit = max(1, min(limit, 1000))
    if not name:
        handler._send_text("missing 'name' query parameter", status=400)
        return
    try:
        handler._send_text(agent_logs.render_text(name, limit=limit))
    except TBError as e:
        handler._send_tb_error(e)


def _h_agent_log_json(handler, parsed: ParseResult) -> None:
    query = parse_qs(parsed.query)
    name = (query.get("name", [""])[0] or "").strip().lower()
    try:
        limit = int(query.get("limit", ["20"])[0])
    except ValueError:
        limit = 20
    limit = max(1, min(limit, 100))
    if not name:
        handler._send_json({"ok": False, "error": "missing 'name' query parameter"}, status=400)
        return
    try:
        handler._send_json({
            "ok": True,
            "name": name,
            "entries": agent_logs.read_entries(name, limit=limit),
            "path": str(agent_logs.log_path(name)),
        })
    except TBError as e:
        handler._send_tb_error(e)


def _h_agent_workflows_get(handler, _parsed: ParseResult) -> None:
    try:
        handler._send_json({
            "ok": True,
            "config": agent_workflows.load(),
            "path": str(config.AGENT_WORKFLOWS_FILE),
        })
    except TBError as e:
        handler._send_tb_error(e)


def _h_agent_workflow_state(handler, _parsed: ParseResult) -> None:
    try:
        sched = getattr(handler.server, "scheduler", None)
        handler._send_json({
            "ok": True,
            "state": agent_workflow_runs.get_all_state(),
            "scheduler_running": sched.running if sched else False,
        })
    except TBError as e:
        handler._send_tb_error(e)


def _h_agent_workflow_runs(handler, parsed: ParseResult) -> None:
    query = parse_qs(parsed.query)
    try:
        limit = int(query.get("limit", ["50"])[0])
    except (ValueError, TypeError):
        limit = 50
    limit = max(1, min(500, limit))
    try:
        handler._send_json({
            "ok": True,
            "runs": agent_workflow_runs.read_runs(limit=limit),
        })
    except TBError as e:
        handler._send_tb_error(e)


def _h_agent_runs(handler, parsed: ParseResult) -> None:
    q = parse_qs(parsed.query)

    def _first(key: str) -> str | None:
        vals = q.get(key)
        if vals:
            return vals[0].strip() or None
        return None

    def _int(key: str, default: int | None = None) -> int | None:
        v = _first(key)
        if v is None:
            return default
        try:
            return int(v)
        except ValueError:
            return default

    try:
        rows = agent_run_index.query(
            agent=_first("agent"),
            status=_first("status"),
            since=_int("since"),
            until=_int("until"),
            text=_first("q"),
            tool=_first("tool"),
            origin=_first("origin"),
            limit=max(1, min(500, _int("limit", 50) or 50)),
        )
        handler._send_json({"ok": True, "runs": rows})
    except TBError as e:
        handler._send_tb_error(e)


def _h_agent_run(handler, parsed: ParseResult) -> None:
    q = parse_qs(parsed.query)
    run_id = (q.get("run_id", [""])[0] or "").strip()
    if not run_id:
        handler._send_json({"ok": False, "error": "missing 'run_id'"}, status=400)
        return
    row = agent_run_index.get_run(run_id)
    if row is None:
        handler._send_json({"ok": False, "error": "run not found"}, status=404)
        return
    handler._send_json({"ok": True, "run": row})


def _h_agent_hooks_get(handler, _parsed: ParseResult) -> None:
    handler._send_json({"ok": True, "hooks": agent_hooks.load()})


def _h_agent_notifications(handler, parsed: ParseResult) -> None:
    query = parse_qs(parsed.query)
    try:
        limit = int(query.get("limit", ["50"])[0])
    except (ValueError, TypeError):
        limit = 50
    handler._send_json({
        "ok": True,
        "notifications": agent_hooks.read_notifications(
            limit=max(1, min(200, limit))),
    })


def _h_agent_conductor_get(handler, _parsed: ParseResult) -> None:
    handler._send_json({
        "ok": True,
        "rules": agent_conductor.load_rules(),
    })


def _h_agent_conductor_events(handler, parsed: ParseResult) -> None:
    query = parse_qs(parsed.query)
    try:
        limit = int(query.get("limit", ["50"])[0])
    except (ValueError, TypeError):
        limit = 50
    agent = (query.get("agent", [""])[0] or "").strip()
    handler._send_json({
        "ok": True,
        "decisions": agent_conductor.read_decisions(
            limit=max(1, min(500, limit)), agent=agent),
    })


def _h_agent_repl_context(handler, parsed: ParseResult) -> None:
    q = parse_qs(parsed.query)
    name = (q.get("name", [""])[0] or "").strip().lower()
    if not name:
        handler._send_json({"ok": False, "error": "missing 'name'"}, status=400)
        return
    ctx = agent_repl_context.load(name)
    kb = agent_kb.list_files(name)
    handler._send_json({
        "ok": True,
        "context": ctx,
        "kb": kb,
        "kb_total_bytes": sum(f["size"] for f in kb),
        "kb_cap_bytes": agent_kb.TOTAL_BYTES_CAP,
    })


def _h_agent_costs(handler, parsed: ParseResult) -> None:
    q = parse_qs(parsed.query)

    def _first(key: str) -> str | None:
        vals = q.get(key)
        return vals[0].strip() if vals else None

    def _int(key: str) -> int | None:
        v = _first(key)
        if v is None:
            return None
        try:
            return int(v)
        except ValueError:
            return None

    try:
        cfg = dashboard_config.load()
        handler._send_json({
            "ok": True,
            "per_agent": agent_costs.per_agent_totals(
                since=_int("since"), until=_int("until")),
            "daily": agent_costs.daily_totals(
                since=_int("since"), until=_int("until")),
            "global_daily_budget": int(cfg.get("global_daily_token_budget") or 0),
        })
    except TBError as e:
        handler._send_tb_error(e)


def _h_agents_post(handler, _parsed: ParseResult, body: dict) -> None:
    if not handler._check_unlock():
        return
    payload = body.get("agent", body)
    name = (payload.get("name") or "").strip()
    api_key = payload.get("api_key")
    if not isinstance(api_key, str):
        api_key = None
    elif not api_key.strip():
        api_key = None

    def _optional_int(field: str) -> int | None:
        value = payload.get(field)
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            raise UsageError(f"{field} must be an integer")

    try:
        row = agent_store.save_agent(
            name,
            api_key=api_key,
            model=(payload.get("model") or "").strip() or None,
            base_url=(payload.get("base_url") or "").strip() or None,
            provider=(payload.get("provider") or "").strip() or None,
            wire_api=(payload.get("wire_api") or "").strip() or None,
            sandbox=(payload.get("sandbox") or "").strip() or None,
            token_budget=_optional_int("token_budget"),
            daily_token_budget=_optional_int("daily_token_budget"),
        )
    except TBError as e:
        handler._send_tb_error(e)
        return
    handler._send_json({"ok": True, "agent": row})


def _h_agents_remove(handler, _parsed: ParseResult, body: dict) -> None:
    if not handler._check_unlock():
        return
    name = (body.get("name") or "").strip()
    if not name:
        handler._send_json({"ok": False, "error": "missing 'name'"}, status=400)
        return
    try:
        removed = agent_store.remove_agent(name)
    except TBError as e:
        handler._send_tb_error(e)
        return
    handler._send_json({"ok": True, "removed": removed, "name": name})


def _h_agent_workflows_post(handler, _parsed: ParseResult, body: dict) -> None:
    if not handler._check_unlock():
        return
    payload = body.get("config", body)
    try:
        saved = agent_workflows.save(payload)
    except TBError as e:
        handler._send_tb_error(e)
        return
    handler._send_json({
        "ok": True,
        "config": saved,
        "path": str(config.AGENT_WORKFLOWS_FILE),
    })


def _h_agent_conversation_open(handler, _parsed: ParseResult, body: dict) -> None:
    agent_name = (body.get("name") or "").strip().lower()
    if not agent_name:
        handler._send_json({"ok": False, "error": "missing 'name'"}, status=400)
        return
    try:
        agent_store.get_agent(agent_name)
    except TBError as e:
        handler._send_tb_error(e)
        return
    session_name = agent_runtime.conversation_session_name(agent_name)
    if not sessions.exists(session_name):
        cmd = " ".join([
            shlex.quote(sys.executable),
            "-u",
            shlex.quote(str(config.PROJECT_DIR / "tb.py")),
            "agent",
            "repl",
            shlex.quote(agent_name),
        ])
        ok, err = sessions.new_session(session_name, cwd=str(config.PROJECT_DIR), cmd=cmd)
        if not ok:
            handler._send_json({"ok": False, "error": err}, status=400)
            return
    tls_paths = getattr(handler.server, "tls_paths", None)
    bind_addr = getattr(handler.server, "ttyd_bind_addr", None)
    ttyd_result = ttyd.start(session_name, tls_paths=tls_paths, bind_addr=bind_addr)
    if not ttyd_result.get("ok"):
        handler._send_json(ttyd_result, status=400)
        return
    handler._send_json({
        "ok": True,
        "agent": agent_name,
        "session": session_name,
        "port": ttyd_result.get("port"),
        "scheme": ttyd_result.get("scheme", "http"),
        "url": f"{ttyd_result.get('scheme', 'http')}://localhost:{ttyd_result.get('port')}/",
        "already": ttyd_result.get("already", False),
    })


def _h_agent_conversation_fork(handler, _parsed: ParseResult, body: dict) -> None:
    agent_name = (body.get("name") or "").strip().lower()
    if not agent_name:
        handler._send_json({"ok": False, "error": "missing 'name'"}, status=400)
        return
    try:
        new_cid = agent_runtime.fork_conversation(agent_name)
    except TBError as e:
        handler._send_tb_error(e)
        return
    # Launch a new REPL session with --fork
    session_name = agent_runtime.conversation_session_name(agent_name)
    fork_session = f"{session_name}-fork"
    if not sessions.exists(fork_session):
        cmd = " ".join([
            shlex.quote(sys.executable), "-u",
            shlex.quote(str(config.PROJECT_DIR / "tb.py")),
            "agent", "repl", "--fork", shlex.quote(agent_name),
        ])
        ok, err = sessions.new_session(fork_session, cwd=str(config.PROJECT_DIR), cmd=cmd)
        if not ok:
            handler._send_json({"ok": False, "error": err}, status=400)
            return
    tls_paths = getattr(handler.server, "tls_paths", None)
    bind_addr = getattr(handler.server, "ttyd_bind_addr", None)
    ttyd_result = ttyd.start(fork_session, tls_paths=tls_paths, bind_addr=bind_addr)
    handler._send_json({
        "ok": True,
        "agent": agent_name,
        "conversation_id": new_cid,
        "session": fork_session,
        "port": ttyd_result.get("port"),
    })


def _h_agent_hooks_post(handler, _parsed: ParseResult, body: dict) -> None:
    if not handler._check_unlock():
        return
    try:
        saved = agent_hooks.save(body.get("hooks", body))
        handler._send_json({"ok": True, "hooks": saved})
    except TBError as e:
        handler._send_tb_error(e)


def _h_agent_conductor_post(handler, _parsed: ParseResult, body: dict) -> None:
    if not handler._check_unlock():
        return
    payload = body.get("rules")
    if payload is None:
        payload = body
    try:
        saved = agent_conductor.save_rules(
            {"rules": payload} if isinstance(payload, list) else payload)
        handler._send_json({"ok": True, "rules": saved})
    except ValueError as e:
        handler._send_json({"ok": False, "error": str(e)}, status=400)


def _h_agent_cycle_post(handler, _parsed: ParseResult, body: dict) -> None:
    if not handler._check_unlock():
        return
    name = (body.get("name") or "").strip().lower()
    if not name:
        handler._send_json({"ok": False, "error": "missing 'name'"}, status=400)
        return
    try:
        agent = agent_store.get_agent(name)
    except TBError as e:
        handler._send_tb_error(e)
        return
    goal_text = (body.get("goal_text") or "").strip() or None
    goal_path = (body.get("goal") or "").strip() or None
    try:
        steps = int(body.get("steps") or 20)
    except (TypeError, ValueError):
        steps = 20
    # Run in a daemon thread so the HTTP request returns quickly.
    # The run itself is visible via the run index as the user reloads.
    result_holder: dict = {}
    def _go():
        try:
            result = _cycle_mode.run(
                agent, goal_path=goal_path, goal_text=goal_text,
                steps=max(1, steps))
            result_holder["result"] = result
        except Exception as e:
            result_holder["error"] = str(e)
    thread = threading.Thread(target=_go, daemon=True)
    thread.start()
    # Wait briefly for the plan phase so the caller gets the plan
    # summary; execute phase continues in the background.
    thread.join(timeout=2.0)
    if "result" in result_holder:
        handler._send_json({
            "ok": True, "finished": True,
            "plan_run_id": result_holder["result"].plan_run_id,
            "exec_run_id": result_holder["result"].exec_run_id,
            "plan": result_holder["result"].plan_message[:500],
        })
    elif "error" in result_holder:
        handler._send_json({"ok": False,
                         "error": result_holder["error"]}, status=500)
    else:
        handler._send_json({"ok": True, "finished": False,
                         "note": "cycle running in background"})


def _h_agent_work_post(handler, _parsed: ParseResult, body: dict) -> None:
    if not handler._check_unlock():
        return
    name = (body.get("name") or "").strip().lower()
    tasks_path = (body.get("tasks") or "").strip()
    if not name or not tasks_path:
        handler._send_json({"ok": False, "error": "missing 'name' or 'tasks'"},
                        status=400)
        return
    try:
        agent = agent_store.get_agent(name)
    except TBError as e:
        handler._send_tb_error(e)
        return
    try:
        max_total = int(body.get("max_total_steps") or 200)
    except (TypeError, ValueError):
        max_total = 200
    stop_on_error = bool(body.get("stop_on_error"))

    def _go():
        try:
            _work_mode.run(
                agent, tasks_path=tasks_path,
                max_total_steps=max(1, max_total),
                stop_on_error=stop_on_error)
        except Exception:
            pass
    threading.Thread(target=_go, daemon=True).start()
    handler._send_json({"ok": True, "started": True})


def _h_agent_work_stop(handler, _parsed: ParseResult, body: dict) -> None:
    if not handler._check_unlock():
        return
    name = (body.get("name") or "").strip().lower()
    if not name:
        handler._send_json({"ok": False, "error": "missing 'name'"}, status=400)
        return
    _work_mode.request_stop(name)
    handler._send_json({"ok": True, "stop_requested": True})


def register() -> Registration:
    """Entry point the core loader calls at server start."""
    reg = Registration(name="agent")
    reg.get_routes.update({
        "/api/agents": _h_agents_get,
        "/api/agent-log": _h_agent_log,
        "/api/agent-log-json": _h_agent_log_json,
        "/api/agent-workflows": _h_agent_workflows_get,
        "/api/agent-workflow-state": _h_agent_workflow_state,
        "/api/agent-workflow-runs": _h_agent_workflow_runs,
        "/api/agent-runs": _h_agent_runs,
        "/api/agent-run": _h_agent_run,
        "/api/agent-costs": _h_agent_costs,
        "/api/agent-hooks": _h_agent_hooks_get,
        "/api/agent-notifications": _h_agent_notifications,
        "/api/agent-conductor": _h_agent_conductor_get,
        "/api/agent-conductor-events": _h_agent_conductor_events,
        "/api/agent-repl-context": _h_agent_repl_context,
    })
    reg.post_routes.update({
        "/api/agents": _h_agents_post,
        "/api/agents/remove": _h_agents_remove,
        "/api/agent-workflows": _h_agent_workflows_post,
        "/api/agent-hooks": _h_agent_hooks_post,
        "/api/agent-conductor": _h_agent_conductor_post,
        "/api/agent-cycle": _h_agent_cycle_post,
        "/api/agent-work": _h_agent_work_post,
        "/api/agent-work/stop": _h_agent_work_stop,
        "/api/agent-conversation": _h_agent_conversation_open,
        "/api/agent-conversation-fork": _h_agent_conversation_fork,
    })
    return reg
