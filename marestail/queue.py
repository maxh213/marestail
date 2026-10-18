import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from marestail import config as config_module
from marestail import status
from marestail.context import DEFAULT_BASE
from marestail.pipeline import HYPER, unknown_role, window
from marestail.task_file import TaskFile, check, read

_MARESTAIL_DIR = ".marestail"
_QUEUE_LOG_NAME = "queue.log"
_BOOLEAN_ENV_TRUE = ("1", "true")
_BOOLEAN_ENV_FALSE = ("0", "false", "")


@dataclass
class _SignalState:
    received: int | None = None
    proc: Any = None
    task_id: str = ""


_SIG_STATE = _SignalState()


@dataclass(frozen=True)
class _GitOptions:
    starting_branch: str
    remote: str
    base: str
    push_on: bool
    pr_on: bool
    here: bool


@dataclass(frozen=True)
class _ChildOptions:
    agent: str | None
    model: str | None
    effort: str | None
    scope: str | None
    focus: list[str]
    start: str | None
    stop: str | None


@dataclass
class _QueueOptions:
    root: Path
    queue_dir: Path
    log_path: Path
    state_file: Path
    retry: list[str]
    git: _GitOptions
    child: _ChildOptions
    start_content: dict[Path, str] = field(default_factory=dict)


def run_queue(
    paths: list[str],
    start: str | None,
    stop: str | None,
    here: bool,
    retry: list[str],
    agent: str | None,
    model: str | None,
    effort: str | None,
    scope: str | None,
    focus: list[str],
) -> int:
    root = _find_root(Path.cwd())
    code = _accept_roles(start, stop, scope)
    if code != 0:
        return code
    return _run_with_lock(root, paths, start, stop, here, retry, agent, model, effort, scope, focus)


def _accept_roles(start: str | None, stop: str | None, scope: str | None) -> int:
    mode = HYPER if scope == HYPER else None
    problem = unknown_role(start, mode) or unknown_role(stop, mode)
    if problem is not None:
        sys.stderr.write(f"{problem}\n")
        return 2
    window(start, stop, mode)
    return 0


def _find_root(start: Path) -> Path:
    cfg = config_module.load(start)
    return cfg.root.resolve()


def _run_with_lock(
    root: Path,
    paths: list[str],
    start: str | None,
    stop: str | None,
    here: bool,
    retry: list[str],
    agent: str | None,
    model: str | None,
    effort: str | None,
    scope: str | None,
    focus: list[str],
) -> int:
    queue_dir = root / _MARESTAIL_DIR / "queue"
    lock_code, lock_path = _acquire_lock(queue_dir)
    if lock_code != 0:
        return lock_code
    try:
        return _execute_queue(root, queue_dir, paths, start, stop, here, retry, agent, model, effort, scope, focus)
    finally:
        _remove_lock(lock_path)


def _acquire_lock(queue_dir: Path) -> tuple[int, Path | None]:
    queue_dir.mkdir(parents=True, exist_ok=True)
    lock_path = queue_dir / "lock"
    code, acquired = _try_create_lock(lock_path)
    return code, lock_path if acquired else None


def _remove_lock(lock_path: Path | None) -> None:
    if lock_path is not None:
        with contextlib.suppress(OSError):
            lock_path.unlink(missing_ok=True)


def _try_create_lock(lock_path: Path) -> tuple[int, bool]:
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, f"{os.getpid()}\n".encode())
        os.close(fd)
        return 0, True
    except FileExistsError:
        return _handle_existing_lock(lock_path)


def _handle_existing_lock(lock_path: Path) -> tuple[int, bool]:
    pid = _read_lock_pid(lock_path)
    if pid is not None and os.path.exists(f"/proc/{pid}"):
        sys.stderr.write(f"another marestail queue (pid {pid}) is running in this repository\n")
        return 2, False
    lock_path.write_text(f"{os.getpid()}\n")
    return 0, True


def _read_lock_pid(lock_path: Path) -> int | None:
    try:
        return int(lock_path.read_text().strip())
    except (OSError, ValueError):
        return None


def _execute_queue(
    root: Path,
    queue_dir: Path,
    paths: list[str],
    start: str | None,
    stop: str | None,
    here: bool,
    retry: list[str],
    agent: str | None,
    model: str | None,
    effort: str | None,
    scope: str | None,
    focus: list[str],
) -> int:
    tasks, check_code = _prepare_tasks(paths, root)
    if check_code != 0:
        return check_code
    flight = _pre_flight(root, tasks, retry, here, queue_dir)
    if isinstance(flight, int):
        return flight
    branch, push_on, pr_on, remote, base, state = flight
    state_file = queue_dir / "state.json"
    git_opts = _GitOptions(branch, remote, base, push_on, pr_on, here)
    child_opts = _ChildOptions(agent, model, effort, scope, focus, start, stop)
    opts = _QueueOptions(root, queue_dir, queue_dir / _QUEUE_LOG_NAME, state_file, retry, git_opts, child_opts)
    return _run_pipeline_loop(tasks, opts, state)


def _prepare_tasks(paths: list[str], root: Path) -> tuple[list[TaskFile], int]:
    targets = _resolve_targets(paths)
    err = _check_task_problems(targets)
    if err != 0:
        return [], err
    return _load_validated_tasks(targets, root)


def _resolve_targets(paths: list[str]) -> list[Path]:
    return [Path(p) for p in (paths or ["tasks"])]


