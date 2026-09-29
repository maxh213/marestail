#!/usr/bin/env python3
import importlib
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import harness

harness.use_tree()

from marestail import cli, javascript
from marestail.config import Config
from marestail.context import Context
from marestail.gates import ts_crap, ts_mutation, ts_tests

FILES = 10
FUNCTIONS = 30
SPAN = 10
LINES = FUNCTIONS * SPAN
TOOLING = ".marestail/tooling"
TEST_CMD = "for f in src/*.test.js; do node \"$f\" || exit 1; done"


def stryker_module() -> Any:
    try:
        return importlib.import_module("marestail.gates._stryker")
    except ImportError:
        return None


stryker = stryker_module()
has_test_cmd = stryker is not None and hasattr(Context, "test_cmd")


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def source_text(changed: bool) -> str:
    marker = "changed" if changed else "base"
    return "".join(f"var v{number} = '{marker}';\n" if number % 4 == 1 else f"var v{number} = 1;\n" for number in range(1, LINES + 1))


def make_repo() -> Path:
    root = Path(tempfile.mkdtemp())
    (root / "src").mkdir()
    for number in range(FILES):
        (root / "src" / f"mod{number}.js").write_text(source_text(False))
    (root / "package.json").write_text(json.dumps({"name": "perf", "devDependencies": {"left-pad": "1.0.0", "vitest": "3.0.0"}}))
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "-c", "user.name=perf", "-c", "user.email=perf@example.com", "commit", "-q", "-m", "seed")
    for number in range(FILES):
        (root / "src" / f"mod{number}.js").write_text(source_text(True))
    (root / ".marestail" / "stryker").mkdir(parents=True)
    return root


repo = make_repo()
js_files = [f"src/mod{number}.js" for number in range(FILES)]
changed_lines = set(range(1, LINES + 1, 4))
raw = {"git": {"base": "HEAD"}, "ts": {"root": ".", "tooling": TOOLING, "crap_max": 4}, "hyper": {"test_cmd": TEST_CMD}}
config = Config(root=repo, raw=raw)


def context() -> Context:
    ctx = Context(config=config, scope_changed=True, changed=set(js_files), changed_lines_map={file: changed_lines for file in js_files})
    ctx.hyper = True
    return ctx


def status(index: int, line: int) -> str:
    if index == 0 and line % 16 == 1:
        return "Survived"
    if line % 24 == 5:
        return "NoCoverage"
    return "Killed"


def mutant(index: int, line: int) -> dict[str, Any]:
    location = {"start": {"line": line, "column": 8}, "end": {"line": line, "column": 17}}
    return {"id": str(index), "mutatorName": "StringLiteral", "replacement": '""', "status": status(index, line), "location": location}


def mutated_lines() -> list[int]:
    return [line for line in sorted(changed_lines) if line % 20 != 13]


def report_text() -> str:
    files = {str(repo / file): {"mutants": [mutant(index, line) for line in mutated_lines() for index in range(3)]} for file in js_files}
    return json.dumps({"files": files})


REPORT = report_text()


def fake_stryker(command: list[str], cwd: Path, timeout: int | None = None) -> tuple[int, str]:
    (repo / ".marestail" / "stryker" / "mutation.json").write_text(REPORT)
    return 0, "stryker done"


def fake_tests(command: list[str], cwd: Path, timeout: int | None = None) -> tuple[int, str]:
    return 0, "ok\n" * 50


def ts_functions(file: str) -> list[dict[str, Any]]:
    found = []
    for index in range(FUNCTIONS):
        start = 1 + index * SPAN
        found.append({"file": file, "name": f"f{index}", "line": start, "endLine": start + SPAN - 1, "complexity": 3 + index % 8})
        found.append({"file": file, "name": "inner", "line": start + 2, "endLine": start + 7, "complexity": 2 + index % 3})
    return found


def fake_scan(_ctx: Any, _mode: str, files: list[Any], _cwd: Path | None = None) -> tuple[int, str]:
    if isinstance(files[0], Path):
        return 0, json.dumps(ts_functions(str(files[0])))
    return 0, json.dumps([fn for file in files for fn in ts_functions(file)])


def gates_together() -> None:
    ctx = context()
    ts_mutation.run_gate(ctx)
    ts_crap.run_gate(ctx)


TARGETS = [
    "context.test_cmd",
    "cli.scope_line test_cmd",
    "ts_tests.known_runner",
    "ts_tests.run_gate test_cmd",
    "ts_mutation.run_gate test_cmd",
    "ts_crap.run_gate test_cmd",
    "ts_mutation+ts_crap test_cmd",
]

if has_test_cmd:
    stryker.run = fake_stryker
    ts_tests.run = fake_tests
    javascript.scan = fake_scan
    shared = context()
    harness.emit("context.test_cmd", harness.measure(lambda: shared.test_cmd))
    harness.emit("cli.scope_line test_cmd", harness.measure(lambda: cli.scope_line(shared)))
    harness.emit("ts_tests.known_runner", harness.measure(lambda: ts_tests.known_runner(shared)))
    harness.emit("ts_tests.run_gate test_cmd", harness.measure(lambda: ts_tests.run_gate(context())))
    harness.emit("ts_mutation.run_gate test_cmd", harness.measure(lambda: ts_mutation.run_gate(context())))
    harness.emit("ts_crap.run_gate test_cmd", harness.measure(lambda: ts_crap.run_gate(context())))
    harness.emit("ts_mutation+ts_crap test_cmd", harness.measure(gates_together))
else:
    for target in TARGETS:
        harness.absent(target)
