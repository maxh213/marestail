#!/usr/bin/env python3
import importlib
import shutil
import subprocess
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

import harness

root = harness.use_tree()

from marestail import pipeline, runner
from marestail.config import Config

STAMP = "2026-09-28T14:03:07.412Z"
ENDED = "2026-09-28T14:05:37.412Z"
PID = 4242
STEP_END = {
    "step": "01-specifier",
    "role": "specifier",
    "attempt": 1,
    "outcome": "done",
    "bounce_to": None,
    "head": "abc123",
    "handoff": ".marestail/handoffs/t/01-specifier.md",
    "seconds": 1.1,
}
RUN_START = {
    "task": "020-runs-write-events",
    "task_path": "tasks/020-runs-write-events.md",
    "branch": "main",
    "base": "origin/master",
    "start_commit": "abc123",
    "from": "specifier",
    "to": "qa",
    "scope": "all",
    "agent": "claude",
    "model": "m",
    "effort": "low",
}
SPEC_ROLES = ["coder", "specifier", "qa"]
OTHER_ROLES = ["coder", "qa"]
PICKED = ("m", None, "low")
SCOPED = (False, False, set(), False)
BOUNCE = ("BOUNCE", None, "1. scenario missing")
PASSED = ("PASS", None, "")
AUTHOR = ("AUTHOR", None, "")
SCOPE_TARGETS = (
    "runner.run_scope",
    "runner.run_scope changed",
    "runner.run_scope hard",
    "runner.run_scope hyper",
)


def load(name: str) -> Any | None:
    try:
        return importlib.import_module(name)
    except ImportError:
        return None


def named(module: Any | None, name: str) -> Any | None:
    if module is None:
        return None
    return getattr(module, name, None)


def time_when(found: Any | None, target: str, call: Callable[[], Any]) -> None:
    if not callable(found):
        harness.absent(target)
        return
    harness.emit(target, harness.measure(call))


def git(folder: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=folder, check=True, capture_output=True)


def seeded_repo(folder: Path) -> Path:
    repo = folder / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "bench@example.com")
    git(repo, "config", "user.name", "bench")
    git(repo, "config", "commit.gpgsign", "false")
    (repo / "file.txt").write_text("x\n")
    git(repo, "add", "file.txt")
    git(repo, "commit", "-q", "-m", "seed")
    return repo


