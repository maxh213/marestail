import ast
import contextlib
import json
import os
import re
import subprocess
import time
from collections.abc import Callable
from functools import partial
from itertools import chain, starmap
from pathlib import Path
from typing import Any, TypeGuard

from .model import Fleet, Process, RepoState, Step, Worker

BACKENDS = frozenset({"claude", "grok", "agy", "cursor-agent", "kilo", "kimi"})
STEP_RE = re.compile(r"^== (\S+) \((\S+)\) attempt (\d+)")
FINISH_RE = re.compile(r"^\s+(\S+) finished in ([0-9.]+) min: (.*)$")
VERDICT_RE = re.compile(r"^\s+verdict (\S+)")
QUOTED_RE = re.compile(r"""('(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")\s*$""")
TAIL_BYTES = 65536
CONV_BYTES = 262144
TAIL_STALE_S = 900
TAIL_LIMIT = 3
TAIL_CHARS = 90
WORK = ".marestail"
RUNS = "runs"
HANDOFFS = "handoffs"
LIVE = "live"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
ERRORS_IGNORE = "ignore"
ERRORS_REPLACE = "replace"
WORK_CONFIG_ENV = "DANDELION_CLAUDE_WORK_CONFIG_DIR"
WORK_HOME_DEFAULT = "~/.claude-work"
CLAUDE_CONFIG_ENV = "CLAUDE_CONFIG_DIR"
CLAUDE_HOME = ".claude"
PROMPT_SUFFIX = ".prompt.md"
RESULT_SUFFIX = ".json"
HANDOFF_SUFFIX = ".md"
CONV_MAX_LINES = 200
NAMED_TOOLS = ("rspec", "rubocop", "mutmut", "mutant", "stryker", "pytest", "vitest", "jest", "tsc", "eslint")
FIELD_KEYS = ("file_path", "command", "pattern")
MODEL_FLAGS = frozenset({"--model", "-m"})

ProcRow = tuple[int, int, int, list[str]]


def skip(*_args: object, **_kwargs: object) -> Any:
    return None


def work_path(root: Path, *parts: str) -> Path:
    return root.joinpath(WORK, *parts)


def none_of(*_args: object, **_kwargs: object) -> Any:
    return None


def present[T](value: T | None) -> TypeGuard[T]:
    return value is not None


def is_str(value: str | None) -> TypeGuard[str]:
    return value is not None


def is_path(value: Path | None) -> TypeGuard[Path]:
    return value is not None


def surely(value: Any) -> Any:
    return value


def first_text(*parts: str | None) -> str:
    return next(filter(is_str, parts), "")


def empty_list(*_args: object) -> list[Any]:
    return []


def blank(*_args: object) -> str:
    return ""


def false_of(*_args: object) -> bool:
    return False


def ident(value: object) -> Any:
    return value


def entries(folder: Path) -> list[Path]:
    with contextlib.suppress(OSError):
        return list(folder.iterdir())
    return []


def has_marestail(child: Path) -> bool:
    return work_path(child).is_dir()


def self_repo(root: Path) -> list[Path]:
    return {True: [root]}.get(has_marestail(root), [])


def discover_root(root: Path) -> list[Path]:
    return [*self_repo(root), *filter(has_marestail, list_dirs(root))]


def discover(roots: list[Path]) -> list[Path]:
    return list(chain.from_iterable(map(discover_root, roots)))


def list_dirs(root: Path) -> list[Path]:
    return sorted(filter(Path.is_dir, entries(root)))


def collect_repo(root: Path) -> RepoState:
    state = repo_shell(root)
    attach_live(state, real_path(root), proc_rows())
    return state


def repo_steps(log_path: Path | None) -> list[Step]:
    chosen = (empty_list, parse_log)[log_path is not None]
    return chosen(surely(log_path))


def repo_shell(root: Path) -> RepoState:
    log_path = latest_log(root)
    return RepoState(
        name=root.name,
        root=root,
        branch=git_line(root, ["branch", "--show-current"]),
        head=git_line(root, ["log", "-1", "--format=%h%x20%s"]),
        task=task_name(root),
        log_path=log_path,
        steps=repo_steps(log_path),
    )


def attach_live(state: RepoState, real: Path, rows: list[ProcRow]) -> None:
    pipeline = pipeline_pids(rows, real)
    state.alive = bool(pipeline)
    bind_running(state, rows, real)
    chosen = (skip, attach_activity)[state.alive]
    chosen(state, rows, pipeline, real)


