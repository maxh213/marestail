import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from marestail import audit, freeze, practices, prompts
from marestail import config as config_module
from marestail import route as dandelion
from marestail.config import Config
from marestail.context import hook_focus, resolve_focus
from marestail.gates import run_gates
from marestail.perf import db as perf_db
from marestail.perf import hygiene as perf_hygiene
from marestail.perf import review as perf_review
from marestail.perf import samples as perf_samples
from marestail.perf import settings as perf_settings
from marestail.perf import trees as perf_trees
from marestail.pipeline import Judge, Step, Worker, find, names, window
from marestail.report import Result, elapsed, render
from marestail.shell import clean, run

PASS = "PASS"
BOUNCE = "BOUNCE"
AUTHOR = "AUTHOR"
PERF = "perf"
CLAUDE = "claude"
CONFIG_CHANGE = "## Config change"
LIMIT_PATTERN = re.compile(
    r"rate.?limit|usage limit|session limit|resets \d|overloaded|capacity|too many requests|\b529\b|quota", re.IGNORECASE
)
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
WORKER_REPEAT_LIMIT = 3
KILO_DEFAULT_MODEL = "kilo/stepfun/step-3.7-flash:free"
KILO_DEFAULT_VARIANT = "high"
AGENT_TIMEOUT = 4 * 3600
AUTHOR_ROUNDS = 3
SUMMARY_WIDTH = 120
MODEL_FLAG = "--model"
EFFORT_FLAG = "--effort"
OUTPUT_FORMAT = "--output-format"
JSON_FORMAT = "json"
ALLOW_EMPTY = "--allow-empty"
UNMATCHED = "--ignore-unmatch"
COMMIT = "commit"
ERROR = "error"
CHANGED = "changed"
TOTAL_COST = "total_cost_usd"
NUM_TURNS = "num_turns"
TOTAL_TOKENS = "total_tokens"
USAGE = "usage"
CACHED = "--cached"
NAME_ONLY = "--name-only"
CHECKOUT = "checkout"
TOKENS = "tokens"
CONTENT = "content"
COST_USD = "costUSD"
RESULT = "result"
MESSAGE = "message"
STATUS_COMMAND = ["git", "status", "--porcelain", "--untracked-files=all"]
INPUT_OUTPUT_TOKENS = ("inputTokens", "outputTokens")
KIMI_TOKENS = ("input_tokens", "output_tokens")
AUTHOR_AGAIN = "You asked for an authoring round; benches are editable again in the authoring phase."
AUTHOR_DONE = "No authoring rounds left; benches stay frozen. Write PASS or BOUNCE with the benches as they are."
BENCHES_CHANGED = "Benches changed in the previous authoring round; samples taken before the change were dropped."

Verdict = tuple[str, str | None, str]
Event = dict[str, Any]
Spawned = subprocess.CompletedProcess[str] | tuple[int, str]


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
        scope = "hard" if self.hard else CHANGED
        return f" --scope {scope}" + "".join(f" --focus {path}" for path in sorted(self.focus))

    @property
    def hard_focus(self) -> set[str] | None:
        return self.focus if self.hard else None

    def gates(self, tier: str) -> list[Result]:
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


@dataclass
class JudgeProgress:
    feedback: str = ""
    author_left: int = AUTHOR_ROUNDS

    def author_requested(self) -> None:
        if self.author_left > 0:
            self.author_left -= 1
            self.feedback = AUTHOR_AGAIN
        else:
            self.feedback = AUTHOR_DONE


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
    picked = pick_model(config, model, agent, effort)
    scoped = pick_scope(config, scope, focus)
    share_scope(*scoped)
    state = make_run(config, task, retries, agent, picked, scoped)
    perf_trees.record_start(config, state.task_name)
    outcome = run_steps(state, window(start, stop), auto)
    print(proposals_summary(state))
    if state.perf_changes:
        print(state.perf_changes)
    return outcome


def make_run(
    config: Config,
    task: Path,
    retries: int,
    agent: str | None,
    picked: tuple[str | None, str | None, str | None],
    scoped: tuple[bool, bool, set[str]],
) -> Run:
    model, route, effort = picked
    scope_changed, hard, focused = scoped
    return Run(
        config=config,
        task=task.resolve(),
        model=model,
        retries=retries,
        agent=agent,
        effort=effort,
        scope_changed=scope_changed,
        focus=focused,
        hard=hard,
        route=route,
    )


def configured(config: Config, key: str, value: str | None) -> Any:
    return config.get("agent", key) if value is None else value


def pick_model(config: Config, model: str | None, agent: str | None, effort: str | None) -> tuple[str | None, str | None, str | None]:
    model = configured(config, "model", model)
    if not dandelion.is_routed(model):
        return model, None, configured(config, "effort", effort)
    if agent or effort:
        raise SystemExit(f"--model {model} picks the backend and effort before every session; drop --agent and --effort")
    dandelion.require()
    return None, model, effort


def focus_list(focus: list[str] | None) -> list[str]:
    return [path for path in focus or [] if path.strip()]


def pick_scope(config: Config, scope: str | None, focus: list[str] | None) -> tuple[bool, bool, set[str]]:
    paths = focus_list(focus)
    if scope == "all" and paths:
        raise SystemExit("--focus cannot be combined with --scope all")
    scope_changed = scope in (CHANGED, "hard") or bool(paths)
    return scope_changed, scope == "hard", scoped_focus(config, scope_changed, scope == "hard", paths)


def scoped_focus(config: Config, scope_changed: bool, hard: bool, paths: list[str]) -> set[str]:
    if not scope_changed:
        return set()
    focused = resolve_focus(config, set(paths)) | hook_focus(config)
    if hard and not focused:
        raise SystemExit("--scope hard needs at least one focus path: pass --focus or set [focus] paths in marestail.toml")
    return focused


