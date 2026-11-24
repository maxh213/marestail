import hashlib
import io
import json
import os
import re
import shlex
import subprocess
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from marestail import remote
from marestail.gates import py_mutation
from tests.conftest import FakeRun, Reply, make_context

TOML = """[remote]
enabled = true
instance = "marestail-mutation"
project = "proj"
zone = "europe-west1-b"
gates = ["py.mutation"]
workers = { "py.mutation" = 8 }
usd_per_hour = 1.2
user = "max"
container = "mt"
"""
INSTANCE = "marestail-mutation"
PROJECT = "proj"
IP = "1.2.3.4"
DESCRIBE = ["compute", "instances", "describe", INSTANCE, "--format=json"]
START = ["compute", "instances", "start", INSTANCE]
RESUME = ["compute", "instances", "resume", INSTANCE]
PROBE = "sg docker -c 'docker exec mt true'"
JOB_LOCK = "flock /var/lib/marestail/job.lock"
JOB = "/var/lib/marestail/busy.d/"
GUARDED = {remote.GUARD: "1"}
STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00$")
JOB_KEYS = ["ts", "event", "instance", "gate", "repo", "result", "seconds", "usd_per_hour", "usd"]
FIXED_NOW = datetime(2026, 10, 10, 12, 34, 59, 500000, tzinfo=UTC)
ALL_EXCLUDES = [
    "--exclude=/.git/",
    "--exclude=.stryker-tmp/",
    "--exclude=.elixir_ls/",
    "--exclude=node_modules/",
    "--exclude=.next/",
    "--exclude=_build/",
    "--exclude=.venv/",
    "--exclude=cover/",
    "--exclude=erl_crash.dump",
    "--exclude=target/",
    "--include=/.marestail/",
    "--include=/.marestail/tooling/***",
    "--include=/.marestail/stryker/***",
    "--exclude=/.marestail/*",
]
PY_EXCLUDES = [pattern for pattern in ALL_EXCLUDES if pattern != "--exclude=.venv/"]
META = {"pkg.a.f__mutmut_1": 1, "pkg.a.f__mutmut_2": 36}


def box_settings(**changes: Any) -> remote.Settings:
    fields: dict[str, Any] = {
        "instance": INSTANCE,
        "project": "proj",
        "zone": "europe-west1-b",
        "container": "mt",
        "gates": ("py.mutation",),
        "workers": {"py.mutation": 8},
        "env": {},
        "usd_per_hour": 1.2,
        "user": "max",
        "key": Path("/keys/id"),
    }
    return remote.Settings(**{**fields, **changes})


class FakeTime:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now
        self.slept: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz: Any = None) -> "FixedDatetime":
        return cls.fromtimestamp(FIXED_NOW.timestamp(), tz)


class FakeBox:
    def __init__(self, clock: FakeTime) -> None:
        self.clock = clock
        self.statuses: list[str | None] = ["RUNNING"]
        self.start: Reply = (0, "")
        self.starts: list[Reply] = []
        self.probe: Reply = (0, "")
        self.jobs: list[Reply | None] = [(0, "done")]
        self.job_seconds = 0.0
        self.sync: Reply = (0, "")
        self.on_fetch: Callable[[list[str]], None] = lambda _args: None
        self.gcloud_calls: list[list[str]] = []
        self.ssh_calls: list[tuple[str, str, int | None, bool]] = []
        self.rsync_calls: list[list[str]] = []

    def gcloud(self, found: remote.Settings, args: list[str]) -> Reply:
        assert found.instance == INSTANCE
        self.gcloud_calls.append(args)
        if args[2] != "describe":
            return self.starts.pop(0) if self.starts else self.start
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        return described(status)

    def ssh(self, found: remote.Settings, ip: str, command: str, timeout: int | None = None, tty: bool = False) -> Reply:
        same_box(found)
        self.ssh_calls.append((ip, command, timeout, tty))
        if JOB in command:
            self.clock.now += self.job_seconds
            return finished(self.jobs.pop(0))
        return self.probe if command == PROBE else (0, "")

    def rsync(self, found: remote.Settings, args: list[str]) -> Reply:
        same_box(found)
        self.rsync_calls.append(args)
        self.on_fetch(args)
        return self.sync if args[-1].endswith("/repo/") else (0, "")

    def job_commands(self) -> list[str]:
        return [command for _ip, command, _timeout, _tty in self.ssh_calls if JOB in command]


def finished(reply: Reply | None) -> Reply:
    if reply is None:
        return 255, ""
    code, output = reply
    return code, f"{output}\nmarestail-exit {code}\n"


def same_box(found: remote.Settings) -> None:
    assert found.instance == INSTANCE


def described(status: str | None) -> Reply:
    if status is None:
        return 1, "ERROR: (gcloud) not found"
    return 0, json.dumps({"status": status, "networkInterfaces": [{"accessConfigs": [{"natIP": IP}]}]})


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "home"
    folder.mkdir()
    config = folder / ".config" / "marestail"
    monkeypatch.setenv("HOME", str(folder))
    monkeypatch.setattr(remote, "CONFIG", config / "remote.toml")
    monkeypatch.setattr(remote, "USAGE", config / "remote-usage.jsonl")
    monkeypatch.setattr(remote, "LOCK", config / "remote.lock")
    monkeypatch.delenv(remote.GATES, raising=False)
    return folder


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeTime:
    fake = FakeTime()
    monkeypatch.setattr(remote, "time", fake)
    return fake


@pytest.fixture
def box(monkeypatch: pytest.MonkeyPatch, clock: FakeTime) -> FakeBox:
    monkeypatch.delenv(remote.ENV, raising=False)
    configure()
    fake = FakeBox(clock)
    monkeypatch.setattr(remote, "gcloud", fake.gcloud)
    monkeypatch.setattr(remote, "ssh", fake.ssh)
    monkeypatch.setattr(remote, "rsync", fake.rsync)
    return fake


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "a.py").write_text("def f():\n    return 1\n")
    return root


@pytest.fixture
def mutmut(repo: Path, monkeypatch: pytest.MonkeyPatch) -> FakeRun:
    def runs(_command: list[str]) -> Reply:
        write_meta(repo / "mutants")
        return 0, "local mutmut"

    fake = FakeRun(runs)
    monkeypatch.setattr(remote, "run", fake)
    return fake


def configure(text: str = TOML) -> None:
    remote.CONFIG.parent.mkdir(parents=True, exist_ok=True)
    remote.CONFIG.write_text(text)


def write_meta(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "pkg.a.meta").write_text(json.dumps({"exit_code_by_key": META}))


def build(workers: int) -> list[str]:
    return ["mutmut", "run", "--max-children", str(workers)]


def offload(repo: Path) -> remote.Outcome:
    ctx = make_context(repo)
    return remote.run_job(ctx, "py.mutation", build, repo, 4, env={"A": "1"}, timeout=7200, pull=("mutants",))


def usage_lines() -> list[dict[str, Any]]:
    return [json.loads(line) for line in remote.USAGE.read_text().splitlines()]


def job_line(repo: Path, result: str, seconds: int = 0, usd: float = 0.0) -> dict[str, Any]:
    return {
        "event": "job",
        "instance": INSTANCE,
        "gate": "py.mutation",
        "repo": str(repo),
        "result": result,
        "seconds": seconds,
        "usd_per_hour": 1.2,
        "usd": usd,
    }


def without_stamp(entry: dict[str, Any]) -> dict[str, Any]:
    assert STAMP.match(entry["ts"])
    return {key: value for key, value in entry.items() if key != "ts"}


def local_call(repo: Path) -> list[dict[str, Any]]:
    return [{"cwd": repo, "env": {"A": "1", **GUARDED}, "timeout": 7200}]


def gate_ctx(repo: Path) -> Any:
    return make_context(repo, {"python": {"mutation_scope": "all"}})


def patience(hours: float) -> None:
    configure(TOML + f"max_wait_hours = {hours}\n")


def test_a_box_that_will_not_start_is_waited_for_and_never_run_locally(
    box: FakeBox, mutmut: FakeRun, repo: Path, clock: FakeTime, capsys: pytest.CaptureFixture[str]
) -> None:
    patience(0.05)
    box.statuses = ["TERMINATED"]
    box.start = (1, "WARNING: retrying\nZONE_RESOURCE_POOL_EXHAUSTED\n")
    unavailable = f"{INSTANCE} is unavailable: ZONE_RESOURCE_POOL_EXHAUSTED"
    assert offload(repo) == remote.Outcome(75, "", f" (gave up after waiting 3m: {unavailable}; not run locally)")
    assert clock.slept == [30, 60, 90]
    assert mutmut.calls == []
    assert box.gcloud_calls == [DESCRIBE, DESCRIBE, START] * 4
    assert box.ssh_calls == []
    assert not remote.USAGE.exists()
    assert capsys.readouterr().err.splitlines() == [
        f"marestail: {unavailable}; trying again in 30s (waited 0s of 3m)",
        f"marestail: {unavailable}; trying again in 1m (waited 30s of 3m)",
        f"marestail: {unavailable}; trying again in 1m30s (waited 1m30s of 3m)",
    ]


def test_a_stockout_that_clears_runs_on_the_box(box: FakeBox, mutmut: FakeRun, repo: Path, clock: FakeTime) -> None:
    box.statuses = ["TERMINATED"] * 4 + ["RUNNING"]
    box.starts = [(1, "ZONE_RESOURCE_POOL_EXHAUSTED"), (0, "")]
    assert offload(repo) == remote.Outcome(0, "done", f" (on {INSTANCE}, 0s, after waiting 30s for the box)")
    assert clock.slept == [30]
    assert mutmut.calls == []
    assert box.gcloud_calls == [DESCRIBE, DESCRIBE, START, DESCRIBE, DESCRIBE, START, DESCRIBE]


def test_waiting_backs_off_to_five_minutes(box: FakeBox, mutmut: FakeRun, repo: Path, clock: FakeTime) -> None:
    patience(0.5)
    box.statuses = [None]
    assert offload(repo).where == f" (gave up after waiting 30m: {INSTANCE} is unavailable: instance is unknown; not run locally)"
    assert clock.slept == [30, 60, 120, 240, 300, 300, 300, 300, 150]
    assert mutmut.calls == []


def test_the_default_patience_is_six_hours(box: FakeBox, mutmut: FakeRun, repo: Path, clock: FakeTime) -> None:
    box.statuses = [None]
    assert offload(repo).where.startswith(" (gave up after waiting 6h: ")
    assert sum(clock.slept) == 6 * 3600
    assert remote.MAX_WAIT_HOURS == 6.0


