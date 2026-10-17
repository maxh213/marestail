import json
import os
import shutil
import signal
import subprocess
import sys
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

from marestail import cli, queue
from marestail import config as config_module
from marestail import status as status_module
from marestail.pipeline import Step
from marestail.pipeline import window as pipeline_window
from marestail.task_file import TaskFile
from tests.conftest import commit_all, git


class FakeChild:
    def __init__(self, pid: int, exit_code: int = 0, timeout_times: int = 0) -> None:
        self.pid = pid
        self.returncode = exit_code
        self._exit_code = exit_code
        self._timeout_times = timeout_times

    def wait(self, timeout: float | None = None) -> int:
        if self._timeout_times > 0 and timeout is not None:
            self._timeout_times -= 1
            raise subprocess.TimeoutExpired(cmd=["child"], timeout=timeout)
        return self._exit_code


def setup_bare_repo(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "commit.gpgsign", "false")
    bare = tmp_path / "bare.git"
    bare.mkdir()
    git(bare, "init", "--bare", "-q")
    git(repo, "remote", "add", "origin", str(bare))
    (repo / "marestail.toml").write_text('[git]\nbase = "origin/main"\n')
    commit_all(repo, "init")
    git(repo, "push", "-q", "-u", "origin", "main")
    return repo, bare


