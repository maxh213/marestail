import io
import json
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
        self.probe: Reply = (0, "")
        self.jobs: list[Reply] = [(0, "done")]
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
            return self.start
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        return described(status)

    def ssh(self, found: remote.Settings, ip: str, command: str, timeout: int | None = None, tty: bool = False) -> Reply:
        same_box(found)
        self.ssh_calls.append((ip, command, timeout, tty))
        if JOB_LOCK in command:
            self.clock.now += self.job_seconds
            return self.jobs.pop(0)
        return self.probe if command == PROBE else (0, "")

    def rsync(self, found: remote.Settings, args: list[str]) -> Reply:
        same_box(found)
        self.rsync_calls.append(args)
        self.on_fetch(args)
        return self.sync if args[-1].endswith("/repo/") else (0, "")

    def job_commands(self) -> list[str]:
        return [command for _ip, command, _timeout, _tty in self.ssh_calls if JOB_LOCK in command]


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
    return remote.run_mutation(ctx, "py.mutation", build, repo, 4, env={"A": "1"}, timeout=7200, pull=("mutants",))


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
    return [{"cwd": repo, "env": {"A": "1"}, "timeout": 7200}]


def gate_ctx(repo: Path) -> Any:
    return make_context(repo, {"python": {"mutation_scope": "all"}})


def test_start_failure_runs_locally_and_names_the_gcloud_error(box: FakeBox, mutmut: FakeRun, repo: Path) -> None:
    box.statuses = ["TERMINATED"]
    box.start = (1, "WARNING: retrying\nPERMISSION_DENIED\n")
    assert offload(repo) == remote.Outcome(0, "local mutmut", " (remote unavailable: PERMISSION_DENIED; ran locally)")
    assert mutmut.calls == [["mutmut", "run", "--max-children", "4"]]
    assert mutmut.options == local_call(repo)
    assert box.gcloud_calls == [DESCRIBE, START]
    assert box.ssh_calls == []
    assert not remote.USAGE.exists()


def test_start_failure_through_the_python_gate(box: FakeBox, mutmut: FakeRun, repo: Path) -> None:
    box.statuses = ["TERMINATED"]
    box.start = (1, "PERMISSION_DENIED")
    result = py_mutation.run_gate(gate_ctx(repo))
    assert result.ok is True
    assert result.summary == "all 2 mutants killed (1 killed by timeout) (remote unavailable: PERMISSION_DENIED; ran locally)"
    assert mutmut.calls == [[str(repo / ".venv" / "bin" / "mutmut"), "run", "--max-children", "4"]]
    assert not remote.USAGE.exists()


@pytest.mark.parametrize(("status", "verb", "args"), [("STOPPED", "start", START), ("SUSPENDED", "resume", RESUME)])
def test_a_silent_power_on_failure_names_the_verb(
    box: FakeBox, mutmut: FakeRun, repo: Path, status: str, verb: str, args: list[str]
) -> None:
    box.statuses = [status]
    box.start = (1, " \n")
    assert offload(repo).where == f" (remote unavailable: {verb} failed; ran locally)"
    assert mutmut.calls == [["mutmut", "run", "--max-children", "4"]]
    assert box.gcloud_calls == [DESCRIBE, args]


def test_a_terminated_box_is_started_and_the_start_is_recorded(box: FakeBox, mutmut: FakeRun, repo: Path) -> None:
    box.statuses = ["TERMINATED", "RUNNING"]
    assert offload(repo) == remote.Outcome(0, "done", f" (on {INSTANCE}, 0s)")
    assert mutmut.calls == []
    assert box.gcloud_calls == [DESCRIBE, START, DESCRIBE]
    first, second = usage_lines()
    assert list(first) == ["ts", "event", "instance", "usd_per_hour"]
    assert without_stamp(first) == {"event": "vm-start", "instance": INSTANCE, "usd_per_hour": 1.2}
    assert without_stamp(second) == job_line(repo, "ok")


def test_a_suspended_box_is_resumed_not_started(box: FakeBox, mutmut: FakeRun, repo: Path) -> None:
    box.statuses = ["SUSPENDED", "RUNNING"]
    assert offload(repo).where == f" (on {INSTANCE}, 0s)"
    assert box.gcloud_calls == [DESCRIBE, RESUME, DESCRIBE]
    assert without_stamp(usage_lines()[0])["event"] == "vm-start"


