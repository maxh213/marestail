#!/usr/bin/env python3
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import harness

harness.use_tree()

from marestail import javascript
from marestail.config import Config
from marestail.context import Context
from marestail.gates import py_crap, ts_crap

FILES = 20
CHANGED_FILES = 10
FUNCTIONS = 30
SPAN = 10


def start_of(index: int) -> int:
    return 1 + index * SPAN


def source_text() -> str:
    return "".join(f"line {number}\n" for number in range(1, FUNCTIONS * SPAN + 1))


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def make_repo() -> Path:
    root = Path(tempfile.mkdtemp())
    (root / "app").mkdir()
    (root / "src").mkdir()
    for number in range(FILES):
        (root / "app" / f"mod{number}.py").write_text(source_text())
        (root / "src" / f"mod{number}.ts").write_text(source_text())
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "-c", "user.name=perf", "-c", "user.email=perf@example.com", "commit", "-q", "-m", "seed")
    (root / ".marestail").mkdir()
    return root


repo = make_repo()
raw = {"git": {"base": "HEAD"}, "python": {"root": ".", "sources": ["app"], "crap_max": 4}, "ts": {"root": ".", "crap_max": 4}}
config = Config(root=repo, raw=raw)
py_files = [f"app/mod{number}.py" for number in range(FILES)]
ts_files = [f"src/mod{number}.ts" for number in range(FILES)]
changed_lines = set(range(1, FUNCTIONS * SPAN + 1, 4))


def context(files: list[str], hyper: bool) -> Context:
    touched = files[:CHANGED_FILES]
    ctx = Context(config=config, scope_changed=True, changed=set(touched), changed_lines_map={file: changed_lines for file in touched})
    ctx.hyper = hyper
    return ctx


def py_blocks(lowered: bool) -> list[dict[str, Any]]:
    blocks = []
    for index in range(FUNCTIONS):
        start = start_of(index)
        drop = 1 if lowered and index % 7 == 0 else 0
        inner = {"type": "function", "name": "inner", "lineno": start + 2, "endline": start + 7, "complexity": 2 + index % 10 - drop, "closures": []}
        outer = {"type": "function", "name": f"f{index}", "lineno": start, "endline": start + SPAN - 1, "complexity": 3 + index % 8, "closures": [inner]}
        blocks.append(outer)
    return blocks


def py_file_coverage() -> dict[str, Any]:
    functions: dict[str, Any] = {"": {"start_line": 0, "summary": {"percent_covered": 50.0}}}
    for index in range(FUNCTIONS):
        start = start_of(index)
        functions[f"f{index}"] = {"start_line": start, "summary": {"percent_covered": float(index * 13 % 101)}}
        functions[f"f{index}.<locals>.inner"] = {"start_line": start + 2, "summary": {"percent_covered": float(index * 29 % 101)}}
    return {"functions": functions, "missing_lines": list(range(3, FUNCTIONS * SPAN, 3))}


py_head = json.dumps({file: py_blocks(False) for file in py_files})
py_base = py_blocks(True)
(repo / ".marestail" / py_crap.COVERAGE_JSON).write_text(json.dumps({"files": {file: py_file_coverage() for file in py_files}}))


def fake_radon(command: list[str], cwd: Path | None = None) -> tuple[int, str]:
    if "-e" in command:
        return 0, py_head
    return 0, json.dumps({command[-1]: py_base})


def ts_functions(file: str, lowered: bool) -> list[dict[str, Any]]:
    found = []
    for index in range(FUNCTIONS):
        start = start_of(index)
        drop = 1 if lowered and index % 7 == 0 else 0
        found.append({"file": file, "name": f"f{index}", "line": start, "endLine": start + SPAN - 1, "complexity": 3 + index % 8})
        found.append({"file": file, "name": "inner", "line": start + 2, "endLine": start + 7, "complexity": 2 + index % 10 - drop})
    return found


def ts_file_coverage() -> dict[str, Any]:
    lines = range(1, FUNCTIONS * SPAN + 1)
    statements = {str(line): {"start": {"line": line, "column": 0}, "end": {"line": line, "column": 1}} for line in lines}
    hits = {str(line): 0 if line % 3 == 0 else 1 for line in lines}
    return {"s": hits, "statementMap": statements, "b": {}, "branchMap": {}}


ts_coverage_dir = repo / ".marestail" / "ts-coverage"
ts_coverage_dir.mkdir()
(ts_coverage_dir / "coverage-final.json").write_text(json.dumps({str(repo / file): ts_file_coverage() for file in ts_files}))


def fake_scan(_ctx: Any, _mode: str, files: list[Any], _cwd: Path | None = None) -> tuple[int, str]:
    if isinstance(files[0], Path):
        return 0, json.dumps(ts_functions(str(files[0]), True))
    return 0, json.dumps([fn for file in files for fn in ts_functions(file, False)])


py_crap.run = fake_radon
javascript.scan = fake_scan

harness.emit("py_crap.run_gate changed", harness.measure(lambda: py_crap.run_gate(context(py_files, False))))
harness.emit("py_crap.run_gate hyper", harness.measure(lambda: py_crap.run_gate(context(py_files, True))))
harness.emit("ts_crap.run_gate changed", harness.measure(lambda: ts_crap.run_gate(context(ts_files, False))))
harness.emit("ts_crap.run_gate hyper", harness.measure(lambda: ts_crap.run_gate(context(ts_files, True))))