def test_a_box_that_will_not_start_fails_the_python_gate(box: FakeBox, mutmut: FakeRun, repo: Path) -> None:
    patience(0)
    box.statuses = ["TERMINATED"]
    box.start = (1, "PERMISSION_DENIED")
    result = py_mutation.run_gate(gate_ctx(repo))
    assert result.ok is False
    assert result.summary == f"mutmut failed (gave up after waiting 0s: {INSTANCE} is unavailable: PERMISSION_DENIED; not run locally)"
    assert mutmut.calls == []
    assert not remote.USAGE.exists()


@pytest.mark.parametrize(("status", "verb", "args"), [("STOPPED", "start", START), ("SUSPENDED", "resume", RESUME)])
def test_a_silent_power_on_failure_names_the_verb(
    box: FakeBox, mutmut: FakeRun, repo: Path, status: str, verb: str, args: list[str]
) -> None:
    patience(0)
    box.statuses = [status]
    box.start = (1, " \n")
    assert offload(repo).where == f" (gave up after waiting 0s: {INSTANCE} is unavailable: {verb} failed; not run locally)"
    assert mutmut.calls == []
    assert box.gcloud_calls == [DESCRIBE, DESCRIBE, args]


def test_a_terminated_box_is_started_and_the_start_is_recorded(box: FakeBox, mutmut: FakeRun, repo: Path) -> None:
    box.statuses = ["TERMINATED", "TERMINATED", "RUNNING"]
    assert offload(repo) == remote.Outcome(0, "done", f" (on {INSTANCE}, 0s)")
    assert mutmut.calls == []
    assert box.gcloud_calls == [DESCRIBE, DESCRIBE, START, DESCRIBE]
    first, second = usage_lines()
    assert list(first) == ["ts", "event", "instance", "usd_per_hour"]
    assert without_stamp(first) == {"event": "vm-start", "instance": INSTANCE, "usd_per_hour": 1.2}
    assert without_stamp(second) == job_line(repo, "ok")


def test_a_suspended_box_is_resumed_not_started(box: FakeBox, mutmut: FakeRun, repo: Path) -> None:
    box.statuses = ["SUSPENDED", "SUSPENDED", "RUNNING"]
    assert offload(repo).where == f" (on {INSTANCE}, 0s)"
    assert box.gcloud_calls == [DESCRIBE, DESCRIBE, RESUME, DESCRIBE]
    assert without_stamp(usage_lines()[0])["event"] == "vm-start"


@pytest.mark.parametrize("stopping", ["STOPPING", "SUSPENDING"])
def test_a_stopping_box_is_waited_for_then_started(box: FakeBox, mutmut: FakeRun, repo: Path, clock: FakeTime, stopping: str) -> None:
    box.statuses = [stopping, stopping, stopping, "TERMINATED", "RUNNING"]
    assert offload(repo) == remote.Outcome(0, "done", f" (on {INSTANCE}, 0s)")
    assert clock.slept == [5]
    assert box.gcloud_calls == [DESCRIBE, DESCRIBE, DESCRIBE, DESCRIBE, START, DESCRIBE]
    assert mutmut.calls == []


def test_boot_wait_is_four_minutes() -> None:
    assert remote.BOOT_WAIT == 240


def test_ssh_that_never_comes_up_is_waited_for(
    box: FakeBox, mutmut: FakeRun, repo: Path, clock: FakeTime, monkeypatch: pytest.MonkeyPatch
) -> None:
    patience(0)
    monkeypatch.setattr(remote, "BOOT_WAIT", 10)
    box.probe = (255, "")
    assert offload(repo).where == f" (gave up after waiting 0s: {INSTANCE} is unavailable: ssh never came up; not run locally)"
    assert box.ssh_calls == [(IP, PROBE, 20, False), (IP, PROBE, 20, False)]
    assert clock.slept == [5, 5]
    assert mutmut.calls == []


def test_a_running_box_runs_the_job_and_records_it(box: FakeBox, mutmut: FakeRun, repo: Path) -> None:
    box.job_seconds = 1800.4
    assert offload(repo) == remote.Outcome(0, "done", f" (on {INSTANCE}, 1800s)")
    assert mutmut.calls == []
    assert box.gcloud_calls == [DESCRIBE]
    (job,) = box.job_commands()
    assert [call[0] for call in box.ssh_calls] == [IP, IP, IP]
    assert box.ssh_calls[:2] == [(IP, PROBE, 20, False), (IP, f"mkdir -p {repo}", 30, False)]
    assert box.ssh_calls[2][2:] == (21720, True)
    for part in (
        "max-children 8",
        "docker exec mt",
        "docker exec -w",
        " mt setsid",
        JOB_LOCK,
        "/var/lib/marestail/busy.d/",
        f"docker exec -w {repo} -e PATH=",
        " -e A=1 mt setsid",
        "timeout -k 30 7200 mutmut run --max-children 8 </dev/null;",
        "printf '\\nmarestail-exit %s\\n' \"$code\"",
    ):
        assert part in job
    assert "npm ci" not in job
    assert box.rsync_calls == [[*PY_EXCLUDES, f"{repo}/", f"max@{IP}:{repo}/"], [f"max@{IP}:{repo}/mutants/", f"{repo}/mutants/"]]
    (line,) = usage_lines()
    assert list(line) == JOB_KEYS
    assert without_stamp(line) == job_line(repo, "ok", 1800, 0.6)


def test_a_running_box_through_the_python_gate(box: FakeBox, mutmut: FakeRun, repo: Path) -> None:
    box.on_fetch = lambda args: write_meta(repo / "mutants") if args[-1].endswith("/mutants/") else None
    result = py_mutation.run_gate(gate_ctx(repo))
    assert result.ok is True
    assert result.summary == f"all 2 mutants killed (1 killed by timeout) (on {INSTANCE}, 0s)"
    assert mutmut.calls == []
    (job,) = box.job_commands()
    assert "max-children 8" in job
    (line,) = usage_lines()
    assert without_stamp(line) == job_line(repo, "ok")