def write_task_file(path: Path, task_id: str, depends: list[str], stack: bool | None = None, title: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    deps_str = json.dumps(depends)
    stack_line = f"stack = {str(stack).lower()}\n" if stack is not None else ""
    heading = f"# {title}\n" if title else ""
    content = f"+++\ndepends = {deps_str}\n{stack_line}+++\n{heading}"
    path.write_text(content)


def append_events(
    repo: Path,
    task_id: str,
    pid: int,
    result: str = "complete",
    exit_code: int = 0,
    role: str = "coder",
    reason: str | None = None,
    stopped_at: str | None = None,
    branch: str = "main",
    omit_start: bool = False,
    omit_end: bool = False,
    head: str | None = None,
) -> None:
    events_file = repo / ".marestail" / "runs" / task_id / "events.jsonl"
    events_file.parent.mkdir(parents=True, exist_ok=True)
    lines = _build_event_lines(pid, task_id, role, branch, result, exit_code, reason, stopped_at, head, omit_start, omit_end)
    with events_file.open("a", encoding="utf-8") as handle:
        handle.write("".join(f"{line}\n" for line in lines))


def _build_event_lines(
    pid: int,
    task_id: str,
    role: str,
    branch: str,
    result: str,
    exit_code: int,
    reason: str | None,
    stopped_at: str | None,
    head: str | None,
    omit_start: bool,
    omit_end: bool,
) -> list[str]:
    lines = []
    if not omit_start:
        lines.extend(_start_event_lines(pid, task_id, role, branch))
    if not omit_end:
        lines.append(json.dumps(_end_event_payload(pid, result, exit_code, reason, stopped_at, head)))
    return lines


def _start_event_lines(pid: int, task_id: str, role: str, branch: str) -> list[str]:
    now = datetime.now(UTC).isoformat()
    return [
        json.dumps({"v": 1, "ts": now, "event": "run-start", "pid": pid, "task": task_id, "branch": branch}),
        json.dumps({"v": 1, "ts": now, "event": "step-start", "pid": pid, "step": f"01-{role}", "role": role}),
        json.dumps({"v": 1, "ts": now, "event": "step-end", "pid": pid, "step": f"01-{role}", "role": role, "outcome": "ok"}),
    ]


def _end_event_payload(
    pid: int, result: str, exit_code: int, reason: str | None, stopped_at: str | None, head: str | None
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "v": 1,
        "ts": datetime.now(UTC).isoformat(),
        "event": "run-end",
        "pid": pid,
        "result": result,
        "exit_code": exit_code,
    }
    payload.update({k: v for k, v in [("reason", reason), ("stopped_at", stopped_at), ("head", head)] if v is not None})
    return payload


def make_child_seam(
    repo: Path, outcomes: Mapping[str, tuple[str, int, str, str | None, str | None]]
) -> tuple[Callable[[list[str], Path, dict[str, str]], FakeChild], list[list[str]]]:
    recorded_argvs: list[list[str]] = []
    pid_counter = [1000]

    def _fake_start(argv: list[str], cwd: Path, env: dict[str, str]) -> FakeChild:
        recorded_argvs.append(argv)
        task_stem = Path(argv[4]).stem
        pid_counter[0] += 1
        pid = pid_counter[0]
        cur_branch = git(repo, "symbolic-ref", "--quiet", "--short", "HEAD").strip()
        work_file = repo / f"work-{task_stem}.txt"
        work_file.write_text(f"work for {task_stem} {pid}\n")
        git(repo, "add", str(work_file))
        git(repo, "commit", "-q", "-m", f"commit {task_stem}")
        head_commit = git(repo, "rev-parse", "HEAD").strip()
        result, code, role, reason, stopped_at = outcomes.get(task_stem, ("complete", 0, "coder", None, None))
        append_events(repo, task_stem, pid, result, code, role, reason, stopped_at, cur_branch, head=head_commit)
        return FakeChild(pid, code)

    return _fake_start, recorded_argvs


def _is_queue_child(argv: object) -> bool:
    return isinstance(argv, list) and len(argv) > 3 and argv[3] == "run"


def _tracking(repo: Path, branch: str) -> int:
    return subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", f"{branch}@{{upstream}}"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    ).returncode


def _ref_missing(repo: Path, branch: str) -> bool:
    missing = subprocess.run(["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], cwd=repo, check=False)
    return missing.returncode != 0


def _gh_on_path(mp: pytest.MonkeyPatch) -> None:
    real_which = shutil.which
    mp.setattr(shutil, "which", lambda name: "/fake/gh" if name == "gh" else real_which(name))


_LIVE_RUNS: list[subprocess.Popen[bytes]] = []


def _stop_live_runs() -> None:
    while _LIVE_RUNS:
        proc = _LIVE_RUNS.pop()
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)


def _queue_tasks(repo: Path) -> dict[str, Any]:
    state_file = repo / ".marestail" / "queue" / "state.json"
    if not state_file.exists():
        return {}
    loaded = cast(dict[str, Any], json.loads(state_file.read_text()))
    return cast(dict[str, Any], loaded["tasks"])


def test_order_filename_breaks_ties_only_after_dependencies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, _ = setup_bare_repo(tmp_path)
    monkeypatch.chdir(repo)
    write_task_file(repo / "tasks" / "001.md", "001", ["002"], stack=True)
    write_task_file(repo / "tasks" / "002.md", "002", [])
    (repo / "tasks" / "README.md").write_text("# Tasks\n")
    commit_all(repo, "add tasks")
    fake_start, recorded = make_child_seam(repo, {})
    monkeypatch.setattr(queue, "_start_child", fake_start)
    code = queue.run_queue([], None, None, False, [], None, None, None, None, [])
    assert code == 0
    assert len(recorded) == 2
    assert Path(recorded[0][4]).name == "002.md"
    assert Path(recorded[1][4]).name == "001.md"


def test_branches_stacking_and_blocked_ancestor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, _ = setup_bare_repo(tmp_path)
    monkeypatch.chdir(repo)
    base_time = datetime(2026, 10, 17, 12, 0, 0, tzinfo=UTC)
    clock_advance = [0]

    def _mock_now() -> datetime:
        return base_time + timedelta(minutes=clock_advance[0])

    monkeypatch.setattr(queue, "_now_utc", _mock_now)
    state_dir = repo / ".marestail" / "queue"
    state_dir.mkdir(parents=True)
    initial_state = {
        "v": 1,
        "tasks": {
            "z": {
                "status": "done",
                "branch": "marestail/z",
                "head": "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz",
                "finished_at": "2026-10-16T00:00:00Z",
                "to": None,
                "exit_code": 0,
                "reason": None,
            }
        },
    }
    (state_dir / "state.json").write_text(json.dumps(initial_state))
    write_task_file(repo / "tasks" / "a.md", "a", [])
    write_task_file(repo / "tasks" / "b.md", "b", ["a"], stack=True)
    write_task_file(repo / "tasks" / "c.md", "c", ["a"], stack=False)
    write_task_file(repo / "tasks" / "d.md", "d", [])
    commit_all(repo, "tasks committed")
    git(repo, "push", "-q", "origin", "main")
    fixed_heads = {
        "a": "111111111111111111111111111111111111111a",
        "b": "222222222222222222222222222222222222222b",
        "d": "444444444444444444444444444444444444444d",
    }
    recorded_argvs: list[list[str]] = []
    pid_counter = [1000]

    def _fixed_child(argv: list[str], cwd: Path, env: dict[str, str]) -> FakeChild:
        recorded_argvs.append(argv)
        task_stem = Path(argv[4]).stem
        pid_counter[0] += 1
        pid = pid_counter[0]
        cur_branch = git(repo, "symbolic-ref", "--quiet", "--short", "HEAD").strip()
        work_file = repo / f"work-{task_stem}.txt"
        work_file.write_text(f"work for {task_stem}\n")
        git(repo, "add", str(work_file))
        git(repo, "commit", "-q", "-m", f"commit {task_stem}")
        clock_advance[0] += 1
        append_events(repo, task_stem, pid, "complete", 0, "coder", None, None, cur_branch, head=fixed_heads[task_stem])
        return FakeChild(pid, 0)

    monkeypatch.setattr(queue, "_start_child", _fixed_child)
    monkeypatch.setattr(queue, "_git_head", lambda _r: fixed_heads[Path(recorded_argvs[-1][4]).stem])
    monkeypatch.setattr(queue, "_git_short", lambda h, _r: h[:7])
    code = queue.run_queue([], None, None, False, [], None, None, None, None, [])
    assert code == 1
    assert git(repo, "symbolic-ref", "--quiet", "--short", "HEAD").strip() == "main"
    out = capsys.readouterr().out
    assert "queue: 2026-10-17T12:00:00Z start a branch marestail/a from origin/main\n" in out
    assert "queue: 2026-10-17T12:01:00Z done a 1111111 1\n" in out
    assert "queue: 2026-10-17T12:01:00Z start b branch marestail/a from marestail/a\n" in out
    assert "queue: 2026-10-17T12:02:00Z done b 2222222 1\n" in out
    assert "queue: 2026-10-17T12:02:00Z blocked c waiting for a (1111111) to be merged into origin/main\n" in out
    assert "queue: 2026-10-17T12:02:00Z start d branch marestail/d from origin/main\n" in out
    assert "queue: 2026-10-17T12:03:00Z done d 4444444 1\n" in out
    assert "queue: 2026-10-17T12:03:00Z end done 3 failed 0 stopped 0 blocked 1\n" in out
    final_state = json.loads((state_dir / "state.json").read_text())
    assert final_state["tasks"]["z"]["head"] == "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz"
    assert final_state["tasks"]["a"]["head"] == "111111111111111111111111111111111111111a"
    assert final_state["tasks"]["b"]["head"] == "222222222222222222222222222222222222222b"
    assert final_state["tasks"]["d"]["head"] == "444444444444444444444444444444444444444d"
    assert final_state["tasks"]["c"]["status"] == "blocked"
    assert final_state["tasks"]["c"]["branch"] is None
    assert final_state["tasks"]["c"]["head"] is None
    assert final_state["tasks"]["c"]["reason"] == "waiting for a (1111111) to be merged into origin/main"
    assert final_state["tasks"]["a"]["status"] == "done"
    assert final_state["tasks"]["a"]["branch"] == "marestail/a"
    assert final_state["tasks"]["a"]["exit_code"] == 0
    assert final_state["tasks"]["a"]["to"] is None
    assert final_state["tasks"]["a"]["reason"] is None
    assert final_state["tasks"]["a"]["finished_at"] == "2026-10-17T12:01:00Z"
    assert len(final_state["tasks"]["a"]["head"]) == 40
    assert final_state["tasks"]["b"]["branch"] == "marestail/a"
    assert final_state["tasks"]["b"]["exit_code"] == 0
    assert final_state["tasks"]["b"]["to"] is None
    assert final_state["tasks"]["b"]["reason"] is None
    assert final_state["tasks"]["b"]["finished_at"] == "2026-10-17T12:02:00Z"
    assert final_state["tasks"]["d"]["branch"] == "marestail/d"
    assert final_state["tasks"]["d"]["exit_code"] == 0
    assert final_state["tasks"]["d"]["to"] is None
    assert final_state["tasks"]["d"]["reason"] is None
    assert final_state["tasks"]["d"]["finished_at"] == "2026-10-17T12:03:00Z"
    assert final_state["tasks"]["c"]["exit_code"] is None
    assert final_state["tasks"]["c"]["to"] is None
    assert final_state["tasks"]["c"]["finished_at"] == "2026-10-17T12:02:00Z"
    assert _tracking(repo, "marestail/a") != 0
    assert _tracking(repo, "marestail/d") != 0


def _done_entry(branch: str, head: str, finished_at: str) -> dict[str, Any]:
    return {"status": "done", "branch": branch, "head": head, "finished_at": finished_at, "to": None, "exit_code": 0, "reason": None}


def test_merge_commit_unblocks_c(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    repo, _ = setup_bare_repo(tmp_path)
    monkeypatch.chdir(repo)
    write_task_file(repo / "tasks" / "a.md", "a", [])
    write_task_file(repo / "tasks" / "b.md", "b", ["a"], stack=True)
    write_task_file(repo / "tasks" / "c.md", "c", ["a"], stack=False)
    write_task_file(repo / "tasks" / "d.md", "d", [])
    commit_all(repo, "tasks committed")
    git(repo, "push", "-q", "origin", "main")
    git(repo, "switch", "-q", "-c", "marestail/a")
    (repo / "from-a.txt").write_text("a\n")
    git(repo, "add", "from-a.txt")
    git(repo, "commit", "-q", "-m", "work a")
    a_head = git(repo, "rev-parse", "HEAD").strip()
    git(repo, "switch", "-q", "main")
    git(repo, "merge", "--no-ff", "-q", "-m", "merge marestail/a", "marestail/a")
    git(repo, "push", "-q", "origin", "main")
    origin_main = git(repo, "rev-parse", "origin/main").strip()
    ancestor = subprocess.run(["git", "merge-base", "--is-ancestor", a_head, "origin/main"], cwd=repo, check=False)
    assert ancestor.returncode == 0
    state_dir = repo / ".marestail" / "queue"
    state_dir.mkdir(parents=True)
    initial_state: dict[str, Any] = {
        "v": 1,
        "tasks": {
            "z": _done_entry("marestail/z", "z" * 40, "2026-10-16T00:00:00Z"),
            "a": _done_entry("marestail/a", a_head, "2026-10-17T12:01:00Z"),
            "b": _done_entry("marestail/a", a_head, "2026-10-17T12:02:00Z"),
            "c": {
                "status": "blocked",
                "branch": None,
                "head": None,
                "finished_at": "2026-10-17T12:02:00Z",
                "to": None,
                "exit_code": None,
                "reason": "waiting for a to be merged",
            },
            "d": _done_entry("marestail/d", "d" * 40, "2026-10-17T12:03:00Z"),
        },
    }
    (state_dir / "state.json").write_text(json.dumps(initial_state))
    fake_start, recorded = make_child_seam(repo, {"c": ("complete", 0, "coder", None, None)})
    monkeypatch.setattr(queue, "_start_child", fake_start)
    code = queue.run_queue([], None, None, False, [], None, None, None, None, [])
    assert code == 0
    assert len(recorded) == 1
    assert Path(recorded[0][4]).name == "c.md"
    assert git(repo, "rev-parse", "marestail/c^").strip() == origin_main
    assert git(repo, "symbolic-ref", "--quiet", "--short", "HEAD").strip() == "main"
    state = json.loads((state_dir / "state.json").read_text())["tasks"]
    assert state["c"]["status"] == "done"
    assert state["c"]["head"] == git(repo, "rev-parse", "marestail/c").strip()
    assert len(state["c"]["head"]) == 40
    assert state["z"] == initial_state["tasks"]["z"]
    out = capsys.readouterr().out
    assert "skip a done" in out
    assert "skip b done" in out
    assert "skip d done" in out
    assert "end done 4 failed 0 stopped 0 blocked 0" in out


def test_failure_blocks_dependants_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, _ = setup_bare_repo(tmp_path)
    monkeypatch.chdir(repo)
    write_task_file(repo / "tasks" / "a.md", "a", [])
    write_task_file(repo / "tasks" / "b.md", "b", ["a"], stack=True)
    write_task_file(repo / "tasks" / "c.md", "c", ["a"], stack=False)
    write_task_file(repo / "tasks" / "d.md", "d", [])
    write_task_file(repo / "tasks" / "e.md", "e", ["b"], stack=True)
    commit_all(repo, "add tasks")
    git(repo, "push", "-q", "origin", "main")
    outcomes = {
        "a": ("failed", 1, "coder", "tests failed", None),
        "d": ("complete", 0, "coder", None, None),
    }
    fake_start, recorded = make_child_seam(repo, outcomes)
    monkeypatch.setattr(queue, "_start_child", fake_start)
    code = queue.run_queue([], None, None, False, [], None, None, None, None, [])
    assert code == 1
    state = json.loads((repo / ".marestail" / "queue" / "state.json").read_text())["tasks"]
    assert state["a"]["status"] == "failed"
    assert state["a"]["reason"] == "tests failed"
    assert state["b"]["status"] == "blocked"
    assert state["b"]["reason"] == "a failed"
    assert state["c"]["status"] == "blocked"
    assert state["c"]["reason"] == "a failed"
    assert state["e"]["status"] == "blocked"
    assert state["e"]["reason"] == "a failed"
    assert state["d"]["status"] == "done"
    assert len(recorded) == 2


def _failed_entry() -> dict[str, Any]:
    return {
        "status": "failed",
        "branch": "marestail/a",
        "head": "a" * 40,
        "finished_at": "2026-10-17T12:00:00Z",
        "to": "hardener",
        "exit_code": 1,
        "reason": "tests failed",
    }


def _blocked_entry() -> dict[str, Any]:
    return {
        "status": "blocked",
        "branch": None,
        "head": None,
        "finished_at": "2026-10-17T12:00:00Z",
        "to": None,
        "exit_code": None,
        "reason": "a failed",
    }


def test_plain_rerun_does_not_start_failed_task(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, _ = setup_bare_repo(tmp_path)
    monkeypatch.chdir(repo)
    write_task_file(repo / "tasks" / "a.md", "a", [])
    write_task_file(repo / "tasks" / "b.md", "b", ["a"], stack=True)
    write_task_file(repo / "tasks" / "c.md", "c", ["a"], stack=False)
    write_task_file(repo / "tasks" / "d.md", "d", [])
    write_task_file(repo / "tasks" / "e.md", "e", ["b"], stack=True)
    commit_all(repo, "add tasks")
    git(repo, "push", "-q", "origin", "main")
    state_dir = repo / ".marestail" / "queue"
    state_dir.mkdir(parents=True)
    initial_state = {
        "v": 1,
        "tasks": {
            "a": _failed_entry(),
            "b": _blocked_entry(),
            "c": _blocked_entry(),
            "d": _done_entry("marestail/d", "d" * 40, "2026-10-17T12:00:00Z"),
            "e": _blocked_entry(),
        },
    }
    (state_dir / "state.json").write_text(json.dumps(initial_state))
    before = json.loads((state_dir / "state.json").read_text())["tasks"]
    fake_start, recorded = make_child_seam(repo, {})
    monkeypatch.setattr(queue, "_start_child", fake_start)
    code = queue.run_queue([], None, None, False, [], None, None, None, None, [])
    assert code == 1
    assert len(recorded) == 0
    after = json.loads((state_dir / "state.json").read_text())["tasks"]
    assert after["a"] == before["a"]
    assert after["d"] == before["d"]
    assert after["b"]["status"] == "blocked"
    assert after["b"]["reason"] == "a failed"
    assert after["c"]["reason"] == "a failed"
    assert after["e"]["reason"] == "a failed"


def test_retry_resumes_and_unblocks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, _ = setup_bare_repo(tmp_path)
    monkeypatch.chdir(repo)
    write_task_file(repo / "tasks" / "a.md", "a", [])
    write_task_file(repo / "tasks" / "b.md", "b", ["a"], stack=True)
    write_task_file(repo / "tasks" / "c.md", "c", ["a"], stack=False)
    write_task_file(repo / "tasks" / "d.md", "d", [])
    write_task_file(repo / "tasks" / "e.md", "e", ["b"], stack=True)
    commit_all(repo, "add tasks")
    git(repo, "push", "-q", "origin", "main")
    state_dir = repo / ".marestail" / "queue"
    state_dir.mkdir(parents=True)
    initial_state = {
        "v": 1,
        "tasks": {
            "a": {
                "status": "failed",
                "branch": "marestail/a",
                "head": "a" * 40,
                "finished_at": "2026-10-17T12:00:00Z",
                "to": None,
                "exit_code": 1,
                "reason": "tests failed",
            },
            "b": {
                "status": "blocked",
                "branch": None,
                "head": None,
                "finished_at": "2026-10-17T12:00:00Z",
                "to": None,
                "exit_code": None,
                "reason": "a failed",
            },
            "c": {
                "status": "blocked",
                "branch": None,
                "head": None,
                "finished_at": "2026-10-17T12:00:00Z",
                "to": None,
                "exit_code": None,
                "reason": "a failed",
            },
            "d": {
                "status": "done",
                "branch": "marestail/d",
                "head": "d" * 40,
                "finished_at": "2026-10-17T12:00:00Z",
                "to": None,
                "exit_code": 0,
                "reason": None,
            },
            "e": {
                "status": "blocked",
                "branch": None,
                "head": None,
                "finished_at": "2026-10-17T12:00:00Z",
                "to": None,
                "exit_code": None,
                "reason": "a failed",
            },
        },
    }
    (state_dir / "state.json").write_text(json.dumps(initial_state))
    append_events(repo, "a", 999, "failed", 1, "coder", "tests failed")
    outcomes = {
        "a": ("complete", 0, "coder", None, None),
        "b": ("complete", 0, "coder", None, None),
        "e": ("complete", 0, "coder", None, None),
    }
    fake_start, recorded = make_child_seam(repo, outcomes)
    monkeypatch.setattr(queue, "_start_child", fake_start)
    code = queue.run_queue([], None, None, False, ["a"], None, None, None, None, [])
    assert code == 1
    cli_path = str(Path(cli.__file__).resolve())
    assert recorded[0] == [sys.executable, "-u", cli_path, "run", "tasks/a.md", "--auto", "--from", "coder"]
    assert recorded[1] == [sys.executable, "-u", cli_path, "run", "tasks/b.md", "--auto"]
    assert Path(recorded[2][4]).as_posix() == "tasks/e.md"
    assert "--from" not in recorded[2]
    state = json.loads((state_dir / "state.json").read_text())["tasks"]
    short = git(repo, "rev-parse", "--short", state["a"]["head"]).strip()
    assert state["b"]["branch"] == "marestail/a"
    assert state["c"]["status"] == "blocked"
    assert state["c"]["reason"] == f"waiting for a ({short}) to be merged into origin/main"
    assert state["e"]["status"] == "done"
    assert state["e"]["branch"] == "marestail/a"
    assert state["d"]["status"] == "done"
    assert "skip d done" in (state_dir / "queue.log").read_text()


def test_stopped_dependency_blocks_dependants(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, _ = setup_bare_repo(tmp_path)
    monkeypatch.chdir(repo)
    write_task_file(repo / "tasks" / "a.md", "a", [])
    write_task_file(repo / "tasks" / "b.md", "b", ["a"], stack=True)
    commit_all(repo, "add tasks")
    git(repo, "push", "-q", "origin", "main")
    outcomes = {"a": ("stopped", 1, "critic", "not approved", "critic")}
    fake_start, _ = make_child_seam(repo, outcomes)
    monkeypatch.setattr(queue, "_start_child", fake_start)
    code = queue.run_queue([], None, None, False, [], None, None, None, None, [])
    assert code == 1
    state = json.loads((repo / ".marestail" / "queue" / "state.json").read_text())["tasks"]
    assert state["a"]["status"] == "stopped"
    assert state["a"]["reason"] == "stopped at critic: not approved"
    assert state["b"]["status"] == "blocked"
    assert state["b"]["reason"] == "a stopped"


def test_here_runs_all_on_checked_out_branch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "marestail/green-the-repo")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "commit.gpgsign", "false")
    (repo / "marestail.toml").write_text('[git]\nbase = "origin/main"\n')
    write_task_file(repo / "tasks" / "a.md", "a", [])
    write_task_file(repo / "tasks" / "b.md", "b", ["a"], stack=True)
    write_task_file(repo / "tasks" / "c.md", "c", ["a"], stack=False)
    write_task_file(repo / "tasks" / "d.md", "d", [])
    commit_all(repo, "init")
    git(repo, "remote", "add", "origin", str(tmp_path / "not-a-repo"))
    monkeypatch.chdir(repo)
    fake_start, recorded = make_child_seam(repo, {})
    monkeypatch.setattr(queue, "_start_child", fake_start)
    code = queue.run_queue([], None, None, True, [], None, None, None, None, [])
    assert code == 0
    assert len(recorded) == 4
    out = capsys.readouterr().out
    assert "fetch " not in out
    state = json.loads((repo / ".marestail" / "queue" / "state.json").read_text())["tasks"]
    assert state["c"]["status"] == "done"
    for task_id in ("a", "b", "c", "d"):
        assert state[task_id]["branch"] == "marestail/green-the-repo"
    assert _ref_missing(repo, "marestail/a")


@pytest.mark.parametrize("res,st", [("failed", "failed"), ("stopped", "stopped")])
def test_here_stops_at_first_failed_or_stopped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, res: str, st: str) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "marestail/green-the-repo")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "commit.gpgsign", "false")
    (repo / "marestail.toml").write_text('[git]\nbase = "origin/main"\n')
    write_task_file(repo / "tasks" / "a.md", "a", [])
    write_task_file(repo / "tasks" / "b.md", "b", ["a"], stack=True)
    write_task_file(repo / "tasks" / "c.md", "c", ["a"], stack=False)
    write_task_file(repo / "tasks" / "d.md", "d", [])
    commit_all(repo, "init")
    monkeypatch.chdir(repo)
    outcomes = {"a": (res, 1, "coder", "broken", None)}
    fake_start, recorded = make_child_seam(repo, outcomes)
    monkeypatch.setattr(queue, "_start_child", fake_start)
    code = queue.run_queue([], None, None, True, [], None, None, None, None, [])
    assert code == 1
    assert len(recorded) == 1
    state = json.loads((repo / ".marestail" / "queue" / "state.json").read_text())["tasks"]
    assert state["a"]["status"] == st
    assert "b" not in state
    assert "c" not in state
    assert "d" not in state


