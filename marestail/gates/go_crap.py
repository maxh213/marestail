import time
from pathlib import Path
from typing import Any

from marestail import golang
from marestail.context import Context
from marestail.gates.rs_crap import DEFAULT, KEY, Hyper, above, crap_score, describe, judged, unhit_lines, unit
from marestail.report import Result, elapsed

GATE = "go.crap"
BLOCKS = "blocks"
FILES = "files"


def run_gate(ctx: Context) -> Result:
    started = time.time()
    coverage = golang.load_coverage(ctx)
    if coverage is None:
        return Result(GATE, False, "no coverage data; go.tests must run first")
    files = crap_files(ctx)
    if not files:
        return Result.skipped(GATE, "no files in scope")
    return scanned(ctx, coverage, files, started)


def crap_files(ctx: Context) -> list[Path]:
    return [
        path for path in golang.in_scope(ctx, golang.sources(ctx)) if not golang.excluded(ctx, golang.rel(ctx, path), "coverage_exclude")
    ]


def scanned(ctx: Context, coverage: dict[str, Any], files: list[Path], started: float) -> Result:
    functions, error = golang.scan(ctx, "complexity", files)
    if error:
        return Result(GATE, False, "complexity scanner failed", [error], elapsed(started))
    return crap_result(ctx, coverage, functions or [], started)


def crap_result(ctx: Context, coverage: dict[str, Any], functions: list[Any], started: float) -> Result:
    limit = float(ctx.go(KEY, DEFAULT))
    if ctx.hyper:
        return judged(ctx, Hyper(GATE, limit, lambda copy: base_units(ctx, copy)), hyper_units(ctx, coverage, functions), started)
    scored = scored_functions(ctx, coverage, functions)
    offenders = above(scored, limit)
    summary = f"{len(scored)} functions, {len(offenders)} above CRAP {limit:g}"
    return Result(GATE, not offenders, summary, [describe(entry) for entry in offenders], elapsed(started))


def scored_functions(ctx: Context, coverage: dict[str, Any], functions: list[Any]) -> list[dict[str, Any]]:
    return [score(ctx, fn, coverage[FILES].get(golang.rel(ctx, fn["file"]), {})) for fn in functions]


def score(ctx: Context, fn: dict[str, Any], file_cov: dict[str, Any]) -> dict[str, Any]:
    covered = statement_share(file_cov.get(BLOCKS, []), fn["line"], fn["end"])
    complexity = fn["complexity"]
    return {
        "file": golang.rel(ctx, fn["file"]),
        "line": fn["line"],
        "name": fn["name"],
        "cc": complexity,
        "cov": covered,
        "crap": crap_score(complexity, covered),
    }


def statement_share(blocks: list[list[int]], start: int, end: int) -> float:
    chosen = inside(blocks, start, end)
    total = statements(chosen)
    if total == 0:
        return 0.0
    return hit_statements(chosen) / total


def inside(blocks: list[list[int]], start: int, end: int) -> list[list[int]]:
    return [block for block in blocks if start <= block[0] <= end]


def statements(blocks: list[list[int]]) -> int:
    return sum(block[4] for block in blocks)


def hit_statements(blocks: list[list[int]]) -> int:
    return sum(block[4] for block in blocks if block[5] > 0)


def hyper_units(ctx: Context, coverage: dict[str, Any], functions: list[Any]) -> list[dict[str, Any]]:
    return [
        covered_unit(go_unit(golang.rel(ctx, fn["file"]), fn), coverage[FILES].get(golang.rel(ctx, fn["file"]), {})) for fn in functions
    ]


def go_unit(file: str, fn: dict[str, Any]) -> dict[str, Any]:
    return unit(file, fn, fn["line"], fn["end"])


def covered_unit(found: dict[str, Any], file_cov: dict[str, Any]) -> dict[str, Any]:
    lines = file_cov.get("lines", {})
    return {**found, "cov": statement_share(file_cov.get(BLOCKS, []), found["start"], found["end"]), "missing": unhit_lines(lines)}


def base_units(ctx: Context, copy: Path) -> list[dict[str, Any]] | None:
    functions, error = golang.scan(ctx, "complexity", [copy])
    if error or functions is None:
        return None
    return [go_unit("", fn) for fn in functions]
