import json
import time
from pathlib import Path
from typing import Any

from marestail import elixir
from marestail.context import Context
from marestail.gates._coverage import EX_COVERAGE
from marestail.gates._coverage import relative_path as relative_path
from marestail.gates._crap import DEFAULT as DEFAULT
from marestail.gates._crap import KEY as KEY
from marestail.gates._crap import crap_result as crap_result
from marestail.gates._crap import scored_functions as scored_functions
from marestail.report import Result, elapsed

COVERAGE_JSON = EX_COVERAGE
GATE = "ex.crap"


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
    code, output = elixir.scan(ctx, "complexity", files, timeout=600)
    if code != 0:
        return Result(GATE, False, "complexity script failed", output.splitlines()[-10:], elapsed(started))
    functions = hunk_functions(json.loads(output), ctx)
    return crap_result(GATE, scored_functions(functions, coverage, ctx), float(ctx.elixir(KEY, DEFAULT)), started)


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
