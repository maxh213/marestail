import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from marestail import audit, freeze, practices, prompts
from marestail import route as dandelion
from marestail import config as config_module
from marestail.cli import hook_focus, resolve_focus, run_gates
from marestail.config import Config
from marestail.perf import db as perf_db
from marestail.perf import hygiene as perf_hygiene
from marestail.perf import review as perf_review
from marestail.perf import samples as perf_samples
from marestail.perf import settings as perf_settings
from marestail.perf import trees as perf_trees
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
    perf_changes: str = ""
    scope_changed: bool = False
    focus: set[str] = field(default_factory=set)
    hard: bool = False
    route: str | None = None
    account_env: dict[str, str] = field(default_factory=dict)
    labels: set[str] = field(default_factory=set)

    @property
    def task_name(self) -> str:
        return self.task.stem

    @property
    def gate_flags(self) -> str:
        if not self.scope_changed:
            return ""
        scope = "hard" if self.hard else "changed"
        return f" --scope {scope}" + "".join(f" --focus {path}" for path in sorted(self.focus))

    @property
    def hard_focus(self) -> set[str] | None:
        return self.focus if self.hard else None

    def gates(self, tier: str):
        return run_gates(tier, self.scope_changed, None, self.focus, self.hard)

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
    scope: str | None = None,
    focus: list[str] | None = None,
) -> int:
    config = config_module.load(Path.cwd())
    if model is None:
        model = config.get("agent", "model")
    route = model if dandelion.is_routed(model) else None
    if route:
        if agent or effort:
            raise SystemExit(f"--model {route} picks the backend and effort before every session; drop --agent and --effort")
        dandelion.require()
        model = None
    elif effort is None:
        effort = config.get("agent", "effort")
    paths = [path for path in focus or [] if path.strip()]
    if scope == "all" and paths:
        raise SystemExit("--focus cannot be combined with --scope all")
    hard = scope == "hard"
    scope_changed = scope in ("changed", "hard") or bool(paths)
    focused = (resolve_focus(config, set(paths)) | hook_focus(config)) if scope_changed else set()
    if hard and not focused:
        raise SystemExit("--scope hard needs at least one focus path: pass --focus or set [focus] paths in marestail.toml")
    share_scope(scope_changed, hard, focused)
    state = Run(config=config, task=task.resolve(), model=model, retries=retries, agent=agent, effort=effort, scope_changed=scope_changed, focus=focused, hard=hard, route=route)
    perf_trees.record_start(config, state.task_name)
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
    if state.perf_changes:
        print(state.perf_changes)
    return outcome


def share_scope(scope_changed: bool, hard: bool, focus: set[str]) -> None:
    if not scope_changed:
        return
    os.environ["MARESTAIL_SCOPE"] = "hard" if hard else "changed"
    os.environ["MARESTAIL_FOCUS"] = os.pathsep.join(sorted(focus))


def run_step(state: Run, step: Step) -> bool:
    if isinstance(step, Worker):
        return run_worker(state, step, "")
    if step.optional and state.config.get(step.name, "enabled", True) is False:
        print(f"{step.name} disabled in marestail.toml; skipping")
        return True
    if step.name == "practices" and not practices.files(state.config.root):
        print("practices: no guidance files; skipping")
        return True
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
        prompt = prompts.worker_prompt(state.config, worker, state.task, state.task_name, report, feedback, agent_label(state), state.gate_flags, state.hard_focus)
        invoke(state, report.stem, prompt)
        problems = verify_worker(state, worker, report, before)
        if not problems:
            saved = drop_ignored_since(state.config, before)
            fold_handoff(state.config, worker.name, report, before, agent_label(state), state.labels)
            restore_files(state.config, saved)
            return True
        feedback = problems
        print(problems)
    return False


