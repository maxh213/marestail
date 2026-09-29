import fcntl
import json
import os
import shlex
import subprocess
import time
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import tomllib

from marestail.context import Context
from marestail.shell import clean, run

CONFIG = Path.home() / ".config" / "marestail" / "remote.toml"
USAGE = Path.home() / ".config" / "marestail" / "remote-usage.jsonl"
LOCK = Path.home() / ".config" / "marestail" / "remote.lock"
ENV = "MARESTAIL_REMOTE"
GATES = "MARESTAIL_REMOTE_GATES"
BUSY = "/var/lib/marestail/busy.d"
JOB_LOCK = "/var/lib/marestail/job.lock"
EXCLUDES = ("/.git/", "/.marestail/", ".stryker-tmp/", ".elixir_ls/", "node_modules/", ".next/", "_build/", ".venv/", "cover/", "erl_crash.dump", "target/")
PREPARE = (
    'if [ -f package-lock.json ]; then '
    'lock=$(sha256sum package-lock.json | cut -c1-16); '
    'if [ "$(cat node_modules/.marestail-lock 2>/dev/null)" != "$lock" ]; then '
    'npm ci --no-audit --no-fund --loglevel=error >&2 && echo "$lock" > node_modules/.marestail-lock || exit 97; '
    'fi; fi'
)
BOOT_WAIT = 240


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


def settings(ctx: Context) -> Settings | None:
    if os.environ.get(ENV, "").lower() in {"0", "off", "false", "no"}:
        return None
    if ctx.config.get("remote", "enabled", True) is False:
        return None
    return load()


def load() -> Settings | None:
    if not CONFIG.exists():
        return None
    raw = tomllib.loads(CONFIG.read_text()).get("remote", {})
    if not raw.get("enabled", True):
        return None
    return Settings(
        instance=raw["instance"],
        project=raw["project"],
        zone=raw["zone"],
        container=raw.get("container", "mt"),
        gates=tuple(os.environ[GATES].split(",")) if os.environ.get(GATES) else tuple(raw.get("gates", [])),
        workers={str(k): int(v) for k, v in raw.get("workers", {}).items()},
        env={str(k): str(v) for k, v in raw.get("env", {}).items()},
        usd_per_hour=float(raw.get("usd_per_hour", 0)),
        user=raw.get("user", os.environ.get("USER", "max")),
        key=Path(raw.get("key", "~/.ssh/google_compute_engine")).expanduser(),
    )


def workers(ctx: Context, gate: str, local: int) -> int:
    found = settings(ctx)
    if found is None or gate not in found.gates:
        return local
    return found.workers.get(gate, local)


def offloads(ctx: Context, gate: str) -> bool:
    found = settings(ctx)
    return found is not None and gate in found.gates


def run_mutation(
    ctx: Context,
    gate: str,
    command: list[str],
    cwd: Path,
    env: dict[str, str] | None = None,
    timeout: int | None = 7200,
    pull: tuple[str, ...] = (),
) -> Outcome:
    found = settings(ctx)
    if found is None or gate not in found.gates:
        return local(command, cwd, env, timeout, "")
    ip, problem = ensure_up(found)
    if ip is None:
        return local(command, cwd, env, timeout, f" (remote unavailable: {problem}; ran locally)")
    started = time.time()
    problem = push(found, ip, ctx.root, gate)
    if problem:
        return local(command, cwd, env, timeout, f" (remote sync failed: {problem}; ran locally)")
    code, output = execute(found, ip, command, cwd, env, timeout)
    if code == 255 and not output.strip():
        return local(command, cwd, env, timeout, " (remote ssh dropped; ran locally)")
    for path in pull:
        fetch(found, ip, ctx.root, path)
    seconds = time.time() - started
    record({"event": "job", "gate": gate, "repo": str(ctx.root), "seconds": round(seconds), "usd": round(seconds / 3600 * found.usd_per_hour, 3)})
    return Outcome(code, output, f" (on {found.instance}, {round(seconds)}s)")


def local(command: list[str], cwd: Path, env: dict[str, str] | None, timeout: int | None, where: str) -> Outcome:
    code, output = run(command, cwd=cwd, env=env, timeout=timeout) if timeout else unbounded(command, cwd, env)
    return Outcome(code, output, where)