def run_steps(state: Run, steps: list[Step], auto: bool) -> int:
    for step in steps:
        if not run_step(state, step):
            print(f"pipeline stopped at {step.name}")
            return 1
        if paused(state, step, auto):
            return 1
    print("pipeline complete")
    archive_handoffs(state)
    return 0


def paused(state: Run, step: Step, auto: bool) -> bool:
    return step.pause_after and not auto and not approve(state)


def share_scope(scope_changed: bool, hard: bool, focus: set[str]) -> None:
    if not scope_changed:
        return
    os.environ["MARESTAIL_SCOPE"] = "hard" if hard else CHANGED
    os.environ["MARESTAIL_FOCUS"] = os.pathsep.join(sorted(focus))


def run_step(state: Run, step: Step) -> bool:
    if isinstance(step, Worker):
        return run_worker(state, step, "")
    reason = skip_reason(state, step)
    if reason:
        print(reason)
        return True
    return run_judge_loop(state, step)


def skip_reason(state: Run, judge: Judge) -> str:
    if disabled(state, judge):
        return f"{judge.name} disabled in marestail.toml; skipping"
    if without_guidance(state, judge):
        return "practices: no guidance files; skipping"
    return ""


def disabled(state: Run, judge: Judge) -> bool:
    return judge.optional and state.config.get(judge.name, "enabled", True) is False


def without_guidance(state: Run, judge: Judge) -> bool:
    return judge.name == "practices" and not practices.files(state.config.root)


MAX_JUDGE_ROUNDS = 1000


def run_judge_loop(state: Run, judge: Judge) -> bool:
    previous = ""
    for bounce in range(MAX_JUDGE_ROUNDS):
        done, previous = judge_round(state, judge, previous, bounce)
        if done is not None:
            return done
    return False


def judge_round(state: Run, judge: Judge, previous: str, bounce: int) -> tuple[bool | None, str]:
    verdict, target, report = run_judge(state, judge)
    if verdict == PASS:
        return True, report
    stop = stall_reason(judge, previous, report, bounce)
    if stop:
        print(stop)
        return False, report
    return (None if rework(state, judge, target, report) else False), report


def stall_reason(judge: Judge, previous: str, report: str, bounce: int) -> str:
    if same_findings(previous, report):
        return f"{judge.name} repeated the same findings twice; the worker is not making progress, stopping for a human"
    if judge.bounces and bounce >= judge.bounces:
        return f"{judge.name} still bouncing after {judge.bounces} rounds; stopping for a human"
    return ""


def rework(state: Run, judge: Judge, target: str | None, report: str) -> bool:
    worker = find(target or judge.bounce_to)
    return isinstance(worker, Worker) and run_worker(state, worker, report)


def same_findings(previous: str, current: str) -> bool:
    return bool(previous) and normalise_findings(previous) == normalise_findings(current)


def normalise_findings(report: str) -> list[str]:
    lines = [re.sub(r"\s+", " ", line.strip()) for line in report.splitlines()]
    return [line for line in lines if re.match(r"^\d+\.", line)]


def attempts(retries: int) -> Iterator[int]:
    n = 1
    while retries <= 0 or n <= retries:
        yield n
        n += 1


def run_worker(state: Run, worker: Worker, feedback: str) -> bool:
    before = head(state.config)
    previous, repeats = "", 0
    for attempt in attempts(state.retries):
        problems = worker_attempt(state, worker, feedback, attempt, before)
        if not problems:
            return True
        print(problems)
        feedback, repeats, previous = problems, repeat_count(previous, problems, repeats), problems
        if repeats >= WORKER_REPEAT_LIMIT:
            print(
                f"{worker.name} got the same problems back {WORKER_REPEAT_LIMIT} times in a row; the worker is not making progress, stopping for a human"
            )
            return False
    return False


def worker_attempt(state: Run, worker: Worker, feedback: str, attempt: int, before: str) -> str:
    report = state.next_report(worker.name)
    print(f"== {worker.name} ({report.stem}) attempt {attempt}")
    prompt = prompts.worker_prompt(
        state.config, worker, state.task, state.task_name, report, feedback, agent_label(state), state.gate_flags, state.hard_focus
    )
    invoke(state, report.stem, prompt)
    problems = verify_worker(state, worker, report, before)
    if not problems:
        saved = drop_ignored_since(state.config, before)
        fold_handoff(state.config, worker.name, report, before, agent_label(state), state.labels)
        restore_files(state.config, saved)
    return problems


def repeat_count(previous: str, problems: str, repeats: int) -> int:
    return repeats + 1 if problem_shape(previous) == problem_shape(problems) else 1


def problem_shape(problems: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\d+(?:\.\d+)?", "#", problems)).strip()


def run_judge(state: Run, judge: Judge) -> Verdict:
    gate = gate_for(state, judge.tier)
    progress = JudgeProgress()
    for attempt in attempts(state.retries):
        outcome = judged(state, judge, gate, progress, attempt)
        if outcome is not None:
            return outcome
    shown = "unlimited" if state.retries <= 0 else str(state.retries)
    return BOUNCE, None, f"{judge.name} produced no verdict after {shown} attempts"


def judged(state: Run, judge: Judge, gate: tuple[str, bool], progress: JudgeProgress, attempt: int) -> Verdict | None:
    report = state.next_report(judge.name)
    print(f"== {judge.name} ({report.stem}) attempt {attempt}")
    with measuring(state, judge) as session:
        prepare_perf(state, judge, gate[1], session, progress)
        outcome, progress.feedback = judge_attempt(state, judge, report, gate, session, progress.feedback)
    if outcome is not None and outcome[0] == AUTHOR:
        progress.author_requested()
        return None
    return outcome


def prepare_perf(state: Run, judge: Judge, gate_ok: bool, session: perf_trees.Session | None, progress: JudgeProgress) -> None:
    if not gate_ok or session is None:
        return
    if progress.author_left > 0:
        progress.feedback = author_phase(state, judge, session, progress.feedback)
    fill_samples(state, session)


@contextlib.contextmanager
def measuring(state: Run, judge: Judge) -> Iterator[perf_trees.Session | None]:
    if judge.name != PERF:
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
) -> tuple[Verdict | None, str]:
    before = judge_session(state, judge, report, gate[0], session, feedback)
    blob = session_output(state, report)
    if judge.name == PERF and asked_to_author(report, blob):
        return (AUTHOR, None, ""), feedback
    parsed = parse_verdict(report, blob)
    if parsed is None:
        print(f"{judge.name} wrote no verdict; retrying")
        return None, no_verdict_feedback(report)
    write_missing_verdict(report, parsed)
    return settle_verdict(state, judge, report, gated_verdict(judge, report, gate, parsed), session, before)


