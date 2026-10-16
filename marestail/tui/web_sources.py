import contextlib
import json
import re
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from itertools import repeat, takewhile
from operator import itemgetter
from pathlib import Path
from typing import Any

from marestail.tui.collect import collect_fleet
from marestail.tui.model import RepoState

PROC_STAT = Path("/proc/stat")
PROC_MEMINFO = Path("/proc/meminfo")
DISK = "/"
CPU_FIELDS = 8
IDLE_FIELD = 3
CPU_EVERY = 2.0
PROBE_TIMEOUT = 300
RUNS_TIMEOUT = 60
ERROR_CHARS = 300
TASK_CHARS = 20000
HANDOFF_CHARS = 16000
LOG_CHARS = 6000
SAMPLES = "/tools/samples/"
STEP_MARK = "== "
UNKNOWN = "unknown"
TASK_NAME = re.compile(r"[\w.-]{1,120}")
Record = dict[str, Any]
Sample = list[tuple[str, int, int]]


@dataclass
class Dandelion:
    data: Record | None = None
    error: str | None = None
    at: float | None = None
    running: bool = False


@dataclass
class Dash:
    root: Path
    every: float
    runs_script: Path
    cache: Path
    dandelion: Dandelion = field(default_factory=Dandelion)
    cpu: Record | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


@dataclass
class Host:
    cpu: Record | None
    mem_used: int
    mem_total: int
    swap_used: int
    swap_total: int
    disk_used: int
    disk_total: int


class Caught:
    def __init__(self) -> None:
        self.error: BaseException | None = None

    def __enter__(self) -> "Caught":
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: object) -> bool:
        self.error = exc
        return isinstance(exc, Exception)


def skip(*_args: object) -> None:
    return None


def none_of(*_args: object) -> Any:
    return None


def blank(*_args: object) -> str:
    return ""


def empty_list(*_args: object) -> list[Any]:
    return []


def present(value: object) -> bool:
    return value is not None


def is_dict(value: object) -> bool:
    return isinstance(value, dict)


def dict_of(value: Any) -> Record:
    return ({}, value)[isinstance(value, dict)]


def list_of(value: Any) -> list[Any]:
    return ([], value)[isinstance(value, list)]


def first_text(*parts: Any) -> str:
    return str(next(filter(None, parts), ""))


def text_of(value: Any) -> str:
    return ("", str(value))[bool(value)]


def mtime(path: Path) -> float:
    return path.stat().st_mtime


def settle_ok(state: Dandelion, out: subprocess.CompletedProcess[str]) -> None:
    state.data = json.loads(out.stdout.strip().splitlines()[-1])
    state.error = None


def settle_failed(state: Dandelion, out: subprocess.CompletedProcess[str]) -> None:
    said = first_text(out.stderr, out.stdout).strip()[-ERROR_CHARS:]
    state.error = first_text(said, f"exit {out.returncode}")


def settle(state: Dandelion, out: subprocess.CompletedProcess[str]) -> None:
    chosen = (settle_failed, settle_ok)[all((out.returncode == 0, bool(out.stdout.strip())))]
    chosen(state, out)


def probe_once(dash: Dash) -> None:
    state = dash.dandelion
    state.running = True
    with Caught() as caught:
        settle(state, subprocess.run(["dandelion", "--json"], capture_output=True, text=True, timeout=PROBE_TIMEOUT, check=False))
    state.error = (state.error, str(caught.error)[:ERROR_CHARS])[caught.error is not None]
    state.at = time.time()
    state.running = False
    remember(dash)


def probe_and_wait(dash: Dash, sleep: Callable[[float], None]) -> bool:
    probe_once(dash)
    sleep(dash.every)
    return False


def probe_forever(dash: Dash, sleep: Callable[[float], None] = time.sleep) -> None:
    any(map(probe_and_wait, repeat(dash), repeat(sleep)))


def remember(dash: Dash) -> None:
    state = dash.dandelion
    with contextlib.suppress(OSError):
        dash.cache.parent.mkdir(parents=True, exist_ok=True)
        dash.cache.write_text(json.dumps({"data": state.data, "error": state.error, "at": state.at}))


def read_cache(path: Path) -> Any:
    with contextlib.suppress(OSError, ValueError):
        return json.loads(path.read_text())
    return None


def restore(dash: Dash, raw: Record) -> None:
    dash.dandelion.data = raw["data"]
    dash.dandelion.error = raw.get("error")
    dash.dandelion.at = raw.get("at")


def recall(dash: Dash) -> None:
    raw = read_cache(dash.cache)
    chosen = (skip, restore)[is_dict(dict_of(raw).get("data"))]
    chosen(dash, raw)


def is_cpu_line(line: str) -> bool:
    return line.startswith("cpu")


def core_row(line: str) -> tuple[str, int, int]:
    parts = line.split()
    numbers = list(map(int, parts[1 : 1 + CPU_FIELDS]))
    return parts[0], sum(numbers), numbers[IDLE_FIELD]