events = load("marestail.events")
timeline = load("marestail.timeline")
reported = load("marestail.reported")
folder = Path(tempfile.mkdtemp())
try:
    config = harness.make_config(root)
    task = root / "README.md"
    state = runner.Run(config=config, task=task, model="m", retries=1, agent="claude", effort="low")
    state.start = "abc123"
    report = root / ".marestail" / "handoffs" / "t" / "01-specifier.md"
    proposal = root / ".marestail" / "handoffs" / "t" / "02-proposal.md"
    missing = folder / "missing.md"
    outside = folder / "outside.md"
    critic = pipeline.find("critic")
    specifier = pipeline.find("specifier")
    steps = [pipeline.find("specifier"), pipeline.find("qa")]
    qa_steps = [pipeline.find("qa")]
    log_type = named(events, "Log")
    encoded = named(events, "_encoded_line")
    payload = named(events, "_payload")
    if callable(log_type):
        step_log = log_type(folder / "step-end.jsonl")
        start_log = log_type(folder / "run-start.jsonl")
        denied = folder / "events-dir"
        denied.mkdir()
        denied_log = log_type(denied)
        time_when(step_log.append, "events.Log.append step-end", lambda: step_log.append("step-end", STEP_END, STAMP, PID))
        time_when(start_log.append, "events.Log.append run-start", lambda: start_log.append("run-start", RUN_START, STAMP, PID))
        time_when(denied_log.append, "events.Log.append denied", lambda: denied_log.append("step-end", STEP_END, STAMP, PID))
    else:
        harness.absent("events.Log.append step-end")
        harness.absent("events.Log.append run-start")
        harness.absent("events.Log.append denied")
    time_when(encoded, "events._encoded_line", lambda: encoded("step-end", STEP_END, STAMP, PID))
    time_when(encoded, "events._encoded_line unknown", lambda: encoded("custom-event", {"added": "later"}, STAMP, PID))
    time_when(payload, "events._payload known", lambda: payload("step-end", {**STEP_END, "extra": True}))
    time_when(payload, "events._payload unknown", lambda: payload("custom-event", {"added": "later"}))

    time_plus = named(timeline, "time_plus")
    duration = named(timeline, "duration_seconds")
    parse_stamp = named(timeline, "_parse_stamp")
    time_when(time_plus, "timeline.time_plus", lambda: time_plus(STAMP, 90))
    time_when(duration, "timeline.duration_seconds", lambda: duration(STAMP, ENDED))
    time_when(parse_stamp, "timeline._parse_stamp", lambda: parse_stamp(STAMP))

    first_role = named(reported, "first_spec_role")
    unreproduced = named(reported, "unreproduced_reason")
    time_when(first_role, "reported.first_spec_role", lambda: first_role(SPEC_ROLES))
    time_when(first_role, "reported.first_spec_role miss", lambda: first_role(OTHER_ROLES))
    time_when(unreproduced, "reported.unreproduced_reason", lambda: unreproduced("specifier"))

    record_run_start = named(runner, "record_run_start")
    record_run_end = named(runner, "record_run_end")
    record_step_start = named(runner, "record_step_start")
    record_step_end = named(runner, "record_step_end")
    record_judge_end = named(runner, "record_judge_end")
    record_wait = named(runner, "record_wait")
    record_proposal = named(runner, "record_proposal")
    step_end_fields = named(runner, "step_end_fields")
    handoff_path = named(runner, "handoff_path")
    repo_path = named(runner, "repo_path")
    current_branch = named(runner, "current_branch")
    run_scope = named(runner, "run_scope")
    completion_reason = named(runner, "completion_reason")
    exit_reason = named(runner, "exception_exit_and_reason")
    judge_outcome = named(runner, "judge_outcome_and_bounce")
    pass_or_bounce = named(runner, "pass_or_bounce")
    write_give_up = named(runner, "write_give_up_report")
    stop_after = named(runner, "stop_after_last_attempt")
    stop_repeat = named(runner, "stop_for_repeated_problems")
    settle = named(runner, "_settle_judge_progress")
    on_sigterm = named(runner, "_on_sigterm")
    record_escape = named(runner, "_record_escape")
    if events is not None and hasattr(state, "last_timeline_step"):
        state.last_timeline_step = {"id": "01-specifier", "started_at": STAMP, "ended_at": ENDED}
        state.last_report = task
        state.current_step = "specifier"
        state.ran_against = "harness"

        def bound(name: str, found: Any | None, target: str, call: Callable[[], Any]) -> None:
            if not callable(found):
                harness.absent(target)
                return
            state.events = events.Log(folder / f"{name}.jsonl")
            harness.emit(target, harness.measure(call))

        def escape_term() -> None:
            try:
                record_escape(state, runner.Terminated(15))
            except SystemExit:
                return

        bound("run-start-writer", record_run_start, "runner.record_run_start", lambda: record_run_start(state, steps))
        bound("run-end", record_run_end, "runner.record_run_end", lambda: record_run_end(state, "complete", 0, None, None, None))
        bound(
            "step-start",
            record_step_start,
            "runner.record_step_start",
            lambda: record_step_start(state, "01-specifier", "specifier", "worker", 1, STAMP),
        )
        bound("step-end-writer", record_step_end, "runner.record_step_end", lambda: record_step_end(state, "specifier", 1, "done", None))
        bound("judge-end", record_judge_end, "runner.record_judge_end", lambda: record_judge_end(state, critic, 1, BOUNCE))
        bound("wait", record_wait, "runner.record_wait", lambda: record_wait(state, "rate-limit"))
        bound("proposal", record_proposal, "runner.record_proposal", lambda: record_proposal(state, report, proposal, ["marestail.toml"]))
        bound("escape", record_escape, "runner._record_escape", escape_term)
    else:
        harness.absent("runner.record_run_start")
        harness.absent("runner.record_run_end")
        harness.absent("runner.record_step_start")
        harness.absent("runner.record_step_end")
        harness.absent("runner.record_judge_end")
        harness.absent("runner.record_wait")
        harness.absent("runner.record_proposal")
        harness.absent("runner._record_escape")
    recorded = {"id": "01-specifier", "started_at": STAMP, "ended_at": ENDED}
    time_when(step_end_fields, "runner.step_end_fields", lambda: step_end_fields(config, recorded, "specifier", 1, "done", None, task))
    time_when(handoff_path, "runner.handoff_path", lambda: handoff_path(task, root))
    time_when(handoff_path, "runner.handoff_path missing", lambda: handoff_path(missing, root))
    time_when(repo_path, "runner.repo_path", lambda: repo_path(task, root))
    time_when(repo_path, "runner.repo_path outside", lambda: repo_path(outside, root))
    time_when(current_branch, "runner.current_branch", lambda: current_branch(config))
    if callable(run_scope):
        scopes = {
            "runner.run_scope": state,
            "runner.run_scope changed": replace(state, scope_changed=True),
            "runner.run_scope hard": replace(state, hard=True, scope_changed=True),
            "runner.run_scope hyper": replace(state, hyper=True),
        }
        for target, scoped_state in scopes.items():
            time_when(run_scope, target, lambda scoped_state=scoped_state: run_scope(scoped_state))
    else:
        for target in SCOPE_TARGETS:
            harness.absent(target)
    time_when(completion_reason, "runner.completion_reason", lambda: completion_reason(3, state, qa_steps))
    time_when(completion_reason, "runner.completion_reason zero", lambda: completion_reason(0, state, qa_steps))
    boom = RuntimeError("boom")
    raised = SystemExit(143)
    time_when(exit_reason, "runner.exception_exit_and_reason", lambda: exit_reason(boom))
    time_when(exit_reason, "runner.exception_exit_and_reason exit", lambda: exit_reason(raised))
    time_when(judge_outcome, "runner.judge_outcome_and_bounce", lambda: judge_outcome(critic, BOUNCE))
    time_when(judge_outcome, "runner.judge_outcome_and_bounce failed", lambda: judge_outcome(critic, None))
    time_when(pass_or_bounce, "runner.pass_or_bounce", lambda: pass_or_bounce(critic, BOUNCE))
    time_when(pass_or_bounce, "runner.pass_or_bounce pass", lambda: pass_or_bounce(critic, PASSED))
    if callable(stop_after) and events is not None and hasattr(state, "events"):
        state.events = events.Log(folder / "stop-after.jsonl")
    time_when(stop_after, "runner.stop_after_last_attempt", lambda: stop_after(state, specifier, 1))
    time_when(stop_repeat, "runner.stop_for_repeated_problems", lambda: stop_repeat(state, specifier, ["once"], 1))
    held_report = state.last_report if hasattr(state, "last_report") else None
    give_up_dir = folder / "give-up"
    give_up_dir.mkdir()
    pending = [give_up_dir / f"{index}.md" for index in range(harness.WARMUP + harness.VALUES)]

    def give_up() -> None:
        state.last_report = pending.pop()
        write_give_up(state, "critic produced no verdict after 1 attempts")

    time_when(write_give_up, "runner.write_give_up_report", give_up)
    if hasattr(state, "last_report"):
        state.last_report = held_report

    def settle_pass() -> None:
        settle(PASSED, runner.JudgeProgress())

    def settle_author() -> None:
        settle(AUTHOR, runner.JudgeProgress())

    time_when(settle, "runner._settle_judge_progress", settle_pass)
    time_when(settle, "runner._settle_judge_progress author", settle_author)

    def trip_term() -> None:
        try:
            on_sigterm(15, None)
        except runner.Terminated:
            return

    time_when(on_sigterm, "runner._on_sigterm", trip_term)

    if events is not None and hasattr(state, "events"):
        state.events = events.Log(folder / "note-wait.jsonl")
    harness.emit("runner.note_wait", harness.measure(lambda: runner.note_wait(state, "rate-limit")))
    temp_task = folder / "tasks" / "020-runs-write-events.md"
    temp_task.parent.mkdir(parents=True)
    temp_task.write_text("# bench\n")
    temp_config = Config(root=folder, raw={"git": {"base": "HEAD"}})
    harness.emit(
        "runner.make_run",
        harness.measure(lambda: runner.make_run(temp_config, temp_task, 1, "claude", PICKED, SCOPED)),
    )
    harness.emit(
        "runner.rework not worker",
        harness.measure(lambda: runner.rework(state, critic, "hardener", "1. finding")),
    )

    repo = seeded_repo(folder)
    attempt_task = repo / "task.md"
    attempt_task.write_text("bench\n")
    handoff = repo / "01-coder.md"
    handoff.write_text("coded\n\nmore\n")
    attempt_config = Config(root=repo, raw={"git": {"base": "HEAD"}})
    attempt_state = runner.Run(config=attempt_config, task=attempt_task, model="m", retries=1, agent="claude")
    before = runner.head(attempt_config)

    def record_one() -> None:
        for name in ("timeline.json", "timeline.md"):
            path = attempt_state.folder / name
            if path.exists():
                path.unlink()
        runner.record_attempt(attempt_state, handoff, "coder", 1, STAMP, before, [], None)

    harness.emit("runner.record_attempt", harness.measure(record_one))
finally:
    shutil.rmtree(folder, ignore_errors=True)
