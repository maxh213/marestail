import os
import time
from pathlib import Path

from marestail import remote
from marestail.context import Context
from marestail.gates import _serve
from marestail.report import Result, elapsed
from marestail.shell import tail

_DEFAULT_PORT = 3400
_DEFAULT_READY = "/"
_DEFAULT_READY_TIMEOUT = 180
_DEFAULT_CMD_TIMEOUT = 3600
_APP_URL = "MARESTAIL_APP_URL"
_TASK_ENV = "MARESTAIL_TASK"
_CMD_TIMEOUT_ENV = "MARESTAIL_QA_CMD_TIMEOUT"
_LOG_NAME = "qa-app.log"
_GATE = "qa"


def run_gate(ctx: Context) -> Result:
    started = time.time()
    command = ctx.config.get("qa", "cmd")
    if not command:
        return Result.skipped("qa", "no [qa] cmd configured")
    start = ctx.config.get("qa", "start")
    if start:
        return _run_with_app(ctx, command, start, started)
    return _run_cmd(ctx, command, _qa_cwd(ctx), started)


def _run_with_app(ctx: Context, command: str, start: str, started: float) -> Result:
    if remote.offloaded(ctx, _GATE):
        return _served_on_box(ctx, command, start, started)
    cwd = _qa_cwd(ctx)
    preferred = int(ctx.config.get("qa", "port", _DEFAULT_PORT))
    ready = ctx.config.get("qa", "ready", _DEFAULT_READY)
    seconds = int(ctx.config.get("qa", "ready_timeout", _DEFAULT_READY_TIMEOUT))
    log_path = _app_log_path(ctx.root)
    with _serve.ready_app(start, cwd, preferred, ready, seconds, _qa_env(ctx), log_path) as (failure, port):
        if failure:
            return Result("qa", False, ctx.global_note(f"qa: {failure}"), _log_tail(log_path), elapsed(started))
        return _run_cmd(ctx, command, cwd, started, {_APP_URL: f"http://localhost:{port}", "PORT": str(port)})


def _served_on_box(ctx: Context, command: str, start: str, started: float) -> Result:
    seconds = int(ctx.config.get("qa", "ready_timeout", _DEFAULT_READY_TIMEOUT))
    preferred = int(ctx.config.get("qa", "port", _DEFAULT_PORT))
    log_path = _app_log_path(ctx.root)
    ready = str(ctx.config.get("qa", "ready", _DEFAULT_READY))
    served = _serve.served_command(start, command, ready, preferred, seconds, log_path, _qa_env(ctx))
    pull = (str(log_path.relative_to(ctx.root)),)
    outcome = remote.run_job(ctx, _GATE, lambda _workers: served, _qa_cwd(ctx), timeout=_cmd_timeout() + seconds + 60, pull=pull)
    return _box_result(ctx, outcome, log_path, started).with_where(outcome.where)


def _box_result(ctx: Context, outcome: remote.Outcome, log_path: Path, started: float) -> Result:
    failure = _serve.serve_failure(outcome.output)
    if failure:
        return Result(_GATE, False, ctx.global_note(f"qa: {failure}"), _log_tail(log_path), elapsed(started))
    return _qa_result(ctx, outcome.code, outcome.output, started)


def _run_cmd(ctx: Context, command: str, cwd: Path, started: float, env: dict[str, str] | None = None) -> Result:
    outcome = remote.run_job(ctx, _GATE, lambda _workers: ["bash", "-lc", command], cwd, env=env, timeout=_cmd_timeout())
    return _qa_result(ctx, outcome.code, outcome.output, started).with_where(outcome.where)


def _qa_result(ctx: Context, code: int, output: str, started: float) -> Result:
    summary = "qa passed" if code == 0 else f"qa failed (exit {code})"
    return Result("qa", code == 0, ctx.global_note(summary), tail(output, 40) if code else [], elapsed(started))


def _qa_cwd(ctx: Context) -> Path:
    return ctx.root / str(ctx.config.get("qa", "cwd", "."))


def _cmd_timeout() -> int:
    raw = os.environ.get(_CMD_TIMEOUT_ENV)
    return _DEFAULT_CMD_TIMEOUT if raw is None else int(raw)


def _qa_env(ctx: Context) -> dict[str, str]:
    raw = ctx.config.get("qa", "env") or {}
    return {str(key): str(value) for key, value in raw.items()}


def _app_log_path(root: Path) -> Path:
    base = root / ".marestail"
    task = os.environ.get(_TASK_ENV)
    return base / "runs" / task / _LOG_NAME if task else base / _LOG_NAME


def _log_tail(path: Path) -> list[str]:
    return [] if not path.exists() else tail(path.read_text(), 10)