def judge_session(state: Run, judge: Judge, report: Path, gate_report: str, session: perf_trees.Session | None, feedback: str) -> str:
    trees = perf_trees.prompt_section(state.config, session) if session else ""
    prompt = prompts.judge_prompt(state.config, judge, state.task, state.task_name, report, gate_report, trees, feedback, state.hard_focus)
    before = head(state.config)
    invoke(state, report.stem, prompt)
    discard_edits(state.config, keep=report, writes=judge_writes(judge))
    return before


def judge_writes(judge: Judge) -> tuple[str, ...]:
    return () if judge.name == PERF else judge.writes


def read_or_empty(path: Path) -> str:
    return path.read_text() if path.exists() else ""


def session_output(state: Run, report: Path) -> str:
    return read_or_empty(state.folder / f"{report.stem}.json")


def asked_to_author(report: Path, blob: str) -> bool:
    combined = read_or_empty(report) + "\n" + blob
    return bool(re.search(r"^\s*VERDICT:\s*AUTHOR\b", combined, re.IGNORECASE | re.MULTILINE))


def with_target(prefix: str, target: str | None) -> str:
    return f"{prefix}{target}" if target else ""


def write_missing_verdict(report: Path, parsed: tuple[str, str | None]) -> None:
    if report.exists():
        return
    verdict, target = parsed
    report.write_text(f"VERDICT: {verdict}" + with_target(" ", target) + "\n")


def gated_verdict(judge: Judge, report: Path, gate: tuple[str, bool], parsed: tuple[str, str | None]) -> Verdict:
    verdict, target = parsed
    text = report.read_text()
    if not gate[1]:
        return BOUNCE, None, gate[0] + "\n\n" + text
    return verdict, None if judge.pinned_bounce else target, text


def settle_verdict(
    state: Run, judge: Judge, report: Path, outcome: Verdict, session: perf_trees.Session | None, before: str
) -> tuple[Verdict | None, str]:
    problems = measured_problems(state, session, report, outcome)
    if problems:
        print(f"   {judge.name} verdict rejected; retrying")
        return None, problems
    commit_verdict(state, judge, before, outcome)
    return outcome, ""


def measured_problems(state: Run, session: perf_trees.Session | None, report: Path, outcome: Verdict) -> str:
    if session is None:
        return ""
    verdict, _, text = outcome
    problems = review_measurements(state, session, report, verdict, text)
    if not problems:
        drop_scratch(state)
    return problems


def drop_scratch(state: Run) -> None:
    for path in perf_hygiene.discard_scratch(state.config.root):
        print(f"   removed perf scratch {path}")


def commit_verdict(state: Run, judge: Judge, before: str, outcome: Verdict) -> None:
    verdict, target, text = outcome
    stage_writes(state.config, judge_writes(judge))
    saved = drop_ignored_since(state.config, before)
    record_commit(state.config, f"{judge.name} verdict: {verdict}" + with_target(" to ", target), text, judge.name, agent_label(state))
    restore_files(state.config, saved)
    print(f"   verdict {verdict}" + with_target(" to ", target))


def no_verdict_feedback(report: Path) -> str:
    return (
        f"Your session produced no verdict I could find: no file at {report} and no line starting `VERDICT:` in your output. "
        "Write the verdict file yourself, first line `VERDICT: PASS` or `VERDICT: BOUNCE`, "
        "or print the `VERDICT:` line in your final output."
    )


def author_phase(state: Run, judge: Judge, session: perf_trees.Session, feedback: str) -> str:
    for round_no in range(1, AUTHOR_ROUNDS + 1):
        note = state.folder / f"perf-author-{round_no}.md"
        if not author_round(state, judge, session, note, feedback):
            print(f"   {note.stem}: benches unchanged")
            return feedback
        print(f"   {note.stem}: benches changed; stale samples dropped, re-authoring")
        feedback = (feedback + "\n\n" if feedback else "") + BENCHES_CHANGED
    return feedback


def author_round(state: Run, judge: Judge, session: perf_trees.Session, note: Path, feedback: str) -> bool:
    config = state.config
    benches = fingerprints(config, perf_review.bench_scripts(config))
    trees = perf_trees.prompt_section(config, session)
    before = head(config)
    invoke(state, note.stem, prompts.perf_author_prompt(config, state.task, state.task_name, trees, note, feedback))
    discard_edits(config, keep=note, writes=judge.writes)
    stage_writes(config, judge.writes)
    record_staged(config, f"{note.stem} benches", judge.name, agent_label(state))
    restore_files(config, drop_ignored_since(config, before))
    return fingerprints(config, perf_review.bench_scripts(config)) != benches


def fingerprints(config: Config, benches: list[str]) -> dict[str, str]:
    return {bench: perf_hygiene.fingerprint(config.root, bench) for bench in benches}


def record_staged(config: Config, message: str, role: str, label: str) -> None:
    _, staged = run(["git", "diff", CACHED, NAME_ONLY], cwd=config.root)
    if staged.strip():
        run(["git", COMMIT, "-q", "-m", stamped(f"{message}\n\nBy {role}.", label)], cwd=config.root)


def fill_samples(state: Run, session: perf_trees.Session) -> None:
    config = state.config
    benches = perf_review.bench_scripts(config)
    if not benches:
        return
    policy = perf_settings.policy(config)
    stamps = fingerprints(config, benches)
    drop_stale_samples(config, stamps)
    records = perf_review.load_records(config)
    for tree in session.trees:
        for bench in benches:
            fill_bench(config, records, tree, bench, stamps[bench], policy.min_runs)