def run_judge(state: Run, judge: Judge) -> tuple[str, str | None, str]:
    gate_report, gate_ok = gate_for(state, judge.tier)
    feedback = ""
    author_left = 3
    for attempt in attempts(state.retries):
        report = state.next_report(judge.name)
        print(f"== {judge.name} ({report.stem}) attempt {attempt}")
        with measuring(state, judge) as session:
            if judge.name == "perf" and gate_ok and session is not None:
                if author_left > 0:
                    feedback = author_phase(state, judge, session, feedback)
                fill_samples(state, session)
            outcome, feedback = judge_attempt(state, judge, report, (gate_report, gate_ok), session, feedback)
        if outcome is not None:
            if judge.name == "perf" and outcome[0] == "AUTHOR":
                if author_left > 0:
                    author_left -= 1
                    feedback = "You asked for an authoring round; benches are editable again in the authoring phase."
                else:
                    feedback = "No authoring rounds left; benches stay frozen. Write PASS or BOUNCE with the benches as they are."
                continue
            return outcome
    shown = "unlimited" if state.retries <= 0 else str(state.retries)
    return BOUNCE, None, f"{judge.name} produced no verdict after {shown} attempts"


@contextlib.contextmanager
def measuring(state: Run, judge: Judge):
    if judge.name != "perf":
        yield None
        return
    with perf_trees.measuring(state.config, state.task_name) as session:
        try:
            perf_db.prepare(state.config, session)
            yield session
        finally:
            perf_db.release(state.config, session)


def judge_attempt(
    state: Run, judge: Judge, report: Path, gate: tuple[str, bool], session: perf_trees.Session | None, feedback: str
) -> tuple[tuple[str, str | None, str] | None, str]:
    gate_report, gate_ok = gate
    trees = perf_trees.prompt_section(state.config, session) if session else ""
    prompt = prompts.judge_prompt(state.config, judge, state.task, state.task_name, report, gate_report, trees, feedback, state.hard_focus)
    before = head(state.config)
    invoke(state, report.stem, prompt)
    writes = () if judge.name == "perf" else judge.writes
    discard_edits(state.config, keep=report, writes=writes)
    blob = ""
    json_path = state.folder / f"{report.stem}.json"
    if json_path.exists():
        blob = json_path.read_text()
    combined = (report.read_text() if report.exists() else "") + "\n" + blob
    if judge.name == "perf" and re.search(r"^\s*VERDICT:\s*AUTHOR\b", combined, re.IGNORECASE | re.MULTILINE):
        return ("AUTHOR", None, ""), feedback
    parsed = parse_verdict(report, blob)
    if parsed is None:
        print(f"{judge.name} wrote no verdict; retrying")
        return None, no_verdict_feedback(report)
    if not report.exists():
        verdict, target = parsed
        line = f"VERDICT: {verdict}" + (f" {target}" if target else "")
        report.write_text(line + "\n")
    verdict, target = parsed
    if judge.pinned_bounce:
        target = None
    text = report.read_text()
    if not gate_ok:
        verdict, target = BOUNCE, None
        text = gate_report + "\n\n" + text
    if session is not None:
        problems = review_measurements(state, session, report, verdict, text)
        if problems:
            print(f"   {judge.name} verdict rejected; retrying")
            return None, problems
        for path in perf_hygiene.discard_scratch(state.config.root):
            print(f"   removed perf scratch {path}")
    stage_writes(state.config, writes)
    saved = drop_ignored_since(state.config, before)
    record_commit(state.config, f"{judge.name} verdict: {verdict}" + (f" to {target}" if target else ""), text, judge.name, agent_label(state))
    restore_files(state.config, saved)
    print(f"   verdict {verdict}" + (f" to {target}" if target else ""))
    return (verdict, target, text), ""


def no_verdict_feedback(report: Path) -> str:
    return (
        f"Your session produced no verdict I could find: no file at {report} and no line starting `VERDICT:` in your output. "
        "Write the verdict file yourself, first line `VERDICT: PASS` or `VERDICT: BOUNCE`, "
        "or print the `VERDICT:` line in your final output."
    )


