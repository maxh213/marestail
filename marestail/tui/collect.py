import ast
import json
import os
import re
import subprocess
import time
from pathlib import Path

from .model import Fleet, Process, RepoState, Step, Worker

BACKENDS = ("claude", "grok", "agy", "cursor-agent", "kilo", "kimi")
STEP_RE = re.compile(r"^== (\S+) \((\S+)\) attempt (\d+)")
FINISH_RE = re.compile(r"^\s+(\S+) finished in ([0-9.]+) min: (.*)$")
VERDICT_RE = re.compile(r"^\s+verdict (\S+)")
QUOTED_RE = re.compile(r"""('(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")\s*$""")
TAIL_BYTES = 65536
CONV_BYTES = 262144
TAIL_STALE_S = 900
TAIL_LIMIT = 3
TAIL_CHARS = 90

ProcRow = tuple[int, int, int, list[str]]


def discover(roots: list[Path]) -> list[Path]:
    found: list[Path] = []
    for root in roots:
        found.extend(discover_root(root))
    return found


def discover_root(root: Path) -> list[Path]:
    found = [root] if (root / ".marestail").is_dir() else []
    return found + [child for child in list_dirs(root) if (child / ".marestail").is_dir()]


def list_dirs(root: Path) -> list[Path]:
    try:
        return sorted(path for path in root.iterdir() if path.is_dir())
    except OSError:
        return []


def collect_repo(root: Path) -> RepoState:
    state = repo_shell(root)
    attach_live(state, real_path(root), proc_rows())
    return state


def repo_shell(root: Path) -> RepoState:
    log_path = latest_log(root)
    return RepoState(
        name=root.name,
        root=root,
        branch=git_line(root, ["branch", "--show-current"]),
        head=git_line(root, ["log", "-1", "--format=%h%x20%s"]),
        task=task_name(root),
        log_path=log_path,
        steps=parse_log(log_path) if log_path is not None else [],
    )


def attach_live(state: RepoState, real: Path, rows: list[ProcRow]) -> None:
    pipeline = pipeline_pids(rows, real)
    state.alive = bool(pipeline)
    bind_running(state, rows, real)
    if state.alive:
        attach_activity(state, rows, pipeline, real)


def pipeline_pids(rows: list[ProcRow], real: Path) -> list[int]:
    return [pid for pid, _, _, tokens in rows if is_pipeline(tokens) and under_root(cwd_of(pid), real)]


def bind_running(state: RepoState, rows: list[ProcRow], real: Path) -> None:
    running = running_step(state.steps)
    if running is not None:
        state.worker = build_worker(state, running, rows, real)


def running_step(steps: list[Step]) -> Step | None:
    return steps[-1] if steps and steps[-1].status == "running" else None


def attach_activity(state: RepoState, rows: list[ProcRow], pipeline: list[int], real: Path) -> None:
    state.gate_activity = gate_activity(rows, pipeline)
    state.tail_lines = transcript_tail(real)
    if state.worker is not None:
        state.worker.tail_lines = state.tail_lines
    elif state.gate_activity is None:
        state.runner_activity = latest_runner_line(state.log_path)


def latest_runner_line(log_path: Path | None) -> str | None:
    lines = nonempty_lines(log_path)
    return lines[-1] if lines else None


def nonempty_lines(log_path: Path | None) -> list[str]:
    return [] if log_path is None else stripped_lines(log_path)


def stripped_lines(log_path: Path) -> list[str]:
    try:
        return [line.strip() for line in log_path.read_text(errors="ignore").splitlines() if line.strip()]
    except OSError:
        return []


def collect_fleet(roots: list[Path]) -> Fleet:
    repos = [collect_repo(root) for root in discover(roots)]
    return Fleet(repos=repos, scanned_at=time.time())


def conversation_for(repo: RepoState) -> list[tuple[str, str]]:
    sections = worker_sections(repo.worker)
    live = transcript_conversation(real_path(repo.root))
    if live:
        sections.append(("live", "\n".join(live)))
    return sections


def worker_sections(worker: Worker | None) -> list[tuple[str, str]]:
    if worker is None:
        return []
    return named_texts(
        (
            ("prompt", read_text(worker.prompt_path)),
            ("handoff", read_text(worker.handoff_path)),
            ("result", result_text(worker.result_path)),
        )
    )