def unbounded(command: list[str], cwd: Path, env: dict[str, str] | None) -> tuple[int, str]:
    completed = subprocess.run(command, cwd=cwd, env={**os.environ, **(env or {})}, capture_output=True, text=True, check=False)
    return completed.returncode, clean(completed.stdout + completed.stderr)


def ensure_up(found: Settings) -> tuple[str | None, str]:
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with LOCK.open("w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        status, ip = describe(found)
        if status in {"STOPPING", "SUSPENDING"}:
            status, ip = wait_for_stop(found)
        if status in {"TERMINATED", "STOPPED", "SUSPENDED"}:
            verb = "resume" if status == "SUSPENDED" else "start"
            code, output = gcloud(found, ["compute", "instances", verb, found.instance])
            if code != 0:
                return None, output.strip().splitlines()[-1] if output.strip() else f"{verb} failed"
            record({"event": "vm-start", "usd_per_hour": found.usd_per_hour})
            status, ip = describe(found)
        if status != "RUNNING" or not ip:
            return None, f"instance is {status or 'unknown'}"
        deadline = time.time() + BOOT_WAIT
        while time.time() < deadline:
            if ssh(found, ip, "sg docker -c 'docker exec " + found.container + " true'", timeout=20)[0] == 0:
                return ip, ""
            time.sleep(5)
        return None, "ssh never came up"


def wait_for_stop(found: Settings) -> tuple[str, str]:
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
    configs = data.get("networkInterfaces", [{}])[0].get("accessConfigs", [{}])
    return data.get("status", ""), configs[0].get("natIP", "") if configs else ""


def gcloud(found: Settings, args: list[str]) -> tuple[int, str]:
    completed = subprocess.run(
        ["gcloud", *args, f"--project={found.project}", f"--zone={found.zone}", "--quiet"],
        capture_output=True, text=True, check=False,
    )
    return completed.returncode, completed.stdout if completed.returncode == 0 else completed.stderr


def ssh_options(found: Settings) -> list[str]:
    control = Path.home() / ".ssh" / f"cm-{found.instance}"
    return [
        "-i", str(found.key),
        "-o", f"HostKeyAlias={found.instance}",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", f"UserKnownHostsFile={Path.home() / '.ssh' / 'marestail_known_hosts'}",
        "-o", "ControlMaster=auto",
        "-o", f"ControlPath={control}-%C",
        "-o", "ControlPersist=600",
        "-o", "ServerAliveInterval=30",
        "-o", "ConnectTimeout=15",
        "-o", "BatchMode=yes",
    ]


def ssh(found: Settings, ip: str, remote: str, timeout: int | None = None) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            ["ssh", *ssh_options(found), f"{found.user}@{ip}", remote],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        return 124, f"ssh timed out after {timeout}s"
    return completed.returncode, clean(completed.stdout + completed.stderr)


def rsync(found: Settings, args: list[str]) -> tuple[int, str]:
    transport = shlex.join(["ssh", *ssh_options(found)])
    completed = subprocess.run(["rsync", "-a", "--delete", "-e", transport, *args], capture_output=True, text=True, check=False)
    return completed.returncode, completed.stderr


def push(found: Settings, ip: str, root: Path, gate: str = "") -> str:
    ssh(found, ip, f"mkdir -p {shlex.quote(str(root))}", timeout=30)
    keep = {".venv/"} if gate.startswith("py.") else set()
    excludes = [f"--exclude={pattern}" for pattern in EXCLUDES if pattern not in keep]
    code, error = rsync(found, [*excludes, f"{root}/", f"{found.user}@{ip}:{root}/"])
    if code != 0:
        return error.strip().splitlines()[-1] if error.strip() else f"rsync exit {code}"
    for toolchain in toolchains(root if keep else None):
        ssh(found, ip, f"mkdir -p {shlex.quote(str(toolchain))}", timeout=30)
        rsync(found, [f"{toolchain}/", f"{found.user}@{ip}:{toolchain}/"])
    return ""


def toolchains(venv_root: Path | None = None) -> list[Path]:
    home = Path.home()
    found = []
    store = home / ".local" / "share" / "uv" / "python"
    if venv_root is not None and (venv_root / ".venv" / "bin" / "python").resolve().is_relative_to(store):
        found.append(store)
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        path = Path(entry)
        if path.name == "bin" and path.parent.parent == home / ".nvm" / "versions" / "node" and path.is_dir():
            found.append(path.parent)
    return list(dict.fromkeys(found))


def fetch(found: Settings, ip: str, root: Path, path: str) -> None:
    target = root / path
    trailing = "/" if not Path(path).suffix else ""
    target.parent.mkdir(parents=True, exist_ok=True)
    rsync(found, [f"{found.user}@{ip}:{target}{trailing}", f"{target}{trailing}"])


def execute(found: Settings, ip: str, command: list[str], cwd: Path, env: dict[str, str] | None, timeout: int | None) -> tuple[int, str]:
    passed = {"PATH": os.environ.get("PATH", ""), "HOME": str(Path.home()), "LANG": "C.UTF-8", **found.env, **(env or {})}
    flags = " ".join(f"-e {shlex.quote(f'{key}={value}')}" for key, value in passed.items())
    inner = shlex.join(command)
    if timeout:
        inner = f"timeout -k 30 {int(timeout)} {inner}"
    inner = f"{PREPARE}; {inner}"
    docker = f"docker exec -w {shlex.quote(str(cwd))} {flags} {found.container} bash -c {shlex.quote(inner)}"
    marker = f"{BUSY}/{uuid.uuid4().hex}"
    queued = f"flock {JOB_LOCK} sg docker -c {shlex.quote(docker)}"
    script = f"echo $$ > {marker}; {queued}; code=$?; rm -f {marker}; touch /var/lib/marestail/last; exit $code"
    return ssh(found, ip, script, timeout=(timeout * 3 + 120) if timeout else None)


def record(entry: dict) -> None:
    USAGE.parent.mkdir(parents=True, exist_ok=True)
    with USAGE.open("a") as handle:
        handle.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), **entry}) + "\n")