def drop_stale_samples(config: Config, stamps: dict[str, str]) -> None:
    for bench, stamp in stamps.items():
        dropped = perf_samples.drop_stale(config, bench, stamp)
        if dropped:
            print(f"   dropped {dropped} stale {bench} samples")


def fill_bench(config: Config, records: list[Event], tree: perf_trees.Tree, bench: str, stamp: str, min_runs: int) -> None:
    have = sample_numbers(records, bench, tree.name, stamp)
    missing = min_runs - len(have)
    if missing <= 0:
        return
    database, ready = bench_database(config, records, bench)
    if not ready:
        return
    print(f"   filling {bench} on {tree.name}: {missing} sample(s)")
    start = max(have, default=0)
    take_samples(config, bench, tree, range(start + 1, start + 1 + missing), (database, stamp))


def sample_numbers(records: list[Event], bench: str, tree_name: str, stamp: str) -> set[int]:
    return {record["sample"] for record in records if same_sample(record, bench, tree_name, stamp)}


def same_sample(record: Event, bench: str, tree_name: str, stamp: str) -> bool:
    return record["script"] == bench and record["tree"] == tree_name and record.get("fingerprint") == stamp


def uses_database(records: list[Event], bench: str) -> bool:
    return any(record.get("db") for record in records if record["script"] == bench)


def bench_database(config: Config, records: list[Event], bench: str) -> tuple[perf_db.Database | None, bool]:
    if not uses_database(records, bench):
        return None, True
    database, problem = perf_db.for_run(config)
    if database is None:
        print(f"   fill_samples: {problem}")
    return database, database is not None


def take_samples(config: Config, bench: str, tree: perf_trees.Tree, numbers: range, harness: tuple[perf_db.Database | None, str]) -> None:
    for number in numbers:
        problem = perf_samples.take_sample(config, bench, tree, number, harness)
        if problem:
            print(f"   fill_samples: {problem}")
            return


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
    blob = read_or_empty(report) + "\n" + extra
    match = re.search(r"VERDICT:\s*(PASS|BOUNCE)(?:[ \t]+(\w+))?", blob, re.IGNORECASE)
    if not match:
        return None
    return match.group(1).upper(), known_target(match.group(2))


def known_target(raw: str | None) -> str | None:
    target = raw.lower() if raw else None
    return target if target in names() else None


def verify_worker(state: Run, worker: Worker, report: Path, before: str) -> str:
    problems = missing_handoff(state.config, report)
    dirty = changed_paths(state.config, STATUS_COMMAND)
    problems.extend(dirty_problems(dirty))
    problems.extend(frozen_problems(state, worker, report, before, dirty))
    problems.extend(audit_problems(state, worker, report))
    problems.extend(gate_problems(state, worker))
    return "\n\n".join(problems)


def missing_handoff(config: Config, report: Path) -> list[str]:
    return [] if report.exists() else [f"missing handoff {report.relative_to(config.root)}"]


def dirty_problems(dirty: list[str]) -> list[str]:
    return ["uncommitted changes:\n" + "\n".join(dirty[:20])] if dirty else []


def frozen_problems(state: Run, worker: Worker, report: Path, before: str, dirty: list[str]) -> list[str]:
    touched = changed_paths(state.config, ["git", "diff", NAME_ONLY, f"{before}..HEAD"]) + dirty
    frozen = frozen_changes(state.config, worker, before, touched)
    if frozen and not dirty:
        return reject_config_change(state, worker, report, before, frozen)
    return [f"{path} is frozen for {worker.name}" for path in frozen]


def frozen_changes(config: Config, worker: Worker, before: str, touched: list[str]) -> list[str]:
    return [
        path for path in freeze.frozen_paths(config, worker.name, touched) if not freeze.tolerated(path, file_diff(config, before, path))
    ]


def audit_problems(state: Run, worker: Worker, report: Path) -> list[str]:
    if worker.audit and report.exists():
        return audit.problems(state.config, state.task_name, report.read_text(), worker.name)
    return []


def gate_problems(state: Run, worker: Worker) -> list[str]:
    if not worker.tier:
        return []
    results = state.gates(worker.tier)
    return [] if all(result.ok for result in results) else [render(results)]


def file_diff(config: Config, before: str, path: str) -> str:
    _, output = run(["git", "diff", before, "--", path], cwd=config.root)
    return output


def reject_config_change(state: Run, worker: Worker, report: Path, before: str, frozen: list[str]) -> list[str]:
    config = state.config
    justification = config_change_section(report)
    label = agent_label(state)
    _, diff = run(["git", "diff", f"{before}..HEAD", "--", *frozen], cwd=config.root)
    if justification is None:
        revert(config, before, frozen, stamped(f"Revert change to frozen files by {report.stem}\n\nBy runner.", label))
        return [f"{path} is frozen for {worker.name}; reverted. Work within the current configuration." for path in frozen]
    body = f"Proposed by {report.stem}: {', '.join(frozen)}\n\n{justification}\n\n```diff\n{diff.strip()}\n```\n"
    revert(
        config,
        before,
        frozen,
        stamped(f"Revert change to frozen files by {report.stem}, recorded as a proposal\n\n{body}\nBy runner.", label),
    )
    proposal = state.next_report("proposal")
    proposal.write_text(body)
    return [
        f"{', '.join(frozen)}: frozen, reverted. Your reason was recorded as {proposal.relative_to(config.root)} "
        "for a human to consider after the run. Find a way within the current configuration."
    ]


def config_change_section(report: Path) -> str | None:
    text = read_or_empty(report)
    if CONFIG_CHANGE not in text:
        return None
    section = text.split(CONFIG_CHANGE, 1)[1]
    return section.split("\n## ", 1)[0].strip()


