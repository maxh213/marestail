import fcntl
import json
import os
import shlex
import subprocess
import time
import tomllib
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from marestail.context import Context
from marestail.shell import clean, run

FOLDER = Path.home() / ".config" / "marestail"
CONFIG = FOLDER / "remote.toml"
USAGE = FOLDER / "remote-usage.jsonl"
LOCK = FOLDER / "remote.lock"
ENV = "MARESTAIL_REMOTE"
GATES = "MARESTAIL_REMOTE_GATES"
BUSY = "/var/lib/marestail/busy.d"
JOB_LOCK = "/var/lib/marestail/job.lock"
EXCLUDES = (
    "/.git/",
    "/.marestail/",
    ".stryker-tmp/",
    ".elixir_ls/",
    "node_modules/",
    ".next/",
    "_build/",
    ".venv/",
    "cover/",
    "erl_crash.dump",
    "target/",
)
PREPARE = (
    "if [ -f package-lock.json ]; then "
    "lock=$(sha256sum package-lock.json | cut -c1-16); "
    'if [ "$(cat node_modules/.marestail-lock 2>/dev/null)" != "$lock" ]; then '
    'npm ci --no-audit --no-fund --loglevel=error >&2 && echo "$lock" > node_modules/.marestail-lock || exit 97; '
    "fi; fi"
)
BOOT_WAIT = 240
AUTHORIZATION = "Authorization"
JOBS = r"""
for f in /var/lib/marestail/busy.d/*; do
  [ -e "$f" ] || continue
  p=$(cat "$f"); kill -0 "$p" 2>/dev/null || continue
  args=$(tr '\0' ' ' < /proc/$p/cmdline)
  repo=$(echo "$args" | grep -oE 'docker exec -w [^ ]+' | head -1 | awk '{print $4}')
  tool=$(echo "$args" | grep -oE 'muex|stryker|mutmut|pitest' | head -1)
  state=running; pgrep -P "$p" -x flock >/dev/null && ! pgrep -f "docker exec -w $repo" >/dev/null && state=queued
  echo "$repo ${tool:-job} $(ps -o etime= -p $p | tr -d ' ') $state"
done
echo "idle $(( $(date +%s) - $(stat -c %Y /var/lib/marestail/last) )) $(grep -oP '^IDLE_MIN=\K[0-9]+' /usr/local/bin/marestail-idle-check)"
"""


@dataclass(frozen=True)
class Settings:
    instance: str
    project: str
    zone: str
    container: str
    gates: tuple[str, ...]
    workers: dict[str, int]
    env: dict[str, str]
    usd_per_hour: float
    user: str
    key: Path


@dataclass(frozen=True)
class Outcome:
    code: int
    output: str
    where: str


@dataclass(frozen=True)
class _Job:
    ctx: Context
    gate: str
    build: Callable[[int], list[str]]
    cwd: Path
    workers: int
    env: dict[str, str] | None
    timeout: int | None
    pull: tuple[str, ...]


def _settings(ctx: Context) -> Settings | None:
    if ENV in os.environ and os.environ[ENV].lower() in {"0", "off", "false", "no"}:
        return None
    if ctx.config.get("remote", "enabled") is False:
        return None
    return _load()


def _load() -> Settings | None:
    if not CONFIG.exists():
        return None
    raw = tomllib.loads(CONFIG.read_text()).get("remote", {})
    if not raw.get("enabled", True):
        return None
    return _parsed(raw)


def _parsed(raw: dict[str, Any]) -> Settings:
    return Settings(
        instance=raw["instance"],
        project=raw["project"],
        zone=raw["zone"],
        container=raw.get("container", "mt"),
        gates=_offloaded_gates(raw),
        workers={str(k): int(v) for k, v in raw.get("workers", {}).items()},
        env={str(k): str(v) for k, v in raw.get("env", {}).items()},
        usd_per_hour=float(raw.get("usd_per_hour", 0)),
        user=raw.get("user", os.environ.get("USER", "max")),
        key=Path(raw.get("key", "~/.ssh/google_compute_engine")).expanduser(),
    )


def _offloaded_gates(raw: dict[str, Any]) -> tuple[str, ...]:
    if os.environ.get(GATES):
        return tuple(os.environ[GATES].split(","))
    return tuple(raw.get("gates", []))