def cpu_sample(text: str) -> Sample:
    return list(map(core_row, takewhile(is_cpu_line, text.splitlines())))


def busy(total: int, idle: int) -> float:
    return (0.0, max(0.0, min(100.0, 100.0 * (total - idle) / max(total, 1))))[total > 0]


def names(sample: Sample) -> list[str]:
    return list(map(itemgetter(0), sample))


def core_pct(now: tuple[str, int, int], before: tuple[str, int, int]) -> tuple[str, float]:
    return now[0], busy(now[1] - before[1], now[2] - before[2])


def is_core(pair: tuple[str, float]) -> bool:
    return pair[0] != "cpu"


def core_entry(pair: tuple[str, float]) -> tuple[str, float]:
    return pair[0].removeprefix("cpu"), pair[1]


def usage_of(before: Sample, now: Sample) -> Record:
    pairs = list(map(core_pct, now, before))
    return {"all": dict(pairs).get("cpu", 0.0), "cores": list(map(core_entry, filter(is_core, pairs)))}


def cpu_usage(before: Sample, now: Sample) -> Record | None:
    chosen = (none_of, usage_of)[all((bool(before), names(before) == names(now)))]
    return chosen(before, now)


def read_sample(read: Callable[[], str], before: Sample) -> Sample:
    with contextlib.suppress(OSError, ValueError, IndexError):
        return cpu_sample(read())
    return before


def publish(dash: Dash, usage: Record) -> None:
    with dash.lock:
        dash.cpu = usage


def cpu_tick(dash: Dash, sleep: Callable[[float], None], read: Callable[[], str], before: list[Sample]) -> bool:
    now = read_sample(read, before[0])
    usage = cpu_usage(before[0], now)
    (skip, publish)[usage is not None](dash, usage)
    before[0] = now
    sleep(CPU_EVERY)
    return False


def cpu_forever(dash: Dash, sleep: Callable[[float], None] = time.sleep, read: Callable[[], str] = PROC_STAT.read_text) -> None:
    before: list[Sample] = [[]]
    any(map(cpu_tick, repeat(dash), repeat(sleep), repeat(read), repeat(before)))


def meminfo_entry(line: str) -> tuple[str, int]:
    key, rest = line.split(":", 1)
    return key, int(rest.split()[0]) * 1024


def meminfo(text: str) -> dict[str, int]:
    return dict(map(meminfo_entry, text.splitlines()))


def memory_used(info: dict[str, int]) -> int:
    cached = info.get("Cached", 0) + info.get("SReclaimable", 0) - info.get("Shmem", 0)
    used = info.get("MemTotal", 0) - info.get("MemFree", 0) - info.get("Buffers", 0) - max(0, cached)
    return max(0, used)


def read_host(dash: Dash) -> Host:
    info = meminfo(PROC_MEMINFO.read_text())
    disk = shutil.disk_usage(DISK)
    swap_total = info.get("SwapTotal", 0)
    with dash.lock:
        cpu = dash.cpu
    return Host(cpu, memory_used(info), info.get("MemTotal", 0), max(0, swap_total - info.get("SwapFree", 0)), swap_total, disk.used, disk.total)


def host_or_error(dash: Dash) -> Host | str:
    with Caught() as caught:
        return read_host(dash)
    return f"unavailable: {caught.error}"


def run_rows(dash: Dash) -> Any:
    command = [sys.executable, str(dash.runs_script), "--json", "--all", "--root", str(dash.root)]
    out = subprocess.run(command, capture_output=True, text=True, timeout=RUNS_TIMEOUT, check=False)
    return json.loads((out.stdout, "[]")[out.returncode != 0])


def fetch_runs(dash: Dash) -> list[Record]:
    with contextlib.suppress(OSError, subprocess.SubprocessError, ValueError):
        return list(filter(is_dict, list_of(run_rows(dash))))
    return []


def runs_table(dash: Dash) -> list[Record]:
    chosen = (empty_list, fetch_runs)[dash.runs_script.is_file()]
    return chosen(dash)


def not_sample(repo: RepoState) -> bool:
    return SAMPLES not in str(repo.root)


def bed_order(repo: RepoState) -> tuple[bool, str]:
    return not repo.alive, repo.name


def collect_beds(dash: Dash) -> list[RepoState]:
    return sorted(filter(not_sample, collect_fleet([dash.root]).repos), key=bed_order)


def beds(dash: Dash) -> list[RepoState] | str:
    with Caught() as caught:
        return collect_beds(dash)
    return f"collect failed: {caught.error}"


def head_cut(text: str, limit: int) -> str:
    return text[:limit] + "\n…"


def tail_cut(text: str, limit: int) -> str:
    return "…\n" + text[-limit:]


def whole(text: str, _limit: int) -> str:
    return text