def test_squash_merge_leaves_c_blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, _ = setup_bare_repo(tmp_path)
    monkeypatch.chdir(repo)
    write_task_file(repo / "tasks" / "a.md", "a", [])
    write_task_file(repo / "tasks" / "c.md", "c", ["a"], stack=False)
    commit_all(repo, "add tasks")
    git(repo, "push", "-q", "origin", "main")
    git(repo, "switch", "-q", "-c", "marestail/a")
    (repo / "squashed.txt").write_text("squash\n")
    git(repo, "add", "squashed.txt")
    git(repo, "commit", "-q", "-m", "work a")
    a_head = git(repo, "rev-parse", "HEAD").strip()
    short = git(repo, "rev-parse", "--short", "HEAD").strip()
    git(repo, "switch", "-q", "main")
    git(repo, "merge", "--squash", "marestail/a")
    git(repo, "commit", "-q", "-m", "squash a")
    git(repo, "push", "-q", "origin", "main")
    ancestor = subprocess.run(["git", "merge-base", "--is-ancestor", a_head, "origin/main"], cwd=repo, check=False)
    assert ancestor.returncode == 1
    state_dir = repo / ".marestail" / "queue"
    state_dir.mkdir(parents=True)
    (state_dir / "state.json").write_text(json.dumps({"v": 1, "tasks": {"a": _done_entry("marestail/a", a_head, "2026-10-17T12:00:00Z")}}))
    fake_start, recorded = make_child_seam(repo, {})
    monkeypatch.setattr(queue, "_start_child", fake_start)
    code = queue.run_queue(["tasks/c.md"], None, None, False, [], None, None, None, None, [])
    assert code == 1
    assert len(recorded) == 0
    state = json.loads((state_dir / "state.json").read_text())["tasks"]
    assert state["c"]["status"] == "blocked"
    assert state["c"]["reason"] == f"waiting for a ({short}) to be merged into origin/main"


@pytest.mark.parametrize(
    "stack,flags,rec,expected_outcome",
    [
        (True, [], ("complete", "marestail/021-dep", "1" * 39 + "a", "ev"), "runs"),
        (True, [], ("none", None, None, None), "blocked_not_completed"),
        (True, [], ("stopped", None, None, None), "blocked_not_completed"),
        (True, [], ("failed", None, None, None), "blocked_not_completed"),
        (True, [], ("complete", None, None, None), "blocked_no_branch"),
        (False, [], ("complete", None, None, None), "blocked_no_branch"),
        (True, ["--here"], ("complete", None, None, None), "runs"),
    ],
)
def test_dependency_outside_the_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stack: bool,
    flags: list[str],
    rec: tuple[str, str | None, str | None, str | None],
    expected_outcome: str,
) -> None:
    repo, _ = setup_bare_repo(tmp_path)
    monkeypatch.chdir(repo)
    write_task_file(repo / "tasks" / "e.md", "e", ["021-dep"], stack=stack)
    commit_all(repo, "add e")
    git(repo, "push", "-q", "origin", "main")
    res_kind, b_name, h_sha, ev = rec
    record: dict[str, Any] = {
        "task": "021-dep",
        "result": res_kind if res_kind != "none" else None,
        "state": res_kind if res_kind != "none" else "never-started",
        "branch": b_name,
        "head": h_sha,
        "events": ev,
    }
    orig_read_run = status_module.read_run
    monkeypatch.setattr(status_module, "read_run", lambda _r, t: record if t == "021-dep" else orig_read_run(_r, t))
    fake_start, recorded = make_child_seam(repo, {})
    monkeypatch.setattr(queue, "_start_child", fake_start)
    code = queue.run_queue(["tasks/e.md"], None, None, "--here" in flags, [], None, None, None, None, [])
    if expected_outcome == "runs":
        assert code == 0
        assert len(recorded) == 1
    elif expected_outcome == "blocked_not_completed":
        assert code == 1
        assert len(recorded) == 0
        st = json.loads((repo / ".marestail" / "queue" / "state.json").read_text())["tasks"]["e"]
        assert st["reason"] == "021-dep is not in this queue and has not completed"
    elif expected_outcome == "blocked_no_branch":
        assert code == 1
        assert len(recorded) == 0
        st = json.loads((repo / ".marestail" / "queue" / "state.json").read_text())["tasks"]["e"]
        assert st["reason"] == "021-dep has no recorded branch; run it with --here or rerun it through the queue"


def test_missing_task_file_committed_onto_branch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, _ = setup_bare_repo(tmp_path)
    monkeypatch.chdir(repo)
    write_task_file(repo / "tasks" / "a.md", "a", [])
    commit_all(repo, "commit a only locally on main")
    fake_start, _ = make_child_seam(repo, {})
    monkeypatch.setattr(queue, "_start_child", fake_start)
    code = queue.run_queue(["tasks/a.md"], None, None, False, [], None, None, None, None, [])
    assert code == 0
    subject = git(repo, "log", "marestail/a", "--grep=queue: task a", "-1", "--format=%s").strip()
    assert subject == "queue: task a"
    paths = git(repo, "log", "marestail/a", "--grep=queue: task a", "-1", "--name-only", "--format=").strip()
    assert paths == "tasks/a.md"


def test_different_task_file_is_committed_onto_branch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, _ = setup_bare_repo(tmp_path)
    monkeypatch.chdir(repo)
    write_task_file(repo / "tasks" / "a.md", "a", [], title="Old")
    commit_all(repo, "old a")
    git(repo, "push", "-q", "origin", "main")
    refreshed = (repo / "tasks" / "a.md").read_text().replace("# Old\n", "# New\n")
    (repo / "tasks" / "a.md").write_text(refreshed)
    commit_all(repo, "refresh a")
    fake_start, _ = make_child_seam(repo, {})
    monkeypatch.setattr(queue, "_start_child", fake_start)
    absolute = str((repo / "tasks" / "a.md").resolve())
    code = queue.run_queue([absolute], None, None, False, [], None, None, None, None, [])
    assert code == 0
    subject = git(repo, "log", "marestail/a", "--grep=queue: task a", "-1", "--format=%s").strip()
    assert subject == "queue: task a"
    paths = git(repo, "log", "marestail/a", "--grep=queue: task a", "-1", "--name-only", "--format=").strip()
    assert paths == "tasks/a.md"
    assert git(repo, "show", "marestail/a:tasks/a.md") == refreshed


def test_existing_task_branch_not_reset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, _ = setup_bare_repo(tmp_path)
    monkeypatch.chdir(repo)
    write_task_file(repo / "tasks" / "a.md", "a", [])
    commit_all(repo, "commit a")
    git(repo, "push", "-q", "origin", "main")
    git(repo, "switch", "-q", "-c", "marestail/a")
    (repo / "keep.txt").write_text("keep\n")
    git(repo, "add", "keep.txt")
    git(repo, "commit", "-q", "-m", "keep me")
    git(repo, "switch", "-q", "main")
    fake_start, _ = make_child_seam(repo, {})
    monkeypatch.setattr(queue, "_start_child", fake_start)
    code = queue.run_queue(["tasks/a.md"], None, None, False, [], None, None, None, None, [])
    assert code == 0
    parent_msg = git(repo, "log", "marestail/a~1", "-1", "--format=%s").strip()
    assert parent_msg == "keep me"


def test_here_does_not_commit_task_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, _ = setup_bare_repo(tmp_path)
    monkeypatch.chdir(repo)
    write_task_file(repo / "tasks" / "a.md", "a", [])
    commit_all(repo, "commit a")
    fake_start, _ = make_child_seam(repo, {})
    monkeypatch.setattr(queue, "_start_child", fake_start)
    code = queue.run_queue(["tasks/a.md"], None, None, True, [], None, None, None, None, [])
    assert code == 0
    log_subject = git(repo, "log", "--grep=queue: task a", "--format=%s").strip()
    assert log_subject == ""


REFUSAL_CASES = [
    ("bad_front", ["queue"], "tasks/bad.md: no front matter: line 1 must be +++\n", ""),
    ("dup_id", ["queue", "tasks/a", "tasks/b"], "tasks/b/019-x.md: duplicate task id 019-x: also tasks/a/019-x.md\n", ""),
    ("ignored", ["queue"], "", "tasks/ignored.md is ignored by git; the queue runs only committed task files\n"),
    ("detached", ["queue"], "", "HEAD is detached; check out a branch first\n"),
    ("running_run", ["queue"], "", "a marestail run is running here (011-live, pid 55555); the queue would change the tree under it\n"),
    ("no_task_retry", ["queue", "--retry", "099-nope"], "", "--retry 099-nope: not a task in this queue\n"),
    ("retry_done", ["queue", "--retry", "d"], "", "--retry d: it is done, not stopped or failed\n"),
    (
        "bad_to",
        ["queue", "--to", "nonexistent"],
        "",
        "unknown role nonexistent; choose from specifier, critic, coder, cleaner, architect, design, practices, perf, hardener, qa\n",
    ),
    (
        "bad_from",
        ["queue", "--from", "nonexistent"],
        "",
        "unknown role nonexistent; choose from specifier, critic, coder, cleaner, architect, design, practices, perf, hardener, qa\n",
    ),
    (
        "hyper_cleaner",
        ["queue", "--scope", "hyper", "--to", "cleaner"],
        "",
        "unknown role cleaner; choose from specifier, critic, coder, architect, blast, hardener, qa\n",
    ),
    ("push_str", ["queue"], "", "[queue] push must be true or false, got 'yes'\n"),
    ("push_env", ["queue"], "", "MARESTAIL_QUEUE_PUSH must be 1, 0, true or false, got 'yes'\n"),
    ("pr_no_push", ["queue"], "", "[queue] pr needs [queue] push\n"),
    ("push_no_remote", ["queue"], "", "[queue] push is on but this repository has no remote named origin\n"),
    ("pr_no_gh", ["queue"], "", "[queue] pr needs the gh CLI on PATH\n"),
    ("base_nonexistent", ["queue"], "", "[git] base origin/nonexistent does not exist; set it in marestail.toml\n"),
    ("state_v2", ["queue"], "", ".marestail/queue/state.json: v must be 1, got 2\n"),
    (
        "state_bad_json",
        ["queue"],
        "",
        ".marestail/queue/state.json: not valid JSON: Expecting property name enclosed in double quotes: line 1 column 2 (char 1)\n",
    ),
    (
        "outside",
        ["queue", "../else/a.md"],
        "",
        "../else/a.md is outside the repository; the queue runs only task files inside /tmp/qa-022/outside/repo\n",
    ),
]


def _setup_bad_front(repo: Path, _mp: pytest.MonkeyPatch, _tmp: Path) -> None:
    (repo / "tasks" / "bad.md").parent.mkdir(parents=True, exist_ok=True)
    (repo / "tasks" / "bad.md").write_text("# bad\n")
    commit_all(repo, "bad")


