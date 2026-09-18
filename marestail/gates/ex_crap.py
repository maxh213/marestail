import json
import time
from pathlib import Path
from typing import Any

from marestail.context import Context
from marestail.gates.er_crap import crap_result, scored_functions
from marestail.gates.er_tests import relative_path
from marestail.gates.ex_tests import COVERAGE_JSON
from marestail.report import Result
from marestail.shell import run

GATE = "ex.crap"
SCRIPT = Path(__file__).resolve().parent.parent / "ex" / "complexity.exs"


def run_gate(ctx: Context) -> Result:
    started = time.time()
    coverage_path = ctx.work / COVERAGE_JSON
    if not coverage_path.exists():
        return Result(GATE, False, "no coverage data; ex.tests must run first", [], 0.0)
    coverage = json.loads(coverage_path.read_text())
    root = ctx.elixir_root()
    files = files_in_scope(coverage, root, ctx)
    if not files:
        return Result.skipped(GATE, "no files in scope")
    code, output = run(["elixir", str(SCRIPT), *map(str, files)], cwd=root, timeout=600)
    if code != 0:
        return Result(GATE, False, "complexity script failed", output.splitlines()[-10:], time.time() - started)
    functions = hunk_functions(json.loads(output), ctx)
    return crap_result(GATE, scored_functions(functions, coverage, ctx), float(ctx.elixir("crap_max", 4)), started)


def files_in_scope(coverage: dict[str, Any], root: Path, ctx: Context) -> list[Path]:
    return [path for path in existing_files(coverage, root) if ctx.in_scope(relative_path(str(path), ctx))]


def existing_files(coverage: dict[str, Any], root: Path) -> list[Path]:
    return [Path(name) for name in coverage.get("files", {}) if (root / name).exists() or Path(name).exists()]


def hunk_functions(functions: list[dict[str, Any]], ctx: Context) -> list[dict[str, Any]]:
    return [fn for fn in functions if in_hunks(fn, ctx.gated_lines(relative_path(fn["file"], ctx)))]


def in_hunks(fn: dict[str, Any], gated: set[int] | None) -> bool:
    if gated is None:
        return True
    start, end = span(fn)
    return any(start <= line <= end for line in gated)


def span(fn: dict[str, Any]) -> tuple[int, int]:
    start = int(fn.get("line") or 0)
    return start, int(fn.get("end_line") or start)