def author_phase(state: Run, judge: Judge, session: perf_trees.Session, feedback: str) -> str:
    config = state.config
    for round_no in range(1, 4):
        benches = {bench: perf_hygiene.fingerprint(config.root, bench) for bench in perf_review.bench_scripts(config)}
        note = state.folder / f"perf-author-{round_no}.md"
        trees = perf_trees.prompt_section(config, session)
        before = head(config)
        invoke(state, note.stem, prompts.perf_author_prompt(config, state.task, state.task_name, trees, note, feedback))
        discard_edits(config, keep=note, writes=judge.writes)
        stage_writes(config, judge.writes)
        record_staged(config, f"{note.stem} benches", judge.name, agent_label(state))
        restore_files(config, drop_ignored_since(config, before))
        after = {bench: perf_hygiene.fingerprint(config.root, bench) for bench in perf_review.bench_scripts(config)}
        if after == benches:
            print(f"   {note.stem}: benches unchanged")
            return feedback
        print(f"   {note.stem}: benches changed; stale samples dropped, re-authoring")
        feedback = (feedback + "\n\n" if feedback else "") + "Benches changed in the previous authoring round; samples taken before the change were dropped."
    return feedback


def record_staged(config: Config, message: str, role: str, label: str) -> None:
    _, staged = run(["git", "diff", "--cached", "--name-only"], cwd=config.root)
    if staged.strip():
        run(["git", "commit", "-q", "-m", stamped(f"{message}\n\nBy {role}.", label)], cwd=config.root)


def fill_samples(state: Run, session: perf_trees.Session) -> None:
    config = state.config
    benches = perf_review.bench_scripts(config)
    if not benches:
        return
    policy = perf_settings.policy(config)
    stamps = {bench: perf_hygiene.fingerprint(config.root, bench) for bench in benches}
    for bench, stamp in stamps.items():
        dropped = perf_samples.drop_stale(config, bench, stamp)
        if dropped:
            print(f"   dropped {dropped} stale {bench} samples")
    records = perf_review.load_records(config)
    for tree in session.trees:
        for bench in benches:
            have = {
                record["sample"]
                for record in records
                if record["script"] == bench and record["tree"] == tree.name and record.get("fingerprint") == stamps[bench]
            }
            missing = policy.min_runs - len(have)
            if missing <= 0:
                continue
            database = None
            if any(record.get("db") for record in records if record["script"] == bench):
                database, problem = perf_db.for_run(config)
                if database is None:
                    print(f"   fill_samples: {problem}")
                    continue
            print(f"   filling {bench} on {tree.name}: {missing} sample(s)")
            start = max(have, default=0)
            for number in range(start + 1, start + 1 + missing):
                problem = perf_samples.take_sample(config, bench, tree, number, (database, stamps[bench]))
                if problem:
                    print(f"   fill_samples: {problem}")
                    break


def review_measurements(state: Run, session: perf_trees.Session, report: Path, verdict: str, text: str) -> str:
    outcome = perf_review.review(state.config, session, report, verdict)
    if outcome.problems:
        return "\n".join(f"- {problem}" for problem in outcome.problems)
    state.perf_changes = perf_review.changes_summary(outcome, text, session)
    if verdict == PASS:
        perf_review.record_table(state.config, session, outcome)
    return ""


def gate_for(state: Run, tier: str | None) -> tuple[str, bool]:
    if tier is None:
        return "", True
    results = state.gates(tier)
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
        results = state.gates(worker.tier)
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


def discard_edits(config: Config, keep: Path, writes: tuple[str, ...] = ()) -> None:
    keep_relative = str(keep.relative_to(config.root))
    stray = [
        path
        for path in changed_paths(config, ["git", "status", "--porcelain", "--untracked-files=all"])
        if path != keep_relative and not freeze.matches_any(path, list(writes))
    ]
    if not stray:
        return
    print(f"   discarding edits a judge made: {', '.join(stray[:10])}")
    if writes:
        restore_paths(config, stray)
        return
    run(["git", "checkout", "--", "."], cwd=config.root)
    run(["git", "clean", "-fdq", "-e", keep_relative, "-e", ".marestail/"], cwd=config.root)


