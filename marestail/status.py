import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from marestail.shell import run

_EVENTS = "events.jsonl"
_LOG = "pipeline.log"
_META = ".marestail"
_NO_LOG = " (no pipeline.log)"
_KEPT = {"run-start", "step-start", "step-end", "wait", "run-end"}
_HIDDEN = {"complete", "abandoned", "leftover"}
_STATE_ORDER = ["running", "dead", "stopped", "never-started", "complete", "abandoned", "leftover"]
_SKIP = {
    ".git",
    _META,
    "node_modules",
    "deps",
    "_build",
    "target",
    "vendor",
    ".next",
    "dist",
    "build",
    "mutants",
    "tmp",
}
_MAX_DEPTH = 2
_MINUTE = 60
_HOUR = 3600
_DAY = 86400
_TWO_DAYS = 172800
_STEP = re.compile(r"^== (\S+) \((\S+)\) attempt (\d+)")
_FINISH = re.compile(r"^\s+(\S+) finished in ([0-9.]+) min:")
_VERDICT = re.compile(r"^\s+verdict (\S+)")
_RATE = re.compile(r"^\s*rate limited; waiting .*retrying (\S+)")
_STOPPED = re.compile(r"^pipeline stopped at (\S+)")
_COMPLETE = re.compile(r"^pipeline complete\b")
_HUMAN = re.compile(r"stopping for a human")
_HANDOFF = re.compile(r"^(\d+)-([a-z][a-z-]*)\.md$")
_STEP_FILE = re.compile(r"^(\d+-[a-z][a-z-]*)(\.prompt\.md|\.json)$")
_ROLE_FILE = re.compile(r"^\d+-(specifier|critic|coder|cleaner|architect|practices|perf|hardener|qa|proposal)\.md$")
_LAST = re.compile(r"([A-Z][a-z]{2} [A-Z][a-z]{2} +\d+ \d\d:\d\d:\d\d \d{4})")
_REASON = re.compile(r"^abandoned \d{4}-\d{2}-\d{2} \d{2}:\d{2}: (.*)$")
_REVIVE = f"Move the handoffs-abandoned-* folder back to {_META}/handoffs/<task>/ and delete this file to revive it.\n"
_KEYS = (
    "repo",
    "task",
    "state",
    "step",
    "role",
    "since",
    "branch",
    "head",
    "pid",
    "result",
    "exit_code",
    "stopped_at",
    "reason",
    "events",
    "log",
)
_Proc = tuple[int, list[str], Path]


@dataclass
class _Log:
    step_label: str = ""
    step_done: bool = False
    verdict: str = ""
    rate_wait: str = ""
    terminal: str = ""
    stopped_at: str = ""
    human: bool = False
    last_line: str = ""
    steps: int = 0


@dataclass
class _Legacy:
    log: _Log | None
    fresh: list[Path]
    archives: list[Path]
    leftover: bool
    handoff_written: bool
    steps: dict[str, dict[str, int | None]]
    handoff_dir: Path
    marker: Path
    log_path: Path
    run_dir: Path
    activity: float
    last_step: str
    current_step: str


@dataclass
class _Row:
    repo: str
    task: str
    state: str = "never-started"
    step: str | None = None
    role: str | None = None
    since: str | None = None
    branch: str | None = None
    head: str | None = None
    pid: int | None = None
    result: str | None = None
    exit_code: int | None = None
    stopped_at: str | None = None
    reason: str | None = None
    events: str | None = None
    log: str | None = None
    detail: str = ""
    activity: float = 0.0
    current_step: str = ""
    last_step: str = ""
    era: str = "legacy"


def _meta(root: Path, *parts: str) -> Path:
    return root.joinpath(_META, *parts)


def _is_repo(path: Path) -> bool:
    return _meta(path).is_dir()


def read_run(repo: Path, task: str, procs: list[_Proc] | None = None) -> dict[str, Any]:
    rows = _repo_rows(repo, _processes(procs))
    _apply_shutdowns(rows)
    return _record(_row_for(rows, task, repo))