def _setup_dup_id(repo: Path, _mp: pytest.MonkeyPatch, _tmp: Path) -> None:
    write_task_file(repo / "tasks" / "a" / "019-x.md", "019-x", [])
    write_task_file(repo / "tasks" / "b" / "019-x.md", "019-x", [])
    commit_all(repo, "dups")


def _setup_ignored(repo: Path, _mp: pytest.MonkeyPatch, _tmp: Path) -> None:
    write_task_file(repo / "tasks" / "ignored.md", "ignored", [])
    (repo / ".gitignore").write_text("tasks/ignored.md\n")
    commit_all(repo, "ignored")


def _setup_detached(repo: Path, _mp: pytest.MonkeyPatch, _tmp: Path) -> None:
    write_task_file(repo / "tasks" / "a.md", "a", [])
    commit_all(repo, "add a")
    git(repo, "checkout", "--detach", "HEAD")


def _setup_running_run(repo: Path, _mp: pytest.MonkeyPatch, tmp: Path) -> None:
    write_task_file(repo / "tasks" / "a.md", "a", [])
    commit_all(repo, "add a")
    _start_live_run(repo, tmp)


def _start_live_run(repo: Path, tmp: Path) -> None:
    shim = tmp / "shim" / "cli.py"
    shim.parent.mkdir(parents=True, exist_ok=True)
    shim.write_text("import time\ntime.sleep(300)\n")
    proc = subprocess.Popen([sys.executable, str(shim), "run", "tasks/011-live.md"], cwd=repo, start_new_session=True)
    _LIVE_RUNS.append(proc)
    events = repo / ".marestail" / "runs" / "011-live" / "events.jsonl"
    events.parent.mkdir(parents=True, exist_ok=True)
    payload = {"v": 1, "event": "run-start", "pid": proc.pid, "task": "011-live", "branch": "main"}
    events.write_text(json.dumps(payload) + "\n")


def _refusal_err(fault: str, expected: str) -> str:
    if fault != "running_run" or not _LIVE_RUNS:
        return expected
    return expected.replace("55555", str(_LIVE_RUNS[0].pid))


def _setup_no_task_retry(repo: Path, _mp: pytest.MonkeyPatch, _tmp: Path) -> None:
    write_task_file(repo / "tasks" / "a.md", "a", [])
    commit_all(repo, "add a")


def _setup_retry_done(repo: Path, _mp: pytest.MonkeyPatch, _tmp: Path) -> None:
    write_task_file(repo / "tasks" / "d.md", "d", [])
    commit_all(repo, "add d")
    state_dir = repo / ".marestail" / "queue"
    state_dir.mkdir(parents=True)
    (state_dir / "state.json").write_text(json.dumps({"v": 1, "tasks": {"d": {"status": "done"}}}))


def _setup_basic_task(repo: Path, _mp: pytest.MonkeyPatch, _tmp: Path) -> None:
    write_task_file(repo / "tasks" / "a.md", "a", [])
    commit_all(repo, "add a")


def _setup_push_str(repo: Path, _mp: pytest.MonkeyPatch, _tmp: Path) -> None:
    (repo / "marestail.toml").write_text('[git]\nbase = "origin/main"\n[queue]\npush = "yes"\n')
    write_task_file(repo / "tasks" / "a.md", "a", [])
    commit_all(repo, "config")


def _setup_push_env(repo: Path, mp: pytest.MonkeyPatch, _tmp: Path) -> None:
    mp.setenv("MARESTAIL_QUEUE_PUSH", "yes")
    write_task_file(repo / "tasks" / "a.md", "a", [])
    commit_all(repo, "task")


def _setup_pr_no_push(repo: Path, _mp: pytest.MonkeyPatch, _tmp: Path) -> None:
    (repo / "marestail.toml").write_text('[git]\nbase = "origin/main"\n[queue]\npush = false\npr = true\n')
    write_task_file(repo / "tasks" / "a.md", "a", [])
    commit_all(repo, "config")


def _setup_push_no_remote(repo: Path, _mp: pytest.MonkeyPatch, _tmp: Path) -> None:
    (repo / "marestail.toml").write_text('[git]\nbase = "main"\n[queue]\npush = true\n')
    git(repo, "remote", "remove", "origin")
    write_task_file(repo / "tasks" / "a.md", "a", [])
    commit_all(repo, "config")


def _setup_pr_no_gh(repo: Path, mp: pytest.MonkeyPatch, _tmp: Path) -> None:
    (repo / "marestail.toml").write_text('[git]\nbase = "origin/main"\n[queue]\npush = true\npr = true\n')
    mp.setattr(shutil, "which", lambda _b: None)
    write_task_file(repo / "tasks" / "a.md", "a", [])
    commit_all(repo, "config")


def _setup_base_nonexistent(repo: Path, _mp: pytest.MonkeyPatch, _tmp: Path) -> None:
    (repo / "marestail.toml").write_text('[git]\nbase = "origin/nonexistent"\n')
    write_task_file(repo / "tasks" / "a.md", "a", [])
    commit_all(repo, "config")


def _setup_state_v2(repo: Path, _mp: pytest.MonkeyPatch, _tmp: Path) -> None:
    write_task_file(repo / "tasks" / "a.md", "a", [])
    commit_all(repo, "task")
    state_dir = repo / ".marestail" / "queue"
    state_dir.mkdir(parents=True)
    (state_dir / "state.json").write_text('{"v": 2}')


def _setup_state_bad_json(repo: Path, _mp: pytest.MonkeyPatch, _tmp: Path) -> None:
    write_task_file(repo / "tasks" / "a.md", "a", [])
    commit_all(repo, "task")
    state_dir = repo / ".marestail" / "queue"
    state_dir.mkdir(parents=True)
    (state_dir / "state.json").write_text("{")


def _setup_outside(_repo: Path, mp: pytest.MonkeyPatch, tmp: Path) -> None:
    mp.setattr(queue, "_find_root", lambda _s: Path("/tmp/qa-022/outside/repo"))
    write_task_file(tmp / "else" / "a.md", "a", [])


REFUSAL_SETUPS: dict[str, Any] = {
    "bad_front": _setup_bad_front,
    "dup_id": _setup_dup_id,
    "ignored": _setup_ignored,
    "detached": _setup_detached,
    "running_run": _setup_running_run,
    "no_task_retry": _setup_no_task_retry,
    "retry_done": _setup_retry_done,
    "bad_to": _setup_basic_task,
    "bad_from": _setup_basic_task,
    "hyper_cleaner": _setup_basic_task,
    "push_str": _setup_push_str,
    "push_env": _setup_push_env,
    "pr_no_push": _setup_pr_no_push,
    "push_no_remote": _setup_push_no_remote,
    "pr_no_gh": _setup_pr_no_gh,
    "base_nonexistent": _setup_base_nonexistent,
    "state_v2": _setup_state_v2,
    "state_bad_json": _setup_state_bad_json,
    "outside": _setup_outside,
}


@pytest.mark.parametrize("fault,cmd,expected_out,expected_err", REFUSAL_CASES)
def test_refusals_before_any_child_or_branch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fault: str,
    cmd: list[str],
    expected_out: str,
    expected_err: str,
) -> None:
    repo, _ = setup_bare_repo(tmp_path)
    monkeypatch.chdir(repo)
    try:
        REFUSAL_SETUPS[fault](repo, monkeypatch, tmp_path)
        code = cli.main(cmd)
        assert code == 2
        captured = capsys.readouterr()
        assert captured.out == expected_out
        assert captured.err == _refusal_err(fault, expected_err)
        assert not (repo / ".marestail" / "queue" / "lock").exists()
    finally:
        _stop_live_runs()