def named_texts(pairs: tuple[tuple[str, str | None], ...]) -> list[tuple[str, str]]:
    return [(name, text) for name, text in pairs if text is not None]


def task_name(root: Path) -> str | None:
    candidates = list_task_dirs(root / ".marestail" / "handoffs") + list_task_dirs(root / ".marestail" / "runs")
    return max(candidates, key=dir_mtime).name if candidates else None


def list_task_dirs(parent: Path) -> list[Path]:
    try:
        return [path for path in parent.iterdir() if path.is_dir()]
    except OSError:
        return []


def dir_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def latest_log(root: Path) -> Path | None:
    logs = overnight_logs(root / ".marestail" / "runs")
    return max(logs, key=lambda path: path.name) if logs else None


def overnight_logs(runs: Path) -> list[Path]:
    try:
        return [path for path in runs.iterdir() if is_overnight_log(path)]
    except OSError:
        return []


def is_overnight_log(path: Path) -> bool:
    return path.is_file() and path.name.startswith("overnight-") and path.suffix == ".log"


def parse_log(path: Path) -> list[Step]:
    lines = read_lines(path)
    steps: list[Step] = []
    for line in lines:
        apply_log_line(steps, line)
    stamp_running(steps, lines)
    return steps


def read_lines(path: Path) -> list[str]:
    try:
        return path.read_text(errors="replace").splitlines()
    except OSError:
        return []


def apply_log_line(steps: list[Step], line: str) -> None:
    if accept_start(steps, line) or accept_finish(steps, line):
        return
    accept_verdict(steps, line)


def accept_start(steps: list[Step], line: str) -> bool:
    matched = STEP_RE.match(line)
    if matched:
        steps.append(new_step(matched))
    return matched is not None


def new_step(matched: re.Match[str]) -> Step:
    return Step(
        role=matched.group(1),
        label=matched.group(2),
        attempt=int(matched.group(3)),
        status="running",
        summary="",
        verdict=None,
        minutes=None,
    )


def accept_finish(steps: list[Step], line: str) -> bool:
    matched = FINISH_RE.match(line)
    if matched:
        finish_step(steps, matched)
    return matched is not None


def accept_verdict(steps: list[Step], line: str) -> None:
    matched = VERDICT_RE.match(line)
    if matched and steps:
        steps[-1].verdict = matched.group(1)


def stamp_running(steps: list[Step], lines: list[str]) -> None:
    if steps and steps[-1].status == "running":
        steps[-1].summary = collapse(last_text(lines))


def last_text(lines: list[str]) -> str:
    return next((line for line in reversed(lines) if line.strip()), "")


def finish_step(steps: list[Step], matched: re.Match[str]) -> None:
    step = running_named(steps, matched.group(1)) or running_step(steps)
    if step is None:
        return
    step.status = "done"
    step.minutes = float(matched.group(2))
    step.summary = summary_of(matched.group(3))


def running_named(steps: list[Step], label: str) -> Step | None:
    return next((step for step in reversed(steps) if step.label == label and step.status == "running"), None)


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
    return [] if path is None else formatted_tail(path, max_lines, window)


def formatted_tail(path: Path, max_lines: int, window: int) -> list[str]:
    entries = [entry for line in transcript_lines(path, window) for entry in [format_entry(line)] if entry]
    return entries[-max_lines:]


def claude_homes() -> list[Path]:
    work = os.environ.get("DANDELION_CLAUDE_WORK_CONFIG_DIR") or "~/.claude-work"
    homes = [Path.home() / ".claude", Path(work).expanduser()]
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    if configured:
        homes.append(Path(configured).expanduser())
    return homes


def live_transcript(root: Path) -> Path | None:
    logs = [path for home in claude_homes() for path in jsonl_logs(home / "projects" / str(root).replace("/", "-"))]
    return fresh_log(logs)


def jsonl_logs(folder: Path) -> list[Path]:
    try:
        return [path for path in folder.iterdir() if is_jsonl(path)]
    except OSError:
        return []


def is_jsonl(path: Path) -> bool:
    return path.is_file() and path.suffix == ".jsonl"


def fresh_log(logs: list[Path]) -> Path | None:
    if not logs:
        return None
    newest = max(logs, key=dir_mtime)
    return None if time.time() - dir_mtime(newest) > TAIL_STALE_S else newest


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
    data = parsed_json(line)
    if not isinstance(data, dict) or data.get("type") != "assistant":
        return None
    return first_block(message_content(data.get("message")))


