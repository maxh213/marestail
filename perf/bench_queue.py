#!/usr/bin/env python3
import argparse
import importlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

import harness

root = harness.use_tree()

from marestail import cli as cli_module
from marestail import pipeline
from marestail.config import Config
from marestail.task_file import TaskFile

STAMP = "2026-10-17T12:00:00.000Z"
PID = 424242
BODY = "# task title\n\nbench\n"
CHAIN_SIZE = 64
START = Path.cwd()
REPOS: dict[str, Path] = {}
SHA: dict[str, str] = {}

PUSH_VARS = ("MARESTAIL_QUEUE_PUSH", "MARESTAIL_QUEUE_PR")
QUEUE_NAMES = (
    "queue._accept_roles",
    "queue._accept_roles hyper",
    "queue._resolve_targets",
    "queue._prepare_tasks",
    "queue._order_tasks",
    "queue._order_tasks chain",
    "queue._check_clean_tree",
    "queue._check_no_running_run",
    "queue._build_child_argv",
    "queue._build_child_env",
    "queue._build_child_env set",
    "queue._map_outcome complete",
    "queue._map_outcome exit 3",
    "queue._map_outcome stopped",
    "queue._map_outcome failed",
    "queue._map_outcome interrupted",
    "queue._find_blocked_reason failed",
    "queue._find_blocked_reason ancestor",
    "queue._write_state",
    "queue._format_pr_body",
    "queue._read_toml_push_pr",
    "queue._env_flag",
    "queue._env_flag off",
    "marestail queue --help",
    "marestail queue bad role",
    "marestail queue here",
    "marestail queue branch",
    "marestail queue blocked",
    "marestail queue skip",
)


def load_queue() -> Any | None:
    try:
        return importlib.import_module("marestail.queue")
    except ImportError:
        return None


def emit_fn(target: str, fn: Callable[[], Any] | None) -> None:
    if fn is None:
        harness.absent(target)
        return
    harness.emit(target, harness.measure(fn))


def git(folder: Path, *args: str) -> str:
    completed = subprocess.run(["git", *args], cwd=folder, check=True, capture_output=True, text=True)
    return completed.stdout.strip()


def init_repo(path: Path) -> None:
    path.mkdir(parents=True)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "bench@example.com")
    git(path, "config", "user.name", "bench")
    git(path, "config", "commit.gpgsign", "false")
    (path / "marestail.toml").write_text('[git]\nbase = "main"\n')
    git(path, "add", "marestail.toml")
    git(path, "commit", "-q", "-m", "init")


def write_task(repo: Path, name: str, depends: list[str], stack: bool | None = None) -> None:
    path = repo / "tasks" / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["+++", f"depends = {json.dumps(depends)}"]
    if stack is not None:
        lines.append(f"stack = {'true' if stack else 'false'}")
    lines.extend(["+++", f"# {name}", ""])
    path.write_text("\n".join(lines) + "\n")


def commit_tasks(repo: Path) -> None:
    git(repo, "add", "tasks")
    git(repo, "commit", "-q", "-m", "tasks")


def done_entry(branch: str, sha: str) -> dict[str, Any]:
    return {
        "status": "done",
        "branch": branch,
        "head": sha,
        "finished_at": STAMP,
        "to": None,
        "exit_code": 0,
        "reason": None,
    }


def write_queue_state(repo: Path, tasks: dict[str, Any]) -> None:
    queue_dir = repo / ".marestail" / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    (queue_dir / "state.json").write_text(json.dumps({"v": 1, "tasks": tasks}))


def reset_work(repo: Path) -> None:
    queue_dir = repo / ".marestail" / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    for name in ("state.json", "queue.log", "lock"):
        (queue_dir / name).unlink(missing_ok=True)
    runs = repo / ".marestail" / "runs"
    if runs.exists():
        shutil.rmtree(runs)


def trim_log(repo: Path) -> None:
    queue_dir = repo / ".marestail" / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    for name in ("queue.log", "lock"):
        (queue_dir / name).unlink(missing_ok=True)


def task_file(name: str, depends: tuple[str, ...] = (), stack: bool | None = None, body: str = BODY) -> TaskFile:
    return TaskFile(name, Path(f"{name}.md"), depends, stack, body, True)


def example_tasks() -> list[TaskFile]:
    return [
        task_file("a"),
        task_file("b", ("a",), True),
        task_file("c", ("a",), False),
        task_file("d"),
    ]


def chain_tasks() -> list[TaskFile]:
    tasks = [task_file("t000")]
    for index in range(1, CHAIN_SIZE):
        name = f"t{index:03d}"
        tasks.append(task_file(name, (f"t{index - 1:03d}",), True))
    return tasks