def restore_paths(config: Config, paths: list[str]) -> None:
    _, listed = run(["git", "ls-tree", "-r", "--name-only", "HEAD", "--", *paths], cwd=config.root)
    tracked = sorted(set(listed.splitlines()) & set(paths))
    untracked = sorted(set(paths) - set(tracked))
    if tracked:
        run(["git", "checkout", "HEAD", "--", *tracked], cwd=config.root)
    if untracked:
        run(["git", "rm", "-q", "--cached", "--ignore-unmatch", "--", *untracked], cwd=config.root)
    for path in untracked:
        (config.root / path).unlink(missing_ok=True)


def stage_writes(config: Config, writes: tuple[str, ...]) -> None:
    if not writes:
        return
    changed = changed_paths(config, ["git", "status", "--porcelain", "--untracked-files=all"])
    kept = [path for path in changed if freeze.matches_any(path, list(writes))]
    if kept:
        run(["git", "add", "-A", "--", *kept], cwd=config.root)


def newly_tracked_ignored(config: Config, before: str) -> list[str]:
    _, then = run(["git", "ls-tree", "-r", "--name-only", before], cwd=config.root)
    _, now = run(["git", "ls-files"], cwd=config.root)
    previous = set(then.splitlines())
    added = [path for path in now.splitlines() if path and path not in previous]
    return [path for path in added if ignored_path(config, path)]


def ignored_path(config: Config, path: str) -> bool:
    code, _ = run(["git", "check-ignore", "-q", "--no-index", "--", path], cwd=config.root)
    return code == 0


def drop_ignored_since(config: Config, before: str) -> dict[str, bytes]:
    paths = newly_tracked_ignored(config, before)
    saved = {}
    for path in paths:
        file = config.root / path
        if file.is_file():
            saved[path] = file.read_bytes()
    if not paths:
        return saved
    print(f"   dropping gitignored files: {', '.join(paths[:10])}")
    run(["git", "rm", "-q", "--cached", "--ignore-unmatch", "--", *paths], cwd=config.root)
    if head(config) != before:
        run(["git", "commit", "--amend", "--allow-empty", "-q", "--no-edit"], cwd=config.root)
    return saved


def restore_files(config: Config, saved: dict[str, bytes]) -> None:
    for path, data in saved.items():
        dest = config.root / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)


def fold_handoff(config: Config, role: str, report: Path, before: str, label: str, used: set[str] | None = None) -> None:
    body = report.read_text().strip()
    if head(config) == before:
        record_commit(config, f"{role} handoff", body, role, label)
        return
    stamp_history(config, before, label, used)
    _, original = run(["git", "log", "-1", "--format=%B"], cwd=config.root)
    message = stamped(strip_byline(original, role), label, used) + f"\n\n{body}\n\nBy {role}."
    run(["git", "commit", "--amend", "--allow-empty", "-q", "-m", message], cwd=config.root)


def strip_byline(message: str, role: str) -> str:
    lines = message.rstrip().splitlines()
    if lines and lines[-1].strip() == f"By {role}.":
        lines = lines[:-1]
    return "\n".join(lines).rstrip()


def record_commit(config: Config, subject: str, body: str, role: str, label: str) -> None:
    message = stamped(f"{subject}\n\n{body.strip()}\n\nBy {role}.", label)
    run(["git", "commit", "--allow-empty", "-q", "-m", message], cwd=config.root)


def stamped(message: str, label: str, used: set[str] | None = None) -> str:
    if not label or any(message.startswith(f"[{known}]") for known in {label, *(used or set())}):
        return message
    return f"[{label}] {message}"


def stamp_history(config: Config, before: str, label: str, used: set[str] | None = None) -> None:
    commits = rev_list(config, before, ["--no-merges"])
    if not label or not commits or commits != rev_list(config, before, []):
        return
    parent = before
    for sha in commits:
        parent = restamp(config, sha, parent, label, used)
    run(["git", "reset", "--hard", "-q", parent], cwd=config.root)


def rev_list(config: Config, before: str, options: list[str]) -> list[str]:
    _, output = run(["git", "rev-list", "--reverse", *options, f"{before}..HEAD"], cwd=config.root)
    return output.split()


