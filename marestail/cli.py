import argparse
import importlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from marestail import config as config_module
from marestail import context as context_module
from marestail import gates as gates_module
from marestail.context import hook_focus
from marestail.report import Result, render, to_json

configured_gates = gates_module.configured_gates
locate_focus = context_module.locate_focus
resolve_focus = context_module.resolve_focus
run_gates = gates_module.run_gates
run_gates_with_context = gates_module.run_gates_with_context
run_one = gates_module.run_one

HOOK_BLOCK_LIMIT = 5
STORE_TRUE = "store_true"
TREE = "--tree"
TUI_APP = "marestail.tui.app"

Payload = dict[str, Any]


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if arguments[:1] == ["route"]:
        from marestail import route

        return route.command(arguments[1:])
    parser = build_parser()
    args = parser.parse_args(arguments)
    code: int = args.handler(args)
    return code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="marestail", description="deterministic gates for coding agents")
    commands = parser.add_subparsers(dest="command", required=True)
    add_gate(commands.add_parser("gate", help="run the gates against the current repo"))
    add_run(commands.add_parser("run", help="run the role pipeline on a task"))
    add_install(commands.add_parser("install", help="install thin config into a target repo"))
    add_sonar(commands.add_parser("sonar", help="manage the local SonarQube"))
    add_watch(commands.add_parser("watch", help="live TUI of every marestail pipeline on this machine"))
    add_perf(commands.add_parser("perf", help="take performance samples during a perf run"))
    commands.add_parser(
        "route", help="print the subscription to use now: runs dandelion route with the same arguments, e.g. --high", add_help=False
    )
    commands.add_parser("graph", help="print the module dependency graph").set_defaults(handler=graph_command)
    commands.add_parser("depth", help="print module interface width and depth").set_defaults(handler=depth_command)
    return parser


def add_perf(parser: argparse.ArgumentParser) -> None:
    actions = parser.add_subparsers(dest="perf_command", required=True)
    sample = actions.add_parser("run", help="take samples of one perf/bench_* script on one tree")
    sample.add_argument("script")
    sample.add_argument(TREE, required=True)
    sample.add_argument("--samples", type=int, default=1)
    sample.add_argument("--db", action=STORE_TRUE, help="reset the tree's performance database before every sample")
    sample.set_defaults(handler=perf_run_command)
    database = actions.add_parser("db", help="manage the local performance database")
    db_actions = database.add_subparsers(dest="db_command", required=True)
    golden = db_actions.add_parser("golden", help="build the seeded golden data directory for one tree")
    golden.add_argument(TREE, required=True)
    golden.add_argument("--wait", action=STORE_TRUE, help="block until the build finishes instead of detaching")
    golden.set_defaults(handler=perf_db_command)
    db_actions.add_parser("status", help="print the golden status of every tree in the perf run").set_defaults(handler=perf_db_command)
    url = db_actions.add_parser("url", help="print the database URL for one tree")
    url.add_argument(TREE, required=True)
    url.set_defaults(handler=perf_db_command)
    db_actions.add_parser("prune", help="delete this repo's goldens the current perf run does not need").set_defaults(
        handler=perf_db_command
    )
    db_actions.add_parser("down", help="remove every performance database container, keeping the volume").set_defaults(
        handler=perf_db_command
    )


def perf_db_command(args: argparse.Namespace) -> int:
    from marestail.perf import db

    return db.command(args.db_command, getattr(args, "tree", None), getattr(args, "wait", False))


def perf_run_command(args: argparse.Namespace) -> int:
    from marestail.perf import samples

    return samples.run_command(args.script, args.tree, args.samples, args.db)


