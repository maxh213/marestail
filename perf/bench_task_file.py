#!/usr/bin/env python3
import importlib
import shutil
import tempfile
from pathlib import Path

import harness

root = harness.use_tree()

from marestail import cli as cli_module

BODY = (
    "# 019 — tasks say what they depend on\n\n"
    "Build the thing and keep the old behaviour working.\n\n"
    "## Notes\n\n- alpha\n- beta\n- gamma\n"
)
FILE_TARGETS = (
    "task_file.read blockless",
    "task_file.read empty depends",
    "task_file.read one dependency",
    "task_file.read several dependencies",
    "task_file.read problems",
    "task_file.read bad TOML",
)
CHECK_TARGETS = (
    "task_file.check clean",
    "task_file.check one file",
    "task_file.check problems",
    "task_file.check cycles",
    "task_file.check missing path",
    "task_file.check edge paths",
)
CLI_TARGETS = (
    "marestail tasks check clean",
    "marestail tasks check problems",
    "marestail tasks check missing path",
)


def task_module():
    try:
        return importlib.import_module("marestail.task_file")
    except ImportError:
        return None


def block(depends: str, stack: str | None = None) -> str:
    head = ["+++", f"depends = {depends}"]
    if stack is not None:
        head.append(f"stack = {stack}")
    return "\n".join([*head, "+++", BODY])


def put(folder: Path, name: str, text: str) -> Path:
    path = folder / name
    path.write_text(text)
    return path


def refuses(read, path: Path) -> None:
    try:
        read(path)
    except ValueError:
        pass


task_file = task_module()
folder = Path(tempfile.mkdtemp())
try:
    clean = folder / "clean"
    clean.mkdir()
    put(clean, "README.md", "# Tasks\n")
    put(clean, "001-base.md", block("[]"))
    one = put(clean, "002-next.md", block('["001-base"]', "true"))
    put(clean, "003-more.md", block('["001-base", "002-next"]', "false"))
    for index in range(4, 9):
        put(clean, f"{index:03d}-leaf.md", block('["002-next"]', "true"))
    put(clean, "notes.txt", "ignored\n")

    problems = folder / "problems"
    problems.mkdir()
    put(problems, "a1-empty-stack.md", block("[]", "true"))
    put(problems, "a2-typo.md", '+++\ndepend = ["a1-empty-stack"]\n+++\n' + BODY)
    put(problems, "a3-open.md", "+++\ndepends = []\n" + BODY)
    put(problems, "a4-plain.md", BODY)
    put(problems, "a5-bad-toml.md", "+++\ndepends = [broken\n+++\n" + BODY)
    put(problems, "a6-gone.md", block('["a9-missing"]', "true"))
    put(problems, "a7-self.md", block('["a7-self"]', "true"))
    put(problems, "a8-bad-depends.md", '+++\ndepends = [1]\nstack = "yes"\n+++\n' + BODY)

    cycles = folder / "cycles"
    cycles.mkdir()
    put(cycles, "b1-a.md", block('["b2-b"]', "true"))
    put(cycles, "b2-b.md", block('["b3-c"]', "true"))
    put(cycles, "b3-c.md", block('["b1-a"]', "true"))
    put(cycles, "b4-x.md", block('["b5-y"]', "true"))
    put(cycles, "b5-y.md", block('["b4-x"]', "true"))
    put(cycles, "b6-tail.md", block('["b1-a"]', "true"))
    put(cycles, "b7-clean.md", block("[]"))

    dups = folder / "dups"
    dups.mkdir()
    put(dups, "b7-clean.md", block("[]"))

    blockless = put(folder, "blockless.md", BODY)
    empty = put(folder, "empty.md", block("[]"))
    several = put(folder, "several.md", block('["001-base", "002-next", "003-more"]', "false"))
    broken = put(folder, "broken.md", '+++\ndepends = ["a", "a", "b/c", "d.md", "", "x\\\\y", "broken"]\nzap = 1\n+++\n' + BODY)
    bad_toml = put(folder, "bad-toml.md", "+++\ndepends = [broken\n+++\n" + BODY)

    if task_file is None:
        for target in (*FILE_TARGETS, *CHECK_TARGETS):
            harness.absent(target)
    else:
        harness.emit("task_file.read blockless", harness.measure(lambda: task_file.read(blockless)))
        harness.emit("task_file.read empty depends", harness.measure(lambda: task_file.read(empty)))
        harness.emit("task_file.read one dependency", harness.measure(lambda: task_file.read(one)))
        harness.emit("task_file.read several dependencies", harness.measure(lambda: task_file.read(several)))
        harness.emit("task_file.read problems", harness.measure(lambda: refuses(task_file.read, broken)))
        harness.emit("task_file.read bad TOML", harness.measure(lambda: refuses(task_file.read, bad_toml)))
        harness.emit("task_file.check clean", harness.measure(lambda: task_file.check([clean])))
        harness.emit("task_file.check one file", harness.measure(lambda: task_file.check([one])))
        harness.emit("task_file.check problems", harness.measure(lambda: task_file.check([problems])))
        harness.emit("task_file.check cycles", harness.measure(lambda: task_file.check([cycles, dups])))
        harness.emit("task_file.check missing path", harness.measure(lambda: task_file.check([folder / "absent"])))
        harness.emit(
            "task_file.check edge paths",
            harness.measure(lambda: task_file.check([clean, one, clean / "notes.txt", folder / "absent"])),
        )

    if task_file is None or not hasattr(cli_module, "tasks_check_command"):
        for target in CLI_TARGETS:
            harness.absent(target)
    else:
        harness.emit("marestail tasks check clean", harness.measure(lambda: cli_module.main(["tasks", "check", str(clean)])))
        harness.emit("marestail tasks check problems", harness.measure(lambda: cli_module.main(["tasks", "check", str(problems)])))
        harness.emit(
            "marestail tasks check missing path",
            harness.measure(lambda: cli_module.main(["tasks", "check", str(folder / "absent")])),
        )
finally:
    shutil.rmtree(folder, ignore_errors=True)