def revert(config: Config, before: str, paths: list[str], message: str) -> None:
    run(["git", CHECKOUT, before, "--", *paths], cwd=config.root)
    run(["git", "add", "-A", "--", *paths], cwd=config.root)
    run(["git", COMMIT, "-q", "-m", message], cwd=config.root)


def proposals_summary(state: Run) -> str:
    files = sorted(state.handoffs.glob("*-proposal.md")) if state.handoffs.exists() else []
    if not files:
        return ""
    body = "\n\n".join(f"### {f.stem}\n{f.read_text().strip()}" for f in files)
    return f"\n## Config changes the agents asked for and were refused\nDecide whether to make any of these yourself.\n\n{body}"


def porcelain_path(line: str) -> str:
    return line[3:] if line[:2].strip() and line[2:3] == " " else line


def outside_work(path: str) -> bool:
    return bool(path.strip()) and not path.strip().startswith(".marestail/")


def changed_paths(config: Config, command: list[str]) -> list[str]:
    _, output = run(command, cwd=config.root)
    paths = [porcelain_path(line) for line in output.splitlines()]
    return [path.split(" -> ")[-1].strip() for path in paths if outside_work(path)]


def stray_edits(config: Config, keep_relative: str, writes: tuple[str, ...]) -> list[str]:
    return [path for path in changed_paths(config, STATUS_COMMAND) if path != keep_relative and not freeze.matches_any(path, list(writes))]


def discard_edits(config: Config, keep: Path, writes: tuple[str, ...] = ()) -> None:
    keep_relative = str(keep.relative_to(config.root))
    stray = stray_edits(config, keep_relative, writes)
    if not stray:
        return
    print(f"   discarding edits a judge made: {', '.join(stray[:10])}")
    if writes:
        restore_paths(config, stray)
        return
    run(["git", CHECKOUT, "--", "."], cwd=config.root)
    run(["git", "clean", "-fdq", "-e", keep_relative, "-e", ".marestail/"], cwd=config.root)


def restore_paths(config: Config, paths: list[str]) -> None:
    _, listed = run(["git", "ls-tree", "-r", NAME_ONLY, "HEAD", "--", *paths], cwd=config.root)
    tracked = sorted(set(listed.splitlines()) & set(paths))
    untracked = sorted(set(paths) - set(tracked))
    if tracked:
        run(["git", CHECKOUT, "HEAD", "--", *tracked], cwd=config.root)
    if untracked:
        run(["git", "rm", "-q", CACHED, UNMATCHED, "--", *untracked], cwd=config.root)
    for path in untracked:
        (config.root / path).unlink(missing_ok=True)


def written_paths(config: Config, writes: tuple[str, ...]) -> list[str]:
    return [path for path in changed_paths(config, STATUS_COMMAND) if freeze.matches_any(path, list(writes))]


def stage_writes(config: Config, writes: tuple[str, ...]) -> None:
    if not writes:
        return
    kept = written_paths(config, writes)
    if kept:
        run(["git", "add", "-A", "--", *kept], cwd=config.root)


def added_paths(then: str, now: str) -> list[str]:
    previous = set(then.splitlines())
    return [path for path in now.splitlines() if path and path not in previous]


def newly_tracked_ignored(config: Config, before: str) -> list[str]:
    _, then = run(["git", "ls-tree", "-r", NAME_ONLY, before], cwd=config.root)
    _, now = run(["git", "ls-files"], cwd=config.root)
    return [path for path in added_paths(then, now) if ignored_path(config, path)]


def ignored_path(config: Config, path: str) -> bool:
    code, _ = run(["git", "check-ignore", "-q", "--no-index", "--", path], cwd=config.root)
    return code == 0


def saved_contents(config: Config, paths: list[str]) -> dict[str, bytes]:
    return {path: (config.root / path).read_bytes() for path in paths if (config.root / path).is_file()}


def drop_ignored_since(config: Config, before: str) -> dict[str, bytes]:
    paths = newly_tracked_ignored(config, before)
    saved = saved_contents(config, paths)
    if paths:
        untrack_ignored(config, before, paths)
    return saved


def untrack_ignored(config: Config, before: str, paths: list[str]) -> None:
    print(f"   dropping gitignored files: {', '.join(paths[:10])}")
    run(["git", "rm", "-q", CACHED, UNMATCHED, "--", *paths], cwd=config.root)
    if head(config) != before:
        run(["git", COMMIT, "--amend", ALLOW_EMPTY, "-q", "--no-edit"], cwd=config.root)


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
    run(["git", COMMIT, "--amend", ALLOW_EMPTY, "-q", "-m", message], cwd=config.root)


def strip_byline(message: str, role: str) -> str:
    lines = message.rstrip().splitlines()
    if lines and lines[-1].strip() == f"By {role}.":
        lines = lines[:-1]
    return "\n".join(lines).rstrip()


def record_commit(config: Config, subject: str, body: str, role: str, label: str) -> None:
    message = stamped(f"{subject}\n\n{body.strip()}\n\nBy {role}.", label)
    run(["git", COMMIT, ALLOW_EMPTY, "-q", "-m", message], cwd=config.root)


def stamped(message: str, label: str, used: set[str] | None = None) -> str:
    if not label or stamped_already(message, {label, *(used or set())}):
        return message
    return f"[{label}] {message}"


def stamped_already(message: str, labels: set[str]) -> bool:
    return any(message.startswith(f"[{known}]") for known in labels)


def stamp_history(config: Config, before: str, label: str, used: set[str] | None = None) -> None:
    commits = rev_list(config, before, ["--no-merges"])
    if not restampable(config, before, label, commits):
        return
    parent = before
    for sha in commits:
        parent = restamp(config, sha, parent, label, used)
    run(["git", "reset", "--hard", "-q", parent], cwd=config.root)


def restampable(config: Config, before: str, label: str, commits: list[str]) -> bool:
    return bool(label) and bool(commits) and commits == rev_list(config, before, [])


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
        prompt, finished = invoke_once(state, label, prompt, prompt_file)
        if finished:
            return
    print(f"   {label}: still rate limited after {LIMIT_WAITS} waits")


