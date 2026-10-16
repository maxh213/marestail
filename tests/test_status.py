import ast
import json
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict

import pytest

from marestail import status
from marestail.shell import run as real_run
from tests.conftest import git


class _Calls(TypedDict):
    reply: tuple[int, str]
    calls: int
    commands: list[list[str]]


NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC).timestamp()
REAL_PROCS = status._machine_processes
LAST: _Calls = {"reply": (1, ""), "calls": 0, "commands": []}
REAL_STRFTIME = time.strftime
REVIVE = "Move the handoffs-abandoned-* folder back to .marestail/handoffs/<task>/ and delete this file to revive it.\n"
GIVEN_UP = "abandoned 2026-10-16 09:00: superseded by 007-done"
FOUR = """== specifier (01-specifier) attempt 1
   01-specifier finished in 2.0 min: ok
== critic (02-critic) attempt 1
   99-other finished in 1.0 min: no
   02-critic finished in 3.0 min: ok
== coder (03-coder) attempt 1
   03-coder finished in 4.0 min: ok

== qa (04-qa) attempt 1
   04-qa finished in 1.0 min: ok
pipeline complete
"""
MID = "== coder (03-coder) attempt 1\n"
DONE = "== coder (03-coder) attempt 1\n   03-coder finished in 2.0 min: ok\n"
STOPPED_HUMAN = "== coder (03-coder) attempt 1\npipeline stopped at 03-coder\nstopping for a human\n"
STOPPED = "== coder (03-coder) attempt 1\npipeline stopped at 03-coder\n"
HALTED = "== coder (03-coder) attempt 1\npipeline stopped at 04-coder\n"
RATE = "== coder (03-coder) attempt 1\nrate limited; waiting 1 min before retrying 03-coder\n"
PERF = "== perf (03-perf) attempt 1\n   03-perf finished in 2.0 min: ok\nfilling perf samples\n"
AUTHOR = "== perf (03-perf) attempt 1\n   03-perf finished in 2.0 min: ok\nperf-author samples\n"
VERDICT = "== coder (03-coder) attempt 1\n   03-coder finished in 2.0 min: ok\n   verdict PASS\n"
LATER = "== specifier (01-specifier) attempt 1\n   01-specifier finished in 2.0 min: ok\n== qa (04-qa) attempt 1\n"
OLD = "== specifier (01-specifier) attempt 1\n   01-specifier finished in 2.0 min: ok\n== coder (02-coder) attempt 1\n"
DEFAULT = """\
marestail runs: 9 known, 1 running, 6 shown, 3 complete/abandoned/leftover hidden (use --all)

| Repo  | Task       | State         | Where it stands               | Last activity         | Branch  |
|-------|------------|---------------|-------------------------------|-----------------------|---------|
| alpha | 011-live   | running       | in 03-coder                   | 10-16 11:58 (2m ago)  | main    |
| beta  | 003-later  | dead          | mid 04-qa                     | 10-16 11:30 (30m ago) | main    |
| alpha | 010-killed | dead          | mid 02-critic                 | 10-16 11:10 (50m ago) | feature |
| beta  | 004-old    | dead          | mid 02-coder                  | 10-15 08:00 (28h ago) | main    |
| alpha | 009-paused | stopped       | at critic: not approved       | 10-16 10:20 (1h ago)  | main    |
| alpha | 008-fresh  | never-started | no handoffs, no steps, no log | 10-16 10:50 (1h ago)  | main    |
"""
ALL = """\
marestail runs: 9 known, 1 running, 9 shown

| Repo  | Task         | State         | Where it stands                                      | Last activity         | Branch  |
|-------|--------------|---------------|------------------------------------------------------|-----------------------|---------|
| alpha | 011-live     | running       | in 03-coder                                          | 10-16 11:58 (2m ago)  | main    |
| beta  | 003-later    | dead          | mid 04-qa                                            | 10-16 11:30 (30m ago) | main    |
| alpha | 010-killed   | dead          | mid 02-critic                                        | 10-16 11:10 (50m ago) | feature |
| beta  | 004-old      | dead          | mid 02-coder                                         | 10-15 08:00 (28h ago) | main    |
| alpha | 009-paused   | stopped       | at critic: not approved                              | 10-16 10:20 (1h ago)  | main    |
| alpha | 008-fresh    | never-started | no handoffs, no steps, no log                        | 10-16 10:50 (1h ago)  | main    |
| alpha | 007-done     | complete      | 2 steps                                              | 10-16 10:00 (2h ago)  | main    |
| alpha | 006-given-up | abandoned     | abandoned 2026-10-16 09:00: superseded by 007-done   | 10-16 09:00 (3h ago)  | main    |
| alpha | 005-restored | leftover      | handoff copies from git or a checkout, no run folder | ?                     | main    |
"""
NONE = "marestail runs: 0 known, 0 running, 0 shown\n\n(nothing to show)\n"
QUEUED = "| Repo | Task     |\n|------|----------|\n| w1   | 012-next |\n"
OUTLINE = [
    ("t1", "complete", "4 steps", "04-qa", "qa"),
    ("t2", "complete", "4 steps, archived", "04-qa", "qa"),
    ("t3", "stopped", "at 03-coder, worker repeated the same problems 3x", "03-coder", "coder"),
    ("t4", "stopped", "at 03-coder, runner asked for a human", "03-coder", "coder"),
    ("t5", "dead", "in the rate-limit wait before 03-coder", "03-coder", "coder"),
    ("t6", "dead", "mid 03-coder", "03-coder", "coder"),
    ("t7", "dead", "mid 03-coder (its handoff was written, the runner never logged it)", "03-coder", "coder"),
    ("t8", "dead", "after 03-perf, in perf sampling", "03-perf", "perf"),
    ("t9", "dead", "after 03-coder finished (verdict PASS)", "03-coder", "coder"),
    ("t10", "dead", "after 03-coder finished", "03-coder", "coder"),
    ("t11", "complete", "archived", None, None),
    ("t12", "complete", "archived, leftover copies in handoffs/", None, None),
    ("t13", "never-started", "no handoffs, no steps, no log", None, None),
    ("t14", "dead", "after 02-critic wrote its handoff (no pipeline.log)", "02-critic", "critic"),
    ("t15", "dead", "mid 03-coder: prompt written, no result (no pipeline.log)", None, None),
    ("t16", "dead", "after 03-coder returned nothing, 0-byte result (no pipeline.log)", None, None),
    ("t17", "dead", "after 03-coder wrote its handoff, then nothing (no pipeline.log)", "03-coder", "coder"),
    ("t18", "dead", "after 03-coder finished, then nothing (no pipeline.log)", None, None),
    ("t19", "leftover", "handoff copies from git or a checkout, no run folder", None, None),
    ("t20", "abandoned", "abandoned 2026-10-16 09:00: later", "03-coder", "coder"),
    ("t21", "abandoned", "aborted archive, nothing since", "03-coder", "coder"),
    ("t22", "complete", "archived", None, None),
    ("t23", "dead", "mid 03-coder (its handoff was written, the runner never logged it)", "03-coder", "coder"),
]
KEYS = [
    "repo",
    "task",
    "state",
    "step",
    "role",
    "since",
    "branch",
    "head",
    "pid",
    "result",
    "exit_code",
    "stopped_at",
    "reason",
    "events",
    "log",
]


def instant(text: str) -> float:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()


def touch(path: Path, when: float) -> None:
    os.utime(path, (when, when))


def ev(event: str, at: str, **fields: object) -> dict[str, object]:
    return {"v": 1, "at": at, "event": event, **fields}


def write_events(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "user.name", "Test")
    git(path, "config", "commit.gpgsign", "false")
    git(path, "commit", "-q", "--allow-empty", "-m", "init")


def pipe(pid: int, cwd: Path, task: str, program: str = "cli.py") -> tuple[int, list[str], Path]:
    tokens = ["python3", "-u", f"/opt/marestail/marestail/{program}", "run", f"tasks/{task}.md", "--auto"]
    return pid, tokens, cwd


def use_procs(monkeypatch: pytest.MonkeyPatch, procs: list[tuple[int, list[str], Path]]) -> None:
    monkeypatch.setattr(status, "_machine_processes", lambda: procs)


def capture_show(
    paths: list[Path], capsys: pytest.CaptureFixture[str], as_json: bool = False, show_all: bool = False, queued: bool = False
) -> tuple[int, str, str]:
    code = status.show(paths, as_json, show_all, queued)
    out, err = capsys.readouterr()
    return code, out, err


def cells(line: str) -> list[str]:
    return [part.strip() for part in line.strip().strip("|").split("|")]


def row_named(text: str, task: str) -> list[str]:
    for line in text.splitlines():
        parts = cells(line)
        if len(parts) > 1 and parts[1] == task:
            return parts
    return []


def repo_cells(text: str) -> list[str]:
    found = []
    for line in text.splitlines():
        if line.startswith("| ") and not line.startswith("| Repo") and not line.startswith("|--"):
            found.append(cells(line)[0])
    return found


def by_task(records: list[dict[str, object]], task: str) -> dict[str, object]:
    return next(record for record in records if record["task"] == task)


def task_detail(repo: Path, task: str) -> str:
    return next(row.detail for row in status._repo_rows(repo, []) if row.task == task)


