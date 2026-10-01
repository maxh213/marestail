#!/usr/bin/env python3
import importlib
import shutil
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

import harness

harness.use_tree()

from marestail import _install, pipeline, practices, runner
from marestail.config import Config

CALLS = harness.VALUES + harness.WARMUP
DESIGN = pipeline.Judge("design", None, "architect", optional=True)
PRACTICES = pipeline.Judge("practices", None, "coder", pinned_bounce=True, optional=True)
OTHER = pipeline.Judge("hardener", "full", "coder")
TRASH: list[Path] = []


def load(name: str) -> object | None:
    try:
        return importlib.import_module(name)
    except ImportError:
        return None


def scratch() -> Path:
    folder = Path(tempfile.mkdtemp())
    TRASH.append(folder)
    return folder


def emit_when(present: bool, target: str, fn: Callable[[], object]) -> None:
    if present:
        harness.emit(target, harness.measure(fn))
    else:
        harness.absent(target)


def write_rules(folder: Path) -> None:
    guidance = folder / "guidance"
    patterns = guidance / "patterns"
    patterns.mkdir(parents=True)
    for name in ("cs.md", "ts.md", "go.md"):
        (guidance / name).write_text("# rules\n")
        (patterns / name).write_text("# patterns\n")


def run_for(config: Config) -> runner.Run:
    return runner.Run(config=config, task=Path("/work/task.md"), model="m", retries=1)


def make_run() -> runner.Run:
    folder = scratch()
    state = runner.Run(config=Config(root=folder, raw={}), task=folder / "task.md", model="m", retries=1)
    state.handoffs.mkdir(parents=True)
    (state.handoffs / "01-coder.md").write_text("handoff\n")
    return state


def plant(target: Path) -> None:
    (target / "Gemfile").write_text("gem 'x'\n")
    (target / "mix.exs").write_text("defmodule App.MixProject do\nend\n")
    (target / "go.mod").write_text("module example.com/app\n\ngo 1.22\n")
    nested = target / "src" / "nested"
    nested.mkdir(parents=True)
    (target / "src" / "App.csproj").write_text("<Project></Project>\n")
    (nested / "a.erl").write_text("-module(a).\n")
    for index in range(30):
        (nested / f"f{index}.txt").write_text("x\n")


def make_target(marked: bool) -> Path:
    target = scratch()
    if marked:
        plant(target)
    return target


def pool(marked: bool) -> list[Path]:
    return [make_target(marked) for _ in range(CALLS)]


def csharp_of() -> Callable[[Path], bool] | None:
    if GUIDANCE is not None and hasattr(GUIDANCE, "uses_csharp"):
        return GUIDANCE.uses_csharp
    found = getattr(_install, "uses_csharp", None)
    return found if callable(found) else None


def measure_lists(pattern_files: Callable[[Path], list[Path]] | None) -> None:
    harness.emit("practices.files populated", harness.measure(lambda: practices.files(PRESENT_ROOT)))
    harness.emit("practices.files missing", harness.measure(lambda: practices.files(MISSING_ROOT)))
    if pattern_files is None:
        harness.absent("practices.pattern_files populated")
        harness.absent("practices.pattern_files missing")
        return
    harness.emit("practices.pattern_files populated", harness.measure(lambda: pattern_files(PRESENT_ROOT)))
    harness.emit("practices.pattern_files missing", harness.measure(lambda: pattern_files(MISSING_ROOT)))


def measure_skip() -> None:
    harness.emit("runner.skip_reason design missing", harness.measure(lambda: runner.skip_reason(MISSING_STATE, DESIGN)))
    harness.emit("runner.skip_reason design present", harness.measure(lambda: runner.skip_reason(PRESENT_STATE, DESIGN)))
    harness.emit("runner.skip_reason design disabled", harness.measure(lambda: runner.skip_reason(DISABLED_STATE, DESIGN)))
    harness.emit(
        "runner.skip_reason practices missing",
        harness.measure(lambda: runner.skip_reason(MISSING_STATE, PRACTICES)),
    )
    harness.emit(
        "runner.skip_reason practices present",
        harness.measure(lambda: runner.skip_reason(PRESENT_STATE, PRACTICES)),
    )