def is_pipeline_row(real: Path, row: ProcRow) -> bool:
    pid, _, _, tokens = row
    return False not in (is_pipeline(tokens), under_root(cwd_of(pid), real))


def row_pid(row: ProcRow) -> int:
    return row[0]


def pipeline_pids(rows: list[ProcRow], real: Path) -> list[int]:
    return list(map(row_pid, filter(partial(is_pipeline_row, real), rows)))


def set_worker(state: RepoState, running: Step, rows: list[ProcRow], real: Path) -> None:
    state.worker = build_worker(state, running, rows, real)


def bind_running(state: RepoState, rows: list[ProcRow], real: Path) -> None:
    running = running_step(state.steps)
    chosen = (skip, set_worker)[running is not None]
    chosen(state, surely(running), rows, real)


def last_if_running(steps: list[Step]) -> Step | None:
    return {True: steps[-1]}.get(steps[-1].status == STATUS_RUNNING)


def running_step(steps: list[Step]) -> Step | None:
    chosen = (none_of, last_if_running)[bool(steps)]
    return chosen(steps)


def copy_tails(state: RepoState) -> None:
    surely(state.worker).tail_lines = state.tail_lines


def set_runner(state: RepoState) -> None:
    state.runner_activity = latest_runner_line(state.log_path)


def maybe_runner(state: RepoState) -> None:
    chosen = (skip, set_runner)[state.gate_activity is None]
    chosen(state)


def bind_tails_or_runner(state: RepoState) -> None:
    chosen = (maybe_runner, copy_tails)[state.worker is not None]
    chosen(state)


def attach_activity(state: RepoState, rows: list[ProcRow], pipeline: list[int], real: Path) -> None:
    state.gate_activity = gate_activity(rows, pipeline)
    state.tail_lines = transcript_tail(real)
    bind_tails_or_runner(state)


def last_item(lines: list[str]) -> str:
    return lines[-1]


def last_or_none(lines: list[str]) -> str | None:
    chosen = (none_of, last_item)[bool(lines)]
    return chosen(lines)


def latest_runner_line(log_path: Path | None) -> str | None:
    return last_or_none(nonempty_lines(log_path))


def nonempty_lines(log_path: Path | None) -> list[str]:
    chosen = (empty_list, stripped_lines)[log_path is not None]
    return chosen(surely(log_path))


def read_ignore(log_path: Path) -> list[str]:
    with contextlib.suppress(OSError):
        return log_path.read_text(errors=ERRORS_IGNORE).splitlines()
    return []


def stripped_lines(log_path: Path) -> list[str]:
    return list(filter(None, map(str.strip, read_ignore(log_path))))


def collect_fleet(roots: list[Path]) -> Fleet:
    return Fleet(repos=list(map(collect_repo, discover(roots))), scanned_at=time.time())


def add_live(sections: list[tuple[str, str]], live: list[str]) -> None:
    sections.append((LIVE, "\n".join(live)))


def conversation_for(repo: RepoState) -> list[tuple[str, str]]:
    sections = worker_sections(repo.worker)
    live = transcript_conversation(real_path(repo.root))
    chosen = (skip, add_live)[bool(live)]
    chosen(sections, live)
    return sections


def worker_texts(worker: Worker) -> list[tuple[str, str]]:
    return named_texts(
        (
            ("prompt", read_text(worker.prompt_path)),
            ("handoff", read_text(worker.handoff_path)),
            ("result", result_text(worker.result_path)),
        )
    )


def worker_sections(worker: Worker | None) -> list[tuple[str, str]]:
    chosen = (empty_list, worker_texts)[worker is not None]
    return chosen(surely(worker))


def has_text(pair: tuple[str, str | None]) -> TypeGuard[tuple[str, str]]:
    return pair[1] is not None


def named_texts(pairs: tuple[tuple[str, str | None], ...]) -> list[tuple[str, str]]:
    return list(filter(has_text, pairs))


def newest_name(candidates: list[Path]) -> str:
    return max(candidates, key=dir_mtime).name


def task_name(root: Path) -> str | None:
    candidates = list_task_dirs(work_path(root, HANDOFFS)) + list_task_dirs(work_path(root, RUNS))
    chosen = (none_of, newest_name)[bool(candidates)]
    return chosen(candidates)


def list_task_dirs(parent: Path) -> list[Path]:
    return list(filter(Path.is_dir, entries(parent)))


def dir_mtime(path: Path) -> float:
    with contextlib.suppress(OSError):
        return path.stat().st_mtime
    return 0.0


def log_name(path: Path) -> str:
    return path.name


