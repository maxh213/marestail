import re
import time
from pathlib import Path
from typing import Any

from marestail import rust
from marestail.context import Context
from marestail.gates._crap import DEFAULT as DEFAULT
from marestail.gates._crap import KEY as KEY
from marestail.gates._crap import above as above
from marestail.gates._crap import crap_score
from marestail.gates._crap import describe as describe
from marestail.gates._hyper_crap import Hyper, judged, unhit_lines, unit
from marestail.report import Result, elapsed

GATE = "rs.crap"


def run_gate(ctx: Context) -> Result:
    started = time.time()
    coverage = rust.load_coverage(ctx)
    if coverage is None:
        return Result(GATE, False, "no coverage data; rs.tests must run first")
    files = crap_files(ctx)
    if not files:
        return Result.skipped(GATE, "no files in scope")
    functions, error = rust.scan(ctx, "complexity", files)
    if error:
        return Result(GATE, False, "complexity scanner failed", [error], elapsed(started))
    return crap_result(ctx, coverage, functions, started)


def crap_files(ctx: Context) -> list[Path]:
    ignored = ctx.rust("coverage_ignore_regex")
    return [path for path in rust.in_scope(ctx, rust.sources(ctx)) if not ignored_path(ignored, path)]


def ignored_path(ignored: str | None, path: Path) -> bool:
    return bool(ignored and re.search(ignored, str(path)))


def crap_result(ctx: Context, coverage: dict[str, Any], functions: list[Any] | None, started: float) -> Result:
    limit = float(ctx.rust(KEY, DEFAULT))
    if ctx.hyper:
        return judged(ctx, Hyper(GATE, limit, lambda copy: base_units(ctx, copy)), hyper_units(ctx, coverage, functions or []), started)
    scored = scored_functions(ctx, coverage, functions)
    offenders = above(scored, limit)
    summary = f"{len(scored)} functions, {len(offenders)} above CRAP {limit:g}"
    return Result(GATE, not offenders, summary, [describe(f) for f in offenders], elapsed(started))


def scored_functions(ctx: Context, coverage: dict[str, Any], functions: list[Any] | None) -> list[dict[str, Any]]:
    return [score(fn, coverage["files"].get(rust.rel(ctx, fn["file"]), {}), ctx) for fn in functions or []]


def measured(lines: dict[str, int], start: int, end: int) -> list[int]:
    return [hits for number, hits in lines.items() if start <= int(number) <= end]


def covered_share(hits_list: list[int]) -> float:
    return sum(1 for hits in hits_list if hits > 0) / len(hits_list) if hits_list else 0.0


def score(fn: dict[str, Any], file_cov: dict[str, Any], ctx: Context) -> dict[str, Any]:
    covered = covered_share(measured(file_cov.get("lines", {}), fn["line"], fn["end"]))
    complexity = fn["complexity"]
    return {
        "file": rust.rel(ctx, fn["file"]),
        "line": fn["line"],
        "name": fn["name"],
        "cc": complexity,
        "cov": covered,
        "crap": crap_score(complexity, covered),
    }


def hyper_units(ctx: Context, coverage: dict[str, Any], functions: list[Any]) -> list[dict[str, Any]]:
    return [covered_unit(rs_unit(rust.rel(ctx, fn["file"]), fn), coverage["files"].get(rust.rel(ctx, fn["file"]), {})) for fn in functions]


def rs_unit(file: str, fn: dict[str, Any]) -> dict[str, Any]:
    return unit(file, fn, fn["line"], fn["end"])


def covered_unit(unit: dict[str, Any], file_cov: dict[str, Any]) -> dict[str, Any]:
    lines = file_cov.get("lines", {})
    return {**unit, "cov": covered_share(measured(lines, unit["start"], unit["end"])), "missing": unhit_lines(lines)}


def base_units(ctx: Context, copy: Path) -> list[dict[str, Any]] | None:
    functions, _ = rust.scan(ctx, "complexity", [copy])
    if functions is None:
        return None
    return [rs_unit("", fn) for fn in functions]