def _load_validated_tasks(targets: list[Path], root: Path) -> tuple[list[TaskFile], int]:
    files = _collect_task_files(targets)
    code = _validate_task_paths(files, root)
    if code != 0:
        return [], code
    return [read(f) for f in files], 0


def _collect_task_files(targets: list[Path]) -> list[Path]:
    chosen: dict[Path, Path] = {}
    for target in targets:
        _add_target_files(target, chosen)
    return list(chosen.values())


def _add_target_files(target: Path, chosen: dict[Path, Path]) -> None:
    if target.is_dir():
        _add_folder_files(target, chosen)
    else:
        chosen.setdefault(target.resolve(), target)


def _add_folder_files(folder: Path, chosen: dict[Path, Path]) -> None:
    for child in sorted(folder.glob("*.md")):
        if child.name.lower() != "readme.md":
            chosen.setdefault(child.resolve(), folder / child.name)


def _check_task_problems(targets: list[Path]) -> int:
    problems = [p for p in check(targets) if not _is_missing_dep_problem(p)]
    if problems:
        print("\n".join(problems))
        return 2
    return 0


def _is_missing_dep_problem(problem: str) -> bool:
    return "depends on " in problem and " does not exist" in problem


def _validate_task_paths(files: list[Path], root: Path) -> int:
    for path in files:
        if not path.resolve().is_relative_to(root.resolve()):
            sys.stderr.write(f"{path} is outside the repository; the queue runs only task files inside {root.resolve()}\n")
            return 2
        if _is_gitignored(path, root):
            sys.stderr.write(f"{path} is ignored by git; the queue runs only committed task files\n")
            return 2
    return 0


def _is_gitignored(path: Path, root: Path) -> bool:
    res = _git_run(["check-ignore", str(path)], root)
    return res.returncode == 0


def _pre_flight(
    root: Path, tasks: list[TaskFile], retry: list[str], here: bool, queue_dir: Path
) -> int | tuple[str, bool, bool, str, str, dict[str, Any]]:
    branch = _pre_flight_tree(root)
    if isinstance(branch, int):
        return branch
    state_code, state = _check_load_state(queue_dir / "state.json", root)
    if state_code != 0:
        return state_code
    ready = _pre_flight_config(root, tasks, retry, state, here, queue_dir, branch)
    if isinstance(ready, int):
        return ready
    return *ready, state


def _pre_flight_tree(root: Path) -> int | str:
    branch = _check_head_branch(root)
    if isinstance(branch, int):
        return branch
    clean_code = _check_clean_tree(root)
    if clean_code != 0:
        return clean_code
    run_code = _check_no_running_run(root)
    if run_code != 0:
        return run_code
    return branch


def _pre_flight_config(
    root: Path, tasks: list[TaskFile], retry: list[str], state: dict[str, Any], here: bool, queue_dir: Path, branch: str
) -> int | tuple[str, bool, bool, str, str]:
    retry_code = _check_retry_ids(retry, {t.id for t in tasks}, state, root)
    if retry_code != 0:
        return retry_code
    return _pre_flight_push_base(root, here, queue_dir, branch)


def _pre_flight_push_base(root: Path, here: bool, queue_dir: Path, branch: str) -> int | tuple[str, bool, bool, str, str]:
    cfg = config_module.load(root)
    settings = _check_push_pr(cfg, root)
    if isinstance(settings, int):
        return settings
    push_on, pr_on, remote, base = settings
    base_code = _check_base(root, base, here, queue_dir / _QUEUE_LOG_NAME)
    if base_code != 0:
        return base_code
    return branch, push_on, pr_on, remote, base


def _check_head_branch(root: Path) -> int | str:
    res = _git_run(["symbolic-ref", "--quiet", "--short", "HEAD"], root)
    if res.returncode != 0:
        sys.stderr.write("HEAD is detached; check out a branch first\n")
        return 2
    return res.stdout.strip()


def _check_clean_tree(root: Path) -> int:
    dirty = _get_dirty_paths(root)
    if dirty:
        sys.stderr.write("the working tree has uncommitted changes; commit or stash them first:\n")
        for item in dirty[:20]:
            sys.stderr.write(f"{item}\n")
        return 2
    return 0


def _get_dirty_paths(root: Path) -> list[str]:
    res = _git_run(["status", "--porcelain", "--untracked-files=all"], root)
    return [p for p in (_status_path(line) for line in res.stdout.splitlines()) if p]


def _status_path(line: str) -> str:
    raw = line[3:].strip()
    target = raw.split(" -> ")[1].strip() if " -> " in raw else raw
    if _is_marestail_path(target):
        return ""
    return target


def _is_marestail_path(target: str) -> bool:
    return target == _MARESTAIL_DIR or target.startswith(f"{_MARESTAIL_DIR}/")


def _check_no_running_run(root: Path) -> int:
    records, _ = status.scan([root])
    for record in records:
        if record.get("state") == "running":
            task_id = record.get("task")
            pid = record.get("pid")
            sys.stderr.write(f"a marestail run is running here ({task_id}, pid {pid}); the queue would change the tree under it\n")
            return 2
    return 0


def _check_load_state(state_path: Path, root: Path) -> tuple[int, dict[str, Any]]:
    if not state_path.exists():
        return 0, {"v": 1, "tasks": {}}
    return _parse_state_file(state_path, root)