@pytest.mark.parametrize("stopping", ["STOPPING", "SUSPENDING"])
def test_a_stopping_box_is_waited_for_then_started(box: FakeBox, mutmut: FakeRun, repo: Path, clock: FakeTime, stopping: str) -> None:
    box.statuses = [stopping, stopping, "TERMINATED", "RUNNING"]
    assert offload(repo) == remote.Outcome(0, "done", f" (on {INSTANCE}, 0s)")
    assert clock.slept == [5]
    assert box.gcloud_calls == [DESCRIBE, DESCRIBE, DESCRIBE, START, DESCRIBE]
    assert mutmut.calls == []


def test_boot_wait_is_four_minutes() -> None:
    assert remote.BOOT_WAIT == 240


def test_ssh_that_never_comes_up_runs_locally(
    box: FakeBox, mutmut: FakeRun, repo: Path, clock: FakeTime, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(remote, "BOOT_WAIT", 10)
    box.probe = (255, "")
    assert offload(repo).where == " (remote unavailable: ssh never came up; ran locally)"
    assert box.ssh_calls == [(IP, PROBE, 20, False), (IP, PROBE, 20, False)]
    assert clock.slept == [5, 5]
    assert mutmut.calls == [["mutmut", "run", "--max-children", "4"]]


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
        "npm ci --no-audit --no-fund --loglevel=error",
        f"docker exec -w {repo} -e PATH=",
        " -e A=1 mt setsid",
        "timeout -k 30 7200 mutmut run --max-children 8;",
    ):
        assert part in job
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


def test_a_gate_that_is_not_offloaded_runs_locally(box: FakeBox, mutmut: FakeRun, repo: Path) -> None:
    configure(TOML.replace('gates = ["py.mutation"]', 'gates = ["ts.mutation"]'))
    assert offload(repo) == remote.Outcome(0, "local mutmut", "")
    assert mutmut.calls == [["mutmut", "run", "--max-children", "4"]]
    assert mutmut.options == local_call(repo)
    assert (box.gcloud_calls, box.ssh_calls, box.rsync_calls) == ([], [], [])
    assert not remote.USAGE.exists()


@pytest.mark.parametrize(("reply", "problem"), [((1, "warning\nconnection reset\n"), "connection reset"), ((23, "  "), "rsync exit 23")])
def test_a_failed_sync_runs_locally(box: FakeBox, mutmut: FakeRun, repo: Path, reply: Reply, problem: str) -> None:
    box.sync = reply
    assert offload(repo) == remote.Outcome(0, "local mutmut", f" (remote sync failed: {problem}; ran locally)")
    assert mutmut.calls == [["mutmut", "run", "--max-children", "4"]]
    assert box.job_commands() == []
    assert not remote.USAGE.exists()


def test_a_drop_without_the_box_coming_back_fails(box: FakeBox, mutmut: FakeRun, repo: Path) -> None:
    box.statuses = ["RUNNING", None]
    box.jobs = [(255, "")]
    assert offload(repo) == remote.Outcome(
        255, "", f" (connection to {INSTANCE} dropped mid-run and it did not come back: instance is unknown)"
    )
    assert mutmut.calls == []
    assert len(box.job_commands()) == 1
    (line,) = usage_lines()
    assert without_stamp(line) == job_line(repo, "dropped")


def test_a_drop_through_the_python_gate_fails(box: FakeBox, mutmut: FakeRun, repo: Path) -> None:
    box.statuses = ["RUNNING", None]
    box.jobs = [(255, "")]
    result = py_mutation.run_gate(gate_ctx(repo))
    assert result.ok is False
    assert result.summary == f"mutmut failed (connection to {INSTANCE} dropped mid-run and it did not come back: instance is unknown)"
    assert mutmut.calls == []


def test_a_second_drop_is_not_rerun_locally(box: FakeBox, mutmut: FakeRun, repo: Path) -> None:
    box.jobs = [(255, ""), (255, " \n")]
    assert offload(repo) == remote.Outcome(255, " \n", f" (connection to {INSTANCE} dropped twice mid-run; not rerun locally)")
    assert len(box.job_commands()) == 2
    assert mutmut.calls == []
    (line,) = usage_lines()
    assert without_stamp(line) == job_line(repo, "dropped")


def test_a_drop_that_recovers_reruns_on_the_box(box: FakeBox, mutmut: FakeRun, repo: Path) -> None:
    box.jobs = [(255, ""), (0, "done")]
    assert offload(repo) == remote.Outcome(0, "done", f" (on {INSTANCE}, 0s)")
    assert len(box.job_commands()) == 2
    assert box.gcloud_calls == [DESCRIBE, DESCRIBE]
    assert {call[0] for call in box.ssh_calls} == {IP}
    assert box.rsync_calls[-1] == [f"max@{IP}:{repo}/mutants/", f"{repo}/mutants/"]
    assert mutmut.calls == []
    (line,) = usage_lines()
    assert without_stamp(line) == job_line(repo, "ok")