def test_a_switched_off_remote_runs_locally(box: FakeBox, mutmut: FakeRun, repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(remote.ENV, "0")
    result = py_mutation.run_gate(gate_ctx(repo))
    assert result.summary == "all 2 mutants killed (1 killed by timeout)"
    assert (box.gcloud_calls, box.ssh_calls, box.rsync_calls) == ([], [], [])


def test_a_switched_off_remote_still_runs_the_gate_on_the_box_under_never(
    box: FakeBox, mutmut: FakeRun, repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    configure(NEVER)
    monkeypatch.setenv(remote.ENV, "off")
    box.on_fetch = lambda args: write_meta(repo / "mutants") if args[-1].endswith("/mutants/") else None
    result = py_mutation.run_gate(gate_ctx(repo))
    assert result.summary == f"all 2 mutants killed (1 killed by timeout) (on {INSTANCE}, 0s)"
    assert mutmut.calls == []
    assert len(box.job_commands()) == 1
    assert capsys.readouterr().err == f"MARESTAIL_REMOTE=off ignored: {POLICY}\n"


def test_a_gate_that_is_not_offloaded_runs_locally(box: FakeBox, mutmut: FakeRun, repo: Path) -> None:
    configure(TOML.replace('gates = ["py.mutation"]', 'gates = ["ts.mutation"]'))
    assert offload(repo) == remote.Outcome(0, "local mutmut", "")
    assert mutmut.calls == [["mutmut", "run", "--max-children", "4"]]
    assert mutmut.options == local_call(repo)
    assert (box.gcloud_calls, box.ssh_calls, box.rsync_calls) == ([], [], [])
    assert not remote.USAGE.exists()


@pytest.mark.parametrize(("reply", "problem"), [((1, "warning\nconnection reset\n"), "connection reset"), ((23, "  "), "rsync exit 23")])
def test_a_failed_sync_is_retried_and_never_run_locally(
    box: FakeBox, mutmut: FakeRun, repo: Path, clock: FakeTime, reply: Reply, problem: str
) -> None:
    patience(0.01)
    box.sync = reply
    assert offload(repo) == remote.Outcome(
        75, "", f" (gave up after waiting 36s: copying the tree to {INSTANCE} failed: {problem}; not run locally)"
    )
    assert clock.slept == [30, 6]
    assert mutmut.calls == []
    assert box.job_commands() == []
    assert not remote.USAGE.exists()


def test_a_drop_without_the_box_coming_back_fails(box: FakeBox, mutmut: FakeRun, repo: Path) -> None:
    patience(0)
    box.jobs = [None]
    assert offload(repo) == remote.Outcome(
        75, "", f" (gave up after waiting 0s: the connection to {INSTANCE} dropped mid-run; not run locally)"
    )
    assert mutmut.calls == []
    assert len(box.job_commands()) == 1
    (line,) = usage_lines()
    assert without_stamp(line) == job_line(repo, "dropped")


def test_a_drop_through_the_python_gate_fails(box: FakeBox, mutmut: FakeRun, repo: Path) -> None:
    patience(0)
    box.jobs = [None]
    result = py_mutation.run_gate(gate_ctx(repo))
    assert result.ok is False
    assert result.summary == f"mutmut failed (gave up after waiting 0s: the connection to {INSTANCE} dropped mid-run; not run locally)"
    assert mutmut.calls == []


def test_drops_are_retried_on_the_box_until_the_patience_runs_out(box: FakeBox, mutmut: FakeRun, repo: Path, clock: FakeTime) -> None:
    patience(0.03)
    box.jobs = [None, None, None, None]
    assert offload(repo).where == f" (gave up after waiting 1m48s: the connection to {INSTANCE} dropped mid-run; not run locally)"
    assert len(box.job_commands()) == 4
    assert clock.slept == [30, 60, 18]
    assert mutmut.calls == []
    assert [without_stamp(line)["result"] for line in usage_lines()] == ["dropped"] * 4


def test_a_drop_that_recovers_reruns_on_the_box(box: FakeBox, mutmut: FakeRun, repo: Path) -> None:
    box.jobs = [None, (0, "done")]
    assert offload(repo) == remote.Outcome(0, "done", f" (on {INSTANCE}, 0s, after waiting 30s for the box)")
    assert len(box.job_commands()) == 2
    assert box.gcloud_calls == [DESCRIBE, DESCRIBE]
    assert {call[0] for call in box.ssh_calls} == {IP}
    assert box.rsync_calls[-1] == [f"max@{IP}:{repo}/mutants/", f"{repo}/mutants/"]
    assert mutmut.calls == []
    assert [without_stamp(line) for line in usage_lines()] == [job_line(repo, "dropped"), job_line(repo, "ok")]


def test_a_job_that_exits_255_with_its_trailer_is_not_a_drop(box: FakeBox, mutmut: FakeRun, repo: Path) -> None:
    box.jobs = [(255, "")]
    assert offload(repo) == remote.Outcome(255, "", f" (on {INSTANCE}, 0s)")
    assert len(box.job_commands()) == 1


def test_an_ssh_timeout_is_not_retried(box: FakeBox, mutmut: FakeRun, repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def ssh(found: remote.Settings, ip: str, command: str, timeout: int | None = None, tty: bool = False) -> Reply:
        reply = box.ssh(found, ip, command, timeout, tty)
        return (124, "ssh timed out after 21720s") if JOB in command else reply

    monkeypatch.setattr(remote, "ssh", ssh)
    assert offload(repo) == remote.Outcome(124, "ssh timed out after 21720s", f" (on {INSTANCE}, 0s)")
    assert without_stamp(usage_lines()[0])["result"] == "exit 124"


def test_run_job_defaults_to_two_hours_on_the_box(box: FakeBox, mutmut: FakeRun, repo: Path) -> None:
    assert remote.run_job(make_context(repo), "py.mutation", build, repo, 4) == remote.Outcome(0, "done", f" (on {INSTANCE}, 0s)")
    (job,) = box.job_commands()
    assert "timeout -k 30 7200 mutmut" in job
    assert box.ssh_calls[-1][2:] == (21720, True)
    assert box.rsync_calls == [[*PY_EXCLUDES, f"{repo}/", f"max@{IP}:{repo}/"]]


def test_run_job_defaults_to_two_hours_and_no_workers_locally(box: FakeBox, mutmut: FakeRun, repo: Path) -> None:
    configure(TOML.replace('gates = ["py.mutation"]', "gates = []"))
    assert remote.run_job(make_context(repo), "py.mutation", build, repo) == remote.Outcome(0, "local mutmut", "")
    assert mutmut.calls == [["mutmut", "run", "--max-children", "0"]]
    assert mutmut.options == [{"cwd": repo, "env": GUARDED, "timeout": 7200}]


def test_a_failing_job_reports_its_output_from_the_box(box: FakeBox, mutmut: FakeRun, repo: Path) -> None:
    box.jobs = [(1, "boom")]
    assert offload(repo) == remote.Outcome(1, "boom", f" (on {INSTANCE}, 0s)")
    (line,) = usage_lines()
    assert without_stamp(line) == job_line(repo, "exit 1")


def test_a_failing_job_through_the_python_gate(box: FakeBox, mutmut: FakeRun, repo: Path) -> None:
    box.jobs = [(1, "boom")]
    result = py_mutation.run_gate(gate_ctx(repo))
    assert (result.ok, result.summary, result.findings) == (False, f"mutmut failed (on {INSTANCE}, 0s)", ["boom"])
    assert len(box.job_commands()) == 1
    assert mutmut.calls == []


def test_sent_paths_follow_the_tree_to_the_box(box: FakeBox, mutmut: FakeRun, repo: Path) -> None:
    coverage = repo / ".marestail" / "py-coverage.xml"
    coverage.parent.mkdir(parents=True)
    coverage.write_text("<x/>")
    folder = repo / ".marestail" / "ts-coverage"
    folder.mkdir()
    missing = repo / "absent"
    sent = (coverage, folder, missing)
    assert remote.run_job(make_context(repo), "py.mutation", build, repo, 4, send=sent).code == 0
    assert box.rsync_calls[1:] == [[str(coverage), f"max@{IP}:{coverage}"], [f"{folder}/", f"max@{IP}:{folder}/"]]
    assert (IP, f"mkdir -p {coverage.parent}", 30, False) in box.ssh_calls


def test_a_failed_send_is_a_failed_sync(box: FakeBox, mutmut: FakeRun, repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    patience(0)
    sent = repo / "pkg"
    monkeypatch.setattr(remote, "rsync", lambda _found, args: (12, "denied") if args[0] == f"{sent}/" else (0, ""))
    assert remote.run_job(make_context(repo), "py.mutation", build, repo, 4, send=(sent,)).where == (
        f" (gave up after waiting 0s: copying the tree to {INSTANCE} failed: denied; not run locally)"
    )


@pytest.mark.parametrize("value", ["0", "off", "false", "no", "OFF", "No"])
def test_a_switched_off_remote_ignores_the_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    configure()
    monkeypatch.setenv(remote.ENV, value)
    assert remote._settings(make_context(tmp_path)) is None
    assert remote.switched_off() is True


def test_settings_load_the_config_when_remote_is_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    configure()
    monkeypatch.setenv(remote.ENV, "1")
    assert remote.switched_off() is False
    assert remote._settings(make_context(tmp_path)) == box_settings(key=home / ".ssh" / "google_compute_engine")


def test_a_repo_can_switch_remote_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    configure()
    monkeypatch.delenv(remote.ENV)
    assert remote._settings(make_context(tmp_path, {"remote": {"enabled": False}})) is None
    assert remote._settings(make_context(tmp_path, {"remote": {"enabled": 0}})) is not None


NEVER = TOML + 'local = "never"\n'
POLICY = '~/.config/marestail/remote.toml sets [remote] local = "never"'


@pytest.mark.parametrize(
    ("text", "policy"),
    [
        (None, "allowed"),
        (TOML, "allowed"),
        (TOML + 'local = "allowed"\n', "allowed"),
        (NEVER, "never"),
        (TOML + 'local = "no"\n', "never"),
        (NEVER.replace("enabled = true", "enabled = false"), "never"),
        ('[other]\nlocal = "never"\n', "allowed"),
    ],
)
def test_policy_prints_never_only_when_remote_toml_forbids_local_runs(
    capsys: pytest.CaptureFixture[str], text: str | None, policy: str
) -> None:
    if text is not None:
        configure(text)
    assert remote.policy_command(None) == 0
    assert capsys.readouterr() == (f"{policy}\n", "")


@pytest.mark.parametrize("value", ["0", "off", "OFF", "false", "no"])
def test_never_ignores_a_switched_off_remote_and_says_so_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], home: Path, value: str
) -> None:
    configure(NEVER)
    monkeypatch.setenv(remote.ENV, value)
    ctx = make_context(tmp_path)
    assert remote._settings(ctx) == box_settings(key=home / ".ssh" / "google_compute_engine")
    assert remote.offloaded(ctx, "py.mutation") is True
    assert capsys.readouterr() == ("", f"MARESTAIL_REMOTE={value} ignored: {POLICY}\n")


def test_never_ignores_a_repo_opt_out_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    configure(NEVER)
    monkeypatch.delenv(remote.ENV)
    assert remote._settings(make_context(tmp_path, {"remote": {"enabled": False}})) is not None
    assert capsys.readouterr().err == f"[remote] enabled = false in {tmp_path}/marestail.toml ignored: {POLICY}\n"


def test_never_names_both_opt_outs_on_their_own_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    configure(NEVER)
    monkeypatch.setenv(remote.ENV, "off")
    assert remote._settings(make_context(tmp_path, {"remote": {"enabled": False}})) is not None
    assert capsys.readouterr().err.splitlines() == [
        f"MARESTAIL_REMOTE=off ignored: {POLICY}",
        f"[remote] enabled = false in {tmp_path}/marestail.toml ignored: {POLICY}",
    ]


def test_never_with_nothing_switched_off_says_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    configure(NEVER)
    monkeypatch.setenv(remote.ENV, "1")
    assert remote._settings(make_context(tmp_path, {"remote": {"enabled": 0}})) is not None
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("local", ["", 'local = "allowed"\n'])
def test_allowed_honours_both_opt_outs_quietly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], local: str
) -> None:
    configure(TOML + local)
    monkeypatch.setenv(remote.ENV, "off")
    assert remote._settings(make_context(tmp_path)) is None
    monkeypatch.delenv(remote.ENV)
    assert remote._settings(make_context(tmp_path, {"remote": {"enabled": False}})) is None
    assert capsys.readouterr() == ("", "")


def test_never_still_obeys_remote_toml_switching_the_box_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    configure(NEVER.replace("enabled = true", "enabled = false"))
    monkeypatch.setenv(remote.ENV, "off")
    assert remote._settings(make_context(tmp_path)) is None
    assert capsys.readouterr() == ("", "")


def test_the_policy_names_a_config_outside_home_by_its_full_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    elsewhere = tmp_path / "etc" / "remote.toml"
    monkeypatch.setattr(remote, "CONFIG", elsewhere)
    configure(NEVER)
    monkeypatch.setenv(remote.ENV, "no")
    assert remote._settings(make_context(tmp_path)) is not None
    assert capsys.readouterr().err == f'MARESTAIL_REMOTE=no ignored: {elsewhere} sets [remote] local = "never"\n'


