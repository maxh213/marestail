import operator
import os
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TextIO

from marestail.shell import ensure_dir

FAILED = "marestail-serve: "
BOX_SCRIPT = r"""start=$1 cmd=$2 ready=$3 port=$4 seconds=$5 log=$6; shift 6
port=$(python3 -c 'import socket, sys
s = socket.socket()
try:
    s.bind(("127.0.0.1", int(sys.argv[1])))
except OSError:
    s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])' "$port")
mkdir -p "$(dirname "$log")"
PORT=$port setsid env "$@" bash -lc "$start" >"$log" 2>&1 &
app=$!
stop() {
  kill -TERM -- -$app 2>/dev/null
  for _ in $(seq 75); do kill -0 -- -$app 2>/dev/null || return 0; sleep 0.2; done
  kill -KILL -- -$app 2>/dev/null
}
url="http://localhost:$port$ready"
deadline=$((SECONDS + seconds))
until answer=$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 "$url") && [ "$answer" -gt 0 ] && [ "$answer" -lt 500 ]; do
  if ! kill -0 $app 2>/dev/null; then wait $app; echo "marestail-serve: app exited with $? before answering"; exit 90; fi
  if [ $SECONDS -ge $deadline ]; then stop; echo "marestail-serve: app did not answer on $url within ${seconds}s"; exit 90; fi
  sleep 0.2
done
MARESTAIL_APP_URL="http://localhost:$port" PORT=$port bash -lc "$cmd"; code=$?
stop
exit $code
"""


def served_command(start: str, command: str, ready: str, port: int, seconds: int, log_path: Path, env: dict[str, str]) -> list[str]:
    settings = [f"{key}={value}" for key, value in env.items()]
    return ["bash", "-c", BOX_SCRIPT, "marestail-serve", start, command, ready, str(port), str(seconds), str(log_path), *settings]


def serve_failure(output: str) -> str | None:
    return next((line.removeprefix(FAILED) for line in output.splitlines() if line.startswith(FAILED)), None)


@contextmanager
def ready_app(
    start: str,
    cwd: Path,
    preferred: int,
    ready: str,
    seconds: int,
    env: dict[str, str],
    log_path: Path,
) -> Iterator[tuple[str | None, int]]:
    port = _chosen_port(preferred)
    ensure_dir(log_path.parent)
    handle = log_path.open("w", buffering=1)
    process = None
    try:
        process = _spawn(start, cwd, port, env, handle)
        failure = _wait_ready(f"http://localhost:{port}{ready}", process, seconds)
        if failure:
            handle.flush()
        yield failure, port
    finally:
        _end_app(process, handle)


def run_logged(command: str, cwd: Path, env: dict[str, str], log_path: Path, seconds: int) -> int | None:
    ensure_dir(log_path.parent)
    with log_path.open("w") as handle:
        process = subprocess.Popen(
            ["bash", "-lc", command],
            cwd=cwd,
            env={**os.environ, **env},
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            return process.wait(timeout=seconds)
        except subprocess.TimeoutExpired:
            return None
        finally:
            _stop(process)


def _chosen_port(preferred: int) -> int:
    return preferred if _can_bind(preferred) else _free_port()


def _can_bind(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _spawn(start: str, cwd: Path, port: int, extra: dict[str, str], handle: TextIO) -> subprocess.Popen[Any]:
    return subprocess.Popen(
        ["bash", "-lc", start],
        cwd=cwd,
        env={**os.environ, **extra, "PORT": str(port)},
        stdout=handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )


def _wait_ready(url: str, process: subprocess.Popen[Any], seconds: int) -> str | None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        early = _exited_early(process)
        if early:
            return early
        if _answers(url):
            return None
        time.sleep(0.2)
    _stop(process)
    return f"app did not answer on {url} within {seconds}s"


def _exited_early(process: subprocess.Popen[Any]) -> str | None:
    code = process.poll()
    return None if code is None else f"app exited with {code} before answering"


def _answers(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return int(response.status) < 500
    except urllib.error.HTTPError as error:
        return int(error.code) < 500
    except OSError:
        return False


def _end_app(process: subprocess.Popen[Any] | None, handle: TextIO) -> None:
    if process is not None:
        _stop(process)
    handle.close()


def _stop(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    try:
        _signal_group(process, signal.SIGTERM)
        process.wait(timeout=15)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        _force_kill(process)


def _force_kill(process: subprocess.Popen[Any]) -> None:
    try:
        _signal_group(process, signal.SIGKILL)
    except ProcessLookupError:
        return


def _signal_group(process: subprocess.Popen[Any], sig: signal.Signals) -> None:
    pid = operator.index(process.pid)
    if pid <= 1:
        raise ProcessLookupError(f"refusing to signal process group {pid}")
    os.killpg(pid, sig)
