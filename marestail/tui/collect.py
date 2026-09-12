import ast
import json
import os
import re
import subprocess
import time
from pathlib import Path

from .model import Fleet, Process, RepoState, Step, Worker

BACKENDS = ("claude", "grok", "agy", "cursor-agent", "kilo")
STEP_RE = re.compile(r"^== (\S+) \((\S+)\) attempt (\d+)")
FINISH_RE = re.compile(r"^\s+(\S+) finished in ([0-9.]+) min: (.*)$")
VERDICT_RE = re.compile(r"^\s+verdict (\S+)")
QUOTED_RE = re.compile(r"""('(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")\s*$""")
TAIL_BYTES = 65536
CONV_BYTES = 262144
TAIL_STALE_S = 900
TAIL_LIMIT = 3
TAIL_CHARS = 90

ProcRow = tuple[int, int, list[str]]


def discover(roots: list[Path]) -> list[Path]:
    found: list[Path] = []
    for root in roots:
        try:
            children = sorted(p for p in root.iterdir() if p.is_dir())
        except OSError:
            children = []
        if (root / ".marestail").is_dir():
            found.append(root)
        found.extend(p for p in children if (p / ".marestail").is_dir())
    return found


def collect_repo(root: Path) -> RepoState:
    log_path = latest_log(root)
    state = RepoState(
        name=root.name,
        root=root,
        branch=git_line(root, ["branch", "--show-current"]),
        head=git_line(root, ["log", "-1", "--format=%h%x20%s"]),
        task=task_name(root),
        log_path=log_path,
        steps=parse_log(log_path) if log_path is not None else [],
    )
    real = real_path(root)
    rows = proc_rows()
    state.alive = any(
        is_pipeline(tokens) and under_root(cwd_of(pid), real) for pid, _, tokens in rows
    )
    running = state.steps[-1] if state.steps and state.steps[-1].status == "running" else None
    if running is not None:
        state.worker = build_worker(state, running, rows, real)
    if state.alive:
        state.tail_lines = transcript_tail(real)
        if state.worker is not None:
            state.worker.tail_lines = state.tail_lines
    return state


def collect_fleet(roots: list[Path]) -> Fleet:
    repos = [collect_repo(root) for root in discover(roots)]
    return Fleet(repos=repos, scanned_at=time.time())


def conversation_for(repo: RepoState) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    worker = repo.worker
    if worker is not None:
        prompt = read_text(worker.prompt_path)
        if prompt is not None:
            sections.append(("prompt", prompt))
        handoff = read_text(worker.handoff_path)
        if handoff is not None:
            sections.append(("handoff", handoff))
        result = result_text(worker.result_path)
        if result is not None:
            sections.append(("result", result))
    live = transcript_conversation(real_path(repo.root))
    if live:
        sections.append(("live", "\n".join(live)))
    return sections


def task_name(root: Path) -> str | None:
    candidates: list[Path] = []
    for parent in (root / ".marestail" / "handoffs", root / ".marestail" / "runs"):
        try:
            candidates.extend(p for p in parent.iterdir() if p.is_dir())
        except OSError:
            continue
    if not candidates:
        return None
    return max(candidates, key=dir_mtime).name


def dir_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def latest_log(root: Path) -> Path | None:
    runs = root / ".marestail" / "runs"
    try:
        logs = [
            p
            for p in runs.iterdir()
            if p.is_file() and p.name.startswith("overnight-") and p.suffix == ".log"
        ]
    except OSError:
        return None
    if not logs:
        return None
    return max(logs, key=lambda p: p.name)


def parse_log(path: Path) -> list[Step]:
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return []
    steps: list[Step] = []
    for line in lines:
        matched = STEP_RE.match(line)
        if matched:
            steps.append(
                Step(
                    role=matched.group(1),
                    label=matched.group(2),
                    attempt=int(matched.group(3)),
                    status="running",
                    summary="",
                    verdict=None,
                    minutes=None,
                )
            )
            continue
        matched = FINISH_RE.match(line)
        if matched:
            finish_step(steps, matched)
            continue
        matched = VERDICT_RE.match(line)
        if matched and steps:
            steps[-1].verdict = matched.group(1)
    if steps and steps[-1].status == "running":
        tail = next((line for line in reversed(lines) if line.strip()), "")
        steps[-1].summary = collapse(tail)
    return steps


def finish_step(steps: list[Step], matched: re.Match[str]) -> None:
    label = matched.group(1)
    step = next(
        (s for s in reversed(steps) if s.label == label and s.status == "running"),
        None,
    )
    if step is None:
        step = next((s for s in reversed(steps) if s.status == "running"), None)
    if step is None:
        return
    step.status = "done"
    step.minutes = float(matched.group(2))
    step.summary = summary_of(matched.group(3))


def summary_of(rest: str) -> str:
    matched = QUOTED_RE.search(rest)
    if matched is None:
        return collapse(rest)
    try:
        value = ast.literal_eval(matched.group(1))
    except (ValueError, SyntaxError):
        return collapse(matched.group(1)[1:-1])
    return collapse(str(value))


