import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from marestail import audit, freeze, prompts
from marestail import config as config_module
from marestail.cli import run_gates
from marestail.config import Config
from marestail.pipeline import Judge, Step, Worker, find, window
from marestail.report import render
from marestail.shell import run

PASS = "PASS"
BOUNCE = "BOUNCE"
CONFIG_CHANGE = "## Config change"
LIMIT_PATTERN = re.compile(r"rate.?limit|usage limit|overloaded|capacity|too many requests|\b529\b|quota", re.IGNORECASE)
LIMIT_WAIT_SECONDS = int(os.environ.get("MARESTAIL_LIMIT_WAIT_SECONDS", "600"))
LIMIT_WAITS = int(os.environ.get("MARESTAIL_LIMIT_WAITS", "12"))
GROK_ENV = {
    "GROK_MEMORY": "0",
    "GROK_ASK_USER_QUESTION": "0",
    "GROK_WORKFLOWS": "0",
    "GROK_CLAUDE_HOOKS_ENABLED": "0",
}
GROK_LIMIT_PATTERN = re.compile(
    r"rate.?limit|usage limit|overloaded|capacity|too many requests|\b529\b|\b503\b",
    re.IGNORECASE,
)
GROK_APPROVE_LOCK = re.compile(
    r"always-approve|always approve|bypassPermissions|yolo.{0,40}(disabled|locked|forbidden)|disable_bypass",
    re.I,
)


@dataclass
class Run:
    config: Config
    task: Path
    model: str | None
    retries: int
    agent: str | None = None

    @property
    def task_name(self) -> str:
        return self.task.stem

    @property
    def folder(self) -> Path:
        return self.config.work / "runs" / self.task_name

    @property
    def handoffs(self) -> Path:
        return self.config.work / "handoffs" / self.task_name

    def next_report(self, role: str) -> Path:
        self.handoffs.mkdir(parents=True, exist_ok=True)
        existing = list(self.handoffs.glob("*.md"))
        return self.handoffs / f"{len(existing) + 1:02d}-{role}.md"


def run_pipeline(
    task: Path,
    start: str | None,
    stop: str | None,
    auto: bool,
    model: str | None,
    retries: int,
    agent: str | None = None,
) -> int:
    config = config_module.load(Path.cwd())
    state = Run(config=config, task=task.resolve(), model=model, retries=retries, agent=agent)
    outcome = 0
    for step in window(start, stop):
        if not run_step(state, step):
            print(f"pipeline stopped at {step.name}")
            outcome = 1
            break
        if step.pause_after and not auto and not approve(state):
            outcome = 1
            break
    else:
        print("pipeline complete")
        archive_handoffs(state)
    print(proposals_summary(state))
    return outcome


def run_step(state: Run, step: Step) -> bool:
    if isinstance(step, Worker):
        return run_worker(state, step, "")
    return run_judge_loop(state, step)


def run_judge_loop(state: Run, judge: Judge) -> bool:
    for bounce in range(judge.bounces + 1):
        verdict, target, report = run_judge(state, judge)
        if verdict == PASS:
            return True
        if bounce == judge.bounces:
            print(f"{judge.name} still bouncing after {judge.bounces} rounds; stopping for a human")
            return False
        worker = find(target or judge.bounce_to)
        if not isinstance(worker, Worker) or not run_worker(state, worker, report):
            return False
    return False


def run_worker(state: Run, worker: Worker, feedback: str) -> bool:
    before = head(state.config)
    for attempt in range(1, state.retries + 1):
        report = state.next_report(worker.name)
        print(f"== {worker.name} ({report.stem}) attempt {attempt}")
        prompt = prompts.worker_prompt(state.config, worker, state.task, state.task_name, report, feedback)
        invoke(state, report.stem, prompt)
        problems = verify_worker(state, worker, report, before)
        if not problems:
            fold_handoff(state.config, worker.name, report, before)
            return True
        feedback = problems
        print(problems)
    return False