def add_gate(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--tier", choices=["fast", "sonar", "full", "qa", "all"], default="fast")
    add_scope(parser, "all (default); changed: the diff against [git] base plus the focus paths; hard: only the focus paths")
    add_focus(parser)
    parser.add_argument("--only", help="comma separated gate names")
    parser.add_argument("--json", action=STORE_TRUE)
    parser.add_argument("--hook", action=STORE_TRUE, help="behave as a Claude Code Stop hook")
    parser.set_defaults(handler=gate_command)


def add_scope(parser: argparse.ArgumentParser, help_text: str) -> None:
    parser.add_argument("--scope", choices=["all", "changed", "hard"], default=None, help=help_text)


def add_focus(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--focus",
        action="append",
        default=[],
        metavar="PATH",
        help="add a file or directory to the gate scope (repeatable); implies --scope changed",
    )


def add_run(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("task", help="path to the task file")
    parser.add_argument("--from", dest="start", default=None)
    parser.add_argument("--to", dest="stop", default=None)
    parser.add_argument("--auto", action=STORE_TRUE, help="skip the approval pause after the critic")
    add_scope(
        parser,
        "changed is a soft scope: gate the diff against [git] base plus the focus paths; workers may still edit any file, and it joins the diff. hard gates only the focus paths and tells every role to leave the rest alone apart from the smallest supporting edits",
    )
    add_focus(parser)
    parser.add_argument(
        "--model", default=None, help="the model, or dandelion/route or dandelion/route-best to ask dandelion before every session"
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=0,
        metavar="N",
        help="attempts per role; 0 means unlimited (default)",
    )
    parser.add_argument(
        "--effort",
        default=None,
        help="reasoning effort (claude and agy: low|medium|high|xhigh|max; grok: reasoning effort; kilo: variant); stamped on every commit",
    )
    parser.add_argument(
        "--agent",
        choices=["claude", "agy", "grok", "cursor", "kilo", "kimi"],
        default=None,
        help="agent backend (claude, agy, grok, cursor, kilo, or kimi)",
    )
    parser.set_defaults(handler=run_command)


def add_install(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("target", nargs="?", default=".")
    parser.add_argument("--gitignore-generated", action=STORE_TRUE, help="add the files marestail generates to the target's .gitignore")
    parser.set_defaults(handler=install_command)


def add_sonar(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("action", choices=["up", "down", "setup"])
    parser.set_defaults(handler=sonar_command)


def add_watch(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("paths", nargs="*", help="directories to scan for repos with a .marestail directory")
    parser.add_argument("--refresh", type=float, default=2.0, help="seconds between redraws")
    parser.add_argument(
        "--all", action=STORE_TRUE, help="show every repo with a .marestail directory, not just those with a running pipeline"
    )
    parser.set_defaults(handler=watch_command)


def gate_command(args: argparse.Namespace) -> int:
    if args.hook:
        return hook_command()
    return focused_gate_command(args, {path for path in args.focus if path.strip()})


def focused_gate_command(args: argparse.Namespace, focus: set[str]) -> int:
    if focus and args.scope == "all":
        sys.stderr.write("--focus cannot be combined with --scope all\n")
        return 2
    hard = args.scope == "hard"
    results, ctx = run_gates_with_context(args.tier, wants_changed(args.scope, focus), parse_only(args.only), focus, hard)
    return report_gates(args, results, ctx)


def wants_changed(scope: str | None, focus: set[str]) -> bool:
    return scope in ("changed", "hard") or bool(focus)


def report_gates(args: argparse.Namespace, results: list[Result], ctx: context_module.Context) -> int:
    if not results:
        sys.stderr.write(f"no gate ran: nothing in the {args.tier} tier matches --only and marestail.toml\n")
        return 2
    print(to_json(results, ctx.scope_name, ctx.focus) if args.json else render(results, scope_line(ctx)))
    return 0 if passed(results) else 1


def passed(results: list[Result]) -> bool:
    return all(result.ok for result in results)


def hook_scope(config: config_module.Config) -> tuple[set[str], bool]:
    found = hook_focus(config)
    for path in filter(None, os.environ.get("MARESTAIL_FOCUS", "").split(os.pathsep)):
        entry = locate_focus(config, path)
        if entry is not None:
            found.add(entry)
    return found, os.environ.get("MARESTAIL_SCOPE") == "hard"


def scope_line(ctx: context_module.Context) -> str | None:
    return ctx.scope_summary() if ctx.scoped else None


def parse_only(value: str | None) -> set[str] | None:
    return {name.strip() for name in value.split(",")} if value else None


def hook_command() -> int:
    payload = json.loads(sys.stdin.read() or "{}")
    if "hookEventName" in payload:
        return grok_hook_command(payload)
    if is_cursor_hook(payload):
        return cursor_hook_command(payload)
    return claude_hook_command(payload)


def claude_hook_command(payload: Payload) -> int:
    config = config_module.load(Path(claude_root(payload)))
    message = hook_verdict(config, payload.get("session_id") or payload.get("conversationId", "default"))
    if "conversationId" in payload:
        print(json.dumps(agy_reply(message)))
        return 0
    return claude_reply(message)


def claude_root(payload: Payload) -> Any:
    return payload.get("cwd") or (payload.get("workspacePaths") or [None])[0] or Path.cwd()


def agy_reply(message: str | None) -> Payload:
    return {} if message is None else {"decision": "continue", "reason": message}


def claude_reply(message: str | None) -> int:
    if message is None:
        return 0
    sys.stderr.write(message)
    return 2


def hook_verdict(config: config_module.Config, session_id: str, loop_count: int = 0, finished: bool = True) -> str | None:
    counter = config.work / f"hook-{session_id}.count"
    sweep_counters(config.work, keep=counter)
    blocked = blocked_count(counter)
    results, ctx = run_gates_with_context("fast", True, None, *hook_scope(config))
    if passed(results) or max(blocked, loop_count) >= HOOK_BLOCK_LIMIT:
        counter.unlink(missing_ok=True)
        return None
    if not finished:
        return None
    counter.write_text(str(blocked + 1))
    return render(results, scope_line(ctx)) + "\nFix these before stopping.\n"


def blocked_count(counter: Path) -> int:
    return int(counter.read_text()) if counter.exists() else 0


def load_hook_config(root: Any) -> config_module.Config | None:
    start = Path(root).resolve()
    if not any((folder / config_module.FILENAME).exists() for folder in [start, *start.parents]):
        return None
    return config_module.load(start)


def is_cursor_hook(payload: Payload) -> bool:
    return "cursor_version" in payload or payload.get("hook_event_name") == "stop"


def cursor_hook_command(payload: Payload) -> int:
    if payload.get("hook_event_name") in (None, "", "stop"):
        answer_cursor(payload)
    return 0


def answer_cursor(payload: Payload) -> None:
    config = load_hook_config(cursor_root(payload))
    if config is not None:
        message = cursor_verdict(config, payload)
        print(json.dumps({} if message is None else {"followup_message": message}))


def cursor_root(payload: Payload) -> Any:
    roots = payload.get("workspace_roots") or []
    return roots[0] if roots else payload.get("cwd") or Path.cwd()


def cursor_session(payload: Payload) -> str:
    return str(payload.get("conversation_id") or payload.get("session_id") or "default")


def cursor_verdict(config: config_module.Config, payload: Payload) -> str | None:
    session_id = cursor_session(payload)
    loop_count = int(payload.get("loop_count") or 0)
    finished = payload.get("status") in (None, "", "completed")
    previous = Path.cwd()
    try:
        os.chdir(config.root)
        return hook_verdict(config, session_id, loop_count, finished)
    finally:
        os.chdir(previous)


def grok_hook_command(payload: Payload) -> int:
    if payload.get("reason") in (None, "", "end_turn"):
        answer_grok(payload)
    return 0


def answer_grok(payload: Payload) -> None:
    config = load_hook_config(grok_root(payload))
    if config is not None:
        grok_turn(config, payload.get("sessionId") or "default", str(payload.get("promptId") or ""))


def grok_root(payload: Payload) -> Any:
    return payload.get("cwd") or payload.get("workspaceRoot") or Path.cwd()


def grok_turn(config: config_module.Config, session_id: str, turn_id: str) -> None:
    replayed = grok_replay_hook(config.work, session_id, turn_id)
    allow, message = replayed if replayed is not None else grok_verdict(config, session_id, turn_id)
    grok_emit_hook(allow, message)


def grok_verdict(config: config_module.Config, session_id: str, turn_id: str) -> tuple[bool, str]:
    message = hook_verdict(config, session_id)
    allow = message is None
    grok_remember_hook(config.work, session_id, turn_id, allow, message or "")
    return allow, message or ""


def grok_emit_hook(allow: bool, message: str) -> None:
    if not allow:
        print(json.dumps({"decision": "block", "reason": message}))


def grok_replay_hook(work: Path, session_id: str, turn_id: str) -> tuple[bool, str] | None:
    if not turn_id:
        return None
    stamp = work / f"hook-{session_id}.turn"
    if not stamp.exists():
        return None
    recorded_id, _, rest = stamp.read_text().partition("\n")
    if recorded_id != turn_id:
        return None
    decision, _, message = rest.partition("\n")
    return decision == "allow", message


def grok_remember_hook(work: Path, session_id: str, turn_id: str, allow: bool, message: str) -> None:
    if not turn_id:
        return
    work.mkdir(parents=True, exist_ok=True)
    body = "allow\n" if allow else f"block\n{message}"
    (work / f"hook-{session_id}.turn").write_text(f"{turn_id}\n{body}")


def sweep_counters(work: Path, keep: Path) -> None:
    for stale in work.glob("hook-*.count"):
        if stale != keep and time.time() - stale.stat().st_mtime > 86400:
            stale.unlink(missing_ok=True)


def run_command(args: argparse.Namespace) -> int:
    from marestail.runner import run_pipeline

    return run_pipeline(
        Path(args.task), args.start, args.stop, args.auto, args.model, args.retries, args.agent, args.effort, args.scope, args.focus
    )


def watch_command(args: argparse.Namespace) -> int:
    paths = args.paths or default_watch_roots()
    code: int = importlib.import_module(TUI_APP).run([Path(p) for p in paths], args.refresh, args.all)
    return code


def default_watch_roots() -> list[Path]:
    workspace = Path.home() / "workspace"
    return [workspace] if workspace.is_dir() else [Path.cwd()]


def install_command(args: argparse.Namespace) -> int:
    from marestail.install import install

    install(Path(args.target).resolve(), gitignore_generated=args.gitignore_generated)
    return 0


def graph_command(args: argparse.Namespace) -> int:
    from marestail.graph import render as render_graph

    print(render_graph(config_module.load(Path.cwd())))
    return 0


def depth_command(args: argparse.Namespace) -> int:
    from marestail import depth

    print(depth.report(depth.analyse(config_module.load(Path.cwd()))))
    return 0


def sonar_command(args: argparse.Namespace) -> int:
    from marestail.sonar import setup

    if args.action == "up":
        setup.up()
    elif args.action == "down":
        setup.down()
    else:
        config = config_module.load(Path.cwd())
        setup.setup(config.get("sonar", "project_key"), config.get("sonar", "project_name", config.root.name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