def run_dir(repo: Path, task: str) -> Path:
    path = repo / ".marestail" / "runs" / task
    path.mkdir(parents=True, exist_ok=True)
    return path


def plant_log(repo: Path, task: str, text: str, when: float | None = None) -> Path:
    folder = run_dir(repo, task)
    log = folder / "pipeline.log"
    log.write_text(text)
    if when is not None:
        touch(log, when)
        touch(folder, when)
    return folder


def handoff(repo: Path, task: str, name: str, body: str = "body\n") -> Path:
    folder = repo / ".marestail" / "handoffs" / task
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(body)
    return path


def archive(repo: Path, task: str, name: str) -> Path:
    path = run_dir(repo, task) / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def marker(folder: Path, text: str, when: float | None = None) -> None:
    path = folder / "abandoned"
    path.write_text(text)
    if when is not None:
        touch(path, when)


def add_events(repo: Path, task: str, rows: list[dict[str, object]], log: str | None = None) -> Path:
    folder = run_dir(repo, task)
    write_events(folder / "events.jsonl", rows)
    if log is not None:
        (folder / "pipeline.log").write_text(log)
    return folder


def dispatch(
    command: list[str],
    cwd: Path,
    env: dict[str, str] | None = None,
    timeout: int | None = 3600,
    stdin: str | None = None,
) -> tuple[int, str]:
    LAST["commands"].append(list(command))
    if command[:1] == ["last"]:
        LAST["calls"] += 1
        return LAST["reply"]
    return real_run(command, cwd, env, timeout, stdin)


@pytest.fixture(autouse=True)
def pinned_clock(monkeypatch: pytest.MonkeyPatch) -> object:
    previous = os.environ.get("TZ")
    os.environ["TZ"] = "UTC"
    time.tzset()
    monkeypatch.setattr(time, "time", lambda: NOW)
    monkeypatch.setattr(status, "_machine_processes", lambda: [])
    monkeypatch.setattr(status, "run", dispatch)
    LAST["reply"] = (1, "")
    LAST["calls"] = 0
    LAST["commands"] = []
    yield None
    if previous is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = previous
    time.tzset()


def track(repo: Path, task: str, name: str, body: str) -> None:
    path = handoff(repo, task, name, body)
    git(repo, "add", str(path.relative_to(repo)))
    git(repo, "commit", "-q", "-m", task)


def plant_live(repo: Path) -> None:
    add_events(
        repo,
        "011-live",
        [
            ev("run-start", "2026-10-16T11:30:00.000Z", pid=4242, branch="main", start_commit="aaa111"),
            ev("step-start", "2026-10-16T11:35:00.000Z", pid=4242, step="01-specifier", role="specifier"),
            ev("step-end", "2026-10-16T11:40:00.000Z", pid=4242, step="01-specifier", role="specifier", outcome="done", head="bbb222"),
            ev("step-start", "2026-10-16T11:58:00.000Z", pid=4242, step="03-coder", role="coder"),
        ],
        "== qa (04-qa) attempt 1\npipeline complete\n",
    )


def plant_killed(repo: Path) -> None:
    add_events(
        repo,
        "010-killed",
        [
            ev("run-start", "2026-10-16T11:00:00.000Z", pid=999999, branch="feature", start_commit="abc"),
            ev("step-start", "2026-10-16T11:02:00.000Z", pid=999999, step="01-specifier", role="specifier"),
            ev("step-end", "2026-10-16T11:05:00.000Z", pid=999999, step="01-specifier", role="specifier", outcome="done", head="fff"),
            ev("step-start", "2026-10-16T11:10:00.000Z", pid=999999, step="02-critic", role="critic"),
        ],
    )


def plant_paused(repo: Path) -> None:
    add_events(
        repo,
        "009-paused",
        [
            ev("run-start", "2026-10-16T10:00:00.000Z", pid=5555, branch="main", start_commit="s"),
            ev("step-start", "2026-10-16T10:05:00.000Z", pid=5555, step="01-critic", role="critic"),
            ev("step-end", "2026-10-16T10:10:00.000Z", pid=5555, step="01-critic", role="critic", outcome="pass", head="ccc333"),
            ev(
                "run-end",
                "2026-10-16T10:20:00.000Z",
                pid=5555,
                result="stopped",
                exit_code=1,
                head="ccc333",
                stopped_at="critic",
                reason="not approved",
            ),
        ],
    )


def plant_done(repo: Path) -> None:
    add_events(
        repo,
        "007-done",
        [
            ev("run-start", "2026-10-16T09:00:00.000Z", pid=6666, branch="main", start_commit="s"),
            ev("step-start", "2026-10-16T09:10:00.000Z", pid=6666, step="01-specifier", role="specifier"),
            ev("step-end", "2026-10-16T09:20:00.000Z", pid=6666, step="01-specifier", role="specifier", outcome="done", head="x"),
            ev("step-start", "2026-10-16T09:30:00.000Z", pid=6666, step="02-critic", role="critic"),
            ev("step-end", "2026-10-16T09:40:00.000Z", pid=6666, step="02-critic", role="critic", outcome="pass", head="y"),
            ev("run-end", "2026-10-16T10:00:00.000Z", pid=6666, result="complete", exit_code=0, head="ddd444"),
        ],
    )


def plant_given_up(repo: Path) -> None:
    folder = add_events(
        repo,
        "006-given-up",
        [
            ev("run-start", "2026-10-16T08:00:00.000Z", pid=7777, branch="main", start_commit="s"),
            ev("run-end", "2026-10-16T08:30:00.000Z", pid=7777, result="stopped", stopped_at="coder", reason="coder used all 1 attempts"),
        ],
    )
    marker(folder, GIVEN_UP + "\n" + REVIVE, instant("2026-10-16T09:00:00Z"))


def plant_alpha(repo: Path) -> None:
    plant_live(repo)
    plant_killed(repo)
    plant_paused(repo)
    plant_done(repo)
    plant_given_up(repo)
    touch(run_dir(repo, "008-fresh"), instant("2026-10-16T10:50:00Z"))
    (repo / ".marestail" / "handoffs" / "overnight-1.log").write_text("log\n")
    (repo / ".marestail" / "runs" / "handoffs-skip").mkdir()


def plant_beta(repo: Path) -> None:
    folder = plant_log(repo, "003-later", LATER, instant("2026-10-16T09:30:00Z"))
    touch(folder, instant("2026-10-16T11:30:00Z"))
    plant_log(repo, "004-old", OLD, instant("2026-10-15T08:00:00Z"))


def build_fleet(root: Path) -> Path:
    fleet = root / "fleet"
    alpha = fleet / "alpha"
    beta = fleet / "beta"
    init_repo(alpha)
    init_repo(beta)
    track(alpha, "005-restored", "01-specifier.md", "from git\n")
    plant_alpha(alpha)
    plant_beta(beta)
    return fleet


def fleet_procs(fleet: Path) -> list[tuple[int, list[str], Path]]:
    return [pipe(4242, fleet / "alpha", "011-live")]


def show_fleet(
    fleet: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], show_all: bool = False, queued: bool = False
) -> tuple[int, str, str]:
    use_procs(monkeypatch, fleet_procs(fleet))
    return capture_show([fleet], capsys, False, show_all, queued)


def build_outline(root: Path) -> Path:
    repo = root / "outline"
    init_repo(repo)
    plant_log(repo, "t1", FOUR)
    plant_log(repo, "t2", FOUR)
    archive(repo, "t2", "handoffs-20261016T100000")
    plant_log(repo, "t3", STOPPED_HUMAN)
    plant_log(repo, "t4", STOPPED)
    plant_log(repo, "t5", RATE)
    plant_log(repo, "t6", MID)
    plant_log(repo, "t7", MID)
    handoff(repo, "t7", "03-coder.md")
    plant_log(repo, "t8", PERF)
    plant_log(repo, "t9", VERDICT)
    plant_log(repo, "t10", DONE)
    archive(repo, "t11", "handoffs-20261016T100000")
    archived = archive(repo, "t12", "handoffs-20261016T100000")
    (archived / "01-specifier.md").write_text("same\n")
    handoff(repo, "t12", "01-specifier.md", "same\n")
    run_dir(repo, "t13")
    handoff(repo, "t14", "02-critic.md")
    handoff(repo, "t14", "notes.md", "ignore\n")
    (run_dir(repo, "t15") / "03-coder.prompt.md").write_text("prompt\n")
    folder = run_dir(repo, "t16")
    (folder / "03-coder.prompt.md").write_text("prompt\n")
    (folder / "03-coder.json").write_bytes(b"")
    folder = run_dir(repo, "t17")
    (folder / "03-coder.prompt.md").write_text("prompt\n")
    (folder / "03-coder.json").write_text("{}\n")
    handoff(repo, "t17", "03-coder.md")
    folder = run_dir(repo, "t18")
    (folder / "03-coder.prompt.md").write_text("prompt\n")
    (folder / "03-coder.json").write_text("{}\n")
    handoff(repo, "t19", "01-specifier.md", "tracked\n")
    folder = plant_log(repo, "t20", MID, instant("2026-10-16T08:00:00Z"))
    marker(folder, "abandoned 2026-10-16 09:00: later\n" + REVIVE, instant("2026-10-16T09:00:00Z"))
    touch(folder, NOW)
    plant_log(repo, "t21", MID)
    archive(repo, "t21", "handoffs-aborted-20261016")
    archive(repo, "t22", "handoffs-aborted-20261016")
    plant_log(repo, "t23", MID)
    marker(run_dir(repo, "t23"), "abandoned 2026-10-16 09:00: later\n")
    handoff(repo, "t23", "03-coder.md")
    git(repo, "add", ".marestail/handoffs/t19/01-specifier.md")
    git(repo, "commit", "-q", "-m", "t19")
    touch(run_dir(repo, "t20"), NOW)
    return repo