def invoke_once(state: Run, label: str, prompt: str, prompt_file: Path) -> tuple[str, bool]:
    if state.route:
        prompt, unrouted = routed(state, prompt)
        if unrouted:
            wait(f"   {state.route}: {unrouted}; waiting {LIMIT_WAIT_SECONDS // 60} min before retrying {label}")
            return prompt, False
        prompt_file.write_text(prompt)
    return prompt, run_session(state, label, prompt, prompt_file)


def wait(message: str) -> None:
    print(message)
    time.sleep(LIMIT_WAIT_SECONDS)


def run_session(state: Run, label: str, prompt: str, prompt_file: Path) -> bool:
    backend = resolve_agent(state)
    started = time.time()
    code, output = run_backend(state, backend, prompt, prompt_file)
    (state.folder / f"{label}.json").write_text(output)
    if backend == "grok" and grok_always_approve_locked(code, output):
        print(f"   {label}: grok always-approve is locked; cannot run unattended")
        return True
    limited, describe = outcome_readers(backend)
    if not limited(code, output):
        print(f"   {label} finished in {(elapsed(started)) / 60:.1f} min: {describe(output)}")
        return True
    wait(f"   rate limited; waiting {LIMIT_WAIT_SECONDS // 60} min before retrying {label}")
    return False


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
        return kimi_run(state, prompt_file)
    return run(agent_command(state), cwd=state.config.root, stdin=prompt, timeout=AGENT_TIMEOUT, env=agent_env(state))


def outcome_readers(backend: str) -> tuple[Callable[[int, str], bool], Callable[[str], str]]:
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
    configured_backend = state.config.get("agent", "backend")
    if configured_backend:
        return str(configured_backend).lower()
    return CLAUDE


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
    return backend_effort(state) or ""


def backend_effort(state: Run) -> str | None:
    backend = resolve_agent(state)
    if backend == "kilo":
        return kilo_variant(state)
    if backend == "grok":
        return grok_effort(state)
    return state.effort


def agent_env(state: Run) -> dict[str, str]:
    if resolve_agent(state) == CLAUDE:
        return {"CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS": "0", **state.account_env}
    return dict(state.account_env)


def optional_flag(flag: str, value: str | None) -> list[str]:
    return [flag, value] if value else []


def agent_command(state: Run) -> list[str]:
    builders = {"agy": agy_command, "cursor": cursor_command, "kilo": kilo_command}
    return builders.get(resolve_agent(state), claude_command)(state)


def agy_command(state: Run) -> list[str]:
    binary = os.environ.get("MARESTAIL_AGY", "agy")
    command = [binary, "--dangerously-skip-permissions", OUTPUT_FORMAT, JSON_FORMAT, "--print-timeout", "4h"]
    return command + optional_flag(MODEL_FLAG, state.model) + optional_flag(EFFORT_FLAG, state.effort)


def cursor_command(state: Run) -> list[str]:
    binary = os.environ.get("MARESTAIL_CURSOR", "cursor-agent")
    command = [binary, "-p", OUTPUT_FORMAT, JSON_FORMAT, "--force", "--trust", "--sandbox", "disabled"]
    return command + optional_flag(MODEL_FLAG, state.model)


def claude_command(state: Run) -> list[str]:
    command = [
        os.environ.get("MARESTAIL_CLAUDE", CLAUDE),
        "-p",
        "--permission-mode",
        "bypassPermissions",
        "--dangerously-skip-permissions",
        OUTPUT_FORMAT,
        JSON_FORMAT,
    ]
    return command + optional_flag(MODEL_FLAG, state.model) + optional_flag(EFFORT_FLAG, state.effort)


def spawn(command: list[str], state: Run, env: Mapping[str, str], stdin: str, shown: str) -> Spawned:
    try:
        return subprocess.run(
            command,
            cwd=state.config.root,
            env=env,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=AGENT_TIMEOUT,
            check=False,
        )
    except FileNotFoundError as error:
        return 127, f"{command[0]}: not found ({error})"
    except subprocess.TimeoutExpired:
        return 124, f"{shown}: timed out after {AGENT_TIMEOUT}s"


def session_result(spawned: Spawned, text_of: Callable[[subprocess.CompletedProcess[str]], str]) -> tuple[int, str]:
    if isinstance(spawned, tuple):
        return spawned
    return spawned.returncode, clean(text_of(spawned))


def stdout_or_stderr(completed: subprocess.CompletedProcess[str]) -> str:
    return completed.stderr if completed.returncode != 0 and not (completed.stdout or "").strip() else completed.stdout


def stdout_and_stderr(completed: subprocess.CompletedProcess[str]) -> str:
    return (completed.stdout + "\n" + completed.stderr).strip() if completed.returncode != 0 else completed.stdout


def grok_run(state: Run, prompt_file: Path) -> tuple[int, str]:
    command = grok_command(state, prompt_file)
    return session_result(spawn(command, state, {**os.environ, **GROK_ENV}, "", " ".join(command)), stdout_or_stderr)


def grok_command(state: Run, prompt_file: Path) -> list[str]:
    command = [
        os.environ.get("MARESTAIL_GROK", "grok"),
        "--prompt-file",
        str(prompt_file.resolve()),
        OUTPUT_FORMAT,
        JSON_FORMAT,
        "--always-approve",
        "--no-plan",
        "--trust",
    ]
    return command + optional_flag(MODEL_FLAG, state.model) + optional_flag("--reasoning-effort", grok_effort(state))


def grok_effort(state: Run) -> str | None:
    return state.effort or os.environ.get("MARESTAIL_GROK_EFFORT")


def kilo_command(state: Run) -> list[str]:
    model = state.model or KILO_DEFAULT_MODEL
    command = [
        os.environ.get("MARESTAIL_KILO", "kilo"),
        "run",
        "--auto",
        "--format",
        JSON_FORMAT,
        "--log-level",
        "ERROR",
        MODEL_FLAG,
        model,
    ]
    return command + optional_flag("--variant", kilo_variant(state))


