import argparse
import json
import sys
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
    commands.add_parser("graph", help="print the module dependency graph").set_defaults(handler=graph_command)
    commands.add_parser("depth", help="print module interface width and depth").set_defaults(handler=depth_command)
    return parser


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
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--agent", choices=["claude", "agy"], default=None, help="agent backend (claude or agy)")
    parser.set_defaults(handler=run_command)


def add_install(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("target", nargs="?", default=".")
    parser.set_defaults(handler=install_command)


def add_sonar(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("action", choices=["up", "down", "setup"])
    parser.set_defaults(handler=sonar_command)


def gate_command(args: argparse.Namespace) -> int:
    if args.hook:
        return hook_command(args)
    results = run_gates(args.tier, args.scope == "changed", parse_only(args.only))
    print(to_json(results) if args.json else render(results))
    return 0 if all(result.ok for result in results) else 1


def run_gates(tier: str, scope_changed: bool, only: set[str] | None) -> list[Result]:
    config = config_module.load(Path.cwd())
    ctx = context_module.build(config, scope_changed)
    results = []
    for gate in gates_module.select(tier, only):
        if gate.section and config.section(gate.section) is None:
            continue
        results.append(gate.run(ctx))
    return results


def parse_only(value: str | None) -> set[str] | None:
    return {name.strip() for name in value.split(",")} if value else None


def hook_command(args: argparse.Namespace) -> int:
    payload = json.loads(sys.stdin.read() or "{}")
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


def sweep_counters(work: Path, keep: Path) -> None:
    import time

    for stale in work.glob("hook-*.count"):
        if stale != keep and time.time() - stale.stat().st_mtime > 86400:
            stale.unlink(missing_ok=True)


def run_command(args: argparse.Namespace) -> int:
    from marestail.runner import run_pipeline

    return run_pipeline(Path(args.task), args.start, args.stop, args.auto, args.model, args.retries, args.agent)


def install_command(args: argparse.Namespace) -> int:
    from marestail.install import install

    install(Path(args.target).resolve())
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