def _parse_state_file(state_path: Path, root: Path) -> tuple[int, dict[str, Any]]:
    rel = state_path.relative_to(root) if state_path.is_relative_to(root) else state_path
    try:
        data = json.loads(state_path.read_text())
    except json.JSONDecodeError as err:
        sys.stderr.write(f"{rel}: not valid JSON: {err}\n")
        return 2, {}
    if data.get("v") != 1:
        sys.stderr.write(f"{rel}: v must be 1, got {data.get('v')}\n")
        return 2, {}
    return 0, data


def _check_retry_ids(retry: list[str], task_ids: set[str], state: dict[str, Any], root: Path) -> int:
    for task_id in retry:
        code = _check_single_retry(task_id, task_ids, state, root)
        if code != 0:
            return code
    return 0


def _check_single_retry(task_id: str, task_ids: set[str], state: dict[str, Any], root: Path) -> int:
    if task_id not in task_ids:
        sys.stderr.write(f"--retry {task_id}: not a task in this queue\n")
        return 2
    st = _stored_status(task_id, state, root)
    if st not in ("stopped", "failed"):
        sys.stderr.write(f"--retry {task_id}: it is {st}, not stopped or failed\n")
        return 2
    return 0


def _stored_status(task_id: str, state: dict[str, Any], root: Path) -> str:
    known = _state_task_status(task_id, state)
    if known is not None:
        return known
    return _reader_task_status(root, task_id)


def _state_task_status(task_id: str, state: dict[str, Any]) -> str | None:
    if task_id in state.get("tasks", {}):
        return str(state["tasks"][task_id].get("status") or "never-started")
    return None


def _reader_task_status(root: Path, task_id: str) -> str:
    rec = status.read_run(root, task_id)
    known = _classify_rec_status(rec)
    if known is not None:
        return known
    return str(rec.get("state") or "never-started")


def _classify_rec_status(rec: dict[str, Any]) -> str | None:
    res = rec.get("result")
    if res == "complete" or rec.get("state") == "complete":
        return "done"
    if res in ("stopped", "failed"):
        return str(res)
    return None


def _check_push_pr(cfg: config_module.Config, root: Path) -> int | tuple[bool, bool, str, str]:
    settings = _read_push_pr_settings(cfg)
    if isinstance(settings, int):
        return settings
    push_val, pr_val = settings
    base = cfg.get("git", "base", DEFAULT_BASE)
    remote = _find_target_remote(base, root)
    val_code = _validate_push_pr_settings(push_val, pr_val, remote, root)
    if val_code != 0:
        return val_code
    return push_val, pr_val, remote, base


def _validate_push_pr_settings(push_val: bool, pr_val: bool, remote: str, root: Path) -> int:
    err = _validate_pr_needs_push(push_val, pr_val)
    if err != 0:
        return err
    return _validate_remote_and_gh(push_val, pr_val, remote, root)


def _validate_pr_needs_push(push_val: bool, pr_val: bool) -> int:
    if pr_val and not push_val:
        sys.stderr.write("[queue] pr needs [queue] push\n")
        return 2
    return 0


def _validate_remote_and_gh(push_val: bool, pr_val: bool, remote: str, root: Path) -> int:
    err = _validate_push_remote(push_val, remote, root)
    if err != 0:
        return err
    return _validate_pr_gh(pr_val)


def _validate_push_remote(push_val: bool, remote: str, root: Path) -> int:
    if push_val and remote not in _git_remotes(root):
        sys.stderr.write(f"[queue] push is on but this repository has no remote named {remote}\n")
        return 2
    return 0


def _validate_pr_gh(pr_val: bool) -> int:
    if pr_val and shutil.which("gh") is None:
        sys.stderr.write("[queue] pr needs the gh CLI on PATH\n")
        return 2
    return 0


def _read_push_pr_settings(cfg: config_module.Config) -> int | tuple[bool, bool]:
    from_file = _read_toml_push_pr(cfg)
    if isinstance(from_file, int):
        return from_file
    from_env = _read_env_push_pr()
    if isinstance(from_env, int):
        return from_env
    push_toml, pr_toml = from_file
    push_env, pr_env = from_env
    return _pick_setting(push_env, push_toml), _pick_setting(pr_env, pr_toml)


def _pick_setting(env_val: bool | None, toml_val: bool) -> bool:
    return env_val if env_val is not None else toml_val


def _read_toml_push_pr(cfg: config_module.Config) -> int | tuple[bool, bool]:
    push_raw = cfg.raw.get("queue", {}).get("push", False)
    if not isinstance(push_raw, bool):
        sys.stderr.write(f"[queue] push must be true or false, got {push_raw!r}\n")
        return 2
    pr_raw = cfg.raw.get("queue", {}).get("pr", False)
    if not isinstance(pr_raw, bool):
        sys.stderr.write(f"[queue] pr must be true or false, got {pr_raw!r}\n")
        return 2
    return push_raw, pr_raw


def _env_flag(name: str) -> bool | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    if raw.lower() in _BOOLEAN_ENV_TRUE:
        return True
    if raw.lower() in _BOOLEAN_ENV_FALSE:
        return False
    raise ValueError(f"{name} must be 1, 0, true or false, got '{raw}'")


def _read_env_push_pr() -> int | tuple[bool | None, bool | None]:
    try:
        return _env_flag("MARESTAIL_QUEUE_PUSH"), _env_flag("MARESTAIL_QUEUE_PR")
    except ValueError as error:
        sys.stderr.write(f"{error}\n")
        return 2


def _find_target_remote(base: str, root: Path) -> str:
    remotes = _git_remotes(root)
    if "/" in base:
        part = base.split("/")[0]
        if part in remotes:
            return part
    return "origin"