def kilo_variant(state: Run) -> str | None:
    variant = state.effort if state.effort is not None else os.environ.get("MARESTAIL_KILO_VARIANT")
    if variant == "":
        return None
    return default_variant(state) if variant is None else variant


def default_variant(state: Run) -> str | None:
    return KILO_DEFAULT_VARIANT if (state.model or KILO_DEFAULT_MODEL) == KILO_DEFAULT_MODEL else None


def kilo_run(state: Run, prompt: str) -> tuple[int, str]:
    command = kilo_command(state)
    return session_result(spawn(command, state, os.environ, prompt, " ".join(command)), stdout_or_stderr)


def json_object(text: str) -> Event | None:
    start = text.find("{")
    if start < 0:
        return None
    try:
        data, _ = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def json_events(output: str) -> list[Event]:
    events = [json_object(line.strip()) for line in output.splitlines()]
    return [event for event in events if event is not None]


kilo_events = json_events
kimi_events = json_events


def event_dict(event: Event, key: str) -> Event:
    value = event.get(key)
    return value if isinstance(value, dict) else {}


def event_error(event: Event, fallback: object) -> str:
    err = event.get(ERROR)
    return str(err.get(MESSAGE) if isinstance(err, dict) else err or fallback)


def output_tail(output: str) -> str:
    return output[-200:].replace("\n", " ")


def last_text(texts: list[str]) -> str:
    return texts[-1] if texts else ""


def limited_output(code: int, output: str, pattern: re.Pattern[str] = LIMIT_PATTERN) -> bool:
    return code != 0 and bool(pattern.search(output))


def cost_bit(cost: Any) -> str:
    try:
        return f"api-equivalent=${float(cost):.2f}"
    except (TypeError, ValueError):
        return f"cost={cost}"


def summary_line(counts: dict[str, Any], cost: Any, text: str) -> str:
    bits = [f"{name}={value}" for name, value in counts.items() if value is not None]
    if cost is not None:
        bits.append(cost_bit(cost))
    bits.append(repr(text))
    return " ".join(bits)


def kilo_rate_limited(code: int, output: str) -> bool:
    events = kilo_events(output)
    if not events:
        return limited_output(code, output)
    blob = " ".join(kilo_errors(events))
    return bool(blob) and bool(LIMIT_PATTERN.search(blob))


def kilo_text(event: Event) -> str:
    if event.get("type") != "text":
        return ""
    return str(event_dict(event, "part").get("text") or event.get("text") or "").strip()


def kilo_texts(events: list[Event]) -> list[str]:
    return [text for text in map(kilo_text, events) if text]


def kilo_errors(events: list[Event]) -> list[str]:
    return [event_error(event, event) for event in events if event.get("type") == ERROR]


def kilo_error(events: list[Event]) -> str:
    return last_text([event_error(event, "")[:SUMMARY_WIDTH] for event in events if event.get("type") == ERROR])


def first_value(part: Event, keys: tuple[str, str], fallback: Any) -> Any:
    return next((part[key] for key in keys if part.get(key)), fallback)


def kilo_usage(events: list[Event]) -> tuple[Any, Any]:
    tokens = cost = None
    for part in (event_dict(event, "part") for event in events if event.get("type") == "step_finish"):
        tokens = first_value(part, (TOKENS, TOTAL_TOKENS), tokens)
        cost = first_value(part, ("cost", COST_USD), cost)
    return tokens, cost


def kilo_summary(output: str) -> str:
    events = kilo_events(output)
    if not events:
        return output_tail(output)
    tokens, cost = kilo_usage(events)
    text = (kilo_error(events) or last_text(kilo_texts(events)))[:SUMMARY_WIDTH]
    return summary_line({TOKENS: tokens}, cost, text)


def kimi_prompt(prompt_file: Path) -> str:
    return f"Your instructions are in {prompt_file.resolve()}. Read that whole file first, then follow it exactly."


def kimi_command(state: Run, prompt_file: Path) -> list[str]:
    command = [
        os.environ.get("MARESTAIL_KIMI", "kimi"),
        "-p",
        kimi_prompt(prompt_file),
        OUTPUT_FORMAT,
        "stream-json",
    ]
    return command + optional_flag("-m", state.model)


def kimi_run(state: Run, prompt_file: Path) -> tuple[int, str]:
    command = kimi_command(state, prompt_file)
    return session_result(spawn(command, state, os.environ, "", command[0]), stdout_and_stderr)


def is_assistant(event: Event) -> bool:
    return event.get("role") == "assistant" or event.get("type") == "assistant"


def string_texts(content: str) -> list[str]:
    return [content.strip()] if content.strip() else []


def part_texts(content: list[Any]) -> list[str]:
    return [str(part["text"]).strip() for part in content if isinstance(part, dict) and part.get("text")]


def content_texts(content: Any) -> list[str]:
    if isinstance(content, str):
        return string_texts(content)
    if isinstance(content, list):
        return part_texts(content)
    return []


def assistant_texts(event: Event) -> list[str]:
    contents = (event.get(CONTENT), event_dict(event, MESSAGE).get(CONTENT))
    return [text for content in contents for text in content_texts(content)]


def kimi_texts(events: list[Event]) -> list[str]:
    return [text for event in events if is_assistant(event) for text in assistant_texts(event)]


def error_typed(event: Event) -> bool:
    return ERROR in str(event.get("type") or "") or ERROR in str(event.get("role") or "")


def error_event_text(event: Event) -> str | None:
    if error_typed(event):
        return str(event.get(MESSAGE) or event.get(CONTENT) or event)
    return None


def kimi_error(event: Event) -> str | None:
    err = event.get(ERROR)
    if isinstance(err, dict):
        return str(err.get(MESSAGE) or err)
    if err:
        return str(err)
    return error_event_text(event)