def test_run_mutation_defaults_to_two_hours_on_the_box(box: FakeBox, mutmut: FakeRun, repo: Path) -> None:
    assert remote.run_mutation(make_context(repo), "py.mutation", build, repo, 4) == remote.Outcome(0, "done", f" (on {INSTANCE}, 0s)")
    (job,) = box.job_commands()
    assert "timeout -k 30 7200 mutmut" in job
    assert box.ssh_calls[-1][2:] == (21720, True)
    assert box.rsync_calls == [[*PY_EXCLUDES, f"{repo}/", f"max@{IP}:{repo}/"]]


def test_run_mutation_defaults_to_two_hours_locally(box: FakeBox, mutmut: FakeRun, repo: Path) -> None:
    configure(TOML.replace('gates = ["py.mutation"]', "gates = []"))
    assert remote.run_mutation(make_context(repo), "py.mutation", build, repo, 4) == remote.Outcome(0, "local mutmut", "")
    assert mutmut.options == [{"cwd": repo, "env": None, "timeout": 7200}]


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


@pytest.mark.parametrize("value", ["0", "off", "false", "no", "OFF", "No"])
def test_a_switched_off_remote_ignores_the_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    configure()
    monkeypatch.setenv(remote.ENV, value)
    assert remote._settings(make_context(tmp_path)) is None


def test_settings_load_the_config_when_remote_is_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    configure()
    monkeypatch.setenv(remote.ENV, "1")
    assert remote._settings(make_context(tmp_path)) == box_settings(key=home / ".ssh" / "google_compute_engine")


def test_a_repo_can_switch_remote_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    configure()
    monkeypatch.delenv(remote.ENV)
    assert remote._settings(make_context(tmp_path, {"remote": {"enabled": False}})) is None
    assert remote._settings(make_context(tmp_path, {"remote": {"enabled": 0}})) is not None


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
    )
    loaded = remote._load()
    assert loaded is not None
    assert (loaded.container, loaded.key, loaded.workers, loaded.env, loaded.usd_per_hour) == ("c", home / "k", {"g": 3}, {"A": "1"}, 2.0)
    assert loaded.user == "bob"
    assert isinstance(loaded.usd_per_hour, float)


def test_the_gates_variable_overrides_the_config(monkeypatch: pytest.MonkeyPatch) -> None:
    configure()
    monkeypatch.setenv(remote.GATES, "ts.mutation,ex.mutation")
    loaded = remote._load()
    assert loaded is not None
    assert loaded.gates == ("ts.mutation", "ex.mutation")
    monkeypatch.setenv(remote.GATES, "")
    assert remote._offloaded_gates({"gates": ["a"]}) == ("a",)


def test_a_box_without_a_count_for_the_gate_keeps_the_local_workers(box: FakeBox, mutmut: FakeRun, repo: Path) -> None:
    configure(TOML.replace('workers = { "py.mutation" = 8 }', 'workers = { "ts.mutation" = 8 }'))
    assert remote.run_mutation(make_context(repo), "py.mutation", build, repo, 3).where == f" (on {INSTANCE}, 0s)"
    (job,) = box.job_commands()
    assert "mutmut run --max-children 3;" in job
    assert mutmut.calls == []