def run_judge(state: Run, judge: Judge) -> tuple[str, str | None, str]:
    gate_report, gate_ok = gate_for(judge.tier)
    for attempt in range(1, state.retries + 1):
        report = state.next_report(judge.name)
        print(f"== {judge.name} ({report.stem}) attempt {attempt}")
        prompt = prompts.judge_prompt(state.config, judge, state.task, state.task_name, report, gate_report)
        invoke(state, report.stem, prompt)
        discard_edits(state.config, keep=report)
        parsed = parse_verdict(report)
        if parsed is None:
            print(f"{judge.name} wrote no verdict; retrying")
            continue
        verdict, target = parsed
        text = report.read_text()
        if not gate_ok:
            verdict, target = BOUNCE, None
            text = gate_report + "\n\n" + text
        record_commit(state.config, f"{judge.name} verdict: {verdict}" + (f" to {target}" if target else ""), text, judge.name)
        print(f"   verdict {verdict}" + (f" to {target}" if target else ""))
        return verdict, target, text
    return BOUNCE, None, f"{judge.name} produced no verdict after {state.retries} attempts"


def gate_for(tier: str | None) -> tuple[str, bool]:
    if tier is None:
        return "", True
    results = run_gates(tier, False, None)
    return render(results), all(result.ok for result in results)


def parse_verdict(report: Path) -> tuple[str, str | None] | None:
    if not report.exists():
        return None
    first = report.read_text().strip().splitlines()[:1]
    match = re.match(r"VERDICT:\s*(PASS|BOUNCE)(?:\s+(\w+))?", first[0].strip(), re.IGNORECASE) if first else None
    if not match:
        return None
    target = match.group(2).lower() if match.group(2) else None
    return match.group(1).upper(), target


def verify_worker(state: Run, worker: Worker, report: Path, before: str) -> str:
    config = state.config
    problems = []
    if not report.exists():
        problems.append(f"missing handoff {report.relative_to(config.root)}")
    dirty = changed_paths(config, ["git", "status", "--porcelain", "--untracked-files=all"])
    if dirty:
        problems.append("uncommitted changes:\n" + "\n".join(dirty[:20]))
    touched = changed_paths(config, ["git", "diff", "--name-only", f"{before}..HEAD"]) + dirty
    frozen = freeze.frozen_paths(config, worker.name, touched)
    if frozen and not dirty:
        problems.extend(reject_config_change(state, worker, report, before, frozen))
    elif frozen:
        problems.extend(f"{path} is frozen for {worker.name}" for path in frozen)
    if worker.audit and report.exists():
        problems.extend(audit.problems(config, state.task_name, report.read_text()))
    if worker.tier:
        results = run_gates(worker.tier, False, None)
        if not all(result.ok for result in results):
            problems.append(render(results))
    return "\n\n".join(problems)


def reject_config_change(state: Run, worker: Worker, report: Path, before: str, frozen: list[str]) -> list[str]:
    config = state.config
    justification = config_change_section(report)
    _, diff = run(["git", "diff", f"{before}..HEAD", "--", *frozen], cwd=config.root)
    if justification is None:
        revert(config, before, frozen, f"Revert change to frozen files by {report.stem}\n\nBy runner.")
        return [f"{path} is frozen for {worker.name}; reverted. Work within the current configuration." for path in frozen]
    body = f"Proposed by {report.stem}: {', '.join(frozen)}\n\n{justification}\n\n```diff\n{diff.strip()}\n```\n"
    revert(config, before, frozen, f"Revert change to frozen files by {report.stem}, recorded as a proposal\n\n{body}\nBy runner.")
    proposal = state.next_report("proposal")
    proposal.write_text(body)
    return [
        f"{', '.join(frozen)}: frozen, reverted. Your reason was recorded as {proposal.relative_to(config.root)} "
        "for a human to consider after the run. Find a way within the current configuration."
    ]


def config_change_section(report: Path) -> str | None:
    text = report.read_text() if report.exists() else ""
    if CONFIG_CHANGE not in text:
        return None
    section = text.split(CONFIG_CHANGE, 1)[1]
    return section.split("\n## ", 1)[0].strip()


def revert(config: Config, before: str, paths: list[str], message: str) -> None:
    run(["git", "checkout", before, "--", *paths], cwd=config.root)
    run(["git", "add", "-A", "--", *paths], cwd=config.root)
    run(["git", "commit", "-q", "-m", message], cwd=config.root)


def proposals_summary(state: Run) -> str:
    files = sorted(state.handoffs.glob("*-proposal.md")) if state.handoffs.exists() else []
    if not files:
        return ""
    body = "\n\n".join(f"### {f.stem}\n{f.read_text().strip()}" for f in files)
    return f"\n## Config changes the agents asked for and were refused\nDecide whether to make any of these yourself.\n\n{body}"


def changed_paths(config: Config, command: list[str]) -> list[str]:
    _, output = run(command, cwd=config.root)
    paths = [line[3:] if line[:2].strip() and line[2:3] == " " else line for line in output.splitlines()]
    return [path.split(" -> ")[-1].strip() for path in paths if path.strip() and not path.strip().startswith(".marestail/")]