def newest_log(logs: list[Path]) -> Path:
    return sorted(logs, key=log_name)[-1]


def latest_log(root: Path) -> Path | None:
    logs = overnight_logs(work_path(root, RUNS))
    chosen = (none_of, newest_log)[bool(logs)]
    return chosen(logs)


def overnight_logs(runs: Path) -> list[Path]:
    return list(filter(is_overnight_log, entries(runs)))


def is_overnight_log(path: Path) -> bool:
    return False not in (path.is_file(), path.name.startswith("overnight-"), path.suffix == ".log")


def parse_log(path: Path) -> list[Step]:
    lines = read_lines(path)
    steps: list[Step] = []
    list(map(partial(apply_log_line, steps), lines))
    stamp_running(steps, lines)
    return steps


def read_lines(path: Path) -> list[str]:
    with contextlib.suppress(OSError):
        return path.read_text(errors=ERRORS_REPLACE).splitlines()
    return []


def try_finish(steps: list[Step], line: str) -> None:
    chosen = (accept_verdict, skip)[accept_finish(steps, line)]
    chosen(steps, line)


def apply_log_line(steps: list[Step], line: str) -> None:
    chosen = (try_finish, skip)[accept_start(steps, line)]
    chosen(steps, line)


def append_step(steps: list[Step], matched: re.Match[str]) -> None:
    steps.append(new_step(matched))


def stored_start(steps: list[Step], matched: re.Match[str] | None) -> bool:
    chosen = (skip, append_step)[matched is not None]
    chosen(steps, surely(matched))
    return matched is not None


def accept_start(steps: list[Step], line: str) -> bool:
    return stored_start(steps, STEP_RE.match(line))


def new_step(matched: re.Match[str]) -> Step:
    return Step(
        role=matched.group(1),
        label=matched.group(2),
        attempt=int(matched.group(3)),
        status=STATUS_RUNNING,
        summary="",
        verdict=None,
        minutes=None,
    )


def stored_finish(steps: list[Step], matched: re.Match[str] | None) -> bool:
    chosen = (skip, finish_step)[matched is not None]
    chosen(steps, surely(matched))
    return matched is not None


def accept_finish(steps: list[Step], line: str) -> bool:
    return stored_finish(steps, FINISH_RE.match(line))


def set_verdict(steps: list[Step], matched: re.Match[str]) -> None:
    steps[-1].verdict = matched.group(1)


def apply_verdict(steps: list[Step], matched: re.Match[str] | None) -> None:
    chosen = (skip, set_verdict)[False not in (matched is not None, bool(steps))]
    chosen(steps, surely(matched))


def accept_verdict(steps: list[Step], line: str) -> None:
    apply_verdict(steps, VERDICT_RE.match(line))


def is_running_last(steps: list[Step]) -> bool:
    return steps[-1].status == STATUS_RUNNING


def last_running(steps: list[Step]) -> bool:
    chosen = (false_of, is_running_last)[bool(steps)]
    return chosen(steps)


def mark_running(steps: list[Step], lines: list[str]) -> None:
    steps[-1].summary = collapse(last_text(lines))


def stamp_running(steps: list[Step], lines: list[str]) -> None:
    chosen = (skip, mark_running)[last_running(steps)]
    chosen(steps, lines)


def last_text(lines: list[str]) -> str:
    return next(filter(str.strip, reversed(lines)), "")


def chosen_step(steps: list[Step], matched: re.Match[str]) -> Step | None:
    return next(filter(present, (running_named(steps, matched.group(1)), running_step(steps))), None)


def complete_step(step: Step, matched: re.Match[str]) -> None:
    step.status = STATUS_DONE
    step.minutes = float(matched.group(2))
    step.summary = summary_of(matched.group(3))


def complete_if_found(step: Step | None, matched: re.Match[str]) -> None:
    chosen = (skip, complete_step)[step is not None]
    chosen(surely(step), matched)


def finish_step(steps: list[Step], matched: re.Match[str]) -> None:
    complete_if_found(chosen_step(steps, matched), matched)


def running_label(label: str, step: Step) -> bool:
    return False not in (step.label == label, step.status == STATUS_RUNNING)


def running_named(steps: list[Step], label: str) -> Step | None:
    return next(filter(partial(running_label, label), reversed(steps)), None)


def collapse_rest(rest: str, _matched: re.Match[str] | None) -> str:
    return collapse(rest)


def eval_quote(_rest: str, matched: re.Match[str]) -> str:
    with contextlib.suppress(ValueError, SyntaxError):
        return collapse(str(ast.literal_eval(matched.group(1))))
    return collapse(matched.group(1)[1:-1])