def test_a_local_run_builds_with_the_local_workers(box: FakeBox, mutmut: FakeRun, repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(remote.ENV, "off")
    assert remote.run_mutation(make_context(repo), "py.mutation", build, repo, 3) == remote.Outcome(0, "local mutmut", "")
    assert mutmut.calls == [["mutmut", "run", "--max-children", "3"]]


@pytest.mark.parametrize(("code", "output", "expected"), [(255, "", True), (255, " \n", True), (255, "x", False), (254, "", False)])
def test_dropped_means_255_and_no_output(code: int, output: str, expected: bool) -> None:
    assert remote._dropped(code, output) is expected


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


def test_execute_queues_the_job_in_the_container(home: Path, recorder: Recorder, monkeypatch: pytest.MonkeyPatch, job_id: str) -> None:
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    remote._execute(box_settings(), IP, ["mutmut", "run", "x y"], Path("/repo/a b"), {"A": "1"}, 100, "prepare")
    session = f"{home}/.cache/marestail-jobs/job-abc"
    inner = (
        f"mkdir -p -m 700 {home}/.cache/marestail-jobs && echo $$ > {session}; prepare; "
        f"timeout -k 30 100 mutmut run 'x y'; code=$?; rm -f {session}; exit $code"
    )
    docker = (
        f"docker exec -w '/repo/a b' -e PATH=/usr/bin:/bin -e HOME={home} -e LANG=C.UTF-8 -e TERM=dumb -e NO_COLOR=1 -e A=1 "
        f"mt setsid -w bash -c {shlex.quote(inner)}"
    )
    stop = f"[ -f {session} ] && pkill -9 -s $(cat {session}); rm -f {session}"
    cleanup = "sg docker -c " + shlex.quote("docker exec mt bash -c " + shlex.quote(stop))
    script = (
        f"echo $$ > /var/lib/marestail/busy.d/abc; stop() {{ {cleanup} >/dev/null 2>&1; rm -f /var/lib/marestail/busy.d/abc; }}; "
        "trap 'stop; exit 129' HUP INT TERM; "
        f"flock /var/lib/marestail/job.lock sg docker -c {shlex.quote(docker)} & wait $!; code=$?; "
        "rm -f /var/lib/marestail/busy.d/abc; touch /var/lib/marestail/last; exit $code"
    )
    assert recorder.ssh_calls == [(IP, script, 420, True)]


def test_execute_without_a_timeout_runs_unbounded(recorder: Recorder, job_id: str) -> None:
    remote._execute(box_settings(), IP, ["mutmut"], Path("/repo"), None, None, remote.PREPARE)
    ((_ip, script, timeout, tty),) = recorder.ssh_calls
    assert (timeout, tty) == (None, True)
    assert "timeout -k" not in script
    assert "; mutmut; code=$?" in script


def test_env_flags_let_the_box_and_the_caller_override(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "/bin")
    found = box_settings(env={"TERM": "xterm", "A": "1"})
    assert remote._env_flags(found, {"A": "2", "B": "x y"}) == (
        f"-e PATH=/bin -e HOME={home} -e LANG=C.UTF-8 -e TERM=xterm -e NO_COLOR=1 -e A=2 -e 'B=x y'"
    )
    monkeypatch.delenv("PATH")
    assert remote._env_flags(box_settings(), None).startswith("-e PATH= -e HOME=")


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


def test_prepare_installs_node_modules_only_when_the_lock_changes() -> None:
    assert remote.PREPARE == (
        "if [ -f package-lock.json ]; then lock=$(sha256sum package-lock.json | cut -c1-16); "
        'if [ "$(cat node_modules/.marestail-lock 2>/dev/null)" != "$lock" ]; then '
        'npm ci --no-audit --no-fund --loglevel=error >&2 && echo "$lock" > node_modules/.marestail-lock || exit 97; fi; '
        "elif [ -f pnpm-lock.yaml ]; then lock=$(sha256sum pnpm-lock.yaml | cut -c1-16); "
        'if [ "$(cat node_modules/.marestail-lock 2>/dev/null)" != "$lock" ]; then '
        "want=$(node -p \"(require('./package.json').packageManager || 'pnpm@latest').split('@')[1]\"); "
        'npx --yes "pnpm@$want" install --frozen-lockfile --reporter=silent >&2 && echo "$lock" > node_modules/.marestail-lock || exit 97; '
        "fi; fi"
    )


def test_prepare_without_tooling_installs_only_the_repo() -> None:
    assert remote._prepare(make_context(Path("/repo"))) == remote.PREPARE


def test_prepare_with_tooling_also_installs_the_tooling_folder() -> None:
    ctx = make_context(Path("/my repo"), {"ts": {"tooling": ".marestail/tooling"}})
    folder = "'/my repo/.marestail/tooling'"
    assert remote._prepare(ctx) == (
        f"{remote.PREPARE}; if [ -f {folder}/package-lock.json ]; then lock=$(sha256sum {folder}/package-lock.json | cut -c1-16); "
        f'if [ "$(cat {folder}/node_modules/.marestail-lock 2>/dev/null)" != "$lock" ]; then '
        f'npm ci --prefix {folder} --no-audit --no-fund --loglevel=error >&2 && echo "$lock" > {folder}/node_modules/.marestail-lock || exit 97; '
        "fi; fi"
    )


def test_the_remote_module_has_no_offloads_helper() -> None:
    assert not hasattr(remote, "offloads")


def noted(calls: list[Any], entry: Any) -> list[Any]:
    calls.append(entry)
    return []
