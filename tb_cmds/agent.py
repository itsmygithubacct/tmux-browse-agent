"""Agent verbs: add/list/remove/run LLM agents over tb.py."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agent import (
    kb as agent_kb,
    repl_context as agent_repl_context,
    runner as agent_runner,
    runtime as agent_runtime,
    store as agent_store,
)
from agent.modes import cycle as cycle_mode
from agent.modes import work as work_mode
from lib import dashboard_config, output
from lib.errors import UsageError


def register_verb():
    """Entry point the core loader calls. Maps the ``agent`` verb to
    :func:`cmd_agent`, the argparse-style dispatch used by ``tb.py``."""
    return {"agent": cmd_agent}


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise UsageError(message)


def _consume_common_flags(argv: list[str], args: argparse.Namespace) -> list[str]:
    """Honor shared flags even when users place them after `tb agent ...`.

    `tb.py` documents `--json`, `--quiet`, and `--no-header` as shared flags
    that work in any position. The top-level parser handles the forms before
    `agent`, so this pass peels them out of the nested remainder as well.
    """
    rest: list[str] = []
    i = 0
    while i < len(argv):
        token = argv[i]
        if token == "--":
            rest.extend(argv[i:])
            break
        if token == "--json":
            args.json = True
        elif token in {"--quiet", "-q"}:
            args.quiet = True
        elif token == "--no-header":
            args.no_header = True
        else:
            rest.append(token)
        i += 1
    return rest


def _read_api_key(ns: argparse.Namespace) -> str:
    if getattr(ns, "api_key", None):
        return ns.api_key
    if getattr(ns, "api_key_stdin", False):
        data = sys.stdin.read().strip()
        if not data:
            raise UsageError("no API key received on stdin")
        return data
    raise UsageError("provide --api-key or --api-key-stdin")


def _parse_add(argv: list[str]) -> argparse.Namespace:
    p = _Parser(prog="tb agent add")
    p.add_argument("name")
    p.add_argument("--api-key")
    p.add_argument("--api-key-stdin", action="store_true")
    p.add_argument("--model")
    p.add_argument("--base-url")
    p.add_argument("--provider")
    p.add_argument("--wire-api")
    return p.parse_args(argv)


def _parse_remove(argv: list[str]) -> argparse.Namespace:
    p = _Parser(prog="tb agent remove")
    p.add_argument("name")
    return p.parse_args(argv)


def _parse_run(name: str, argv: list[str]) -> argparse.Namespace:
    p = _Parser(prog=f"tb agent {name}")
    p.add_argument("prompt", nargs="+")
    p.add_argument("--steps", type=int, default=None)
    p.add_argument("--timeout", type=float, default=90.0)
    return p.parse_args(argv)


def _parse_repl(argv: list[str]) -> argparse.Namespace:
    p = _Parser(prog="tb agent repl")
    p.add_argument("name")
    p.add_argument("--steps", type=int, default=None)
    p.add_argument("--timeout", type=float, default=90.0)
    p.add_argument("--fork", action="store_true",
                   help="fork the current conversation into a new branch")
    return p.parse_args(argv)


def _parse_cycle(argv: list[str]) -> argparse.Namespace:
    p = _Parser(prog="tb agent cycle")
    p.add_argument("name")
    p.add_argument("--goal", default=None,
                   help="path to a goal file")
    p.add_argument("--goal-text", dest="goal_text", default=None,
                   help="inline goal text (overrides --goal)")
    p.add_argument("--steps", type=int, default=None,
                   help="execute-phase step budget")
    p.add_argument("--timeout", type=float, default=90.0)
    return p.parse_args(argv)


def _parse_work(argv: list[str]) -> argparse.Namespace:
    p = _Parser(prog="tb agent work")
    p.add_argument("name")
    p.add_argument("--tasks", required=True,
                   help="path to a task file (one task per line)")
    p.add_argument("--steps-per-task", dest="steps_per_task", type=int,
                   default=None, help="per-task step budget")
    p.add_argument("--max-total-steps", dest="max_total_steps", type=int,
                   default=200, help="cumulative step cap across the queue")
    p.add_argument("--stop-on-error", dest="stop_on_error",
                   action="store_true",
                   help="halt the loop on the first failing task")
    p.add_argument("--timeout", type=float, default=90.0)
    return p.parse_args(argv)


def _default_agent_steps() -> int:
    return max(1, int(dashboard_config.load().get("agent_max_steps", 20)))


def _run_steps(value: int | None) -> int:
    return max(1, value if value is not None else _default_agent_steps())


def _run_repl(name: str, *, steps: int, timeout: float, fork: bool = False) -> int:
    agent = agent_store.get_agent(name)
    repo_root = Path(__file__).resolve().parents[2]
    if fork:
        cid = agent_runtime.fork_conversation(name)
        prior = agent_runtime.load_context(name)
        print(f"Agent REPL for {agent['name']} ({agent['model']})")
        print(f"  Forked conversation ({len(prior)} inherited messages)")
    else:
        cid = agent_runtime.get_or_create_conversation(name)
        prior = agent_runtime.load_context(name)
        print(f"Agent REPL for {agent['name']} ({agent['model']})")
        if prior:
            print(f"  Resumed conversation ({len(prior)} prior messages)")
    print("Commands: /exit  /help  /history  /clear  /new  /fork  "
          "/exec  /watch  /unwatch  /mode  /tick  /kb  /context")
    while True:
        try:
            prompt = input(f"{agent['name']}> ").strip()
        except EOFError:
            print()
            return 0
        except KeyboardInterrupt:
            print()
            return 130
        if not prompt:
            continue
        if prompt in {"/exit", "/quit"}:
            return 0
        if prompt == "/help":
            print("Type any prompt to run the tmux agent.")
            print("  /history   — show conversation turns")
            print("  /clear     — delete conversation and start fresh")
            print("  /new       — start a new conversation (keeps old one)")
            print("  /fork      — branch into a new conversation with copied history")
            print("  /exec T    — set default tmux target for this REPL (T like work: or sandbox:)")
            print("  /watch T   — add an observed pane to the context block")
            print("  /unwatch T — remove one")
            print("  /mode M    — observe | act | watch")
            print("  /tick N    — watch-mode poll interval in seconds (min 5)")
            print("  /kb add P  — attach a file under ~/.tmux-browse/agent-kb/<agent>/")
            print("  /kb rm F   — detach a KB file by filename")
            print("  /kb ls     — list attached KB files")
            print("  /context   — print the current REPL context and KB summary")
            print("  /exit      — quit the REPL")
            continue
        # --- REPL context commands ---
        if prompt.startswith("/exec"):
            target = prompt[len("/exec"):].strip()
            ctx = agent_repl_context.set_exec_target(name, target)
            print(f"exec target: {ctx['exec_target'] or '(unset)'}")
            continue
        if prompt.startswith("/watch "):
            target = prompt[len("/watch "):].strip()
            try:
                ctx = agent_repl_context.add_observed(name, target)
                print(f"observed panes: {ctx['observed_panes']}")
            except ValueError as e:
                print(f"error: {e}")
            continue
        if prompt.startswith("/unwatch "):
            target = prompt[len("/unwatch "):].strip()
            ctx = agent_repl_context.remove_observed(name, target)
            print(f"observed panes: {ctx['observed_panes']}")
            continue
        if prompt.startswith("/mode"):
            mode = prompt[len("/mode"):].strip()
            if not mode:
                ctx = agent_repl_context.load(name)
                print(f"mode: {ctx['mode']}")
            else:
                try:
                    ctx = agent_repl_context.set_mode(name, mode)
                    print(f"mode: {ctx['mode']}")
                except ValueError as e:
                    print(f"error: {e}")
            continue
        if prompt.startswith("/tick "):
            try:
                n = int(prompt[len("/tick "):].strip())
                ctx = agent_repl_context.set_tick(name, n)
                print(f"tick_sec: {ctx['tick_sec']}")
            except ValueError:
                print("error: /tick expects an integer number of seconds")
            continue
        if prompt == "/kb ls":
            files = agent_kb.list_files(name)
            if not files:
                print("(no KB files attached)")
            else:
                for f in files:
                    print(f"  {f['name']:40s} {f['size']} bytes")
            continue
        if prompt.startswith("/kb add "):
            path = prompt[len("/kb add "):].strip()
            try:
                info = agent_kb.add_file(name, path)
                print(f"added {info['name']} ({info['size']} bytes)")
            except (FileNotFoundError, ValueError) as e:
                print(f"error: {e}")
            continue
        if prompt.startswith("/kb rm "):
            fname = prompt[len("/kb rm "):].strip()
            if agent_kb.remove_file(name, fname):
                print(f"removed {fname}")
            else:
                print(f"not found: {fname}")
            continue
        if prompt == "/context":
            ctx = agent_repl_context.load(name)
            print(f"  exec_target:    {ctx['exec_target'] or '(unset)'}")
            print(f"  observed panes: {ctx['observed_panes']}")
            print(f"  mode:           {ctx['mode']}")
            print(f"  tick_sec:       {ctx['tick_sec']}")
            files = agent_kb.list_files(name)
            total = sum(f['size'] for f in files)
            print(f"  KB files:       {len(files)} ({total} bytes)")
            continue
        if prompt == "/history":
            turns = agent_runtime.load_context(name)
            if not turns:
                print("(no conversation history)")
            else:
                for t in turns:
                    tag = "You" if t["role"] == "user" else "Agent"
                    preview = t["content"][:120]
                    if len(t["content"]) > 120:
                        preview += "..."
                    print(f"  [{tag}] {preview}")
            continue
        if prompt == "/clear":
            agent_runtime.clear_conversation(name)
            prior = []
            cid = agent_runtime.get_or_create_conversation(name)
            print("Conversation cleared.")
            continue
        if prompt == "/new":
            cid = agent_runtime.start_new_conversation(name)
            prior = []
            print("Started new conversation.")
            continue
        if prompt == "/fork":
            cid = agent_runtime.fork_conversation(name)
            prior = agent_runtime.load_context(name)
            print(f"Forked conversation ({len(prior)} inherited messages).")
            continue

        context = agent_runtime.load_context(name)
        repl_ctx = agent_repl_context.load(name)
        agent_runtime.record_turn(name, role="user", content=prompt)
        try:
            result = agent_runner.run_agent(
                agent,
                prompt,
                repo_root=repo_root,
                max_steps=steps,
                request_timeout=max(5.0, timeout),
                origin="repl",
                conversation_messages=context,
                repl_context=repl_ctx,
            )
        except Exception as e:
            print(f"error: {e}")
            continue
        agent_runtime.record_turn(
            name, role="assistant", content=result["message"],
            run_id=result.get("run_id"),
        )
        print(result["message"])


def _run_cycle(ns: argparse.Namespace, *, json_output: bool,
               quiet: bool) -> int:
    agent = agent_store.get_agent(ns.name)
    try:
        result = cycle_mode.run(
            agent,
            goal_path=ns.goal,
            goal_text=ns.goal_text,
            steps=_run_steps(ns.steps),
            request_timeout=max(5.0, ns.timeout),
        )
    except Exception as e:
        if json_output:
            output.emit_json({"ok": False, "error": str(e)})
        else:
            print(f"cycle failed: {e}")
        return 7
    if json_output:
        output.emit_json({
            "ok": True,
            "plan_run_id": result.plan_run_id,
            "exec_run_id": result.exec_run_id,
            "plan": result.plan_message,
            "message": result.exec_message,
        })
    elif not quiet:
        print("[plan]")
        print(result.plan_message)
        print()
        print("[execute]")
        print(result.exec_message)
    return 0


def _run_work(ns: argparse.Namespace, *, json_output: bool,
              quiet: bool) -> int:
    agent = agent_store.get_agent(ns.name)
    try:
        result = work_mode.run(
            agent,
            tasks_path=ns.tasks,
            steps_per_task=_run_steps(ns.steps_per_task),
            max_total_steps=max(1, ns.max_total_steps),
            stop_on_error=ns.stop_on_error,
            request_timeout=max(5.0, ns.timeout),
        )
    except Exception as e:
        if json_output:
            output.emit_json({"ok": False, "error": str(e)})
        else:
            print(f"work failed: {e}")
        return 7
    if json_output:
        output.emit_json(result.as_dict())
    elif not quiet:
        print(f"work {result.status}: "
              f"{result.completed} ok / {result.failed} failed "
              f"({result.total_tasks} tasks seen)")
    return 0 if result.status in ("done", "empty") else 7


def _rows() -> list[dict]:
    rows = []
    for row in agent_store.list_agents():
        rows.append({
            "name": row["name"],
            "provider": row.get("provider", "-"),
            "model": row.get("model", "-"),
            "base_url": row.get("base_url", "-"),
            "key": "yes" if row.get("has_api_key") else "no",
        })
    return rows


def cmd_agent(args: argparse.Namespace) -> int:
    mode = (args.mode or "").strip()
    rest = _consume_common_flags(list(args.rest or []), args)
    if not mode:
        raise UsageError("usage: tb agent <name> <prompt...> | tb agent repl <name> | tb agent add|list|remove|defaults ...")

    if mode in {"list", "ls"}:
        rows = _rows()
        if args.json:
            output.emit_json({"agents": rows})
        elif not args.quiet:
            output.emit_table(
                rows,
                [("name", "NAME"), ("provider", "PROVIDER"), ("model", "MODEL"),
                 ("base_url", "BASE_URL"), ("key", "KEY")],
                no_header=args.no_header,
                empty_message="(no configured agents)",
            )
        return 0

    if mode == "defaults":
        rows = []
        for name, spec in sorted(agent_store.load_catalog().items()):
            rows.append({
                "name": name,
                "provider": spec["provider"],
                "model": spec["model"],
                "base_url": spec["base_url"],
            })
        if args.json:
            output.emit_json({"defaults": rows})
        elif not args.quiet:
            output.emit_table(
                rows,
                [("name", "NAME"), ("provider", "PROVIDER"), ("model", "MODEL"), ("base_url", "BASE_URL")],
                no_header=args.no_header,
            )
        return 0

    if mode == "add":
        ns = _parse_add(rest)
        row = agent_store.add_agent(
            ns.name,
            _read_api_key(ns),
            model=ns.model,
            base_url=ns.base_url,
            provider=ns.provider,
            wire_api=ns.wire_api,
        )
        if args.json:
            output.emit_json({"added": row})
        elif not args.quiet:
            print(
                f"added agent {row['name']} ({row['provider']} {row['model']}) "
                f"using {row['base_url']}",
            )
        return 0

    if mode in {"remove", "rm", "delete"}:
        ns = _parse_remove(rest)
        removed = agent_store.remove_agent(ns.name)
        if args.json:
            output.emit_json({"removed": removed, "name": ns.name})
        elif not args.quiet:
            print("removed" if removed else "not found")
        return 0

    if mode == "repl":
        ns = _parse_repl(rest)
        return _run_repl(ns.name, steps=_run_steps(ns.steps), timeout=ns.timeout,
                         fork=getattr(ns, "fork", False))

    if mode == "cycle":
        ns = _parse_cycle(rest)
        return _run_cycle(ns, json_output=args.json, quiet=args.quiet)

    if mode == "work":
        ns = _parse_work(rest)
        return _run_work(ns, json_output=args.json, quiet=args.quiet)

    run = _parse_run(mode, rest)
    agent = agent_store.get_agent(mode)
    repo_root = Path(__file__).resolve().parents[2]
    result = agent_runner.run_agent(
        agent,
        " ".join(run.prompt),
        repo_root=repo_root,
        max_steps=_run_steps(run.steps),
        request_timeout=max(5.0, run.timeout),
        origin="cli",
    )
    if args.json:
        output.emit_json(result)
    elif not args.quiet:
        print(result["message"])
    return 0


def register(sub, common) -> None:
    p = sub.add_parser(
        "agent",
        help="configure and run LLM agents that operate through tb.py",
        parents=[common],
    )
    p.add_argument("mode", nargs="?")
    p.add_argument("rest", nargs=argparse.REMAINDER)
    p.set_defaults(func=cmd_agent)