def run_mutation(
    ctx: Context,
    gate: str,
    build: Callable[[int], list[str]],
    cwd: Path,
    workers: int,
    env: dict[str, str] | None = None,
    timeout: int | None = 7200,
    pull: tuple[str, ...] = (),
) -> Outcome:
    found = _settings(ctx)
    job = _Job(ctx, gate, build, cwd, workers, env, timeout, pull)
    if found is None or gate not in found.gates:
        return _locally(job, "")
    return _offloaded(found, job)


def _offloaded(found: Settings, job: _Job) -> Outcome:
    ip, problem = ensure_up(found)
    if ip is None:
        return _locally(job, f" (remote unavailable: {problem}; ran locally)")
    started = time.time()
    problem = _push(found, ip, job.ctx.root, job.gate)
    if problem:
        return _locally(job, f" (remote sync failed: {problem}; ran locally)")
    reply = _remotely(found, ip, job)
    if _dropped(*reply):
        return _retried(found, job, started, reply)
    return _finished(found, ip, job, started, reply)


def _retried(found: Settings, job: _Job, started: float, reply: tuple[int, str]) -> Outcome:
    ip, problem = ensure_up(found)
    if ip is None:
        _record_job(found, job, started, "dropped")
        return Outcome(*reply, f" (connection to {found.instance} dropped mid-run and it did not come back: {problem})")
    reply = _remotely(found, ip, job)
    if _dropped(*reply):
        _record_job(found, job, started, "dropped")
        return Outcome(*reply, f" (connection to {found.instance} dropped twice mid-run; not rerun locally)")
    return _finished(found, ip, job, started, reply)


def _finished(found: Settings, ip: str, job: _Job, started: float, reply: tuple[int, str]) -> Outcome:
    code, output = reply
    for path in job.pull:
        _fetch(found, ip, job.ctx.root, path)
    seconds = _record_job(found, job, started, "ok" if code == 0 else f"exit {code}")
    return Outcome(code, output, f" (on {found.instance}, {round(seconds)}s)")


def _locally(job: _Job, where: str) -> Outcome:
    return _local(job.build(job.workers), job.cwd, job.env, job.timeout, where)


def _remotely(found: Settings, ip: str, job: _Job) -> tuple[int, str]:
    return _execute(found, ip, job.build(found.workers.get(job.gate, job.workers)), job.cwd, job.env, job.timeout)


def _record_job(found: Settings, job: _Job, started: float, result: str) -> float:
    seconds = time.time() - started
    _record(
        {
            "event": "job",
            "instance": found.instance,
            "gate": job.gate,
            "repo": str(job.ctx.root),
            "result": result,
            "seconds": round(seconds),
            "usd_per_hour": found.usd_per_hour,
            "usd": round(seconds / 3600 * found.usd_per_hour, 3),
        }
    )
    return seconds


def _dropped(code: int, output: str) -> bool:
    return code == 255 and not output.strip()


def _local(command: list[str], cwd: Path, env: dict[str, str] | None, timeout: int | None, where: str) -> Outcome:
    code, output = run(command, cwd=cwd, env=env, timeout=timeout) if timeout else _unbounded(command, cwd, env)
    return Outcome(code, output, where)


def _unbounded(command: list[str], cwd: Path, env: dict[str, str] | None) -> tuple[int, str]:
    completed = subprocess.run(command, cwd=cwd, env={**os.environ, **(env or {})}, capture_output=True, text=True, check=False)
    return completed.returncode, clean(completed.stdout + completed.stderr)


