#!/usr/bin/env python3
import importlib
import inspect
import io
import json
import os
import shutil
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import harness

scratch = Path(tempfile.mkdtemp())
os.environ["HOME"] = str(scratch / "home")
os.environ.pop("MARESTAIL_REMOTE", None)
os.environ.pop("MARESTAIL_REMOTE_GATES", None)

harness.use_tree()

from marestail.config import Config
from marestail.context import Context

TARGETS = [
    "remote.run_mutation local",
    "remote.run_mutation not offloaded",
    "remote.run_mutation offloaded",
    "remote.run_mutation dropped once",
    "remote.ensure_up running",
    "remote.status_command",
    "remote.status_command idle",
    "remote.uptime_seconds",
]
GATE = "py.mutation"
INSTANCE = "marestail-mutation"
USAGE_LINES = 500
SERIES = 24
POINTS = 60
JOB_ROWS = 6


def load_remote() -> Any:
    try:
        return importlib.import_module("marestail.remote")
    except ImportError:
        return None


remote = load_remote()
real_uptime = [getattr(remote, "uptime_seconds", None)]
repo = scratch / "repo"
repo.mkdir(parents=True)
config_dir = scratch / "home" / ".config" / "marestail"
config_dir.mkdir(parents=True)


def remote_toml(gates: list[str]) -> str:
    listed = ", ".join(f'"{gate}"' for gate in gates)
    return (
        "[remote]\n"
        f'instance = "{INSTANCE}"\nproject = "proj"\nzone = "europe-west1-b"\nuser = "max"\n'
        f"gates = [{listed}]\nusd_per_hour = 1.2\n"
        f'[remote.workers]\n"{GATE}" = 16\n'
        '[remote.env]\nMIX_ENV = "test"\n'
    )


def stamp(offset: int) -> str:
    return (datetime.now(UTC) - timedelta(minutes=offset)).isoformat(timespec="seconds")


def usage_text() -> str:
    lines = []
    for index in range(USAGE_LINES):
        instance = INSTANCE if index % 5 else "old-box"
        if index % 25 == 0:
            lines.append({"ts": stamp(index), "event": "vm-start", "instance": instance, "usd_per_hour": 0.5})
            continue
        lines.append(
            {
                "ts": stamp(index * 90),
                "event": "job",
                "instance": instance,
                "gate": GATE,
                "repo": str(repo),
                "result": "ok",
                "seconds": 600 + index,
                "usd_per_hour": 1.2,
                "usd": 0.2,
            }
        )
    return "".join(json.dumps(line) + "\n" for line in lines)


DESCRIBED = json.dumps({"status": "RUNNING", "networkInterfaces": [{"accessConfigs": [{"natIP": "1.2.3.4"}]}]})
BUSY_PROBE = "".join(f"/repo/web{index} stryker 01:0{index} {'queued' if index % 2 else 'running'}\n" for index in range(JOB_ROWS)) + "idle 120 15\n"
IDLE_PROBE = "idle 120 15\n"
probe = {"output": BUSY_PROBE}
drops = {"left": 0}


def fake_gcloud(_found: Any, args: list[str]) -> tuple[int, str]:
    if "describe" in args:
        return 0, DESCRIBED
    return 0, ""


def fake_ssh(_found: Any, _ip: str, command: str, timeout: int | None = None, tty: bool = False) -> tuple[int, str]:
    if "marestail-idle-check" in command:
        return 0, probe["output"]
    if "job.lock" in command:
        if drops["left"]:
            drops["left"] -= 1
            return 255, ""
        return 0, "mutants: 40 killed\n" * 20
    return 0, ""


def fake_rsync(_found: Any, _args: list[str]) -> tuple[int, str]:
    return 0, ""


def fake_run(_command: list[str], cwd: Path | None = None, env: dict[str, str] | None = None, timeout: int | None = None) -> tuple[int, str]:
    return 0, "mutants: 40 killed\n" * 20