def scan(paths: list[Path]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    rows, waiting = _gather(_roots(paths))
    return [_record(row) for row in rows], waiting


def show(paths: list[Path], as_json: bool, show_all: bool, queued: bool) -> int:
    missing = _missing(paths)
    if missing is not None:
        sys.stderr.write(f"marestail status: not a directory: {missing}\n")
        return 2
    roots = _roots(paths)
    if as_json:
        _emit_json(roots, queued)
        return 0
    _emit_table(roots, show_all, queued)
    return 0


def abandon(targets: list[str], reason: str) -> int:
    procs = _machine_processes()
    stamp = time.strftime("%Y%m%dT%H%M%S")
    failed = 0
    for target in targets:
        if not _abandon_target(target, reason, stamp, procs):
            failed += 1
    return 1 if failed else 0


def _processes(procs: list[_Proc] | None) -> list[_Proc]:
    if procs is None:
        return _machine_processes()
    return procs


def _emit_json(roots: list[Path], queued: bool) -> None:
    records, waiting = scan(roots)
    _print_json(records, waiting, queued)


def _emit_table(roots: list[Path], show_all: bool, queued: bool) -> None:
    rows, waiting = _gather(roots)
    _print_table(rows, waiting, roots, show_all, queued)


def _gather(paths: list[Path]) -> tuple[list[_Row], list[dict[str, str]]]:
    procs = _machine_processes()
    repos = _discover(paths)
    rows = [row for repo in repos for row in _repo_rows(repo, procs)]
    _apply_shutdowns(rows)
    return rows, _queued(repos)


def _missing(paths: list[Path]) -> str | None:
    for path in paths:
        if not path.is_dir():
            return str(path)
    return None


def _roots(paths: list[Path]) -> list[Path]:
    if paths:
        return paths
    return [_default_root()]


def _default_root() -> Path:
    found = _repo_above(Path.cwd().resolve())
    if found is not None:
        return found
    return Path.cwd()


def _repo_above(start: Path) -> Path | None:
    for candidate in [start, *start.parents]:
        if _is_repo(candidate):
            return candidate
    return None


def _discover(roots: list[Path]) -> list[Path]:
    found: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        _walk(root, 0, found, seen)
    return found


def _walk(path: Path, depth: int, found: list[Path], seen: set[Path]) -> None:
    real = _safe_resolve(path)
    if real is None or real in seen:
        return
    seen.add(real)
    _visit(path, depth, found, seen)


def _visit(path: Path, depth: int, found: list[Path], seen: set[Path]) -> None:
    if _repo_or_deep(path, depth, found):
        return
    for child in _children(path):
        _walk(child, depth + 1, found, seen)


def _repo_or_deep(path: Path, depth: int, found: list[Path]) -> bool:
    if _is_repo(path):
        found.append(path)
        return True
    return depth >= _MAX_DEPTH


def _safe_resolve(path: Path) -> Path | None:
    try:
        return path.resolve()
    except OSError:
        return None


def _children(path: Path) -> list[Path]:
    return sorted(child for child in _entries(path) if _walkable(child))


def _walkable(child: Path) -> bool:
    if child.is_symlink() or not child.is_dir():
        return False
    return _kept_name(child.name)


def _kept_name(name: str) -> bool:
    return name not in _SKIP and not name.startswith(".")


def _entries(parent: Path) -> list[Path]:
    try:
        return list(parent.iterdir())
    except OSError:
        return []


def _machine_processes() -> list[_Proc]:
    rows: list[_Proc] = []
    for pid in _proc_pids():
        row = _proc_row(pid)
        if row is not None:
            rows.append(row)
    return rows


def _proc_pids() -> list[int]:
    try:
        names = os.listdir("/proc")
    except OSError:
        return []
    return [int(name) for name in names if name.isdigit()]


def _proc_row(pid: int) -> _Proc | None:
    try:
        tokens = _cmdline(pid)
        cwd = Path(os.readlink(f"/proc/{pid}/cwd")).resolve()
    except OSError:
        return None
    return pid, tokens, cwd


def _cmdline(pid: int) -> list[str]:
    raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    return [token for token in raw.decode(errors="replace").split("\0") if token]


def _repo_rows(repo: Path, procs: list[_Proc]) -> list[_Row]:
    tracked = _tracked(repo)
    branch = _branch(repo)
    rows = [_one_row(repo, task, procs, branch, tracked) for task in _task_names(repo)]
    _promote(rows, _unattributed_pids(procs, repo, rows))
    return rows


def _one_row(repo: Path, task: str, procs: list[_Proc], branch: str | None, tracked: set[str]) -> _Row:
    found = _events_row(repo, task, procs)
    if found is not None:
        return found
    return _legacy_row(repo, task, branch, tracked)


def _task_names(repo: Path) -> list[str]:
    names: set[str] = set()
    for parent in (_meta(repo, "handoffs"), _meta(repo, "runs")):
        names.update(_dir_names(parent))
    return sorted(names)


def _dir_names(parent: Path) -> set[str]:
    return {path.name for path in _entries(parent) if _task_dir(path)}


def _task_dir(path: Path) -> bool:
    return path.is_dir() and not path.name.startswith("handoffs-")


def _row_for(rows: list[_Row], task: str, repo: Path) -> _Row:
    for row in rows:
        if row.task == task:
            return row
    return _missing_row(repo, task)


def _missing_row(repo: Path, task: str) -> _Row:
    return _Row(repo=str(repo.resolve()), task=task)


def _events_row(repo: Path, task: str, procs: list[_Proc]) -> _Row | None:
    path = _meta(repo, "runs", task, _EVENTS)
    if not path.is_file():
        return None
    run = _event_lines(path)
    if run is None:
        return None
    return _row_from_events(repo, task, run, procs, path)


def _event_lines(path: Path) -> list[dict[str, Any]] | None:
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return None
    return _latest_run(_known_events(text))


def _known_events(text: str) -> list[dict[str, Any]]:
    found = []
    for line in text.splitlines():
        event = _kept_event(_valid_event(line))
        if event is not None:
            found.append(event)
    return found


def _valid_event(raw: str) -> dict[str, Any] | None:
    data = _json_object(raw)
    if _version_one(data):
        return data
    return None


def _json_object(raw: str) -> dict[str, Any] | None:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict):
        return data
    return None


def _version_one(data: dict[str, Any] | None) -> bool:
    return data is not None and data.get("v") == 1 and isinstance(data.get("event"), str)


def _kept_event(event: dict[str, Any] | None) -> dict[str, Any] | None:
    if event is None or event["event"] not in _KEPT:
        return None
    return event


