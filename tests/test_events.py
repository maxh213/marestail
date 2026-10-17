import ast
import io
import json
import os
import re
import signal
import subprocess
import sys
import tomllib
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any

import pytest

import marestail.events as events
import marestail.freeze as freeze
import marestail.reported as reported
import marestail.runner as runner
import marestail.timeline as timeline
from marestail.config import Config
from marestail.pipeline import Judge, Step
from marestail.report import Result
from marestail.route import Choice
from marestail.runner import Run

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
CLI = str(ROOT / "marestail" / "cli.py")
ENV_KEYS = (
    "MARESTAIL_AGENT",
    "MARESTAIL_NICE",
    "MARESTAIL_SCOPE",
    "MARESTAIL_FOCUS",
    "MARESTAIL_TASK",
    "MARESTAIL_DANDELION",
)
BASE_TOML = '[git]\nbase = "main"\n[perf]\nenabled = false\n[practices]\nenabled = false\n'
AT_REGEX = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
ARCHIVE_REGEX = re.compile(r"^\.marestail/runs/t/handoffs-\d{8}T\d{6}(-\d+)?$")
WAIT_SECONDS = 90
EYE = "pipeline complete, NOT verified by eye"
VISUAL_BLOCK = "```visual\nroute: /page\nselector: #box\n```\n"
HANDOFF_BODY = "# Handoff\n\n## What I did\n\ndone\n"
GUARD = {
    "specifier": "specifier got the same problems back 3 times in a row; the worker is not making progress, stopping for a human",
    "coder": "coder got the same problems back 3 times in a row; the worker is not making progress, stopping for a human",
}
KEYS = {
    "run-start": [
        "v",
        "at",
        "event",
        "pid",
        "task",
        "task_path",
        "branch",
        "base",
        "start_commit",
        "from",
        "to",
        "scope",
        "agent",
        "model",
        "effort",
    ],
    "step-start": ["v", "at", "event", "pid", "step", "role", "kind", "attempt"],
    "step-end": ["v", "at", "event", "pid", "step", "role", "attempt", "outcome", "bounce_to", "head", "handoff", "seconds"],
    "wait": ["v", "at", "event", "pid", "reason", "until"],
    "proposal": ["v", "at", "event", "pid", "step", "path", "files"],
    "run-end": ["v", "at", "event", "pid", "result", "exit_code", "head", "stopped_at", "reason", "archive"],
}


def git_run(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"git {args!r}\n{result.stderr}")
    return result.stdout.strip()


def make_repo(root: Path, toml: str = BASE_TOML, task: str = "# Add one\n", task_name: str = "t.md") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    git_run(root, "init", "-b", "main")
    git_run(root, "config", "user.email", "qa@example.com")
    git_run(root, "config", "user.name", "QA")
    (root / ".gitignore").write_text(".marestail/\n")
    (root / "marestail.toml").write_text(toml)
    (root / "tasks").mkdir(exist_ok=True)
    (root / "tasks" / task_name).write_text(task)
    (root / "src").mkdir(exist_ok=True)
    (root / "src" / "a.py").write_text("x = 1\n")
    git_run(root, "add", "-A")
    git_run(root, "commit", "-q", "-m", "init")
    return root


def commit_feature(root: Path, msg: str = "add feature", content: str = "Feature: x\n") -> None:
    path = root / "features" / "x.feature"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    git_run(root, "add", "features/x.feature")
    git_run(root, "commit", "-q", "-m", msg)


def commit_toml(root: Path) -> None:
    path = root / "marestail.toml"
    path.write_text(path.read_text() + "\n\n[events]\nkeep = false\n")
    git_run(root, "add", "marestail.toml")
    git_run(root, "commit", "-q", "-m", "edit toml")