def quoted_or_plain(rest: str, matched: re.Match[str] | None) -> str:
    chosen = (collapse_rest, eval_quote)[matched is not None]
    return chosen(rest, surely(matched))


def summary_of(rest: str) -> str:
    return quoted_or_plain(rest, QUOTED_RE.search(rest))


def collapse(text: str) -> str:
    return " ".join(text.split())


def transcript_tail(root: Path) -> list[str]:
    return transcript_conversation(root, TAIL_LIMIT, TAIL_BYTES)


def empty_at(_path: Path | None, _max_lines: int, _window: int) -> list[str]:
    return []


def formatted_if(path: Path | None, max_lines: int, window: int) -> list[str]:
    chosen = (empty_at, formatted_tail)[path is not None]
    return chosen(surely(path), max_lines, window)


def transcript_conversation(root: Path, max_lines: int = CONV_MAX_LINES, window: int = CONV_BYTES) -> list[str]:
    return formatted_if(live_transcript(root), max_lines, window)


def formatted_tail(path: Path, max_lines: int, window: int) -> list[str]:
    return list(filter(None, map(format_entry, transcript_lines(path, window))))[-max_lines:]


def expand_var(value: str) -> Path:
    return Path(value).expanduser()


def expanded_env(name: str) -> Path | None:
    chosen = (none_of, expand_var)[bool(os.environ.get(name))]
    return chosen(os.environ.get(name, ""))


def work_home() -> Path:
    return Path(next(filter(None, (os.environ.get(WORK_CONFIG_ENV), WORK_HOME_DEFAULT)))).expanduser()


def claude_homes() -> list[Path]:
    homes: tuple[Path | None, ...] = (Path.home() / CLAUDE_HOME, work_home(), expanded_env(CLAUDE_CONFIG_ENV))
    return list(filter(is_path, homes))


def project_jsonl(root: Path, home: Path) -> list[Path]:
    return jsonl_logs(home / "projects" / str(root).replace("/", "-"))


def live_transcript(root: Path) -> Path | None:
    return fresh_log(list(chain.from_iterable(map(partial(project_jsonl, root), claude_homes()))))


def jsonl_logs(folder: Path) -> list[Path]:
    return list(filter(is_jsonl, entries(folder)))


def is_jsonl(path: Path) -> bool:
    return False not in (path.is_file(), path.suffix == ".jsonl")


def newest_fresh(logs: list[Path]) -> Path | None:
    newest = sorted(logs, key=dir_mtime)[-1]
    return (newest, None)[time.time() - dir_mtime(newest) > TAIL_STALE_S]


def fresh_log(logs: list[Path]) -> Path | None:
    chosen = (none_of, newest_fresh)[bool(logs)]
    return chosen(logs)


def load_tail(path: Path, window: int) -> tuple[int, bytes]:
    size = path.stat().st_size
    with path.open("rb") as handle:
        handle.seek(max(0, size - window))
        return size, handle.read()


def read_tail(path: Path, window: int) -> tuple[int, bytes | None]:
    with contextlib.suppress(OSError):
        return load_tail(path, window)
    return 0, None


def empty_lines(_size: int, _raw: bytes | None, _window: int) -> list[str]:
    return []


def split_tail(size: int, raw: bytes, window: int) -> list[str]:
    lines = raw.decode(errors=ERRORS_REPLACE).splitlines()
    return (lines, lines[1:])[size > window]


def decode_tail(pair: tuple[int, bytes | None], window: int) -> list[str]:
    size, raw = pair
    chosen = (empty_lines, split_tail)[raw is not None]
    return chosen(size, surely(raw), window)


def transcript_lines(path: Path, window: int = TAIL_BYTES) -> list[str]:
    return decode_tail(read_tail(path, window), window)


def assistant_dict(data: object) -> TypeGuard[dict[str, object]]:
    return False not in (isinstance(data, dict), getattr(data, "get", none_of)("type") == "assistant")


def first_from(data: Any) -> str | None:
    return first_block(message_content(data.get("message")))


def assistant_block(data: object) -> str | None:
    chosen = (none_of, first_from)[assistant_dict(data)]
    return chosen(data)


def format_entry(line: str) -> str | None:
    return assistant_block(parsed_json(line))


def parsed_json(line: str) -> object:
    with contextlib.suppress(json.JSONDecodeError):
        return json.loads(line)
    return None


def dict_content(message: Any) -> list[object]:
    return list_or_empty(message.get("content"))