def restamp(config: Config, sha: str, parent: str, label: str, used: set[str] | None = None) -> str:
    _, details = run(["git", "log", "-1", "--format=%an%n%ae%n%aI%n%B", sha], cwd=config.root)
    name, email, date, message = details.split("\n", 3)
    author = {"GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email, "GIT_AUTHOR_DATE": date}
    tree = f"{sha}^{{tree}}"
    _, created = run(["git", "commit-tree", tree, "-p", parent, "-m", stamped(message.strip(), label, used)], cwd=config.root, env=author)
    return created.split()[0]


def archive_handoffs(state: Run) -> None:
    destination = state.folder / f"handoffs-{time.strftime('%Y%m%dT%H%M%S')}"
    if state.handoffs.exists():
        state.folder.mkdir(parents=True, exist_ok=True)
        shutil.move(str(state.handoffs), str(destination))
    perf_trees.archive_start(state.config, state.task_name, destination if destination.exists() else None)


def head(config: Config) -> str:
    _, output = run(["git", "rev-parse", "HEAD"], cwd=config.root)
    return output.strip()


def invoke(state: Run, label: str, prompt: str) -> None:
    state.folder.mkdir(parents=True, exist_ok=True)
    prompt_file = state.folder / f"{label}.prompt.md"
    prompt_file.write_text(prompt)
    for _ in range(LIMIT_WAITS):
        if state.route:
            prompt, unrouted = routed(state, prompt)
            if unrouted:
                print(f"   {state.route}: {unrouted}; waiting {LIMIT_WAIT_SECONDS // 60} min before retrying {label}")
                time.sleep(LIMIT_WAIT_SECONDS)
                continue
            prompt_file.write_text(prompt)
        backend = resolve_agent(state)
        started = time.time()
        code, output = run_backend(state, backend, prompt, prompt_file)
        (state.folder / f"{label}.json").write_text(output)
        if backend == "grok" and grok_always_approve_locked(code, output):
            print(f"   {label}: grok always-approve is locked; cannot run unattended")
            return
        limited, describe = outcome_readers(backend)
        if not limited(code, output):
            print(f"   {label} finished in {(time.time() - started) / 60:.1f} min: {describe(output)}")
            return
        print(f"   rate limited; waiting {LIMIT_WAIT_SECONDS // 60} min before retrying {label}")
        time.sleep(LIMIT_WAIT_SECONDS)
    print(f"   {label}: still rate limited after {LIMIT_WAITS} waits")


def routed(state: Run, prompt: str) -> tuple[str, str]:
    before = agent_label(state)
    choice, unrouted = dandelion.choose(str(state.route), state.config.root)
    if choice is None:
        return prompt, unrouted
    state.agent, state.model, state.effort, state.account_env = choice.backend, choice.model, choice.effort, choice.env
    after = agent_label(state)
    state.labels.add(after)
    print(f"   {state.route}: {choice.line}")
    return prompt.replace(f"[{before}] ", f"[{after}] "), ""


def run_backend(state: Run, backend: str, prompt: str, prompt_file: Path) -> tuple[int, str]:
    if backend == "grok":
        return grok_run(state, prompt_file)
    if backend == "kilo":
        return kilo_run(state, prompt)
    if backend == "kimi":
        return kimi_run(state, prompt)
    return run(agent_command(state), cwd=state.config.root, stdin=prompt, timeout=4 * 3600, env=agent_env(state))


def outcome_readers(backend: str):
    readers = {
        "grok": (grok_rate_limited, grok_summary),
        "kilo": (kilo_rate_limited, kilo_summary),
        "kimi": (kimi_rate_limited, kimi_summary),
    }
    return readers.get(backend, (rate_limited, summary))


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
    if state.route:
        return state.route
    backend = resolve_agent(state)
    return KILO_DEFAULT_MODEL if backend == "kilo" else backend


def effort_name(state: Run) -> str:
    if state.route and not state.agent:
        return ""
    backend = resolve_agent(state)
    if backend == "kilo":
        return kilo_variant(state) or ""
    if backend == "grok":
        return grok_effort(state) or ""
    return state.effort or ""


def agent_env(state: Run) -> dict[str, str]:
    if resolve_agent(state) == "claude":
        return {"CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS": "0", **state.account_env}
    return dict(state.account_env)


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
