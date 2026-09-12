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
from marestail.pipeline import Judge, Step, Worker, find, names, window
from marestail.report import render
from marestail.shell import run

PASS = "PASS"
BOUNCE = "BOUNCE"
CONFIG_CHANGE = "## Config change"
LIMIT_PATTERN = re.compile(r"rate.?limit|usage limit|session limit|resets \d|overloaded|capacity|too many requests|\b529\b|quota", re.IGNORECASE)
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
KILO_DEFAULT_MODEL = "kilo/stepfun/step-3.7-flash:free"
KILO_DEFAULT_VARIANT = "high"


@dataclass
class Run:
    config: Config
    task: Path
    model: str | None
    retries: int
    agent: str | None = None
    effort: str | None = None

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
    effort: str | None = None,
) -> int:
    config = config_module.load(Path.cwd())
    if model is None:
        model = config.get("agent", "model")
    if effort is None:
        effort = config.get("agent", "effort")
    state = Run(config=config, task=task.resolve(), model=model, retries=retries, agent=agent, effort=effort)
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
    previous = ""
    bounce = 0
    while True:
        verdict, target, report = run_judge(state, judge)
        if verdict == PASS:
            return True
        if same_findings(previous, report):
            print(f"{judge.name} repeated the same findings twice; the worker is not making progress, stopping for a human")
            return False
        if judge.bounces and bounce >= judge.bounces:
            print(f"{judge.name} still bouncing after {judge.bounces} rounds; stopping for a human")
            return False
        previous = report
        bounce += 1
        worker = find(target or judge.bounce_to)
        if not isinstance(worker, Worker) or not run_worker(state, worker, report):
            return False


def same_findings(previous: str, current: str) -> bool:
    return bool(previous) and normalise_findings(previous) == normalise_findings(current)


def normalise_findings(report: str) -> list[str]:
    lines = [re.sub(r"\s+", " ", line.strip()) for line in report.splitlines()]
    return [line for line in lines if re.match(r"^\d+\.", line)]


def attempts(retries: int):
    n = 1
    while retries <= 0 or n <= retries:
        yield n
        n += 1


def run_worker(state: Run, worker: Worker, feedback: str) -> bool:
    before = head(state.config)
    for attempt in attempts(state.retries):
        report = state.next_report(worker.name)
        print(f"== {worker.name} ({report.stem}) attempt {attempt}")
        prompt = prompts.worker_prompt(state.config, worker, state.task, state.task_name, report, feedback, agent_label(state))
        invoke(state, report.stem, prompt)
        problems = verify_worker(state, worker, report, before)
        if not problems:
            fold_handoff(state.config, worker.name, report, before, agent_label(state))
            return True
        feedback = problems
        print(problems)
    return False


def run_judge(state: Run, judge: Judge) -> tuple[str, str | None, str]:
    gate_report, gate_ok = gate_for(judge.tier)
    for attempt in attempts(state.retries):
        report = state.next_report(judge.name)
        print(f"== {judge.name} ({report.stem}) attempt {attempt}")
        prompt = prompts.judge_prompt(state.config, judge, state.task, state.task_name, report, gate_report)
        invoke(state, report.stem, prompt)
        discard_edits(state.config, keep=report)
        blob = ""
        json_path = state.folder / f"{report.stem}.json"
        if json_path.exists():
            blob = json_path.read_text()
        parsed = parse_verdict(report, blob)
        if parsed is None:
            print(f"{judge.name} wrote no verdict; retrying")
            continue
        if not report.exists():
            verdict, target = parsed
            line = f"VERDICT: {verdict}" + (f" {target}" if target else "")
            report.write_text(line + "\n")
        verdict, target = parsed
        text = report.read_text()
        if not gate_ok:
            verdict, target = BOUNCE, None
            text = gate_report + "\n\n" + text
        record_commit(state.config, f"{judge.name} verdict: {verdict}" + (f" to {target}" if target else ""), text, judge.name, agent_label(state))
        print(f"   verdict {verdict}" + (f" to {target}" if target else ""))
        return verdict, target, text
    shown = "unlimited" if state.retries <= 0 else str(state.retries)
    return BOUNCE, None, f"{judge.name} produced no verdict after {shown} attempts"


def gate_for(tier: str | None) -> tuple[str, bool]:
    if tier is None:
        return "", True
    results = run_gates(tier, False, None)
    return render(results), all(result.ok for result in results)