def list_or_empty(content: object) -> list[object]:
    chosen = (empty_list, ident)[isinstance(content, list)]
    return chosen(content)


def is_dict(block: object) -> TypeGuard[dict[str, object]]:
    return isinstance(block, dict)


def message_content(message: object) -> list[object]:
    chosen = (empty_list, dict_content)[isinstance(message, dict)]
    return chosen(message)


def first_block(content: list[object]) -> str | None:
    return next(filter(None, rendered_blocks(content)), None)


def rendered_blocks(content: list[object]) -> list[str | None]:
    return list(map(render_block, filter(is_dict, content)))


def thinking_line(block: dict[str, object]) -> str | None:
    return prefix_text("💭 ", collapse(str(or_blank(block.get("thinking"))))[:TAIL_CHARS])


def prefix_text(prefix: str, text: str) -> str | None:
    return {True: None}.get(not text, prefix + text)


def or_blank(value: object) -> str:
    return str({True: "", False: value}[not bool(value)])


def nonempty(text: str) -> str | None:
    return {True: None}.get(not text, text)


def text_line(block: dict[str, object]) -> str | None:
    return nonempty(collapse(str(or_blank(block.get("text")))))


def tool_text(name: str, block: dict[str, object]) -> str:
    return f"⚒ {name} {tool_detail(block.get('input'))}".rstrip()


def tool_named(name: str, block: dict[str, object]) -> str | None:
    collapsed = collapse(str(name))
    chosen = (none_of, tool_text)[bool(collapsed)]
    return chosen(collapsed, block)


def tool_line(block: dict[str, object]) -> str | None:
    return tool_named(or_blank(block.get("name")), block)


def missing_block(_block: dict[str, object]) -> str | None:
    return None


def render_block(block: dict[str, object]) -> str | None:
    chosen = BLOCK_RENDER.get(str(block.get("type")), missing_block)
    return chosen(block)


BLOCK_RENDER: dict[str, Callable[[dict[str, object]], str | None]] = {
    "thinking": thinking_line,
    "text": text_line,
    "tool_use": tool_line,
}


def tool_detail(value: object) -> str:
    return next(filter(None, tool_fields(value)), "")


def has_field(value: dict[str, object], key: str) -> bool:
    return stripped_str(value.get(key))


def field_text(value: dict[str, object], key: str) -> str:
    return collapse(str(value[key]))[:TAIL_CHARS]


def dict_fields(value: Any) -> list[str]:
    return list(map(partial(field_text, value), filter(partial(has_field, value), FIELD_KEYS)))


def tool_fields(value: object) -> list[str]:
    chosen = (empty_list, dict_fields)[isinstance(value, dict)]
    return chosen(value)


def stripped_str(value: object) -> bool:
    return False not in (isinstance(value, str), bool(getattr(value, "strip", blank)()))


def worker_without_task(state: RepoState, step: Step, process: Process | None) -> Worker:
    return Worker(step=step, process=process, result_path=None, prompt_path=None, handoff_path=None)


def worker_with_task(state: RepoState, step: Step, process: Process | None) -> Worker:
    base = work_path(state.root)
    task = surely(state.task)
    return Worker(
        step=step,
        process=process,
        result_path=existing(base / RUNS / task / f"{step.label}{RESULT_SUFFIX}"),
        prompt_path=existing(base / RUNS / task / f"{step.label}{PROMPT_SUFFIX}"),
        handoff_path=existing(base / HANDOFFS / task / f"{step.label}{HANDOFF_SUFFIX}"),
    )


def build_worker(state: RepoState, step: Step, rows: list[ProcRow], real: Path) -> Worker:
    process = matching_agent(rows, real)
    chosen = (worker_without_task, worker_with_task)[state.task is not None]
    return chosen(state, step, process)


def agent_under(real: Path, process: Process) -> bool:
    return under_root(cwd_of(process.pid), real)


def elapsed_of(item: Process) -> int:
    return item.elapsed_s


def min_process(found: list[Process]) -> Process:
    return min(found, key=elapsed_of)


def min_elapsed(found: list[Process]) -> Process | None:
    chosen = (min_process, none_of)[not found]
    return chosen(found)


def matching_agent(rows: list[ProcRow], real: Path) -> Process | None:
    return min_elapsed(list(filter(partial(agent_under, real), agents_of(rows))))


def row_agent(pid: int, _ppid: int, elapsed: int, tokens: list[str]) -> Process | None:
    return agent_process(pid, elapsed, tokens)


