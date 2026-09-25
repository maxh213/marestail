import contextlib
import os
import re
import shutil
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from marestail import audit, backends, freeze, practices, prompts, ran_against, timeline
from marestail import config as config_module
from marestail import route as dandelion
from marestail.backends import (
    AGENT_TIMEOUT,
    CLAUDE,
    EMPTY,
    GROK,
    HERMES,
    JUNIE,
    KILO_DEFAULT_MODEL,
    SPACE,
    Event,
    agy_command,
    claude_command,
    cursor_command,
    grok_always_approve_locked,
    grok_effort,
    junie_command,
    kilo_command,
    kilo_variant,
)
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
from marestail.shell import ensure_dir, run

PASS = "PASS"
BOUNCE = "BOUNCE"
AUTHOR = "AUTHOR"
PERF = "perf"
QA = "qa"
CONFIG_CHANGE = "## Config change"
MISSING_RAN_AGAINST = ran_against.MISSING
LIMIT_WAIT_SECONDS = int(os.environ.get("MARESTAIL_LIMIT_WAIT_SECONDS", "600"))
LIMIT_WAITS = int(os.environ.get("MARESTAIL_LIMIT_WAITS", "12"))
WORKER_REPEAT_LIMIT = 3
UNLIMITED = "unlimited"
ENABLED = "enabled"
ATTEMPT_CAP = 10000
MISSING_OK = True
AUTHOR_ROUNDS = 3
ALLOW_EMPTY = "--allow-empty"
UNMATCHED = "--ignore-unmatch"
COMMIT = "commit"
CHANGED = "changed"
CACHED = "--cached"
NAME_ONLY = "--name-only"
CHECKOUT = "checkout"
GIT = "git"
DIFF = "diff"
LS_TREE = "ls-tree"
LS_FILES = "ls-files"
LOG = "log"
ADD = "add"
RM = "rm"
CLEAN = "clean"
NEWLINE = "\n"
RENAME_MARK = " -> "
HEAD_REF = "HEAD"
ALL_FILES = "-A"
QUIET = "-q"
RECURSIVE = "-r"
DOUBLE_DASH = "--"
CHECK_IGNORE = "check-ignore"
NO_INDEX = "--no-index"
COMMIT_TREE = "commit-tree"
PARENT_FLAG = "-p"
MESSAGE_FLAG = "-m"
HEADING_MARK = "\n## "
AUTHOR_NAME = "GIT_AUTHOR_NAME"
AUTHOR_EMAIL = "GIT_AUTHOR_EMAIL"
AUTHOR_DATE = "GIT_AUTHOR_DATE"
SPLIT_FIELDS = 3
MINUTES = 60
LIST_SHOW = 10
COMMA_JOIN = ", "
PROPOSAL_GLOB = "*-proposal.md"
STATUS_WIDTH = 2
SPACE_AT = 2
MISSING = -1
LAST = -1
STATUS_COMMAND = [GIT, "status", "--porcelain", "--untracked-files=all"]
AUTHOR_AGAIN = "You asked for an authoring round; benches are editable again in the authoring phase."
AUTHOR_DONE = "No authoring rounds left; benches stay frozen. Write PASS or BOUNCE with the benches as they are."
BENCHES_CHANGED = "Benches changed in the previous authoring round; samples taken before the change were dropped."
AUTHOR_VERDICT = re.compile(r"^\s*VERDICT:\s*AUTHOR\b", re.IGNORECASE | re.MULTILINE)
VERDICT_LINE = re.compile(r"VERDICT:\s*(PASS|BOUNCE)(?:[ \t]+(?:to\s+)?(\w+))?", re.IGNORECASE)

Verdict = tuple[str, str | None, str]


def drop_missing(path: Path, missing_ok: bool = MISSING_OK) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        if missing_ok:
            return
        raise


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
    account: str = ""
    labels: set[str] = field(default_factory=set)
    attempt_waits: list[dict[str, Any]] = field(default_factory=list)
    attempt_agent: dict[str, Any] | None = None
    ran_against: str | None = None

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
        ensure_dir(self.handoffs)
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
    return complete_pipeline(state, steps)


