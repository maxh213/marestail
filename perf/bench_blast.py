#!/usr/bin/env python3
import inspect
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import harness

root = harness.use_tree()

from marestail import freeze, pipeline, prompts, runner
from marestail.config import Config

SOURCE_FILES = 20
FUNCTIONS = 12
TEST_FILES = 5
REVERTS = harness.VALUES


def git(folder: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=folder, check=True, capture_output=True, text=True).stdout


def function(index: int, indent: str, value: int) -> str:
    return f"def f{index}(x):\n{indent}total = x\n{indent}total += {value}\n{indent}return total\n\n\n"


def module(indent_from: int, changed: set[int]) -> str:
    return "".join(
        function(index, "        " if index >= indent_from else "    ", index + (100 if index in changed else 0))
        for index in range(FUNCTIONS)
    )


def fixture() -> tuple[Path, str, str]:
    folder = Path(tempfile.mkdtemp())
    git(folder, "init", "-q")
    git(folder, "config", "user.email", "bench@example.com")
    git(folder, "config", "user.name", "bench")
    git(folder, "config", "commit.gpgsign", "false")
    (folder / "pkg").mkdir()
    (folder / "tests").mkdir()
    for index in range(SOURCE_FILES):
        (folder / "pkg" / f"mod{index}.py").write_text(module(FUNCTIONS, set()))
    (folder / "pkg" / "old.py").write_text(module(FUNCTIONS, set()))
    (folder / "package.json").write_text('{"name": "bench"}\n')
    git(folder, "add", "-A")
    git(folder, "commit", "-q", "-m", "base")
    start = git(folder, "rev-parse", "HEAD").strip()
    for index in range(SOURCE_FILES):
        (folder / "pkg" / f"mod{index}.py").write_text(module(FUNCTIONS - 2, {1, 5, 9}))
    git(folder, "mv", "pkg/old.py", "pkg/new.py")
    for index in range(TEST_FILES):
        (folder / "tests" / f"test_mod{index}.py").write_text(module(FUNCTIONS, {index}))
    git(folder, "add", "-A")
    git(folder, "commit", "-q", "-m", "change")
    listed = [f"- pkg/mod{index}.py:{line}-{line} — the fix needs it" for index in range(SOURCE_FILES) for line in (8, 32)]
    handoff = "# Coder handoff\n## What I did\nbench\n\n## Hunks\n" + "\n".join(listed) + "\n\n## Config change\nNone.\n"
    return folder, start, handoff


folder, start, handoff = fixture()
try:
    config = Config(root=folder, raw={"git": {"base": "HEAD"}})
    handoffs = folder / ".marestail" / "handoffs" / "task"
    handoffs.mkdir(parents=True)
    (handoffs / "01-coder.md").write_text(handoff)
    (handoffs / "02-architect.md").write_text(handoff)
    task = folder / "task.md"
    task.write_text("# task\nbench\n")
    report = folder / ".marestail" / "report.md"
    report.write_text("VERDICT: PASS\n")
    paths = [f"pkg/mod{index}.py" for index in range(SOURCE_FILES)] + [
        "package.json",
        "package-lock.json",
        ".gitignore",
        "README.md",
        "tests/test_mod0.py",
        "src/app.test.ts",
        "marestail.toml",
        ".github/workflows/ci.yml",
    ]

    try:
        from marestail import hunks
    except ImportError:
        hunks = None

    if hunks is not None:
        review = hunks.review(config, start, handoffs)
        harness.emit("hunks.problems", harness.measure(lambda: hunks.problems(config, start, handoff)))
        harness.emit("hunks.review", harness.measure(lambda: hunks.review(config, start, handoffs)))
    else:
        review = None
        harness.absent("hunks.problems")
        harness.absent("hunks.review")

    if "hyper" in inspect.signature(freeze.frozen_paths).parameters:
        harness.emit("freeze.frozen_paths hyper", harness.measure(lambda: freeze.frozen_paths(config, "coder", paths, True)))
    else:
        harness.absent("freeze.frozen_paths hyper")

    if "review" in inspect.signature(prompts.judge_prompt).parameters:
        blast = pipeline.find("blast", "hyper")
        harness.emit(
            "prompts.judge_prompt blast",
            harness.measure(lambda: prompts.judge_prompt(config, blast, task, "task", report, "", hyper=True, review=review)),
        )
    else:
        harness.absent("prompts.judge_prompt blast")

    reverts: list[float] = []
    for attempt in range(harness.WARMUP + REVERTS):
        (folder / "package.json").write_text(f'{{"name": "bench", "attempt": {attempt}}}\n')
        git(folder, "commit", "-q", "-am", f"edit {attempt}")
        before = git(folder, "rev-parse", "HEAD~1").strip()
        started = time.perf_counter()
        runner.revert(config, before, ["package.json"], f"revert {attempt}")
        reverts.append((time.perf_counter() - started) * 1000)
    harness.emit("runner.revert", reverts[harness.WARMUP :])
finally:
    shutil.rmtree(folder, ignore_errors=True)