def measure_rulebooks(missing_rulebooks: Callable[..., str] | None) -> None:
    pairs = (
        ("runner.missing_rulebooks design missing", MISSING_ROOT, DESIGN),
        ("runner.missing_rulebooks design present", PRESENT_ROOT, DESIGN),
        ("runner.missing_rulebooks practices missing", MISSING_ROOT, PRACTICES),
        ("runner.missing_rulebooks practices present", PRESENT_ROOT, PRACTICES),
        ("runner.missing_rulebooks other", PRESENT_ROOT, OTHER),
    )
    for target, folder, judge in pairs:
        emit_when(missing_rulebooks is not None, target, bound(missing_rulebooks, folder, judge))


def bound(fn: Callable[..., str] | None, folder: Path, judge: pipeline.Judge) -> Callable[[], object]:
    def run() -> object:
        return None if fn is None else fn(folder, judge)

    return run


def measure_csharp(uses_csharp: Callable[[Path], bool] | None) -> None:
    if uses_csharp is None:
        harness.absent("uses_csharp hit")
        harness.absent("uses_csharp miss")
        return
    hit = make_target(True)
    miss = scratch()
    nested = miss / "src" / "nested"
    nested.mkdir(parents=True)
    for index in range(30):
        (nested / f"f{index}.txt").write_text("x\n")
    harness.emit("uses_csharp hit", harness.measure(lambda: uses_csharp(hit)))
    harness.emit("uses_csharp miss", harness.measure(lambda: uses_csharp(miss)))


def measure_copy_if_missing() -> None:
    source = scratch() / "source.md"
    source.write_text("hello\n")
    destinations = [scratch() / "copied.md" for _ in range(CALLS)]
    harness.emit("install.copy_if_missing", harness.measure(lambda: _install.copy_if_missing(source, destinations.pop())))


def measure_archive() -> None:
    states = [make_run() for _ in range(CALLS)]
    harness.emit("runner.archive_handoffs", harness.measure(lambda: runner.archive_handoffs(states.pop())))


def measure_free(free: Callable[[Path], Path] | None) -> None:
    if free is None:
        harness.absent("runner.free_handoffs_folder")
        harness.absent("runner.free_handoffs_folder taken")
        return
    fresh = scratch()
    taken = scratch()
    harness.emit("runner.free_handoffs_folder", harness.measure(lambda: free(fresh)))

    def taken_once() -> Path:
        stamp = time.strftime("%Y%m%dT%H%M%S")
        (taken / f"handoffs-{stamp}").mkdir(exist_ok=True)
        return free(taken)

    harness.emit("runner.free_handoffs_folder taken", harness.measure(taken_once))


def measure_copy(copy: Callable[[Path], None] | None) -> None:
    if copy is None:
        harness.absent("guidance.copy bare")
        harness.absent("guidance.copy marked")
        return
    bare = pool(False)
    marked = pool(True)
    harness.emit("guidance.copy bare", harness.measure(lambda: copy(bare.pop())))
    harness.emit("guidance.copy marked", harness.measure(lambda: copy(marked.pop())))


GUIDANCE = load("marestail._guidance")
PRESENT_ROOT = scratch()
MISSING_ROOT = scratch()
write_rules(PRESENT_ROOT)
PRESENT_STATE = run_for(Config(root=PRESENT_ROOT, raw={}))
MISSING_STATE = run_for(Config(root=MISSING_ROOT, raw={}))
DISABLED_STATE = run_for(Config(root=MISSING_ROOT, raw={"design": {"enabled": False}}))

try:
    measure_lists(getattr(practices, "pattern_files", None))
    measure_skip()
    measure_rulebooks(getattr(runner, "missing_rulebooks", None))
    measure_csharp(csharp_of())
    measure_copy_if_missing()
    measure_archive()
    measure_free(getattr(runner, "free_handoffs_folder", None))
    measure_copy(getattr(GUIDANCE, "copy", None) if GUIDANCE is not None else None)
finally:
    for folder in TRASH:
        shutil.rmtree(folder, ignore_errors=True)