def complete_pipeline(state: Run, steps: list[Step]) -> int:
    archive_handoffs(state)
    return ending_for(state, steps)


def includes_qa(steps: list[Step]) -> bool:
    return any(step.name == QA for step in steps)


def say_complete() -> int:
    line, code = ran_against.finish("app")
    print(line)
    return code


def ending_for(state: Run, steps: list[Step]) -> int:
    if not includes_qa(steps):
        return say_complete()
    return qa_ending(state.ran_against or "nothing")


def qa_ending(against: str) -> int:
    line, code = ran_against.finish(against)
    print(line)
    return code


def paused(state: Run, step: Step, auto: bool) -> bool:
    return step.pause_after and not auto and not approve(state)


def share_scope(scope_changed: bool, hard: bool, focus: set[str]) -> None:
    if not scope_changed:
        return
    os.environ["MARESTAIL_SCOPE"] = "hard" if hard else CHANGED
    os.environ["MARESTAIL_FOCUS"] = os.pathsep.join(sorted(focus))


def run_step(state: Run, step: Step) -> bool:
    if isinstance(step, Worker):
        return run_named_worker(state, step)
    reason = skip_reason(state, step)
    if reason:
        print(reason)
        return True
    return run_judge_loop(state, step)


def run_named_worker(state: Run, worker: Worker) -> bool:
    if worker.name == QA:
        return run_qa(state, worker)
    return run_worker(state, worker, "")


def latest_qa_text(state: Run) -> str:
    reports = sorted(state.handoffs.glob("*-qa.md"))
    return reports[LAST].read_text() if reports else EMPTY


def read_qa_ran_against(state: Run) -> str | None:
    return ran_against.parse(latest_qa_text(state))


def record_ran_against(state: Run, against: str) -> bool:
    state.ran_against = against
    return True


def retry_qa_ran_against(state: Run, worker: Worker) -> bool:
    if not run_worker(state, worker, MISSING_RAN_AGAINST):
        return False
    return record_ran_against(state, read_qa_ran_against(state) or "nothing")


def settle_ran_against(state: Run, worker: Worker, against: str | None) -> bool:
    if against is not None:
        return record_ran_against(state, against)
    return retry_qa_ran_against(state, worker)


def run_qa(state: Run, worker: Worker) -> bool:
    if not run_worker(state, worker, ""):
        return False
    return settle_ran_against(state, worker, read_qa_ran_against(state))


def skip_reason(state: Run, judge: Judge) -> str:
    if disabled(state, judge):
        return f"{judge.name} disabled in marestail.toml; skipping"
    if without_guidance(state, judge):
        return "practices: no guidance files; skipping"
    return ""


def disabled(state: Run, judge: Judge) -> bool:
    return judge.optional and state.config.get(judge.name, ENABLED, True) is False


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
    if rework(state, judge, target, report):
        return None, report
    return False, report


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


def attempt_limit(retries: int) -> int:
    return ATTEMPT_CAP if retries < 1 else retries


def attempts_shown(retries: int) -> str:
    return UNLIMITED if retries < 1 else str(retries)


def attempts(retries: int) -> Iterator[int]:
    yield from range(1, attempt_limit(retries) + 1)


def run_worker(state: Run, worker: Worker, feedback: str) -> bool:
    before = head(state.config)
    streak: list[str] = []
    for attempt in attempts(state.retries):
        problems = worker_attempt(state, worker, feedback, attempt, before)
        if not problems:
            return True
        print(problems)
        feedback = problems
        streak = next_streak(streak, problems)
        if len(streak) >= WORKER_REPEAT_LIMIT:
            print(
                f"{worker.name} got the same problems back {WORKER_REPEAT_LIMIT} times in a row; the worker is not making progress, stopping for a human"
            )
            return False
    return False