def collapse(text: str) -> str:
    return " ".join(text.split())


def transcript_tail(root: Path) -> list[str]:
    return transcript_conversation(root, TAIL_LIMIT, TAIL_BYTES)


def transcript_conversation(root: Path, max_lines: int = 200, window: int = CONV_BYTES) -> list[str]:
    path = live_transcript(root)
    if path is None:
        return []
    entries = [
        entry
        for line in transcript_lines(path, window)
        for entry in [format_entry(line)]
        if entry
    ]
    return entries[-max_lines:]


def live_transcript(root: Path) -> Path | None:
    slug = str(root).replace("/", "-")
    projects = Path.home() / ".claude" / "projects" / slug
    try:
        logs = [p for p in projects.iterdir() if p.is_file() and p.suffix == ".jsonl"]
    except OSError:
        return None
    if not logs:
        return None
    newest = max(logs, key=dir_mtime)
    if time.time() - dir_mtime(newest) > TAIL_STALE_S:
        return None
    return newest


def transcript_lines(path: Path, window: int = TAIL_BYTES) -> list[str]:
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            handle.seek(max(0, size - window))
            raw = handle.read()
    except OSError:
        return []
    lines = raw.decode(errors="replace").splitlines()
    return lines[1:] if size > window else lines


def format_entry(line: str) -> str | None:
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or data.get("type") != "assistant":
        return None
    message = data.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if not isinstance(content, list):
        return None
    for block in content:
        if isinstance(block, dict):
            rendered = render_block(block)
            if rendered:
                return rendered
    return None


def render_block(block: dict[str, object]) -> str | None:
    kind = block.get("type")
    if kind == "thinking":
        text = collapse(str(block.get("thinking") or ""))[:TAIL_CHARS]
        return f"💭 {text}" if text else None
    if kind == "text":
        text = collapse(str(block.get("text") or ""))
        return text or None
    if kind == "tool_use":
        name = collapse(str(block.get("name") or ""))
        detail = tool_detail(block.get("input"))
        return f"⚒ {name} {detail}".rstrip() if name else None
    return None


def tool_detail(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    for key in ("file_path", "command", "pattern"):
        detail = value.get(key)
        if isinstance(detail, str) and detail.strip():
            return collapse(detail)[:TAIL_CHARS]
    return ""


def build_worker(state: RepoState, step: Step, rows: list[ProcRow], real: Path) -> Worker:
    found = [
        process
        for pid, elapsed, tokens in rows
        for process in [agent_process(pid, elapsed, tokens)]
        if process is not None and under_root(cwd_of(pid), real)
    ]
    process = min(found, key=lambda p: p.elapsed_s) if found else None
    if state.task is None:
        return Worker(step=step, process=process, result_path=None, prompt_path=None, handoff_path=None)
    base = state.root / ".marestail"
    return Worker(
        step=step,
        process=process,
        result_path=existing(base / "runs" / state.task / f"{step.label}.json"),
        prompt_path=existing(base / "runs" / state.task / f"{step.label}.prompt.md"),
        handoff_path=existing(base / "handoffs" / state.task / f"{step.label}.md"),
    )


def existing(path: Path) -> Path | None:
    return path if path.is_file() else None


def proc_rows() -> list[ProcRow]:
    try:
        out = subprocess.run(
            ["ps", "-eo", "pid,etimes,args"], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return []
    rows: list[ProcRow] = []
    for line in out.stdout.splitlines()[1:]:
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        try:
            rows.append((int(parts[0]), int(parts[1]), parts[2].split()))
        except ValueError:
            continue
    return rows


def is_pipeline(tokens: list[str]) -> bool:
    return any(
        os.path.basename(token) == "cli.py" and tokens[index + 1] == "run"
        for index, token in enumerate(tokens[:-1])
    )


def agent_process(pid: int, elapsed: int, tokens: list[str]) -> Process | None:
    if is_pipeline(tokens):
        return None
    backend = next(
        (name for token in tokens for name in [os.path.basename(token)] if name in BACKENDS),
        None,
    )
    if backend is None:
        return None
    return Process(pid=pid, elapsed_s=elapsed, model=model_of(tokens), backend=backend)


def model_of(tokens: list[str]) -> str:
    return next(
        (tokens[index + 1] for index, token in enumerate(tokens[:-1]) if token == "--model"),
        "",
    )


def cwd_of(pid: int) -> Path | None:
    try:
        return Path(os.readlink(f"/proc/{pid}/cwd"))
    except OSError:
        return None


def under_root(cwd: Path | None, root: Path) -> bool:
    if cwd is None:
        return False
    return cwd == root or root in cwd.parents


def real_path(root: Path) -> Path:
    try:
        return root.resolve()
    except OSError:
        return root


def git_line(root: Path, args: list[str]) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if out.returncode != 0:
        return ""
    return out.stdout.strip()


def read_text(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return path.read_text(errors="replace")
    except OSError:
        return None


def result_text(path: Path | None) -> str | None:
    raw = read_text(path)
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    result = data.get("result") if isinstance(data, dict) else None
    return result if isinstance(result, str) else raw