def _latest_run(lines: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    starts = [index for index, event in enumerate(lines) if event["event"] == "run-start"]
    if not starts:
        return None
    return lines[starts[-1] :]


def _row_from_events(repo: Path, task: str, run: list[dict[str, Any]], procs: list[_Proc], path: Path) -> _Row:
    start = _last(run, "run-start") or {}
    end = _last(run, "run-end")
    state = _events_state(run, end, repo, task, procs)
    row = _events_record(repo, task, run, start, end, state, path)
    _maybe_abandon(row, _meta(repo, "runs", task, "abandoned"), _text(start.get("at")))
    return row


def _events_state(run: list[dict[str, Any]], end: dict[str, Any] | None, repo: Path, task: str, procs: list[_Proc]) -> str:
    if end is not None:
        return _ended(end)
    if _this_run(_last(run, "run-start") or {}, repo, task, procs):
        return "running"
    return "dead"


def _ended(end: dict[str, Any]) -> str:
    result = end.get("result")
    if result == "complete":
        return "complete"
    if result == "interrupted":
        return "dead"
    return "stopped"


def _this_run(start: dict[str, Any], repo: Path, task: str, procs: list[_Proc]) -> bool:
    pid = start.get("pid")
    if not isinstance(pid, int):
        return False
    proc = _find_proc(procs, pid)
    if proc is None:
        return False
    return _proc_is_task(proc, repo, task)


def _find_proc(procs: list[_Proc], pid: int) -> _Proc | None:
    for proc in procs:
        if proc[0] == pid:
            return proc
    return None


def _proc_is_task(proc: _Proc, repo: Path, task: str) -> bool:
    _pid, tokens, cwd = proc
    index = _run_index(tokens)
    if index is None:
        return False
    return _names_task(tokens, index, task) and _inside(cwd, repo)


def _run_index(tokens: list[str]) -> int | None:
    for index, token in enumerate(tokens[:-1]):
        if _launches(token, tokens[index + 1]):
            return index + 1
    return None


def _launches(token: str, nxt: str) -> bool:
    return Path(token).name in {"cli.py", "marestail"} and nxt == "run"


def _names_task(tokens: list[str], index: int, task: str) -> bool:
    return any(Path(token).stem == task for token in tokens[index + 1 :])


def _inside(cwd: Path, repo: Path) -> bool:
    try:
        here = cwd.resolve()
        root = repo.resolve()
    except OSError:
        return False
    if here == root:
        return True
    return root in here.parents


def _is_pipeline(tokens: list[str]) -> bool:
    return _run_index(tokens) is not None


def _events_record(
    repo: Path,
    task: str,
    run: list[dict[str, Any]],
    start: dict[str, Any],
    end: dict[str, Any] | None,
    state: str,
    path: Path,
) -> _Row:
    step_event = _latest_step(run)
    since = _text(run[-1].get("at"))
    return _Row(
        repo=str(repo.resolve()),
        task=task,
        state=state,
        step=_field(step_event, "step"),
        role=_field(step_event, "role"),
        since=since,
        branch=_text(start.get("branch")),
        head=_events_head(run),
        pid=_int_or_none(start.get("pid")),
        result=_field(end, "result"),
        exit_code=_exit_of(end),
        stopped_at=_field(end, "stopped_at"),
        reason=_events_reason(state, end),
        events=str(path.resolve()),
        log=_existing(_meta(repo, "runs", task, _LOG)),
        detail=_events_detail(run, state),
        activity=_activity_of(since),
        era="events",
    )


def _events_reason(state: str, end: dict[str, Any] | None) -> str | None:
    if state == "dead" or end is None:
        return None
    return _text(end.get("reason"))


def _events_detail(run: list[dict[str, Any]], state: str) -> str:
    if state == "complete":
        return f"{_count(run, 'step-end')} steps"
    if state == "stopped":
        return _stopped_text(run)
    if state == "running":
        return _running_text(run)
    return _dead_text(run)


def _running_text(run: list[dict[str, Any]]) -> str:
    if run[-1]["event"] == "wait":
        return f"waiting for a rate limit until {run[-1].get('until')}"
    name = _text((_last(run, "step-start") or {}).get("step"))
    if name:
        return f"in {name}"
    return "starting"


def _stopped_text(run: list[dict[str, Any]]) -> str:
    end = _last(run, "run-end") or {}
    reason = _text(end.get("reason"))
    if reason:
        return f"{_stop_prefix(end)}: {reason}"
    return _stop_prefix(end)


def _stop_prefix(end: dict[str, Any]) -> str:
    label = "failed at" if end.get("result") == "failed" else "at"
    where = _text(end.get("stopped_at"))
    if where:
        return f"{label} {where}"
    return label


def _dead_text(run: list[dict[str, Any]]) -> str:
    if _was_interrupted(run):
        return _interrupted_text(run)
    opened = _unfinished_step(run)
    if opened:
        return f"mid {opened}"
    ended = _last(run, "step-end")
    if ended is not None:
        return f"after {ended.get('step')} ({ended.get('outcome')})"
    return "before the first step"


def _was_interrupted(run: list[dict[str, Any]]) -> bool:
    end = _last(run, "run-end")
    return end is not None and end.get("result") == "interrupted"


def _interrupted_text(run: list[dict[str, Any]]) -> str:
    name = _field(_latest_step(run), "step")
    if name:
        return f"interrupted in {name}"
    return "interrupted"


def _unfinished_step(run: list[dict[str, Any]]) -> str | None:
    start = _index(run, "step-start")
    if start is None or _comes_after(_index(run, "step-end"), start):
        return None
    return _text(run[start].get("step"))


def _comes_after(end: int | None, start: int) -> bool:
    return end is not None and end > start


def _events_head(run: list[dict[str, Any]]) -> str | None:
    ended = _field(_last(run, "run-end"), "head")
    if ended:
        return ended
    stepped = _field(_last(run, "step-end"), "head")
    if stepped:
        return stepped
    return _field(_last(run, "run-start"), "start_commit")


def _field(event: dict[str, Any] | None, key: str) -> str | None:
    if event is None:
        return None
    return _text(event.get(key))


def _latest_step(run: list[dict[str, Any]]) -> dict[str, Any] | None:
    found = [event for event in run if event["event"] in {"step-start", "step-end"}]
    if not found:
        return None
    return found[-1]


def _last(run: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    index = _index(run, name)
    if index is None:
        return None
    return run[index]


def _index(run: list[dict[str, Any]], name: str) -> int | None:
    found = [index for index, event in enumerate(run) if event["event"] == name]
    if not found:
        return None
    return found[-1]


def _count(run: list[dict[str, Any]], name: str) -> int:
    return sum(1 for event in run if event["event"] == name)


def _exit_of(end: dict[str, Any] | None) -> int | None:
    if end is None:
        return None
    return _int_or_none(end.get("exit_code"))


def _int_or_none(value: object) -> int | None:
    if isinstance(value, int):
        return value
    return None


def _text(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _activity_of(since: str | None) -> float:
    if since is None:
        return 0.0
    parsed = _parse_time(since)
    if parsed is None:
        return 0.0
    return parsed


def _parse_time(text: str) -> float | None:
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _maybe_abandon(row: _Row, marker: Path, started: str | None) -> None:
    if row.state in {"dead", "stopped"} and _marker_applies(marker, started):
        _overlay_marker(row, marker)


def _marker_applies(marker: Path, started: str | None) -> bool:
    if not marker.is_file():
        return False
    if started is None:
        return True
    began = _parse_time(started)
    if began is None:
        return False
    return _mtime(marker) >= began


def _overlay_marker(row: _Row, marker: Path) -> None:
    text = _read_text(marker)
    row.state = "abandoned"
    row.detail = _first_line(text)
    row.reason = _reason_line(text)
    row.activity = _mtime(marker)
    row.since = _since_from(row.activity)


def _read_text(path: Path) -> str:
    try:
        return path.read_text()
    except OSError:
        return ""


def _first_line(text: str) -> str:
    lines = text.strip().splitlines()
    if not lines:
        return "abandoned"
    return lines[0]


def _reason_line(text: str) -> str | None:
    if not text:
        return None
    match = _REASON.match(text.splitlines()[0])
    if match is None:
        return None
    return match.group(1) or None


def _legacy_row(repo: Path, task: str, branch: str | None, tracked: set[str]) -> _Row:
    ctx = _legacy_context(repo, task, tracked)
    state, detail = _with_abandon(ctx, *_classify(ctx))
    return _row_from_legacy(repo, task, branch, ctx, state, detail)


def _legacy_context(repo: Path, task: str, tracked: set[str]) -> _Legacy:
    folder = _meta(repo, "handoffs", task)
    run_dir = _meta(repo, "runs", task)
    log_path = run_dir / _LOG
    log = _parse_log(log_path)
    live = _handoff_files(folder)
    archives = _archives(run_dir)
    leftover = _is_leftover(repo, live, archives, tracked)
    fresh = _fresh(leftover, live)
    return _Legacy(
        log=log,
        fresh=fresh,
        archives=archives,
        leftover=leftover,
        handoff_written=_step_handoff_exists(log, folder),
        steps=_folder_steps(run_dir),
        handoff_dir=folder,
        marker=run_dir / "abandoned",
        log_path=log_path,
        run_dir=run_dir,
        activity=_latest_mtime(fresh, log_path, archives, run_dir),
        last_step=_legacy_last_step(fresh, log),
        current_step=_current_step(log),
    )


def _fresh(leftover: bool, live: list[Path]) -> list[Path]:
    if leftover:
        return []
    return live


def _step_handoff_exists(log: _Log | None, folder: Path) -> bool:
    if log is None or not log.step_label:
        return False
    return (folder / f"{log.step_label}.md").is_file()


def _legacy_last_step(fresh: list[Path], log: _Log | None) -> str:
    named = _newest_step(fresh)
    if named:
        return named
    if log is None:
        return ""
    return log.step_label


def _current_step(log: _Log | None) -> str:
    if log is None or not log.step_label or log.step_done:
        return ""
    return log.step_label


def _latest_mtime(fresh: list[Path], log_path: Path, archives: list[Path], run_dir: Path) -> float:
    return max([*_mtimes(fresh), _mtime(log_path), *_mtimes(archives), _mtime(run_dir), 0.0])


def _mtimes(paths: list[Path]) -> list[float]:
    return [_mtime(path) for path in paths]


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _with_abandon(ctx: _Legacy, state: str, detail: str) -> tuple[str, str]:
    if not _given_up(ctx.marker, ctx.archives, ctx.fresh, state):
        return state, detail
    return "abandoned", _abandon_detail(ctx.marker)


def _given_up(marker: Path, archives: list[Path], fresh: list[Path], state: str) -> bool:
    if fresh or state == "complete":
        return False
    return marker.is_file() or _aborted(archives)


def _aborted(archives: list[Path]) -> bool:
    return any(path.name.startswith(("handoffs-abandoned-", "handoffs-aborted-")) for path in archives)


def _abandon_detail(marker: Path) -> str:
    if not marker.is_file():
        return "aborted archive, nothing since"
    return _first_line(_read_text(marker))


def _row_from_legacy(repo: Path, task: str, branch: str | None, ctx: _Legacy, state: str, detail: str) -> _Row:
    since, activity = _legacy_clock(state, ctx.activity, ctx.marker)
    step = ctx.last_step or None
    return _Row(
        repo=str(repo.resolve()),
        task=task,
        state=state,
        step=step,
        role=_role_of(step),
        since=since,
        branch=branch,
        head=None,
        pid=None,
        result=_log_result(ctx.log),
        exit_code=None,
        stopped_at=None,
        reason=_legacy_reason(state, ctx.log, ctx.marker),
        events=None,
        log=_existing(ctx.log_path),
        detail=detail,
        activity=activity,
        current_step=ctx.current_step,
        last_step=ctx.last_step,
        era="legacy",
    )


def _legacy_clock(state: str, activity: float, marker: Path) -> tuple[str | None, float]:
    if state == "abandoned" and marker.is_file():
        when = _mtime(marker)
        return _since_from(when), when
    return _since_from(activity), activity


def _role_of(step: str | None) -> str | None:
    if step is None or "-" not in step:
        return None
    return step.split("-", 1)[1]


def _log_result(log: _Log | None) -> str | None:
    if log is None or not log.steps:
        return None
    if log.terminal in {"complete", "stopped"}:
        return log.terminal
    return None


def _legacy_reason(state: str, log: _Log | None, marker: Path) -> str | None:
    if state == "stopped":
        return _human_reason(log)
    if state == "abandoned":
        return _marker_reason(marker)
    return None


def _human_reason(log: _Log | None) -> str:
    if log is not None and log.human:
        return "worker repeated the same problems 3x"
    return "runner asked for a human"


def _marker_reason(marker: Path) -> str | None:
    if not marker.is_file():
        return None
    return _reason_line(_read_text(marker))


def _classify(ctx: _Legacy) -> tuple[str, str]:
    logged = _from_log(ctx)
    if logged is not None:
        return logged
    archived = _from_archive(ctx)
    if archived is not None:
        return archived
    return _from_files(ctx)


def _from_log(ctx: _Legacy) -> tuple[str, str] | None:
    log = ctx.log
    if log is None or not log.steps:
        return None
    ended = _terminal_log(log, ctx.archives)
    if ended is not None:
        return ended
    return _mid_log(log, ctx.handoff_written)


def _terminal_log(log: _Log, archives: list[Path]) -> tuple[str, str] | None:
    if log.terminal == "complete":
        return "complete", _complete_detail(log.steps, archives)
    if log.terminal == "stopped":
        return "stopped", _stopped_detail(log)
    return None


def _complete_detail(steps: int, archives: list[Path]) -> str:
    if archives:
        return f"{steps} steps, archived"
    return f"{steps} steps"


def _stopped_detail(log: _Log) -> str:
    return f"at {log.stopped_at}, {_human_reason(log)}"


def _mid_log(log: _Log, written: bool) -> tuple[str, str]:
    if not log.step_done:
        return "dead", _open_detail(log, written)
    return "dead", _after_note(log)


def _open_detail(log: _Log, written: bool) -> str:
    if log.rate_wait:
        return f"in the rate-limit wait before {log.rate_wait}"
    return _mid_note(log.step_label, written)


def _mid_note(step: str, written: bool) -> str:
    note = f"mid {step}"
    if written:
        return note + " (its handoff was written, the runner never logged it)"
    return note


def _after_note(log: _Log) -> str:
    if _is_sampling(log.last_line):
        return f"after {log.step_label}, in perf sampling"
    if log.verdict:
        return f"after {log.step_label} finished (verdict {log.verdict})"
    return f"after {log.step_label} finished"


def _is_sampling(tail: str) -> bool:
    return tail.startswith(("filling perf", "perf-author"))


def _from_archive(ctx: _Legacy) -> tuple[str, str] | None:
    if not ctx.archives or ctx.fresh:
        return None
    return "complete", _archive_detail(ctx.leftover)


def _archive_detail(leftover: bool) -> str:
    if leftover:
        return "archived, leftover copies in handoffs/"
    return "archived"


def _from_files(ctx: _Legacy) -> tuple[str, str]:
    labels = _labels(ctx)
    if not labels:
        return _empty_state(ctx.leftover)
    label = max(labels, key=_label_number)
    return _file_state(ctx.steps.get(label), label, ctx.handoff_dir)


def _labels(ctx: _Legacy) -> set[str]:
    named = {path.stem for path in ctx.fresh if _HANDOFF.match(path.name)}
    return set(ctx.steps) | named


def _empty_state(leftover: bool) -> tuple[str, str]:
    if leftover:
        return "leftover", "handoff copies from git or a checkout, no run folder"
    return "never-started", "no handoffs, no steps, no log"


def _file_state(entry: dict[str, int | None] | None, label: str, folder: Path) -> tuple[str, str]:
    if entry is None:
        return "dead", f"after {label} wrote its handoff{_NO_LOG}"
    if entry["result"] is None:
        return "dead", f"mid {label}: prompt written, no result{_NO_LOG}"
    if entry["result"] == 0:
        return "dead", f"after {label} returned nothing, 0-byte result{_NO_LOG}"
    return _after_files(label, folder)


def _after_files(label: str, folder: Path) -> tuple[str, str]:
    if (folder / f"{label}.md").is_file():
        return "dead", f"after {label} wrote its handoff, then nothing{_NO_LOG}"
    return "dead", f"after {label} finished, then nothing{_NO_LOG}"


def _label_number(label: str) -> int:
    match = re.match(r"^(\d+)-", label)
    if match is None:
        return -1
    return int(match.group(1))


def _parse_log(path: Path) -> _Log | None:
    if not path.is_file():
        return None
    return _read_log(path)


def _read_log(path: Path) -> _Log:
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return _Log()
    state = _Log()
    for raw in lines:
        state = _apply_line(state, raw.rstrip())
    return state


def _apply_line(state: _Log, line: str) -> _Log:
    if not line.strip():
        return state
    state.last_line = line.strip()
    stepped = _step_line(state, line)
    if stepped is not None:
        return stepped
    return _tail_line(state, line)


def _step_line(state: _Log, line: str) -> _Log | None:
    match = _STEP.match(line)
    if match is None:
        return None
    return _Log(
        step_label=match.group(2),
        last_line=line.strip(),
        steps=state.steps + 1,
    )


def _tail_line(state: _Log, line: str) -> _Log:
    if _mark_finish(state, line):
        return state
    if _mark_verdict(state, line):
        return state
    if _mark_rate(state, line):
        return state
    return _mark_terminal(state, line)


def _mark_finish(state: _Log, line: str) -> bool:
    match = _FINISH.match(line)
    if match is None or match.group(1) != state.step_label:
        return False
    state.step_done = True
    return True


def _mark_verdict(state: _Log, line: str) -> bool:
    match = _VERDICT.match(line)
    if match is None:
        return False
    state.verdict = match.group(1)
    return True


def _mark_rate(state: _Log, line: str) -> bool:
    match = _RATE.match(line)
    if match is None:
        return False
    state.rate_wait = match.group(1)
    return True


def _mark_terminal(state: _Log, line: str) -> _Log:
    if _COMPLETE.match(line):
        state.terminal = "complete"
        return state
    stopped = _STOPPED.match(line)
    if stopped is not None:
        state.terminal = "stopped"
        state.stopped_at = stopped.group(1)
        return state
    if _HUMAN.search(line):
        state.human = True
    return state


def _archives(run_dir: Path) -> list[Path]:
    if not run_dir.is_dir():
        return []
    return sorted(path for path in run_dir.glob("handoffs-*") if path.is_dir())


def _handoff_files(folder: Path) -> list[Path]:
    return sorted(path for path in _entries(folder) if path.is_file() and path.suffix == ".md")


def _newest_step(files: list[Path]) -> str:
    best = ""
    number = -1
    for path in files:
        found = _step_name_number(path)
        if found is not None and found[1] > number:
            best, number = found
    return best


def _step_name_number(path: Path) -> tuple[str, int] | None:
    match = _HANDOFF.match(path.name)
    if match is None:
        return None
    return path.stem, int(match.group(1))


def _is_leftover(root: Path, live: list[Path], archives: list[Path], tracked: set[str]) -> bool:
    if not live:
        return False
    if _tracked_live(root, live, tracked):
        return True
    return _copies_match(live, archives)


def _tracked_live(root: Path, live: list[Path], tracked: set[str]) -> bool:
    return all(_relative(root, path) in tracked for path in live)


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _copies_match(live: list[Path], archives: list[Path]) -> bool:
    if not archives:
        return False
    return all(_copy_matches(path, archives) for path in live)


def _copy_matches(path: Path, archives: list[Path]) -> bool:
    try:
        return _bytes_match(path, _archived_copies(path, archives))
    except OSError:
        return False


def _archived_copies(path: Path, archives: list[Path]) -> list[Path]:
    return [archive / path.name for archive in archives if (archive / path.name).is_file()]


def _bytes_match(path: Path, copies: list[Path]) -> bool:
    return any(copy.read_bytes() == path.read_bytes() for copy in copies)


def _folder_steps(folder: Path) -> dict[str, dict[str, int | None]]:
    steps: dict[str, dict[str, int | None]] = {}
    for path in _entries(folder):
        _note_step_file(steps, path)
    return steps


def _note_step_file(steps: dict[str, dict[str, int | None]], path: Path) -> None:
    match = _STEP_FILE.match(path.name)
    if match is None:
        return
    entry = steps.setdefault(match.group(1), {"prompt": 0, "result": None})
    _store_step(entry, match.group(2), path)


def _store_step(entry: dict[str, int | None], kind: str, path: Path) -> None:
    if kind == ".json":
        entry["result"] = path.stat().st_size
        return
    entry["prompt"] = 1


def _promote(rows: list[_Row], pids: list[int]) -> None:
    if not pids:
        return
    chosen = _newest_promotable(rows)
    if chosen is None:
        return
    _mark_running(chosen, pids[0])


def _newest_promotable(rows: list[_Row]) -> _Row | None:
    found = [row for row in rows if _promotable(row)]
    if not found:
        return None
    return max(found, key=lambda row: row.activity)


def _promotable(row: _Row) -> bool:
    return row.era == "legacy" and row.state in {"dead", "never-started"}


def _mark_running(row: _Row, pid: int) -> None:
    row.state = "running"
    row.pid = pid
    step = row.current_step or row.last_step
    row.detail = f"in {step}" if step else "starting"
    row.step = row.current_step or None
    row.role = _role_of(row.step)


def _unattributed_pids(procs: list[_Proc], repo: Path, rows: list[_Row]) -> list[int]:
    taken = _running_event_pids(rows)
    return sorted(pid for pid, tokens, cwd in procs if _is_unattributed(pid, tokens, cwd, repo, taken))


def _running_event_pids(rows: list[_Row]) -> set[int]:
    pids: set[int] = set()
    for row in rows:
        if isinstance(row.pid, int) and _event_running(row):
            pids.add(row.pid)
    return pids


def _event_running(row: _Row) -> bool:
    return row.era == "events" and row.state == "running" and isinstance(row.pid, int)


def _is_unattributed(pid: int, tokens: list[str], cwd: Path, repo: Path, taken: set[int]) -> bool:
    if pid in taken:
        return False
    return _is_pipeline(tokens) and _inside(cwd, repo)


def _apply_shutdowns(rows: list[_Row]) -> None:
    dead = _dead_rows(rows)
    if not dead:
        return
    _note_shutdowns(dead)


def _dead_rows(rows: list[_Row]) -> list[_Row]:
    return [row for row in rows if row.state == "dead"]


def _note_shutdowns(dead: list[_Row]) -> None:
    stamps = _shutdown_times()
    for row in dead:
        _add_shutdown(row, stamps)


def _add_shutdown(row: _Row, stamps: list[float]) -> None:
    down = _shutdown_after(row.activity, stamps)
    if down is None:
        return
    row.detail += f"; machine shut down {time.strftime('%H:%M', time.localtime(down))}"
    row.reason = f"machine shut down {_utc_stamp(down)}"


def _shutdown_after(activity: float, stamps: list[float]) -> float | None:
    for stamp in stamps:
        if activity <= stamp <= activity + _HOUR:
            return stamp
    return None


def _shutdown_times() -> list[float]:
    code, output = run(["last", "-x", "-F", "shutdown"], Path.cwd())
    if code != 0:
        return []
    return _stamps(output)


def _stamps(output: str) -> list[float]:
    found = [stamp for line in output.splitlines() if (stamp := _stamp(line))]
    return sorted(found)


def _stamp(line: str) -> float | None:
    match = _LAST.search(line)
    if match is None:
        return None
    try:
        return time.mktime(time.strptime(match.group(1), "%a %b %d %H:%M:%S %Y"))
    except ValueError:
        return None


def _utc_stamp(when: float) -> str:
    return datetime.fromtimestamp(when, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _since_from(activity: float) -> str | None:
    if activity:
        return _utc_stamp(activity)
    return None


def _existing(path: Path) -> str | None:
    if path.is_file():
        return str(path.resolve())
    return None


def _git(root: Path, *args: str) -> str:
    code, output = run(["git", "-C", str(root), *args], root)
    if code != 0:
        return ""
    return output.strip()


def _branch(root: Path) -> str | None:
    name = _git(root, "branch", "--show-current")
    if name:
        return name
    return None


def _identity(root: Path) -> str:
    url = _git(root, "config", "--get", "remote.origin.url")
    if url:
        return url
    common = _git(root, "rev-parse", "--git-common-dir")
    if common:
        return common
    return str(root.resolve())


def _tracked(root: Path) -> set[str]:
    out = _git(root, "ls-files", "-z", "--", f"{_META}/handoffs")
    return {path for path in out.split("\0") if path}


def _queued(repos: list[Path]) -> list[dict[str, str]]:
    ran: dict[str, set[str]] = {}
    chosen: dict[str, dict[str, dict[str, str]]] = {}
    for repo in repos:
        _queue_repo(repo, ran, chosen)
    return _queue_list(ran, chosen)


def _queue_repo(repo: Path, ran: dict[str, set[str]], chosen: dict[str, dict[str, dict[str, str]]]) -> None:
    identity = _identity(repo)
    ran.setdefault(identity, set()).update(_task_names(repo))
    bucket = chosen.setdefault(identity, {})
    for path in _task_files(repo):
        bucket.setdefault(path.stem, _queue_item(repo, path))


def _task_files(repo: Path) -> list[Path]:
    folder = repo / "tasks"
    if not folder.is_dir():
        return []
    return sorted(path for path in folder.glob("*.md") if _task_md(path))


def _task_md(path: Path) -> bool:
    if path.name.lower() == "readme.md":
        return False
    return _ROLE_FILE.match(path.name) is None


def _queue_item(repo: Path, path: Path) -> dict[str, str]:
    return {"repo": str(repo.resolve()), "task": path.stem, "path": str(path.resolve())}


def _queue_list(ran: dict[str, set[str]], chosen: dict[str, dict[str, dict[str, str]]]) -> list[dict[str, str]]:
    pending: list[dict[str, str]] = []
    for identity, tasks in chosen.items():
        done = ran.get(identity, set())
        pending.extend(item for stem, item in tasks.items() if stem not in done)
    return sorted(pending, key=lambda item: (item["repo"], item["task"]))


def _record(row: _Row) -> dict[str, Any]:
    return {key: getattr(row, key) for key in _KEYS}


def _print_json(records: list[dict[str, Any]], waiting: list[dict[str, str]], queued: bool) -> None:
    ordered = sorted(records, key=lambda record: (record["repo"], record["task"]))
    payload = {"v": 1, "runs": ordered, "queued": waiting if queued else []}
    print(json.dumps(payload, indent=2))


def _print_table(rows: list[_Row], waiting: list[dict[str, str]], roots: list[Path], show_all: bool, queued: bool) -> None:
    shown = _visible_rows(rows, show_all)
    print(_header(rows, shown))
    print()
    print(_body(shown, roots))
    _maybe_queued(waiting, roots, queued)


def _visible_rows(rows: list[_Row], show_all: bool) -> list[_Row]:
    picked = [row for row in rows if show_all or row.state not in _HIDDEN]
    picked.sort(key=_sort_key)
    return picked


def _sort_key(row: _Row) -> tuple[int, float]:
    return _STATE_ORDER.index(row.state), -row.activity


def _header(rows: list[_Row], shown: list[_Row]) -> str:
    running = sum(1 for row in rows if row.state == "running")
    text = f"marestail runs: {len(rows)} known, {running} running, {len(shown)} shown"
    hidden = len(rows) - len(shown)
    if hidden:
        return text + f", {hidden} complete/abandoned/leftover hidden (use --all)"
    return text


def _body(shown: list[_Row], roots: list[Path]) -> str:
    if not shown:
        return "(nothing to show)"
    header = ["Repo", "Task", "State", "Where it stands", "Last activity", "Branch"]
    return _table([_cells(row, roots, time.time()) for row in shown], header)


def _cells(row: _Row, roots: list[Path], now: float) -> list[str]:
    return [
        _display(Path(row.repo), roots),
        row.task,
        row.state,
        row.detail,
        _activity_text(row.activity, now),
        row.branch or "(detached)",
    ]


def _maybe_queued(waiting: list[dict[str, str]], roots: list[Path], queued: bool) -> None:
    if queued and waiting:
        _print_queued(waiting, roots)


def _print_queued(waiting: list[dict[str, str]], roots: list[Path]) -> None:
    print()
    print("Tasks in tasks/ that never ran:")
    print()
    print(_table(_queued_pairs(waiting, roots), ["Repo", "Task"]))


def _queued_pairs(waiting: list[dict[str, str]], roots: list[Path]) -> list[list[str]]:
    pairs = [[_display(Path(item["repo"]), roots), item["task"]] for item in waiting]
    pairs.sort()
    return pairs


def _display(repo: Path, roots: list[Path]) -> str:
    for root in roots:
        rel = _relative_to(repo, root)
        if rel is not None:
            return rel
    return repo.name


def _relative_to(repo: Path, root: Path) -> str | None:
    try:
        base = root.resolve()
        real = repo.resolve()
    except OSError:
        return None
    if not real.is_relative_to(base):
        return None
    rel = real.relative_to(base).as_posix()
    if rel == ".":
        return real.name
    return rel


def _activity_text(activity: float, now: float) -> str:
    if not activity:
        return "?"
    return time.strftime("%m-%d %H:%M", time.localtime(activity)) + f" ({_age(activity, now)})"


def _age(activity: float, now: float) -> str:
    seconds = int(now - activity)
    if seconds < _HOUR:
        return f"{seconds // _MINUTE}m ago"
    if seconds < _TWO_DAYS:
        return f"{seconds // _HOUR}h ago"
    return f"{seconds // _DAY}d ago"


def _table(rows: list[list[str]], header: list[str]) -> str:
    widths = _widths(rows, header)
    lines = [_line(header, widths), _rule(widths)]
    lines.extend(_line(row, widths) for row in rows)
    return "\n".join(lines)


def _widths(rows: list[list[str]], header: list[str]) -> list[int]:
    return [max(len(header[index]), *_column(rows, index)) for index in range(len(header))]


def _column(rows: list[list[str]], index: int) -> list[int]:
    return [len(row[index]) for row in rows]


def _line(cells: list[str], widths: list[int]) -> str:
    body = " | ".join(cell.ljust(widths[index]) for index, cell in enumerate(cells))
    return f"| {body} |"


def _rule(widths: list[int]) -> str:
    return "|" + "|".join("-" * (width + 2) for width in widths) + "|"


def _abandon_target(target: str, reason: str, stamp: str, procs: list[_Proc]) -> bool:
    repo, task, problem = _target_repo(target)
    if problem:
        _report_error(target, problem)
        return False
    blocked = _refuse(repo, task, procs)
    if blocked:
        _report_error(target, blocked)
        return False
    return _give_up(repo, task, target, reason, stamp)


def _target_repo(target: str) -> tuple[Path, str, str]:
    repo_text, task = _split_target(target)
    repo = _locate_repo(repo_text)
    if repo is None:
        return Path(), task, f"no {_META} folder at {_shown_repo(repo_text)}"
    return repo, task, ""


def _split_target(target: str) -> tuple[str, str]:
    repo_text, sep, task = target.rpartition("/")
    if sep:
        return repo_text, task
    return "", target


def _shown_repo(repo_text: str) -> str:
    if repo_text:
        return repo_text
    return str(Path.cwd())


def _locate_repo(repo_text: str) -> Path | None:
    if repo_text:
        return _repo_at(Path(repo_text))
    return _repo_above(Path.cwd().resolve())


def _repo_at(path: Path) -> Path | None:
    if _is_repo(path):
        return path
    return None


def _refuse(repo: Path, task: str, procs: list[_Proc]) -> str:
    if read_run(repo, task, procs)["state"] == "running" or _unattributed_pids(procs, repo, _repo_rows(repo, procs)):
        return _busy_text(repo)
    return ""


def _busy_text(repo: Path) -> str:
    return f"a marestail pipeline is running in {repo.resolve()}, not touching it"


def _report_error(target: str, problem: str) -> None:
    print(f"{target}: {problem}", file=sys.stderr)


def _give_up(repo: Path, task: str, target: str, reason: str, stamp: str) -> bool:
    run_dir = _meta(repo, "runs", task)
    live = _meta(repo, "handoffs", task)
    if not run_dir.is_dir() and not live.is_dir():
        _report_error(target, "no run or handoffs folder for that task")
        return False
    run_dir.mkdir(parents=True, exist_ok=True)
    moved = _relocate(live, run_dir, stamp, repo)
    _write_marker(run_dir, reason)
    print(f"{target}: abandoned{moved}")
    return True


def _relocate(live: Path, run_dir: Path, stamp: str, repo: Path) -> str:
    if not live.is_dir() or not _handoff_files(live):
        return ""
    dest = run_dir / f"handoffs-abandoned-{stamp}"
    live.rename(dest)
    return f", {len(_handoff_files(dest))} handoffs moved to {dest.relative_to(repo)}"


def _write_marker(run_dir: Path, reason: str) -> None:
    when = time.strftime("%Y-%m-%d %H:%M")
    suffix = f": {reason}" if reason else ""
    (run_dir / "abandoned").write_text(f"abandoned {when}{suffix}\n{_REVIVE}")
