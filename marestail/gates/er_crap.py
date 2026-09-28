import json
import time
from pathlib import Path
from typing import Any

from marestail import erlang
from marestail.context import Context
from marestail.gates._coverage import ER_COVERAGE
from marestail.gates._coverage import relative_path as relative_path
from marestail.gates._crap import DEFAULT as DEFAULT
from marestail.gates._crap import KEY as KEY
from marestail.gates._crap import crap_result as crap_result
from marestail.gates._crap import file_percent_score, scored_functions
from marestail.gates._hyper_crap import Hyper, judged, unit
from marestail.report import Result, elapsed

COVERAGE_JSON = ER_COVERAGE
GATE = "er.crap"
REPLACE = "replace"
COMPLEXITY_TIMEOUT = 600
ENDS_ERROR = "ends"


def run_gate(ctx: Context) -> Result:
    started = time.time()
    coverage_path = ctx.work / COVERAGE_JSON
    if not coverage_path.exists():
        return Result(GATE, False, "no coverage data; er.tests must run first")
    coverage = json.loads(coverage_path.read_text())
    files = files_in_scope(coverage, ctx)
    if not files:
        return Result.skipped(GATE, "no files in scope")
    code, output = erlang.escript(ctx, "complexity.escript", list(map(str, files)), timeout=COMPLEXITY_TIMEOUT)
    failed = erlang.trouble(code, output, "complexity script failed", output.splitlines()[-10:])
    if failed:
        return Result(GATE, False, *failed, elapsed(started))
    return verdict(ctx, coverage, json.loads(output), started)


def verdict(ctx: Context, coverage: dict[str, Any], functions: list[dict[str, Any]], started: float) -> Result:
    limit = float(ctx.erlang(KEY, DEFAULT))
    if ctx.hyper:
        return judged(ctx, Hyper(GATE, limit, lambda copy: base_units(ctx, copy)), hyper_units(ctx, coverage, functions), started)
    return crap_result(GATE, scored_functions(scoped_functions(functions, ctx), coverage, ctx), limit, started)


def files_in_scope(coverage: dict[str, Any], ctx: Context) -> list[Path]:
    return [path for path in existing_files(coverage) if ctx.in_scope(relative_path(str(path), ctx))]


def existing_files(coverage: dict[str, Any]) -> list[Path]:
    return [Path(name) for name in coverage.get("files", {}) if Path(name).exists()]


def scoped_functions(functions: list[dict[str, Any]], ctx: Context) -> list[dict[str, Any]]:
    if not ctx.scoped:
        return functions
    ends = function_ends(functions)
    return [fn for fn in functions if touches_hunk(fn, ends, ctx)]


def function_ends(functions: list[dict[str, Any]]) -> dict[tuple[str, int], int]:
    by_file: dict[str, list[int]] = {}
    for fn in functions:
        by_file.setdefault(fn["file"], []).append(fn["line"])
    ends: dict[tuple[str, int], int] = {}
    for file, lines in by_file.items():
        ends.update(file_ends(file, lines))
    return ends


def file_ends(file: str, lines: list[int]) -> dict[tuple[str, int], int]:
    ordered = sorted(lines)
    total = len(Path(file).read_text(errors=REPLACE).splitlines())
    bounds = [line - 1 for line in ordered[1:]] + [total]
    return paired_ends(file, ordered, bounds)


def paired_ends(file: str, starts: list[int], ends: list[int]) -> dict[tuple[str, int], int]:
    if len(starts) != len(ends):
        raise ValueError(ENDS_ERROR)
    return {(file, starts[index]): ends[index] for index in range(len(starts))}


def touches_hunk(fn: dict[str, Any], ends: dict[tuple[str, int], int], ctx: Context) -> bool:
    gated = ctx.gated_lines(relative_path(fn["file"], ctx))
    if gated is None:
        return True
    end = ends[(fn["file"], fn["line"])]
    return any(fn["line"] <= line <= end for line in gated)


def ranged(file: str, functions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ends = function_ends(functions)
    return [er_unit(file, fn, ends[(fn["file"], fn["line"])]) for fn in functions]


def er_unit(file: str, fn: dict[str, Any], end: int) -> dict[str, Any]:
    return unit(file, fn, fn["line"], end)


def hyper_units(ctx: Context, coverage: dict[str, Any], functions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ends = function_ends(functions)
    return [covered_unit(ctx, fn, ends[(fn["file"], fn["line"])], coverage["files"].get(fn["file"], {})) for fn in functions]


def covered_unit(ctx: Context, fn: dict[str, Any], end: int, file_cov: dict[str, Any]) -> dict[str, Any]:
    covered = file_percent_score(fn, file_cov, ctx)["cov"]
    return {**er_unit(relative_path(fn["file"], ctx), fn, end), "cov": covered, "missing": set(file_cov.get("missing_lines", []))}


def base_units(ctx: Context, copy: Path) -> list[dict[str, Any]] | None:
    code, output = erlang.escript(ctx, "complexity.escript", [str(copy)], timeout=COMPLEXITY_TIMEOUT)
    if erlang.trouble(code, output, "complexity script failed"):
        return None
    return ranged("", json.loads(output))
