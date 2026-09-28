#!/usr/bin/env python3
import inspect
import shutil
import tempfile
from pathlib import Path

import harness

root = harness.use_tree()

from marestail import pipeline, prompts, runner
from marestail.config import Config

HYPER = "hyper"
HYPER_TARGETS = (
    "pipeline.steps hyper",
    "pipeline.window hyper",
    "pipeline.find hyper",
    "runner.known_target hyper",
    "prompts.worker_prompt hyper",
    "prompts.judge_prompt hyper",
)
VERDICT = "VERDICT: BOUNCE cleaner\n1. finding\n"


def takes(fn: object, name: str) -> bool:
    return name in inspect.signature(fn).parameters


folder = Path(tempfile.mkdtemp())
try:
    (folder / "tasks").mkdir()
    task = folder / "tasks" / "task.md"
    task.write_text("# task\nbench\n")
    config = Config(root=folder, raw={"git": {"base": "HEAD"}})
    handoffs = config.work / "handoffs" / "task"
    handoffs.mkdir(parents=True)
    (handoffs / "01-specifier.md").write_text("# Specifier handoff\nBy specifier.\n")
    report = folder / ".marestail" / "report.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(VERDICT)
    coder = pipeline.find("coder")
    hardener = pipeline.find("hardener")
    focus = {"src.py"}

    harness.emit("pipeline.names", harness.measure(lambda: pipeline.names()))
    harness.emit("pipeline.find", harness.measure(lambda: pipeline.find("hardener")))
    harness.emit("pipeline.window", harness.measure(lambda: pipeline.window("coder", "hardener")))
    harness.emit("runner.known_target", harness.measure(lambda: runner.known_target("cleaner")))
    harness.emit("runner.parse_verdict", harness.measure(lambda: runner.parse_verdict(report)))
    harness.emit(
        "prompts.worker_prompt",
        harness.measure(lambda: prompts.worker_prompt(config, coder, task, "task", report, "")),
    )
    harness.emit(
        "prompts.worker_prompt hard",
        harness.measure(lambda: prompts.worker_prompt(config, coder, task, "task", report, "", hard_focus=focus)),
    )
    harness.emit(
        "prompts.judge_prompt",
        harness.measure(lambda: prompts.judge_prompt(config, hardener, task, "task", report, "")),
    )
    if hasattr(pipeline, "steps") and takes(prompts.worker_prompt, HYPER):
        harness.emit("pipeline.steps hyper", harness.measure(lambda: pipeline.steps(HYPER)))
        harness.emit("pipeline.window hyper", harness.measure(lambda: pipeline.window("coder", "hardener", HYPER)))
        harness.emit("pipeline.find hyper", harness.measure(lambda: pipeline.find("hardener", HYPER)))
        harness.emit("runner.known_target hyper", harness.measure(lambda: runner.known_target("cleaner", HYPER)))
        hyper_coder = pipeline.find("coder", HYPER)
        harness.emit(
            "prompts.worker_prompt hyper",
            harness.measure(lambda: prompts.worker_prompt(config, hyper_coder, task, "task", report, "", hyper=True)),
        )
        harness.emit(
            "prompts.judge_prompt hyper",
            harness.measure(lambda: prompts.judge_prompt(config, hardener, task, "task", report, "", hyper=True)),
        )
    else:
        for target in HYPER_TARGETS:
            harness.absent(target)
finally:
    shutil.rmtree(folder, ignore_errors=True)
