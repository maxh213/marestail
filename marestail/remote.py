import fcntl
import hashlib
import json
import os
import shlex
import subprocess
import sys
import time
import tomllib
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from marestail.config import Config
from marestail.context import Context
from marestail.shell import NO_OFFLOAD, clean, run

FOLDER = Path.home() / ".config" / "marestail"
CONFIG = FOLDER / "remote.toml"
USAGE = FOLDER / "remote-usage.jsonl"
LOCK = FOLDER / "remote.lock"
ENV = "MARESTAIL_REMOTE"
GATES = "MARESTAIL_REMOTE_GATES"
GUARD = NO_OFFLOAD
GATE_NAME = "MARESTAIL_GATE"
SHIMS = Path(__file__).resolve().parent / "shims"
OFF = {"0", "off", "false", "no"}
HOST = "host"
OFFLOAD = "offload"
SONAR = "sonar"
DEFAULT_PLACE = "default"
QUEUED = ".mutation"
BUSY = "/var/lib/marestail/busy.d"
JOB_LOCK = "/var/lib/marestail/job.lock"
JOBS_DIR = ".cache/marestail-jobs"
EXIT_MARK = "marestail-exit "
SSH_TIMEOUT = "ssh timed out after"
UNAVAILABLE = 75
TIMED_OUT = 124
MAX_WAIT_HOURS = 6.0
BACKOFF = (30, 60, 120, 240, 300)
LOCKFILES = ("package-lock.json", "pnpm-lock.yaml")
NVM = 'export NVM_DIR="$HOME/.nvm"; [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh" >/dev/null 2>&1'
EXCLUDES = (
    "/.git/",
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
TOOLING_SYNC = (
    "--include=/.marestail/",
    "--include=/.marestail/tooling/***",
    "--include=/.marestail/stryker/***",
    "--exclude=/.marestail/*",
)
PREPARE = (
    "if [ -f package-lock.json ]; then "
    "lock=$(sha256sum package-lock.json | cut -c1-16); "
    'if [ "$(cat node_modules/.marestail-lock 2>/dev/null)" != "$lock" ]; then '
    'npm ci --no-audit --no-fund --loglevel=error >&2 && echo "$lock" > node_modules/.marestail-lock || exit 97; '
    "fi; "
    "elif [ -f pnpm-lock.yaml ]; then "
    "lock=$(sha256sum pnpm-lock.yaml | cut -c1-16); "
    'if [ "$(cat node_modules/.marestail-lock 2>/dev/null)" != "$lock" ]; then '
    "want=$(node -p \"(require('./package.json').packageManager || 'pnpm@latest').split('@')[1]\"); "
    'out=$(npx --yes "pnpm@$want" install --frozen-lockfile --reporter=append-only 2>&1) '
    '&& echo "$lock" > node_modules/.marestail-lock || { printf "%s\\n" "$out" >&2; exit 97; }; '
    "fi; fi"
)
TOOLING_PREPARE = (
    "if [ -f {folder}/package-lock.json ]; then "
    "lock=$(sha256sum {folder}/package-lock.json | cut -c1-16); "
    'if [ "$(cat {folder}/node_modules/.marestail-lock 2>/dev/null)" != "$lock" ]; then '
    'npm ci --prefix {folder} --no-audit --no-fund --loglevel=error >&2 && echo "$lock" > {folder}/node_modules/.marestail-lock || exit 97; '
    "fi; fi"
)
BOOT_WAIT = 240
AUTHORIZATION = "Authorization"
JOBS = r"""
for f in /var/lib/marestail/busy.d/*; do
  [ -e "$f" ] || continue
  p=$(cat "$f"); kill -0 "$p" 2>/dev/null || continue
  args=$(tr '\0' ' ' < /proc/$p/cmdline)
  repo=$(echo "$args" | grep -oE '(docker exec -w|env -C) [^ ]+' | head -1 | awk '{print $NF}')
  gate=$(echo "$args" | grep -oE 'MARESTAIL_GATE=[^ ]+' | head -1 | cut -d= -f2)
  tool=${gate:-$(echo "$args" | grep -oE 'muex|stryker|mutmut|pitest' | head -1)}
  state=running; q=$(pgrep -P "$p" -x flock) && ! pgrep -P "$q" >/dev/null && state=queued
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
    run_in: dict[str, str] = field(default_factory=dict)
    max_wait: float = MAX_WAIT_HOURS * 3600
    sonar_host_url: str = ""

    def place(self, gate: str) -> str:
        fallback = HOST if gate == OFFLOAD else self.run_in.get(DEFAULT_PLACE, self.container)
        return self.run_in.get(gate, fallback)


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
    send: tuple[Path, ...] = ()
    stream: bool = False
    fresh: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Try:
    ip: str = ""
    code: int = UNAVAILABLE
    output: str = ""
    problem: str = ""
    started: float = 0.0


class _Waits:
    def __init__(self, found: Settings) -> None:
        self.found = found
        self.waited = 0.0
        self.tries = 0

    def another(self, problem: str) -> bool:
        delay = min(BACKOFF[min(self.tries, len(BACKOFF) - 1)], self.found.max_wait - self.waited)
        if delay <= 0:
            return False
        self.tries += 1
        _say(f"marestail: {problem}; trying again in {span(delay)} (waited {span(self.waited)} of {span(self.found.max_wait)})")
        time.sleep(delay)
        self.waited += delay
        return True

    def note(self) -> str:
        return f", after waiting {span(self.waited)} for the box" if self.waited else ""

    def gave_up(self, problem: str) -> str:
        return f" (gave up after waiting {span(self.waited)}: {problem}; not run locally)"


def span(seconds: float) -> str:
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    parts = [f"{value}{unit}" for value, unit in ((hours, "h"), (minutes, "m"), (secs, "s")) if value]
    return "".join(parts) or "0s"


def _say(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def switched_off() -> bool:
    return os.environ.get(ENV, "").lower() in OFF


def settings_for(config: Config) -> Settings | None:
    if switched_off() or config.get("remote", "enabled") is False:
        return None
    return _load()


def _settings(ctx: Context) -> Settings | None:
    return settings_for(ctx.config)


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
        run_in=_places(raw.get("run_in", {})),
        max_wait=float(raw.get("max_wait_hours", MAX_WAIT_HOURS)) * 3600,
        sonar_host_url=str(raw.get("sonar_host_url", "")),
    )


def _places(value: str | dict[str, Any]) -> dict[str, str]:
    if isinstance(value, str):
        return {DEFAULT_PLACE: value}
    return {str(k): str(v) for k, v in value.items()}


def _offloaded_gates(raw: dict[str, Any]) -> tuple[str, ...]:
    if os.environ.get(GATES):
        return tuple(os.environ[GATES].split(","))
    return tuple(raw.get("gates", []))


def offloaded(ctx: Context, gate: str) -> bool:
    found = _settings(ctx)
    return found is not None and gate in found.gates


def sonar_url(ctx: Context, fallback: str) -> str:
    found = _settings(ctx)
    if found is None or SONAR not in found.gates:
        return fallback
    return found.sonar_host_url or fallback


def run_job(
    ctx: Context,
    gate: str,
    build: Callable[[int], list[str]],
    cwd: Path,
    workers: int = 0,
    env: dict[str, str] | None = None,
    timeout: int | None = 7200,
    pull: tuple[str, ...] = (),
    send: tuple[Path, ...] = (),
    fresh: tuple[str, ...] = (),
) -> Outcome:
    found = _settings(ctx)
    job = _Job(ctx, gate, build, cwd, workers, env, timeout, pull, send, fresh=fresh)
    if found is None or gate not in found.gates:
        return _locally(job, "")
    return _offloaded(found, job)


def run_streamed(ctx: Context, gate: str, build: Callable[[int], list[str]], cwd: Path, pull: tuple[str, ...]) -> Outcome:
    found = _settings(ctx)
    job = _Job(ctx, gate, build, cwd, 0, None, 7200, pull, stream=True)
    if found is None:
        return _locally(job, "")
    return _offloaded(found, job)


def _offloaded(found: Settings, job: _Job) -> Outcome:
    waits = _Waits(found)
    attempt = _attempt(found, job)
    while attempt.problem and waits.another(attempt.problem):
        attempt = _attempt(found, job)
    if attempt.problem:
        return Outcome(UNAVAILABLE, attempt.output, waits.gave_up(attempt.problem))
    return _finished(found, job, attempt, waits)


def _attempt(found: Settings, job: _Job) -> _Try:
    ip, problem = ensure_up(found)
    if ip is None:
        return _Try(problem=f"{found.instance} is unavailable: {problem}")
    return _synced(found, ip, job)


def _synced(found: Settings, ip: str, job: _Job) -> _Try:
    started = time.time()
    problem = _push(found, ip, job.ctx.root, job.gate) or _send(found, ip, job.send)
    if problem:
        return _Try(ip, problem=f"copying the tree to {found.instance} failed: {problem}")
    return _ran(found, ip, job, started)


def _ran(found: Settings, ip: str, job: _Job, started: float) -> _Try:
    code, output = _execute(found, ip, job)
    if code is None:
        _record_job(found, job, started, "dropped")
        return _Try(ip, UNAVAILABLE, output, f"the connection to {found.instance} dropped mid-run", started)
    return _Try(ip, code, output, "", started)


def _finished(found: Settings, job: _Job, attempt: _Try, waits: _Waits) -> Outcome:
    for path in job.pull:
        _fetch(found, attempt.ip, job.ctx.root, path)
    seconds = _record_job(found, job, attempt.started, "ok" if attempt.code == 0 else f"exit {attempt.code}")
    return Outcome(attempt.code, attempt.output, f" (on {found.instance}, {round(seconds)}s{waits.note()})")


def _locally(job: _Job, where: str) -> Outcome:
    env = {**(job.env or {}), GUARD: "1"}
    command = job.build(job.workers)
    if job.stream:
        return Outcome(_passthrough(command, job.cwd, env), "", where)
    return _local(command, job.cwd, env, job.timeout, where)


def _passthrough(command: list[str], cwd: Path, env: dict[str, str]) -> int:
    path = without_shims(os.environ.get("PATH", ""))
    try:
        return subprocess.run(command, cwd=cwd, env={**os.environ, **env, "PATH": path}, check=False).returncode
    except FileNotFoundError:
        _say(f"{command[0]}: not found")
        return 127


def without_shims(path: str) -> str:
    return os.pathsep.join(part for part in path.split(os.pathsep) if part != str(SHIMS))


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


def _local(command: list[str], cwd: Path, env: dict[str, str] | None, timeout: int | None, where: str) -> Outcome:
    code, output = run(command, cwd=cwd, env=env, timeout=timeout) if timeout else _unbounded(command, cwd, env)
    return Outcome(code, output, where)


def _unbounded(command: list[str], cwd: Path, env: dict[str, str] | None) -> tuple[int, str]:
    completed = subprocess.run(command, cwd=cwd, env={**os.environ, **(env or {})}, capture_output=True, text=True, check=False)
    return completed.returncode, clean(completed.stdout + completed.stderr)


def ensure_up(found: Settings) -> tuple[str | None, str]:
    status, ip = describe(found)
    if status == "RUNNING" and ip:
        return _reachable(found, ip)
    return _locked_boot(found)


def _locked_boot(found: Settings) -> tuple[str | None, str]:
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
    return [*(f"--exclude={pattern}" for pattern in EXCLUDES if pattern not in keep), *TOOLING_SYNC]


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


def _send(found: Settings, ip: str, paths: tuple[Path, ...]) -> str:
    return next(filter(None, (_sent(found, ip, path) for path in paths if path.exists())), "")


def _sent(found: Settings, ip: str, path: Path) -> str:
    trailing = "/" if path.is_dir() else ""
    ssh(found, ip, f"mkdir -p {shlex.quote(str(path.parent))}", timeout=30)
    code, error = rsync(found, [f"{path}{trailing}", f"{found.user}@{ip}:{path}{trailing}"])
    return _last_line(error, f"rsync exit {code}") if code else ""


def _execute(found: Settings, ip: str, job: _Job) -> tuple[int | None, str]:
    script = _script(found, job, job.build(found.workers.get(job.gate, job.workers)))
    if job.stream:
        return _streamed(found, ip, script)
    code, output = ssh(found, ip, script, timeout=(job.timeout * 3 + 120) if job.timeout else None, tty=True)
    return _concluded(code, output)


def _concluded(code: int, output: str) -> tuple[int | None, str]:
    body, mark, tail = output.rpartition("\n" + EXIT_MARK)
    if mark and tail.strip().isdigit():
        return int(tail.strip()), body
    return _unfinished(code, output)


def _unfinished(code: int, output: str) -> tuple[int | None, str]:
    if code == TIMED_OUT and output.startswith(SSH_TIMEOUT):
        return code, output
    return None, output


def _streamed(found: Settings, ip: str, script: str) -> tuple[int | None, str]:
    relay = _Relay()
    process = subprocess.Popen(
        ["ssh", "-tt", "-q", *_ssh_options(found), f"{found.user}@{ip}", script],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
    )
    try:
        relay.feed_all(process.stdout or ())
    finally:
        process.kill()
        returned = process.wait()
    return relay.code, "" if relay.code is not None else f"ssh exit {returned}"


class _Relay:
    def __init__(self) -> None:
        self.held = ""
        self.code: int | None = None

    def feed_all(self, lines: Iterable[str]) -> None:
        for line in lines:
            self.feed(clean(line))
        sys.stdout.write(self.held)
        sys.stdout.flush()

    def feed(self, text: str) -> None:
        if text.startswith(EXIT_MARK):
            self.held, self.code = "", _exit_code(text)
            return
        sys.stdout.write(self.held + _shown(text))
        self.held = "" if text.strip() else text
        sys.stdout.flush()


def _shown(text: str) -> str:
    return text if text.strip() else ""


def _exit_code(text: str) -> int | None:
    value = text[len(EXIT_MARK) :].strip()
    return int(value) if value.isdigit() else None


def _script(found: Settings, job: _Job, command: list[str]) -> str:
    job_id = uuid.uuid4().hex
    session = f"{Path.home()}/{JOBS_DIR}/job-{job_id}"
    marker = f"{BUSY}/{job_id}"
    runner = _queued(job.gate, _runner(found, job, _inner(found, job, command, session)))
    return (
        f"echo $$ > {marker}; stop() {{ {_cleanup(found.place(job.gate), session)} >/dev/null 2>&1; rm -f {marker}; }}; "
        "trap 'stop; exit 129' HUP INT TERM; "
        f"{runner} & wait $!; code=$?; rm -f {marker}; touch /var/lib/marestail/last; "
        f"printf '\\n{EXIT_MARK}%s\\n' \"$code\"; exit $code"
    )


def _inner(found: Settings, job: _Job, command: list[str], session: str) -> str:
    folder = f"{Path.home()}/{JOBS_DIR}"
    line = shlex.join(command)
    if job.timeout:
        line = f"timeout -k 30 {int(job.timeout)} {line}"
    return f"mkdir -p -m 700 {folder} && echo $$ > {session}; {_setup(found, job)}{line} </dev/null; code=$?; rm -f {session}; exit $code"


def _setup(found: Settings, job: _Job) -> str:
    return "".join(f"{step}; " for step in (_cleared(job), *_nvm(found, job), _prepare(job.ctx, job.cwd)) if step)


def _cleared(job: _Job) -> str:
    paths = [shlex.quote(str(job.ctx.root / path)) for path in job.fresh]
    return f"rm -rf {' '.join(paths)}" if paths else ""


def _nvm(found: Settings, job: _Job) -> tuple[str, ...]:
    return (NVM,) if found.place(job.gate) == HOST else ()


def _runner(found: Settings, job: _Job, inner: str) -> str:
    place = found.place(job.gate)
    cwd = shlex.quote(str(job.cwd))
    if place == HOST:
        return f"env -C {cwd} {_assignments(_passed(found, job))} setsid -w bash -c {shlex.quote(inner)}"
    docker = f"docker exec -w {cwd} {_env_flags(found, job)} {place} setsid -w bash -c {shlex.quote(inner)}"
    return f"sg docker -c {shlex.quote(docker)}"


def _queued(gate: str, runner: str) -> str:
    return f"flock {JOB_LOCK} {runner}" if gate.endswith(QUEUED) else runner


def _cleanup(place: str, session: str) -> str:
    stop = f"[ -f {session} ] && pkill -9 -s $(cat {session}); rm -f {session}"
    if place == HOST:
        return f"{{ {stop}; }}"
    return f"sg docker -c {shlex.quote(f'docker exec {place} bash -c {shlex.quote(stop)}')}"


def _prepare(ctx: Context, cwd: Path) -> str:
    steps = [*_node_install(ctx.root, cwd), *_tooling_install(ctx)]
    if not steps:
        return ""
    lock = f"{Path.home()}/{JOBS_DIR}/prepare-{hashlib.sha256(str(ctx.root).encode()).hexdigest()[:16]}.lock"
    return f"{{ flock 9; {'; '.join(steps)}; }} 9>{lock}"


def _node_install(root: Path, cwd: Path) -> list[str]:
    folder = _lock_folder(root, cwd)
    if folder is None:
        return []
    quoted = shlex.quote(str(folder))
    return [f'( cd {quoted} && {PREPARE} ) || {{ echo "marestail: dependencies did not install in "{quoted} >&2; exit 97; }}']


def _lock_folder(root: Path, cwd: Path) -> Path | None:
    return next((folder for folder in (cwd, *cwd.parents) if folder.is_relative_to(root) and _locked(folder)), None)


def _locked(folder: Path) -> bool:
    return any((folder / name).is_file() for name in LOCKFILES)


def _tooling_install(ctx: Context) -> list[str]:
    folder = ctx.ts("tooling")
    if folder is None:
        return []
    return [TOOLING_PREPARE.format(folder=shlex.quote(str(ctx.root / str(folder))))]


def _passed(found: Settings, job: _Job) -> dict[str, str]:
    return {
        "HOME": str(Path.home()),
        "LANG": "C.UTF-8",
        "TERM": "dumb",
        "NO_COLOR": "1",
        GUARD: "1",
        GATE_NAME: job.gate,
        **found.env,
        **(job.env or {}),
    }


def _assignments(passed: dict[str, str]) -> str:
    return " ".join(shlex.quote(f"{key}={value}") for key, value in passed.items())


def _env_flags(found: Settings, job: _Job) -> str:
    passed = {"PATH": without_shims(os.environ.get("PATH", "")), **_passed(found, job)}
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