def parse_verdict(report: Path, extra: str = "") -> tuple[str, str | None] | None:
    text = report.read_text() if report.exists() else ""
    blob = text + "\n" + extra
    match = re.search(r"VERDICT:\s*(PASS|BOUNCE)(?:[ \t]+(\w+))?", blob, re.IGNORECASE)
    if not match:
        return None
    target = match.group(2).lower() if match.group(2) else None
    if target and target not in names():
        target = None
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
    label = agent_label(state)
    _, diff = run(["git", "diff", f"{before}..HEAD", "--", *frozen], cwd=config.root)
    if justification is None:
        revert(config, before, frozen, stamped(f"Revert change to frozen files by {report.stem}\n\nBy runner.", label))
        return [f"{path} is frozen for {worker.name}; reverted. Work within the current configuration." for path in frozen]
    body = f"Proposed by {report.stem}: {', '.join(frozen)}\n\n{justification}\n\n```diff\n{diff.strip()}\n```\n"
    revert(config, before, frozen, stamped(f"Revert change to frozen files by {report.stem}, recorded as a proposal\n\n{body}\nBy runner.", label))
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


def fold_handoff(config: Config, role: str, report: Path, before: str, label: str) -> None:
    body = report.read_text().strip()
    if head(config) == before:
        record_commit(config, f"{role} handoff", body, role, label)
        return
    stamp_history(config, before, label)
    _, original = run(["git", "log", "-1", "--format=%B"], cwd=config.root)
    message = stamped(strip_byline(original, role), label) + f"\n\n{body}\n\nBy {role}."
    run(["git", "commit", "--amend", "--allow-empty", "-q", "-m", message], cwd=config.root)


def strip_byline(message: str, role: str) -> str:
    lines = message.rstrip().splitlines()
    if lines and lines[-1].strip() == f"By {role}.":
        lines = lines[:-1]
    return "\n".join(lines).rstrip()


def record_commit(config: Config, subject: str, body: str, role: str, label: str) -> None:
    message = stamped(f"{subject}\n\n{body.strip()}\n\nBy {role}.", label)
    run(["git", "commit", "--allow-empty", "-q", "-m", message], cwd=config.root)


def stamped(message: str, label: str) -> str:
    prefix = f"[{label}]"
    if not label or message.startswith(prefix):
        return message
    return f"{prefix} {message}"


def stamp_history(config: Config, before: str, label: str) -> None:
    commits = rev_list(config, before, ["--no-merges"])
    if not label or not commits or commits != rev_list(config, before, []):
        return
    parent = before
    for sha in commits:
        parent = restamp(config, sha, parent, label)
    run(["git", "reset", "--hard", "-q", parent], cwd=config.root)


def rev_list(config: Config, before: str, options: list[str]) -> list[str]:
    _, output = run(["git", "rev-list", "--reverse", *options, f"{before}..HEAD"], cwd=config.root)
    return output.split()