def _check_base(root: Path, base: str, here: bool, log_path: Path) -> int:
    if here:
        return 0
    _maybe_fetch_base(root, base, log_path)
    res = _git_run(["rev-parse", "--verify", "--quiet", f"{base}^{{commit}}"], root)
    if res.returncode != 0:
        sys.stderr.write(f"[git] base {base} does not exist; set it in marestail.toml\n")
        return 2
    return 0


def _maybe_fetch_base(root: Path, base: str, log_path: Path) -> None:
    remote = _base_remote(base, root)
    if remote:
        proc = _git_run(["fetch", "--quiet", remote], root)
        if proc.returncode != 0:
            _log_event(log_path, f"fetch {remote} failed: {_last_stderr_line(proc.stderr)}")


def _base_remote(base: str, root: Path) -> str | None:
    if "/" not in base:
        return None
    part = base.split("/")[0]
    return part if part in _git_remotes(root) else None


def _git_remotes(root: Path) -> list[str]:
    res = _git_run(["remote"], root)
    return [r.strip() for r in res.stdout.splitlines() if r.strip()]


def _git_run(args: list[str], root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False)


def _git_short(rev: str, root: Path) -> str:
    return _git_run(["rev-parse", "--short", rev], root).stdout.strip()


def _run_gh(args: list[str], root: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged_env = {**os.environ, **(env or {})}
    return subprocess.run(["gh", *args], cwd=root, capture_output=True, text=True, check=False, env=merged_env)


def _start_child(argv: list[str], cwd: Path, env: dict[str, str]) -> Any:
    return subprocess.Popen(argv, cwd=cwd, env=env, start_new_session=True)


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _format_iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _log_event(log_path: Path, msg: str, now_dt: datetime | None = None) -> None:
    ts = _format_iso(now_dt or _now_utc())
    line = f"{ts} {msg}"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"{line}\n")
    print(f"queue: {line}")
    sys.stdout.flush()


def _write_state(state_path: Path, state: dict[str, Any]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = state_path.parent / f"state.{os.getpid()}.tmp"
    temp_path.write_text(json.dumps(state, indent=2))
    os.replace(temp_path, state_path)


def _last_stderr_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _order_tasks(tasks: list[TaskFile]) -> list[TaskFile]:
    task_ids = {t.id for t in tasks}
    ordered: list[TaskFile] = []
    remaining = list(tasks)
    while remaining:
        _step_next_task(remaining, task_ids, ordered)
    return ordered


def _step_next_task(remaining: list[TaskFile], task_ids: set[str], ordered: list[TaskFile]) -> None:
    pick = _pick_next_ready(remaining, task_ids, ordered)
    ordered.append(pick)
    remaining.remove(pick)


def _pick_next_ready(remaining: list[TaskFile], task_ids: set[str], ordered: list[TaskFile]) -> TaskFile:
    ready = [t for t in remaining if _deps_ordered(t, task_ids, ordered)]
    return min(ready, key=_task_file_name)


def _task_file_name(task: TaskFile) -> str:
    return task.path.name


def _deps_ordered(task: TaskFile, task_ids: set[str], ordered: list[TaskFile]) -> bool:
    return all(dep not in task_ids or any(o.id == dep for o in ordered) for dep in task.depends)


def _run_pipeline_loop(tasks: list[TaskFile], opts: _QueueOptions, state: dict[str, Any]) -> int:
    ordered = _order_tasks(tasks)
    opts.start_content.update({t.path.resolve(): t.path.read_text() for t in ordered})
    _clear_non_sticky(ordered, state, opts.retry)
    old_handlers = _install_signals(opts.root, opts.log_path)
    try:
        code = _process_all_tasks(ordered, state, opts)
    finally:
        _restore_signals(old_handlers)
    _switch_back_clean(opts.root, opts.git.starting_branch, opts.git.here, opts.log_path)
    return code


def _clear_non_sticky(ordered: list[TaskFile], state: dict[str, Any], retry: list[str]) -> None:
    for t in ordered:
        if _should_clear_task(t.id, state, retry):
            del state["tasks"][t.id]


def _should_clear_task(task_id: str, state: dict[str, Any], retry: list[str]) -> bool:
    if task_id in state.get("tasks", {}):
        st = state["tasks"][task_id].get("status")
        return bool(st == "blocked" or task_id in retry)
    return False


def _reset_signals() -> None:
    _SIG_STATE.received = None
    _SIG_STATE.proc = None
    _SIG_STATE.task_id = ""


def _install_signals(root: Path, log_path: Path) -> tuple[Any, Any]:
    _reset_signals()

    def _sig_handler(signum: int, _frame: Any) -> None:
        _SIG_STATE.received = signum
        if _SIG_STATE.proc is not None:
            _terminate_child(_SIG_STATE.proc, _SIG_STATE.task_id, root, log_path, signum)
            sys.exit(128 + signum)

    old_term = signal.signal(signal.SIGTERM, _sig_handler)
    old_int = signal.signal(signal.SIGINT, _sig_handler)
    return old_term, old_int


def _restore_signals(handlers: tuple[Any, Any]) -> None:
    signal.signal(signal.SIGTERM, handlers[0])
    signal.signal(signal.SIGINT, handlers[1])
    _reset_signals()


def _terminate_child(proc: Any, task_id: str, root: Path, log_path: Path, signum: int) -> None:
    _kill_child_process(proc)
    rec = status.read_run(root, task_id)
    role = rec.get("role") or "coder"
    _log_event(log_path, f"interrupted {task_id} by signal {signum}; the next run resumes from {role}")


def _kill_child_process(proc: Any) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=30)
    except (subprocess.TimeoutExpired, OSError):
        with contextlib.suppress(OSError):
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()


