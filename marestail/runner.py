import json
import os
import re
import shutil
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
LIMIT_PATTERN = re.compile(r"rate.?limit|usage limit|overloaded|capacity|too many requests|\b529\b", re.IGNORECASE)
LIMIT_WAIT_SECONDS = int(os.environ.get("MARESTAIL_LIMIT_WAIT_SECONDS", "600"))
LIMIT_WAITS = int(os.environ.get("MARESTAIL_LIMIT_WAITS", "12"))


@dataclass
class Run:
    config: Config
    task: Path
    model: str | None
    retries: int

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


def run_pipeline(task: Path, start: str | None, stop: str | None, auto: bool, model: str | None, retries: int) -> int:
    config = config_module.load(Path.cwd())
    state = Run(config=config, task=task.resolve(), model=model, retries=retries)
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
    (state.folder / f"{label}.prompt.md").write_text(prompt)
    for _ in range(LIMIT_WAITS):
        started = time.time()
        code, output = run(claude_command(state.model), cwd=state.config.root, stdin=prompt, timeout=4 * 3600)
        (state.folder / f"{label}.json").write_text(output)
        if not rate_limited(code, output):
            print(f"   {label} finished in {(time.time() - started) / 60:.1f} min: {summary(output)}")
            return
        print(f"   rate limited; waiting {LIMIT_WAIT_SECONDS // 60} min before retrying {label}")
        time.sleep(LIMIT_WAIT_SECONDS)
    print(f"   {label}: still rate limited after {LIMIT_WAITS} waits")


def claude_command(model: str | None) -> list[str]:
    command = [os.environ.get("MARESTAIL_CLAUDE", "claude"), "-p", "--permission-mode", "bypassPermissions", "--dangerously-skip-permissions", "--output-format", "json"]
    if model:
        command += ["--model", model]
    return command


def rate_limited(code: int, output: str) -> bool:
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return code != 0 and bool(LIMIT_PATTERN.search(output))
    return bool(data.get("is_error")) and bool(LIMIT_PATTERN.search(str(data.get("result", ""))))


def summary(output: str) -> str:
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return output[-200:].replace("\n", " ")
    cost = data.get("total_cost_usd", 0)
    return f"turns={data.get('num_turns')} api-equivalent=${cost:.2f} {str(data.get('result', ''))[:120]!r}"


def approve(state: Run) -> bool:
    reports = sorted(state.handoffs.glob("*.md"))
    if reports:
        print("\n" + reports[-1].read_text())
    if not sys.stdin.isatty():
        print("non-interactive: continuing without approval (use --to critic to stop here)")
        return True
    return input("continue to coder? [y/N] ").strip().lower() == "y"