def restamp(config: Config, sha: str, parent: str, label: str) -> str:
    _, details = run(["git", "log", "-1", "--format=%an%n%ae%n%aI%n%B", sha], cwd=config.root)
    name, email, date, message = details.split("\n", 3)
    author = {"GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email, "GIT_AUTHOR_DATE": date}
    tree = f"{sha}^{{tree}}"
    _, created = run(["git", "commit-tree", tree, "-p", parent, "-m", stamped(message.strip(), label)], cwd=config.root, env=author)
    return created.split()[0]


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
    backend = resolve_agent(state)
    if backend == "grok":
        invoke_grok(state, label, prompt_file)
        return
    if backend == "kilo":
        invoke_kilo(state, label, prompt)
        return
    if backend == "kimi":
        invoke_kimi(state, label, prompt)
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


def invoke_kilo(state: Run, label: str, prompt: str) -> None:
    for _ in range(LIMIT_WAITS):
        started = time.time()
        code, output = kilo_run(state, prompt)
        (state.folder / f"{label}.json").write_text(output)
        if not kilo_rate_limited(code, output):
            print(f"   {label} finished in {(time.time() - started) / 60:.1f} min: {kilo_summary(output)}")
            return
        print(f"   rate limited; waiting {LIMIT_WAIT_SECONDS // 60} min before retrying {label}")
        time.sleep(LIMIT_WAIT_SECONDS)
    print(f"   {label}: still rate limited after {LIMIT_WAITS} waits")


def invoke_kimi(state: Run, label: str, prompt: str) -> None:
    for _ in range(LIMIT_WAITS):
        started = time.time()
        code, output = kimi_run(state, prompt)
        (state.folder / f"{label}.json").write_text(output)
        if not kimi_rate_limited(code, output):
            print(f"   {label} finished in {(time.time() - started) / 60:.1f} min: {kimi_summary(output)}")
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


def agent_label(state: Run) -> str:
    return " ".join(part for part in (model_name(state), effort_name(state)) if part)


def model_name(state: Run) -> str:
    if state.model:
        return state.model
    backend = resolve_agent(state)
    return KILO_DEFAULT_MODEL if backend == "kilo" else backend


def effort_name(state: Run) -> str:
    backend = resolve_agent(state)
    if backend == "kilo":
        return kilo_variant(state) or ""
    if backend == "grok":
        return grok_effort(state) or ""
    return state.effort or ""


def agent_command(state: Run) -> list[str]:
    backend = resolve_agent(state)
    if backend == "agy":
        binary = os.environ.get("MARESTAIL_AGY", "agy")
        command = [binary, "--dangerously-skip-permissions", "--output-format", "json", "--print-timeout", "4h"]
        if state.model:
            command += ["--model", state.model]
        if state.effort:
            command += ["--effort", state.effort]
        return command
    if backend == "cursor":
        binary = os.environ.get("MARESTAIL_CURSOR", "cursor-agent")
        command = [
            binary,
            "-p",
            "--output-format", "json",
            "--force",
            "--trust",
            "--sandbox", "disabled",
        ]
        if state.model:
            command += ["--model", state.model]
        return command
    if backend == "kilo":
        return kilo_command(state)
    command = [os.environ.get("MARESTAIL_CLAUDE", "claude"), "-p", "--permission-mode", "bypassPermissions", "--dangerously-skip-permissions", "--output-format", "json"]
    if state.model:
        command += ["--model", state.model]
    if state.effort:
        command += ["--effort", state.effort]
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
    effort = grok_effort(state)
    if effort:
        command += ["--reasoning-effort", effort]
    return command


def grok_effort(state: Run) -> str | None:
    return state.effort or os.environ.get("MARESTAIL_GROK_EFFORT")


def kilo_command(state: Run) -> list[str]:
    model = state.model or KILO_DEFAULT_MODEL
    command = [
        os.environ.get("MARESTAIL_KILO", "kilo"),
        "run",
        "--auto",
        "--format", "json",
        "--log-level", "ERROR",
        "--model", model,
    ]
    variant = kilo_variant(state)
    if variant:
        command += ["--variant", variant]
    return command


def kilo_variant(state: Run) -> str | None:
    variant = state.effort if state.effort is not None else os.environ.get("MARESTAIL_KILO_VARIANT")
    if variant == "":
        return None
    if variant is None and (state.model or KILO_DEFAULT_MODEL) == KILO_DEFAULT_MODEL:
        return KILO_DEFAULT_VARIANT
    return variant


def kilo_run(state: Run, prompt: str) -> tuple[int, str]:
    from marestail.shell import clean

    command = kilo_command(state)
    try:
        completed = subprocess.run(
            command,
            cwd=state.config.root,
            env=os.environ,
            input=prompt,
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


def kilo_events(output: str) -> list[dict]:
    events = []
    decoder = json.JSONDecoder()
    for line in output.splitlines():
        text = line.strip()
        start = text.find("{")
        if start < 0:
            continue
        try:
            data, _ = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            events.append(data)
    return events


def kilo_rate_limited(code: int, output: str) -> bool:
    events = kilo_events(output)
    if not events:
        return code != 0 and bool(LIMIT_PATTERN.search(output))
    errors = []
    for event in events:
        if event.get("type") != "error":
            continue
        err = event.get("error")
        errors.append(str(err.get("message") if isinstance(err, dict) else err or event))
    blob = " ".join(errors)
    if not blob:
        return False
    return bool(LIMIT_PATTERN.search(blob))


def kilo_summary(output: str) -> str:
    events = kilo_events(output)
    if not events:
        return output[-200:].replace("\n", " ")
    texts = []
    error = ""
    tokens = None
    cost = None
    for event in events:
        kind = event.get("type")
        part = event.get("part") if isinstance(event.get("part"), dict) else {}
        if kind == "text":
            text = str(part.get("text") or event.get("text") or "").strip()
            if text:
                texts.append(text)
        elif kind == "error":
            err = event.get("error")
            error = str(err.get("message") if isinstance(err, dict) else err or "")[:120]
        elif kind == "step_finish":
            tokens = part.get("tokens") or part.get("total_tokens") or tokens
            cost = part.get("cost") or part.get("costUSD") or cost
    text = error or (texts[-1] if texts else "")
    text = text[:120]
    bits = []
    if tokens is not None:
        bits.append(f"tokens={tokens}")
    if cost is not None:
        try:
            bits.append(f"api-equivalent=${float(cost):.2f}")
        except (TypeError, ValueError):
            bits.append(f"cost={cost}")
    bits.append(repr(text))
    return " ".join(bits)


def kimi_command(state: Run, prompt: str) -> list[str]:
    command = [
        os.environ.get("MARESTAIL_KIMI", "kimi"),
        "-p", prompt,
        "--output-format", "stream-json",
    ]
    if state.model:
        command += ["-m", state.model]
    return command


def kimi_run(state: Run, prompt: str) -> tuple[int, str]:
    from marestail.shell import clean

    command = kimi_command(state, prompt)
    try:
        completed = subprocess.run(
            command,
            cwd=state.config.root,
            env=os.environ,
            input="",
            capture_output=True,
            text=True,
            timeout=4 * 3600,
            check=False,
        )
    except FileNotFoundError as error:
        return 127, f"{command[0]}: not found ({error})"
    except subprocess.TimeoutExpired:
        return 124, f"{command[0]}: timed out after {4 * 3600}s"
    text = completed.stdout
    if completed.returncode != 0:
        text = (text + "\n" + completed.stderr).strip()
    return completed.returncode, clean(text)


def kimi_events(output: str) -> list[dict]:
    events = []
    decoder = json.JSONDecoder()
    for line in output.splitlines():
        text = line.strip()
        start = text.find("{")
        if start < 0:
            continue
        try:
            data, _ = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            events.append(data)
    return events


def kimi_texts(events: list[dict]) -> list[str]:
    texts = []
    for event in events:
        if event.get("role") != "assistant" and event.get("type") != "assistant":
            continue
        message = event.get("message") if isinstance(event.get("message"), dict) else {}
        for content in (event.get("content"), message.get("content")):
            if isinstance(content, str) and content.strip():
                texts.append(content.strip())
            elif isinstance(content, list):
                texts.extend(str(part["text"]).strip() for part in content if isinstance(part, dict) and part.get("text"))
    return texts


def kimi_errors(events: list[dict]) -> list[str]:
    errors = []
    for event in events:
        err = event.get("error")
        if isinstance(err, dict):
            errors.append(str(err.get("message") or err))
        elif err:
            errors.append(str(err))
        elif "error" in str(event.get("type") or "") or "error" in str(event.get("role") or ""):
            errors.append(str(event.get("message") or event.get("content") or event))
    return errors


def kimi_rate_limited(code: int, output: str) -> bool:
    errors = kimi_errors(kimi_events(output))
    if errors:
        return bool(LIMIT_PATTERN.search(" ".join(errors)))
    return code != 0 and bool(LIMIT_PATTERN.search(output))


def kimi_summary(output: str) -> str:
    events = kimi_events(output)
    if not events:
        return output[-200:].replace("\n", " ")
    texts = kimi_texts(events)
    errors = kimi_errors(events)
    turns = None
    tokens = None
    cost = None
    for event in events:
        turns = event.get("num_turns", turns)
        cost = event.get("total_cost_usd", cost)
        usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
        tokens = usage.get("total_tokens") or tokens
        if tokens is None and ("input_tokens" in usage or "output_tokens" in usage):
            tokens = int(usage.get("input_tokens") or 0) + int(usage.get("output_tokens") or 0)
    text = (errors[-1] if errors else (texts[-1] if texts else ""))[:120]
    bits = []
    if turns is not None:
        bits.append(f"turns={turns}")
    if tokens is not None:
        bits.append(f"tokens={tokens}")
    if cost is not None:
        try:
            bits.append(f"api-equivalent=${float(cost):.2f}")
        except (TypeError, ValueError):
            bits.append(f"cost={cost}")
    bits.append(repr(text))
    return " ".join(bits)


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
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    if data.get("type") == "result" or ("inputTokens" in usage or "outputTokens" in usage):
        text = repr(str(data.get("result") or "")[:120])
        tokens = usage.get("total_tokens")
        if tokens is None and ("inputTokens" in usage or "outputTokens" in usage):
            tokens = int(usage.get("inputTokens") or 0) + int(usage.get("outputTokens") or 0)
        token_info = f"tokens={tokens} " if tokens is not None else ""
        return f"{token_info}{text}".strip()
    turns = data.get("num_turns", "?")
    text = repr(str(data.get("result") or data.get("response") or "")[:120])
    tokens = usage.get("total_tokens")
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