def fake_uptime(*_args: Any) -> float:
    return 5400.0


def monitoring_body() -> bytes:
    series = [{"points": [{"value": {"doubleValue": 60.0 + point}} for point in range(POINTS)]} for _ in range(SERIES)]
    return json.dumps({"timeSeries": series}).encode()


BODY = monitoring_body()


class Token:
    stdout = "tok\n"


def fake_token(*_args: Any, **_kwargs: Any) -> Token:
    return Token()


def fake_urlopen(*_args: Any, **_kwargs: Any) -> io.BytesIO:
    return io.BytesIO(BODY)


def builder(command: list[str]) -> Any:
    return lambda _workers: command


def caller() -> Any:
    command = ["mutmut", "run", "marestail.x*", "--max-children", "4"]
    takes_workers = "workers" in inspect.signature(remote.run_mutation).parameters
    pull = ("mutants",)
    if takes_workers:
        return lambda ctx: remote.run_mutation(ctx, GATE, builder(command), repo, 4, timeout=7200, pull=pull)
    return lambda ctx: remote.run_mutation(ctx, GATE, builder(command), repo, timeout=7200, pull=pull)


def offloaded_run(call: Any, ctx: Context) -> Any:
    remote.USAGE.unlink(missing_ok=True)
    return call(ctx)


def dropped_run(call: Any, ctx: Context) -> Any:
    drops["left"] = 1
    return offloaded_run(call, ctx)


def loaded_settings() -> Any:
    load = getattr(remote, "load", None) or remote._load
    return load()


def uptime_call() -> Any:
    uptime_seconds = real_uptime[0]
    names = list(inspect.signature(uptime_seconds).parameters)
    now = datetime.now(UTC)
    since = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if names[0] == "project":
        return lambda: uptime_seconds("proj", INSTANCE, since, now)
    found = loaded_settings()
    return lambda: uptime_seconds(found, since, now)


def measure_uptime() -> None:
    real_run, real_urlopen = remote.subprocess.run, remote.urllib.request.urlopen
    remote.subprocess.run, remote.urllib.request.urlopen = fake_token, fake_urlopen
    try:
        harness.emit("remote.uptime_seconds", harness.measure(uptime_call()))
    finally:
        remote.subprocess.run, remote.urllib.request.urlopen = real_run, real_urlopen


def measure_remote() -> None:
    remote.CONFIG = config_dir / "remote.toml"
    remote.USAGE = config_dir / "remote-usage.jsonl"
    remote.LOCK = config_dir / "remote.lock"
    remote.gcloud, remote.ssh, remote.rsync, remote.run = fake_gcloud, fake_ssh, fake_rsync, fake_run
    ctx = Context(config=Config(root=repo, raw={}))
    call = caller()
    harness.emit("remote.run_mutation local", harness.measure(lambda: call(ctx)))
    remote.CONFIG.write_text(remote_toml(["ts.mutation"]))
    harness.emit("remote.run_mutation not offloaded", harness.measure(lambda: call(ctx)))
    remote.CONFIG.write_text(remote_toml([GATE, "ts.mutation"]))
    harness.emit("remote.run_mutation offloaded", harness.measure(lambda: offloaded_run(call, ctx)))
    harness.emit("remote.run_mutation dropped once", harness.measure(lambda: dropped_run(call, ctx)))
    found = loaded_settings()
    harness.emit("remote.ensure_up running", harness.measure(lambda: remote.ensure_up(found)))
    remote.USAGE.write_text(usage_text())
    remote.uptime_seconds = fake_uptime
    harness.emit("remote.status_command", harness.measure(lambda: remote.status_command(None)))
    probe["output"] = IDLE_PROBE
    harness.emit("remote.status_command idle", harness.measure(lambda: remote.status_command(None)))


try:
    if remote is None:
        for target in TARGETS:
            harness.absent(target)
    else:
        measure_remote()
        measure_uptime()
finally:
    shutil.rmtree(scratch, ignore_errors=True)