def plus(stamp: str, seconds: int) -> str:
    parsed = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    return (parsed + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def events_path(root: Path, task: str = "t") -> Path:
    return root / ".marestail" / "runs" / task / "events.jsonl"


def read_objects(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line != "not json"]


def assert_shape(path: Path) -> None:
    text = path.read_text()
    assert text.endswith("\n")
    assert "\n\n" not in text
    for line in text.splitlines():
        if line == "not json":
            continue
        _check_line_shape(line)


def _check_line_shape(line: str) -> None:
    obj = json.loads(line)
    assert json.dumps(obj) == line
    assert list(obj) == KEYS[obj["event"]]
    assert obj["v"] == 1
    assert obj["pid"] == os.getpid()
    assert AT_REGEX.fullmatch(obj["at"])


def marker(obj: dict[str, Any]) -> str | None:
    if obj["event"] == "step-end":
        return obj.get("outcome")
    if obj["event"] == "run-end":
        return obj.get("result")
    return None


def sequence(rows: list[dict[str, Any]]) -> list[tuple[str, str | None, str | None]]:
    steps = {"step-start", "step-end", "proposal"}
    return [(row["event"], row.get("step") if row["event"] in steps else None, marker(row)) for row in rows]


def write_report(state: Run, label: str, text: str) -> Path:
    path = state.handoffs / f"{label}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def child(args: list[str], cwd: Path | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    merged["PYTHONPATH"] = str(ROOT)
    for key in ENV_KEYS:
        merged.pop(key, None)
    if env:
        merged.update(env)
    return subprocess.run([PY, CLI, *args], cwd=cwd or ROOT, env=merged, capture_output=True, text=True)


def passing(root: Path) -> Callable[..., None]:
    counter = {"n": 0}

    def plan(state: Run, label: str, prompt: str, rebuild: Any = None) -> None:
        role = label.split("-", 1)[1]
        if role == "specifier":
            counter["n"] += 1
            stamp = git_run(root, "rev-parse", "--short", "HEAD")
            commit_feature(root, f"add feature {counter['n']}", f"Feature: x {counter['n']} {stamp}\n")
            write_report(state, label, "# Specifier handoff\n\n## What I did\n\nwrote it\n")
        elif role == "critic":
            write_report(state, label, "VERDICT: PASS\n")
        else:
            raise AssertionError(label)

    return plan


def handoff_plan(text: str) -> Callable[..., None]:
    def plan(state: Run, label: str, prompt: str, rebuild: Any = None) -> None:
        write_report(state, label, text)

    return plan


def join_timeline(root: Path, rows: list[dict[str, Any]], task: str = "t") -> None:
    doc = json.loads((root / ".marestail" / "runs" / task / "timeline.json").read_text())
    found = {(step["id"], step["attempt"]): step for step in doc["steps"]}
    for row in rows:
        _check_timeline_row(row, found)


def _check_timeline_row(row: dict[str, Any], found: dict[tuple[str, int], dict[str, Any]]) -> None:
    if row["event"] not in {"step-start", "step-end"}:
        return
    step = found[(row["step"], row["attempt"])]
    if row["event"] == "step-start":
        assert row["at"] == step["started_at"]
        return
    assert row["at"] == step["ended_at"]
    started = datetime.strptime(step["started_at"], "%Y-%m-%dT%H:%M:%S.%fZ")
    ended = datetime.strptime(step["ended_at"], "%Y-%m-%dT%H:%M:%S.%fZ")
    assert row["seconds"] == round((ended - started).total_seconds(), 3)


def ends(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if row["event"] == "step-end"]


class Tty(io.StringIO):
    def __init__(self, text: str, is_tty: bool) -> None:
        super().__init__(text)
        self._tty = is_tty

    def isatty(self) -> bool:
        return self._tty


def drive(root: Path, plan: Any, **kw: Any) -> dict[str, Any]:
    task = kw.get("task") or (root / "tasks" / "t.md")
    raw = kw.get("raw")
    if raw is None:
        raw = tomllib.loads((root / "marestail.toml").read_text())
    config = Config(root=root, raw=raw)
    saved_env, saved = _save_drive_state()
    heads: list[str] = []
    prompts: list[str] = []
    order: list[str] = []
    out, err = io.StringIO(), io.StringIO()
    _install_drive_mocks(root, task, config, plan, heads, prompts, order, out, saved, kw)
    try:
        return _run_drive(root, task, out, err, heads, prompts, order, kw)
    finally:
        _restore_drive_state(saved_env, saved)


def _attr(owner: Any, name: str) -> Any:
    return getattr(owner, name)


def _set(owner: Any, name: str, value: Any) -> None:
    setattr(owner, name, value)


def _save_drive_state() -> tuple[dict[str, str | None], dict[str, Any]]:
    saved_env = {key: os.environ.get(key) for key in ENV_KEYS}
    for key in ENV_KEYS:
        os.environ.pop(key, None)
    config_module = _attr(runner, "config_module")
    nice = _attr(runner, "nice")
    dandelion = _attr(runner, "dandelion")
    saved = {
        "load": config_module.load,
        "apply": nice.apply,
        "invoke": runner.invoke,
        "gates": _attr(runner, "run_gates"),
        "audit": _attr(runner, "audit").problems,
        "hunks": _attr(runner, "hunks").problems,
        "backend": runner.run_backend,
        "elapsed": _attr(runner, "elapsed"),
        "require": dandelion.require,
        "choose": dandelion.choose,
        "wait": runner.LIMIT_WAIT_SECONDS,
        "waits": runner.LIMIT_WAITS,
        "sleep": _attr(runner, "time").sleep,
        "rounds": runner.MAX_JUDGE_ROUNDS,
        "summary": runner.proposals_summary,
        "window": _attr(runner, "window"),
        "read": _attr(runner, "task_file").read,
        "focus": _attr(runner, "resolve_focus"),
        "hook": _attr(runner, "hook_focus"),
        "reproduce": _attr(reported, "visual").reproduce,
        "sigterm": signal.getsignal(signal.SIGTERM),
        "sigint": signal.getsignal(signal.SIGINT),
        "stdin": sys.stdin,
    }
    return saved_env, saved


def _install_drive_mocks(
    root: Path,
    task: Path,
    config: Config,
    plan: Any,
    heads: list[str],
    prompts: list[str],
    order: list[str],
    out: io.StringIO,
    saved: dict[str, Any],
    kw: dict[str, Any],
) -> None:
    _set(_attr(runner, "config_module"), "load", lambda _start: config)
    _set(_attr(runner, "nice"), "apply", lambda _config=None: None)
    if kw.get("invoke", True):
        runner.invoke = _make_drive_invoke(root, task, plan, heads, prompts, kw)
    if kw.get("watch_summary", True) and kw.get("invoke", True):
        _set(runner, "proposals_summary", _make_drive_summary(root, task, out, order, saved["summary"]))
    _apply_extra_mocks(kw)


def _make_drive_invoke(root: Path, task: Path, plan: Any, heads: list[str], prompts: list[str], kw: dict[str, Any]) -> Callable[..., None]:
    def invoke(state: Run, label: str, prompt: str, rebuild: Any = None) -> None:
        prompts.append(prompt)
        if kw.get("boundary", True) and re.fullmatch(r"\d+-.+", label):
            last = read_objects(events_path(root, task.stem))[-1]
            assert (last["event"], last["step"]) == ("step-start", label)
        heads.append(git_run(root, "rev-parse", "HEAD"))
        if plan is not None:
            plan(state, label, prompt, rebuild)
        state.attempt_agent = {
            "backend": "claude",
            "model": state.model or "claude",
            "effort": state.effort,
            "account": None,
            "minutes": 0.0,
            "summary": "ok",
        }

    return invoke


def _make_drive_summary(
    root: Path, task: Path, out: io.StringIO, order: list[str], real_summary: Callable[[Run], str]
) -> Callable[[Run], str]:
    def wrapped_summary(state: Run) -> str:
        rows = read_objects(events_path(root, task.stem))
        assert rows[-1]["event"] == "run-end"
        if rows[-1]["result"] == "complete":
            assert "pipeline complete" in out.getvalue()
        order.append("summary")
        return real_summary(state)

    return wrapped_summary


_SIMPLE_MOCKS = (
    ("gates", "run_gates"),
    ("backend", "run_backend"),
    ("elapsed", "elapsed"),
    ("require", "require"),
    ("choose", "choose"),
    ("wait", "LIMIT_WAIT_SECONDS"),
    ("waits", "LIMIT_WAITS"),
    ("rounds", "MAX_JUDGE_ROUNDS"),
    ("window", "window"),
    ("hook", "hook_focus"),
    ("resolve", "resolve_focus"),
)


def _module_mock_targets() -> list[tuple[str, Any, str]]:
    return [
        ("sleep", _attr(runner, "time"), "sleep"),
        ("audit", _attr(runner, "audit"), "problems"),
        ("hunks", _attr(runner, "hunks"), "problems"),
        ("read", _attr(runner, "task_file"), "read"),
        ("reproduce", _attr(reported, "visual"), "reproduce"),
    ]


def _apply_extra_mocks(kw: dict[str, Any]) -> None:
    _map_runner_mocks(kw)
    _map_module_mocks(kw)
    if "stdin" in kw:
        sys.stdin = kw["stdin"]


def _map_runner_mocks(kw: dict[str, Any]) -> None:
    for kw_key, attr in _SIMPLE_MOCKS:
        if kw_key in kw:
            _set_runner_attr(attr, kw[kw_key])


def _map_module_mocks(kw: dict[str, Any]) -> None:
    for kw_key, obj, attr in _module_mock_targets():
        if kw_key in kw:
            setattr(obj, attr, kw[kw_key])


def _set_runner_attr(attr: str, value: Any) -> None:
    if attr in ("require", "choose"):
        _set(_attr(runner, "dandelion"), attr, value)
    else:
        setattr(runner, attr, value)


def _run_drive(
    root: Path,
    task: Path,
    out: io.StringIO,
    err: io.StringIO,
    heads: list[str],
    prompts: list[str],
    order: list[str],
    kw: dict[str, Any],
) -> dict[str, Any]:
    before = git_run(root, "rev-parse", "HEAD") if kw.get("git", True) else None
    value = None
    exc = None
    with redirect_stdout(out), redirect_stderr(err):
        try:
            value = runner.run_pipeline(
                task,
                kw.get("start", "specifier"),
                kw.get("stop", "critic"),
                kw.get("auto", True),
                kw.get("model", "m"),
                kw.get("retries", 0),
                kw.get("agent", "claude"),
                kw.get("effort"),
                kw.get("scope"),
                kw.get("focus"),
            )
        except BaseException as caught:
            exc = caught
    path = events_path(root, task.stem)
    rows = read_objects(path) if path.is_file() else None
    if rows is not None and kw.get("shape", True):
        assert_shape(path)
    return {
        "value": value,
        "out": out.getvalue(),
        "err": err.getvalue(),
        "exc": exc,
        "rows": rows,
        "heads": heads,
        "prompts": prompts,
        "before": before,
        "order": order,
    }


def _restore_drive_state(saved_env: dict[str, str | None], saved: dict[str, Any]) -> None:
    _set(_attr(runner, "config_module"), "load", saved["load"])
    _set(_attr(runner, "nice"), "apply", saved["apply"])
    runner.invoke = saved["invoke"]
    _set(runner, "run_gates", saved["gates"])
    _set(_attr(runner, "audit"), "problems", saved["audit"])
    _set(_attr(runner, "hunks"), "problems", saved["hunks"])
    runner.run_backend = saved["backend"]
    _set(runner, "elapsed", saved["elapsed"])
    dandelion = _attr(runner, "dandelion")
    _set(dandelion, "require", saved["require"])
    _set(dandelion, "choose", saved["choose"])
    runner.LIMIT_WAIT_SECONDS = saved["wait"]
    runner.LIMIT_WAITS = saved["waits"]
    _set(_attr(runner, "time"), "sleep", saved["sleep"])
    runner.MAX_JUDGE_ROUNDS = saved["rounds"]
    runner.proposals_summary = saved["summary"]
    _set(runner, "window", saved["window"])
    _set(_attr(runner, "task_file"), "read", saved["read"])
    _set(runner, "resolve_focus", saved["focus"])
    _set(runner, "hook_focus", saved["hook"])
    _set(_attr(reported, "visual"), "reproduce", saved["reproduce"])
    signal.signal(signal.SIGTERM, saved["sigterm"])
    signal.signal(signal.SIGINT, saved["sigint"])
    sys.stdin = saved["stdin"]
    for key, value in saved_env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def test_pass_specifier_critic(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "pass")
    before = git_run(root, "rev-parse", "HEAD")
    got = drive(root, passing(root))
    assert got["exc"] is None
    assert got["value"] == 0
    assert got["err"] == ""
    rows = got["rows"]
    assert sequence(rows) == [
        ("run-start", None, None),
        ("step-start", "01-specifier", None),
        ("step-end", "01-specifier", "done"),
        ("step-start", "02-critic", None),
        ("step-end", "02-critic", "pass"),
        ("run-end", None, "complete"),
    ]
    start = rows[0]
    assert (start["task"], start["task_path"], start["branch"], start["base"], start["start_commit"]) == (
        "t",
        "tasks/t.md",
        "main",
        "main",
        before,
    )
    assert (start["from"], start["to"], start["scope"], start["agent"], start["model"], start["effort"]) == (
        "specifier",
        "critic",
        "all",
        "claude",
        "m",
        None,
    )
    starts = [row for row in rows if row["event"] == "step-start"]
    assert [(row["role"], row["kind"], row["attempt"]) for row in starts] == [("specifier", "worker", 1), ("critic", "judge", 1)]
    spec, critic = ends(rows)
    assert (spec["handoff"], spec["bounce_to"]) == (".marestail/handoffs/t/01-specifier.md", None)
    assert (critic["handoff"], critic["bounce_to"]) == (".marestail/handoffs/t/02-critic.md", None)
    assert spec["head"] == got["heads"][1]
    assert git_run(root, "log", "-1", "--format=%s", spec["head"]) == "[claude/m] add feature 1"
    assert git_run(root, "log", "-1", "--format=%s", critic["head"]) == "[claude/m] critic verdict: PASS"
    finish = rows[-1]
    assert (finish["exit_code"], finish["stopped_at"], finish["reason"]) == (0, None, None)
    assert finish["head"] == git_run(root, "rev-parse", "HEAD")
    assert ARCHIVE_REGEX.fullmatch(finish["archive"])
    assert got["out"].endswith("pipeline complete\n\n")
    assert got["order"] == ["summary"]
    join_timeline(root, rows)


def test_second_run_appends(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "append")
    folder = root / ".marestail" / "runs" / "t"
    folder.mkdir(parents=True)
    (folder / "events.jsonl").write_text("not json\n")
    first = drive(root, passing(root))
    second = drive(root, passing(root))
    assert first["exc"] is None
    assert second["exc"] is None
    text = events_path(root).read_text()
    assert text.startswith("not json\n")
    starts = [row for row in read_objects(events_path(root)) if row["event"] == "run-start"]
    assert len(starts) == 2
    assert starts[0]["pid"] == starts[1]["pid"] == os.getpid()
    assert starts[0]["start_commit"] != starts[1]["start_commit"]
    assert_shape(events_path(root))


def test_bounce_returns_to_specifier(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "bounce")
    seen = {"n": 0}

    def plan(state: Run, label: str, prompt: str, rebuild: Any = None) -> None:
        seen["n"] += 1
        role = label.split("-", 1)[1]
        if role == "critic" and seen["n"] == 2:
            write_report(state, label, "VERDICT: BOUNCE\n1. scenario missing\n")
        elif role == "critic":
            write_report(state, label, "VERDICT: PASS\n")
        else:
            write_report(state, label, HANDOFF_BODY)

    got = drive(root, plan)
    assert got["exc"] is None
    assert got["value"] == 0
    assert sequence(got["rows"])[4] == ("step-end", "02-critic", "bounce")
    assert got["rows"][4]["bounce_to"] == "specifier"
    assert sequence(got["rows"])[-2:] == [("step-end", "04-critic", "pass"), ("run-end", None, "complete")]
    assert "   verdict BOUNCE" in got["out"]


def test_critic_repeated_findings_stop(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "stop")

    def plan(state: Run, label: str, prompt: str, rebuild: Any = None) -> None:
        role = label.split("-", 1)[1]
        body = "VERDICT: BOUNCE\n1. scenario missing\n" if role == "critic" else HANDOFF_BODY
        write_report(state, label, body)

    got = drive(root, plan)
    reason = "critic repeated the same findings twice; the worker is not making progress, stopping for a human"
    assert got["exc"] is None
    assert got["value"] == 1
    assert sequence(got["rows"])[-3:] == [
        ("step-start", "04-critic", None),
        ("step-end", "04-critic", "bounce"),
        ("run-end", None, "stopped"),
    ]
    assert "05-specifier" not in events_path(root).read_text()
    end = got["rows"][-1]
    assert (end["exit_code"], end["stopped_at"], end["reason"]) == (1, "critic", reason)
    assert reason in got["out"]
    assert "pipeline stopped at critic" in got["out"]


def test_worker_repeat_guard_stop(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "guard")
    got = drive(root, lambda *args: None, stop="specifier")
    assert got["value"] == 1
    assert got["exc"] is None
    pairs = [row for row in got["rows"] if row["event"] in {"step-start", "step-end"}]
    assert [(row["role"], row["kind"], row["attempt"]) for row in pairs if row["event"] == "step-start"] == [
        ("specifier", "worker", 1),
        ("specifier", "worker", 2),
        ("specifier", "worker", 3),
    ]
    assert [(row["role"], row["attempt"], row["outcome"]) for row in pairs if row["event"] == "step-end"] == [
        ("specifier", 1, "failed"),
        ("specifier", 2, "failed"),
        ("specifier", 3, "stopped"),
    ]
    assert all(row["step"] == "01-specifier" and row["handoff"] is None for row in ends(got["rows"]))
    end = got["rows"][-1]
    assert (end["result"], end["stopped_at"], end["reason"], end["archive"]) == (
        "stopped",
        "specifier",
        GUARD["specifier"],
        None,
    )
    assert "missing handoff .marestail/handoffs/t/01-specifier.md" in got["out"]
    assert "used all" not in got["out"]
    assert (root / ".marestail" / "handoffs" / "t").is_dir()


def silent_plan(*args: Any) -> None:
    pass


def _retries_once(
    tmp_path: Path,
    retries: int,
    plan: Any,
    gates: Any,
    outcomes: list[str],
    reason: str | None,
    complete: bool = False,
    audit: Any = None,
) -> dict[str, Any]:
    root = make_repo(tmp_path / f"retries-{retries}-{complete}")
    extra: dict[str, Any] = {"audit": audit} if audit is not None else {}
    got = drive(root, plan, start="coder", stop="coder", retries=retries, gates=gates, **extra)
    assert got["exc"] is None
    assert [(row["role"], row["attempt"], row["outcome"]) for row in ends(got["rows"])] == [
        ("coder", index, outcome) for index, outcome in enumerate(outcomes, 1)
    ]
    end = got["rows"][-1]
    if complete:
        assert (end["result"], got["value"]) == ("complete", 0)
    else:
        assert (end["result"], end["reason"], got["value"]) == ("stopped", reason, 1)
    return got


def _late_handoff(calls: dict[str, int]) -> Callable[..., None]:
    def plan(state: Run, label: str, prompt: str, rebuild: Any = None) -> None:
        calls["n"] += 1
        if calls["n"] == 2:
            write_report(state, label, HANDOFF_BODY)

    return plan


def _gates_until(fail: list[Result], calls: dict[str, int]) -> Callable[..., list[Result]]:
    def check(*args: Any) -> list[Result]:
        return fail if calls["n"] < 2 else []

    return check


def test_retries_and_repeat_guard_reasons(tmp_path: Path) -> None:
    fail = [Result("py.tests", False, "nope", [], 1.0)]
    _retries_once(tmp_path, 1, silent_plan, lambda *args: fail, ["stopped"], "coder used all 1 attempts")
    _check_retries_stdout(tmp_path, fail)
    _retries_once(tmp_path, 2, silent_plan, lambda *args: fail, ["failed", "stopped"], "coder used all 2 attempts")
    _retries_once(tmp_path, 3, silent_plan, lambda *args: fail, ["failed", "failed", "stopped"], GUARD["coder"])
    calls = {"n": 0}
    _retries_once(
        tmp_path, 2, _late_handoff(calls), _gates_until(fail, calls), ["failed", "done"], None, complete=True, audit=lambda *args: []
    )


def _check_retries_stdout(tmp_path: Path, fail: list[Result]) -> None:
    root = make_repo(tmp_path / "retries-stdout")
    got = drive(root, silent_plan, start="coder", stop="coder", retries=1, gates=lambda *args: fail)
    assert "GATE FAILED: py.tests" in got["out"]
    assert "pipeline stopped at coder" in got["out"]
    assert "used all 1 attempts" not in got["out"]


def test_justified_frozen_edit_proposal(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "proposal")
    seen = {"n": 0}

    def plan(state: Run, label: str, prompt: str, rebuild: Any = None) -> None:
        seen["n"] += 1
        role = label.split("-", 1)[1]
        if seen["n"] == 1:
            commit_toml(root)
            write_report(
                state,
                label,
                "# Specifier handoff\n\n## Config change\nAllow the events file.\n\n## What I did\n\nwrote it\n",
            )
        elif role == "specifier":
            commit_feature(root)
            write_report(state, label, HANDOFF_BODY)
        else:
            write_report(state, label, "VERDICT: PASS\n")

    got = drive(root, plan)
    assert got["exc"] is None
    assert got["value"] == 0
    assert sequence(got["rows"]) == [
        ("run-start", None, None),
        ("step-start", "01-specifier", None),
        ("proposal", "01-specifier", None),
        ("step-end", "01-specifier", "failed"),
        ("step-start", "03-specifier", None),
        ("step-end", "03-specifier", "done"),
        ("step-start", "04-critic", None),
        ("step-end", "04-critic", "pass"),
        ("run-end", None, "complete"),
    ]
    proposal = got["rows"][2]
    assert (proposal["path"], proposal["files"]) == (".marestail/handoffs/t/02-proposal.md", ["marestail.toml"])
    failed = ends(got["rows"])[0]
    assert git_run(root, "log", "-1", "--format=%s", failed["head"]) == (
        "[claude/m] Revert change to frozen files by 01-specifier, recorded as a proposal"
    )
    sentence = "marestail.toml: frozen, reverted. Your reason was recorded as .marestail/handoffs/t/02-proposal.md"
    assert sentence in got["out"]
    assert got["order"] == ["summary"]
    assert got["out"].endswith("pipeline complete\n\n")
    assert "## Config changes the agents asked for and were refused" not in got["out"]
    assert (root / got["rows"][-1]["archive"] / "02-proposal.md").is_file()


def test_stopped_justified_edit_keeps_proposal(tmp_path: Path) -> None:
    stopped = make_repo(tmp_path / "proposal-stopped")

    def once(state: Run, label: str, prompt: str, rebuild: Any = None) -> None:
        commit_toml(stopped)
        write_report(
            state,
            label,
            "# Specifier handoff\n\n## Config change\nAllow the events file.\n\n## What I did\n\nwrote it\n",
        )

    held = drive(stopped, once, stop="specifier", retries=1)
    assert held["exc"] is None
    assert held["value"] == 1
    assert sequence(held["rows"]) == [
        ("run-start", None, None),
        ("step-start", "01-specifier", None),
        ("proposal", "01-specifier", None),
        ("step-end", "01-specifier", "stopped"),
        ("run-end", None, "stopped"),
    ]
    assert held["rows"][1]["attempt"] == 1
    assert (held["rows"][2]["path"], held["rows"][2]["files"]) == (
        ".marestail/handoffs/t/02-proposal.md",
        ["marestail.toml"],
    )
    subject = git_run(stopped, "log", "-1", "--format=%s", ends(held["rows"])[0]["head"])
    assert subject == "[claude/m] Revert change to frozen files by 01-specifier, recorded as a proposal"
    end = held["rows"][-1]
    assert (end["exit_code"], end["stopped_at"], end["reason"], end["archive"]) == (
        1,
        "specifier",
        "specifier used all 1 attempts",
        None,
    )
    assert (stopped / ".marestail" / "handoffs" / "t" / "02-proposal.md").is_file()
    assert list((stopped / ".marestail" / "runs" / "t").glob("handoffs-*")) == []
    sentence = "marestail.toml: frozen, reverted. Your reason was recorded as .marestail/handoffs/t/02-proposal.md"
    assert sentence in held["out"]
    assert "used all" not in held["out"]
    assert held["out"].index("## Config changes the agents asked for and were refused") > held["out"].index("pipeline stopped at specifier")
    assert held["order"] == ["summary"]


def test_frozen_edit_without_justification_no_proposal(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "plain")

    def committed(state: Run, label: str, prompt: str, rebuild: Any = None) -> None:
        commit_toml(root)
        write_report(state, label, HANDOFF_BODY)

    got = drive(root, committed, stop="specifier", retries=1)
    assert not any(row["event"] == "proposal" for row in got["rows"])
    subject = git_run(root, "log", "-1", "--format=%s", ends(got["rows"])[0]["head"])
    assert subject == "[claude/m] Revert change to frozen files by 01-specifier"
    assert "recorded as a proposal" not in subject
    assert "marestail.toml is frozen for specifier; reverted. Work within the current configuration." in got["out"]
    dirty_root = make_repo(tmp_path / "dirty")
    before = git_run(dirty_root, "rev-parse", "HEAD")

    def dirty(state: Run, label: str, prompt: str, rebuild: Any = None) -> None:
        path = dirty_root / "marestail.toml"
        path.write_text(path.read_text() + "\n\n[events]\nkeep = false\n")
        write_report(state, label, HANDOFF_BODY)

    got_dirty = drive(dirty_root, dirty, stop="specifier", retries=1)
    assert not any(row["event"] == "proposal" for row in got_dirty["rows"])
    assert git_run(dirty_root, "rev-parse", "HEAD") == before
    assert "marestail.toml is frozen for specifier" in got_dirty["out"]
    assert "reverted" not in got_dirty["out"]


def test_disabled_or_rulebookless_judge_skips(tmp_path: Path) -> None:
    rows = [
        ("practices", BASE_TOML, "practices disabled in marestail.toml; skipping"),
        ("practices", '[git]\nbase = "main"\n[perf]\nenabled = false\n', "practices: no guidance files; skipping"),
        ("design", BASE_TOML, "design: no pattern rulebooks; skipping"),
        ("perf", BASE_TOML, "perf disabled in marestail.toml; skipping"),
    ]
    for judge, toml, line in rows:
        root = make_repo(tmp_path / f"skip-{judge}-{len(toml)}", toml)
        got = drive(root, lambda *args: (_ for _ in ()).throw(AssertionError("invoked")), start=judge, stop=judge)
        assert got["exc"] is None
        assert got["value"] == 0
        assert got["heads"] == []
        assert line in got["out"]
        assert "pipeline complete" in got["out"]
        assert "== " not in got["out"]
        assert sequence(got["rows"]) == [("run-start", None, None), ("run-end", None, "complete")]
        assert (got["rows"][0]["from"], got["rows"][0]["to"], got["rows"][-1]["archive"], got["rows"][-1]["exit_code"]) == (
            judge,
            judge,
            None,
            0,
        )
        assert not (root / ".marestail" / "runs" / "t" / "start-commit").exists()
        assert events_path(root).is_file()


def _perf_author_plan(root: Path) -> Callable[..., None]:
    def plan(state: Run, label: str, prompt: str, rebuild: Any = None) -> None:
        if label == "perf-author-1":
            last = read_objects(events_path(root))[-1]
            assert (last["event"], last["step"].endswith("-perf")) == ("step-start", True)
            return
        write_report(state, label, {"01-perf": "VERDICT: AUTHOR\n", "02-perf": "VERDICT: PASS\n"}[label])

    return plan


def test_perf_authoring_not_step(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = make_repo(tmp_path / "perf", '[git]\nbase = "main"\n[practices]\nenabled = false\n')
    monkeypatch.setattr(_attr(runner, "perf_db"), "prepare", lambda *args: None)
    monkeypatch.setattr(_attr(runner, "perf_db"), "release", lambda *args: None)
    got = drive(root, _perf_author_plan(root), start="perf", stop="perf", retries=2)
    assert got["exc"] is None
    assert got["value"] == 0
    assert got["out"].count("   perf-author-1: benches unchanged") == 2
    assert "== perf (perf-author-1)" not in got["out"]
    assert sequence(got["rows"]) == [
        ("run-start", None, None),
        ("step-start", "01-perf", None),
        ("step-end", "01-perf", "failed"),
        ("step-start", "02-perf", None),
        ("step-end", "02-perf", "pass"),
        ("run-end", None, "complete"),
    ]
    failed, passed = ends(got["rows"])
    assert failed["head"] == got["rows"][0]["start_commit"]
    assert passed["head"] != failed["head"]
    assert (got["rows"][0]["from"], got["rows"][0]["to"]) == ("perf", "perf")


def test_hardener_rework_no_progress_stops(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "rework")

    def plan(state: Run, label: str, prompt: str, rebuild: Any = None) -> None:
        if "hardener" in label:
            write_report(state, label, "VERDICT: BOUNCE\n1. same problem\n")

    got = drive(root, plan, start="hardener", stop="hardener", gates=lambda *args: [], audit=lambda *args: [])
    assert got["exc"] is None
    assert got["value"] == 1
    assert sequence(got["rows"]) == [
        ("run-start", None, None),
        ("step-start", "01-hardener", None),
        ("step-end", "01-hardener", "bounce"),
        ("step-start", "02-coder", None),
        ("step-end", "02-coder", "failed"),
        ("step-start", "02-coder", None),
        ("step-end", "02-coder", "failed"),
        ("step-start", "02-coder", None),
        ("step-end", "02-coder", "stopped"),
        ("run-end", None, "stopped"),
    ]
    assert got["rows"][2]["bounce_to"] == "coder"
    assert all(row["role"] == "coder" for row in got["rows"] if row.get("step") == "02-coder")
    end = got["rows"][-1]
    assert (end["exit_code"], end["stopped_at"], end["reason"]) == (1, "hardener", GUARD["coder"])
    assert "pipeline stopped at hardener" in got["out"]
    assert GUARD["coder"] in got["out"]


def _failing_full_gate(root: Path, order: list[tuple[str, bool]]) -> Callable[..., list[Result]]:
    def gates(tier: str, *args: Any) -> list[Result]:
        written = events_path(root).read_text() if events_path(root).is_file() else ""
        order.append((tier, "step-start" in written))
        if tier == "full":
            return [Result("py.tests", False, "nope", [], 1.0)]
        return []

    return gates


def _role_verdict_plan(role: str, verdict: str, other: str) -> Callable[..., None]:
    def plan(state: Run, label: str, prompt: str, rebuild: Any = None) -> None:
        write_report(state, label, verdict if role in label else other)

    return plan


def test_hardener_gate_failure_bounces(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "gate")
    order: list[tuple[str, bool]] = []
    plan = _role_verdict_plan("hardener", "VERDICT: PASS\n", HANDOFF_BODY)
    got = drive(root, plan, start="hardener", stop="hardener", gates=_failing_full_gate(root, order), audit=lambda *args: [])
    reason = "hardener repeated the same findings twice; the worker is not making progress, stopping for a human"
    assert sequence(got["rows"]) == [
        ("run-start", None, None),
        ("step-start", "01-hardener", None),
        ("step-end", "01-hardener", "bounce"),
        ("step-start", "02-coder", None),
        ("step-end", "02-coder", "done"),
        ("step-start", "03-hardener", None),
        ("step-end", "03-hardener", "bounce"),
        ("run-end", None, "stopped"),
    ]
    assert got["rows"][2]["bounce_to"] == "coder"
    assert "gate" not in got["rows"][2]
    assert got["rows"][-1]["reason"] == reason
    assert got["value"] == 1
    doc = json.loads((root / ".marestail" / "runs" / "t" / "timeline.json").read_text())
    hardener = [step for step in doc["steps"] if step["role"] == "hardener"]
    assert hardener[0]["gate"] == [{"name": "py.tests", "seconds": 1.0, "ok": False}]
    assert order[0] == ("full", False)
    assert "   verdict BOUNCE" in got["out"]
    assert "GATE FAILED" not in got["out"]


def test_bounce_to_judge_stops(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "nonworker")
    got = drive(root, handoff_plan("VERDICT: BOUNCE hardener\n1. not a worker\n"), start="critic", stop="critic")
    assert ends(got["rows"])[0]["bounce_to"] == "hardener"
    end = got["rows"][-1]
    assert (end["result"], end["exit_code"], end["stopped_at"], end["reason"]) == (
        "stopped",
        1,
        "critic",
        "critic bounced to hardener, which is not a worker",
    )
    assert "   verdict BOUNCE to hardener" in got["out"]
    assert "pipeline stopped at critic" in got["out"]
    assert "which is not a worker" not in got["out"]


def test_judge_round_cap_and_bounce_cap(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "rounds")
    seen = {"n": 0}

    def plan(state: Run, label: str, prompt: str, rebuild: Any = None) -> None:
        if "critic" in label:
            seen["n"] += 1
            letter = "A" if seen["n"] == 1 else "B"
            write_report(state, label, f"VERDICT: BOUNCE\n1. finding {letter}\n")
        else:
            write_report(state, label, HANDOFF_BODY)

    got = drive(root, plan, start="critic", stop="critic", rounds=2)
    assert runner.MAX_JUDGE_ROUNDS == 1000
    assert sequence(got["rows"]) == [
        ("run-start", None, None),
        ("step-start", "01-critic", None),
        ("step-end", "01-critic", "bounce"),
        ("step-start", "02-specifier", None),
        ("step-end", "02-specifier", "done"),
        ("step-start", "03-critic", None),
        ("step-end", "03-critic", "bounce"),
        ("step-start", "04-specifier", None),
        ("step-end", "04-specifier", "done"),
        ("run-end", None, "stopped"),
    ]
    assert got["rows"][-1]["reason"] == "critic ran 2 rounds without a pass"
    assert "pipeline stopped at critic" in got["out"]
    assert "ran 2 rounds" not in got["out"]
    assert "ran 1000 rounds" not in got["out"]
    _check_bounce_cap(tmp_path)


def _one_bounce(step: Step) -> Step:
    if isinstance(step, Judge) and step.name == "critic":
        return replace(step, bounces=1)
    return step


def _check_bounce_cap(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "cap")
    real = _attr(runner, "window")

    def window(start: Any, stop: Any, mode: Any = None, visual: Any = False) -> list[Step]:
        return [_one_bounce(step) for step in real(start, stop, mode, visual)]

    seen = {"n": 0}

    def plan(state: Run, label: str, prompt: str, rebuild: Any = None) -> None:
        if "critic" in label:
            seen["n"] += 1
            write_report(state, label, f"VERDICT: BOUNCE\n1. finding {'A' if seen['n'] == 1 else 'B'}\n")
        else:
            write_report(state, label, HANDOFF_BODY)

    got = drive(root, plan, start="critic", stop="critic", window=window)
    assert sequence(got["rows"])[-2:] == [("step-end", "03-critic", "bounce"), ("run-end", None, "stopped")]
    assert "04-specifier" not in events_path(root).read_text()
    reason = "critic still bouncing after 1 rounds; stopping for a human"
    assert got["rows"][-1]["reason"] == reason
    assert reason in got["out"]


def test_judge_no_verdict_made_up_bounce(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "noverdict")
    seen = {"c": 0}

    def plan(state: Run, label: str, prompt: str, rebuild: Any = None) -> None:
        if "critic" in label:
            seen["c"] += 1
            if seen["c"] > 1:
                write_report(state, label, "VERDICT: PASS\n")
        else:
            write_report(state, label, HANDOFF_BODY)

    got = drive(root, plan, start="critic", stop="critic", retries=1)
    failed = ends(got["rows"])[0]
    assert (failed["step"], failed["outcome"], failed["handoff"], failed["head"]) == (
        "01-critic",
        "failed",
        None,
        got["rows"][0]["start_commit"],
    )
    assert sequence(got["rows"])[3] == ("step-start", "02-specifier", None)
    assert "critic wrote no verdict; retrying" in got["out"]
    assert all("produced no verdict" not in json.dumps(row) for row in got["rows"])
    assert "critic produced no verdict after 1 attempts" in got["prompts"][1]
    assert got["rows"][-1]["result"] == "complete"
    assert got["value"] == 0


def test_give_up_keeps_a_written_non_verdict_report(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "kept")
    seen = {"c": 0}

    def plan(state: Run, label: str, prompt: str, rebuild: Any = None) -> None:
        if "critic" in label:
            seen["c"] += 1
            write_report(state, label, "no verdict here\n" if seen["c"] == 1 else "VERDICT: PASS\n")
        else:
            write_report(state, label, HANDOFF_BODY)

    got = drive(root, plan, start="critic", stop="critic", retries=1)
    assert got["exc"] is None
    assert got["value"] == 0
    assert sequence(got["rows"]) == [
        ("run-start", None, None),
        ("step-start", "01-critic", None),
        ("step-end", "01-critic", "failed"),
        ("step-start", "02-specifier", None),
        ("step-end", "02-specifier", "done"),
        ("step-start", "03-critic", None),
        ("step-end", "03-critic", "pass"),
        ("run-end", None, "complete"),
    ]
    archive = got["rows"][-1]["archive"]
    assert (root / archive / "01-critic.md").read_text() == "no verdict here\n"


def test_run_start_without_writer_is_a_noop(tmp_path: Path) -> None:
    state = Run(config=Config(root=tmp_path, raw={}), task=tmp_path / "t.md", model=None, retries=0)
    start_run: Any = runner.record_run_start
    assert start_run(state, []) is None
    assert not (tmp_path / ".marestail").exists()


def test_declining_critic_approval(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "approval-no")
    got = drive(root, handoff_plan("VERDICT: PASS\n"), start="critic", stop="critic", auto=False, stdin=Tty("n\n", True))
    assert got["value"] == 1
    assert got["exc"] is None
    assert sequence(got["rows"]) == [
        ("run-start", None, None),
        ("step-start", "01-critic", None),
        ("step-end", "01-critic", "pass"),
        ("run-end", None, "stopped"),
    ]
    end = got["rows"][-1]
    assert (end["exit_code"], end["stopped_at"], end["reason"]) == (1, "critic", "not approved")
    assert "continue to coder? [y/N] " in got["out"]
    assert "not approved" not in got["out"]
    assert "pipeline stopped" not in got["out"]
    _check_approval_yes(tmp_path)


def _check_approval_yes(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "approval-yes")
    got = drive(root, handoff_plan("VERDICT: PASS\n"), start="critic", stop="critic", auto=False, stdin=Tty("", False))
    assert "non-interactive: continuing without approval (use --to critic to stop here)" in got["out"]
    assert "pipeline complete" in got["out"]
    assert got["rows"][-1]["result"] == "complete"
    assert got["value"] == 0
    assert got["rows"][-1]["reason"] is None


def test_qa_names_what_it_ran_against(tmp_path: Path) -> None:
    cases = [
        ("ran-against: app\n", 0, None),
        ("ran-against: harness\n", 3, "pipeline complete, NOT verified against the running app (qa ran against a harness)"),
        ("ran-against: nothing\n", 3, "pipeline complete, NOT verified against the running app (qa ran against nothing)"),
    ]
    for index, (text, code, reason) in enumerate(cases):
        root = make_repo(tmp_path / f"qa-{index}")
        got = drive(root, handoff_plan(text), start="qa", stop="qa", gates=lambda *args: [])
        end = got["rows"][-1]
        assert got["exc"] is None
        assert got["value"] == code
        assert (end["result"], end["exit_code"], end["reason"]) == ("complete", code, reason)
        if reason is None:
            assert "pipeline complete" in got["out"]
            assert "NOT verified" not in got["out"]
        else:
            assert reason in got["out"]


def _limited(*args: Any) -> tuple[int, str]:
    return 1, "rate limit exceeded"


def _visual_plan(qa_text: str | None = None) -> Callable[..., None]:
    def plan(state: Run, label: str, prompt: str, rebuild: Any = None) -> None:
        role = label.split("-", 1)[1]
        text = {"visual": "VERDICT: PASS\n", "qa": qa_text}[role]
        assert isinstance(text, str)
        write_report(state, label, text)

    return plan


def _visual_repo(folder: Path) -> Path:
    root = make_repo(folder, BASE_TOML + "[visual]\nenabled = true\n")
    (root / "qa").mkdir(exist_ok=True)
    (root / "qa" / "t.md").write_text(VISUAL_BLOCK)
    git_run(root, "add", "qa/t.md")
    git_run(root, "commit", "-q", "-m", "qa")
    return root


def _eye_run(folder: Path, plan_fn: Any, stop: str, reason: str, eye_on_stdout: bool) -> dict[str, Any]:
    got = drive(
        _visual_repo(folder), plan_fn, start="visual", stop=stop, agent="kilo", model=None, gates=lambda *args: [], backend=_limited
    )
    end = got["rows"][-1]
    assert got["exc"] is None
    assert got["value"] == 3
    assert (end["result"], end["exit_code"], end["stopped_at"], end["reason"]) == ("complete", 3, None, reason)
    if eye_on_stdout:
        assert EYE in got["out"]
    else:
        assert "NOT verified by eye" not in got["out"]
        assert reason in got["out"]
    return got


def test_blind_visual_fallback(tmp_path: Path) -> None:
    seen = _eye_run(tmp_path / "eye-visual", _visual_plan(), "visual", EYE, True)
    assert "visual: claude-fable-5-1 is out of usage; judging with kilo" in seen["out"]
    assert "   verdict PASS (geometry only, pictures not seen)" in seen["out"]
    _eye_run(tmp_path / "eye-app", _visual_plan("ran-against: app\n"), "qa", EYE, True)
    harness_reason = "pipeline complete, NOT verified against the running app (qa ran against a harness)"
    _eye_run(tmp_path / "eye-harness", _visual_plan("ran-against: harness\n"), "qa", harness_reason, False)


def test_rate_limit_and_dandelion_miss_waits(tmp_path: Path) -> None:
    pause = {"wait": WAIT_SECONDS, "waits": 12, "sleep": lambda _seconds: None}
    root = make_repo(tmp_path / "wait-once")
    calls = {"n": 0}

    def backend(state: Run, name: str, prompt: str, prompt_file: Path) -> tuple[int, str]:
        calls["n"] += 1
        return (1, "rate limit exceeded") if calls["n"] == 1 else (0, "ok")

    got = drive(root, None, invoke=False, stop="specifier", retries=1, backend=backend, boundary=False, **pause)
    waits = [row for row in got["rows"] if row["event"] == "wait"]
    assert len(waits) == 1
    assert waits[0]["reason"] == "rate-limit"
    assert waits[0]["until"] == plus(waits[0]["at"], WAIT_SECONDS)
    assert waits[0]["until"] != waits[0]["at"]
    assert "   rate limited; waiting 1 min before retrying 01-specifier" in got["out"]
    _check_twelve_waits(tmp_path, pause)
    _check_dandelion_miss(tmp_path, pause)


def _check_twelve_waits(tmp_path: Path, pause: dict[str, Any]) -> None:
    root = make_repo(tmp_path / "wait-twelve")
    got = drive(
        root,
        None,
        invoke=False,
        stop="specifier",
        retries=1,
        backend=lambda *args: (1, "rate limit exceeded"),
        boundary=False,
        **pause,
    )
    waits = [row for row in got["rows"] if row["event"] == "wait"]
    assert len(waits) == 12
    assert all(row["until"] == plus(row["at"], WAIT_SECONDS) for row in waits)
    assert got["rows"][-1]["reason"] == "specifier used all 1 attempts"
    assert "   01-specifier: still rate limited after 12 waits" in got["out"]


def _check_dandelion_miss(tmp_path: Path, pause: dict[str, Any]) -> None:
    root = make_repo(tmp_path / "wait-route")
    chosen = {"n": 0}

    def choose(mode: Any, folder: Any) -> tuple[Choice | None, str]:
        chosen["n"] += 1
        if chosen["n"] == 1:
            return None, "no subscription has quota left"
        return Choice("claude-opus-5 high claude", "claude", "claude-opus-5", "high", {}), ""

    got = drive(
        root,
        None,
        invoke=False,
        stop="specifier",
        retries=1,
        model="dandelion/route",
        agent=None,
        require=lambda: None,
        choose=choose,
        backend=lambda *args: (0, "ok"),
        boundary=False,
        **pause,
    )
    start = got["rows"][0]
    assert (start["agent"], start["model"], start["effort"]) == (None, "dandelion/route", None)
    waits = [row for row in got["rows"] if row["event"] == "wait"]
    assert len(waits) == 1
    assert waits[0]["reason"] == "dandelion-unrouted"
    assert waits[0]["until"] == plus(waits[0]["at"], WAIT_SECONDS)
    assert "   dandelion/route: no subscription has quota left; waiting 1 min before retrying 01-specifier" in got["out"]


def test_session_line_says_finished_in(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "session")

    def backend(state: Run, name: str, prompt: str, prompt_file: Path) -> tuple[int, str]:
        label = prompt_file.name.removesuffix(".prompt.md")
        role = label.split("-", 1)[1]
        if role == "specifier":
            commit_feature(root)
            write_report(state, label, HANDOFF_BODY)
        else:
            write_report(state, label, "VERDICT: PASS\n")
        return 0, "ok"

    got = drive(root, None, invoke=False, backend=backend, elapsed=lambda started: 0, boundary=False, watch_summary=False)
    assert got["exc"] is None
    assert got["err"] == ""
    assert got["value"] == 0
    assert "   01-specifier finished in 0.0 min: ok" in got["out"]
    assert "pipeline complete" in got["out"]


def test_events_directory_warns_once(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "dir-a")
    first = drive(root, passing(root), elapsed=lambda started: 0)
    other = make_repo(tmp_path / "dir-b")
    path = events_path(other)
    path.parent.mkdir(parents=True)
    path.mkdir()
    second = drive(
        other,
        passing(other),
        elapsed=lambda started: 0,
        boundary=False,
        shape=False,
        watch_summary=False,
    )
    assert first["out"] == second["out"]
    assert first["value"] == second["value"] == 0
    assert second["err"].count("\n") == 1
    assert second["err"].startswith("warning: cannot write run events to ")
    assert "Is a directory" in second["err"]
    assert "events.jsonl" in second["err"]
    third = drive(other, passing(other), elapsed=lambda started: 0, boundary=False, shape=False, watch_summary=False)
    assert third["err"].count("\n") == 1
    assert third["err"].startswith("warning: cannot write run events to ")


def test_signals_and_exceptions_propagate(tmp_path: Path) -> None:
    cases = [
        (KeyboardInterrupt(), KeyboardInterrupt, "interrupted", 130, "SIGINT"),
        ("term", SystemExit, "interrupted", 143, "SIGTERM"),
        (RuntimeError("boom"), RuntimeError, "failed", 1, "RuntimeError: boom"),
        (RuntimeError("boom\nnext"), RuntimeError, "failed", 1, "RuntimeError: boom"),
        (SystemExit(4), SystemExit, "failed", 4, "SystemExit: 4"),
        (SystemExit("nope"), SystemExit, "failed", 1, "SystemExit: nope"),
        (SystemExit(), SystemExit, "failed", 0, "SystemExit: "),
    ]
    for index, (raised, kind, result, code, reason) in enumerate(cases):
        root = make_repo(tmp_path / f"sig-{index}")
        _run_signal_case(root, raised, kind, result, code, reason)
    _check_sig_rework(tmp_path)


def _raising_plan(raised: Any) -> Callable[..., None]:
    def plan(state: Run, label: str, prompt: str, rebuild: Any = None) -> None:
        if raised == "term":
            handler: Any = signal.getsignal(signal.SIGTERM)
            try:
                handler(signal.SIGTERM, None)
            except BaseException as caught:
                assert not isinstance(caught, Exception)
                assert not isinstance(caught, (SystemExit, KeyboardInterrupt))
                assert caught.args == (signal.SIGTERM,)
                raise
        raise raised

    return plan


def _run_signal_case(root: Path, raised: Any, kind: type, result: str, code: int, reason: str) -> None:
    got = drive(root, _raising_plan(raised), stop="specifier")
    assert isinstance(got["exc"], kind)
    if raised == "term":
        assert _attr(got["exc"], "code") == 143
    events_list = [row["event"] for row in got["rows"]]
    assert "step-end" not in events_list
    assert events_list[1] == "step-start"
    assert events_list[-1] == "run-end"
    end = got["rows"][-1]
    assert (end["result"], end["exit_code"], end["stopped_at"], end["reason"]) == (result, code, "specifier", reason)


def _check_sig_rework(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "sig-rework")

    def plan(state: Run, label: str, prompt: str, rebuild: Any = None) -> None:
        if "hardener" in label:
            write_report(state, label, "VERDICT: BOUNCE\n1. same problem\n")
        else:
            raise KeyboardInterrupt

    got = drive(root, plan, start="hardener", stop="hardener", gates=lambda *args: [], audit=lambda *args: [])
    assert isinstance(got["exc"], KeyboardInterrupt)
    assert ("step-end", "01-hardener", "bounce") in sequence(got["rows"])
    assert ("step-start", "02-coder", None) in sequence(got["rows"])
    assert not any(row["event"] == "step-end" and row["step"] == "02-coder" for row in got["rows"])
    assert got["rows"][-1]["stopped_at"] == "hardener"
    assert got["rows"][-1]["reason"] == "SIGINT"


def test_bug_cannot_be_reproduced(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "repro", BASE_TOML + "[visual]\nenabled = true\n", "where: /page\nsymptom: the box is square\n")
    called = {"n": 0}

    def reproduce(config: Config, report: Any) -> None:
        called["n"] += 1
        return None

    got = drive(
        root,
        lambda *args: (_ for _ in ()).throw(AssertionError("invoked")),
        stop="specifier",
        reproduce=reproduce,
    )
    reason = "pipeline stopped before specifier: the bug could not be reproduced"
    assert called["n"] == 1
    assert got["exc"] is None
    assert got["value"] == 1
    assert sequence(got["rows"]) == [("run-start", None, None), ("run-end", None, "stopped")]
    assert (got["rows"][-1]["exit_code"], got["rows"][-1]["stopped_at"], got["rows"][-1]["reason"]) == (1, "specifier", reason)
    assert reason in got["out"]
    assert "pipeline stopped at" not in got["out"]
    assert "pipeline complete" not in got["out"]


def test_scope_model_effort_branch_values(tmp_path: Path) -> None:
    def one(name: str, **kw: Any) -> Any:
        repo_root = kw.pop("root", None) or make_repo(tmp_path / name, kw.pop("toml", BASE_TOML))
        got = drive(repo_root, handoff_plan(HANDOFF_BODY), stop="specifier", **kw)
        assert got["exc"] is None
        return got["rows"][0]

    assert (one("scope-all")["scope"], one("scope-all2")["agent"], one("scope-all3")["model"], one("scope-all4")["branch"]) == (
        "all",
        "claude",
        "m",
        "main",
    )
    assert one("scope-changed", scope="changed")["scope"] == "changed"
    assert one("scope-focus", focus=["src/a.py"], model="m")["scope"] == "changed"
    assert one("scope-hard", scope="hard", focus=["src/a.py"])["scope"] == "hard"
    assert one("scope-hyper", scope="hyper")["scope"] == "hyper"
    assert one("scope-effort", effort="low")["effort"] == "low"
    assert (one("scope-nomodel", model=None)["model"], one("scope-nomodel2", model=None)["agent"]) == (None, "claude")
    _check_toml_options(tmp_path, one)
    _check_hyper_and_visual(tmp_path)


def _check_toml_options(tmp_path: Path, one: Callable[..., dict[str, Any]]) -> None:
    toml = BASE_TOML + '[agent]\nmodel = "from-toml"\n'
    assert one("scope-toml-model", toml=toml, model=None)["model"] == "from-toml"
    toml = BASE_TOML + '[agent]\nbackend = "grok"\n'
    assert one("scope-backend", toml=toml, agent=None)["agent"] == "grok"
    toml = BASE_TOML + '[agent]\neffort = "high"\n'
    assert one("scope-toml-effort", toml=toml)["effort"] == "high"
    root = make_repo(tmp_path / "scope-detach")
    git_run(root, "checkout", "--detach")
    assert one("scope-detach", root=root)["branch"] is None
    assert one("scope-base", toml="[perf]\nenabled = false\n[practices]\nenabled = false\n")["base"] == "origin/master"
    outside = tmp_path / "outside.md"
    outside.write_text("# Outside\n")
    got = drive(root, handoff_plan(HANDOFF_BODY), stop="specifier", task=outside)
    assert Path(got["rows"][0]["task_path"]) == outside.resolve()
    assert Path(got["rows"][0]["task_path"]).is_absolute()


def _check_hyper_and_visual(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "scope-hyper-all")

    def plan(state: Run, label: str, prompt: str, rebuild: Any = None) -> None:
        role = label.split("-", 1)[1]
        if role == "qa":
            write_report(state, label, "ran-against: app\n")
        elif role in {"critic", "blast", "hardener"}:
            write_report(state, label, "VERDICT: PASS\n")
        else:
            write_report(state, label, HANDOFF_BODY)

    got = drive(
        root,
        plan,
        start=None,
        stop=None,
        scope="hyper",
        gates=lambda *args: [],
        audit=lambda *args: [],
        hunks=lambda *args: [],
    )
    assert (got["rows"][0]["from"], got["rows"][0]["to"], got["rows"][0]["scope"]) == ("specifier", "qa", "hyper")
    roles = [row["role"] for row in got["rows"] if row["event"] == "step-end"]
    assert roles == ["specifier", "critic", "coder", "architect", "blast", "hardener", "qa"]
    missing = make_repo(tmp_path / "scope-visual-missing")
    seen = child(
        ["run", "tasks/t.md", "--from", "visual", "--to", "visual", "--auto", "--model", "m", "--agent", "claude"],
        missing,
    )
    assert seen.returncode == 1
    assert seen.stdout == ""
    assert seen.stderr == (
        "unknown role visual; choose from specifier, critic, coder, cleaner, architect, design, practices, perf, hardener, qa\n"
    )
    assert not events_path(missing).exists()
    present = make_repo(tmp_path / "scope-visual", BASE_TOML + "[visual]\nenabled = true\n")
    got_visual = drive(
        present,
        lambda *args: (_ for _ in ()).throw(AssertionError("invoked")),
        start="visual",
        stop="visual",
    )
    assert "visual: no qa/t.md; skipping" in got_visual["out"]
    assert (got_visual["rows"][0]["from"], got_visual["rows"][0]["to"]) == ("visual", "visual")
    assert [row["event"] for row in got_visual["rows"]] == ["run-start", "run-end"]


def test_failures_before_window_no_events(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "early")
    cases = [
        (
            ["run", "tasks/t.md", "--model", "dandelion/route", "--agent", "grok"],
            None,
            "--model dandelion/route picks the backend and effort before every session; drop --agent and --effort\n",
        ),
        (
            ["run", "tasks/t.md", "--model", "m", "--agent", "claude"],
            {"MARESTAIL_NICE": "high"},
            "nice must be an integer 0-19, got 'high'\n",
        ),
        (
            ["run", "tasks/t.md", "--model", "dandelion/route", "--agent", "grok"],
            {"MARESTAIL_NICE": "high"},
            "nice must be an integer 0-19, got 'high'\n",
        ),
        (
            ["run", "tasks/t.md", "--scope", "hard", "--model", "m", "--agent", "claude"],
            None,
            "--scope hard needs at least one focus path: pass --focus or set [focus] paths in marestail.toml\n",
        ),
        (
            ["run", "tasks/t.md", "--scope", "all", "--focus", "src/a.py", "--model", "m", "--agent", "claude"],
            None,
            "--focus cannot be combined with --scope all\n",
        ),
        (
            ["run", "tasks/t.md", "--scope", "hyper", "--focus", "src/a.py", "--model", "m", "--agent", "claude"],
            None,
            "--focus cannot be combined with --scope hyper; hyper gates the diff and nothing else\n",
        ),
        (
            ["run", "tasks/t.md", "--focus", "src/missing", "--model", "m", "--agent", "claude"],
            None,
            f"focus path not found under {root.resolve()}: src/missing\n",
        ),
    ]
    for args, env, stderr in cases:
        seen = child(args, root, env)
        assert (seen.returncode, seen.stdout, seen.stderr) == (1, "", stderr)
        assert not events_path(root).exists()
    _check_task_and_role_failures(root)


def _check_task_and_role_failures(root: Path) -> None:
    bad = root / "tasks" / "bad.md"
    bad.write_text('+++\ndepends = ["018-a", "019-b"]\nstack = true\n+++\n# x\n')
    seen = child(["run", "tasks/bad.md", "--model", "m", "--agent", "claude"], root)
    assert seen.returncode == 1
    assert seen.stdout == ""
    assert seen.stderr == "tasks/bad.md: stack = true needs exactly one dependency, got 2\n"
    seen = child(["run", "tasks/missing.md", "--model", "m", "--agent", "claude"], root)
    assert seen.returncode == 1
    assert "FileNotFoundError" in seen.stderr
    assert "tasks/missing.md" in seen.stderr
    assert not events_path(root, "missing").exists()
    seen = child(["run", "tasks/t.md", "--scope", "hyper", "--from", "cleaner", "--model", "m", "--agent", "claude"], root)
    assert seen.returncode == 1
    assert seen.stdout == ""
    assert seen.stderr == "unknown role cleaner; choose from specifier, critic, coder, architect, blast, hardener, qa\n"
    assert (root / ".marestail" / "runs" / "t" / "start-commit").is_file()
    assert not events_path(root).exists()
    seen = child(["run", "tasks/t.md", "--from", "nope", "--model", "m", "--agent", "claude"], root)
    assert seen.returncode == 1
    assert seen.stdout == ""
    assert seen.stderr == (
        "unknown role nope; choose from specifier, critic, coder, cleaner, architect, design, practices, perf, hardener, qa\n"
    )


def test_step_end_without_timeline_writes_nothing(tmp_path: Path) -> None:
    root = tmp_path / "bare"
    root.mkdir()
    path = root / "events.jsonl"
    state = Run(
        config=Config(root=root, raw={}),
        task=root / "tasks" / "t.md",
        model="m",
        retries=0,
        events=events.Log(path),
        last_report=root / "01-specifier.md",
    )
    runner.record_step_end(state, "specifier", 1, "done", None)
    assert state.last_timeline_step is None
    assert not path.exists()


def test_module_writes_bytes(tmp_path: Path) -> None:
    folder = tmp_path / "module"
    folder.mkdir()
    path = folder / "events.jsonl"
    log = events.Log(path)
    at = "2026-09-28T14:03:07.412Z"
    log.append("wait", {"reason": "rate-limit", "until": at}, at, 4)
    line = '{"v": 1, "at": "2026-09-28T14:03:07.412Z", "event": "wait", "pid": 4, "reason": "rate-limit", "until": "2026-09-28T14:03:07.412Z"}\n'
    assert path.read_text() == line
    log.append("wait", {"reason": "rate-limit", "until": at}, at, 4)
    assert path.read_text() == line + line
    other = folder / "null.jsonl"
    events.Log(other).append(
        "run-end",
        {"result": "failed", "exit_code": 1, "head": "abc", "stopped_at": None, "reason": None, "archive": None},
        at,
        4,
    )
    assert '"stopped_at": null' in other.read_text()
    _check_module_os_calls(folder, at, line)
    _check_schema(folder, at)
    _check_module_source()


def _record(calls: list[tuple[str, Any, Any]], name: str, arg: Any, extra: Any) -> None:
    calls.append((name, arg, extra))


def _opened(calls: list[tuple[str, Any, Any]], _file: Any, flags: int, mode: int = 0o777) -> int:
    _record(calls, "open", flags, mode)
    return 7


def _written(calls: list[tuple[str, Any, Any]], fd: Any, data: bytes) -> int:
    _record(calls, "write", fd, data)
    return len(data)


def _synced(calls: list[tuple[str, Any, Any]], fd: Any) -> None:
    _record(calls, "fsync", fd, None)


def _closed(calls: list[tuple[str, Any, Any]], fd: Any) -> None:
    _record(calls, "close", fd, None)


def _fake_os(calls: list[tuple[str, Any, Any]]) -> dict[str, Any]:
    return {
        "open": partial(_opened, calls),
        "write": partial(_written, calls),
        "fsync": partial(_synced, calls),
        "close": partial(_closed, calls),
    }


def _check_module_os_calls(folder: Path, at: str, line: str) -> None:
    calls: list[tuple[str, Any, Any]] = []
    fakes = _fake_os(calls)
    events_os = _attr(events, "os")
    saved = {name: getattr(events_os, name) for name in fakes}
    for name, fake in fakes.items():
        setattr(events_os, name, fake)
    try:
        events.Log(folder / "flags.jsonl").append("wait", {"reason": "rate-limit", "until": at}, at, 4)
    finally:
        for name, original in saved.items():
            setattr(events_os, name, original)
    flags, mode = calls[0][1], calls[0][2]
    assert flags & os.O_WRONLY
    assert flags & os.O_APPEND
    assert flags & os.O_CREAT
    assert mode == 0o644
    assert calls[1][0] == "write"
    assert calls[1][2] == line.encode()
    assert ("fsync", 7, None) in calls
    assert ("close", 7, None) in calls
    _check_module_warning(folder, at)


def _check_module_warning(folder: Path, at: str) -> None:
    directory = folder / "blocked"
    directory.mkdir()
    warned = io.StringIO()
    with redirect_stderr(warned):
        blocked = events.Log(directory)
        blocked.append("wait", {"reason": "rate-limit", "until": at}, at, 4)
        blocked.append("wait", {"reason": "rate-limit", "until": at}, at, 4)
        events.Log(directory).append("wait", {"reason": "rate-limit", "until": at}, at, 4)
    text = warned.getvalue()
    assert text.count("warning: cannot write run events to ") == 2
    assert text.count("Is a directory") == 2
    assert blocked._warned is True


def _check_module_source() -> None:
    source = (ROOT / "marestail" / "events.py").read_text()
    tree = ast.parse(source)
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert [name for name in imported if name.startswith("marestail")] == ["marestail.shell"]
    assert "datetime" not in source
    assert "utc_now" not in source
    assert "time." not in source
    assert "os.environ" not in source


def _check_schema(folder: Path, at: str) -> None:
    path = folder / "order.jsonl"
    events.Log(path).append("wait", {"until": at, "extra": True, "reason": "rate-limit"}, at, 4)
    line = (
        '{"v": 1, "at": "2026-09-28T14:03:07.412Z", "event": "wait", "pid": 4, '
        '"reason": "rate-limit", "until": "2026-09-28T14:03:07.412Z"}\n'
    )
    assert path.read_text() == line
    events.Log(path).append("wait", {}, at, 4)
    missing = json.loads(path.read_text().splitlines()[1])
    assert list(missing) == ["v", "at", "event", "pid", "reason", "until"]
    assert missing["reason"] is None
    assert missing["until"] is None
    other = folder / "later.jsonl"
    events.Log(other).append("later", {"z": 1, "a": 2}, at, 4)
    assert other.read_text() == '{"v": 1, "at": "2026-09-28T14:03:07.412Z", "event": "later", "pid": 4, "z": 1, "a": 2}\n'


def test_timeline_freeze_imports_readme(tmp_path: Path) -> None:
    folder = tmp_path / "timeline"
    folder.mkdir()
    agent = {"backend": "claude", "model": "m", "effort": None, "account": None, "minutes": 0.1, "summary": "ok"}
    returned = timeline.record(
        folder, "t", "07-coder", "coder", 1, "2026-01-01T00:00:00.000Z", [], [], agent, None, [], [], folder / "missing"
    )
    data = json.loads((folder / "timeline.json").read_text())
    assert returned == data["steps"][0]
    assert "verdict" not in returned
    assert returned["ended_at"] == data["steps"][0]["ended_at"]
    _check_freeze_paths()
    _check_importlinter_contracts()
    _check_readme_content()


def _check_freeze_paths() -> None:
    config = Config(root=ROOT, raw={})
    old = [".marestail/runs/t/timeline.md", ".marestail/runs/t/timeline.json", "src/a.py", ".marestail/runs/t/19-coder.md"]
    assert freeze.frozen_paths(config, "coder", old) == old[:2]
    paths = [*old[:2], ".marestail/runs/t/events.jsonl", "src/a.py"]
    assert freeze.frozen_paths(config, "coder", paths) == [*old[:2], ".marestail/runs/t/events.jsonl"]


def _module_line(line: str, which: str, taking: bool) -> tuple[bool, list[str]]:
    key = line.split("=")[0].strip()
    if key in {"source_modules", "forbidden_modules"}:
        return key == which, []
    if not line.startswith("    "):
        return False, []
    return (taking, [line.strip()]) if taking else (False, [])


def _contract_modules(text: str, name: str, which: str) -> list[str]:
    body = text.split(f"[importlinter:contract:{name}]", 1)[1].split("\n[", 1)[0]
    modules: list[str] = []
    taking = False
    for line in body.splitlines():
        taking, added = _module_line(line, which, taking)
        modules.extend(added)
    return modules


def _check_importlinter_contracts() -> None:
    text = (ROOT / ".importlinter").read_text()
    layer = next(
        line for line in text.splitlines() if "marestail.report : marestail.config : marestail.changes : marestail._location" in line
    )
    assert "marestail.events" in layer
    assert "marestail.task_file" in layer
    assert "marestail.events" in _contract_modules(text, "foundations-import-nothing-above", "source_modules")
    assert set(_contract_modules(text, "events-stays-below-runner", "forbidden_modules")) == set(
        _contract_modules(text, "timeline-stays-below-runner", "forbidden_modules")
    )


def _check_readme_content() -> None:
    readme = (ROOT / "README.md").read_text()
    anchor = "Start it detached: `nohup setsid tools/overnight.sh ... > /dev/null 2>&1 &`."
    assert anchor in readme
    rest = readme.split(anchor, 1)[1].lstrip("\n")
    assert rest.startswith(
        "`.marestail/runs/<task>/events.jsonl` gets one JSON line per run start, attempt start, attempt end, wait, proposal and run end"
    )
    block = rest.split("Kilo Code pipeline runs", 1)[0]
    for event in ("run-start", "step-start", "step-end", "wait", "proposal", "run-end"):
        assert f"`{event}`" in block
    for field in ("start_commit", "kind", "bounce_to", "until", "files", "stopped_at"):
        assert field in block
    assert "MARESTAIL_" not in (ROOT / "marestail" / "events.py").read_text()


def test_existing_commands_keep_text(tmp_path: Path) -> None:
    seen = child(["--help"])
    assert seen.returncode == 0
    assert "{gate,run,tasks,install,sonar,watch,status,abandon,dash,perf,visual,route,remote,graph,depth}" in seen.stdout
    run_help = child(["run", "--help"]).stdout
    flags = ["--from", "--to", "--auto", "--scope", "--focus", "--model", "--retries", "--effort", "--agent"]
    indexes = [run_help.index(flag) for flag in flags]
    assert indexes == sorted(indexes)
    assert "attempts per role; 0 means unlimited (default)" in run_help
    assert "skip the approval pause after the critic" in run_help
    root = make_repo(tmp_path / "help-tasks")
    (root / "tasks" / "t.md").write_text("+++\ndepends = []\n+++\n# t\n")
    checked = child(["tasks", "check", "tasks/t.md"], root)
    assert (checked.returncode, checked.stdout, checked.stderr) == (0, "", "")
    route = child(["route", "--help"], ROOT, {"PATH": "/usr/bin:/bin"})
    assert route.returncode == 127
    assert "dandelion is not installed" in route.stderr
