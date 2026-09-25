#!/usr/bin/env python3
import shutil
import tempfile
from pathlib import Path

import harness

root = harness.use_tree()

TARGETS = (
    "ran_against.parse",
    "ran_against.finish",
    "runner.includes_qa",
    "runner.ending_for",
    "runner.read_qa_ran_against",
    "collect.finished_note",
    "collect.not_verified_activity",
    "panels.dead_row_text",
)

HANDOFF = (
    "# QA handoff\n"
    "Exercised the donate page.\n"
    "ran-against: app\n"
    "Defects: none.\n"
)
NOTE_LINE = "pipeline complete, NOT verified against the running app (qa ran against a harness)"

try:
    from marestail import ran_against
except ImportError:
    ran_against = None

if ran_against is None:
    for target in TARGETS:
        harness.absent(target)
else:
    from marestail.config import Config
    from marestail.pipeline import Worker
    from marestail.runner import Run
    from marestail import runner
    from marestail.tui import collect, panels
    from marestail.tui.model import RepoState

    folder = Path(tempfile.mkdtemp())
    log_dir = Path(tempfile.mkdtemp())
    try:
        (folder / "tasks").mkdir()
        task = folder / "tasks" / "task.md"
        task.write_text("bench\n")
        config = Config(root=folder, raw={})
        state = Run(config=config, task=task, model="m", retries=1, agent="claude")
        state.handoffs.mkdir(parents=True)
        (state.handoffs / "01-qa.md").write_text(HANDOFF)
        state.ran_against = "app"
        qa_steps = [Worker("qa", "qa")]
        log_path = log_dir / "overnight.log"
        log_path.write_text(f"== qa (stub) attempt 1\nfinished in 0.1m\n{NOTE_LINE}\n")
        repo = RepoState(
            name="bed",
            root=folder,
            branch="main",
            head="abc",
            task="005",
            log_path=log_path,
            runner_activity=NOTE_LINE,
        )

        harness.emit("ran_against.parse", harness.measure(lambda: ran_against.parse(HANDOFF)))
        harness.emit("ran_against.finish", harness.measure(lambda: ran_against.finish("harness")))
        harness.emit("runner.includes_qa", harness.measure(lambda: runner.includes_qa(qa_steps)))
        harness.emit("runner.ending_for", harness.measure(lambda: runner.ending_for(state, qa_steps)))
        harness.emit(
            "runner.read_qa_ran_against",
            harness.measure(lambda: runner.read_qa_ran_against(state)),
        )
        harness.emit("collect.finished_note", harness.measure(lambda: collect.finished_note(NOTE_LINE)))
        harness.emit(
            "collect.not_verified_activity",
            harness.measure(lambda: collect.not_verified_activity(log_path)),
        )
        harness.emit("panels.dead_row_text", harness.measure(lambda: panels.dead_row_text(repo)))
    finally:
        shutil.rmtree(folder, ignore_errors=True)
        shutil.rmtree(log_dir, ignore_errors=True)