def test_lock_behavior(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    repo, _ = setup_bare_repo(tmp_path)
    monkeypatch.chdir(repo)
    write_task_file(repo / "tasks" / "a.md", "a", [])
    commit_all(repo, "add a")
    lock_file = repo / ".marestail" / "queue" / "lock"
    lock_file.parent.mkdir(parents=True)
    lock_file.write_text("12345\n")
    orig_exists = os.path.exists
    monkeypatch.setattr(os.path, "exists", lambda p: True if p == "/proc/12345" else orig_exists(p))
    code = cli.main(["queue"])
    assert code == 2
    assert "another marestail queue (pid 12345) is running in this repository\n" in capsys.readouterr().err
    assert lock_file.read_text() == "12345\n"
    code2 = cli.main(["queue", "--to", "nonexistent"])
    assert code2 == 2
    err2 = capsys.readouterr().err
    assert "unknown role nonexistent" in err2
    assert "another marestail queue" not in err2
    assert lock_file.read_text() == "12345\n"
    lock_file.write_text("999999\n")
    shutil.rmtree(repo / "tasks")
    commit_all(repo, "remove tasks")
    code3 = cli.main(["queue"])
    assert code3 == 2
    out3 = capsys.readouterr().out
    assert "tasks: no such file or folder\n" in out3
    assert not lock_file.exists()


def test_clean_tree_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    repo, _ = setup_bare_repo(tmp_path)
    monkeypatch.chdir(repo)
    write_task_file(repo / "tasks" / "a.md", "a", [])
    commit_all(repo, "add a")
    notes = repo / ".marestail" / "notes.txt"
    notes.parent.mkdir(parents=True, exist_ok=True)
    notes.write_text("note\n")
    (repo / "marestail.toml").write_text('[git]\nbase = "origin/main"\n[queue]\npush = "yes"\n')
    commit_all(repo, "commit config")
    code1 = cli.main(["queue"])
    assert code1 == 2
    err1 = capsys.readouterr().err
    assert "[queue] push must be true or false, got 'yes'" in err1
    assert "uncommitted changes" not in err1
    (repo / "marestail.toml").write_text('[git]\nbase = "origin/main"\n')
    commit_all(repo, "reset config")
    (repo / "tasks" / "a.md").write_text("+++\ndepends = []\n+++\n# edited\n")
    code2 = cli.main(["queue"])
    assert code2 == 2
    err2 = capsys.readouterr().err
    assert "the working tree has uncommitted changes; commit or stash them first:\ntasks/a.md\n" in err2
    git(repo, "checkout", "--", "tasks/a.md")
    (repo / "untracked.py").write_text("x = 1\n")
    code3 = cli.main(["queue"])
    assert code3 == 2
    err3 = capsys.readouterr().err
    assert "the working tree has uncommitted changes; commit or stash them first:\nuntracked.py\n" in err3
    (repo / "untracked.py").unlink()
    for i in range(1, 22):
        (repo / f"p{i:02d}").write_text("test")
    code4 = cli.main(["queue"])
    assert code4 == 2
    err4 = capsys.readouterr().err
    assert "p20\n" in err4
    assert "p21\n" not in err4


def test_child_arguments_and_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, _ = setup_bare_repo(tmp_path)
    monkeypatch.chdir(repo)
    write_task_file(repo / "tasks" / "a.md", "a", [])
    write_task_file(repo / "tasks" / "d.md", "d", [])
    commit_all(repo, "add tasks")
    git(repo, "push", "-q", "origin", "main")
    captured_envs: list[dict[str, str]] = []
    captured_cwds: list[Path] = []
    fake_start, recorded = make_child_seam(repo, {})
    monkeypatch.delenv("MARESTAIL_LIMIT_WAITS", raising=False)
    frozen = datetime(2026, 10, 17, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(queue, "_now_utc", lambda: frozen)
    real_popen = subprocess.Popen

    def _mock_popen(argv: list[str], **kwargs: Any) -> Any:
        if not _is_queue_child(argv):
            return real_popen(argv, **kwargs)
        captured_cwds.append(Path(kwargs["cwd"]).resolve())
        captured_envs.append(kwargs["env"])
        assert kwargs["start_new_session"] is True
        return fake_start(argv, kwargs["cwd"], kwargs["env"])

    monkeypatch.setattr(subprocess, "Popen", _mock_popen)
    cmd = [
        "queue",
        "--from",
        "coder",
        "--to",
        "hardener",
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
        "--focus",
        "y",
    ]
    code = cli.main(cmd)
    assert code == 0
    assert len(recorded) == 2
    cli_path = str(Path(cli.__file__).resolve())
    shared = ["--agent", "claude", "--model", "m", "--effort", "high", "--scope", "changed", "--focus", "x", "--focus", "y"]
    assert recorded[0] == [sys.executable, "-u", cli_path, "run", "tasks/a.md", "--auto", "--from", "coder", "--to", "hardener", *shared]
    assert recorded[1] == [sys.executable, "-u", cli_path, "run", "tasks/d.md", "--auto", "--to", "hardener", *shared]
    assert "--retries" not in recorded[0]
    assert captured_cwds[0] == repo.resolve()
    assert captured_cwds[1] == repo.resolve()
    assert captured_envs[0]["MARESTAIL_LIMIT_WAITS"] == "36"
    recorded_state = json.loads((repo / ".marestail" / "queue" / "state.json").read_text())["tasks"]["a"]
    assert recorded_state["to"] == "hardener"
    assert recorded_state["exit_code"] == 0
    assert recorded_state["finished_at"] == "2026-10-17T12:00:00Z"
    monkeypatch.setenv("MARESTAIL_LIMIT_WAITS", "5")
    captured_envs.clear()
    recorded.clear()
    (repo / ".marestail" / "queue" / "state.json").unlink(missing_ok=True)
    cli.main(["queue", "tasks/a.md"])
    assert captured_envs[0]["MARESTAIL_LIMIT_WAITS"] == "5"
    assert "--to" not in recorded[0]


OUTCOME_CONFIGS: dict[str, tuple[int, str | None, int, str, str | None, str | None, bool]] = {
    "early_exit": (2, None, 0, "", None, None, False),
    "complete_0": (0, "complete", 0, "coder", None, None, False),
    "complete_3": (3, "complete", 3, "qa", None, None, False),
    "stopped_critic": (1, "stopped", 1, "critic", "not approved", "critic", False),
    "failed_build": (1, "failed", 1, "coder", "build error", None, False),
    "interrupted": (1, "interrupted", 1, "coder", None, None, True),
}


def _make_outcome_child(repo: Path, ending: str) -> Any:
    child_exit, res, code, role, reason, stopped_at, omit = OUTCOME_CONFIGS[ending]

    def _start(_argv: list[str], _cwd: Path, _env: dict[str, str]) -> FakeChild:
        cur_branch = git(repo, "symbolic-ref", "--quiet", "--short", "HEAD").strip()
        if res is not None:
            append_events(repo, "a", 7777, res, code, role, reason=reason, stopped_at=stopped_at, branch=cur_branch, omit_end=omit)
        return FakeChild(7777, child_exit)

    return _start


def _check_outcome_assertions(
    repo: Path, ending: str, expected_status: str | None, expected_reason: str | None, capsys: pytest.CaptureFixture[str]
) -> None:
    state_file = repo / ".marestail" / "queue" / "state.json"
    if ending == "early_exit":
        captured = capsys.readouterr()
        assert captured.err == "marestail run exited 2 before it started the pipeline\n"
        assert "a" not in _queue_tasks(repo)
    else:
        st = json.loads(state_file.read_text())["tasks"]["a"]
        assert st["status"] == expected_status
        assert st["reason"] == expected_reason


@pytest.mark.parametrize(
    "ending,expected_status,expected_code,expected_reason",
    [
        ("early_exit", None, 2, "marestail run exited 2 before it started the pipeline"),
        ("complete_0", "done", 0, None),
        ("complete_3", "stopped", 1, "complete but NOT verified against the running app"),
        ("stopped_critic", "stopped", 1, "stopped at critic: not approved"),
        ("failed_build", "failed", 1, "build error"),
        ("interrupted", "failed", 1, "marestail run ended without finishing (exit 1) at coder"),
    ],
)
def test_outcome_mapping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    ending: str,
    expected_status: str | None,
    expected_code: int,
    expected_reason: str | None,
) -> None:
    repo, _ = setup_bare_repo(tmp_path)
    monkeypatch.chdir(repo)
    write_task_file(repo / "tasks" / "a.md", "a", [])
    commit_all(repo, "add a")
    git(repo, "push", "-q", "origin", "main")
    monkeypatch.setattr(queue, "_start_child", _make_outcome_child(repo, ending))
    code = cli.main(["queue", "tasks/a.md"])
    assert code == expected_code
    _check_outcome_assertions(repo, ending, expected_status, expected_reason, capsys)


@pytest.mark.parametrize(
    "rec,expected_from,child_runs",
    [
        (("dead", "coder", "interrupted"), "coder", True),
        (("stopped", "critic", "stopped"), None, False),
        (("failed", "coder", "failed"), None, False),
        (("abandoned", None, None), None, True),
        (("leftover", None, None), None, True),
        (("none", None, None), None, True),
    ],
)
def test_resume_from_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rec: tuple[str, str | None, str | None], expected_from: str | None, child_runs: bool
) -> None:
    repo, _ = setup_bare_repo(tmp_path)
    monkeypatch.chdir(repo)
    write_task_file(repo / "tasks" / "a.md", "a", [])
    commit_all(repo, "add a")
    git(repo, "push", "-q", "origin", "main")
    state_name, role_name, res_name = rec
    record = {
        "task": "a",
        "state": state_name if state_name != "none" else "never-started",
        "role": role_name,
        "result": res_name,
        "stopped_at": "critic" if state_name == "stopped" else None,
        "reason": "not approved" if state_name == "stopped" else ("build error" if state_name == "failed" else None),
    }
    monkeypatch.setattr(status_module, "read_run", lambda _r, _t: record)
    fake_start, recorded = make_child_seam(repo, {})
    monkeypatch.setattr(queue, "_start_child", fake_start)
    _ = cli.main(["queue", "tasks/a.md"])
    if not child_runs:
        assert len(recorded) == 0
        state = json.loads((repo / ".marestail" / "queue" / "state.json").read_text())["tasks"]["a"]
        assert state["status"] == state_name
    else:
        assert len(recorded) == 1
        if expected_from:
            assert "--from" in recorded[0]
            assert expected_from in recorded[0]
        else:
            assert "--from" not in recorded[0]