class _Child:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.returncode = 0

    def wait(self, _timeout: float | None = None) -> int:
        return 0


def fake_start(argv: list[str], cwd: Path, _env: dict[str, str]) -> _Child:
    task_id = Path(argv[4]).stem
    folder = Path(cwd) / ".marestail" / "runs" / task_id
    folder.mkdir(parents=True, exist_ok=True)
    rows = [
        {"v": 1, "at": STAMP, "event": "run-start", "pid": PID, "task": task_id, "branch": "main"},
        {"v": 1, "at": STAMP, "event": "step-start", "pid": PID, "step": "01-coder", "role": "coder"},
        {"v": 1, "at": STAMP, "event": "step-end", "pid": PID, "step": "01-coder", "role": "coder", "outcome": "done"},
        {"v": 1, "at": STAMP, "event": "run-end", "pid": PID, "result": "complete", "exit_code": 0},
    ]
    (folder / "events.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    return _Child(PID)


def in_repo(repo: Path, fn: Callable[[], Any]) -> None:
    os.chdir(repo)
    try:
        fn()
    finally:
        os.chdir(START)


def without_push(fn: Callable[[], Any]) -> None:
    saved = {name: os.environ.pop(name, None) for name in PUSH_VARS}
    try:
        fn()
    finally:
        for name, value in saved.items():
            if value is not None:
                os.environ[name] = value


def build_repos(folder: Path) -> None:
    here = folder / "here"
    branch = folder / "branch"
    blocked = folder / "blocked"
    skip = folder / "skip"
    init_repo(here)
    for name, depends, stack in (
        ("a", [], None),
        ("b", ["a"], True),
        ("c", ["a"], False),
        ("d", [], None),
    ):
        write_task(here, name, depends, stack)
    commit_tasks(here)
    init_repo(branch)
    write_task(branch, "a", [])
    write_task(branch, "d", [])
    commit_tasks(branch)
    init_repo(blocked)
    write_task(blocked, "a", [])
    write_task(blocked, "c", ["a"], False)
    write_task(blocked, "d", [])
    commit_tasks(blocked)
    git(blocked, "switch", "-q", "-c", "side")
    git(blocked, "commit", "-q", "--allow-empty", "-m", "side")
    SHA["side"] = git(blocked, "rev-parse", "HEAD")
    git(blocked, "switch", "-q", "main")
    main_sha = git(blocked, "rev-parse", "HEAD")
    write_queue_state(
        blocked,
        {"a": done_entry("marestail/a", SHA["side"]), "d": done_entry("marestail/d", main_sha)},
    )
    init_repo(skip)
    write_task(skip, "a", [])
    write_task(skip, "d", [])
    commit_tasks(skip)
    skip_sha = git(skip, "rev-parse", "HEAD")
    write_queue_state(skip, {"a": done_entry("main", skip_sha), "d": done_entry("main", skip_sha)})
    REPOS["root"] = folder
    REPOS["here"] = here
    REPOS["branch"] = branch
    REPOS["blocked"] = blocked
    REPOS["skip"] = skip


def find_unknown() -> None:
    try:
        pipeline.find("nope")
    except SystemExit:
        return


def parse_queue() -> None:
    parser = argparse.ArgumentParser(prog="marestail")
    commands = parser.add_subparsers(dest="command", required=True)
    cli_module.add_queue(commands.add_parser("queue"))
    parser.parse_args(
        [
            "queue",
            "tasks",
            "--from",
            "coder",
            "--to",
            "hardener",
            "--here",
            "--retry",
            "a",
            "--agent",
            "claude",
            "--model",
            "m",
            "--effort",
            "high",
            "--scope",
            "changed",
            "--focus",
            "x",
        ]
    )


def expect(code: int, wanted: int, label: str) -> None:
    if code != wanted:
        raise RuntimeError(f"{label} exited {code}")


def queue_help() -> None:
    try:
        cli_module.main(["queue", "--help"])
    except SystemExit as err:
        if err.code not in (0, None):
            raise RuntimeError(f"queue help exited {err.code}") from err
        return
    raise RuntimeError("queue help returned")


def queue_bad_role() -> None:
    def run() -> None:
        code: dict[str, int] = {}

        def invoke() -> None:
            code["value"] = cli_module.main(["queue", "--to", "nope"])

        in_repo(REPOS["here"], invoke)
        expect(code["value"], 2, "bad role")

    without_push(run)


def queue_here() -> None:
    def run() -> None:
        reset_work(REPOS["here"])
        code: dict[str, int] = {}

        def invoke() -> None:
            code["value"] = cli_module.main(["queue", "--here", "tasks"])

        in_repo(REPOS["here"], invoke)
        expect(code["value"], 0, "here")

    without_push(run)


def queue_branch() -> None:
    def run() -> None:
        reset_work(REPOS["branch"])
        code: dict[str, int] = {}

        def invoke() -> None:
            code["value"] = cli_module.main(["queue", "tasks"])

        in_repo(REPOS["branch"], invoke)
        expect(code["value"], 0, "branch")

    without_push(run)


def queue_blocked() -> None:
    def run() -> None:
        trim_log(REPOS["blocked"])
        code: dict[str, int] = {}

        def invoke() -> None:
            code["value"] = cli_module.main(["queue", "tasks"])

        in_repo(REPOS["blocked"], invoke)
        expect(code["value"], 1, "blocked")

    without_push(run)


def queue_skip() -> None:
    def run() -> None:
        trim_log(REPOS["skip"])
        code: dict[str, int] = {}

        def invoke() -> None:
            code["value"] = cli_module.main(["queue", "tasks"])

        in_repo(REPOS["skip"], invoke)
        expect(code["value"], 0, "skip")

    without_push(run)


def accept_roles() -> None:
    expect(queue._accept_roles("coder", "hardener", None), 0, "roles")


def accept_roles_hyper() -> None:
    expect(queue._accept_roles(None, "cleaner", "hyper"), 2, "hyper")


def resolve_targets() -> None:
    if queue._resolve_targets([]) != [Path("tasks")]:
        raise RuntimeError("default path")


def prepare_tasks() -> None:
    _tasks, code = queue._prepare_tasks([str(REPOS["here"] / "tasks")], REPOS["here"])
    expect(code, 0, "prepare")


def order_tasks() -> None:
    ordered = [item.id for item in queue._order_tasks(EXAMPLE)]
    if ordered != ["a", "b", "c", "d"]:
        raise RuntimeError(f"order {ordered}")


def order_chain() -> None:
    ordered = queue._order_tasks(CHAIN)
    if [item.id for item in ordered] != [f"t{index:03d}" for index in range(CHAIN_SIZE)]:
        raise RuntimeError("chain")


def clean_tree() -> None:
    expect(queue._check_clean_tree(REPOS["here"]), 0, "clean")


def no_running() -> None:
    expect(queue._check_no_running_run(REPOS["here"]), 0, "running")


def build_argv() -> None:
    child = queue._ChildOptions("claude", "m", "high", "changed", ["x", "y"], "coder", "hardener")
    task = TaskFile("a", REPOS["here"] / "tasks" / "a.md", (), None, BODY, True)
    queue._build_child_argv(task, REPOS["here"], "coder", child)


def child_env() -> None:
    saved = os.environ.pop("MARESTAIL_LIMIT_WAITS", None)
    try:
        queue._build_child_env()
    finally:
        if saved is not None:
            os.environ["MARESTAIL_LIMIT_WAITS"] = saved


def child_env_set() -> None:
    saved = os.environ.get("MARESTAIL_LIMIT_WAITS")
    os.environ["MARESTAIL_LIMIT_WAITS"] = "5"
    try:
        queue._build_child_env()
    finally:
        if saved is None:
            os.environ.pop("MARESTAIL_LIMIT_WAITS", None)
        else:
            os.environ["MARESTAIL_LIMIT_WAITS"] = saved


def map_complete() -> None:
    if queue._map_outcome({"result": "complete"}, 0) != ("done", None):
        raise RuntimeError("complete")


def map_exit_3() -> None:
    status, reason = queue._map_outcome({"result": "complete"}, 3)
    if status != "stopped" or reason != "complete but NOT verified against the running app":
        raise RuntimeError("exit 3")


def map_stopped() -> None:
    status, reason = queue._map_outcome({"result": "stopped", "stopped_at": "critic", "reason": "not approved"}, 1)
    if status != "stopped" or reason != "stopped at critic: not approved":
        raise RuntimeError("stopped")


def map_failed() -> None:
    status, reason = queue._map_outcome({"result": "failed", "reason": "build error"}, 1)
    if status != "failed" or reason != "build error":
        raise RuntimeError("failed")


def map_interrupted() -> None:
    status, reason = queue._map_outcome({"result": "interrupted", "role": "coder"}, 1)
    if status != "failed" or reason != "marestail run ended without finishing (exit 1) at coder":
        raise RuntimeError("interrupted")


def blocked_failed() -> None:
    task = task_file("b", ("a",), True)
    state = {"tasks": {"a": {"status": "failed", "reason": "build error"}}}
    reason = queue._find_blocked_reason(task, state, REPOS["here"], False, "main", REPOS["here"] / "bench.log")
    if reason != "a failed":
        raise RuntimeError(reason)


def blocked_ancestor() -> None:
    task = task_file("c", ("a",), False)
    state = {"tasks": {"a": done_entry("marestail/a", SHA["side"])}}
    log = REPOS["blocked"] / ".marestail" / "queue" / "bench.log"
    reason = queue._find_blocked_reason(task, state, REPOS["blocked"], False, "main", log)
    if reason is None or not reason.startswith("waiting for a (") or not reason.endswith(" to be merged into main"):
        raise RuntimeError(reason)


def write_state() -> None:
    queue._write_state(REPOS["root"] / "state.json", {"v": 1, "tasks": {"a": done_entry("marestail/a", "abc")}})


def format_pr() -> None:
    tasks = [task_file("a", body="# alpha\n"), task_file("b", ("a",), True, "# beta\n")]
    state = {
        "tasks": {
            "a": {"status": "done", "branch": "marestail/a"},
            "b": {"status": "done", "branch": "marestail/a"},
        }
    }
    queue._format_pr_body("b", "marestail/a", tasks, state)


def read_toml_push() -> None:
    queue._read_toml_push_pr(Config(root=START, raw={}))
    queue._read_toml_push_pr(Config(root=START, raw={"queue": {"push": True, "pr": False}}))


def env_flag_unset() -> None:
    saved = os.environ.pop("MARESTAIL_QUEUE_PUSH", None)
    try:
        queue._env_flag("MARESTAIL_QUEUE_PUSH")
    finally:
        if saved is not None:
            os.environ["MARESTAIL_QUEUE_PUSH"] = saved


def env_flag_off() -> None:
    saved = os.environ.get("MARESTAIL_QUEUE_PUSH")
    os.environ["MARESTAIL_QUEUE_PUSH"] = "0"
    try:
        queue._env_flag("MARESTAIL_QUEUE_PUSH")
    finally:
        if saved is None:
            os.environ.pop("MARESTAIL_QUEUE_PUSH", None)
        else:
            os.environ["MARESTAIL_QUEUE_PUSH"] = saved


queue = load_queue()
EXAMPLE = example_tasks()
CHAIN = chain_tasks()

harness.emit("pipeline.find unknown", harness.measure(find_unknown))

unknown_role = getattr(pipeline, "unknown_role", None)
unknown_message = getattr(pipeline, "_unknown_message", None)
emit_fn("pipeline.unknown_role", (lambda: unknown_role("hardener")) if callable(unknown_role) else None)
emit_fn("pipeline.unknown_role unknown", (lambda: unknown_role("nope")) if callable(unknown_role) else None)
emit_fn("pipeline.unknown_role hyper", (lambda: unknown_role("cleaner", "hyper")) if callable(unknown_role) else None)
emit_fn(
    "pipeline._unknown_message",
    (lambda: unknown_message("nope", None, False)) if callable(unknown_message) else None,
)
emit_fn("cli.add_queue", parse_queue if callable(getattr(cli_module, "add_queue", None)) else None)

QUEUE_FNS: dict[str, Callable[[], Any]] = {
    "marestail queue --help": queue_help,
    "marestail queue bad role": queue_bad_role,
    "marestail queue here": queue_here,
    "marestail queue branch": queue_branch,
    "marestail queue blocked": queue_blocked,
    "marestail queue skip": queue_skip,
    "queue._accept_roles": accept_roles,
    "queue._accept_roles hyper": accept_roles_hyper,
    "queue._resolve_targets": resolve_targets,
    "queue._prepare_tasks": prepare_tasks,
    "queue._order_tasks": order_tasks,
    "queue._order_tasks chain": order_chain,
    "queue._check_clean_tree": clean_tree,
    "queue._check_no_running_run": no_running,
    "queue._build_child_argv": build_argv,
    "queue._build_child_env": child_env,
    "queue._build_child_env set": child_env_set,
    "queue._map_outcome complete": map_complete,
    "queue._map_outcome exit 3": map_exit_3,
    "queue._map_outcome stopped": map_stopped,
    "queue._map_outcome failed": map_failed,
    "queue._map_outcome interrupted": map_interrupted,
    "queue._find_blocked_reason failed": blocked_failed,
    "queue._find_blocked_reason ancestor": blocked_ancestor,
    "queue._write_state": write_state,
    "queue._format_pr_body": format_pr,
    "queue._read_toml_push_pr": read_toml_push,
    "queue._env_flag": env_flag_unset,
    "queue._env_flag off": env_flag_off,
}

folder = Path(tempfile.mkdtemp())
try:
    if queue is None:
        for name in QUEUE_NAMES:
            harness.absent(name)
    else:
        build_repos(folder)
        queue._start_child = fake_start
        for name in QUEUE_NAMES:
            emit_fn(name, QUEUE_FNS[name])
finally:
    shutil.rmtree(folder, ignore_errors=True)
