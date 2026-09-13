import argparse
import json
import os
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from marestail import config as config_module
from marestail import context as context_module
from marestail import gates as gates_module
from marestail.report import Result, render, to_json

HOOK_BLOCK_LIMIT = 5


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.handler(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="marestail", description="deterministic gates for coding agents")
    commands = parser.add_subparsers(dest="command", required=True)
    add_gate(commands.add_parser("gate", help="run the gates against the current repo"))
    add_run(commands.add_parser("run", help="run the role pipeline on a task"))
    add_install(commands.add_parser("install", help="install thin config into a target repo"))
    add_sonar(commands.add_parser("sonar", help="manage the local SonarQube"))
    add_watch(commands.add_parser("watch", help="live TUI of every marestail pipeline on this machine"))
    add_perf(commands.add_parser("perf", help="take performance samples during a perf run"))
    commands.add_parser("graph", help="print the module dependency graph").set_defaults(handler=graph_command)
    commands.add_parser("depth", help="print module interface width and depth").set_defaults(handler=depth_command)
    return parser


def add_perf(parser: argparse.ArgumentParser) -> None:
    actions = parser.add_subparsers(dest="perf_command", required=True)
    sample = actions.add_parser("run", help="take samples of one perf/bench_* script on one tree")
    sample.add_argument("script")
    sample.add_argument("--tree", required=True)
    sample.add_argument("--samples", type=int, default=1)
    sample.add_argument("--db", action="store_true", help="reset the tree's performance database before every sample")
    sample.set_defaults(handler=perf_run_command)
    database = actions.add_parser("db", help="manage the local performance database")
    db_actions = database.add_subparsers(dest="db_command", required=True)
    golden = db_actions.add_parser("golden", help="build the seeded golden data directory for one tree")
    golden.add_argument("--tree", required=True)
    golden.add_argument("--wait", action="store_true", help="block until the build finishes instead of detaching")
    golden.set_defaults(handler=perf_db_command)
    db_actions.add_parser("status", help="print the golden status of every tree in the perf run").set_defaults(handler=perf_db_command)
    url = db_actions.add_parser("url", help="print the database URL for one tree")
    url.add_argument("--tree", required=True)
    url.set_defaults(handler=perf_db_command)
    db_actions.add_parser("prune", help="delete this repo's goldens the current perf run does not need").set_defaults(handler=perf_db_command)
    db_actions.add_parser("down", help="remove every performance database container, keeping the volume").set_defaults(handler=perf_db_command)


def perf_db_command(args: argparse.Namespace) -> int:
    from marestail.perf import db

    return db.command(args.db_command, getattr(args, "tree", None), getattr(args, "wait", False))


def perf_run_command(args: argparse.Namespace) -> int:
    from marestail.perf import samples

    return samples.run_command(args.script, args.tree, args.samples, args.db)