def capped(text: str, limit: int, tail: bool = False) -> str:
    text = text.strip()
    chosen = ((head_cut, tail_cut)[tail], whole)[len(text) <= limit]
    return chosen(text, limit)


def read_capped(path: Path, limit: int) -> str:
    with contextlib.suppress(OSError):
        return capped(path.read_text(errors="replace"), limit)
    return ""


def inside(path: Path, parent: Path) -> bool:
    with contextlib.suppress(ValueError, OSError):
        path.resolve().relative_to(parent.resolve())
        return True
    return False


def md_files(folder: Path) -> list[Path]:
    return list(filter(Path.is_file, folder.glob("*.md")))


def newest_md(folder: Path) -> Path | None:
    files = (empty_list, md_files)[folder.is_dir()](folder)
    return max(files, key=mtime, default=None)


def archives(run_dir: Path) -> list[Path]:
    return list(filter(Path.is_dir, run_dir.glob("handoffs-*")))


def archived_handoff(root: Path, task: str) -> Path | None:
    run_dir = root / ".marestail" / "runs" / task
    folder = max((empty_list, archives)[run_dir.is_dir()](run_dir), key=mtime, default=None)
    chosen = (none_of, newest_md)[folder is not None]
    return chosen(folder)


def newest_handoff(root: Path, task: str) -> Path | None:
    live = newest_md(root / ".marestail" / "handoffs" / task)
    return next(filter(present, (live, archived_handoff(root, task))), None)


def read_lines(path: Path) -> list[str] | None:
    with contextlib.suppress(OSError):
        return path.read_text(errors="replace").splitlines()
    return None


def starts_step(pair: tuple[int, str]) -> bool:
    return pair[1].startswith(STEP_MARK)


def from_last_step(lines: list[str]) -> str:
    start = max(map(itemgetter(0), filter(starts_step, enumerate(lines))), default=0)
    return capped("\n".join(lines[start:]), LOG_CHARS, tail=True)


def log_excerpt(path: Path) -> str:
    lines = read_lines(path)
    chosen = (blank, from_last_step)[lines is not None]
    return chosen(lines)


def valid_request(repo: str, task: str) -> bool:
    return all((bool(repo), "/" not in repo, ".." not in repo, TASK_NAME.fullmatch(task) is not None))


def same_run(repo: str, task: str, row: Record) -> bool:
    return (row.get("repo"), row.get("task")) == (repo, task)


def found_run(dash: Dash, repo: str, task: str) -> Record | None:
    match = next(filter(partial(same_run, repo, task), runs_table(dash)), None)
    allowed = all((match is not None, inside(Path(str(dict_of(match).get("root"))), dash.root)))
    return (None, match)[allowed]


def matching_run(dash: Dash, repo: str, task: str) -> Record | None:
    chosen = (none_of, found_run)[valid_request(repo, task)]
    return chosen(dash, repo, task)


def task_text(root: Path, task: str) -> str:
    path = (root / "tasks" / f"{task}.md").resolve()
    chosen = (blank, partial(read_capped, limit=TASK_CHARS))[all((inside(path, root / "tasks"), path.is_file()))]
    return chosen(path)


def run_summary(match: Record, step: str) -> str:
    return " · ".join(filter(None, (first_text(match.get("state"), UNKNOWN), step, text_of(match.get("verdict")))))


def log_path(match: Record, root: Path, task: str) -> Path:
    named = match.get("log")
    return (root / ".marestail" / "runs" / task / "pipeline.log", Path(str(named)))[bool(named)]


def handoff_text(handoff: Path) -> str:
    return f"── {handoff.name}\n{read_capped(handoff, HANDOFF_CHARS)}"


def handoff_note(root: Path, task: str) -> str:
    handoff = newest_handoff(root, task)
    chosen = (blank, handoff_text)[all((handoff is not None, inside(Path(str(handoff)), root)))]
    return chosen(handoff)


def excerpt_of(log: Path, root: Path) -> str:
    chosen = (blank, log_excerpt)[inside(log, root)]
    return chosen(log)


def working_notes(match: Record, root: Path, task: str, step: str) -> str:
    bits = (run_summary(match, step), text_of(match.get("detail")), excerpt_of(log_path(match, root, task), root), handoff_note(root, task))
    return "\n\n".join(filter(None, bits)).strip()


def view_of(match: Record, repo: str, task: str) -> Record:
    root = Path(str(match["root"])).resolve()
    step = first_text(match.get("current_step"), match.get("last_step"))
    return {"repo": repo, "task": task, "state": text_of(match.get("state")), "step": step, "task_text": task_text(root, task), "working": working_notes(match, root, task, step)}


def task_view(dash: Dash, repo: str, task: str) -> Record | None:
    match = matching_run(dash, repo, task)
    chosen = (none_of, view_of)[match is not None]
    return chosen(match, repo, task)