def worker_attempt(state: Run, worker: Worker, feedback: str, attempt: int, before: str) -> str:
    report = state.next_report(worker.name)
    started_at = timeline.utc_now()
    attempt_before = head(state.config)
    reset_attempt(state)
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
    record_attempt(state, report, worker.name, attempt, started_at, attempt_before, [], None)
    return problems


def reset_attempt(state: Run) -> None:
    state.attempt_waits = []
    state.attempt_agent = None


def record_attempt(
    state: Run,
    report: Path,
    role: str,
    attempt: int,
    started_at: str,
    before: str,
    gate_results: list[Result],
    verdict: str | None,
) -> None:
    timeline.record(
        state.folder,
        state.task_name,
        report.stem,
        role,
        attempt,
        started_at,
        gate_results,
        list(state.attempt_waits),
        state.attempt_agent,
        verdict,
        attempt_commits(state.config, before),
        attempt_files(state.config, before),
        report,
    )


def attempt_commits(config: Config, before: str) -> list[dict[str, str]]:
    _, output = run([GIT, LOG, f"{before}..{HEAD_REF}", "--format=%H%x00%s"], cwd=config.root)
    return [commit_entry(line) for line in output.splitlines() if "\0" in line]


def commit_entry(line: str) -> dict[str, str]:
    sha, subject = line.split("\0", 1)
    return {"hash": sha, "subject": subject}


def attempt_files(config: Config, before: str) -> list[str]:
    _, output = run([GIT, DIFF, NAME_ONLY, f"{before}..{HEAD_REF}"], cwd=config.root)
    return [path for path in output.splitlines() if path]


def next_streak(streak: list[str], problems: str) -> list[str]:
    matched = bool(streak) and problem_shape(streak[LAST]) == problem_shape(problems)
    return {True: [*streak, problems], False: [problems]}[matched]