def test_explicit_from_and_retry_exit_3(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, _ = setup_bare_repo(tmp_path)
    monkeypatch.chdir(repo)
    write_task_file(repo / "tasks" / "a.md", "a", [])
    write_task_file(repo / "tasks" / "b.md", "b", [])
    commit_all(repo, "add tasks")
    git(repo, "push", "-q", "origin", "main")
    append_events(repo, "b", 888, "interrupted", 1, "qa", None, None, "marestail/b", omit_end=True)
    fake_start, recorded = make_child_seam(repo, {})
    monkeypatch.setattr(queue, "_start_child", fake_start)
    cli.main(["queue", "--from", "specifier"])
    assert "--from" in recorded[0]
    assert "specifier" in recorded[0]
    assert "--from" in recorded[1]
    assert "qa" in recorded[1]
    (repo / ".marestail" / "queue" / "state.json").unlink(missing_ok=True)
    recorded.clear()
    state_dir = repo / ".marestail" / "queue"
    state_dir.mkdir(parents=True, exist_ok=True)
    initial_state = {
        "v": 1,
        "tasks": {
            "a": {
                "status": "stopped",
                "branch": "marestail/a",
                "head": "a" * 40,
                "finished_at": "2026-10-17T12:00:00Z",
                "to": None,
                "exit_code": 3,
                "reason": "complete but NOT verified against the running app",
            }
        },
    }
    (state_dir / "state.json").write_text(json.dumps(initial_state))
    monkeypatch.setattr(status_module, "read_run", lambda _r, _t: {"task": "a", "state": "stopped", "role": "qa"})
    cli.main(["queue", "--retry", "a"])
    assert len(recorded) == 1
    assert "--from" in recorded[0]
    assert "qa" in recorded[0]


def test_push_and_pr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    repo, _ = setup_bare_repo(tmp_path)
    monkeypatch.chdir(repo)
    write_task_file(repo / "tasks" / "a.md", "a", [], title="Task A")
    write_task_file(repo / "tasks" / "b.md", "b", ["a"], stack=True, title="Task B")
    commit_all(repo, "add tasks")
    git(repo, "push", "-q", "origin", "main")
    monkeypatch.setenv("MARESTAIL_QUEUE_PUSH", "1")
    monkeypatch.setenv("MARESTAIL_QUEUE_PR", "1")
    _gh_on_path(monkeypatch)
    gh_calls: list[tuple[list[str], dict[str, str]]] = []

    def _mock_gh(args: list[str], root: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        gh_calls.append((args, env or {}))
        if args[:2] == ["pr", "list"]:
            count = "0\n" if len([c for c in gh_calls if c[0][:2] == ["pr", "create"]]) == 0 else "1\n"
            return subprocess.CompletedProcess(args, 0, count, "")
        if args[:2] == ["pr", "create"]:
            return subprocess.CompletedProcess(args, 0, "https://github.com/pr/1\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(queue, "_run_gh", _mock_gh)
    fake_start, _ = make_child_seam(repo, {})
    monkeypatch.setattr(queue, "_start_child", fake_start)
    code = cli.main(["queue", "tasks/a.md", "tasks/b.md"])
    assert code == 0
    pr_creates = [c for c in gh_calls if c[0][:2] == ["pr", "create"]]
    assert len(pr_creates) == 1
    body_path = repo / ".marestail" / "queue" / "pr-a.md"
    assert pr_creates[0][0] == [
        "pr",
        "create",
        "--base",
        "main",
        "--head",
        "marestail/a",
        "--title",
        "Task A",
        "--body-file",
        ".marestail/queue/pr-a.md",
    ]
    assert pr_creates[0][1]["GH_PROMPT_DISABLED"] == "1"
    pr_body = body_path.read_text()
    assert pr_body == (
        "`marestail queue` opened this pull request after task `a` finished on `marestail/a`.\n\n"
        "Tasks done on this branch:\n"
        "- a: Task A\n\n"
        "Each commit is stamped with the role and model that made it.\n"
    )
    assert "pr-opened a marestail/a" in (repo / ".marestail" / "queue" / "queue.log").read_text()


class _GroupChild:
    def __init__(self, proc: subprocess.Popen[bytes], times_out: bool) -> None:
        self.pid = proc.pid
        self._proc = proc
        self._times_out = times_out
        self._signalled = False
        self.waits: list[float | None] = []
        self.returncode: int | None = None

    def wait(self, timeout: float | None = None) -> int:
        self.waits.append(timeout)
        if self._deadline(timeout):
            raise subprocess.TimeoutExpired(cmd="sleep", timeout=30)
        if self._ping(timeout):
            return 0
        return self._reap()

    def _deadline(self, timeout: float | None) -> bool:
        return timeout == 30 and self._times_out

    def _ping(self, timeout: float | None) -> bool:
        if timeout is not None or self._signalled:
            return False
        self._signalled = True
        signal.raise_signal(signal.SIGTERM)
        return True

    def _reap(self) -> int:
        code = self._proc.wait(timeout=5)
        self.returncode = code
        return int(code)


def _spawn_group(times_out: bool) -> subprocess.Popen[bytes]:
    if times_out:
        code = "import signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(120)\n"
        return subprocess.Popen([sys.executable, "-c", code], start_new_session=True)
    return subprocess.Popen(["sleep", "120"], start_new_session=True)


def _hold_child(repo: Path, times_out: bool) -> _GroupChild:
    proc = _spawn_group(times_out)
    append_events(repo, "a", proc.pid, "interrupted", 1, "coder", branch="marestail/a", omit_end=True)
    return _GroupChild(proc, times_out)


def _arm_signal_queue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, times_out: bool) -> tuple[Path, _GroupChild, list[tuple[int, int]]]:
    repo, _ = setup_bare_repo(tmp_path)
    monkeypatch.chdir(repo)
    write_task_file(repo / "tasks" / "a.md", "a", [])
    commit_all(repo, "add a")
    git(repo, "push", "-q", "origin", "main")
    monkeypatch.setenv("MARESTAIL_QUEUE_PUSH", "1")
    killed: list[tuple[int, int]] = []
    real_killpg = os.killpg

    def _killpg(pid: int, sig: int) -> None:
        killed.append((pid, sig))
        real_killpg(pid, sig)

    monkeypatch.setattr(os, "killpg", _killpg)
    child = _hold_child(repo, times_out)
    monkeypatch.setattr(queue, "_start_child", lambda _argv, _cwd, _env: child)
    return repo, child, killed


def _reap_group(child: _GroupChild) -> None:
    if child._proc.poll() is None:
        child._proc.kill()
    child._proc.wait(timeout=5)


def _assert_signal_stop(repo: Path, child: _GroupChild, killed: list[tuple[int, int]], code: object, times_out: bool) -> None:
    assert code == 143
    assert (child.pid, signal.SIGTERM) in killed
    assert ((child.pid, signal.SIGKILL) in killed) is times_out
    assert 30 in child.waits
    with pytest.raises(ProcessLookupError):
        os.kill(child.pid, 0)
    log_content = (repo / ".marestail" / "queue" / "queue.log").read_text()
    assert "interrupted a by signal 15; the next run resumes from coder" in log_content
    assert "ended without finishing" not in log_content
    assert "pushed " not in log_content
    assert not (repo / ".marestail" / "queue" / "lock").exists()
    assert git(repo, "symbolic-ref", "--quiet", "--short", "HEAD").strip() == "marestail/a"
    assert "a" not in _queue_tasks(repo)
    remote = subprocess.run(["git", "ls-remote", "--heads", "origin", "marestail/a"], cwd=repo, capture_output=True, text=True, check=False)
    assert remote.stdout.strip() == ""


@pytest.mark.parametrize("times_out", [False, True])
def test_signals(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, times_out: bool) -> None:
    repo, child, killed = _arm_signal_queue(tmp_path, monkeypatch, times_out)
    try:
        with pytest.raises(SystemExit) as raised:
            queue.run_queue(["tasks/a.md"], None, None, False, [], None, None, None, None, [])
        _assert_signal_stop(repo, child, killed, raised.value.code, times_out)
    finally:
        _reap_group(child)


def test_dirt_after_done_child_stops_next_switch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, _ = setup_bare_repo(tmp_path)
    monkeypatch.chdir(repo)
    write_task_file(repo / "tasks" / "a.md", "a", [])
    write_task_file(repo / "tasks" / "b.md", "b", [])
    commit_all(repo, "add tasks")
    git(repo, "push", "-q", "origin", "main")
    fake_start, _ = make_child_seam(repo, {})

    def _leaking_child(argv: list[str], cwd: Path, env: dict[str, str]) -> FakeChild:
        res = fake_start(argv, cwd, env)
        (repo / "leak.txt").write_text("leak\n")
        return res

    monkeypatch.setattr(queue, "_start_child", _leaking_child)
    code = queue.run_queue([], None, None, False, [], None, None, None, None, [])
    assert code == 1
    log_content = (repo / ".marestail" / "queue" / "queue.log").read_text()
    assert "stopped before b: the working tree has uncommitted changes" in log_content
    assert "left on marestail/a: the working tree has uncommitted changes" in log_content
    state = json.loads((repo / ".marestail" / "queue" / "state.json").read_text())["tasks"]
    assert "b" not in state


def test_read_lock_pid_invalid_or_missing(tmp_path: Path) -> None:
    p = tmp_path / "lock"
    p.write_text("not-a-number\n")
    assert queue._read_lock_pid(p) is None
    p.unlink()
    assert queue._read_lock_pid(p) is None


def test_remove_lock_none() -> None:
    queue._remove_lock(None)


def test_stored_status_reader_delegation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(status_module, "read_run", lambda _r, t: {"task": t, "result": "complete"})
    assert queue._stored_status("a", {}, repo) == "done"
    monkeypatch.setattr(status_module, "read_run", lambda _r, t: {"task": t, "result": "stopped"})
    assert queue._stored_status("a", {}, repo) == "stopped"
    monkeypatch.setattr(status_module, "read_run", lambda _r, t: {"task": t, "result": "failed"})
    assert queue._stored_status("a", {}, repo) == "failed"
    monkeypatch.setattr(status_module, "read_run", lambda _r, t: {"task": t, "state": "never-started"})
    assert queue._stored_status("a", {}, repo) == "never-started"


def test_read_env_push_pr_false_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARESTAIL_QUEUE_PUSH", "0")
    monkeypatch.setenv("MARESTAIL_QUEUE_PR", "false")
    code, push_val, pr_val = queue._read_env_push_pr()
    assert code == 0
    assert push_val is False
    assert pr_val is False


def test_read_env_pr_invalid(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.delenv("MARESTAIL_QUEUE_PUSH", raising=False)
    monkeypatch.setenv("MARESTAIL_QUEUE_PR", "maybe")
    code, _, _ = queue._read_env_push_pr()
    assert code == 2
    assert "MARESTAIL_QUEUE_PR must be 1, 0, true or false, got 'maybe'" in capsys.readouterr().err


def test_read_toml_push_pr_invalid_pr(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "marestail.toml").write_text('[queue]\npush = true\npr = "yes"\n')
    cfg = config_module.load(tmp_path)
    code, _, _ = queue._read_toml_push_pr(cfg)
    assert code == 2
    assert "[queue] pr must be true or false, got 'yes'" in capsys.readouterr().err


def test_fetch_failure_is_logged_and_the_queue_continues(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, _bare = setup_bare_repo(tmp_path)
    monkeypatch.chdir(repo)
    write_task_file(repo / "tasks" / "a.md", "a", [])
    write_task_file(repo / "tasks" / "c.md", "c", ["a"], stack=False)
    commit_all(repo, "tasks")
    git(repo, "push", "-q", "origin", "main")
    git(repo, "remote", "set-url", "origin", str(tmp_path / "not-a-repo"))
    fake_start, recorded = make_child_seam(repo, {})
    monkeypatch.setattr(queue, "_start_child", fake_start)
    assert queue.run_queue([], None, None, False, [], None, None, None, None, []) == 1
    assert len(recorded) == 1
    log = (repo / ".marestail" / "queue" / "queue.log").read_text()
    assert "fetch origin failed: and the repository exists." in log
    state = json.loads((repo / ".marestail" / "queue" / "state.json").read_text())["tasks"]
    assert state["a"]["status"] == "done"
    assert state["c"]["status"] == "blocked"


def test_maybe_fetch_base_branches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log_path = tmp_path / "queue.log"
    queue._maybe_fetch_base(tmp_path, "main", log_path)
    assert not log_path.exists()
    proc = subprocess.CompletedProcess([], 1, "", "fatal: could not read from remote\n")
    remote_proc = subprocess.CompletedProcess([], 0, "origin\n", "")
    orig_run = queue._git_run

    def _mock_git(args: list[str], root: Path) -> subprocess.CompletedProcess[str]:
        if args[0] == "fetch":
            return proc
        if args[0] == "remote":
            return remote_proc
        return orig_run(args, root)

    monkeypatch.setattr(queue, "_git_run", _mock_git)
    queue._maybe_fetch_base(tmp_path, "origin/main", log_path)
    assert "fetch origin failed: fatal: could not read from remote" in log_path.read_text()


def test_count_statuses_with_unknown_status() -> None:
    t = TaskFile("a", Path("a.md"), (), None, "", True)
    counts = queue._count_statuses([t], {"tasks": {"a": {"status": "unknown"}}})
    assert counts["done"] == 0


def test_run_gh_wrapper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    recorded = []

    def _mock_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        recorded.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0, "ok", "")

    monkeypatch.setattr(subprocess, "run", _mock_run)
    res = queue._run_gh(["pr", "view"], tmp_path, {"CUSTOM_VAR": "val"})
    assert res.returncode == 0
    assert recorded[0][0] == ["gh", "pr", "view"]
    assert recorded[0][1]["cwd"] == tmp_path
    assert recorded[0][1]["capture_output"] is True
    assert recorded[0][1]["text"] is True
    assert recorded[0][1]["check"] is False
    assert recorded[0][1]["env"]["CUSTOM_VAR"] == "val"


def test_start_child_wrapper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    recorded = []

    def _mock_popen(cmd: list[str], **kwargs: Any) -> FakeChild:
        recorded.append((cmd, kwargs))
        return FakeChild(777)

    monkeypatch.setattr(subprocess, "Popen", _mock_popen)
    proc = queue._start_child(["echo", "hi"], tmp_path, {"MARESTAIL_LIMIT_WAITS": "36"})
    assert proc.pid == 777
    assert recorded[0][0] == ["echo", "hi"]
    assert recorded[0][1]["cwd"] == tmp_path
    assert recorded[0][1]["start_new_session"] is True
    assert recorded[0][1]["env"]["MARESTAIL_LIMIT_WAITS"] == "36"


def test_last_stderr_line_empty() -> None:
    assert queue._last_stderr_line("") == ""


def test_build_child_env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MARESTAIL_LIMIT_WAITS", raising=False)
    env = queue._build_child_env()
    assert env["MARESTAIL_LIMIT_WAITS"] == "36"


def test_push_and_pr_failures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log_path = tmp_path / "queue.log"
    fail_proc = subprocess.CompletedProcess([], 1, "", "push rejected\n")
    orig_run = queue._git_run

    monkeypatch.setattr(queue, "_git_run", lambda args, root: fail_proc if args[0] == "push" else orig_run(args, root))
    queue._push_branch("a", "marestail/a", "origin", tmp_path, log_path)
    assert "push-failed a marestail/a: push rejected" in log_path.read_text()

    monkeypatch.setattr(
        queue,
        "_run_gh",
        lambda args, root, env=None: (
            subprocess.CompletedProcess([], 0, "1\n", "")
            if args[1] == "list"
            else subprocess.CompletedProcess([], 1, "", "pr create error\n")
        ),
    )
    queue._maybe_open_pr(TaskFile("a", Path("tasks/a.md"), (), None, "", True), "marestail/a", "origin/main", [], {}, tmp_path, log_path)
    monkeypatch.setattr(
        queue,
        "_run_gh",
        lambda args, root, env=None: (
            subprocess.CompletedProcess([], 0, "0\n", "")
            if args[1] == "list"
            else subprocess.CompletedProcess([], 1, "", "pr create error\n")
        ),
    )
    queue._maybe_open_pr(TaskFile("a", Path("tasks/a.md"), (), None, "", True), "marestail/a", "origin/main", [], {}, tmp_path, log_path)
    assert "pr-failed a marestail/a: pr create error" in log_path.read_text()


def test_task_title_fallback() -> None:
    t = TaskFile("fallback-id", Path("tasks/fallback.md"), (), None, "some description without h1", True)
    assert queue._task_title(t) == "fallback-id"


def test_after_child_finish_push_only_no_pr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log_path = tmp_path / "queue.log"
    pushed: list[tuple[str, str]] = []
    monkeypatch.setattr(queue, "_push_branch", lambda task_id, branch, remote, root, log_path: pushed.append((task_id, branch)))
    queue._after_child_finish(
        TaskFile("a", Path("tasks/a.md"), (), None, "", True),
        "marestail/a",
        "done",
        True,
        False,
        "origin",
        "origin/main",
        [],
        {},
        tmp_path,
        log_path,
    )
    assert pushed == [("a", "marestail/a")]


def _sigint_after_done(state: dict[str, Any]) -> None:
    entry = state.get("tasks", {}).get("a", {})
    if entry.get("status") == "done":
        signal.raise_signal(signal.SIGINT)


def _arm_sigint_queue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, list[list[str]]]:
    repo, _ = setup_bare_repo(tmp_path)
    monkeypatch.chdir(repo)
    write_task_file(repo / "tasks" / "a.md", "a", [])
    write_task_file(repo / "tasks" / "b.md", "b", [])
    commit_all(repo, "add tasks")
    git(repo, "push", "-q", "origin", "main")
    monkeypatch.setenv("MARESTAIL_QUEUE_PUSH", "1")
    fake_start, recorded = make_child_seam(repo, {})
    monkeypatch.setattr(queue, "_start_child", fake_start)
    real_write = queue._write_state

    def _write(path: Path, state: dict[str, Any]) -> None:
        real_write(path, state)
        _sigint_after_done(state)

    monkeypatch.setattr(queue, "_write_state", _write)
    return repo, recorded


def _assert_sigint_stop(repo: Path, recorded: list[list[str]], code: object) -> None:
    assert code == 130
    assert len(recorded) == 1
    assert Path(recorded[0][4]).name == "a.md"
    assert git(repo, "symbolic-ref", "--quiet", "--short", "HEAD").strip() == "marestail/a"
    assert not (repo / ".marestail" / "queue" / "lock").exists()
    tasks = _queue_tasks(repo)
    assert tasks["a"]["status"] == "done"
    assert "b" not in tasks
    remote = subprocess.run(["git", "ls-remote", "--heads", "origin", "marestail/a"], cwd=repo, capture_output=True, text=True, check=False)
    assert remote.stdout.strip() == ""


def test_sigint_between_tasks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, recorded = _arm_sigint_queue(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as raised:
        queue.run_queue([], None, None, False, [], None, None, None, None, [])
    _assert_sigint_stop(repo, recorded, raised.value.code)


def test_detached_head_is_reported_before_bad_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, _ = setup_bare_repo(tmp_path)
    monkeypatch.chdir(repo)
    write_task_file(repo / "tasks" / "a.md", "a", [])
    commit_all(repo, "add a")
    state = repo / ".marestail" / "queue" / "state.json"
    state.parent.mkdir(parents=True)
    state.write_text('{"v": 2}')
    git(repo, "checkout", "--detach", "HEAD")
    assert cli.main(["queue"]) == 2
    seen = capsys.readouterr()
    assert seen.out == ""
    assert seen.err == "HEAD is detached; check out a branch first\n"
    assert state.read_text() == '{"v": 2}'


def test_running_run_is_reported_before_bad_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, _ = setup_bare_repo(tmp_path)
    monkeypatch.chdir(repo)
    try:
        _setup_running_run(repo, monkeypatch, tmp_path)
        state = repo / ".marestail" / "queue" / "state.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text('{"v": 2}')
        assert cli.main(["queue"]) == 2
        seen = capsys.readouterr()
        pid = _LIVE_RUNS[0].pid
        assert seen.out == ""
        assert seen.err == f"a marestail run is running here (011-live, pid {pid}); the queue would change the tree under it\n"
        assert state.read_text() == '{"v": 2}'
    finally:
        _stop_live_runs()


def test_roles_are_checked_with_the_pipeline(capsys: pytest.CaptureFixture[str]) -> None:
    assert queue._accept_roles(None, "hardener", None) == 0
    assert capsys.readouterr().err == ""
    assert queue._accept_roles("nope", None, None) == 2
    plain = "unknown role nope; choose from specifier, critic, coder, cleaner, architect, design, practices, perf, hardener, qa\n"
    assert capsys.readouterr().err == plain
    assert queue._accept_roles(None, "cleaner", "hyper") == 2
    hyper = "unknown role cleaner; choose from specifier, critic, coder, architect, blast, hardener, qa\n"
    assert capsys.readouterr().err == hyper
    assert queue._accept_roles("coder", "qa", "hyper") == 0


def test_queue_default_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    repo, _bare = setup_bare_repo(tmp_path)
    monkeypatch.chdir(repo)
    (repo / "tasks").mkdir()
    (repo / "tasks" / "bad.md").write_text("# bad\n")
    (repo / "other").mkdir()
    (repo / "other" / "bad.md").write_text("# bad\n")
    code = cli.main(["queue"])
    assert code == 2
    seen = capsys.readouterr()
    assert seen.out == "tasks/bad.md: no front matter: line 1 must be +++\n"
    assert "other/bad.md" not in seen.out
    assert seen.err == ""


@pytest.mark.parametrize(
    "result,code,role,reason,stopped_at,status",
    [
        ("complete", 0, "coder", None, None, "done"),
        ("failed", 1, "coder", "tests failed", None, "failed"),
        ("stopped", 1, "critic", "not approved", "critic", "stopped"),
    ],
)
def test_push_follows_a_task_that_ran(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    result: str,
    code: int,
    role: str,
    reason: str | None,
    stopped_at: str | None,
    status: str,
) -> None:
    repo, bare = setup_bare_repo(tmp_path)
    monkeypatch.chdir(repo)
    write_task_file(repo / "tasks" / "a.md", "a", [])
    write_task_file(repo / "tasks" / "b.md", "b", ["a"], stack=False)
    commit_all(repo, "tasks")
    git(repo, "push", "-q", "origin", "main")
    monkeypatch.setenv("MARESTAIL_QUEUE_PUSH", "1")
    monkeypatch.setenv("MARESTAIL_QUEUE_PR", "1")
    _gh_on_path(monkeypatch)
    gh_calls: list[list[str]] = []

    def _record_gh(args: list[str], _root: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        gh_calls.append(args)
        return subprocess.CompletedProcess(args, 0, "0\n", "")

    monkeypatch.setattr(queue, "_run_gh", _record_gh)
    outcomes = {"a": (result, code, role, reason, stopped_at)}
    fake_start, _recorded = make_child_seam(repo, outcomes)
    monkeypatch.setattr(queue, "_start_child", fake_start)
    exit_code = queue.run_queue(["tasks/a.md", "tasks/b.md"], None, None, False, [], None, None, None, None, [])
    assert exit_code == 1
    local = git(repo, "rev-parse", "marestail/a").strip()
    assert git(bare, "rev-parse", "marestail/a").strip() == local
    state = json.loads((repo / ".marestail" / "queue" / "state.json").read_text())["tasks"]
    assert state["a"]["status"] == status
    assert state["a"]["head"] == local
    assert state["b"]["status"] == "blocked"
    missing = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", "marestail/b"], cwd=bare, capture_output=True, text=True, check=False
    )
    assert missing.returncode != 0
    assert gh_calls == [] if status != "done" else gh_calls != []


def test_push_off_does_not_push(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, bare = setup_bare_repo(tmp_path)
    monkeypatch.chdir(repo)
    (repo / "marestail.toml").write_text('[git]\nbase = "origin/main"\n[queue]\npush = true\n')
    write_task_file(repo / "tasks" / "a.md", "a", [])
    commit_all(repo, "task")
    git(repo, "push", "-q", "origin", "main")
    monkeypatch.setenv("MARESTAIL_QUEUE_PUSH", "0")
    fake_start, _recorded = make_child_seam(repo, {})
    monkeypatch.setattr(queue, "_start_child", fake_start)
    assert queue.run_queue(["tasks/a.md"], None, None, False, [], None, None, None, None, []) == 0
    missing = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", "marestail/a"], cwd=bare, capture_output=True, text=True, check=False
    )
    assert missing.returncode != 0


def test_empty_push_variable_beats_the_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    repo, _bare = setup_bare_repo(tmp_path)
    monkeypatch.chdir(repo)
    (repo / "marestail.toml").write_text('[git]\nbase = "origin/main"\n[queue]\npush = true\npr = true\n')
    write_task_file(repo / "tasks" / "a.md", "a", [])
    commit_all(repo, "task")
    git(repo, "push", "-q", "origin", "main")
    monkeypatch.setenv("MARESTAIL_QUEUE_PUSH", "")
    assert cli.main(["queue"]) == 2
    assert capsys.readouterr() == ("", "[queue] pr needs [queue] push\n")


def test_pr_env_beats_a_false_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, _bare = setup_bare_repo(tmp_path)
    monkeypatch.chdir(repo)
    (repo / "marestail.toml").write_text('[git]\nbase = "origin/main"\n[queue]\npush = true\npr = false\n')
    write_task_file(repo / "tasks" / "a.md", "a", [], title="Task A")
    commit_all(repo, "task")
    git(repo, "push", "-q", "origin", "main")
    monkeypatch.setenv("MARESTAIL_QUEUE_PR", "true")
    _gh_on_path(monkeypatch)
    created: list[list[str]] = []

    def _mock_gh(args: list[str], root: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        if args[1] == "list":
            return subprocess.CompletedProcess(args, 0, "0\n", "")
        created.append(args)
        return subprocess.CompletedProcess(args, 0, "https://example\n", "")

    monkeypatch.setattr(queue, "_run_gh", _mock_gh)
    fake_start, _recorded = make_child_seam(repo, {})
    monkeypatch.setattr(queue, "_start_child", fake_start)
    assert queue.run_queue(["tasks/a.md"], None, None, False, [], None, None, None, None, []) == 0
    assert created
    assert created[0][created[0].index("--title") + 1] == "Task A"


def test_failed_push_keeps_the_done_exit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, _bare = setup_bare_repo(tmp_path)
    monkeypatch.chdir(repo)
    write_task_file(repo / "tasks" / "a.md", "a", [])
    commit_all(repo, "task")
    git(repo, "push", "-q", "origin", "main")
    monkeypatch.setenv("MARESTAIL_QUEUE_PUSH", "1")
    fake_start, _recorded = make_child_seam(repo, {})
    monkeypatch.setattr(queue, "_start_child", fake_start)
    original = queue._git_run

    def _git(args: list[str], root: Path) -> subprocess.CompletedProcess[str]:
        if args[0] == "push":
            return subprocess.CompletedProcess(args, 1, "", "and the repository exists.\n")
        return original(args, root)

    monkeypatch.setattr(queue, "_git_run", _git)
    assert queue.run_queue(["tasks/a.md"], None, None, False, [], None, None, None, None, []) == 0
    state = json.loads((repo / ".marestail" / "queue" / "state.json").read_text())["tasks"]["a"]
    assert state["status"] == "done"
    assert "push-failed a marestail/a: and the repository exists." in (repo / ".marestail" / "queue" / "queue.log").read_text()


def test_task_with_no_heading_uses_id_as_title(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def _mock_gh(args: list[str], root: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        text = "0\n" if args[1] == "list" else "https://example\n"
        return subprocess.CompletedProcess(args, 0, text, "")

    monkeypatch.setattr(queue, "_run_gh", _mock_gh)
    task = TaskFile("fallback-id", tmp_path / "fallback-id.md", (), None, "no heading here\n", True)
    state = {"tasks": {"fallback-id": {"status": "done", "branch": "marestail/fallback-id"}}}
    queue._maybe_open_pr(task, "marestail/fallback-id", "origin/main", [task], state, tmp_path, tmp_path / "queue.log")
    create = next(args for args in calls if args[1] == "create")
    assert create[create.index("--title") + 1] == "fallback-id"


def test_queue_sits_under_the_cli_and_reads_the_role_list() -> None:
    contracts = Path(".importlinter").read_text()
    after_cli = contracts.split("    marestail.cli\n", 1)[1]
    assert after_cli.startswith("    marestail.queue\n")
    pipeline = contracts.split("[importlinter:contract:pipeline-belongs-to-orchestration]", 1)[1].split("\n[", 1)[0]
    assert "name = The role list and how a scope reshapes it are known only to the runner, the prompt builder and the queue" in pipeline
    allowed = pipeline.split("allowed_importers =", 1)[1]
    assert "marestail.runner" in allowed
    assert "marestail.prompts" in allowed
    assert "marestail.queue" in allowed
    ban = contracts.split("[importlinter:contract:queue-does-not-import-the-runner]", 1)[1].split("\n[", 1)[0]
    assert "marestail.queue" in ban.split("forbidden_modules", 1)[0]
    assert "marestail.runner" in ban.split("forbidden_modules", 1)[1]
    source = Path("marestail/queue.py").read_text()
    assert "from marestail.pipeline import HYPER, unknown_role, window" in source
    assert "marestail.runner" not in source
    assert "def role_window" not in Path("marestail/runner.py").read_text()


def test_try_create_lock_creates_then_handles_live_and_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    lock = tmp_path / "lock"
    assert queue._try_create_lock(lock) == (0, True)
    assert lock.read_text() == f"{os.getpid()}\n"
    lock.write_text("999999\n")
    assert queue._try_create_lock(lock) == (0, True)
    assert lock.read_text() == f"{os.getpid()}\n"
    lock.write_text("12345\n")
    orig_exists = os.path.exists
    monkeypatch.setattr(os.path, "exists", lambda p: True if p == "/proc/12345" else orig_exists(p))
    assert queue._try_create_lock(lock) == (2, False)
    assert lock.read_text() == "12345\n"
    assert capsys.readouterr().err == "another marestail queue (pid 12345) is running in this repository\n"


def test_is_missing_dep_problem() -> None:
    assert queue._is_missing_dep_problem("tasks/e.md: depends on zzz, but zzz does not exist") is True
    assert queue._is_missing_dep_problem("tasks/e.md: depends on zzz") is False
    assert queue._is_missing_dep_problem("tasks/e.md: zzz does not exist") is False


def test_failed_flight_returns_an_empty_bundle() -> None:
    assert queue._failed_flight(2) == (2, "", False, False, "", "", {})


def test_parse_state_file_outside_the_root(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    state_file = tmp_path / "state.json"
    state_file.write_text("{")
    code, data = queue._parse_state_file(state_file, tmp_path / "root")
    assert code == 2
    assert data == {}
    assert capsys.readouterr().err.startswith(f"{state_file}: not valid JSON: ")


def test_state_task_status() -> None:
    assert queue._state_task_status("a", {"tasks": {"a": {"status": "done"}}}) == "done"
    assert queue._state_task_status("a", {"tasks": {"a": {"status": None}}}) == "never-started"
    assert queue._state_task_status("a", {"tasks": {"a": {}}}) == "never-started"
    assert queue._state_task_status("a", {"tasks": {}}) is None
    assert queue._state_task_status("a", {}) is None


def test_reader_task_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, _ = setup_bare_repo(tmp_path)
    assert queue._reader_task_status(repo, "zzz") == "never-started"
    monkeypatch.setattr(status_module, "read_run", lambda _r, _t: {"result": None, "state": "dead"})
    assert queue._reader_task_status(repo, "a") == "dead"
    monkeypatch.setattr(status_module, "read_run", lambda _r, _t: {"result": None, "state": None})
    assert queue._reader_task_status(repo, "a") == "never-started"
    monkeypatch.setattr(status_module, "read_run", lambda _r, _t: {"result": "stopped"})
    assert queue._reader_task_status(repo, "a") == "stopped"


def test_stored_status_falls_back_to_the_reader(tmp_path: Path) -> None:
    repo, _ = setup_bare_repo(tmp_path)
    append_events(repo, "a", 424242, "failed", 1, reason="boom")
    assert queue._stored_status("a", {}, repo) == "failed"
    assert queue._stored_status("a", {"tasks": {"a": {"status": "blocked"}}}, repo) == "blocked"


def test_classify_rec_status() -> None:
    assert queue._classify_rec_status({"result": "complete"}) == "done"
    assert queue._classify_rec_status({"result": None, "state": "complete"}) == "done"
    assert queue._classify_rec_status({"result": "stopped"}) == "stopped"
    assert queue._classify_rec_status({"result": "failed"}) == "failed"
    assert queue._classify_rec_status({"result": None, "state": "dead"}) is None


def test_retry_validation_uses_the_reader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, _ = setup_bare_repo(tmp_path)
    monkeypatch.chdir(repo)
    write_task_file(repo / "tasks" / "a.md", "a", [])
    commit_all(repo, "add a")
    git(repo, "push", "-q", "origin", "main")
    append_events(repo, "a", 424242, "failed", 1, role="critic", reason="boom", branch="marestail/a")
    fake_start, recorded = make_child_seam(repo, {})
    monkeypatch.setattr(queue, "_start_child", fake_start)
    code = queue.run_queue(["tasks/a.md"], None, None, False, ["a"], None, None, None, None, [])
    assert code == 0
    assert len(recorded) == 1
    assert recorded[0][recorded[0].index("--from") + 1] == "critic"


def test_dep_blocked_reason(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(status_module, "read_run", lambda _r, _t: {"result": None, "state": "never-started"})
    assert queue._dep_blocked_reason("z", {}, tmp_path) == "z is not in this queue and has not completed"
    assert queue._dep_blocked_reason("z", {"tasks": {"z": {"status": "failed"}}}, tmp_path) == "z failed"
    assert queue._dep_blocked_reason("z", {"tasks": {"z": {"status": "stopped"}}}, tmp_path) == "z stopped"
    assert queue._dep_blocked_reason("z", {"tasks": {"z": {"status": "blocked", "reason": "w"}}}, tmp_path) == "w"
    assert queue._dep_blocked_reason("z", {"tasks": {"z": {"status": "done"}}}, tmp_path) is None


def test_blocked_dep_reason() -> None:
    assert queue._blocked_dep_reason("blocked", {"reason": "why"}) == "why"
    assert queue._blocked_dep_reason("blocked", {"reason": None}) == ""
    assert queue._blocked_dep_reason("blocked", {}) == ""
    assert queue._blocked_dep_reason("done", {"reason": "why"}) is None


def test_external_dep_reason_records_a_complete_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rec = {"result": "complete", "state": "running", "events": "e", "branch": "marestail/z", "head": "abc123"}
    monkeypatch.setattr(status_module, "read_run", lambda _r, _t: rec)
    state: dict[str, Any] = {}
    assert queue._external_dep_reason("z", state, tmp_path) is None
    entry = state["tasks"]["z"]
    assert entry["status"] == "done"
    assert entry["branch"] == "marestail/z"
    assert entry["head"] == "abc123"
    assert entry["exit_code"] == 0
    assert entry["reason"] is None


def test_external_dep_reason_records_a_legacy_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rec = {"result": None, "state": "complete", "events": None, "branch": "main", "head": "abc999"}
    monkeypatch.setattr(status_module, "read_run", lambda _r, _t: rec)
    state: dict[str, Any] = {}
    assert queue._external_dep_reason("z", state, tmp_path) is None
    entry = state["tasks"]["z"]
    assert entry["status"] == "done"
    assert entry["branch"] is None
    assert entry["head"] is None


def test_external_dep_reason_for_an_incomplete_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(status_module, "read_run", lambda _r, _t: {"result": "failed", "state": "stopped"})
    state: dict[str, Any] = {}
    assert queue._external_dep_reason("z", state, tmp_path) == "z is not in this queue and has not completed"
    assert state == {}


def test_external_dep_blocked_with_the_real_reader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, _ = setup_bare_repo(tmp_path)
    monkeypatch.chdir(repo)
    write_task_file(repo / "tasks" / "e.md", "e", ["zzz"], stack=True)
    commit_all(repo, "add e")
    git(repo, "push", "-q", "origin", "main")
    fake_start, recorded = make_child_seam(repo, {})
    monkeypatch.setattr(queue, "_start_child", fake_start)
    code = queue.run_queue(["tasks/e.md"], None, None, False, [], None, None, None, None, [])
    assert code == 1
    assert recorded == []
    entry = json.loads((repo / ".marestail" / "queue" / "state.json").read_text())["tasks"]["e"]
    assert entry["reason"] == "zzz is not in this queue and has not completed"


def test_was_uncommitted_stop() -> None:
    assert queue._was_uncommitted_stop({}, "a") is True
    assert queue._was_uncommitted_stop({"tasks": {}}, "a") is True
    assert queue._was_uncommitted_stop({"tasks": {"a": {}}}, "a") is False


def test_already_sticky() -> None:
    assert queue._already_sticky({}, "a", "failed") is False
    assert queue._already_sticky({"tasks": {"a": {"status": "failed"}}}, "a", "failed") is True
    assert queue._already_sticky({"tasks": {"a": {"status": "done"}}}, "a", "failed") is False
    assert queue._already_sticky({"tasks": {"a": "junk"}}, "a", "failed") is False


def test_keep_sticky(tmp_path: Path) -> None:
    log = tmp_path / "queue.log"
    state_file = tmp_path / "state.json"
    state: dict[str, Any] = {"tasks": {"a": {"status": "failed", "reason": "boom"}}}
    queue._keep_sticky("a", "failed", "boom", state, state_file, log)
    assert "failed a boom\n" in log.read_text()
    assert not state_file.exists()
    assert state == {"tasks": {"a": {"status": "failed", "reason": "boom"}}}
    queue._keep_sticky("b", "stopped", "halt", state, state_file, log)
    assert state["tasks"]["b"]["status"] == "stopped"
    assert state["tasks"]["b"]["reason"] == "halt"
    assert "stopped b halt\n" in log.read_text()


def test_terminate_child_logs_the_resume_role(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, _ = setup_bare_repo(tmp_path)
    monkeypatch.chdir(repo)
    append_events(repo, "a", 424242, "interrupted", 1, role="hardener", branch="marestail/a", omit_end=True)
    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(os, "killpg", lambda pid, sig: killed.append((pid, sig)))
    log = repo / ".marestail" / "queue" / "queue.log"
    queue._terminate_child(FakeChild(424242), "a", repo, log, 15)
    queue._terminate_child(FakeChild(424243), "zzz", repo, log, 2)
    text = log.read_text()
    assert "interrupted a by signal 15; the next run resumes from hardener\n" in text
    assert "interrupted zzz by signal 2; the next run resumes from coder\n" in text
    assert killed == [(424242, signal.SIGTERM), (424243, signal.SIGTERM)]


def test_kill_child_process_suppresses_a_gone_group(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def _killpg(_pid: int, sig: int) -> None:
        calls.append(sig)
        if sig == signal.SIGKILL:
            raise ProcessLookupError("gone")

    monkeypatch.setattr(os, "killpg", _killpg)
    queue._kill_child_process(FakeChild(98765, exit_code=0, timeout_times=1))
    assert calls == [signal.SIGTERM, signal.SIGKILL]


def test_git_short_reads_the_given_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, _ = setup_bare_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert queue._git_short("HEAD", repo) == git(repo, "rev-parse", "--short", "HEAD").strip()


def test_push_branch_pushes_and_logs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    repo, bare = setup_bare_repo(tmp_path)
    git(repo, "switch", "-q", "-c", "marestail/a")
    (repo / "x.txt").write_text("x\n")
    commit_all(repo, "x")
    monkeypatch.chdir(tmp_path)
    log = tmp_path / "queue.log"
    queue._push_branch("a", "marestail/a", "origin", repo, log)
    capsys.readouterr()
    assert git(bare, "rev-parse", "marestail/a").strip() == git(repo, "rev-parse", "HEAD").strip()
    assert "pushed a origin marestail/a\n" in log.read_text()


def test_maybe_open_pr_exact_gh_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], Path, dict[str, str] | None]] = []

    def _fake_gh(args: list[str], root: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        calls.append((list(args), root, env))
        text = "0\n" if args[1] == "list" else "https://example/1\n"
        return subprocess.CompletedProcess(args, 0, text, "")

    monkeypatch.setattr(queue, "_run_gh", _fake_gh)
    task = TaskFile("a", Path("tasks/a.md"), (), None, "# Task A\n", True)
    state: dict[str, Any] = {"tasks": {"a": {"status": "done", "branch": "marestail/a"}}}
    log = tmp_path / "queue.log"
    queue._maybe_open_pr(task, "marestail/a", "origin/release/1", [task], state, tmp_path, log)
    queue._maybe_open_pr(task, "marestail/a", "main", [task], state, tmp_path, log)
    assert calls[0] == (
        ["pr", "list", "--head", "marestail/a", "--state", "open", "--json", "number", "--jq", "length"],
        tmp_path,
        None,
    )
    assert calls[1][0] == [
        "pr",
        "create",
        "--base",
        "release/1",
        "--head",
        "marestail/a",
        "--title",
        "Task A",
        "--body-file",
        ".marestail/queue/pr-a.md",
    ]
    assert calls[1][1] == tmp_path
    assert calls[1][2] == {"GH_PROMPT_DISABLED": "1"}
    assert calls[3][0][calls[3][0].index("--base") + 1] == "main"


def test_format_pr_body_joins_every_task_on_the_branch() -> None:
    a = TaskFile("a", Path("tasks/a.md"), (), None, "# Task A\n", True)
    b = TaskFile("b", Path("tasks/b.md"), (), None, "# Task B\n", True)
    state = {"tasks": {"a": {"status": "done", "branch": "marestail/a"}, "b": {"status": "done", "branch": "marestail/a"}}}
    assert queue._format_pr_body("b", "marestail/a", [a, b], state) == (
        "`marestail queue` opened this pull request after task `b` finished on `marestail/a`.\n\n"
        "Tasks done on this branch:\n"
        "- a: Task A\n"
        "- b: Task B\n\n"
        "Each commit is stamped with the role and model that made it.\n"
    )


def test_tasks_on_branch_filters_by_branch_and_status() -> None:
    a = TaskFile("a", Path("tasks/a.md"), (), None, "", True)
    b = TaskFile("b", Path("tasks/b.md"), (), None, "", True)
    state = {"tasks": {"a": {"status": "done", "branch": "marestail/a"}, "b": {"status": "failed", "branch": "marestail/a"}}}
    assert queue._tasks_on_branch("marestail/a", [a, b], state) == [a]
    assert queue._tasks_on_branch("marestail/b", [a, b], state) == []
    assert queue._tasks_on_branch("marestail/a", [a], {}) == []


def test_commit_task_copy_creates_parents_and_commits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, _ = setup_bare_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    task = TaskFile("deep", repo / "tasks" / "deep" / "deep.md", (), None, "", True)
    queue._commit_task_copy(task, repo, "+++\ndepends = []\n+++\n# Deep\n")
    assert (repo / "tasks" / "deep" / "deep.md").read_text() == "+++\ndepends = []\n+++\n# Deep\n"
    assert git(repo, "log", "-1", "--format=%s").strip() == "queue: task deep"
    assert git(repo, "status", "--porcelain").strip() == ""


def test_accept_roles_passes_hyper_through_to_the_window(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str | None, str | None, str | None]] = []

    def spy(start: str | None, stop: str | None, mode: str | None = None, visual: bool = False) -> list[Step]:
        calls.append((start, stop, mode))
        return pipeline_window(start, stop, mode, visual)

    monkeypatch.setattr(queue, "window", spy)
    assert queue._accept_roles("blast", "qa", "hyper") == 0
    assert calls == [("blast", "qa", "hyper")]


def test_branch_exists_locally_asks_git_in_that_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, _ = setup_bare_repo(tmp_path)
    git(repo, "switch", "-q", "-c", "marestail/a")
    git(repo, "switch", "-q", "main")
    monkeypatch.chdir(tmp_path)
    calls: list[tuple[list[str], Path | None]] = []
    real = queue._git_run

    def spy(args: list[str], root: Any) -> subprocess.CompletedProcess[str]:
        calls.append((list(args), root))
        return real(args, root)

    monkeypatch.setattr(queue, "_git_run", spy)
    assert queue._branch_exists_locally("marestail/a", repo) is True
    assert calls == [(["rev-parse", "--verify", "--quiet", "refs/heads/marestail/a"], repo)]
    assert queue._branch_exists_locally("marestail/missing", repo) is False


def test_git_head_reads_the_given_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, _ = setup_bare_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert queue._git_head(repo) == git(repo, "rev-parse", "HEAD").strip()