def _process_all_tasks(ordered: list[TaskFile], state: dict[str, Any], opts: _QueueOptions) -> int:
    stop_code = _iterate_tasks(ordered, state, opts)
    if stop_code is not None:
        return stop_code
    _log_summary(ordered, state, opts.log_path)
    return _exit_verdict(ordered, state)


def _exit_verdict(ordered: list[TaskFile], state: dict[str, Any]) -> int:
    if all(_is_done(t.id, state) for t in ordered):
        return 0
    return 1


def _iterate_tasks(ordered: list[TaskFile], state: dict[str, Any], opts: _QueueOptions) -> int | None:
    start_used = False
    for t in ordered:
        _stop_if_signalled()
        code, start_used, stop_now = _handle_task_in_loop(t, ordered, state, opts, start_used)
        if stop_now:
            return code
    return None


def _stop_if_signalled() -> None:
    signum = _SIG_STATE.received
    if signum is not None:
        sys.exit(128 + signum)


def _handle_task_in_loop(
    task: TaskFile,
    ordered: list[TaskFile],
    state: dict[str, Any],
    opts: _QueueOptions,
    start_used: bool,
) -> tuple[int, bool, bool]:
    if _is_done(task.id, state):
        _log_event(opts.log_path, f"skip {task.id} done")
        return 0, start_used, False
    return _run_task_step(task, ordered, state, opts, start_used)


def _run_task_step(
    task: TaskFile,
    ordered: list[TaskFile],
    state: dict[str, Any],
    opts: _QueueOptions,
    start_used: bool,
) -> tuple[int, bool, bool]:
    res_code, new_used = _step_one_task(task, ordered, state, opts, start_used)
    stop_now = _should_stop_queue(res_code, opts.git.here, state, task.id)
    return res_code, new_used, stop_now


def _should_stop_queue(res_code: int, here: bool, state: dict[str, Any], task_id: str) -> bool:
    if res_code == 2:
        return True
    if res_code == 1:
        return _failure_stops_queue(here, state, task_id)
    return False


def _failure_stops_queue(here: bool, state: dict[str, Any], task_id: str) -> bool:
    if here:
        return True
    return _was_uncommitted_stop(state, task_id)


def _is_done(task_id: str, state: dict[str, Any]) -> bool:
    return bool(state.get("tasks", {}).get(task_id, {}).get("status") == "done")


def _was_uncommitted_stop(state: dict[str, Any], task_id: str) -> bool:
    return task_id not in state.get("tasks", {})


def _step_one_task(
    task: TaskFile,
    ordered: list[TaskFile],
    state: dict[str, Any],
    opts: _QueueOptions,
    start_used: bool,
) -> tuple[int, bool]:
    blocked_reason = _find_blocked_reason(task, state, opts.root, opts.git.here, opts.git.base, opts.log_path)
    if blocked_reason is not None:
        _record_persisted_state(task.id, "blocked", blocked_reason, state, opts.state_file, opts.log_path)
        return 0, start_used
    action, reason = _determine_initial_action(task.id, state, opts.retry, opts.root)
    if action in ("stopped", "failed"):
        _keep_sticky(task.id, action, reason, state, opts.state_file, opts.log_path)
        return 0, start_used
    return _execute_child_flow(task, ordered, state, opts, start_used)


def _keep_sticky(task_id: str, action: str, reason: str | None, state: dict[str, Any], state_path: Path, log_path: Path) -> None:
    if _already_sticky(state, task_id, action):
        _log_event(log_path, f"{action} {task_id} {reason}")
        return
    _record_persisted_state(task_id, action, reason, state, state_path, log_path)


def _already_sticky(state: dict[str, Any], task_id: str, action: str) -> bool:
    entry = state.get("tasks", {}).get(task_id)
    return isinstance(entry, dict) and entry.get("status") == action


def _determine_initial_action(task_id: str, state: dict[str, Any], retry: list[str], root: Path) -> tuple[str, str | None]:
    early = _state_or_retry_action(task_id, state, retry)
    if early is not None:
        return early
    return _read_run_action(root, task_id)


def _state_or_retry_action(task_id: str, state: dict[str, Any], retry: list[str]) -> tuple[str, str | None] | None:
    if task_id in state.get("tasks", {}):
        return state["tasks"][task_id]["status"], state["tasks"][task_id].get("reason")
    if task_id in retry:
        return "run", None
    return None


def _read_run_action(root: Path, task_id: str) -> tuple[str, str | None]:
    rec = status.read_run(root, task_id)
    res = rec.get("result")
    if res == "stopped":
        where = rec.get("stopped_at")
        why = rec.get("reason")
        return "stopped", f"stopped at {where}: {why}" if where else str(why)
    if res == "failed":
        return "failed", rec.get("reason")
    return "run", None


def _state_entry(
    st: str, branch: str | None, head: str | None, to: str | None, exit_code: int | None, reason: str | None
) -> dict[str, Any]:
    return {
        "status": st,
        "branch": branch,
        "head": head,
        "finished_at": _format_iso(_now_utc()),
        "to": to,
        "exit_code": exit_code,
        "reason": reason,
    }