def agents_of(rows: list[ProcRow]) -> list[Process]:
    return list(filter(None, starmap(row_agent, rows)))


def existing(path: Path) -> Path | None:
    return {True: path}.get(path.is_file())


def proc_rows() -> list[ProcRow]:
    return list(filter(None, map(parse_ps_line, ps_lines())))


def run_ps() -> str:
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        return subprocess.run(["ps", "-eo", "pid,ppid,etimes,args"], capture_output=True, text=True, timeout=10).stdout
    return ""


def ps_lines() -> list[str]:
    return run_ps().splitlines()[1:]


def ints_row(parts: list[str]) -> ProcRow | None:
    with contextlib.suppress(ValueError):
        return int(parts[0]), int(parts[1]), int(parts[2]), parts[3].split()
    return None


def parsed_parts(parts: list[str]) -> ProcRow | None:
    chosen = (none_of, ints_row)[len(parts) >= 4]
    return chosen(parts)


def parse_ps_line(line: str) -> ProcRow | None:
    return parsed_parts(line.split(None, 3))


def cli_run_at(tokens: list[str], index: int) -> bool:
    return False not in (os.path.basename(tokens[index]) == "cli.py", tokens[index + 1] == "run")


def is_pipeline(tokens: list[str]) -> bool:
    return any(map(partial(cli_run_at, tokens), range(max(0, len(tokens) - 1))))


def pid_entry(row: ProcRow) -> tuple[int, tuple[int, list[str]]]:
    pid, _, elapsed, tokens = row
    return pid, (elapsed, tokens)


def first_of(item: tuple[int, str]) -> int:
    return item[0]


def gate_text(found: list[tuple[int, str]]) -> str:
    elapsed, label = sorted(found, key=first_of)[-1]
    return f"{label} {fmt_seconds(elapsed)}"


def format_gate(found: list[tuple[int, str]]) -> str | None:
    chosen = (none_of, gate_text)[bool(found)]
    return chosen(found)


def gate_activity(rows: list[ProcRow], pipeline: list[int]) -> str | None:
    return format_gate(descendant_gates(child_map(rows), dict(map(pid_entry, rows)), pipeline))


def add_child(children: dict[int, list[int]], row: ProcRow) -> None:
    pid, ppid, _, _ = row
    children.setdefault(ppid, []).append(pid)


def child_map(rows: list[ProcRow]) -> dict[int, list[int]]:
    children: dict[int, list[int]] = {}
    list(map(partial(add_child, children), rows))
    return children


def kids(children: dict[int, list[int]], pid: int) -> list[int]:
    return children.get(pid, [])


def start_stack(children: dict[int, list[int]], pipeline: list[int]) -> list[int]:
    return list(chain.from_iterable(map(partial(kids, children), pipeline)))


def push_level(children: dict[int, list[int]], stack: list[int], found: list[int]) -> Any:
    found.extend(stack)
    extra = list(chain.from_iterable(map(partial(kids, children), stack)))
    chosen = (skip, push_level)[bool(extra)]
    chosen(children, extra, found)


def unwind(children: dict[int, list[int]], stack: list[int]) -> list[int]:
    found: list[int] = []
    push_level(children, stack, found)
    return found


def gate_hit_pid(by_pid: dict[int, tuple[int, list[str]]], pid: int) -> list[tuple[int, str]]:
    return gate_hit(by_pid.get(pid, (0, [])))


def descendant_gates(
    children: dict[int, list[int]], by_pid: dict[int, tuple[int, list[str]]], pipeline: list[int]
) -> list[tuple[int, str]]:
    return list(chain.from_iterable(map(partial(gate_hit_pid, by_pid), unwind(children, start_stack(children, pipeline)))))


def gate_hit(row: tuple[int, list[str]]) -> list[tuple[int, str]]:
    elapsed, tokens = row
    label = classify_gate(tokens)
    return ([], [(elapsed, surely(label))])[label is not None]


def classified(tokens: list[str], names: list[str]) -> str | None:
    chosen = (none_of, gate_label)[not skipped_gate(tokens, names)]
    return chosen(tokens, names)


def classify_gate(tokens: list[str]) -> str | None:
    return classified(tokens, list(map(os.path.basename, tokens)))


def skipped_gate(tokens: list[str], names: list[str]) -> bool:
    return True in (is_pipeline(tokens), bool(BACKENDS.intersection(names)))


def gate_label(tokens: list[str], names: list[str]) -> str | None:
    return next(filter(present, gate_candidates(tokens, names)), None)