def parsed_json(line: str) -> object:
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def message_content(message: object) -> list[object]:
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    return content if isinstance(content, list) else []


def first_block(content: list[object]) -> str | None:
    return next((text for text in rendered_blocks(content) if text), None)


def rendered_blocks(content: list[object]) -> list[str | None]:
    return [render_block(block) for block in content if isinstance(block, dict)]


def render_block(block: dict[str, object]) -> str | None:
    kind = block.get("type")
    if kind == "thinking":
        return thinking_line(block)
    if kind == "text":
        return text_line(block)
    if kind == "tool_use":
        return tool_line(block)
    return None


def thinking_line(block: dict[str, object]) -> str | None:
    text = collapse(str(block.get("thinking") or ""))[:TAIL_CHARS]
    return f"💭 {text}" if text else None


def text_line(block: dict[str, object]) -> str | None:
    text = collapse(str(block.get("text") or ""))
    return text or None


def tool_line(block: dict[str, object]) -> str | None:
    name = collapse(str(block.get("name") or ""))
    return f"⚒ {name} {tool_detail(block.get('input'))}".rstrip() if name else None


def tool_detail(value: object) -> str:
    return next((detail for detail in tool_fields(value) if detail), "")


def tool_fields(value: object) -> list[str]:
    if not isinstance(value, dict):
        return []
    return [collapse(str(value[key]))[:TAIL_CHARS] for key in ("file_path", "command", "pattern") if stripped_str(value.get(key))]