def test_never_keeps_every_configured_gate_on_the_box(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    configure(NEVER.replace('gates = ["py.mutation"]', 'gates = ["py.mutation", "ts.tests", "qa"]'))
    monkeypatch.setenv(remote.GATES, "ts.mutation,ts.tests")
    loaded = remote._load()
    assert loaded is not None
    assert loaded.gates == ("py.mutation", "ts.tests", "qa", "ts.mutation")
    assert capsys.readouterr().err == f"MARESTAIL_REMOTE_GATES=ts.mutation,ts.tests ignored for py.mutation, qa: {POLICY}\n"


def test_never_lets_the_gates_variable_add_gates_quietly(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    configure(NEVER)
    monkeypatch.setenv(remote.GATES, "ts.mutation,py.mutation")
    loaded = remote._load()
    assert loaded is not None
    assert loaded.gates == ("py.mutation", "ts.mutation")
    assert capsys.readouterr() == ("", "")


def test_load_without_a_config_is_none() -> None:
    assert remote._load() is None


def test_load_needs_a_remote_section() -> None:
    configure('[other]\ninstance = "b"\n')
    with pytest.raises(KeyError, match="instance"):
        remote._load()


def test_load_honours_enabled_false() -> None:
    configure(TOML.replace("enabled = true", "enabled = false"))
    assert remote._load() is None


def test_load_fills_defaults(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    configure('[remote]\ninstance = "b"\nproject = "p"\nzone = "z"\n')
    monkeypatch.setenv("USER", "alice")
    assert remote._load() == remote.Settings(
        instance="b",
        project="p",
        zone="z",
        container="mt",
        gates=(),
        workers={},
        env={},
        usd_per_hour=0.0,
        user="alice",
        key=home / ".ssh" / "google_compute_engine",
        run_in={},
        max_wait=21600.0,
        sonar_host_url="",
    )
    monkeypatch.delenv("USER")
    loaded = remote._load()
    assert loaded is not None
    assert loaded.user == "max"


def test_load_converts_values(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("USER", "alice")
    configure(
        '[remote]\ninstance = "b"\nproject = "p"\nzone = "z"\ncontainer = "c"\nkey = "~/k"\n'
        'workers = { g = "3" }\nenv = { A = 1 }\nusd_per_hour = 2\nuser = "bob"\nUSER = "carol"\n'
        'max_wait_hours = 0.5\nsonar_host_url = "http://10.0.0.2:9000"\nrun_in = { default = "host", "py.mutation" = 7 }\n'
    )
    loaded = remote._load()
    assert loaded is not None
    assert (loaded.container, loaded.key, loaded.workers, loaded.env, loaded.usd_per_hour) == ("c", home / "k", {"g": 3}, {"A": "1"}, 2.0)
    assert loaded.user == "bob"
    assert isinstance(loaded.usd_per_hour, float)
    assert (loaded.max_wait, loaded.sonar_host_url) == (1800.0, "http://10.0.0.2:9000")
    assert loaded.run_in == {"default": "host", "py.mutation": "7"}


def test_run_in_may_be_one_place_for_every_gate() -> None:
    configure('[remote]\ninstance = "b"\nproject = "p"\nzone = "z"\nrun_in = "host"\n')
    loaded = remote._load()
    assert loaded is not None
    assert loaded.run_in == {"default": "host"}
    assert (loaded.place("py.mutation"), loaded.place("offload")) == ("host", "host")


@pytest.mark.parametrize(
    ("run_in", "gate", "place"),
    [
        ({}, "ts.tests", "mt"),
        ({}, "offload", "host"),
        ({"default": "host"}, "ts.mutation", "host"),
        ({"default": "host", "ts.mutation": "mt"}, "ts.mutation", "mt"),
        ({"ts.tests": "host"}, "ts.tests", "host"),
        ({"ts.tests": "host"}, "py.tests", "mt"),
        ({"offload": "other"}, "offload", "other"),
        ({"default": "mt"}, "offload", "host"),
    ],
)
def test_place_reads_the_gate_then_the_default_then_the_container(run_in: dict[str, str], gate: str, place: str) -> None:
    assert box_settings(run_in=run_in).place(gate) == place


def test_the_gates_variable_overrides_the_config(monkeypatch: pytest.MonkeyPatch) -> None:
    configure()
    monkeypatch.setenv(remote.GATES, "ts.mutation,ex.mutation")
    loaded = remote._load()
    assert loaded is not None
    assert loaded.gates == ("ts.mutation", "ex.mutation")
    monkeypatch.setenv(remote.GATES, "")
    assert remote._offloaded_gates({"gates": ["a"]}) == ("a",)


@pytest.mark.parametrize(
    ("toml", "url"),
    [
        ('gates = ["sonar"]\nsonar_host_url = "http://10.1.2.3:9000"\n', "http://10.1.2.3:9000"),
        ('gates = ["sonar"]\n', "http://localhost:9000"),
        ('gates = ["py.mutation"]\nsonar_host_url = "http://10.1.2.3:9000"\n', "http://localhost:9000"),
    ],
)
def test_sonar_url_points_the_box_at_the_sonar_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, toml: str, url: str) -> None:
    monkeypatch.delenv(remote.ENV, raising=False)
    configure('[remote]\ninstance = "b"\nproject = "p"\nzone = "z"\n' + toml)
    assert remote.sonar_url(make_context(tmp_path), "http://localhost:9000") == url


def test_sonar_url_without_a_box_is_the_fallback(tmp_path: Path) -> None:
    assert remote.sonar_url(make_context(tmp_path), "http://localhost:9000") == "http://localhost:9000"


def test_a_box_without_a_count_for_the_gate_keeps_the_local_workers(box: FakeBox, mutmut: FakeRun, repo: Path) -> None:
    configure(TOML.replace('workers = { "py.mutation" = 8 }', 'workers = { "ts.mutation" = 8 }'))
    assert remote.run_job(make_context(repo), "py.mutation", build, repo, 3).where == f" (on {INSTANCE}, 0s)"
    (job,) = box.job_commands()
    assert "mutmut run --max-children 3 </dev/null;" in job
    assert mutmut.calls == []


def test_a_local_run_builds_with_the_local_workers(box: FakeBox, mutmut: FakeRun, repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(remote.ENV, "off")
    assert remote.run_job(make_context(repo), "py.mutation", build, repo, 3) == remote.Outcome(0, "local mutmut", "")
    assert mutmut.calls == [["mutmut", "run", "--max-children", "3"]]


@pytest.mark.parametrize(
    ("code", "output", "expected"),
    [
        (0, "a\nb\nmarestail-exit 3\n", (3, "a\nb")),
        (0, "a\n\nmarestail-exit 0\n", (0, "a\n")),
        (255, "\nmarestail-exit 255", (255, "")),
        (255, "", (None, "")),
        (255, "partial output", (None, "partial output")),
        (0, "a\nmarestail-exit x\n", (None, "a\nmarestail-exit x\n")),
        (124, "ssh timed out after 9s", (124, "ssh timed out after 9s")),
        (124, "job said ssh timed out after", (None, "job said ssh timed out after")),
        (1, "ssh timed out after 9s", (None, "ssh timed out after 9s")),
    ],
)
def test_concluded_reads_the_exit_trailer(code: int, output: str, expected: tuple[int | None, str]) -> None:
    assert remote._concluded(code, output) == expected


@pytest.mark.parametrize(("seconds", "text"), [(0, "0s"), (30, "30s"), (60, "1m"), (90, "1m30s"), (3600, "1h"), (21599.9, "5h59m59s")])
def test_span_reads_like_a_duration(seconds: float, text: str) -> None:
    assert remote.span(seconds) == text


def test_local_with_a_timeout_uses_the_shell(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeRun([(2, "out")])
    monkeypatch.setattr(remote, "run", fake)
    assert remote._local(["a"], tmp_path, {"E": "1"}, 60, " where") == remote.Outcome(2, "out", " where")
    assert fake.options == [{"cwd": tmp_path, "env": {"E": "1"}, "timeout": 60}]


class Completed(SimpleNamespace):
    pass


class FakeSubprocess:
    def __init__(self, reply: Completed | BaseException) -> None:
        self.reply = reply
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(self, command: list[str], **options: Any) -> Completed:
        self.calls.append((command, options))
        if isinstance(self.reply, BaseException):
            raise self.reply
        return self.reply


@pytest.fixture
def fake_subprocess(monkeypatch: pytest.MonkeyPatch) -> Callable[[Completed | BaseException], FakeSubprocess]:
    def install(reply: Completed | BaseException) -> FakeSubprocess:
        fake = FakeSubprocess(reply)
        monkeypatch.setattr("marestail.remote.subprocess.run", fake)
        return fake

    return install


def test_local_without_a_timeout_runs_unbounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_subprocess: Any) -> None:
    monkeypatch.setenv("KEEP", "yes")
    fake = fake_subprocess(Completed(returncode=3, stdout="a\x1b[31mb\r\n", stderr="c"))
    assert remote._local(["a", "b"], tmp_path, {"E": "1"}, None, "") == remote.Outcome(3, "ab\nc", "")
    ((command, options),) = fake.calls
    assert command == ["a", "b"]
    assert options["env"]["KEEP"] == "yes"
    assert options["env"]["E"] == "1"
    assert {key: value for key, value in options.items() if key != "env"} == {
        "cwd": tmp_path,
        "capture_output": True,
        "text": True,
        "check": False,
    }


def test_unbounded_without_env_keeps_the_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_subprocess: Any) -> None:
    monkeypatch.setenv("KEEP", "yes")
    fake = fake_subprocess(Completed(returncode=0, stdout="", stderr=""))
    assert remote._unbounded(["a"], tmp_path, None) == (0, "")
    assert fake.calls[0][1]["env"]["KEEP"] == "yes"


def test_gcloud_adds_project_zone_and_quiet(fake_subprocess: Any) -> None:
    fake = fake_subprocess(Completed(returncode=0, stdout="out", stderr="err"))
    assert remote.gcloud(box_settings(), START) == (0, "out")
    assert fake.calls == [
        (
            ["gcloud", "compute", "instances", "start", INSTANCE, "--project=proj", "--zone=europe-west1-b", "--quiet"],
            {"capture_output": True, "text": True, "check": False},
        )
    ]


def test_gcloud_returns_stderr_on_failure(fake_subprocess: Any) -> None:
    fake_subprocess(Completed(returncode=1, stdout="out", stderr="err"))
    assert remote.gcloud(box_settings(), START) == (1, "err")


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ((1, "{}"), ("", "")),
        ((0, json.dumps({"status": "RUNNING", "networkInterfaces": [{"accessConfigs": [{"natIP": IP}]}]})), ("RUNNING", IP)),
        ((0, json.dumps({"status": "STOPPED"})), ("STOPPED", "")),
        ((0, json.dumps({"networkInterfaces": [{"accessConfigs": []}]})), ("", "")),
        ((0, json.dumps({"status": "RUNNING", "networkInterfaces": [{}]})), ("RUNNING", "")),
        ((0, json.dumps({"status": "RUNNING", "networkInterfaces": [{"accessConfigs": None}]})), ("RUNNING", "")),
    ],
)
def test_describe_reads_status_and_address(monkeypatch: pytest.MonkeyPatch, reply: Reply, expected: tuple[str, str]) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(remote, "gcloud", lambda found, args: [*noted(calls, [found.instance, *args]), reply][-1])
    assert remote.describe(box_settings()) == expected
    assert calls == [[INSTANCE, *DESCRIBE]]


def options(home: Path) -> list[str]:
    return [
        "-i",
        "/keys/id",
        "-o",
        f"HostKeyAlias={INSTANCE}",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        f"UserKnownHostsFile={home}/.ssh/marestail_known_hosts",
        "-o",
        "ServerAliveInterval=30",
        "-o",
        "ConnectTimeout=15",
        "-o",
        "BatchMode=yes",
    ]


def test_ssh_options_pin_the_host_key_and_batch_mode(home: Path) -> None:
    assert remote._ssh_options(box_settings()) == options(home)


def test_ssh_runs_the_command_on_the_box(home: Path, fake_subprocess: Any) -> None:
    fake = fake_subprocess(Completed(returncode=0, stdout="a\r\n", stderr="b"))
    assert remote.ssh(box_settings(), IP, "true") == (0, "a\nb")
    assert fake.calls == [
        (["ssh", *options(home), f"max@{IP}", "true"], {"capture_output": True, "text": True, "timeout": None, "check": False})
    ]


def test_ssh_with_a_tty_hangs_up_quietly(home: Path, fake_subprocess: Any) -> None:
    fake = fake_subprocess(Completed(returncode=5, stdout="", stderr=""))
    assert remote.ssh(box_settings(), IP, "job", timeout=9, tty=True) == (5, "")
    assert fake.calls[0][0] == ["ssh", "-tt", "-q", *options(home), f"max@{IP}", "job"]
    assert fake.calls[0][1]["timeout"] == 9


def test_ssh_timeout_is_exit_124(fake_subprocess: Any) -> None:
    fake_subprocess(subprocess.TimeoutExpired(["ssh"], 20))
    assert remote.ssh(box_settings(), IP, "true", timeout=20) == (124, "ssh timed out after 20s")


def test_rsync_tunnels_over_ssh_and_returns_stderr(home: Path, fake_subprocess: Any) -> None:
    fake = fake_subprocess(Completed(returncode=12, stdout="out", stderr="err"))
    assert remote.rsync(box_settings(), ["a/", "b/"]) == (12, "err")
    transport = (
        f"ssh -i /keys/id -o HostKeyAlias={INSTANCE} -o StrictHostKeyChecking=accept-new "
        f"-o UserKnownHostsFile={home}/.ssh/marestail_known_hosts -o ServerAliveInterval=30 -o ConnectTimeout=15 -o BatchMode=yes"
    )
    assert fake.calls == [
        (["rsync", "-a", "--delete", "-e", transport, "a/", "b/"], {"capture_output": True, "text": True, "check": False})
    ]


class Recorder:
    def __init__(self, sync: Reply = (0, "")) -> None:
        self.sync = sync
        self.ssh_calls: list[tuple[str, str, int | None, bool]] = []
        self.rsync_calls: list[list[str]] = []

    def ssh(self, found: remote.Settings, ip: str, command: str, timeout: int | None = None, tty: bool = False) -> Reply:
        same_box(found)
        self.ssh_calls.append((ip, command, timeout, tty))
        return 0, ""

    def rsync(self, found: remote.Settings, args: list[str]) -> Reply:
        same_box(found)
        self.rsync_calls.append(args)
        return self.sync


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    fake = Recorder()
    monkeypatch.setattr(remote, "ssh", fake.ssh)
    monkeypatch.setattr(remote, "rsync", fake.rsync)
    return fake


def nvm_node(home: Path, version: str) -> Path:
    folder = home / ".nvm" / "versions" / "node" / version
    (folder / "bin").mkdir(parents=True)
    return folder


def uv_venv(home: Path, root: Path) -> Path:
    store = home / ".local" / "share" / "uv" / "python"
    (store / "cpython" / "bin").mkdir(parents=True)
    (store / "cpython" / "bin" / "python3").write_text("")
    (root / ".venv" / "bin").mkdir(parents=True)
    (root / ".venv" / "bin" / "python").symlink_to(store / "cpython" / "bin" / "python3")
    return store


def test_push_syncs_the_repo_and_the_toolchains(tmp_path: Path, home: Path, recorder: Recorder, monkeypatch: pytest.MonkeyPatch) -> None:
    node = nvm_node(home, "v20")
    monkeypatch.setenv("PATH", str(node / "bin"))
    root = tmp_path / "my repo"
    uv_venv(home, root)
    assert remote._push(box_settings(), IP, root, "ts.mutation") == ""
    assert recorder.ssh_calls == [(IP, f"mkdir -p '{root}'", 30, False), (IP, f"mkdir -p {node}", 30, False)]
    assert recorder.rsync_calls == [[*ALL_EXCLUDES, f"{root}/", f"max@{IP}:{root}/"], [f"{node}/", f"max@{IP}:{node}/"]]


def test_push_keeps_the_venv_for_python(tmp_path: Path, home: Path, recorder: Recorder, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "")
    store = uv_venv(home, tmp_path)
    assert remote._push(box_settings(), IP, tmp_path, "py.tests") == ""
    assert recorder.rsync_calls == [[*PY_EXCLUDES, f"{tmp_path}/", f"max@{IP}:{tmp_path}/"], [f"{store}/", f"max@{IP}:{store}/"]]


def test_push_without_a_gate_excludes_the_venv(tmp_path: Path, recorder: Recorder, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "")
    assert remote._push(box_settings(), IP, tmp_path, "") == ""
    assert recorder.rsync_calls == [[*ALL_EXCLUDES, f"{tmp_path}/", f"max@{IP}:{tmp_path}/"]]


@pytest.mark.parametrize(("sync", "problem"), [((1, "a\nb\n"), "b"), ((7, ""), "rsync exit 7")])
def test_push_reports_a_failed_sync(tmp_path: Path, recorder: Recorder, sync: Reply, problem: str) -> None:
    recorder.sync = sync
    assert remote._push(box_settings(), IP, tmp_path, "py.mutation") == problem
    assert len(recorder.rsync_calls) == 1
    assert len(recorder.ssh_calls) == 1


def test_toolchains_list_nvm_bins_on_path_once(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    v20 = nvm_node(home, "v20")
    v22 = nvm_node(home, "v22")
    (v20 / "sbin").mkdir()
    other = home / "other" / "v1" / "bin"
    other.mkdir(parents=True)
    missing = home / ".nvm" / "versions" / "node" / "v18" / "bin"
    entries = [v22 / "bin", v20 / "sbin", other, missing, v20 / "bin", v22 / "bin"]
    monkeypatch.setenv("PATH", ":".join(map(str, entries)))
    assert remote._toolchains() == [v22, v20]
    assert remote._toolchains(tmp_path) == [v22, v20]


def test_toolchains_without_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PATH")
    assert remote._toolchains() == []


@pytest.mark.parametrize(("path", "trailing"), [("mutants", "/"), ("reports/mutation/mutation.json", "")])
def test_fetch_pulls_a_folder_or_a_file(tmp_path: Path, recorder: Recorder, path: str, trailing: str) -> None:
    remote._fetch(box_settings(), IP, tmp_path, path)
    target = tmp_path / path
    assert recorder.rsync_calls == [[f"max@{IP}:{target}{trailing}", f"{target}{trailing}"]]
    assert target.parent.is_dir()


@pytest.fixture
def job_id(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(remote, "uuid", SimpleNamespace(uuid4=lambda: SimpleNamespace(hex="abc")))
    return "abc"


def job_of(root: Path, gate: str, command: list[str], **changes: Any) -> remote._Job:
    fields: dict[str, Any] = {
        "ctx": make_context(root),
        "gate": gate,
        "build": lambda workers: [*command, *([str(workers)] if workers else [])],
        "cwd": root,
        "workers": 0,
        "env": None,
        "timeout": 100,
        "pull": (),
    }
    return remote._Job(**{**fields, **changes})


def wrapped(home: Path, runner: str, cleanup: str) -> str:
    marker = "/var/lib/marestail/busy.d/abc"
    return (
        f"echo $$ > {marker}; stop() {{ {cleanup} >/dev/null 2>&1; rm -f {marker}; }}; "
        "trap 'stop; exit 129' HUP INT TERM; "
        f"{runner} & wait $!; code=$?; rm -f {marker}; touch /var/lib/marestail/last; "
        "printf '\\nmarestail-exit %s\\n' \"$code\"; exit $code"
    )


def test_execute_queues_a_mutation_job_in_the_container(
    home: Path, recorder: Recorder, monkeypatch: pytest.MonkeyPatch, job_id: str
) -> None:
    monkeypatch.setenv("PATH", f"/usr/bin:{remote.SHIMS}:/bin")
    root = Path("/repo/a b")
    job = job_of(root, "py.mutation", ["mutmut", "run", "x y"], env={"A": "1"})
    assert remote._execute(box_settings(), IP, job) == (None, "")
    session = f"{home}/.cache/marestail-jobs/job-abc"
    inner = (
        f"mkdir -p -m 700 {home}/.cache/marestail-jobs && echo $$ > {session}; "
        f"timeout -k 30 100 mutmut run 'x y' 8 </dev/null; code=$?; rm -f {session}; exit $code"
    )
    docker = (
        f"docker exec -w '/repo/a b' -e PATH=/usr/bin:/bin -e HOME={home} -e LANG=C.UTF-8 -e TERM=dumb -e NO_COLOR=1 "
        f"-e MARESTAIL_NO_OFFLOAD=1 -e MARESTAIL_GATE=py.mutation -e A=1 mt setsid -w bash -c {shlex.quote(inner)}"
    )
    stop = f"[ -f {session} ] && pkill -9 -s $(cat {session}); rm -f {session}"
    cleanup = "sg docker -c " + shlex.quote("docker exec mt bash -c " + shlex.quote(stop))
    runner = f"flock /var/lib/marestail/job.lock sg docker -c {shlex.quote(docker)}"
    assert recorder.ssh_calls == [(IP, wrapped(home, runner, cleanup), 420, True)]


def failed(root: Path) -> str:
    return f'{{ echo "marestail: dependencies did not install in "{shlex.quote(str(root))} >&2; exit 97; }}'


def test_execute_runs_a_test_job_on_the_host_unqueued_with_nvm(home: Path, recorder: Recorder, job_id: str, tmp_path: Path) -> None:
    root = tmp_path / "web app"
    root.mkdir()
    (root / "pnpm-lock.yaml").write_text("lockfileVersion: 9\n")
    job = job_of(root, "ts.tests", ["npx", "vitest", "run"], env={"PLAYWRIGHT_JSON_OUTPUT_NAME": "/x/r.json"})
    found = box_settings(env={"PGPORT": "5432"}, run_in={"ts.tests": "host"}, workers={"ts.tests": 16})
    remote._execute(found, IP, job)
    session = f"{home}/.cache/marestail-jobs/job-abc"
    digest = hashlib.sha256(str(root).encode()).hexdigest()[:16]
    prepare = (
        f"{{ flock 9; ( cd {shlex.quote(str(root))} && {remote.PREPARE} ) || {failed(root)}; }} "
        f"9>{home}/.cache/marestail-jobs/prepare-{digest}.lock"
    )
    inner = (
        f"mkdir -p -m 700 {home}/.cache/marestail-jobs && echo $$ > {session}; {remote.NVM}; {prepare}; "
        f"timeout -k 30 100 npx vitest run 16 </dev/null; code=$?; rm -f {session}; exit $code"
    )
    assignments = (
        f"HOME={home} LANG=C.UTF-8 TERM=dumb NO_COLOR=1 MARESTAIL_NO_OFFLOAD=1 MARESTAIL_GATE=ts.tests PGPORT=5432 "
        "PLAYWRIGHT_JSON_OUTPUT_NAME=/x/r.json"
    )
    runner = f"env -C {shlex.quote(str(root))} {assignments} setsid -w bash -c {shlex.quote(inner)}"
    cleanup = f"{{ [ -f {session} ] && pkill -9 -s $(cat {session}); rm -f {session}; }}"
    assert recorder.ssh_calls == [(IP, wrapped(home, runner, cleanup), 420, True)]


def test_execute_without_a_timeout_runs_unbounded(recorder: Recorder, job_id: str, tmp_path: Path) -> None:
    remote._execute(box_settings(), IP, job_of(tmp_path, "py.mutation", ["mutmut"], timeout=None))
    ((_ip, script, timeout, tty),) = recorder.ssh_calls
    assert (timeout, tty) == (None, True)
    assert "timeout -k" not in script
    assert f"{tmp_path}/.cache" not in script
    assert "; mutmut 8 </dev/null; code=$?" in script


def test_execute_reads_the_trailer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, job_id: str) -> None:
    monkeypatch.setattr(remote, "ssh", lambda _found, _ip, _script, timeout=None, tty=False: (3, "out\nmarestail-exit 3\n"))
    assert remote._execute(box_settings(), IP, job_of(tmp_path, "ts.tests", ["true"])) == (3, "out")


def test_a_job_in_another_container_cleans_up_there(home: Path, recorder: Recorder, job_id: str, tmp_path: Path) -> None:
    remote._execute(box_settings(run_in={"default": "other"}), IP, job_of(tmp_path, "ts.tests", ["true"]))
    ((_ip, script, _timeout, _tty),) = recorder.ssh_calls
    assert "sg docker -c 'docker exec other bash -c" in script
    assert " other setsid -w bash -c " in script
    assert "flock /var/lib/marestail/job.lock" not in script


def test_env_flags_let_the_box_and_the_caller_override(home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PATH", "/bin")
    found = box_settings(env={"TERM": "xterm", "A": "1"})
    assert remote._env_flags(found, job_of(tmp_path, "g", [], env={"A": "2", "B": "x y"})) == (
        f"-e PATH=/bin -e HOME={home} -e LANG=C.UTF-8 -e TERM=xterm -e NO_COLOR=1 -e MARESTAIL_NO_OFFLOAD=1 "
        "-e MARESTAIL_GATE=g -e A=2 -e 'B=x y'"
    )
    monkeypatch.delenv("PATH")
    assert remote._env_flags(box_settings(), job_of(tmp_path, "g", [])).startswith("-e PATH= -e HOME=")


def test_fresh_paths_are_cleared_on_the_box_before_the_job(tmp_path: Path) -> None:
    root = tmp_path / "my repo"
    job = job_of(root, "java.tests", ["mvn", "test"], fresh=("target/surefire-reports", ".marestail/j.exec"))
    reports, data = (shlex.quote(str(root / path)) for path in ("target/surefire-reports", ".marestail/j.exec"))
    assert remote._setup(box_settings(), job) == f"rm -rf {reports} {data}; "


def test_run_job_passes_fresh_paths_through(box: FakeBox, mutmut: FakeRun, repo: Path) -> None:
    remote.run_job(make_context(repo), "py.mutation", build, repo, 4, fresh=("mutants",))
    (job,) = box.job_commands()
    assert f"rm -rf {repo}/mutants; " in job


def test_offloaded_names_the_listed_gates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    configure()
    monkeypatch.delenv(remote.ENV)
    ctx = make_context(tmp_path)
    assert (remote.offloaded(ctx, "py.mutation"), remote.offloaded(ctx, "ts.tests")) == (True, False)
    monkeypatch.setenv(remote.ENV, "0")
    assert remote.offloaded(ctx, "py.mutation") is False


def test_without_shims_drops_every_shim_entry() -> None:
    assert remote.without_shims(f"{remote.SHIMS}:/usr/bin:{remote.SHIMS}:/bin") == "/usr/bin:/bin"
    assert remote.SHIMS.name == "shims"


def test_the_prepare_lock_folder_is_the_nearest_with_a_lockfile(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    web = root / "apps" / "web"
    web.mkdir(parents=True)
    assert remote._lock_folder(root, web) is None
    (tmp_path / "package-lock.json").write_text("{}")
    assert remote._lock_folder(root, web) is None
    (root / "pnpm-lock.yaml").write_text("")
    assert remote._lock_folder(root, web) == root
    (web / "package-lock.json").mkdir()
    assert remote._lock_folder(root, web) == root
    (root / "apps" / "package-lock.json").write_text("{}")
    assert remote._lock_folder(root, web) == root / "apps"


class FakeProcess:
    def __init__(self, lines: list[str], code: int = 0) -> None:
        self.stdout: list[str] | None = lines
        self.code = code
        self.killed = 0

    def kill(self) -> None:
        self.killed += 1

    def wait(self) -> int:
        return self.code


@pytest.fixture
def popen(monkeypatch: pytest.MonkeyPatch) -> Callable[..., list[tuple[list[str], dict[str, Any]]]]:
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def install(process: FakeProcess) -> list[tuple[list[str], dict[str, Any]]]:
        def started(command: list[str], **options: Any) -> FakeProcess:
            calls.append((command, options))
            return process

        monkeypatch.setattr("marestail.remote.subprocess.Popen", started)
        return calls

    return install


def test_streamed_relays_output_live_and_keeps_the_job_exit_code(home: Path, popen: Any, capsys: pytest.CaptureFixture[str]) -> None:
    process = FakeProcess(["one\r\n", "\r\n", "two\r\n", "\r\n", "marestail-exit 4\r\n"], code=4)
    calls = popen(process)
    assert remote._streamed(box_settings(), IP, "script") == (4, "")
    assert capsys.readouterr().out == "one\n\ntwo\n"
    ((command, kwargs),) = calls
    assert command == ["ssh", "-tt", "-q", *options(home), f"max@{IP}", "script"]
    assert kwargs == {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "errors": "replace",
    }
    assert process.killed == 1


def test_streamed_without_a_trailer_is_a_drop(popen: Any, capsys: pytest.CaptureFixture[str]) -> None:
    popen(FakeProcess(["partial\n", "\n"], code=255))
    assert remote._streamed(box_settings(), IP, "script") == (None, "ssh exit 255")
    assert capsys.readouterr().out == "partial\n\n"


def test_streamed_with_a_garbled_trailer_is_a_drop(popen: Any) -> None:
    popen(FakeProcess(["marestail-exit x\n"], code=0))
    assert remote._streamed(box_settings(), IP, "script") == (None, "ssh exit 0")


def interrupted() -> Iterator[str]:
    yield "started\n"
    raise KeyboardInterrupt


def test_streamed_kills_ssh_when_interrupted(popen: Any, capsys: pytest.CaptureFixture[str]) -> None:
    process = FakeProcess([])
    process.stdout = interrupted()  # type: ignore[assignment]
    popen(process)
    with pytest.raises(KeyboardInterrupt):
        remote._streamed(box_settings(), IP, "script")
    assert process.killed == 1
    assert capsys.readouterr().out == "started\n"


def test_streamed_without_a_pipe_reads_nothing(popen: Any) -> None:
    process = FakeProcess([])
    process.stdout = None
    popen(process)
    assert remote._streamed(box_settings(), IP, "script") == (None, "ssh exit 0")


def test_record_appends_a_stamped_line() -> None:
    remote._record({"event": "x", "n": 1})
    remote._record({"event": "y"})
    lines = usage_lines()
    assert [list(line) for line in lines] == [["ts", "event", "n"], ["ts", "event"]]
    assert [without_stamp(line) for line in lines] == [{"event": "x", "n": 1}, {"event": "y"}]


def test_record_job_returns_the_elapsed_seconds(repo: Path, clock: FakeTime) -> None:
    clock.now = 1000.0 + 36000.6
    job = remote._Job(make_context(repo), "py.mutation", build, repo, 4, None, None, ())
    assert remote._record_job(box_settings(), job, 1000.0, "ok") == pytest.approx(36000.6)
    assert without_stamp(usage_lines()[0]) == job_line(repo, "ok", 36001, 12.0)


def test_wait_for_stop_gives_up_after_sixty_polls(monkeypatch: pytest.MonkeyPatch, clock: FakeTime) -> None:
    calls: list[str] = []
    monkeypatch.setattr(remote, "describe", lambda found: [*noted(calls, found.instance), ("STOPPING", "")][-1])
    assert remote._wait_for_stop(box_settings()) == ("STOPPING", "")
    assert calls == [INSTANCE] * 61
    assert clock.slept == [5] * 60


def test_ensure_up_reports_a_box_with_no_address(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(remote, "describe", lambda _found: ("RUNNING", ""))
    assert remote.ensure_up(box_settings()) == (None, "instance is RUNNING")
    assert remote.LOCK.exists()


def test_ensure_up_reports_an_odd_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(remote, "describe", lambda _found: ("REPAIRING", IP))
    assert remote.ensure_up(box_settings()) == (None, "instance is REPAIRING")


def test_ensure_up_returns_the_address_once_ssh_answers(box: FakeBox, clock: FakeTime) -> None:
    box.probe = (0, "")
    assert remote.ensure_up(box_settings()) == (IP, "")
    assert clock.slept == []


def test_a_running_box_is_used_without_the_start_lock(box: FakeBox, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(remote.fcntl, "flock", lambda _handle, _how: pytest.fail("took the start lock"))
    assert remote.ensure_up(box_settings()) == (IP, "")
    assert not remote.LOCK.exists()
    assert box.gcloud_calls == [DESCRIBE]


@pytest.mark.parametrize(("text", "expected"), [("a\nb\n", "b"), ("  \n", "fallback"), ("", "fallback")])
def test_last_line(text: str, expected: str) -> None:
    assert remote._last_line(text, "fallback") == expected


class StatusBox:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, described_as: Reply, jobs: Reply) -> None:
        self.ssh_calls: list[tuple[str, str, int | None]] = []
        self.jobs = jobs
        configure()
        monkeypatch.setattr(remote, "gcloud", lambda _found, _args: described_as)
        monkeypatch.setattr(remote, "ssh", self.ssh)
        monkeypatch.setattr(remote, "uptime_seconds", lambda _project, _instance, _since, _until: None)

    def ssh(self, found: remote.Settings, ip: str, command: str, timeout: int | None = None, tty: bool = False) -> Reply:
        same_box(found)
        self.ssh_calls.append((ip, command, timeout))
        return self.jobs


RUNNING_BOX = described("RUNNING")
UNKNOWN_SPEND = "today: VM up unknown; 0 jobs, 0.00h busy\nthis month: VM up unknown; 0 jobs, 0.00h busy\n"


def test_status_without_a_config_says_so(capsys: pytest.CaptureFixture[str]) -> None:
    assert remote.status_command(None) == 1
    assert capsys.readouterr().out == f"no remote configured ({remote.CONFIG})\n"


def test_status_prints_the_box_its_jobs_and_spend(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    fake = StatusBox(monkeypatch, RUNNING_BOX, (0, "/repo/web stryker 01:02 running\nidle 120 15\n"))
    assert remote.status_command(None) == 0
    assert capsys.readouterr().out == (
        f"{INSTANCE} (proj/europe-west1-b): RUNNING {IP}\noffloaded gates: py.mutation\n  running: stryker for web, 01:02\n" + UNKNOWN_SPEND
    )
    assert fake.ssh_calls == [(IP, remote.JOBS, 30)]


def test_status_skips_jobs_when_the_probe_fails(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    StatusBox(monkeypatch, RUNNING_BOX, (1, "/repo/web stryker 01:02 running\nidle 120 15\n"))
    assert remote.status_command(None) == 0
    assert capsys.readouterr().out == f"{INSTANCE} (proj/europe-west1-b): RUNNING {IP}\noffloaded gates: py.mutation\n" + UNKNOWN_SPEND


def test_status_of_an_unknown_box(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    fake = StatusBox(monkeypatch, (1, "gone"), (0, "idle 1 1"))
    assert remote.status_command(None) == 0
    assert capsys.readouterr().out == f"{INSTANCE} (proj/europe-west1-b): unknown\noffloaded gates: py.mutation\n" + UNKNOWN_SPEND
    assert fake.ssh_calls == []


def test_status_of_a_stopped_box_runs_no_ssh(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    fake = StatusBox(monkeypatch, described("TERMINATED"), (0, "idle 1 1"))
    configure(TOML.replace('gates = ["py.mutation"]', "gates = []"))
    assert remote.status_command(None) == 0
    assert capsys.readouterr().out.splitlines()[:2] == [f"{INSTANCE} (proj/europe-west1-b): TERMINATED {IP}", "offloaded gates: none"]
    assert fake.ssh_calls == []


def test_status_lists_every_offloaded_gate(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    StatusBox(monkeypatch, (1, ""), (0, ""))
    configure(TOML.replace('gates = ["py.mutation"]', 'gates = ["py.mutation", "ts.mutation"]'))
    assert remote.status_command(None) == 0
    assert capsys.readouterr().out.splitlines()[1] == "offloaded gates: py.mutation, ts.mutation"


def test_status_of_an_idle_box(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    StatusBox(monkeypatch, RUNNING_BOX, (0, "idle 120 15\n"))
    assert remote.status_command(None) == 0
    third = capsys.readouterr().out.splitlines()[2]
    assert re.match(r"^  no jobs: idle for 2 min, powers itself off after 15 idle min \(about \d{2}:\d{2}\)$", third)


def test_status_counts_todays_and_this_months_jobs(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(remote, "datetime", FixedDatetime)
    StatusBox(monkeypatch, RUNNING_BOX, (0, "/repo/web stryker 01:02 queued\nidle 120 15\n"))
    stamp = FIXED_NOW.isoformat()
    lines = [
        {"ts": stamp, "event": "job", "seconds": 1800},
        {"ts": stamp, "event": "job", "seconds": 5400},
        {"ts": stamp, "event": "vm-start", "seconds": 99},
        {"ts": "2026-10-10T00:00:10+00:00", "event": "job", "seconds": 360000},
        {"ts": "2026-10-10T00:00:00+00:00", "event": "job", "seconds": 3600},
        {"ts": "2026-10-02T00:00:00", "event": "job"},
        {"ts": "2026-09-30T23:59:59+00:00", "event": "job", "seconds": 7},
        {"ts": "2000-01-01T00:00:00+00:00", "event": "job", "seconds": 3600},
    ]
    remote.USAGE.parent.mkdir(parents=True, exist_ok=True)
    remote.USAGE.write_text("".join(json.dumps(line) + "\n\n" for line in lines))
    assert remote.status_command(None) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[2] == "  running: stryker for web, 01:02 (queued)"
    assert out[-2:] == ["today: VM up unknown; 4 jobs, 103.00h busy", "this month: VM up unknown; 5 jobs, 103.00h busy"]
    assert len(out) == 5


def test_status_counts_a_job_without_seconds_as_none(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(remote, "datetime", FixedDatetime)
    StatusBox(monkeypatch, (1, ""), (0, ""))
    lines = [{"ts": FIXED_NOW.isoformat(), "event": "job", "seconds": 17}, *[{"ts": FIXED_NOW.isoformat(), "event": "job"}] * 2]
    remote.USAGE.parent.mkdir(parents=True, exist_ok=True)
    remote.USAGE.write_text("".join(json.dumps(line) + "\n" for line in lines))
    assert remote.status_command(None) == 0
    assert capsys.readouterr().out.splitlines()[-2] == "today: VM up unknown; 3 jobs, 0.00h busy"


def body(payload: object) -> bytes:
    return json.dumps(payload).encode()


class FakeMonitoring:
    def __init__(self, reply: bytes | BaseException) -> None:
        self.reply = reply
        self.requests: list[tuple[urllib.request.Request, int]] = []

    def __call__(self, request: urllib.request.Request, timeout: int) -> io.BytesIO:
        self.requests.append((request, timeout))
        if isinstance(self.reply, BaseException):
            raise self.reply
        return io.BytesIO(self.reply)


UPTIME = body({"timeSeries": [{"points": [{"value": {"doubleValue": 3600.0}}, {"value": {"doubleValue": 1800.0}}]}]})


@pytest.fixture
def priced(monkeypatch: pytest.MonkeyPatch, fake_subprocess: Any) -> Iterator[Callable[[bytes | BaseException], FakeMonitoring]]:
    configure()
    monkeypatch.setattr(remote, "datetime", FixedDatetime)
    monkeypatch.setattr(remote, "gcloud", lambda _found, _args: RUNNING_BOX)
    monkeypatch.setattr(remote, "ssh", lambda *_args, **_options: (0, "idle 120 15"))
    remote.USAGE.parent.mkdir(parents=True, exist_ok=True)
    line = {"ts": FIXED_NOW.isoformat(), "event": "job", "instance": "old-box", "seconds": 1800, "usd_per_hour": 0.5}
    remote.USAGE.write_text(json.dumps(line) + "\n")
    token = fake_subprocess(Completed(returncode=0, stdout="tok\n", stderr=""))

    def install(reply: bytes | BaseException) -> FakeMonitoring:
        fake = FakeMonitoring(reply)
        monkeypatch.setattr("marestail.remote.urllib.request.urlopen", fake)
        return fake

    yield install
    assert all(
        call == (["gcloud", "auth", "print-access-token"], {"capture_output": True, "text": True, "check": False}) for call in token.calls
    )


def query(request: urllib.request.Request) -> tuple[str, dict[str, list[str]]]:
    base, _, params = request.full_url.partition("?")
    return base, urllib.parse.parse_qs(params)


def test_status_prices_uptime_per_instance(priced: Any, capsys: pytest.CaptureFixture[str]) -> None:
    fake = priced(UPTIME)
    assert remote.status_command(None) == 0
    assert capsys.readouterr().out.splitlines()[-2:] == [
        "today: VM up 3.00h = $2.55; 1 jobs, 0.50h busy",
        "this month: VM up 3.00h = $2.55; 1 jobs, 0.50h busy",
    ]
    assert [timeout for _request, timeout in fake.requests] == [20, 20, 20, 20]
    assert [request.header_items() for request, _timeout in fake.requests] == [[("Authorization", "Bearer tok")]] * 4
    assert remote.AUTHORIZATION == "Authorization"
    queries = [query(request) for request, _timeout in fake.requests]
    assert {base for base, _params in queries} == {"https://monitoring.googleapis.com/v3/projects/proj/timeSeries"}
    expected = [
        ("old-box", "2026-10-10T00:00:00Z", "45299s"),
        (INSTANCE, "2026-10-10T00:00:00Z", "45299s"),
        ("old-box", "2026-10-01T00:00:00Z", "822899s"),
        (INSTANCE, "2026-10-01T00:00:00Z", "822899s"),
    ]
    assert [uptime_query(instance, start, period) for instance, start, period in expected] == [params for _base, params in queries]


def uptime_query(instance: str, start: str, period: str) -> dict[str, list[str]]:
    return {
        "filter": [f'metric.type="compute.googleapis.com/instance/uptime" AND metric.labels.instance_name="{instance}"'],
        "interval.startTime": [start],
        "interval.endTime": ["2026-10-10T12:34:59.500000Z"],
        "aggregation.alignmentPeriod": [period],
        "aggregation.perSeriesAligner": ["ALIGN_SUM"],
    }


@pytest.mark.parametrize("reply", [OSError("down"), b"not json"])
def test_status_spend_is_unknown_when_monitoring_fails(
    priced: Any, capsys: pytest.CaptureFixture[str], reply: bytes | BaseException
) -> None:
    fake = priced(reply)
    assert remote.status_command(None) == 0
    assert capsys.readouterr().out.splitlines()[-2:] == [
        "today: VM up unknown; 1 jobs, 0.50h busy",
        "this month: VM up unknown; 1 jobs, 0.50h busy",
    ]
    assert len(fake.requests) == 4


def test_uptime_without_a_token_is_unknown(fake_subprocess: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeMonitoring(UPTIME)
    monkeypatch.setattr("marestail.remote.urllib.request.urlopen", fake)
    fake_subprocess(Completed(returncode=1, stdout=" \n", stderr="no auth"))
    assert remote.uptime_seconds(PROJECT, INSTANCE, FIXED_NOW - timedelta(hours=1), FIXED_NOW) is None
    assert fake.requests == []


def test_uptime_sums_every_point_of_every_series() -> None:
    data = {"timeSeries": [{"points": [{"value": {"doubleValue": 1.5}}]}, {}, {"points": [{"value": {"doubleValue": 2}}]}]}
    assert remote._total_uptime(data) == 3.5
    assert isinstance(remote._total_uptime({}), float)


def test_uptime_url_aligns_at_least_a_minute() -> None:
    url = remote._uptime_url(PROJECT, INSTANCE, FIXED_NOW - timedelta(seconds=30), FIXED_NOW)
    assert urllib.parse.parse_qs(url.partition("?")[2])["aggregation.alignmentPeriod"] == ["60s"]


def test_billed_is_unknown_if_any_instance_is_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    replies = [None, 3600.0]
    asked: list[str] = []

    def uptime(project: str, instance: str, _since: datetime, _until: datetime) -> float | None:
        asked.append(f"{project}/{instance}")
        return replies.pop(0)

    monkeypatch.setattr(remote, "uptime_seconds", uptime)
    entries = [{"instance": "old-box"}]
    assert remote._billed(box_settings(), entries, FIXED_NOW, FIXED_NOW) == "unknown"
    assert asked == [f"{PROJECT}/old-box", f"{PROJECT}/{INSTANCE}"]


def test_billed_adds_hours_and_dollars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(remote, "uptime_seconds", lambda _project, instance, _since, _until: 360000.0 if instance == INSTANCE else 1800.0)
    entries = [{"instance": "old-box", "usd_per_hour": 2}]
    assert remote._billed(box_settings(), entries, FIXED_NOW, FIXED_NOW) == "100.50h = $121.00"


def test_instances_keep_the_order_and_the_configured_rate() -> None:
    entries: list[dict[str, Any]] = [
        {"instance": "b", "usd_per_hour": "0.5"},
        {"event": "job"},
        {"instance": INSTANCE, "usd_per_hour": 9},
        {"instance": "c"},
        {"instance": ""},
    ]
    assert list(remote._instances(box_settings(), entries).items()) == [("b", 0.5), (INSTANCE, 1.2), ("c", 1.2)]


def test_when_reads_naive_stamps_as_utc() -> None:
    assert remote._when({"ts": "2026-01-02T03:04:05"}) == datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    aware = remote._when({"ts": "2026-01-02T03:04:05+02:00"})
    assert aware.utcoffset() == timedelta(hours=2)


def test_usage_without_a_file_is_empty() -> None:
    assert remote._usage() == []


def test_usage_skips_blank_lines() -> None:
    remote.USAGE.parent.mkdir(parents=True, exist_ok=True)
    remote.USAGE.write_text('{"a": 1}\n  \n{"b": 2}\n')
    assert remote._usage() == [{"a": 1}, {"b": 2}]


def jobs_box(monkeypatch: pytest.MonkeyPatch, reply: Reply) -> list[tuple[str, str, int | None]]:
    calls: list[tuple[str, str, int | None]] = []

    def ssh(found: remote.Settings, ip: str, command: str, timeout: int | None = None, tty: bool = False) -> Reply:
        same_box(found)
        calls.append((ip, command, timeout))
        return reply

    monkeypatch.setattr(remote, "ssh", ssh)
    return calls


def test_running_jobs_list_running_and_queued_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = jobs_box(monkeypatch, (0, "/r/web stryker 01:02 running\nnoise\nfoo 1 2\n/r/api mutmut 3:04 queued\nidle 1 1\n"))
    assert remote._running_jobs(box_settings(), IP) == ["running: stryker for web, 01:02", "running: mutmut for api, 3:04 (queued)"]
    assert calls == [(IP, remote.JOBS, 30)]


def test_running_jobs_fail_quietly(monkeypatch: pytest.MonkeyPatch) -> None:
    jobs_box(monkeypatch, (255, "/r/web stryker 01:02 running"))
    assert remote._running_jobs(box_settings(), IP) == []


def test_running_jobs_without_jobs_or_idle_line(monkeypatch: pytest.MonkeyPatch) -> None:
    jobs_box(monkeypatch, (0, "idle 1\n"))
    assert remote._running_jobs(box_settings(), IP) == []


def test_running_jobs_use_the_last_idle_line(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(remote, "datetime", FixedDatetime)
    jobs_box(monkeypatch, (0, "idle 60 1\nidle 1200 15\n"))
    assert remote._running_jobs(box_settings(), IP) == [remote._idle_line(1200, 15)]


def test_idle_line_says_when_the_box_powers_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(remote, "datetime", FixedDatetime)
    off = datetime(2026, 10, 10, 12, 47, tzinfo=UTC).astimezone()
    assert remote._idle_line(120, 15) == f"no jobs: idle for 2 min, powers itself off after 15 idle min (about {off:%H:%M})"
    now = datetime(2026, 10, 10, 12, 34, tzinfo=UTC).astimezone()
    assert remote._idle_line(3000, 15) == f"no jobs: idle for 50 min, powers itself off after 15 idle min (about {now:%H:%M})"


def test_run_streamed_without_a_box_runs_here_with_the_shims_off(
    tmp_path: Path, capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", f"{remote.SHIMS}:{os.environ['PATH']}")
    command = ["sh", "-c", 'echo "$MARESTAIL_NO_OFFLOAD $(pwd)"; case ":$PATH:" in *shims*) echo shimmed;; esac; exit 3']
    outcome = remote.run_streamed(make_context(tmp_path), "offload", lambda _workers: command, tmp_path, ())
    assert outcome == remote.Outcome(3, "", "")
    assert capfd.readouterr().out == f"1 {tmp_path}\n"


def test_run_streamed_goes_to_the_box_under_never_whatever_the_switches_say(
    box: FakeBox, repo: Path, popen: Any, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    configure(NEVER)
    monkeypatch.setenv(remote.ENV, "0")
    calls = popen(FakeProcess(["PASS 1 test\n", "marestail-exit 0\n"]))
    ctx = make_context(repo, {"remote": {"enabled": False}})
    outcome = remote.run_streamed(ctx, "offload", lambda _workers: ["pnpm", "test"], repo, ())
    assert outcome == remote.Outcome(0, "", f" (on {INSTANCE}, 0s)")
    assert len(calls) == 1
    assert capsys.readouterr() == (
        "PASS 1 test\n",
        f"MARESTAIL_REMOTE=0 ignored: {POLICY}\n[remote] enabled = false in {repo}/marestail.toml ignored: {POLICY}\n",
    )


def test_passthrough_reports_a_missing_command(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert remote._passthrough(["/nonexistent/tool"], tmp_path, {}) == 127
    assert capsys.readouterr().err == "/nonexistent/tool: not found\n"


def test_run_streamed_offloads_even_an_unlisted_gate_and_pulls_back(
    box: FakeBox, repo: Path, popen: Any, home: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(remote, "uuid", SimpleNamespace(uuid4=lambda: SimpleNamespace(hex="abc")))
    calls = popen(FakeProcess(["PASS 3 tests\n", "\n", "marestail-exit 0\n"]))
    outcome = remote.run_streamed(make_context(repo), "offload", lambda _workers: ["pnpm", "test"], repo, ("coverage",))
    assert outcome == remote.Outcome(0, "", f" (on {INSTANCE}, 0s)")
    assert capsys.readouterr().out == "PASS 3 tests\n"
    ((command, _options),) = calls
    assert command[-1].startswith("echo $$ > /var/lib/marestail/busy.d/abc;")
    assert f"env -C {repo} " in command[-1]
    assert "flock /var/lib/marestail/job.lock" not in command[-1]
    assert "timeout -k 30 7200 pnpm test </dev/null" in command[-1]
    assert box.rsync_calls[-1] == [f"max@{IP}:{repo}/coverage/", f"{repo}/coverage/"]
    assert without_stamp(usage_lines()[0])["gate"] == "offload"


def test_prepare_installs_node_modules_only_when_the_lock_changes() -> None:
    assert remote.PREPARE == (
        "if [ -f package-lock.json ]; then lock=$(sha256sum package-lock.json | cut -c1-16); "
        'if [ "$(cat node_modules/.marestail-lock 2>/dev/null)" != "$lock" ]; then '
        'npm ci --no-audit --no-fund --loglevel=error >&2 && echo "$lock" > node_modules/.marestail-lock || exit 97; fi; '
        "elif [ -f pnpm-lock.yaml ]; then lock=$(sha256sum pnpm-lock.yaml | cut -c1-16); "
        'if [ "$(cat node_modules/.marestail-lock 2>/dev/null)" != "$lock" ]; then '
        "want=$(node -p \"(require('./package.json').packageManager || 'pnpm@latest').split('@')[1]\"); "
        'out=$(npx --yes "pnpm@$want" install --frozen-lockfile --reporter=append-only 2>&1) '
        '&& echo "$lock" > node_modules/.marestail-lock || { printf "%s\\n" "$out" >&2; exit 97; }; '
        "fi; fi"
    )


def test_prepare_without_a_lockfile_or_tooling_is_nothing(tmp_path: Path) -> None:
    assert remote._prepare(make_context(tmp_path), tmp_path) == ""


def test_prepare_with_tooling_also_installs_the_tooling_folder(home: Path, tmp_path: Path) -> None:
    root = tmp_path / "my repo"
    root.mkdir()
    (root / "package-lock.json").write_text("{}")
    ctx = make_context(root, {"ts": {"tooling": ".marestail/tooling"}})
    folder = shlex.quote(f"{root}/.marestail/tooling")
    digest = hashlib.sha256(str(root).encode()).hexdigest()[:16]
    assert remote._prepare(ctx, root) == (
        f"{{ flock 9; ( cd {shlex.quote(str(root))} && {remote.PREPARE} ) || {failed(root)}; "
        f"if [ -f {folder}/package-lock.json ]; then lock=$(sha256sum {folder}/package-lock.json | cut -c1-16); "
        f'if [ "$(cat {folder}/node_modules/.marestail-lock 2>/dev/null)" != "$lock" ]; then '
        f'npm ci --prefix {folder} --no-audit --no-fund --loglevel=error >&2 && echo "$lock" > {folder}/node_modules/.marestail-lock || exit 97; '
        f"fi; fi; }} 9>{home}/.cache/marestail-jobs/prepare-{digest}.lock"
    )


def test_prepare_with_tooling_alone(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"ts": {"tooling": "t"}})
    assert remote._prepare(ctx, tmp_path).startswith(f"{{ flock 9; if [ -f {tmp_path}/t/package-lock.json ]")


def test_the_remote_module_has_no_offloads_helper() -> None:
    assert not hasattr(remote, "offloads")


def noted(calls: list[Any], entry: Any) -> list[Any]:
    calls.append(entry)
    return []