def check_outline_row(repo: Path, table: str, task: str, state: str, text: str, step: str | None, role: str | None) -> None:
    assert row_named(table, task)[2:4] == [state, text]
    record = status.read_run(repo, task)
    assert (record["state"], record["step"], record["role"], record["events"]) == (state, step, role, None)


def legacy_pair(root: Path, name: str) -> Path:
    repo = root / name
    init_repo(repo)
    halted = plant_log(repo, "020-halted", HALTED, instant("2026-10-16T11:00:00Z"))
    touch(halted, instant("2026-10-16T11:00:00Z"))
    cut = plant_log(repo, "019-cut", MID, instant("2026-10-16T10:00:00Z"))
    touch(cut, instant("2026-10-16T10:00:00Z"))
    return repo


def epsilon(root: Path) -> Path:
    repo = root / "epsilon"
    init_repo(repo)
    bare = plant_log(repo, "021-bare-stop", "pipeline stopped at 04-coder\n", instant("2026-10-16T11:00:00Z"))
    touch(bare, instant("2026-10-16T11:00:00Z"))
    mid = plant_log(repo, "017-mid", MID, instant("2026-10-16T10:00:00Z"))
    touch(mid, instant("2026-10-16T10:00:00Z"))
    return repo


def make_task(path: Path, task: str) -> None:
    (path / ".marestail" / "runs" / task).mkdir(parents=True)


def build_scan(root: Path) -> Path:
    fleet = root / "fleet"
    make_task(fleet / "alpha", "001-outer")
    make_task(fleet / "alpha" / "nested", "099-inside")
    make_task(fleet / "team" / "gamma", "031-mid")
    make_task(fleet / "team" / "deep" / "delta", "030-deep")
    for name in status._SKIP:
        if name == ".marestail":
            continue
        make_task(fleet / name / "x", "skip-" + name.strip("."))
    make_task(fleet / ".hidden" / "x", "skip-hidden")
    (fleet / "link").symlink_to(fleet / "alpha", target_is_directory=True)
    (fleet / "plain.txt").write_text("x")
    (fleet / "alpha" / "sub").mkdir()
    return fleet


def clone_pair(root: Path) -> tuple[Path, Path]:
    parent = root / "clones"
    origin = "https://example.com/same.git"
    first = parent / "w1"
    second = parent / "w2"
    init_repo(first)
    init_repo(second)
    git(first, "remote", "add", "origin", origin)
    git(second, "remote", "add", "origin", origin)
    (first / ".marestail").mkdir()
    (second / ".marestail").mkdir()
    tasks = first / "tasks"
    tasks.mkdir()
    (tasks / "012-next.md").write_text("next\n")
    (tasks / "013-done.md").write_text("done\n")
    (tasks / "README.md").write_text("read\n")
    (tasks / "03-coder.md").write_text("role\n")
    (second / "tasks").mkdir()
    (second / "tasks" / "012-next.md").write_text("next\n")
    run_dir(second, "013-done")
    return parent, first


def dead_repo(path: Path, when: str) -> Path:
    init_repo(path)
    add_events(path, "dead-run", [ev("run-start", when, pid=1, branch="main")])
    return path


def zone(name: str) -> None:
    os.environ["TZ"] = name
    time.tzset()


def shutdown_text(repo: Path, capsys: pytest.CaptureFixture[str], reply: tuple[int, str]) -> tuple[dict[str, object], str]:
    LAST["reply"] = reply
    LAST["calls"] = 0
    _code, table, _err = capture_show([repo], capsys, False, True, False)
    _code, out, _err = capture_show([repo], capsys, True, False, False)
    payload = json.loads(out)
    return by_task(payload["runs"], "dead-run"), table


def git_writing(command: list[str]) -> bool:
    if command[:1] != ["git"] or len(command) < 4:
        return False
    return command[3] in {"add", "commit", "mv", "rm", "reset", "clean", "checkout"}


def frozen(fmt: str, when: time.struct_time | None = None) -> str:
    if fmt == "%Y%m%dT%H%M%S":
        return "20261016T120000"
    if fmt == "%Y-%m-%d %H:%M":
        return "2026-10-16 12:00"
    if when is None:
        return REAL_STRFTIME(fmt)
    return REAL_STRFTIME(fmt, when)


def import_names(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom) and node.module:
        return [node.module]
    return []


def test_default_table_lists_unfinished_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    fleet = build_fleet(tmp_path)
    code, out, err = show_fleet(fleet, monkeypatch, capsys)
    assert (code, err, out) == (0, "", DEFAULT)
    again, queued_out, _err = show_fleet(fleet, monkeypatch, capsys, queued=True)
    assert again == 0
    assert queued_out == DEFAULT
    assert "Tasks in tasks/" not in queued_out


def test_all_adds_hidden_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    fleet = build_fleet(tmp_path)
    code, out, err = show_fleet(fleet, monkeypatch, capsys, show_all=True)
    assert (code, err, out) == (0, "", ALL)
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)
    code, out, err = capture_show([], capsys)
    assert (code, err, out) == (0, "", NONE)


def test_events_decide_the_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    fleet = build_fleet(tmp_path)
    check_fleet_events(fleet, monkeypatch, capsys)
    check_event_texts(tmp_path, monkeypatch, capsys)
    check_pid_matrix(tmp_path, monkeypatch, capsys)