def add_gate(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--tier", choices=["fast", "sonar", "full", "qa", "all"], default="fast")
    parser.add_argument("--scope", choices=["all", "changed"], default="all")
    parser.add_argument("--only", help="comma separated gate names")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--hook", action="store_true", help="behave as a Claude Code Stop hook")
    parser.set_defaults(handler=gate_command)


def add_run(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("task", help="path to the task file")
    parser.add_argument("--from", dest="start", default=None)
    parser.add_argument("--to", dest="stop", default=None)
    parser.add_argument("--auto", action="store_true", help="skip the approval pause after the critic")
    parser.add_argument("--model", default=None)
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
    parser.add_argument("--gitignore-generated", action="store_true", help="add the files marestail generates to the target's .gitignore")
    parser.set_defaults(handler=install_command)


def add_sonar(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("action", choices=["up", "down", "setup"])
    parser.set_defaults(handler=sonar_command)


def add_watch(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("paths", nargs="*", help="directories to scan for repos with a .marestail directory")
    parser.add_argument("--refresh", type=float, default=2.0, help="seconds between redraws")
    parser.add_argument("--all", action="store_true", help="show every repo with a .marestail directory, not just those with a running pipeline")
    parser.set_defaults(handler=watch_command)


def gate_command(args: argparse.Namespace) -> int:
    if args.hook:
        return hook_command(args)
    results = run_gates(args.tier, args.scope == "changed", parse_only(args.only))
    if not results:
        sys.stderr.write(f"no gate ran: nothing in the {args.tier} tier matches --only and marestail.toml\n")
        return 2
    print(to_json(results) if args.json else render(results))
    return 0 if all(result.ok for result in results) else 1


def run_gates(tier: str, scope_changed: bool, only: set[str] | None) -> list[Result]:
    os.environ["MARESTAIL_GATE_ACTIVE"] = "true"
    config = config_module.load(Path.cwd())
    ctx = context_module.build(config, scope_changed)
    results = []
    for gate in gates_module.select(tier, only):
        if gate.section and config.section(gate.section) is None:
            continue
        results.append(run_one(gate, ctx))
    return results


def run_one(gate: gates_module.Gate, ctx: context_module.Context) -> Result:
    started = time.time()
    try:
        return gate.run(ctx)
    except (Exception, SystemExit) as error:
        detail = " ".join(str(error).split())[:200]
        return Result(gate.name, False, f"{gate.name} crashed: {type(error).__name__} {detail}", traceback.format_exc().strip().splitlines()[-6:], time.time() - started)


def parse_only(value: str | None) -> set[str] | None:
    return {name.strip() for name in value.split(",")} if value else None


def hook_command(args: argparse.Namespace) -> int:
    payload = json.loads(sys.stdin.read() or "{}")
    if "hookEventName" in payload:
        return grok_hook_command(payload)
    if is_cursor_hook(payload):
        return cursor_hook_command(payload)
    root_path = payload.get("cwd") or (payload.get("workspacePaths") or [None])[0] or Path.cwd()
    config = config_module.load(Path(root_path))
    session_id = payload.get("session_id") or payload.get("conversationId", "default")
    counter = config.work / f"hook-{session_id}.count"
    sweep_counters(config.work, keep=counter)
    blocked = int(counter.read_text()) if counter.exists() else 0
    results = run_gates("fast", True, None)
    is_agy = "conversationId" in payload
    if all(result.ok for result in results) or blocked >= HOOK_BLOCK_LIMIT:
        counter.unlink(missing_ok=True)
        if is_agy:
            print(json.dumps({}))
        return 0
    counter.write_text(str(blocked + 1))
    message = render(results) + "\nFix these before stopping.\n"
    if is_agy:
        print(json.dumps({"decision": "continue", "reason": message}))
        return 0
    sys.stderr.write(message)
    return 2


def is_cursor_hook(payload: dict) -> bool:
    return "cursor_version" in payload or payload.get("hook_event_name") == "stop"


def cursor_hook_command(payload: dict) -> int:
    if payload.get("hook_event_name") not in (None, "", "stop"):
        return 0
    roots = payload.get("workspace_roots") or []
    root_path = roots[0] if roots else payload.get("cwd") or Path.cwd()
    try:
        config = config_module.load(Path(root_path))
    except SystemExit:
        return 0
    session_id = payload.get("conversation_id") or payload.get("session_id") or "default"
    loop_count = int(payload.get("loop_count") or 0)
    counter = config.work / f"hook-{session_id}.count"
    sweep_counters(config.work, keep=counter)
    blocked = int(counter.read_text()) if counter.exists() else 0
    previous = Path.cwd()
    try:
        os.chdir(config.root)
        results = run_gates("fast", True, None)
    finally:
        os.chdir(previous)
    if all(result.ok for result in results) or blocked >= HOOK_BLOCK_LIMIT or loop_count >= HOOK_BLOCK_LIMIT:
        counter.unlink(missing_ok=True)
        print(json.dumps({}))
        return 0
    if payload.get("status") not in (None, "", "completed"):
        print(json.dumps({}))
        return 0
    counter.write_text(str(blocked + 1))
    message = render(results) + "\nFix these before stopping.\n"
    print(json.dumps({"followup_message": message}))
    return 0


def grok_hook_command(payload: dict) -> int:
    if payload.get("reason") not in (None, "", "end_turn"):
        return 0
    root_path = payload.get("cwd") or payload.get("workspaceRoot") or Path.cwd()
    try:
        config = config_module.load(Path(root_path))
    except SystemExit:
        return 0
    session_id = payload.get("sessionId") or "default"
    turn_id = str(payload.get("promptId") or "")
    replayed = grok_replay_hook(config.work, session_id, turn_id)
    if replayed is not None:
        return grok_emit_hook(replayed[0], replayed[1])
    counter = config.work / f"hook-{session_id}.count"
    sweep_counters(config.work, keep=counter)
    blocked = int(counter.read_text()) if counter.exists() else 0
    results = run_gates("fast", True, None)
    if all(result.ok for result in results) or blocked >= HOOK_BLOCK_LIMIT:
        counter.unlink(missing_ok=True)
        grok_remember_hook(config.work, session_id, turn_id, True, "")
        return grok_emit_hook(True, "")
    counter.write_text(str(blocked + 1))
    message = render(results) + "\nFix these before stopping.\n"
    grok_remember_hook(config.work, session_id, turn_id, False, message)
    return grok_emit_hook(False, message)


def grok_emit_hook(allow: bool, message: str) -> int:
    if allow:
        return 0
    print(json.dumps({"decision": "block", "reason": message}))
    return 0


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
    import time

    for stale in work.glob("hook-*.count"):
        if stale != keep and time.time() - stale.stat().st_mtime > 86400:
            stale.unlink(missing_ok=True)


def run_command(args: argparse.Namespace) -> int:
    from marestail.runner import run_pipeline

    return run_pipeline(Path(args.task), args.start, args.stop, args.auto, args.model, args.retries, args.agent, args.effort)


def watch_command(args: argparse.Namespace) -> int:
    from marestail.tui import app as tui_app

    paths = args.paths or default_watch_roots()
    return tui_app.run([Path(p) for p in paths], args.refresh, args.all)


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