def status_command(_args) -> int:
    found = load()
    if found is None:
        print(f"no remote configured ({CONFIG})")
        return 1
    status, ip = describe(found)
    print(f"{found.instance} ({found.project}/{found.zone}): {status or 'unknown'} {ip}".rstrip())
    print(f"offloaded gates: {', '.join(found.gates) or 'none'}")
    now = datetime.now(timezone.utc)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    for label, since in (("today", midnight), ("this month", midnight.replace(day=1))):
        billed = uptime_seconds(found, since, now)
        jobs = [entry for entry in usage() if entry.get("event") == "job" and entry["ts"] >= since.isoformat(timespec="seconds")]
        busy = sum(entry.get("seconds", 0) for entry in jobs)
        billed_text = "unknown" if billed is None else f"{billed / 3600:.2f}h = ${billed / 3600 * found.usd_per_hour:.2f}"
        print(f"{label}: VM up {billed_text}; {len(jobs)} jobs, {busy / 3600:.2f}h busy")
    return 0


def usage() -> list[dict]:
    if not USAGE.exists():
        return []
    return [json.loads(line) for line in USAGE.read_text().splitlines() if line.strip()]


def uptime_seconds(found: Settings, since: datetime, until: datetime) -> float | None:
    token = subprocess.run(["gcloud", "auth", "print-access-token"], capture_output=True, text=True, check=False).stdout.strip()
    if not token:
        return None
    query = {
        "filter": f'metric.type="compute.googleapis.com/instance/uptime" AND metric.labels.instance_name="{found.instance}"',
        "interval.startTime": since.isoformat().replace("+00:00", "Z"),
        "interval.endTime": until.isoformat().replace("+00:00", "Z"),
        "aggregation.alignmentPeriod": f"{max(60, int((until - since).total_seconds()))}s",
        "aggregation.perSeriesAligner": "ALIGN_SUM",
    }
    url = f"https://monitoring.googleapis.com/v3/projects/{found.project}/timeSeries?{urllib.parse.urlencode(query)}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"}), timeout=20) as response:
            data = json.load(response)
    except (OSError, ValueError):
        return None
    return sum(point["value"]["doubleValue"] for series in data.get("timeSeries", []) for point in series.get("points", []))