def _record_persisted_state(task_id: str, st: str, reason: str | None, state: dict[str, Any], state_path: Path, log_path: Path) -> None:
    state["tasks"][task_id] = _state_entry(st, None, None, None, None, reason)
    _write_state(state_path, state)
    _log_event(log_path, f"{st} {task_id} {reason}")


def _find_blocked_reason(task: TaskFile, state: dict[str, Any], root: Path, here: bool, base: str, log_path: Path) -> str | None:
    for dep in task.depends:
        reason = _dep_blocked_reason(dep, state, root)
        if reason is not None:
            return reason
    return _check_stack_ancestor_blocked(task, state, root, here, base, log_path)


def _dep_blocked_reason(dep: str, state: dict[str, Any], root: Path) -> str | None:
    if dep in state.get("tasks", {}):
        return _state_dep_reason(dep, state["tasks"][dep])
    return _external_dep_reason(dep, state, root)


def _state_dep_reason(dep: str, entry: dict[str, Any]) -> str | None:
    st = entry.get("status")
    if st in ("failed", "stopped"):
        return f"{dep} {st}"
    return _blocked_dep_reason(st, entry)


def _blocked_dep_reason(st: Any, entry: dict[str, Any]) -> str | None:
    if st == "blocked":
        return str(entry.get("reason") or "")
    return None


def _external_dep_reason(dep: str, state: dict[str, Any], root: Path) -> str | None:
    rec = status.read_run(root, dep)
    if rec.get("result") == "complete" or rec.get("state") == "complete":
        _record_external_done(dep, rec, state)
        return None
    return f"{dep} is not in this queue and has not completed"


def _record_external_done(dep: str, rec: dict[str, Any], state: dict[str, Any]) -> None:
    has_events = rec.get("events") is not None
    state.setdefault("tasks", {})[dep] = _state_entry(
        "done",
        rec.get("branch") if has_events else None,
        rec.get("head") if has_events else None,
        None,
        0,
        None,
    )


def _check_stack_ancestor_blocked(task: TaskFile, state: dict[str, Any], root: Path, here: bool, base: str, log_path: Path) -> str | None:
    if here:
        return None
    if task.stack is True:
        dep = task.depends[0]
        if not state["tasks"][dep].get("branch"):
            return f"{dep} has no recorded branch; run it with --here or rerun it through the queue"
        return None
    return _check_ancestor_merges(task, state, root, base, log_path)


def _check_ancestor_merges(task: TaskFile, state: dict[str, Any], root: Path, base: str, log_path: Path) -> str | None:
    for dep in task.depends:
        head = state["tasks"][dep].get("head")
        if not head:
            return f"{dep} has no recorded branch; run it with --here or rerun it through the queue"
        _maybe_fetch_base(root, base, log_path)
        res = _git_run(["merge-base", "--is-ancestor", head, base], root)
        if res.returncode != 0:
            short = _git_short(head, root)
            return f"waiting for {dep} ({short}) to be merged into {base}"
    return None


def _execute_child_flow(
    task: TaskFile,
    ordered: list[TaskFile],
    state: dict[str, Any],
    opts: _QueueOptions,
    start_used: bool,
) -> tuple[int, bool]:
    branch, where, switch_code = _prepare_branch_for_task(
        task, opts.git.here, state, opts.git.base, opts.root, opts.log_path, opts.start_content
    )
    if switch_code != 0:
        return 1, start_used
    role, new_used = _choose_start_role(task.id, opts.child.start, start_used, opts.retry, opts.root)
    child_code, outcome = _run_child_process(task, opts.root, opts.log_path, branch, where, role, opts.child)
    if child_code == 2:
        return 2, new_used
    _update_state_record(task.id, branch, opts.child.stop, outcome, state, opts.state_file, opts.root)
    _after_child_finish(
        task,
        branch,
        outcome["status"],
        opts.git.push_on,
        opts.git.pr_on,
        opts.git.remote,
        opts.git.base,
        ordered,
        state,
        opts.root,
        opts.log_path,
    )
    return (1 if outcome["status"] in ("failed", "stopped") else 0), new_used


def _prepare_branch_for_task(
    task: TaskFile,
    here: bool,
    state: dict[str, Any],
    base: str,
    root: Path,
    log_path: Path,
    start_content: dict[Path, str],
) -> tuple[str, str, int]:
    if here:
        return _git_current_branch(root), "here", 0
    clean_code = _check_clean_tree(root)
    if clean_code != 0:
        _log_event(log_path, f"stopped before {task.id}: the working tree has uncommitted changes")
        cur = _git_current_branch(root)
        _log_event(log_path, f"left on {cur}: the working tree has uncommitted changes")
        return "", "", 1
    branch, where = _switch_task_branch(task, state, base, root)
    _commit_task_file_if_needed(task, root, start_content.get(task.path.resolve(), ""))
    return branch, where, 0


def _git_current_branch(root: Path) -> str:
    return _git_run(["symbolic-ref", "--quiet", "--short", "HEAD"], root).stdout.strip()


def _switch_task_branch(task: TaskFile, state: dict[str, Any], base: str, root: Path) -> tuple[str, str]:
    if task.stack is True:
        dep = task.depends[0]
        dep_branch = str(state["tasks"][dep]["branch"])
        _git_run(["switch", "--quiet", dep_branch], root)
        return dep_branch, dep_branch
    target = f"marestail/{task.id}"
    if _branch_exists_locally(target, root):
        _git_run(["switch", "--quiet", target], root)
        return target, "existing"
    _git_run(["switch", "--quiet", "--no-track", "--create", target, base], root)
    return target, base


