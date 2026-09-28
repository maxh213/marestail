#!/usr/bin/env python3
import inspect
import tempfile
from pathlib import Path

import harness

root = harness.use_tree()

from marestail.config import Config
from marestail.context import Context, build
from marestail.gates import comments, py_mutation, ts_mutation

config = harness.make_config(root)
has_hyper = hasattr(Context, "on_changed_lines")
python_files = sorted(str(path.relative_to(root)) for path in (root / "marestail").rglob("*.py"))
every_third = {file: set(range(1, 2000, 3)) for file in python_files}


def synthetic(hyper: bool) -> Context:
    ctx = Context(config=config, scope_changed=True, changed=set(python_files), changed_lines_map=every_third)
    ctx.hyper = hyper
    return ctx


findings = [
    shape.format(path=python_files[index % len(python_files)], line=index % 400)
    for index in range(2000)
    for shape in (
        "{path}:{line}:5 E711 comparison to None",
        "{path}:{line} comment: # old note",
        "sonar {path}:{line} (python:S1481) unused local",
        "{path} Would reformat",
    )
][:2000]


def build_hyper() -> None:
    build(config, False, None, False, True)


def filter_findings() -> None:
    ctx = synthetic(True)
    ctx.on_changed_lines(findings)
    ctx.file_level_note()


def parse_locations() -> None:
    from marestail._location import location

    for finding in findings:
        location(finding)


MODULE_FUNCTIONS = 40
MUTANTS_PER_FUNCTION = 6


def function_source(name: str, body_offset: int) -> str:
    lines = [f"def {name}(value):", "    total = 0"]
    lines += [f"    total += value * {step + body_offset}" for step in range(8)]
    return "\n".join([*lines, "    return total", ""])


def mutant_tree() -> Path:
    base = Path(tempfile.mkdtemp())
    (base / "pkg").mkdir()
    (base / "mutants" / "pkg").mkdir(parents=True)
    original = "\n".join(function_source(f"f{index}", 0) for index in range(MODULE_FUNCTIONS))
    (base / "pkg" / "mod.py").write_text(original)
    mutated = [function_source(f"x_f{index}__mutmut_orig", 0) for index in range(MODULE_FUNCTIONS)]
    mutated += [
        function_source(f"x_f{index}__mutmut_{number}", number)
        for index in range(MODULE_FUNCTIONS)
        for number in range(1, MUTANTS_PER_FUNCTION + 1)
    ]
    (base / "mutants" / "pkg" / "mod.py").write_text("\n".join(mutated))
    return base


mutant_root = mutant_tree()
mutant_ctx = Context(config=Config(root=mutant_root, raw={"python": {"root": "."}}), scope_changed=True)
statuses = [
    (f"pkg.mod.x_f{index}__mutmut_{number}", "survived")
    for index in range(MODULE_FUNCTIONS)
    for number in range(1, MUTANTS_PER_FUNCTION + 1)
]


def mutant_lines() -> None:
    where = py_mutation.MutantLines(mutant_ctx).where
    for status in statuses:
        where(status)


changed_ts = {f"src/file{index}.ts": set(range(1, 600, 2)) | set(range(600, 700)) for index in range(20)}


def ts_ranges() -> None:
    for source, lines in changed_ts.items():
        ts_mutation.line_ranges(source, lines)


def build_accepts_hyper() -> bool:
    return "hyper" in inspect.signature(build).parameters


if build_accepts_hyper():
    harness.emit("context.build hyper", harness.measure(build_hyper))
else:
    harness.absent("context.build hyper")

if has_hyper:
    harness.emit("context.on_changed_lines hyper", harness.measure(filter_findings))
    harness.emit("_location.location", harness.measure(parse_locations))
    harness.emit("comments.python_findings hyper", harness.measure(lambda: comments.python_findings(synthetic(True))))
else:
    harness.absent("context.on_changed_lines hyper")
    harness.absent("_location.location")
    harness.absent("comments.python_findings hyper")

harness.emit("comments.python_findings changed", harness.measure(lambda: comments.python_findings(synthetic(False))))

if hasattr(py_mutation, "MutantLines"):
    harness.emit("py_mutation.MutantLines.where", harness.measure(mutant_lines))
else:
    harness.absent("py_mutation.MutantLines.where")

if hasattr(ts_mutation, "line_ranges"):
    harness.emit("ts_mutation.line_ranges", harness.measure(ts_ranges))
else:
    harness.absent("ts_mutation.line_ranges")