def kimi_errors(events: list[Event]) -> list[str]:
    return [error for error in map(kimi_error, events) if error is not None]


def kimi_rate_limited(code: int, output: str) -> bool:
    errors = kimi_errors(kimi_events(output))
    if errors:
        return bool(LIMIT_PATTERN.search(" ".join(errors)))
    return limited_output(code, output)


def token_count(usage: Event, key: str) -> int:
    return int(usage.get(key) or 0)


def summed_tokens(usage: Event, keys: tuple[str, str]) -> int | None:
    if not any(key in usage for key in keys):
        return None
    return sum(token_count(usage, key) for key in keys)


def kimi_tokens(usage: Event, tokens: Any) -> Any:
    found = usage.get(TOTAL_TOKENS) or tokens
    return summed_tokens(usage, KIMI_TOKENS) if found is None else found


def kimi_usage(events: list[Event]) -> tuple[Any, Any, Any]:
    turns = tokens = cost = None
    for event in events:
        turns = event.get(NUM_TURNS, turns)
        cost = event.get(TOTAL_COST, cost)
        tokens = kimi_tokens(event_dict(event, USAGE), tokens)
    return turns, tokens, cost


def kimi_summary(output: str) -> str:
    events = kimi_events(output)
    if not events:
        return output_tail(output)
    turns, tokens, cost = kimi_usage(events)
    errors = kimi_errors(events)
    text = (errors[-1] if errors else last_text(kimi_texts(events)))[:SUMMARY_WIDTH]
    return summary_line({"turns": turns, TOKENS: tokens}, cost, text)


def grok_parse_json(output: str) -> Event | None:
    text = output.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return json_object(text)
    return data if isinstance(data, dict) else None


def grok_failed(data: Event, code: int) -> bool:
    return data.get("type") == ERROR or code != 0


def grok_limit_text(data: Event, output: str) -> bool:
    blob = " ".join(str(data.get(key, "")) for key in (MESSAGE, "text", "type", "stopReason"))
    return bool(GROK_LIMIT_PATTERN.search(blob) or GROK_LIMIT_PATTERN.search(output))


def grok_rate_limited(code: int, output: str) -> bool:
    data = grok_parse_json(output)
    if data is None:
        return limited_output(code, output, GROK_LIMIT_PATTERN)
    return grok_failed(data, code) and grok_limit_text(data, output)


def grok_cost(data: Event) -> Any:
    cost = data.get(TOTAL_COST)
    if cost is not None:
        return cost
    parts = model_costs(data)
    return sum(parts) if parts else None


def model_costs(data: Event) -> list[Any]:
    rows = event_dict(data, "modelUsage").values()
    return [row[COST_USD] for row in rows if isinstance(row, dict) and row.get(COST_USD) is not None]


def token_info(tokens: Any) -> str:
    return f"tokens={tokens} " if tokens else ""


def grok_text(data: Event) -> str:
    return str(data.get("text") or data.get(MESSAGE) or "")[:SUMMARY_WIDTH]


def grok_summary(output: str) -> str:
    data = grok_parse_json(output)
    if data is None:
        return output_tail(output)
    text = grok_text(data)
    turns = data.get(NUM_TURNS, "?")
    cost = grok_cost(data)
    if cost is not None:
        return f"turns={turns} api-equivalent=${float(cost):.2f} {text!r}"
    tokens = event_dict(data, USAGE).get(TOTAL_TOKENS)
    return f"turns={turns} {token_info(tokens)}{text!r}".strip()


def grok_always_approve_locked(code: int, output: str) -> bool:
    if code == 0:
        return False
    data = grok_parse_json(output)
    blob = output if data is None else str(data.get(MESSAGE) or output)
    return bool(GROK_APPROVE_LOCK.search(blob))


def rate_limited(code: int, output: str) -> bool:
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return limited_output(code, output)
    if "is_error" in data:
        return bool(data.get("is_error")) and bool(LIMIT_PATTERN.search(str(data.get(RESULT, ""))))
    return response_limited(data, code)


def response_limited(data: Any, code: int) -> bool:
    error_text = str(data.get("response", "")) or str(data.get(ERROR, ""))
    failed = data.get("status") == "ERROR" or code != 0
    return failed and bool(LIMIT_PATTERN.search(error_text))


def result_tokens(usage: Event) -> Any:
    found = usage.get(TOTAL_TOKENS)
    return summed_tokens(usage, INPUT_OUTPUT_TOKENS) if found is None else found


def is_result(data: Any, usage: Event) -> bool:
    return data.get("type") == RESULT or any(key in usage for key in INPUT_OUTPUT_TOKENS)


def result_summary(data: Any, usage: Event) -> str:
    text = repr(str(data.get(RESULT) or "")[:SUMMARY_WIDTH])
    tokens = result_tokens(usage)
    shown = "" if tokens is None else f"tokens={tokens} "
    return f"{shown}{text}".strip()


def turns_summary(data: Any, usage: Event) -> str:
    turns = data.get(NUM_TURNS, "?")
    text = repr(str(data.get(RESULT) or data.get("response") or "")[:SUMMARY_WIDTH])
    return f"turns={turns} {token_info(usage.get('total_tokens'))}{text}".strip()


def summary(output: str) -> str:
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return output_tail(output)
    if TOTAL_COST in data:
        cost = data.get(TOTAL_COST, 0)
        return f"turns={data.get('num_turns')} api-equivalent=${cost:.2f} {str(data.get('result', ''))[:SUMMARY_WIDTH]!r}"
    usage = event_dict(data, USAGE)
    if is_result(data, usage):
        return result_summary(data, usage)
    return turns_summary(data, usage)


def approve(state: Run) -> bool:
    reports = sorted(state.handoffs.glob("*.md"))
    if reports:
        print("\n" + reports[-1].read_text())
    if not sys.stdin.isatty():
        print("non-interactive: continuing without approval (use --to critic to stop here)")
        return True
    return input("continue to coder? [y/N] ").strip().lower() == "y"