def _branch_exists_locally(name: str, root: Path) -> bool:
    res = _git_run(["rev-parse", "--verify", "--quiet", f"refs/heads/{name}"], root)
    return res.returncode == 0


def _commit_task_file_if_needed(task: TaskFile, root: Path, expected: str) -> None:
    if _worktree_matches(task.path, expected):
        return
    _commit_task_copy(task, root, expected)


def _worktree_matches(path: Path, expected: str) -> bool:
    try:
        return path.read_text() == expected
    except OSError:
        return False


def _commit_task_copy(task: TaskFile, root: Path, expected: str) -> None:
    rel = _relative_task(task, root)
    task.path.parent.mkdir(parents=True, exist_ok=True)
    task.path.write_text(expected)
    _git_run(["add", "--", str(rel)], root)
    _git_run(["commit", "--quiet", "-m", f"queue: task {task.id}", "--", str(rel)], root)


def _relative_task(task: TaskFile, root: Path) -> Path:
    if task.path.is_absolute():
        return task.path.relative_to(root)
    return task.path


def _choose_start_role(task_id: str, start: str | None, start_used: bool, retry: list[str], root: Path) -> tuple[str | None, bool]:
    cli_role = _cli_start_role(start, start_used)
    if cli_role is not None:
        return cli_role, True
    return _reader_start_role(root, task_id, retry, start_used)


def _cli_start_role(start: str | None, start_used: bool) -> str | None:
    if not start_used and start:
        return start
    return None


def _reader_start_role(root: Path, task_id: str, retry: list[str], start_used: bool) -> tuple[str | None, bool]:
    rec = status.read_run(root, task_id)
    if rec.get("state") in ("dead", "interrupted") or task_id in retry:
        return rec.get("role"), start_used or bool(rec.get("role"))
    return None, start_used