def problem_shape(problems: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\d+(?:\.\d+)?", "#", problems)).strip()


def run_judge(state: Run, judge: Judge) -> Verdict:
    gate = gate_for(state, judge.tier)
    progress = JudgeProgress()
    for attempt in attempts(state.retries):
        outcome = judged(state, judge, gate, progress, attempt)
        if outcome is not None:
            return outcome
    shown = attempts_shown(state.retries)
    return BOUNCE, None, f"{judge.name} produced no verdict after {shown} attempts"


def judged(state: Run, judge: Judge, gate: tuple[str, bool, list[Result]], progress: JudgeProgress, attempt: int) -> Verdict | None:
    report = state.next_report(judge.name)
    started_at = timeline.utc_now()
    before = head(state.config)
    reset_attempt(state)
    print(f"== {judge.name} ({report.stem}) attempt {attempt}")
    with measuring(state, judge) as session:
        prepare_perf(state, judge, gate[1], session, progress)
        outcome, progress.feedback = judge_attempt(state, judge, report, gate, session, progress.feedback)
    record_attempt(state, report, judge.name, attempt, started_at, before, gate[2], outcome_verdict(outcome))
    if outcome is not None and outcome[0] == AUTHOR:
        progress.author_requested()
        return None
    return outcome


def outcome_verdict(outcome: Verdict | None) -> str | None:
    if outcome is None:
        return None
    return timeline.verdict_text(outcome[0], outcome[1])


def has_author_round(left: int) -> bool:
    return left > 0


def prepare_perf(state: Run, judge: Judge, gate_ok: bool, session: perf_trees.Session | None, progress: JudgeProgress) -> None:
    if not gate_ok or session is None:
        return
    if has_author_round(progress.author_left):
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
    state: Run, judge: Judge, report: Path, gate: tuple[str, bool, list[Result]], session: perf_trees.Session | None, feedback: str
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
    trees = perf_trees.prompt_section(state.config, session) if session else EMPTY
    prompt = prompts.judge_prompt(state.config, judge, state.task, state.task_name, report, gate_report, trees, feedback, state.hard_focus)
    before = head(state.config)
    invoke(state, report.stem, prompt)
    discard_edits(state.config, keep=report, writes=judge_writes(judge))
    return before


def judge_writes(judge: Judge) -> tuple[str, ...]:
    return () if judge.name == PERF else judge.writes


def read_or_empty(path: Path) -> str:
    return path.read_text() if path.exists() else EMPTY


def session_output(state: Run, report: Path) -> str:
    return read_or_empty(state.folder / f"{report.stem}.json")


def asked_to_author(report: Path, blob: str) -> bool:
    combined = read_or_empty(report) + NEWLINE + blob
    return bool(AUTHOR_VERDICT.search(combined))


def with_target(prefix: str, target: str | None) -> str:
    return f"{prefix}{target}" if target else ""


def write_missing_verdict(report: Path, parsed: tuple[str, str | None]) -> None:
    if report.exists():
        return
    verdict, target = parsed
    report.write_text(f"VERDICT: {verdict}" + with_target(" ", target) + "\n")


def gated_verdict(judge: Judge, report: Path, gate: tuple[str, bool, list[Result]], parsed: tuple[str, str | None]) -> Verdict:
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
    _, staged = run([GIT, DIFF, CACHED, NAME_ONLY], cwd=config.root)
    if staged.strip():
        run([GIT, COMMIT, "-q", "-m", stamped(f"{message}\n\nBy {role}.", label)], cwd=config.root)


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


def gate_for(state: Run, tier: str | None) -> tuple[str, bool, list[Result]]:
    if tier is None:
        return "", True, []
    results = state.gates(tier)
    return render(results), all(result.ok for result in results), results


def parse_verdict(report: Path, extra: str = EMPTY) -> tuple[str, str | None] | None:
    blob = read_or_empty(report) + NEWLINE + extra
    match = VERDICT_LINE.search(blob)
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
    touched = changed_paths(state.config, [GIT, DIFF, NAME_ONLY, f"{before}..{HEAD_REF}"]) + dirty
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
    _, output = run([GIT, DIFF, before, DOUBLE_DASH, path], cwd=config.root)
    return output


def reject_config_change(state: Run, worker: Worker, report: Path, before: str, frozen: list[str]) -> list[str]:
    config = state.config
    justification = config_change_section(report)
    label = agent_label(state)
    _, diff = run([GIT, DIFF, f"{before}..{HEAD_REF}", "--", *frozen], cwd=config.root)
    if justification is None:
        revert(config, before, frozen, stamped(f"Revert change to frozen files by {report.stem}\n\nBy runner.", label))
        return [f"{path} is frozen for {worker.name}; reverted. Work within the current configuration." for path in frozen]
    body = f"Proposed by {report.stem}: {COMMA_JOIN.join(frozen)}\n\n{justification}\n\n```diff\n{diff.strip()}\n```\n"
    revert(
        config,
        before,
        frozen,
        stamped(f"Revert change to frozen files by {report.stem}, recorded as a proposal\n\n{body}\nBy runner.", label),
    )
    proposal = state.next_report("proposal")
    proposal.write_text(body)
    return [
        f"{COMMA_JOIN.join(frozen)}: frozen, reverted. Your reason was recorded as {proposal.relative_to(config.root)} "
        "for a human to consider after the run. Find a way within the current configuration."
    ]


def after_marker(text: str, marker: str) -> str:
    index = text.find(marker)
    return {True: text, False: text[index + len(marker) :]}[index == MISSING]


def until_heading(section: str) -> str:
    index = section.find(HEADING_MARK)
    return {True: section, False: section[:index]}[index == MISSING].strip()


def config_change_section(report: Path) -> str | None:
    text = read_or_empty(report)
    if CONFIG_CHANGE not in text:
        return None
    return until_heading(after_marker(text, CONFIG_CHANGE))


def revert(config: Config, before: str, paths: list[str], message: str) -> None:
    for args in revert_commands(before, paths, message):
        run(args, cwd=config.root)


def revert_commands(before: str, paths: list[str], message: str) -> list[list[str]]:
    return [
        [GIT, CHECKOUT, before, DOUBLE_DASH, *paths],
        [GIT, ADD, ALL_FILES, DOUBLE_DASH, *paths],
        [GIT, COMMIT, QUIET, MESSAGE_FLAG, message],
    ]


def proposals_summary(state: Run) -> str:
    files = sorted(state.handoffs.glob(PROPOSAL_GLOB))
    if not files:
        return ""
    body = "\n\n".join(f"### {f.stem}\n{f.read_text().strip()}" for f in files)
    return f"\n## Config changes the agents asked for and were refused\nDecide whether to make any of these yourself.\n\n{body}"


def porcelain_path(line: str) -> str:
    marked = bool(line[:STATUS_WIDTH].strip()) and line[SPACE_AT : SPACE_AT + 1] == SPACE
    return {True: line[STATUS_WIDTH + 1 :], False: line}[marked]


def outside_work(path: str) -> bool:
    return bool(path.strip()) and not path.strip().startswith(".marestail/")


def changed_paths(config: Config, command: list[str]) -> list[str]:
    _, output = run(command, cwd=config.root)
    paths = [porcelain_path(line) for line in output.splitlines()]
    return [renamed_path(path) for path in paths if outside_work(path)]


def renamed_path(path: str) -> str:
    index = path.find(RENAME_MARK)
    return {True: path, False: path[index + len(RENAME_MARK) :]}[index == MISSING].strip()


def stray_edits(config: Config, keep_relative: str, writes: tuple[str, ...]) -> list[str]:
    return [path for path in changed_paths(config, STATUS_COMMAND) if path != keep_relative and not freeze.matches_any(path, list(writes))]


def discard_edits(config: Config, keep: Path, writes: tuple[str, ...] = ()) -> None:
    keep_relative = str(keep.relative_to(config.root))
    stray = stray_edits(config, keep_relative, writes)
    if not stray:
        return
    print(f"   discarding edits a judge made: {COMMA_JOIN.join(stray[:LIST_SHOW])}")
    if writes:
        restore_paths(config, stray)
        return
    run([GIT, CHECKOUT, DOUBLE_DASH, "."], cwd=config.root)
    run([GIT, CLEAN, "-fdq", "-e", keep_relative, "-e", ".marestail/"], cwd=config.root)


def restore_paths(config: Config, paths: list[str]) -> None:
    _, listed = run([GIT, LS_TREE, RECURSIVE, NAME_ONLY, HEAD_REF, DOUBLE_DASH, *paths], cwd=config.root)
    tracked = sorted(set(listed.splitlines()) & set(paths))
    untracked = sorted(set(paths) - set(tracked))
    if tracked:
        run([GIT, CHECKOUT, HEAD_REF, DOUBLE_DASH, *tracked], cwd=config.root)
    if untracked:
        run([GIT, RM, QUIET, CACHED, UNMATCHED, DOUBLE_DASH, *untracked], cwd=config.root)
    for path in untracked:
        drop_missing(config.root / path)


def written_paths(config: Config, writes: tuple[str, ...]) -> list[str]:
    return [path for path in changed_paths(config, STATUS_COMMAND) if freeze.matches_any(path, list(writes))]


def stage_writes(config: Config, writes: tuple[str, ...]) -> None:
    if not writes:
        return
    kept = written_paths(config, writes)
    if kept:
        run([GIT, ADD, ALL_FILES, DOUBLE_DASH, *kept], cwd=config.root)


def added_paths(then: str, now: str) -> list[str]:
    previous = set(then.splitlines())
    return [path for path in now.splitlines() if path and path not in previous]


def newly_tracked_ignored(config: Config, before: str) -> list[str]:
    _, then = run([GIT, LS_TREE, RECURSIVE, NAME_ONLY, before], cwd=config.root)
    _, now = run([GIT, LS_FILES], cwd=config.root)
    return [path for path in added_paths(then, now) if ignored_path(config, path)]


def ignored_path(config: Config, path: str) -> bool:
    code, _ = run([GIT, CHECK_IGNORE, QUIET, NO_INDEX, DOUBLE_DASH, path], cwd=config.root)
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
    print(f"   dropping gitignored files: {COMMA_JOIN.join(paths[:LIST_SHOW])}")
    run([GIT, RM, QUIET, CACHED, UNMATCHED, DOUBLE_DASH, *paths], cwd=config.root)
    if head(config) != before:
        run([GIT, COMMIT, "--amend", ALLOW_EMPTY, "-q", "--no-edit"], cwd=config.root)


def restore_files(config: Config, saved: dict[str, bytes]) -> None:
    for path, data in saved.items():
        dest = config.root / path
        ensure_dir(dest.parent)
        dest.write_bytes(data)


def labels_of(used: set[str] | None) -> set[str]:
    if used is None:
        return set()
    return used


def fold_handoff(config: Config, role: str, report: Path, before: str, label: str, used: set[str] | None = None) -> None:
    body = report.read_text().strip()
    if head(config) == before:
        record_commit(config, f"{role} handoff", body, role, label)
        return
    labels = labels_of(used)
    stamp_history(config, before, label, labels)
    _, original = run([GIT, LOG, "-1", "--format=%B"], cwd=config.root)
    message = stamped(strip_byline(original, role), label, labels) + f"\n\n{body}\n\nBy {role}."
    run([GIT, COMMIT, "--amend", ALLOW_EMPTY, "-q", "-m", message], cwd=config.root)


def strip_byline(message: str, role: str) -> str:
    lines = message.rstrip().splitlines()
    if lines and lines[-1].strip() == f"By {role}.":
        lines = lines[:LAST]
    return "\n".join(lines).rstrip()


def record_commit(config: Config, subject: str, body: str, role: str, label: str) -> None:
    message = stamped(f"{subject}\n\n{body.strip()}\n\nBy {role}.", label)
    run([GIT, COMMIT, ALLOW_EMPTY, "-q", "-m", message], cwd=config.root)


def stamped(message: str, label: str, used: set[str] | None = None) -> str:
    if not label or stamped_already(message, {label, *labels_of(used)}):
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
    run([GIT, "reset", "--hard", "-q", parent], cwd=config.root)


def restampable(config: Config, before: str, label: str, commits: list[str]) -> bool:
    return bool(label) and bool(commits) and commits == rev_list(config, before, [])


def rev_list(config: Config, before: str, options: list[str]) -> list[str]:
    _, output = run([GIT, "rev-list", "--reverse", *options, f"{before}..{HEAD_REF}"], cwd=config.root)
    return output.split()


def restamp(config: Config, sha: str, parent: str, label: str, used: set[str] | None = None) -> str:
    _, details = run([GIT, LOG, "-1", "--format=%an%n%ae%n%aI%n%B", sha], cwd=config.root)
    name, email, date, message = details.split(NEWLINE, SPLIT_FIELDS)
    author = {AUTHOR_NAME: name, AUTHOR_EMAIL: email, AUTHOR_DATE: date}
    tree = f"{sha}^{{tree}}"
    _, created = run(
        [GIT, COMMIT_TREE, tree, PARENT_FLAG, parent, MESSAGE_FLAG, stamped(message.strip(), label, used)],
        cwd=config.root,
        env=author,
    )
    return created.split()[0]


def archive_handoffs(state: Run) -> None:
    destination = state.folder / f"handoffs-{time.strftime('%Y%m%dT%H%M%S')}"
    if state.handoffs.exists():
        ensure_dir(state.folder)
        shutil.move(str(state.handoffs), str(destination))
    perf_trees.archive_start(state.config, state.task_name, destination if destination.exists() else None)


def head(config: Config) -> str:
    _, output = run([GIT, "rev-parse", HEAD_REF], cwd=config.root)
    return output.strip()


def invoke(state: Run, label: str, prompt: str) -> None:
    ensure_dir(state.folder)
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
            note_wait(state, "dandelion-unrouted")
            wait(f"   {state.route}: {unrouted}; waiting {LIMIT_WAIT_SECONDS // 60} min before retrying {label}")
            return prompt, False
        prompt_file.write_text(prompt)
    return prompt, run_session(state, label, prompt, prompt_file)


def note_wait(state: Run, reason: str) -> None:
    state.attempt_waits.append({"reason": reason, "seconds": LIMIT_WAIT_SECONDS})


def wait(message: str) -> None:
    print(message)
    time.sleep(LIMIT_WAIT_SECONDS)


def run_session(state: Run, label: str, prompt: str, prompt_file: Path) -> bool:
    backend = resolve_agent(state)
    started = time.time()
    code, output = run_backend(state, backend, prompt, prompt_file)
    (state.folder / f"{label}.json").write_text(output)
    if backend == GROK and grok_always_approve_locked(code, output):
        print(f"   {label}: grok always-approve is locked; cannot run unattended")
        remember_agent(state, backend, started, "")
        return True
    limited, describe = outcome_readers(backend)
    if not limited(code, output):
        summary = describe(output)
        print(f"   {label} finished in {(elapsed(started)) / MINUTES:.1f} min: {summary}")
        remember_agent(state, backend, started, summary)
        return True
    note_wait(state, "rate-limit")
    wait(f"   rate limited; waiting {LIMIT_WAIT_SECONDS // 60} min before retrying {label}")
    return False


def remember_agent(state: Run, backend: str, started: float, summary: str) -> None:
    state.attempt_agent = {
        "backend": backend,
        "model": model_name(state),
        "effort": effort_name(state) or None,
        "account": state.account or None,
        "minutes": elapsed(started) / MINUTES,
        "summary": summary,
    }


def routed(state: Run, prompt: str) -> tuple[str, str]:
    before = agent_label(state)
    choice, unrouted = dandelion.choose(str(state.route), state.config.root)
    if choice is None:
        return prompt, unrouted
    state.agent, state.model, state.effort, state.account_env = choice.backend, choice.model, choice.effort, choice.env
    state.account = choice.line.split()[-1] if choice.env else ""
    after = agent_label(state)
    state.labels.add(after)
    print(f"   {state.route}: {choice.line}")
    return prompt.replace(f"[{before}] ", f"[{after}] "), ""


def run_backend(state: Run, backend: str, prompt: str, prompt_file: Path) -> tuple[int, str]:
    if backend in (GROK, "kimi", HERMES):
        return {GROK: backends.grok_run, "kimi": backends.kimi_run, HERMES: backends.hermes_run}[backend](state, prompt_file)
    if backend in ("kilo", JUNIE):
        return {"kilo": backends.kilo_run, JUNIE: backends.junie_run}[backend](state, prompt)
    return run(agent_command(state), cwd=state.config.root, stdin=prompt, timeout=AGENT_TIMEOUT, env=agent_env(state))


def outcome_readers(backend: str) -> tuple[Callable[[int, str], bool], Callable[[str], str]]:
    readers = {
        "grok": (backends.grok_rate_limited, backends.grok_summary),
        "kilo": (backends.kilo_rate_limited, backends.kilo_summary),
        "kimi": (backends.kimi_rate_limited, backends.kimi_summary),
        JUNIE: (backends.junie_rate_limited, backends.junie_summary),
        HERMES: (backends.hermes_rate_limited, backends.hermes_summary),
    }
    return readers.get(backend, (backends.rate_limited, backends.summary))


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
    return SPACE.join(part for part in (model_name(state), effort_name(state)) if part)


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
    if backend == GROK:
        return grok_effort(state)
    return state.effort


def agent_env(state: Run) -> dict[str, str]:
    if resolve_agent(state) == CLAUDE:
        return {"CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS": "0", **state.account_env}
    return dict(state.account_env)


def agent_command(state: Run) -> list[str]:
    builders = {"agy": agy_command, "cursor": cursor_command, "kilo": kilo_command, JUNIE: junie_command}
    return builders.get(resolve_agent(state), claude_command)(state)


def approve(state: Run) -> bool:
    reports = sorted(state.handoffs.glob("*.md"))
    if reports:
        print("\n" + reports[LAST].read_text())
    if not sys.stdin.isatty():
        print("non-interactive: continuing without approval (use --to critic to stop here)")
        return True
    return input("continue to coder? [y/N] ").strip().lower() == "y"