def gate_candidates(tokens: list[str], names: list[str]) -> tuple[str | None, ...]:
    return (
        named_one(names, "muex"),
        mix_gate(tokens),
        named_command(tokens, "dotnet", "test", "dotnet test"),
        scanner_sonar(names),
        sonarqube_token(tokens),
        java_sonar(names, tokens),
        maven_gate(names, tokens),
        pmd_gate(names, tokens),
        bundle_inner(tokens),
        docker_inner(tokens),
        named_tool(names),
        eunit_gate(names, tokens),
    )


def named_one(names: list[str], tool: str) -> str | None:
    return {True: tool}.get(tool in names)


def mix_from(mix: str | None) -> str | None:
    chosen = (none_of, mix_kind)[mix is not None]
    return chosen(surely(mix))


def mix_gate(tokens: list[str]) -> str | None:
    return mix_from(after(tokens, "mix"))


def mix_kind(mix: str) -> str:
    return ("mix", "mix test")[mix == "test"]


def named_command(tokens: list[str], program: str, argument: str, label: str) -> str | None:
    return {True: label}.get(after(tokens, program) == argument)


def scanner_sonar(names: list[str]) -> str | None:
    return {True: "sonar"}.get("sonar-scanner" in names)


def has_sonarqube(token: str) -> bool:
    return "sonarqube" in token


def sonarqube_token(tokens: list[str]) -> str | None:
    return {True: "sonar"}.get(any(map(has_sonarqube, tokens)))


def has_sonar(token: str) -> bool:
    return "sonar" in token


def sonar_if_named(tokens: list[str], _names: list[str] | None = None) -> str | None:
    return {True: "sonar"}.get(any(map(has_sonar, tokens)))


def java_sonar(names: list[str], tokens: list[str]) -> str | None:
    chosen = (none_of, partial(sonar_if_named, tokens))["java" in names]
    return chosen(names)


def maven_cmd(names: list[str]) -> bool:
    return True in ("mvn" in names, "mvnw" in names)


def maven_gate(names: list[str], tokens: list[str]) -> str | None:
    return {True: None}.get(not maven_cmd(names), maven_kind(tokens))


def has_pitest(token: str) -> bool:
    return "pitest" in token


def maven_kind(tokens: list[str]) -> str:
    return ("mvn", "pitest")[any(map(has_pitest, tokens))]


def has_pmd(token: str) -> bool:
    return token.endswith("PmdCli")


def pmd_if_java(tokens: list[str], _names: list[str] | None = None) -> str | None:
    return {True: "pmd"}.get(any(map(has_pmd, tokens)))


def pmd_gate(names: list[str], tokens: list[str]) -> str | None:
    chosen = (none_of, partial(pmd_if_java, tokens))["java" in names]
    return chosen(names)


def named_tool(names: list[str]) -> str | None:
    return next(filter(partial(contained, names), NAMED_TOOLS), None)


def contained(names: list[str], tool: str) -> bool:
    return tool in names


def has_eunit(token: str) -> bool:
    return "eunit" in token


def erlc_eunit(names: list[str]) -> str | None:
    return {True: "eunit"}.get("erlc" in names)


def token_eunit(tokens: list[str]) -> str | None:
    return {True: "eunit"}.get(any(map(has_eunit, tokens)))


def eunit_gate(names: list[str], tokens: list[str]) -> str | None:
    return next(filter(present, (erlc_eunit(names), token_eunit(tokens))), None)


def basename_is(tokens: list[str], name: str, index: int) -> bool:
    return os.path.basename(tokens[index]) == name


def token_after(tokens: list[str], index: int) -> str:
    return tokens[index + 1]


def after(tokens: list[str], name: str) -> str | None:
    return next(map(partial(token_after, tokens), filter(partial(basename_is, tokens, name), range(max(0, len(tokens) - 1)))), None)


def is_bundle_exec(tokens: list[str], index: int) -> bool:
    return False not in (os.path.basename(tokens[index]) == "bundle", tokens[index + 1] == "exec")


def bundle_at(tokens: list[str], index: int) -> str:
    return os.path.basename(tokens[index + 2])


def bundle_inner(tokens: list[str]) -> str | None:
    return next(map(partial(bundle_at, tokens), filter(partial(is_bundle_exec, tokens), range(max(0, len(tokens) - 2)))), None)


def docker_compose_run(token: str, tokens: list[str], index: int) -> bool:
    return False not in (os.path.basename(token) == "docker", tokens[index + 1 : index + 3] == ["compose", "run"])