def _run_child_process(
    task: TaskFile,
    root: Path,
    log_path: Path,
    branch: str,
    where: str,
    role: str | None,
    child: _ChildOptions,
) -> tuple[int, dict[str, Any]]:
    argv = _build_child_argv(task, root, role, child)
    env = _build_child_env()
    log_detail = f"branch {branch} from {where}" + (f" --from {role}" if role else "")
    _log_event(log_path, f"start {task.id} {log_detail}")
    start_time = _now_utc()
    proc = _start_child(argv, root, env)
    _SIG_STATE.proc = proc
    _SIG_STATE.task_id = task.id
    code = proc.wait()
    _SIG_STATE.proc = None
    elapsed_min = int((_now_utc() - start_time).total_seconds() // 60)
    return _inspect_child_run(root, task.id, proc.pid, code, log_path, elapsed_min)


def _build_child_argv(task: TaskFile, root: Path, role: str | None, child: _ChildOptions) -> list[str]:
    cli_file = getattr(sys.modules.get("marestail.cli"), "__file__", None) or ""
    cli_path = str(Path(cli_file).resolve())
    rel_path = task.path.resolve().relative_to(root.resolve())
    argv = [sys.executable, "-u", cli_path, "run", str(rel_path), "--auto"]
    _append_role_flags(argv, role, child.stop)
    _append_agent_flags(argv, child)
    _append_focus_flags(argv, child.focus)
    return argv


def _append_role_flags(argv: list[str], role: str | None, stop: str | None) -> None:
    if role:
        argv.extend(["--from", role])
    if stop:
        argv.extend(["--to", stop])


def _append_agent_flags(argv: list[str], child: _ChildOptions) -> None:
    _append_agent_model(argv, child.agent, child.model)
    _append_effort_scope(argv, child.effort, child.scope)


def _append_agent_model(argv: list[str], agent: str | None, model: str | None) -> None:
    if agent:
        argv.extend(["--agent", agent])
    if model:
        argv.extend(["--model", model])


def _append_effort_scope(argv: list[str], effort: str | None, scope: str | None) -> None:
    if effort:
        argv.extend(["--effort", effort])
    if scope:
        argv.extend(["--scope", scope])


def _append_focus_flags(argv: list[str], focus: list[str]) -> None:
    for f in focus:
        argv.extend(["--focus", f])


def _build_child_env() -> dict[str, str]:
    env = dict(os.environ)
    if "MARESTAIL_LIMIT_WAITS" not in env:
        env["MARESTAIL_LIMIT_WAITS"] = "36"
    return env


def _inspect_child_run(root: Path, task_id: str, pid: int, exit_code: int, log_path: Path, elapsed_min: int) -> tuple[int, dict[str, Any]]:
    rec = status.read_run(root, task_id)
    if rec.get("pid") != pid:
        sys.stderr.write(f"marestail run exited {exit_code} before it started the pipeline\n")
        return 2, {}
    st, reason = _map_outcome(rec, exit_code)
    _emit_outcome_log(log_path, task_id, st, reason, elapsed_min, root)
    return 0, {"status": st, "reason": reason, "exit_code": exit_code}


def _map_outcome(rec: dict[str, Any], exit_code: int) -> tuple[str, str | None]:
    res = rec.get("result")
    if res == "complete":
        return _map_complete(exit_code)
    return _map_incomplete(rec, res, exit_code)


def _map_complete(exit_code: int) -> tuple[str, str | None]:
    if exit_code == 0:
        return "done", None
    return "stopped", "complete but NOT verified against the running app"


def _map_incomplete(rec: dict[str, Any], res: str | None, exit_code: int) -> tuple[str, str | None]:
    if res == "stopped":
        return "stopped", f"stopped at {rec.get('stopped_at')}: {rec.get('reason')}"
    if res == "failed":
        return "failed", rec.get("reason")
    role = rec.get("role")
    return "failed", f"marestail run ended without finishing (exit {exit_code}) at {role}"


def _emit_outcome_log(log_path: Path, task_id: str, st: str, reason: str | None, elapsed_min: int, root: Path) -> None:
    if st == "done":
        short = _git_short(_git_head(root), root)
        _log_event(log_path, f"done {task_id} {short} {elapsed_min}")
    else:
        _log_event(log_path, f"{st} {task_id} {reason}")


def _git_head(root: Path) -> str:
    return _git_run(["rev-parse", "HEAD"], root).stdout.strip()


def _update_state_record(
    task_id: str, branch: str, stop: str | None, outcome: dict[str, Any], state: dict[str, Any], state_path: Path, root: Path
) -> None:
    state.setdefault("tasks", {})[task_id] = _state_entry(
        outcome["status"], branch, _git_head(root), stop, outcome["exit_code"], outcome["reason"]
    )
    _write_state(state_path, state)


def _after_child_finish(
    task: TaskFile,
    branch: str,
    task_status: str,
    push_on: bool,
    pr_on: bool,
    remote: str,
    base: str,
    ordered: list[TaskFile],
    state: dict[str, Any],
    root: Path,
    log_path: Path,
) -> None:
    _stop_if_signalled()
    if push_on:
        _push_branch(task.id, branch, remote, root, log_path)
        if pr_on and task_status == "done":
            _maybe_open_pr(task, branch, base, ordered, state, root, log_path)


def _push_branch(task_id: str, branch: str, remote: str, root: Path, log_path: Path) -> None:
    res = _git_run(["push", "--quiet", "-u", remote, branch], root)
    if res.returncode == 0:
        _log_event(log_path, f"pushed {task_id} {remote} {branch}")
    else:
        _log_event(log_path, f"push-failed {task_id} {branch}: {_last_stderr_line(res.stderr)}")


def _maybe_open_pr(
    task: TaskFile, branch: str, base: str, ordered: list[TaskFile], state: dict[str, Any], root: Path, log_path: Path
) -> None:
    res = _run_gh(["pr", "list", "--head", branch, "--state", "open", "--json", "number", "--jq", "length"], root)
    if res.stdout.strip() != "0":
        return
    rel_body = f"{_MARESTAIL_DIR}/queue/pr-{task.id}.md"
    pr_file = root / rel_body
    pr_file.parent.mkdir(parents=True, exist_ok=True)
    pr_file.write_text(_format_pr_body(task.id, branch, ordered, state))
    base_branch = base.split("/", 1)[1] if "/" in base else base
    cmd = ["pr", "create", "--base", base_branch, "--head", branch, "--title", _task_title(task), "--body-file", rel_body]
    created = _run_gh(cmd, root, {"GH_PROMPT_DISABLED": "1"})
    if created.returncode == 0:
        _log_event(log_path, f"pr-opened {task.id} {branch}")
    else:
        _log_event(log_path, f"pr-failed {task.id} {branch}: {_last_stderr_line(created.stderr)}")


def _format_pr_body(task_id: str, branch: str, tasks: list[TaskFile], state: dict[str, Any]) -> str:
    lines = [f"- {t.id}: {_task_title(t)}" for t in _tasks_on_branch(branch, tasks, state)]
    task_lines = "\n".join(lines)
    return (
        f"`marestail queue` opened this pull request after task `{task_id}` finished on `{branch}`.\n\n"
        f"Tasks done on this branch:\n{task_lines}\n\n"
        "Each commit is stamped with the role and model that made it.\n"
    )


def _tasks_on_branch(branch: str, tasks: list[TaskFile], state: dict[str, Any]) -> list[TaskFile]:
    matched = []
    for t in tasks:
        entry = state.get("tasks", {}).get(t.id, {})
        if entry.get("branch") == branch and entry.get("status") == "done":
            matched.append(t)
    return matched


def _task_title(task: TaskFile) -> str:
    for line in task.body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return task.id


def _log_summary(ordered: list[TaskFile], state: dict[str, Any], log_path: Path) -> None:
    counts = _count_statuses(ordered, state)
    done_c = counts["done"]
    failed_c = counts["failed"]
    stopped_c = counts["stopped"]
    blocked_c = counts["blocked"]
    _log_event(log_path, f"end done {done_c} failed {failed_c} stopped {stopped_c} blocked {blocked_c}")


def _count_statuses(ordered: list[TaskFile], state: dict[str, Any]) -> dict[str, int]:
    counts = {"done": 0, "failed": 0, "stopped": 0, "blocked": 0}
    for t in ordered:
        st = state.get("tasks", {}).get(t.id, {}).get("status")
        if st in counts:
            counts[st] += 1
    return counts


def _switch_back_clean(root: Path, branch: str, here: bool, log_path: Path) -> None:
    if here:
        return
    cur = _git_current_branch(root)
    if cur == branch:
        return
    clean_code = _check_clean_tree(root)
    if clean_code != 0:
        _log_event(log_path, f"left on {cur}: the working tree has uncommitted changes")
        return
    _git_run(["switch", "--quiet", branch], root)