def ensure_up(found: Settings) -> tuple[str | None, str]:
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with LOCK.open("w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        return _booted(found)


def _booted(found: Settings) -> tuple[str | None, str]:
    status, ip = _settled(found)
    if status not in {"TERMINATED", "STOPPED", "SUSPENDED"}:
        return _running(found, status, ip)
    problem = _power_on(found, status)
    if problem:
        return None, problem
    return _running(found, *describe(found))


def _settled(found: Settings) -> tuple[str, str]:
    status, ip = describe(found)
    if status in {"STOPPING", "SUSPENDING"}:
        return _wait_for_stop(found)
    return status, ip


def _power_on(found: Settings, status: str) -> str:
    verb = "resume" if status == "SUSPENDED" else "start"
    code, output = gcloud(found, ["compute", "instances", verb, found.instance])
    if code != 0:
        return _last_line(output, f"{verb} failed")
    _record({"event": "vm-start", "instance": found.instance, "usd_per_hour": found.usd_per_hour})
    return ""


def _running(found: Settings, status: str, ip: str) -> tuple[str | None, str]:
    if status != "RUNNING" or not ip:
        return None, f"instance is {status or 'unknown'}"
    return _reachable(found, ip)


def _reachable(found: Settings, ip: str) -> tuple[str | None, str]:
    deadline = time.time() + BOOT_WAIT
    while time.time() < deadline:
        if ssh(found, ip, "sg docker -c 'docker exec " + found.container + " true'", timeout=20)[0] == 0:
            return ip, ""
        time.sleep(5)
    return None, "ssh never came up"


def _last_line(text: str, fallback: str) -> str:
    return text.strip().splitlines()[-1] if text.strip() else fallback


def _wait_for_stop(found: Settings) -> tuple[str, str]:
    for _ in range(60):
        status, ip = describe(found)
        if status not in {"STOPPING", "SUSPENDING"}:
            return status, ip
        time.sleep(5)
    return describe(found)


def describe(found: Settings) -> tuple[str, str]:
    code, output = gcloud(found, ["compute", "instances", "describe", found.instance, "--format=json"])
    if code != 0:
        return "", ""
    data = json.loads(output)
    configs = data.get("networkInterfaces", [{}])[0].get("accessConfigs") or [{}]
    return data.get("status", ""), configs[0].get("natIP", "")


def gcloud(found: Settings, args: list[str]) -> tuple[int, str]:
    completed = subprocess.run(
        ["gcloud", *args, f"--project={found.project}", f"--zone={found.zone}", "--quiet"],
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode, completed.stdout if completed.returncode == 0 else completed.stderr


def _ssh_options(found: Settings) -> list[str]:
    return [
        "-i",
        str(found.key),
        "-o",
        f"HostKeyAlias={found.instance}",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        f"UserKnownHostsFile={Path.home() / '.ssh' / 'marestail_known_hosts'}",
        "-o",
        "ServerAliveInterval=30",
        "-o",
        "ConnectTimeout=15",
        "-o",
        "BatchMode=yes",
    ]


def ssh(found: Settings, ip: str, remote: str, timeout: int | None = None, tty: bool = False) -> tuple[int, str]:
    hangup = ["-tt", "-q"] if tty else []
    try:
        completed = subprocess.run(
            ["ssh", *hangup, *_ssh_options(found), f"{found.user}@{ip}", remote],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return 124, f"ssh timed out after {timeout}s"
    return completed.returncode, clean(completed.stdout + completed.stderr)


def rsync(found: Settings, args: list[str]) -> tuple[int, str]:
    transport = shlex.join(["ssh", *_ssh_options(found)])
    completed = subprocess.run(["rsync", "-a", "--delete", "-e", transport, *args], capture_output=True, text=True, check=False)
    return completed.returncode, completed.stderr


def _push(found: Settings, ip: str, root: Path, gate: str) -> str:
    ssh(found, ip, f"mkdir -p {shlex.quote(str(root))}", timeout=30)
    keep = {".venv/"} if gate.startswith("py.") else set()
    code, error = rsync(found, [*_excludes(keep), f"{root}/", f"{found.user}@{ip}:{root}/"])
    if code != 0:
        return _last_line(error, f"rsync exit {code}")
    _copy_toolchains(found, ip, _toolchains(root if keep else None))
    return ""


def _excludes(keep: set[str]) -> list[str]:
    return [f"--exclude={pattern}" for pattern in EXCLUDES if pattern not in keep]


def _copy_toolchains(found: Settings, ip: str, found_toolchains: list[Path]) -> None:
    for toolchain in found_toolchains:
        ssh(found, ip, f"mkdir -p {shlex.quote(str(toolchain))}", timeout=30)
        rsync(found, [f"{toolchain}/", f"{found.user}@{ip}:{toolchain}/"])


def _toolchains(venv_root: Path | None = None) -> list[Path]:
    home = Path.home()
    return list(dict.fromkeys([*_uv_store(home, venv_root), *_nvm_bins(home)]))


def _uv_store(home: Path, venv_root: Path | None) -> list[Path]:
    store = home / ".local" / "share" / "uv" / "python"
    if venv_root is not None and (venv_root / ".venv" / "bin" / "python").resolve().is_relative_to(store):
        return [store]
    return []


def _nvm_bins(home: Path) -> list[Path]:
    versions = home / ".nvm" / "versions" / "node"
    return [path.parent for path in map(Path, os.get_exec_path()) if _nvm_bin(path, versions)]


def _nvm_bin(path: Path, versions: Path) -> bool:
    return path.name == "bin" and path.parent.parent == versions and path.is_dir()


def _fetch(found: Settings, ip: str, root: Path, path: str) -> None:
    target = root / path
    trailing = "/" if not Path(path).suffix else ""
    target.parent.mkdir(parents=True, exist_ok=True)
    rsync(found, [f"{found.user}@{ip}:{target}{trailing}", f"{target}{trailing}"])


def _execute(found: Settings, ip: str, command: list[str], cwd: Path, env: dict[str, str] | None, timeout: int | None) -> tuple[int, str]:
    flags = _env_flags(found, env)
    job = uuid.uuid4().hex
    folder = cwd / ".marestail"
    session = shlex.quote(str(folder / f"job-{job}"))
    inner = shlex.join(command)
    if timeout:
        inner = f"timeout -k 30 {int(timeout)} {inner}"
    inner = f"mkdir -p {shlex.quote(str(folder))}; echo $$ > {session}; {PREPARE}; {inner}; code=$?; rm -f {session}; exit $code"
    docker = f"docker exec -w {shlex.quote(str(cwd))} {flags} {found.container} setsid -w bash -c {shlex.quote(inner)}"
    stop = f"[ -f {session} ] && pkill -9 -s $(cat {session}); rm -f {session}"
    cleanup = f"sg docker -c {shlex.quote(f'docker exec {found.container} bash -c {shlex.quote(stop)}')}"
    marker = f"{BUSY}/{job}"
    queued = f"flock {JOB_LOCK} sg docker -c {shlex.quote(docker)}"
    script = (
        f"echo $$ > {marker}; stop() {{ {cleanup} >/dev/null 2>&1; rm -f {marker}; }}; trap 'stop; exit 129' HUP INT TERM; "
        f"{queued} & wait $!; code=$?; rm -f {marker}; touch /var/lib/marestail/last; exit $code"
    )
    return ssh(found, ip, script, timeout=(timeout * 3 + 120) if timeout else None, tty=True)


def _env_flags(found: Settings, env: dict[str, str] | None) -> str:
    passed = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(Path.home()),
        "LANG": "C.UTF-8",
        "TERM": "dumb",
        "NO_COLOR": "1",
        **found.env,
        **(env or {}),
    }
    return " ".join(f"-e {shlex.quote(f'{key}={value}')}" for key, value in passed.items())


def _record(entry: dict[str, Any]) -> None:
    USAGE.parent.mkdir(parents=True, exist_ok=True)
    with USAGE.open("a") as handle:
        handle.write(json.dumps({"ts": datetime.now(UTC).isoformat(timespec="seconds"), **entry}) + "\n")


def status_command(_args: object) -> int:
    found = _load()
    if found is None:
        print(f"no remote configured ({CONFIG})")
        return 1
    _show_box(found)
    _show_spend(found)
    return 0


def _show_box(found: Settings) -> None:
    status, ip = describe(found)
    print(f"{found.instance} ({found.project}/{found.zone}): {status or 'unknown'} {ip}".rstrip())
    print(f"offloaded gates: {', '.join(found.gates) or 'none'}")
    for line in _box_jobs(found, status, ip):
        print(f"  {line}")


def _box_jobs(found: Settings, status: str, ip: str) -> list[str]:
    if status == "RUNNING" and ip:
        return _running_jobs(found, ip)
    return []


def _show_spend(found: Settings) -> None:
    now = datetime.now(UTC)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    entries = _usage()
    for label, since in (("today", midnight), ("this month", midnight.replace(day=1))):
        billed_text = _billed(found, entries, since, now)
        jobs = _jobs_since(entries, since)
        busy = sum(entry.get("seconds", 0) for entry in jobs)
        print(f"{label}: VM up {billed_text}; {len(jobs)} jobs, {busy / 3600:.2f}h busy")


def _billed(found: Settings, entries: list[dict[str, Any]], since: datetime, now: datetime) -> str:
    measured = _measured_uptimes(found, entries, since, now)
    known = [(seconds, rate) for seconds, rate in measured if seconds is not None]
    if len(known) < len(measured):
        return "unknown"
    return _priced(known)


def _measured_uptimes(found: Settings, entries: list[dict[str, Any]], since: datetime, now: datetime) -> list[tuple[float | None, float]]:
    rates = _instances(found, entries)
    return [(uptime_seconds(found.project, instance, since, now), rate) for instance, rate in rates.items()]


def _priced(known: list[tuple[float, float]]) -> str:
    hours = sum(seconds / 3600 for seconds, _rate in known)
    dollars = sum(seconds / 3600 * rate for seconds, rate in known)
    return f"{hours:.2f}h = ${dollars:.2f}"


def _jobs_since(entries: list[dict[str, Any]], since: datetime) -> list[dict[str, Any]]:
    return [entry for entry in entries if entry.get("event") == "job" and _when(entry) >= since]


def _running_jobs(found: Settings, ip: str) -> list[str]:
    code, output = ssh(found, ip, JOBS, timeout=30)
    if code != 0:
        return []
    rows = list(map(str.split, output.splitlines()))
    jobs, idles = _job_rows(rows), _idle_rows(rows)
    return jobs or idles[-1:]


def _job_rows(rows: list[list[str]]) -> list[str]:
    return [_job_line(*parts) for parts in rows if len(parts) == 4]


def _job_line(repo: str, tool: str, age: str, state: str) -> str:
    return f"running: {tool} for {Path(repo).name}, {age}" + (" (queued)" if state == "queued" else "")


def _idle_rows(rows: list[list[str]]) -> list[str]:
    return [_idle_line(int(parts[1]), int(parts[2])) for parts in rows if len(parts) == 3 and parts[0] == "idle"]


def _idle_line(seconds: int, limit: int) -> str:
    off = datetime.now().astimezone() + timedelta(seconds=max(0, limit * 60 - seconds))
    return f"no jobs: idle for {seconds // 60} min, powers itself off after {limit} idle min (about {off:%H:%M})"


def _instances(found: Settings, entries: list[dict[str, Any]]) -> dict[str, float]:
    rates = {entry["instance"]: float(entry.get("usd_per_hour", found.usd_per_hour)) for entry in entries if entry.get("instance")}
    rates[found.instance] = found.usd_per_hour
    return rates


def _when(entry: dict[str, Any]) -> datetime:
    stamp = datetime.fromisoformat(entry["ts"])
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)


def _usage() -> list[dict[str, Any]]:
    if not USAGE.exists():
        return []
    return [json.loads(line) for line in USAGE.read_text().splitlines() if line.strip()]


def uptime_seconds(project: str, instance: str, since: datetime, until: datetime) -> float | None:
    token = subprocess.run(["gcloud", "auth", "print-access-token"], capture_output=True, text=True, check=False).stdout.strip()
    if not token:
        return None
    data = _monitored(_uptime_url(project, instance, since, until), token)
    if data is None:
        return None
    return _total_uptime(data)


def _total_uptime(data: dict[str, Any]) -> float:
    return float(sum(point["value"]["doubleValue"] for series in data.get("timeSeries", []) for point in series.get("points", [])))


def _uptime_url(project: str, instance: str, since: datetime, until: datetime) -> str:
    query = {
        "filter": f'metric.type="compute.googleapis.com/instance/uptime" AND metric.labels.instance_name="{instance}"',
        "interval.startTime": since.isoformat().replace("+00:00", "Z"),
        "interval.endTime": until.isoformat().replace("+00:00", "Z"),
        "aggregation.alignmentPeriod": f"{max(60, int((until - since).total_seconds()))}s",
        "aggregation.perSeriesAligner": "ALIGN_SUM",
    }
    return f"https://monitoring.googleapis.com/v3/projects/{project}/timeSeries?{urllib.parse.urlencode(query)}"


def _monitored(url: str, token: str) -> dict[str, Any] | None:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={AUTHORIZATION: f"Bearer {token}"}), timeout=20) as response:
            data: dict[str, Any] = json.load(response)
    except (OSError, ValueError):
        return None
    return data