def stripped_str(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def build_worker(state: RepoState, step: Step, rows: list[ProcRow], real: Path) -> Worker:
    process = matching_agent(rows, real)
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


def matching_agent(rows: list[ProcRow], real: Path) -> Process | None:
    found = [process for process in agents_of(rows) if under_root(cwd_of(process.pid), real)]
    return min(found, key=lambda item: item.elapsed_s) if found else None


def agents_of(rows: list[ProcRow]) -> list[Process]:
    return [process for pid, _, elapsed, tokens in rows if (process := agent_process(pid, elapsed, tokens)) is not None]


def existing(path: Path) -> Path | None:
    return path if path.is_file() else None


def proc_rows() -> list[ProcRow]:
    return [row for line in ps_lines() if (row := parse_ps_line(line)) is not None]


def ps_lines() -> list[str]:
    try:
        out = subprocess.run(["ps", "-eo", "pid,ppid,etimes,args"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return []
    return out.stdout.splitlines()[1:]


def parse_ps_line(line: str) -> ProcRow | None:
    parts = line.split(None, 3)
    if len(parts) < 4:
        return None
    try:
        return int(parts[0]), int(parts[1]), int(parts[2]), parts[3].split()
    except ValueError:
        return None


def is_pipeline(tokens: list[str]) -> bool:
    return any(os.path.basename(token) == "cli.py" and tokens[index + 1] == "run" for index, token in enumerate(tokens[:-1]))


def gate_activity(rows: list[ProcRow], pipeline: list[int]) -> str | None:
    found = descendant_gates(child_map(rows), {pid: (elapsed, tokens) for pid, _, elapsed, tokens in rows}, pipeline)
    if not found:
        return None
    elapsed, label = max(found, key=lambda item: item[0])
    return f"{label} {fmt_seconds(elapsed)}"


def child_map(rows: list[ProcRow]) -> dict[int, list[int]]:
    children: dict[int, list[int]] = {}
    for pid, ppid, _, _ in rows:
        children.setdefault(ppid, []).append(pid)
    return children


def descendant_gates(
    children: dict[int, list[int]], by_pid: dict[int, tuple[int, list[str]]], pipeline: list[int]
) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    stack = [child for pid in pipeline for child in children.get(pid, [])]
    while stack:
        pid = stack.pop()
        found.extend(gate_hit(by_pid.get(pid, (0, []))))
        stack.extend(children.get(pid, []))
    return found


def gate_hit(row: tuple[int, list[str]]) -> list[tuple[int, str]]:
    elapsed, tokens = row
    label = classify_gate(tokens)
    return [(elapsed, label)] if label is not None else []


def classify_gate(tokens: list[str]) -> str | None:
    names = [os.path.basename(token) for token in tokens]
    return None if skipped_gate(tokens, names) else gate_label(tokens, names)


def skipped_gate(tokens: list[str], names: list[str]) -> bool:
    return is_pipeline(tokens) or any(name in BACKENDS for name in names)


def gate_label(tokens: list[str], names: list[str]) -> str | None:
    return next((label for label in gate_candidates(tokens, names) if label is not None), None)


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
    return tool if tool in names else None


def mix_gate(tokens: list[str]) -> str | None:
    mix = after(tokens, "mix")
    return None if mix is None else mix_kind(mix)


def mix_kind(mix: str) -> str:
    return "mix test" if mix == "test" else "mix"


def named_command(tokens: list[str], program: str, argument: str, label: str) -> str | None:
    return label if after(tokens, program) == argument else None


def scanner_sonar(names: list[str]) -> str | None:
    return "sonar" if "sonar-scanner" in names else None


def sonarqube_token(tokens: list[str]) -> str | None:
    return "sonar" if any("sonarqube" in token for token in tokens) else None


def java_sonar(names: list[str], tokens: list[str]) -> str | None:
    if "java" not in names:
        return None
    return "sonar" if any("sonar" in token for token in tokens) else None


def maven_gate(names: list[str], tokens: list[str]) -> str | None:
    return None if not maven_cmd(names) else maven_kind(tokens)


def maven_cmd(names: list[str]) -> bool:
    return "mvn" in names or "mvnw" in names


def maven_kind(tokens: list[str]) -> str:
    return "pitest" if any("pitest" in token for token in tokens) else "mvn"


def pmd_gate(names: list[str], tokens: list[str]) -> str | None:
    if "java" not in names:
        return None
    return "pmd" if any(token.endswith("PmdCli") for token in tokens) else None


NAMED_TOOLS = ("rspec", "rubocop", "mutmut", "mutant", "stryker", "pytest", "vitest", "jest", "tsc", "eslint")


def named_tool(names: list[str]) -> str | None:
    return next((tool for tool in NAMED_TOOLS if tool in names), None)


def eunit_gate(names: list[str], tokens: list[str]) -> str | None:
    if "erlc" in names:
        return "eunit"
    return "eunit" if any("eunit" in token for token in tokens) else None


def after(tokens: list[str], name: str) -> str | None:
    for index, token in enumerate(tokens[:-1]):
        if os.path.basename(token) == name:
            return tokens[index + 1]
    return None


def bundle_inner(tokens: list[str]) -> str | None:
    for index, token in enumerate(tokens[:-2]):
        if os.path.basename(token) == "bundle" and tokens[index + 1] == "exec":
            return os.path.basename(tokens[index + 2])
    return None


def docker_inner(tokens: list[str]) -> str | None:
    return next(
        (compose_run_target(tokens, index) for index, token in enumerate(tokens[:-3]) if docker_compose_run(token, tokens, index)), None
    )


def docker_compose_run(token: str, tokens: list[str], index: int) -> bool:
    return os.path.basename(token) == "docker" and tokens[index + 1 : index + 3] == ["compose", "run"]


def compose_run_target(tokens: list[str], index: int) -> str | None:
    inner = [token for token in tokens[index + 3 :] if not token.startswith("-")]
    return os.path.basename(inner[1]) if len(inner) > 1 else None


def fmt_seconds(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h{seconds % 3600 // 60:02d}m"


def agent_process(pid: int, elapsed: int, tokens: list[str]) -> Process | None:
    backend = None if is_pipeline(tokens) else backend_of(tokens)
    return None if backend is None else Process(pid=pid, elapsed_s=elapsed, model=model_of(tokens), backend=backend)


def backend_of(tokens: list[str]) -> str | None:
    return next((name for token in tokens for name in [os.path.basename(token)] if name in BACKENDS), None)


def model_of(tokens: list[str]) -> str:
    return next(
        (tokens[index + 1] for index, token in enumerate(tokens[:-1]) if token in ("--model", "-m")),
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
        out = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=10)
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
    return None if raw is None else decoded_result(raw)


def decoded_result(raw: str) -> str:
    data = parsed_json(raw)
    result = data.get("result") if isinstance(data, dict) else None
    return result if isinstance(result, str) else raw