def discard_edits(config: Config, keep: Path) -> None:
    keep_relative = str(keep.relative_to(config.root))
    stray = [path for path in changed_paths(config, ["git", "status", "--porcelain", "--untracked-files=all"]) if path != keep_relative]
    if not stray:
        return
    print(f"   discarding edits a judge made: {', '.join(stray[:10])}")
    run(["git", "checkout", "--", "."], cwd=config.root)
    run(["git", "clean", "-fdq", "-e", keep_relative, "-e", ".marestail/"], cwd=config.root)


def fold_handoff(config: Config, role: str, report: Path, before: str) -> None:
    body = report.read_text().strip()
    if head(config) == before:
        record_commit(config, f"{role} handoff", body, role)
        return
    _, original = run(["git", "log", "-1", "--format=%B"], cwd=config.root)
    message = strip_byline(original, role) + f"\n\n{body}\n\nBy {role}."
    run(["git", "commit", "--amend", "--allow-empty", "-q", "-m", message], cwd=config.root)


def strip_byline(message: str, role: str) -> str:
    lines = message.rstrip().splitlines()
    if lines and lines[-1].strip() == f"By {role}.":
        lines = lines[:-1]
    return "\n".join(lines).rstrip()


def record_commit(config: Config, subject: str, body: str, role: str) -> None:
    run(["git", "commit", "--allow-empty", "-q", "-m", f"{subject}\n\n{body.strip()}\n\nBy {role}."], cwd=config.root)


def archive_handoffs(state: Run) -> None:
    if not state.handoffs.exists():
        return
    destination = state.folder / f"handoffs-{time.strftime('%Y%m%dT%H%M%S')}"
    state.folder.mkdir(parents=True, exist_ok=True)
    shutil.move(str(state.handoffs), str(destination))


def head(config: Config) -> str:
    _, output = run(["git", "rev-parse", "HEAD"], cwd=config.root)
    return output.strip()


def invoke(state: Run, label: str, prompt: str) -> None:
    state.folder.mkdir(parents=True, exist_ok=True)
    prompt_file = state.folder / f"{label}.prompt.md"
    prompt_file.write_text(prompt)
    if resolve_agent(state) == "grok":
        invoke_grok(state, label, prompt_file)
        return
    for _ in range(LIMIT_WAITS):
        started = time.time()
        code, output = run(agent_command(state), cwd=state.config.root, stdin=prompt, timeout=4 * 3600)
        (state.folder / f"{label}.json").write_text(output)
        if not rate_limited(code, output):
            print(f"   {label} finished in {(time.time() - started) / 60:.1f} min: {summary(output)}")
            return
        print(f"   rate limited; waiting {LIMIT_WAIT_SECONDS // 60} min before retrying {label}")
        time.sleep(LIMIT_WAIT_SECONDS)
    print(f"   {label}: still rate limited after {LIMIT_WAITS} waits")


def invoke_grok(state: Run, label: str, prompt_file: Path) -> None:
    for _ in range(LIMIT_WAITS):
        started = time.time()
        code, output = grok_run(state, prompt_file)
        (state.folder / f"{label}.json").write_text(output)
        if grok_always_approve_locked(code, output):
            print(f"   {label}: grok always-approve is locked; cannot run unattended")
            return
        if not grok_rate_limited(code, output):
            print(f"   {label} finished in {(time.time() - started) / 60:.1f} min: {grok_summary(output)}")
            return
        print(f"   rate limited; waiting {LIMIT_WAIT_SECONDS // 60} min before retrying {label}")
        time.sleep(LIMIT_WAIT_SECONDS)
    print(f"   {label}: still rate limited after {LIMIT_WAITS} waits")


def resolve_agent(state: Run) -> str:
    if state.agent:
        return state.agent.lower()
    env = os.environ.get("MARESTAIL_AGENT")
    if env:
        return env.lower()
    configured = state.config.get("agent", "backend")
    if configured:
        return str(configured).lower()
    return "claude"


def agent_command(state: Run) -> list[str]:
    backend = resolve_agent(state)
    if backend == "agy":
        binary = os.environ.get("MARESTAIL_AGY", "agy")
        command = [binary, "--dangerously-skip-permissions", "--output-format", "json", "--print-timeout", "4h"]
        if state.model:
            command += ["--model", state.model]
        return command
    command = [os.environ.get("MARESTAIL_CLAUDE", "claude"), "-p", "--permission-mode", "bypassPermissions", "--dangerously-skip-permissions", "--output-format", "json"]
    if state.model:
        command += ["--model", state.model]
    return command