def docker_at(tokens: list[str], index: int) -> bool:
    return docker_compose_run(tokens[index], tokens, index)


def docker_inner(tokens: list[str]) -> str | None:
    return next(
        filter(present, map(partial(compose_run_target, tokens), filter(partial(docker_at, tokens), range(max(0, len(tokens) - 3))))),
        None,
    )


def not_flag(token: str) -> bool:
    return not token.startswith("-")


def second_basename(inner: list[str]) -> str:
    return os.path.basename(inner[1])


def inner_name(inner: list[str]) -> str | None:
    chosen = (none_of, second_basename)[len(inner) > 1]
    return chosen(inner)


def compose_run_target(tokens: list[str], index: int) -> str | None:
    return inner_name(list(filter(not_flag, tokens[index + 3 :])))


def secs_fmt(seconds: int) -> str | None:
    return {True: f"{seconds}s"}.get(seconds < 60)


def mins_fmt(seconds: int) -> str | None:
    return {True: f"{seconds // 60}m"}.get(seconds < 3600)


def hours_fmt(seconds: int) -> str:
    return f"{seconds // 3600}h{seconds % 3600 // 60:02d}m"


def fmt_seconds(seconds: int) -> str:
    return str(first_text(secs_fmt(seconds), mins_fmt(seconds), hours_fmt(seconds)))


def make_process(pid: int, elapsed: int, tokens: list[str], backend: str) -> Process:
    return Process(pid=pid, elapsed_s=elapsed, model=model_of(tokens), backend=backend)


def process_of(pid: int, elapsed: int, tokens: list[str], backend: str | None) -> Process | None:
    chosen = (none_of, make_process)[backend is not None]
    return chosen(pid, elapsed, tokens, surely(backend))


def agent_process(pid: int, elapsed: int, tokens: list[str]) -> Process | None:
    chosen = (backend_of, none_of)[is_pipeline(tokens)]
    return process_of(pid, elapsed, tokens, chosen(tokens))


def in_backends(name: str) -> bool:
    return name in BACKENDS


def backend_of(tokens: list[str]) -> str | None:
    return next(filter(in_backends, map(os.path.basename, tokens)), None)


def is_model_flag(tokens: list[str], index: int) -> bool:
    return tokens[index] in MODEL_FLAGS


def model_of(tokens: list[str]) -> str:
    return next(map(partial(token_after, tokens), filter(partial(is_model_flag, tokens), range(max(0, len(tokens) - 1)))), "")


def cwd_of(pid: int) -> Path | None:
    with contextlib.suppress(OSError):
        return Path(os.readlink(f"/proc/{pid}/cwd"))
    return None


def path_under(cwd: Path, root: Path) -> bool:
    return True in (cwd == root, root in cwd.parents)


def under_root(cwd: Path | None, root: Path) -> bool:
    chosen = (false_of, path_under)[cwd is not None]
    return chosen(surely(cwd), root)


def real_path(root: Path) -> Path:
    with contextlib.suppress(OSError):
        return root.resolve()
    return root


def run_git(root: Path, args: list[str]) -> subprocess.CompletedProcess[str] | None:
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=10)
    return None


def ok_git(out: object) -> bool:
    return False not in (out is not None, getattr(out, "returncode", 1) == 0)


def stripped_out(out: subprocess.CompletedProcess[str]) -> str:
    return out.stdout.strip()


def git_stdout(out: subprocess.CompletedProcess[str] | None) -> str:
    chosen = (blank, stripped_out)[ok_git(out)]
    return chosen(surely(out))


def git_line(root: Path, args: list[str]) -> str:
    return git_stdout(run_git(root, args))


def load_text(path: Path) -> str | None:
    with contextlib.suppress(OSError):
        return path.read_text(errors=ERRORS_REPLACE)
    return None


def read_text(path: Path | None) -> str | None:
    chosen = (none_of, load_text)[path is not None]
    return chosen(surely(path))


def decoded_if(raw: str | None) -> str | None:
    chosen = (none_of, decoded_result)[raw is not None]
    return chosen(surely(raw))


def result_text(path: Path | None) -> str | None:
    return decoded_if(read_text(path))


def dict_result(data: Any) -> object:
    return data.get("result")


def result_field(data: object) -> object:
    chosen = (none_of, dict_result)[isinstance(data, dict)]
    return chosen(data)


def str_or_raw(result: object, raw: str) -> str:
    return str((raw, result)[isinstance(result, str)])


def decoded_result(raw: str) -> str:
    return str_or_raw(result_field(parsed_json(raw)), raw)
