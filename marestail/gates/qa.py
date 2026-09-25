import os
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, TextIO

from marestail.context import Context
from marestail.report import Result, elapsed
from marestail.shell import ensure_dir, run, tail

DEFAULT_PORT = 3400
DEFAULT_READY = "/"
DEFAULT_READY_TIMEOUT = 180
DEFAULT_CMD_TIMEOUT = 3600
APP_URL = "MARESTAIL_APP_URL"
TASK_ENV = "MARESTAIL_TASK"
CMD_TIMEOUT_ENV = "MARESTAIL_QA_CMD_TIMEOUT"
LOG_NAME = "qa-app.log"


def run_gate(ctx: Context) -> Result:
    started = time.time()
    command = ctx.config.get("qa", "cmd")
    if not command:
        return Result.skipped("qa", "no [qa] cmd configured")
    start = ctx.config.get("qa", "start")
    if start:
        return run_with_app(ctx, command, start, started)
    return run_cmd(ctx, command, qa_cwd(ctx), started)


def run_with_app(ctx: Context, command: str, start: str, started: float) -> Result:
    cwd = qa_cwd(ctx)
    port = chosen_port(int(ctx.config.get("qa", "port", DEFAULT_PORT)))
    ready = ctx.config.get("qa", "ready", DEFAULT_READY)
    seconds = int(ctx.config.get("qa", "ready_timeout", DEFAULT_READY_TIMEOUT))
    return serve_then_cmd(ctx, command, start, cwd, port, ready, seconds, started)


def serve_then_cmd(
    ctx: Context,
    command: str,
    start: str,
    cwd: Path,
    port: int,
    ready: str,
    seconds: int,
    started: float,
) -> Result:
    url = f"http://localhost:{port}{ready}"
    log_path = app_log_path(ctx.root)
    ensure_dir(log_path.parent)
    handle = log_path.open("w", buffering=1)
    process = None
    try:
        process = spawn(start, cwd, port, qa_env(ctx), handle)
        failure = wait_ready(url, process, seconds)
        if failure:
            handle.flush()
            return Result("qa", False, ctx.global_note(failure), log_tail(log_path), elapsed(started))
        return run_cmd(ctx, command, cwd, started, {APP_URL: f"http://localhost:{port}", "PORT": str(port)})
    finally:
        end_app(process, handle)


def run_cmd(ctx: Context, command: str, cwd: Path, started: float, env: dict[str, str] | None = None) -> Result:
    options: dict[str, Any] = {"cwd": cwd, "timeout": cmd_timeout()}
    if env is not None:
        options["env"] = env
    code, output = run(["bash", "-lc", command], **options)
    return qa_result(ctx, code, output, started)


def qa_result(ctx: Context, code: int, output: str, started: float) -> Result:
    summary = "qa passed" if code == 0 else f"qa failed (exit {code})"
    return Result("qa", code == 0, ctx.global_note(summary), tail(output, 40) if code else [], elapsed(started))


def qa_cwd(ctx: Context) -> Path:
    return ctx.root / str(ctx.config.get("qa", "cwd", "."))


def cmd_timeout() -> int:
    raw = os.environ.get(CMD_TIMEOUT_ENV)
    return DEFAULT_CMD_TIMEOUT if raw is None else int(raw)


def qa_env(ctx: Context) -> dict[str, str]:
    raw = ctx.config.get("qa", "env") or {}
    return {str(key): str(value) for key, value in raw.items()}


def app_log_path(root: Path) -> Path:
    base = root / ".marestail"
    task = os.environ.get(TASK_ENV)
    return base / "runs" / task / LOG_NAME if task else base / LOG_NAME


def log_tail(path: Path) -> list[str]:
    return [] if not path.exists() else tail(path.read_text(), 10)


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
    return f"qa: app did not answer on {url} within {seconds}s"


def exited_early(process: subprocess.Popen[Any]) -> str | None:
    code = process.poll()
    return None if code is None else f"qa: app exited with {code} before answering"


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