def grok_run(state: Run, prompt_file: Path) -> tuple[int, str]:
    from marestail.shell import clean

    command = grok_command(state, prompt_file)
    merged = {**os.environ, **GROK_ENV}
    try:
        completed = subprocess.run(
            command,
            cwd=state.config.root,
            env=merged,
            input="",
            capture_output=True,
            text=True,
            timeout=4 * 3600,
            check=False,
        )
    except FileNotFoundError as error:
        return 127, f"{command[0]}: not found ({error})"
    except subprocess.TimeoutExpired:
        return 124, f"{' '.join(command)}: timed out after {4 * 3600}s"
    text = completed.stdout
    if completed.returncode != 0 and not (completed.stdout or "").strip():
        text = completed.stderr
    return completed.returncode, clean(text)


def grok_command(state: Run, prompt_file: Path) -> list[str]:
    command = [
        os.environ.get("MARESTAIL_GROK", "grok"),
        "--prompt-file", str(prompt_file.resolve()),
        "--output-format", "json",
        "--always-approve",
        "--no-plan",
        "--trust",
    ]
    if state.model:
        command += ["--model", state.model]
    return command


def grok_parse_json(output: str) -> dict | None:
    text = output.strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    if start < 0:
        return None
    try:
        data, _ = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def grok_rate_limited(code: int, output: str) -> bool:
    data = grok_parse_json(output)
    if data is None:
        return code != 0 and bool(GROK_LIMIT_PATTERN.search(output))
    blob = " ".join(str(data.get(key, "")) for key in ("message", "text", "type", "stopReason"))
    is_error = data.get("type") == "error" or code != 0
    return is_error and bool(GROK_LIMIT_PATTERN.search(blob) or GROK_LIMIT_PATTERN.search(output))


def grok_summary(output: str) -> str:
    data = grok_parse_json(output)
    if data is None:
        return output[-200:].replace("\n", " ")
    text = str(data.get("text") or data.get("message") or "")[:120]
    turns = data.get("num_turns", "?")
    cost = data.get("total_cost_usd")
    if cost is None:
        models = data.get("modelUsage") if isinstance(data.get("modelUsage"), dict) else {}
        parts = [row.get("costUSD") for row in models.values() if isinstance(row, dict)]
        parts = [value for value in parts if value is not None]
        cost = sum(parts) if parts else None
    if cost is not None:
        return f"turns={turns} api-equivalent=${float(cost):.2f} {text!r}"
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    tokens = usage.get("total_tokens")
    token_info = f"tokens={tokens} " if tokens else ""
    return f"turns={turns} {token_info}{text!r}".strip()


def grok_always_approve_locked(code: int, output: str) -> bool:
    if code == 0:
        return False
    data = grok_parse_json(output)
    blob = output if data is None else str(data.get("message") or output)
    return bool(GROK_APPROVE_LOCK.search(blob))


def rate_limited(code: int, output: str) -> bool:
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return code != 0 and bool(LIMIT_PATTERN.search(output))
    if "is_error" in data:
        return bool(data.get("is_error")) and bool(LIMIT_PATTERN.search(str(data.get("result", ""))))
    error_text = str(data.get("response", "")) or str(data.get("error", ""))
    is_error = data.get("status") == "ERROR" or code != 0
    return is_error and bool(LIMIT_PATTERN.search(error_text))


def summary(output: str) -> str:
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return output[-200:].replace("\n", " ")
    if "total_cost_usd" in data:
        cost = data.get("total_cost_usd", 0)
        return f"turns={data.get('num_turns')} api-equivalent=${cost:.2f} {str(data.get('result', ''))[:120]!r}"
    turns = data.get("num_turns", "?")
    text = repr(str(data.get("result") or data.get("response") or "")[:120])
    tokens = data.get("usage", {}).get("total_tokens")
    token_info = f"tokens={tokens} " if tokens else ""
    return f"turns={turns} {token_info}{text}".strip()


def approve(state: Run) -> bool:
    reports = sorted(state.handoffs.glob("*.md"))
    if reports:
        print("\n" + reports[-1].read_text())
    if not sys.stdin.isatty():
        print("non-interactive: continuing without approval (use --to critic to stop here)")
        return True
    return input("continue to coder? [y/N] ").strip().lower() == "y"
