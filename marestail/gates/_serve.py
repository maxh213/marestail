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


@contextmanager
def ready_app(
    start: str,
    cwd: Path,
    port: int,
    ready: str,
    seconds: int,
    env: dict[str, str],
    log_path: Path,
) -> Iterator[str | None]:
    ensure_dir(log_path.parent)
    handle = log_path.open("w", buffering=1)
    process = None
    try:
        process = spawn(start, cwd, port, env, handle)
        failure = wait_ready(f"http://localhost:{port}{ready}", process, seconds)
        if failure:
            handle.flush()
        yield failure
    finally:
        end_app(process, handle)


def chosen_port(preferred: int) -> int:
    return preferred if can_bind(preferred) else free_port()


def can_bind(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def spawn(start: str, cwd: Path, port: int, extra: dict[str, str], handle: TextIO) -> subprocess.Popen[Any]:
    return subprocess.Popen(
        ["bash", "-lc", start],
        cwd=cwd,
        env={**os.environ, **extra, "PORT": str(port)},
        stdout=handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )


def wait_ready(url: str, process: subprocess.Popen[Any], seconds: int) -> str | None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        early = exited_early(process)
        if early:
            return early
        if answers(url):
            return None
        time.sleep(0.2)
    stop(process)
    return f"app did not answer on {url} within {seconds}s"


def exited_early(process: subprocess.Popen[Any]) -> str | None:
    code = process.poll()
    return None if code is None else f"app exited with {code} before answering"


def answers(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return int(response.status) < 500
    except urllib.error.HTTPError as error:
        return int(error.code) < 500
    except OSError:
        return False


def end_app(process: subprocess.Popen[Any] | None, handle: TextIO) -> None:
    if process is not None:
        stop(process)
    handle.close()


def stop(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=15)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        force_kill(process)


def force_kill(process: subprocess.Popen[Any]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
