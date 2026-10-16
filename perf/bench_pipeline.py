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
DEP_TASK = '+++\ndepends = ["018-runs-stay-nice"]\nstack = true\n+++\n# 019 — one dependency\n\nbench body\n'
MANY_TASK = '+++\ndepends = ["017-a", "018-b", "019-c"]\nstack = false\n+++\n# 019 — several dependencies\n\nbench body\n'
BROKEN_TASK = "+++\ndepends = []\nstack = true\n+++\n# 019 — broken\n\nbench body\n"
TREES = "- baseline: 30e2679 at /tmp/baseline\n- head: 3c9c587 at /tmp/head\n"
ACCEPT_TARGETS = (
    "runner._accept_task blockless",
    "runner._accept_task front matter",
    "runner._accept_task broken",
)


def takes(fn: object, name: str) -> bool:
    return name in inspect.signature(fn).parameters


folder = Path(tempfile.mkdtemp())
try:
    (folder / "tasks").mkdir()
    task = folder / "tasks" / "task.md"
    task.write_text("# task\nbench\n")
    dep_task = folder / "tasks" / "019-dep.md"
    dep_task.write_text(DEP_TASK)
    many_task = folder / "tasks" / "019-many.md"
    many_task.write_text(MANY_TASK)
    broken_task = folder / "tasks" / "019-broken.md"
    broken_task.write_text(BROKEN_TASK)
    config = Config(root=folder, raw={"git": {"base": "HEAD"}})
    handoffs = config.work / "handoffs" / "task"
    handoffs.mkdir(parents=True)
    (handoffs / "01-specifier.md").write_text("# Specifier handoff\nBy specifier.\n")
    for name in ("019-dep", "019-many"):
        extra = config.work / "handoffs" / name
        extra.mkdir(parents=True)
        (extra / "01-specifier.md").write_text("# Specifier handoff\nBy specifier.\n")
    note = config.work / "runs" / "019-dep" / "perf-author-1.md"
    note.parent.mkdir(parents=True)
    note.write_text("# Perf author note\n")

    def accept_broken() -> None:
        try:
            runner._accept_task(broken_task)
        except SystemExit:
            pass

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
    harness.emit(
        "prompts.worker_prompt depends",
        harness.measure(lambda: prompts.worker_prompt(config, coder, dep_task, "019-dep", report, "")),
    )
    harness.emit(
        "prompts.worker_prompt depends many",
        harness.measure(lambda: prompts.worker_prompt(config, coder, many_task, "019-many", report, "")),
    )
    harness.emit(
        "prompts.judge_prompt depends",
        harness.measure(lambda: prompts.judge_prompt(config, hardener, dep_task, "019-dep", report, "")),
    )
    harness.emit(
        "prompts.perf_author_prompt depends",
        harness.measure(lambda: prompts.perf_author_prompt(config, dep_task, "019-dep", TREES, note)),
    )
    if hasattr(runner, "_accept_task"):
        harness.emit("runner._accept_task blockless", harness.measure(lambda: runner._accept_task(task)))
        harness.emit("runner._accept_task front matter", harness.measure(lambda: runner._accept_task(dep_task)))
        harness.emit("runner._accept_task broken", harness.measure(accept_broken))
    else:
        for target in ACCEPT_TARGETS:
            harness.absent(target)
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