def check_fleet_events(fleet: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    use_procs(monkeypatch, fleet_procs(fleet))
    _code, out, _err = capture_show([fleet], capsys, False, True, False)
    alpha = fleet / "alpha"
    expect = {
        "011-live": ("running", "in 03-coder"),
        "010-killed": ("dead", "mid 02-critic"),
        "009-paused": ("stopped", "at critic: not approved"),
        "007-done": ("complete", "2 steps"),
        "006-given-up": ("abandoned", GIVEN_UP),
    }
    for task, pair in expect.items():
        assert tuple(row_named(out, task)[2:4]) == pair
        assert status.read_run(alpha, task)["state"] == pair[0]


def check_event_texts(root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    repo = root / "events"
    init_repo(repo)
    plant_event_cases(repo)
    procs = [
        pipe(100, repo, "writer"),
        pipe(101, repo, "starting"),
        pipe(102, repo, "waiting"),
        pipe(103, repo, "basename", "marestail"),
        pipe(104, repo / "child", "child"),
    ]
    use_procs(monkeypatch, procs)
    _code, out, _err = capture_show([repo], capsys, False, True, False)
    assert row_named(out, "writer")[2:4] == ["running", "in 03-coder"]
    assert row_named(out, "starting")[3] == "starting"
    assert row_named(out, "waiting")[3] == "waiting for a rate limit until 2026-10-16T12:30:00.000Z"
    assert row_named(out, "deadwait")[2:4] == ["dead", "mid 03-coder"]
    assert row_named(out, "failed")[3] == "failed at coder: gate failed"
    assert row_named(out, "failed-bare")[3] == "failed at coder"
    assert row_named(out, "cut")[3] == "interrupted in 03-coder"
    assert row_named(out, "cut-plain")[3] == "interrupted"
    assert row_named(out, "between")[3] == "after 02-critic (pass)"
    assert row_named(out, "before")[3] == "before the first step"
    assert row_named(out, "basename")[2] == "running"
    assert row_named(out, "child")[2] == "running"
    assert_event_records(repo)


def plant_event_cases(repo: Path) -> None:
    add_events(
        repo,
        "writer",
        [
            ev("run-start", "2026-10-16T11:00:00.000Z", pid=100, branch="main"),
            ev("step-start", "2026-10-16T11:01:00.000Z", pid=100, step="03-coder", role="writer"),
        ],
    )
    add_events(repo, "starting", [ev("run-start", "2026-10-16T11:00:00.000Z", pid=101, branch="main")])
    add_events(
        repo,
        "waiting",
        [
            ev("run-start", "2026-10-16T11:00:00.000Z", pid=102, branch="main"),
            ev("step-start", "2026-10-16T11:01:00.000Z", pid=102, step="03-coder", role="coder"),
            ev("wait", "2026-10-16T11:02:00.000Z", pid=102, until="2026-10-16T12:30:00.000Z"),
        ],
    )
    add_events(
        repo,
        "deadwait",
        [
            ev("run-start", "2026-10-16T11:00:00.000Z", pid=9, branch="main"),
            ev("step-start", "2026-10-16T11:01:00.000Z", pid=9, step="03-coder", role="coder"),
            ev("wait", "2026-10-16T11:45:00.000Z", pid=9, until="2026-10-16T12:30:00.000Z"),
        ],
    )
    add_events(
        repo,
        "failed",
        [
            ev("run-start", "2026-10-16T11:00:00.000Z", pid=9, branch="main"),
            ev("run-end", "2026-10-16T11:05:00.000Z", pid=9, result="failed", stopped_at="coder", reason="gate failed"),
        ],
    )
    add_events(
        repo,
        "failed-bare",
        [
            ev("run-start", "2026-10-16T11:00:00.000Z", pid=9, branch="main"),
            ev("run-end", "2026-10-16T11:05:00.000Z", pid=9, result="failed", stopped_at="coder", reason=None),
        ],
    )
    add_events(
        repo,
        "cut",
        [
            ev("run-start", "2026-10-16T11:00:00.000Z", pid=9, branch="main"),
            ev("step-start", "2026-10-16T11:01:00.000Z", pid=9, step="03-coder", role="coder"),
            ev("run-end", "2026-10-16T11:05:00.000Z", pid=9, result="interrupted", stopped_at="coder", reason="SIGINT"),
        ],
    )
    add_events(
        repo,
        "cut-plain",
        [
            ev("run-start", "2026-10-16T11:00:00.000Z", pid=9, branch="main"),
            ev("run-end", "2026-10-16T11:05:00.000Z", pid=9, result="interrupted", stopped_at=None, reason="SIGINT"),
        ],
    )
    add_events(
        repo,
        "between",
        [
            ev("run-start", "2026-10-16T11:00:00.000Z", pid=9, branch="main"),
            ev("step-end", "2026-10-16T11:05:00.000Z", pid=9, step="02-critic", role="critic", outcome="pass"),
        ],
    )
    add_events(repo, "before", [ev("run-start", "2026-10-16T11:00:00.000Z", pid=9, branch="main", start_commit="aaa111")])
    add_events(repo, "basename", [ev("run-start", "2026-10-16T11:00:00.000Z", pid=103, branch="main", start_commit="aaa")])
    add_events(repo, "child", [ev("run-start", "2026-10-16T11:00:00.000Z", pid=104, branch="main")])
    (repo / "child").mkdir()


def assert_event_records(repo: Path) -> None:
    writer = status.read_run(repo, "writer")
    assert (writer["step"], writer["role"], writer["state"]) == ("03-coder", "writer", "running")
    deadwait = status.read_run(repo, "deadwait")
    assert (deadwait["state"], deadwait["since"]) == ("dead", "2026-10-16T11:45:00.000Z")
    failed = status.read_run(repo, "failed")
    assert (failed["state"], failed["result"], failed["stopped_at"], failed["reason"]) == ("stopped", "failed", "coder", "gate failed")
    bare = status.read_run(repo, "failed-bare")
    assert (bare["result"], bare["reason"]) == ("failed", None)
    cut = status.read_run(repo, "cut")
    assert (cut["state"], cut["step"], cut["role"], cut["stopped_at"], cut["result"], cut["reason"]) == (
        "dead",
        "03-coder",
        "coder",
        "coder",
        "interrupted",
        None,
    )
    plain = status.read_run(repo, "cut-plain")
    assert (plain["step"], plain["stopped_at"], plain["result"], plain["reason"]) == (None, None, "interrupted", None)
    assert status.read_run(repo, "before")["head"] == "aaa111"


def check_pid_matrix(root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    repo = root / "pids"
    init_repo(repo)
    add_events(
        repo,
        "t-vary",
        [
            ev("run-start", "2026-10-16T11:00:00.000Z", pid=50, branch="main"),
            ev("step-start", "2026-10-16T11:01:00.000Z", pid=50, step="03-coder", role="coder"),
        ],
    )
    other = root / "other"
    other.mkdir()
    cases = [
        [],
        [(50, ["sleep", "100"], repo)],
        [pipe(50, repo, "other")],
        [pipe(50, other, "t-vary")],
        [pipe(50, Path("/tmp/explode"), "t-vary")],
        [(50, ["python3", "/opt/marestail/cli.py", "watch", "tasks/t-vary.md"], repo)],
    ]
    patch_explode(monkeypatch)
    for procs in cases:
        use_procs(monkeypatch, procs)
        _code, out, _err = capture_show([repo], capsys, False, True, False)
        assert row_named(out, "t-vary")[2] == "dead"
    use_procs(monkeypatch, [pipe(50, repo, "t-vary")])
    _code, out, _err = capture_show([repo], capsys, False, True, False)
    assert row_named(out, "t-vary")[2] == "running"
    add_events(repo, "t-text", [ev("run-start", "2026-10-16T11:00:00.000Z", pid="50", branch="")])
    record = status.read_run(repo, "t-text", [])
    assert (record["state"], record["pid"], record["branch"]) == ("dead", None, None)


def patch_explode(monkeypatch: pytest.MonkeyPatch) -> None:
    real = Path.resolve

    def wrapped(self: Path, strict: bool = False) -> Path:
        if self.name == "explode":
            raise OSError
        return real(self, strict)

    monkeypatch.setattr(Path, "resolve", wrapped)


def test_latest_run_is_the_last_run_start(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    init_repo(repo)
    check_second_run_wins(repo)
    check_skipped_lines(repo)
    check_no_run_start(repo)


def check_second_run_wins(repo: Path) -> None:
    add_events(
        repo,
        "order",
        [
            ev("run-start", "2026-10-16T08:00:00.000Z", pid=1, branch="main"),
            ev("step-end", "2026-10-16T08:10:00.000Z", pid=1, step="01-specifier", role="specifier", outcome="done", head="ddd444"),
            ev("run-end", "2026-10-16T08:20:00.000Z", pid=1, result="complete", head="ddd444"),
            ev("run-start", "2026-10-16T09:00:00.000Z", pid=2, branch="main", start_commit="aaa111"),
            ev("step-start", "2026-10-16T09:10:00.000Z", pid=2, step="03-coder", role="coder"),
        ],
    )
    dead = status.read_run(repo, "order", [])
    assert (dead["state"], dead["head"]) == ("dead", "aaa111")
    assert task_detail(repo, "order") == "mid 03-coder"
    add_events(
        repo,
        "reverse",
        [
            ev("run-start", "2026-10-16T08:00:00.000Z", pid=1, branch="main"),
            ev("step-end", "2026-10-16T08:10:00.000Z", pid=1, step="01-specifier", role="specifier", outcome="done", head="fff000"),
            ev("run-start", "2026-10-16T09:00:00.000Z", pid=2, branch="main"),
            ev("step-end", "2026-10-16T09:10:00.000Z", pid=2, step="01-specifier", role="specifier", outcome="done", head="aaa"),
            ev("step-end", "2026-10-16T09:20:00.000Z", pid=2, step="02-critic", role="critic", outcome="pass", head="bbb"),
            ev("run-end", "2026-10-16T09:30:00.000Z", pid=2, result="complete", head="eee555"),
        ],
    )
    done = status.read_run(repo, "reverse", [])
    assert (done["state"], done["head"]) == ("complete", "eee555")
    assert task_detail(repo, "reverse") == "2 steps"


def check_skipped_lines(repo: Path) -> None:
    raw = [
        "{",
        "",
        json.dumps({"v": 2, "event": "run-start", "at": "2026-10-16T11:50:00.000Z", "pid": 1}),
        json.dumps({"v": 1, "event": "nope", "at": "2026-10-16T11:51:00.000Z"}),
        json.dumps([1]),
        json.dumps({"v": 1, "event": 5, "at": "2026-10-16T11:52:00.000Z"}),
        json.dumps(ev("run-start", "2026-10-16T10:00:00.000Z", pid=3, branch="main")),
        json.dumps(ev("step-end", "2026-10-16T11:20:00.000Z", pid=3, step="01-specifier", role="specifier", outcome="done", head="bbb")),
    ]
    folder = run_dir(repo, "skip")
    (folder / "events.jsonl").write_text("\n".join(raw) + "\n")
    record = status.read_run(repo, "skip", [])
    assert (record["state"], record["since"], record["head"]) == ("dead", "2026-10-16T11:20:00.000Z", "bbb")
    add_events(
        repo,
        "proposal",
        [
            ev("run-start", "2026-10-16T10:00:00.000Z", pid=1, branch="main"),
            ev("step-end", "2026-10-16T11:00:00.000Z", pid=1, step="01-specifier", role="specifier", outcome="done", head="bbb"),
            {
                "v": 1,
                "at": "2026-10-16T11:40:00.000Z",
                "event": "proposal",
                "pid": 1,
                "step": "01-specifier",
                "path": ".marestail/handoffs/t/02-proposal.md",
                "files": ["marestail.toml"],
            },
        ],
    )
    proposed = status.read_run(repo, "proposal", [])
    assert (proposed["state"], proposed["since"]) == ("dead", "2026-10-16T11:00:00.000Z")
    assert task_detail(repo, "proposal") == "after 01-specifier (done)"
    assert task_detail(repo, "skip") == "after 01-specifier (done)"


def check_no_run_start(repo: Path) -> None:
    folder = run_dir(repo, "legacy")
    (folder / "events.jsonl").write_text(
        json.dumps({"v": 1, "event": "step-end", "at": "2026-10-16T11:00:00.000Z", "step": "01-specifier"}) + "\n"
    )
    (folder / "pipeline.log").write_text("== qa (04-qa) attempt 1\npipeline complete\n")
    record = status.read_run(repo, "legacy", [])
    assert (record["state"], record["events"], record["step"]) == ("complete", None, "04-qa")


def test_abandoned_marker_respects_the_run_start_time(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    init_repo(repo)
    check_newer_marker(repo, monkeypatch)
    check_marker_edges(repo, monkeypatch)


def check_newer_marker(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = add_events(
        repo,
        "dead",
        [
            ev("run-start", "2026-10-16T08:00:00.000Z", pid=41, branch="main"),
            ev("step-start", "2026-10-16T08:10:00.000Z", pid=41, step="03-coder", role="coder"),
        ],
    )
    marker(folder, "abandoned 2026-10-16 09:00: superseded\n" + REVIVE, instant("2026-10-16T09:00:00Z"))
    record = status.read_run(repo, "dead", [])
    assert (record["state"], record["since"], record["reason"], record["pid"]) == ("abandoned", "2026-10-16T09:00:00Z", "superseded", 41)
    assert status._repo_rows(repo, [])[0].detail == "abandoned 2026-10-16 09:00: superseded"
    touch(folder / "abandoned", instant("2026-10-16T07:00:00Z"))
    assert status.read_run(repo, "dead", [])["state"] == "dead"
    touch(folder / "abandoned", instant("2026-10-16T08:00:00.000Z"))
    assert status.read_run(repo, "dead", [])["state"] == "abandoned"
    stopped = add_events(
        repo,
        "halt",
        [
            ev("run-start", "2026-10-16T08:00:00.000Z", pid=42, branch="main"),
            ev("run-end", "2026-10-16T08:30:00.000Z", pid=42, result="stopped", stopped_at="coder", reason="hold"),
        ],
    )
    marker(stopped, "abandoned 2026-10-16 09:00: hold\n", instant("2026-10-16T09:00:00Z"))
    kept = status.read_run(repo, "halt", [])
    assert (kept["state"], kept["result"], kept["stopped_at"], kept["pid"]) == ("abandoned", "stopped", "coder", 42)


def check_marker_edges(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    done = add_events(
        repo,
        "done",
        [
            ev("run-start", "2026-10-16T08:00:00.000Z", pid=1, branch="main"),
            ev("run-end", "2026-10-16T08:30:00.000Z", pid=1, result="complete", head="abc"),
        ],
    )
    marker(done, "abandoned 2026-10-16 09:00: no\n", instant("2026-10-16T09:00:00Z"))
    assert status.read_run(repo, "done", [])["state"] == "complete"
    use_procs(monkeypatch, [pipe(3333, repo, "live")])
    live = add_events(repo, "live", [ev("run-start", "2026-10-16T08:00:00.000Z", pid=3333, branch="main")])
    marker(live, "abandoned 2026-10-16 09:00: no\n", instant("2026-10-16T09:00:00Z"))
    running = status.read_run(repo, "live")
    assert (running["state"], running["since"]) == ("running", "2026-10-16T08:00:00.000Z")
    add_events(repo, "bad-at", [ev("run-start", "nope", pid=1, branch="main")])
    marker(run_dir(repo, "bad-at"), "abandoned 2026-10-16 09:00: no\n", instant("2026-10-16T09:00:00Z"))
    assert status.read_run(repo, "bad-at", [])["state"] == "dead"
    add_events(repo, "no-at", [{"v": 1, "event": "run-start", "pid": 1, "branch": "main"}])
    marker(run_dir(repo, "no-at"), "abandoned 2026-10-16 09:00: gone\n", instant("2026-10-16T09:00:00Z"))
    assert status.read_run(repo, "no-at", [])["state"] == "abandoned"


def test_legacy_outline(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = build_outline(tmp_path)
    _code, out, _err = capture_show([repo], capsys, False, True, False)
    for task, state, text, step, role in OUTLINE:
        check_outline_row(repo, out, task, state, text, step, role)


def test_legacy_abandoned_since_is_the_marker_mtime(tmp_path: Path) -> None:
    repo = build_outline(tmp_path)
    record = status.read_run(repo, "t20")
    assert (record["state"], record["since"]) == ("abandoned", "2026-10-16T09:00:00Z")
    assert record["since"] not in {"2026-10-16T08:00:00Z", "2026-10-16T12:00:00Z"}


def test_legacy_log_fills_result_reason_and_step(tmp_path: Path) -> None:
    repo = build_outline(tmp_path)
    done = status.read_run(repo, "t1")
    assert (done["result"], done["reason"], done["step"], done["role"]) == ("complete", None, "04-qa", "qa")
    human = status.read_run(repo, "t3")
    assert (human["result"], human["reason"], human["stopped_at"], human["step"], human["role"]) == (
        "stopped",
        "worker repeated the same problems 3x",
        None,
        "03-coder",
        "coder",
    )
    asked = status.read_run(repo, "t4")
    assert (asked["result"], asked["reason"], asked["stopped_at"], asked["step"]) == (
        "stopped",
        "runner asked for a human",
        None,
        "03-coder",
    )
    finished = status.read_run(repo, "t10")
    assert (finished["state"], finished["step"], finished["role"]) == ("dead", "03-coder", "coder")


def test_unattributed_pipeline_promotes_only_the_newest_legacy_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fleet = build_fleet(tmp_path)
    alpha = fleet / "alpha"
    use_procs(monkeypatch, [pipe(4242, alpha, "011-live"), pipe(77, alpha, "other")])
    _code, out, _err = capture_show([fleet], capsys, False, True, False)
    assert row_named(out, "008-fresh")[2:4] == ["running", "starting"]
    assert status.read_run(alpha, "008-fresh")["pid"] == 77
    assert row_named(out, "010-killed")[2:4] == ["dead", "mid 02-critic"]
    assert row_named(out, "011-live")[2:4] == ["running", "in 03-coder"]
    assert status.read_run(alpha, "011-live")["pid"] == 4242


def test_newer_stopped_does_not_block_older_mid_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = legacy_pair(tmp_path, "gamma")
    use_procs(monkeypatch, [pipe(80, repo, "other"), pipe(50, repo, "other")])
    _code, out, _err = capture_show([repo], capsys, False, True, False)
    assert row_named(out, "019-cut")[2:4] == ["running", "in 03-coder"]
    cut = status.read_run(repo, "019-cut")
    assert (cut["step"], cut["role"], cut["pid"]) == ("03-coder", "coder", 50)
    assert row_named(out, "020-halted")[3] == "at 04-coder, runner asked for a human"
    halted = status.read_run(repo, "020-halted")
    assert (halted["state"], halted["step"], halted["role"]) == ("stopped", "03-coder", "coder")


def test_log_without_step_line_is_never_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = epsilon(tmp_path)
    _code, out, _err = capture_show([repo], capsys, False, True, False)
    assert row_named(out, "021-bare-stop")[2:4] == ["never-started", "no handoffs, no steps, no log"]
    bare = status.read_run(repo, "021-bare-stop")
    assert (bare["step"], bare["role"], bare["result"]) == (None, None, None)
    assert row_named(out, "017-mid")[2:4] == ["dead", "mid 03-coder"]
    use_procs(monkeypatch, [pipe(91, repo, "other")])
    _code, out, _err = capture_show([repo], capsys, False, True, False)
    assert row_named(out, "021-bare-stop")[2:4] == ["running", "starting"]
    promoted = status.read_run(repo, "021-bare-stop")
    assert (promoted["step"], promoted["role"], promoted["result"], promoted["pid"]) == (None, None, None, 91)
    assert row_named(out, "017-mid")[2:4] == ["dead", "mid 03-coder"]


def test_finished_step_promotes_to_in_that_step_with_null_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "delta"
    init_repo(repo)
    plant_log(repo, "018-done-step", DONE)
    use_procs(monkeypatch, [pipe(61, repo, "other")])
    _code, out, _err = capture_show([repo], capsys, False, True, False)
    assert row_named(out, "018-done-step")[2:4] == ["running", "in 03-coder"]
    record = status.read_run(repo, "018-done-step")
    assert (record["step"], record["role"], record["pid"]) == (None, None, 61)


def test_events_running_pid_does_not_promote_legacy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fleet = build_fleet(tmp_path)
    _code, out, _err = show_fleet(fleet, monkeypatch, capsys, show_all=True)
    assert row_named(out, "008-fresh")[2:4] == ["never-started", "no handoffs, no steps, no log"]
    assert row_named(out, "010-killed")[2:4] == ["dead", "mid 02-critic"]


def test_shutdown_within_the_hour(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = dead_repo(tmp_path / "utc", "2026-10-16T11:00:00.000Z")
    check_utc_shutdowns(repo, capsys)
    check_new_york(tmp_path, capsys)
    check_shutdown_calls(tmp_path, capsys)


def check_utc_shutdowns(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    noted, out = shutdown_text(repo, capsys, (0, "nope\nThu Oct 99 11:10:00 2026\nshutdown Thu Oct 16 11:10:00 2026\n"))
    assert noted["reason"] == "machine shut down 2026-10-16T11:10:00Z"
    assert "; machine shut down 11:10" in out
    late, out = shutdown_text(repo, capsys, (0, "shutdown Thu Oct 16 13:01:00 2026\n"))
    assert late["reason"] is None
    assert "machine shut down" not in out
    edge, _out = shutdown_text(repo, capsys, (0, "shutdown Thu Oct 16 12:00:00 2026\n"))
    assert edge["reason"] == "machine shut down 2026-10-16T12:00:00Z"
    past, out = shutdown_text(repo, capsys, (0, "shutdown Thu Oct 16 12:00:01 2026\n"))
    assert past["reason"] is None
    assert "machine shut down" not in out
    missing, out = shutdown_text(repo, capsys, (1, ""))
    assert missing["state"] == "dead"
    assert "machine shut down" not in out
    broken, out = shutdown_text(repo, capsys, (0, "Thu Oct 99 11:10:00 2026\nreboot still running\n"))
    assert broken["reason"] is None
    assert "machine shut down" not in out


def check_new_york(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    zone("America/New_York")
    repo = dead_repo(root / "ny", "2026-10-16T15:00:00.000Z")
    noted, out = shutdown_text(repo, capsys, (0, "shutdown Thu Oct 16 11:10:00 2026\n"))
    assert noted["reason"] == "machine shut down 2026-10-16T15:10:00Z"
    assert "; machine shut down 11:10" in out
    late, out = shutdown_text(repo, capsys, (0, "shutdown Thu Oct 16 13:01:00 2026\n"))
    assert late["reason"] is None
    assert "machine shut down" not in out
    zone("UTC")


def check_shutdown_calls(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = root / "quiet"
    init_repo(repo)
    add_events(
        repo,
        "done",
        [
            ev("run-start", "2026-10-16T10:00:00.000Z", pid=1, branch="main"),
            ev("run-end", "2026-10-16T10:05:00.000Z", pid=1, result="complete"),
        ],
    )
    LAST["calls"] = 0
    capture_show([repo], capsys)
    assert LAST["calls"] == 0
    add_events(repo, "one", [ev("run-start", "2026-10-16T11:00:00.000Z", pid=2, branch="main")])
    add_events(repo, "two", [ev("run-start", "2026-10-16T11:00:00.000Z", pid=3, branch="main")])
    LAST["calls"] = 0
    capture_show([repo], capsys)
    assert LAST["calls"] == 1


def test_scan_finds_repos_two_levels_down(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    fleet = build_scan(tmp_path)
    check_scan_tree(fleet, capsys)
    check_scan_overlap(fleet, monkeypatch, capsys)
    check_scan_errors(capsys)


def check_scan_tree(fleet: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = capture_show([fleet], capsys)
    assert code == 0
    assert err == ""
    assert sorted(repo_cells(out)) == ["alpha", "team/gamma"]
    assert "001-outer" in out
    for banned in ("099-inside", "030-deep", "skip-hidden", "skip-node_modules", "skip-mutants", "skip-tmp", "nested"):
        assert banned not in out
    code, out, _err = capture_show([fleet / "alpha"], capsys)
    assert code == 0
    assert repo_cells(out) == ["alpha"]
    assert "001-outer" in out
    assert "099-inside" not in out


def check_scan_overlap(fleet: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _code, out, _err = capture_show([fleet, fleet / "team"], capsys)
    assert repo_cells(out).count("team/gamma") == 1
    assert "gamma" not in repo_cells(out)
    monkeypatch.chdir(fleet / "alpha" / "sub")
    _code, out, _err = capture_show([], capsys)
    assert repo_cells(out) == ["alpha"]
    assert "001-outer" in out


def check_scan_errors(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = capture_show([Path("fleet/no-such")], capsys)
    assert (code, out, err) == (2, "", "marestail status: not a directory: fleet/no-such\n")


def test_legacy_run_outside_git_has_null_branch(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    loose = tmp_path / "loose"
    make_task(loose, "014-plain")
    code, out, err = capture_show([loose], capsys)
    assert code == 0
    assert err == ""
    assert row_named(out, "014-plain")[2:4] == ["never-started", "no handoffs, no steps, no log"]
    assert row_named(out, "014-plain")[5] == "(detached)"
    assert status.read_run(loose, "014-plain")["branch"] is None


def test_queued_lists_task_files_once_per_clone(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    parent, first = clone_pair(tmp_path)
    _code, out, _err = capture_show([parent], capsys, False, False, True)
    head, tail = out.split("Tasks in tasks/ that never ran:\n\n", 1)
    assert "013-done" in head
    assert tail == QUEUED
    for banned in ("013-done", "README", "03-coder"):
        assert banned not in tail
    _code, plain, _err = capture_show([parent], capsys)
    assert "Tasks in tasks/" not in plain
    _code, raw, _err = capture_show([parent], capsys, True, False, True)
    payload = json.loads(raw)
    want = {"repo": str(first.resolve()), "task": "012-next", "path": str((first / "tasks" / "012-next.md").resolve())}
    assert payload["queued"] == [want]


def test_json_prints_every_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    fleet = build_fleet(tmp_path)
    use_procs(monkeypatch, fleet_procs(fleet))
    code, out, err = capture_show([fleet], capsys, True, False, False)
    assert code == 0
    assert err == ""
    payload = json.loads(out)
    assert out == json.dumps(payload, indent=2) + "\n"
    assert payload["v"] == 1
    assert payload["queued"] == []
    assert len(payload["runs"]) == 9
    assert [row["task"] for row in payload["runs"]] == [
        "005-restored",
        "006-given-up",
        "007-done",
        "008-fresh",
        "009-paused",
        "010-killed",
        "011-live",
        "003-later",
        "004-old",
    ]
    check_json_records(fleet, payload["runs"])
    records, waiting = status.scan([fleet])
    assert waiting == []
    assert len(records) == 9
    _code, queued_out, _err = capture_show([fleet], capsys, False, False, True)
    assert queued_out == DEFAULT
    _code, both, _err = capture_show([fleet], capsys, True, False, True)
    assert json.loads(both)["queued"] == []


def check_json_records(fleet: Path, runs: list[dict[str, object]]) -> None:
    alpha = str((fleet / "alpha").resolve())
    beta = str((fleet / "beta").resolve())
    for record in runs:
        assert list(record) == KEYS
    live = by_task(runs, "011-live")
    assert live == {
        "repo": alpha,
        "task": "011-live",
        "state": "running",
        "step": "03-coder",
        "role": "coder",
        "since": "2026-10-16T11:58:00.000Z",
        "branch": "main",
        "head": "bbb222",
        "pid": 4242,
        "result": None,
        "exit_code": None,
        "stopped_at": None,
        "reason": None,
        "events": alpha + "/.marestail/runs/011-live/events.jsonl",
        "log": alpha + "/.marestail/runs/011-live/pipeline.log",
    }
    paused = by_task(runs, "009-paused")
    assert (paused["result"], paused["exit_code"], paused["stopped_at"], paused["reason"], paused["pid"]) == (
        "stopped",
        1,
        "critic",
        "not approved",
        5555,
    )
    killed = by_task(runs, "010-killed")
    assert (killed["state"], killed["branch"], killed["pid"], killed["reason"]) == ("dead", "feature", 999999, None)
    done = by_task(runs, "007-done")
    assert (done["head"], done["result"], done["exit_code"], done["pid"]) == ("ddd444", "complete", 0, 6666)
    later = by_task(runs, "003-later")
    assert (
        later["repo"],
        later["step"],
        later["role"],
        later["since"],
        later["head"],
        later["pid"],
        later["result"],
        later["reason"],
        later["events"],
    ) == (beta, "04-qa", "qa", "2026-10-16T11:30:00Z", None, None, None, None, None)
    assert later["log"] == beta + "/.marestail/runs/003-later/pipeline.log"
    old = by_task(runs, "004-old")
    assert (old["step"], old["role"], old["since"], old["events"], old["result"]) == (
        "02-coder",
        "coder",
        "2026-10-15T08:00:00Z",
        None,
        None,
    )
    fresh = by_task(runs, "008-fresh")
    assert (fresh["step"], fresh["role"], fresh["since"]) == (None, None, "2026-10-16T10:50:00Z")
    restored = by_task(runs, "005-restored")
    assert (restored["since"], restored["state"]) == (None, "leftover")
    given = by_task(runs, "006-given-up")
    assert (given["state"], given["since"], given["reason"], given["result"], given["stopped_at"], given["pid"]) == (
        "abandoned",
        "2026-10-16T09:00:00Z",
        "superseded by 007-done",
        "stopped",
        "coder",
        7777,
    )


def test_abandon_moves_handoffs_and_writes_the_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "work"
    alpha = root / "alpha"
    init_repo(alpha)
    add_events(
        alpha,
        "009-paused",
        [
            ev("run-start", "2026-10-16T10:00:00.000Z", pid=1, branch="main"),
            ev("run-end", "2026-10-16T10:20:00.000Z", pid=1, result="stopped", stopped_at="critic", reason="not approved"),
        ],
    )
    handoff(alpha, "009-paused", "01-specifier.md", "one\n")
    handoff(alpha, "009-paused", "02-critic.md", "two\n")
    run_dir(alpha, "008-fresh")
    events = (alpha / ".marestail" / "runs" / "009-paused" / "events.jsonl").read_bytes()
    monkeypatch.setattr(time, "strftime", frozen)
    monkeypatch.chdir(root)
    code = status.abandon(["alpha/009-paused"], "superseded")
    out, err = capsys.readouterr()
    assert code == 0
    assert err == ""
    assert out == "alpha/009-paused: abandoned, 2 handoffs moved to .marestail/runs/009-paused/handoffs-abandoned-20261016T120000\n"
    dest = alpha / ".marestail" / "runs" / "009-paused" / "handoffs-abandoned-20261016T120000"
    assert not (alpha / ".marestail" / "handoffs" / "009-paused").exists()
    assert sorted(path.name for path in dest.iterdir()) == ["01-specifier.md", "02-critic.md"]
    marker_path = alpha / ".marestail" / "runs" / "009-paused" / "abandoned"
    assert marker_path.read_text() == "abandoned 2026-10-16 12:00: superseded\n" + REVIVE
    assert (alpha / ".marestail" / "runs" / "009-paused" / "events.jsonl").read_bytes() == events
    assert [command for command in LAST["commands"] if git_writing(command)] == []
    _code, table, _err = capture_show([root], capsys, False, True, False)
    assert row_named(table, "009-paused")[2:4] == ["abandoned", "abandoned 2026-10-16 12:00: superseded"]
    code = status.abandon(["alpha/009-paused"], "")
    out, err = capsys.readouterr()
    assert code == 0
    assert err == ""
    assert out == "alpha/009-paused: abandoned\n"
    assert marker_path.read_text().splitlines()[0] == "abandoned 2026-10-16 12:00"
    monkeypatch.chdir(alpha)
    code = status.abandon(["008-fresh"], "")
    out, err = capsys.readouterr()
    assert code == 0
    assert err == ""
    assert out == "008-fresh: abandoned\n"
    assert (alpha / ".marestail" / "runs" / "008-fresh" / "abandoned").is_file()


def test_abandon_refuses_a_running_events_era_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fleet = build_fleet(tmp_path)
    alpha = fleet / "alpha"
    use_procs(monkeypatch, fleet_procs(fleet))
    monkeypatch.chdir(fleet)
    code = status.abandon(["alpha/011-live"], "")
    out, err = capsys.readouterr()
    assert code == 1
    assert out == ""
    assert err == f"alpha/011-live: a marestail pipeline is running in {alpha.resolve()}, not touching it\n"
    assert not (alpha / ".marestail" / "runs" / "011-live" / "abandoned").exists()


def test_abandon_refuses_while_an_unattributed_pipeline_is_alive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fleet = build_fleet(tmp_path)
    alpha = fleet / "alpha"
    use_procs(monkeypatch, [pipe(4242, alpha, "011-live"), pipe(77, alpha, "other")])
    monkeypatch.chdir(fleet)
    before = list((alpha / ".marestail" / "runs" / "010-killed").iterdir())
    code = status.abandon(["alpha/010-killed"], "")
    out, err = capsys.readouterr()
    assert code == 1
    assert out == ""
    assert err == f"alpha/010-killed: a marestail pipeline is running in {alpha.resolve()}, not touching it\n"
    assert list((alpha / ".marestail" / "runs" / "010-killed").iterdir()) == before


def test_abandon_reports_missing_task_repo_and_bare_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fleet = build_fleet(tmp_path)
    monkeypatch.chdir(fleet)
    code = status.abandon(["alpha/099-nope"], "")
    out, err = capsys.readouterr()
    assert (code, out, err) == (1, "", "alpha/099-nope: no run or handoffs folder for that task\n")
    code = status.abandon(["beta-nope/010-killed"], "")
    out, err = capsys.readouterr()
    assert (code, out, err) == (1, "", "beta-nope/010-killed: no .marestail folder at beta-nope\n")
    bare = tmp_path / "bare"
    bare.mkdir()
    monkeypatch.chdir(bare)
    code = status.abandon(["099-nope"], "")
    out, err = capsys.readouterr()
    assert (code, out, err) == (1, "", f"099-nope: no .marestail folder at {Path.cwd()}\n")


def test_one_failed_target_does_not_stop_the_others(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fleet = build_fleet(tmp_path)
    use_procs(monkeypatch, fleet_procs(fleet))
    monkeypatch.chdir(fleet)
    code = status.abandon(["alpha/099-nope", "alpha/009-paused"], "")
    out, err = capsys.readouterr()
    assert code == 1
    assert err == "alpha/099-nope: no run or handoffs folder for that task\n"
    assert out == "alpha/009-paused: abandoned\n"
    assert (fleet / "alpha" / ".marestail" / "runs" / "009-paused" / "abandoned").is_file()


def test_module_stays_at_the_foundations_layer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    check_status_imports()
    check_contracts()
    check_readme()
    check_untouched()
    check_listed_once(tmp_path, monkeypatch, capsys)


def check_status_imports() -> None:
    text = Path("marestail/status.py").read_text()
    tree = ast.parse(text)
    modules = [name for node in ast.walk(tree) for name in import_names(node)]
    assert [name for name in modules if name.startswith("marestail")] == ["marestail.shell"]
    assert "subprocess" not in text
    assert "os.environ" not in text
    assert "getenv" not in text
    assert '["ps"' not in text
    assert "ps -" not in text


def check_contracts() -> None:
    text = Path(".importlinter").read_text()
    layer = next(line for line in text.splitlines() if "marestail.timeline : marestail.ran_against" in line)
    parts = [part.strip() for part in layer.split(":")]
    block = text.split("[importlinter:contract:foundations-import-nothing-above]", 1)[1]
    names = block.split("forbidden_modules", 1)[0].split()
    assert "marestail.status" in parts
    assert "marestail.status" in names


def check_readme() -> None:
    readme = Path("README.md").read_text()
    watch = readme.index("## Watch")
    here = readme.index("## Status")
    dash = readme.index("## Dashboard")
    assert watch < here < dash
    section = readme[here:dash]
    assert "marestail status [--json] [--all] [--queued] [PATH ...]" in section
    assert "marestail abandon TARGET [TARGET ...] [--reason TEXT]" in section
    for name in ("running", "dead", "stopped", "never-started", "complete", "abandoned", "leftover"):
        assert f"`{name}`" in section
    for key in KEYS:
        assert f"`{key}`" in section
    assert '"v": 1' in section
    assert "handoffs-abandoned-*" in section
    assert "delete the marker" in section
    assert "MARESTAIL_" not in section


def check_untouched() -> None:
    dirty = subprocess.run(["git", "status", "--short"], capture_output=True, text=True, check=True).stdout
    assert "marestail/tui/" not in dirty
    assert ".marestail/phases/" not in dirty


def check_listed_once(root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    first = root / "once"
    second = root / "twice"
    init_repo(first)
    init_repo(second)
    run_dir(first, "a")
    run_dir(first, "b")
    run_dir(second, "c")
    calls = {"n": 0}

    def fake() -> list[tuple[int, list[str], Path]]:
        calls["n"] += 1
        return []

    monkeypatch.setattr(status, "_machine_processes", fake)
    capture_show([first, second], capsys)
    assert calls["n"] == 1
    status.abandon([str(first) + "/a", str(first) + "/b"], "")
    assert calls["n"] == 2


def proc_names(_path: str) -> list[str]:
    return ["3", "nope", "8"]


def proc_bytes(self: Path) -> bytes:
    if "3" in self.parts:
        return b"\0python3\0/bin/marestail\0run\0tasks/z.md\0"
    raise OSError


def proc_link(path: str) -> str:
    if path == "/proc/3/cwd":
        return "/tmp"
    raise OSError


def test_proc_reader_lists_tokens_and_skips_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "listdir", proc_names)
    monkeypatch.setattr(Path, "read_bytes", proc_bytes)
    monkeypatch.setattr(os, "readlink", proc_link)
    assert REAL_PROCS() == [(3, ["python3", "/bin/marestail", "run", "tasks/z.md"], Path("/tmp").resolve())]


def test_proc_listing_survives_a_missing_proc(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(path: str) -> list[str]:
        raise OSError

    monkeypatch.setattr(os, "listdir", boom)
    assert REAL_PROCS() == []


def test_unreadable_directory_is_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    fleet = tmp_path / "fleet"
    make_task(fleet / "alpha", "001-outer")
    make_task(fleet / "blocked" / "hidden", "099-blocked")
    real = Path.iterdir

    def iterdir(self: Path) -> object:
        if self.name == "blocked":
            raise OSError
        return real(self)

    monkeypatch.setattr(Path, "iterdir", iterdir)
    _code, out, _err = capture_show([fleet], capsys)
    assert "001-outer" in out
    assert "099-blocked" not in out


def test_resolve_errors_are_not_this_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    fleet = tmp_path / "fleet"
    make_task(fleet / "alpha", "001-outer")
    make_task(fleet / "broken", "099-broken")
    real = Path.resolve

    def wrapped(self: Path, strict: bool = False) -> Path:
        if self.name == "broken":
            raise OSError
        return real(self, strict)

    monkeypatch.setattr(Path, "resolve", wrapped)
    _code, out, _err = capture_show([fleet], capsys)
    assert "001-outer" in out
    assert "099-broken" not in out


def test_oserror_while_reading_falls_through(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    init_repo(repo)
    folder = add_events(
        repo, "events", [ev("run-start", "2026-10-16T11:00:00.000Z", pid=1, branch="main")], "== qa (04-qa) attempt 1\npipeline complete\n"
    )
    real = Path.read_text

    def wrapped(self: Path, encoding: str | None = None, errors: str | None = None) -> str:
        if self.name in {"events.jsonl", "abandoned"}:
            raise OSError
        return real(self, encoding, errors)

    monkeypatch.setattr(Path, "read_text", wrapped)
    record = status.read_run(repo, "events", [])
    assert (record["state"], record["events"], record["step"]) == ("complete", None, "04-qa")
    monkeypatch.setattr(Path, "read_text", real)
    bare = plant_log(repo, "quiet", MID)
    monkeypatch.setattr(Path, "read_text", lambda self, *args, **kwargs: (_ for _ in ()).throw(OSError()))
    assert status.read_run(repo, "quiet", [])["state"] == "never-started"
    monkeypatch.setattr(Path, "read_text", wrapped)
    marker(folder, "abandoned 2026-10-16 12:00: x\n", NOW)
    (folder / "pipeline.log").unlink()
    (folder / "events.jsonl").unlink()
    dead = add_events(repo, "marked", [ev("run-start", "2026-10-16T08:00:00.000Z", pid=1, branch="main")])
    marker(dead, "abandoned 2026-10-16 09:00: x\n", instant("2026-10-16T09:00:00Z"))
    marked = status.read_run(repo, "marked", [])
    assert (marked["state"], marked["reason"]) == ("abandoned", None)
    assert task_detail(repo, "marked") == "abandoned"
    assert bare.is_dir()


def test_bytes_differ_and_read_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    repo = tmp_path / "repo"
    init_repo(repo)
    copied = archive(repo, "differ", "handoffs-20261016T100000")
    (copied / "01-specifier.md").write_text("archived\n")
    handoff(repo, "differ", "01-specifier.md", "live\n")
    record = status.read_run(repo, "differ", [])
    assert record["state"] == "dead"
    monkeypatch.setattr(Path, "read_bytes", lambda self: (_ for _ in ()).throw(OSError()))
    assert status.read_run(repo, "differ", [])["state"] == "dead"
    _code, out, _err = capture_show([repo], capsys, False, True, False)
    assert "after 01-specifier wrote its handoff" in out


def test_partial_track_is_not_leftover(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    init_repo(repo)
    handoff(repo, "mix", "01-specifier.md", "one\n")
    handoff(repo, "mix", "02-critic.md", "two\n")
    git(repo, "add", ".marestail/handoffs/mix/01-specifier.md")
    git(repo, "commit", "-q", "-m", "one")
    record = status.read_run(repo, "mix")
    assert (record["state"], record["step"]) == ("dead", "02-critic")


def test_perf_author_prefix(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = tmp_path / "repo"
    init_repo(repo)
    plant_log(repo, "perf", AUTHOR)
    _code, out, _err = capture_show([repo], capsys, False, True, False)
    assert row_named(out, "perf")[3] == "after 03-perf, in perf sampling"


def test_stopped_alone_is_not_promoted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    init_repo(repo)
    plant_log(repo, "halt", STOPPED)
    use_procs(monkeypatch, [pipe(70, repo, "other")])
    assert status.read_run(repo, "halt")["state"] == "stopped"


def test_age_uses_days_after_two_days(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = tmp_path / "repo"
    init_repo(repo)
    plant_log(repo, "old", MID, instant("2026-10-13T12:00:00Z"))
    _code, out, _err = capture_show([repo], capsys, False, True, False)
    assert "10-13 12:00 (3d ago)" in out


def test_detached_checkout_has_null_branch(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = tmp_path / "repo"
    init_repo(repo)
    git(repo, "checkout", "--detach", "-q")
    run_dir(repo, "plain")
    _code, out, _err = capture_show([repo], capsys)
    assert row_named(out, "plain")[5] == "(detached)"
    assert status.read_run(repo, "plain")["branch"] is None


def test_marker_reason_edges(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    init_repo(repo)
    empty = add_events(repo, "empty", [ev("run-start", "2026-10-16T08:00:00.000Z", pid=1, branch="main")])
    marker(empty, "", instant("2026-10-16T09:00:00Z"))
    assert task_detail(repo, "empty") == "abandoned"
    assert status.read_run(repo, "empty", [])["reason"] is None
    blank = add_events(repo, "blank", [ev("run-start", "2026-10-16T08:00:00.000Z", pid=1, branch="main")])
    marker(blank, "abandoned 2026-10-16 09:00: \n", instant("2026-10-16T09:00:00Z"))
    record = status.read_run(repo, "blank", [])
    assert record["reason"] is None
    assert task_detail(repo, "blank") == "abandoned 2026-10-16 09:00:"


def test_private_edges(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert status._label_number("nope") == -1
    assert status._label_number("03-coder") == 3
    assert status._human_reason(None) == "runner asked for a human"
    assert status._display(Path("/somewhere/else/repo"), [Path("/other")]) == "repo"
    row = status._missing_row(tmp_path, "missing")
    assert row.state == "never-started"
    row.era = "events"
    row.state = "running"
    row.pid = None
    assert status._event_running(row) is False
    row.state = "dead"
    row.pid = 4
    assert status._event_running(row) is False
    row.state = "running"
    assert status._event_running(row) is True
    check_blank_prefixes(tmp_path)
    monkeypatch.setattr(Path, "resolve", lambda self, *args, **kwargs: (_ for _ in ()).throw(OSError()))
    assert status._relative_to(Path("/a"), Path("/b")) is None
    assert status._safe_resolve(Path("/a")) is None
    assert status._inside(Path("/a"), Path("/b")) is False


def check_blank_prefixes(root: Path) -> None:
    repo = root / "edges"
    init_repo(repo)
    add_events(
        repo,
        "hold",
        [
            ev("run-start", "2026-10-16T10:00:00.000Z", pid=1, branch="main"),
            ev("run-end", "2026-10-16T10:05:00.000Z", pid=1, result="stopped", reason="hold"),
        ],
    )
    assert task_detail(repo, "hold") == "at: hold"
    add_events(
        repo,
        "bare",
        [
            ev("run-start", "2026-10-16T10:00:00.000Z", pid=1, branch="main"),
            ev("run-end", "2026-10-16T10:05:00.000Z", pid=1, result="failed"),
        ],
    )
    assert task_detail(repo, "bare") == "failed at"
    add_events(
        repo,
        "heads",
        [
            ev("run-start", "2026-10-16T10:00:00.000Z", pid=1, branch="main", start_commit=""),
            ev("step-end", "2026-10-16T10:05:00.000Z", pid=1, step="01-specifier", role="specifier", outcome="done", head=""),
            ev("run-end", "2026-10-16T10:06:00.000Z", pid=1, result="interrupted", head=""),
        ],
    )
    assert status.read_run(repo, "heads", [])["head"] is None
    add_events(
        repo,
        "fall",
        [
            ev("run-start", "2026-10-16T10:00:00.000Z", pid=1, branch="main", start_commit="aaa"),
            ev("step-end", "2026-10-16T10:05:00.000Z", pid=1, step="01-specifier", role="specifier", outcome="done", head="from-step"),
            ev("run-end", "2026-10-16T10:06:00.000Z", pid=1, result="interrupted", head=""),
        ],
    )
    assert status.read_run(repo, "fall", [])["head"] == "from-step"
    add_events(
        repo,
        "code",
        [
            ev("run-start", "2026-10-16T10:00:00.000Z", pid=1, branch="main"),
            ev("run-end", "2026-10-16T10:05:00.000Z", pid=1, result="stopped", exit_code="1", stopped_at="coder"),
        ],
    )
    assert status.read_run(repo, "code", [])["exit_code"] is None
    plant_log(repo, "nodash", "== role (nodash) attempt 1\n")
    nodash = status.read_run(repo, "nodash", [])
    assert (nodash["step"], nodash["role"]) == ("nodash", None)


def test_abandon_creates_a_missing_run_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    repo = tmp_path / "repo"
    init_repo(repo)
    handoff(repo, "014-only", "01-specifier.md", "one\n")
    (repo / ".marestail" / "handoffs" / "014-only" / "notes.txt").write_text("nope\n")
    folder = repo / ".marestail" / "handoffs" / "015-txt"
    folder.mkdir(parents=True)
    (folder / "notes.txt").write_text("z\n")
    monkeypatch.setattr(time, "strftime", frozen)
    monkeypatch.chdir(repo.parent)
    code = status.abandon([f"{repo.name}/014-only", f"{repo.name}/015-txt"], "")
    out, err = capsys.readouterr()
    assert code == 0
    assert err == ""
    assert f"{repo.name}/014-only: abandoned, 1 handoffs moved to .marestail/runs/014-only/handoffs-abandoned-20261016T120000" in out
    assert f"{repo.name}/015-txt: abandoned\n" in out
    assert (repo / ".marestail" / "runs" / "014-only" / "abandoned").is_file()
    assert (repo / ".marestail" / "handoffs" / "015-txt" / "notes.txt").is_file()
    assert status.read_run(repo, "missing-task", [])["state"] == "never-started"


def test_scan_reports_the_first_path_that_is_not_a_directory(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    fleet = build_scan(tmp_path / "scan")
    code, out, err = capture_show([fleet, Path("fleet/no-such")], capsys)
    assert (code, out, err) == (2, "", "marestail status: not a directory: fleet/no-such\n")
