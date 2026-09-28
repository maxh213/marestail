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
from marestail.gates._crap import file_percent_score, scored_functions
from marestail.gates._hyper_crap import Hyper, judged
from marestail.report import Result, elapsed

COVERAGE_JSON = EX_COVERAGE
GATE = "ex.crap"


def run_gate(ctx: Context) -> Result:
    started = time.time()
    coverage_path = ctx.work / COVERAGE_JSON
    if not coverage_path.exists():
        return Result(GATE, False, "no coverage data; ex.tests must run first")
    coverage = json.loads(coverage_path.read_text())
    root = ctx.elixir_root()
    files = files_in_scope(coverage, root, ctx)
    if not files:
        return Result.skipped(GATE, "no files in scope")
    code, output = elixir.scan(ctx, "complexity", files, timeout=600)
    if code != 0:
        return Result(GATE, False, "complexity script failed", output.splitlines()[-10:], elapsed(started))
    return verdict(ctx, coverage, json.loads(output), started)


def verdict(ctx: Context, coverage: dict[str, Any], functions: list[dict[str, Any]], started: float) -> Result:
    limit = float(ctx.elixir(KEY, DEFAULT))
    if ctx.hyper:
        return judged(ctx, Hyper(GATE, limit, lambda copy: base_units(ctx, copy)), hyper_units(ctx, coverage, functions), started)
    return crap_result(GATE, scored_functions(hunk_functions(functions, ctx), coverage, ctx), limit, started)


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


def unit(file: str, fn: dict[str, Any]) -> dict[str, Any]:
    start, end = span(fn)
    return {"file": file, "line": fn["line"], "start": start, "end": end, "name": fn["name"], "label": fn["name"], "cc": fn["complexity"]}


def hyper_units(ctx: Context, coverage: dict[str, Any], functions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [covered_unit(ctx, fn, coverage["files"].get(fn["file"], {})) for fn in functions]


def covered_unit(ctx: Context, fn: dict[str, Any], file_cov: dict[str, Any]) -> dict[str, Any]:
    covered = file_percent_score(fn, file_cov, ctx)["cov"]
    return {**unit(relative_path(fn["file"], ctx), fn), "cov": covered, "missing": set(file_cov.get("missing_lines", []))}


def base_units(ctx: Context, copy: Path) -> list[dict[str, Any]] | None:
    code, output = elixir.scan(ctx, "complexity", [copy], timeout=600)
    if code != 0:
        return None
    return [unit("", fn) for fn in json.loads(output)]
