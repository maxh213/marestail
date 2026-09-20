import re
import time
from pathlib import Path
from typing import Any

from marestail import rust
from marestail.context import Context
from marestail.gates._crap import above, describe
from marestail.report import Result

GATE = "rs.crap"


def run_gate(ctx: Context) -> Result:
    started = time.time()
    coverage = rust.load_coverage(ctx)
    if coverage is None:
        return Result(GATE, False, "no coverage data; rs.tests must run first", [], 0.0)
    files = crap_files(ctx)
    if not files:
        return Result.skipped(GATE, "no files in scope")
    functions, error = rust.scan(ctx, "complexity", files)
    if error:
        return Result(GATE, False, "complexity scanner failed", [error], time.time() - started)
    return crap_result(ctx, coverage, functions, started)


def crap_files(ctx: Context) -> list[Path]:
    ignored = ctx.rust("coverage_ignore_regex")
    return [path for path in rust.in_scope(ctx, rust.sources(ctx)) if not ignored_path(ignored, path)]


def ignored_path(ignored: str | None, path: Path) -> bool:
    return bool(ignored and re.search(ignored, str(path)))


def crap_result(ctx: Context, coverage: dict[str, Any], functions: list[Any] | None, started: float) -> Result:
    limit = float(ctx.rust("crap_max", 4))
    scored = [score(fn, coverage["files"].get(rust.rel(ctx, fn["file"]), {}), ctx) for fn in functions or []]
    offenders = above(scored, limit)
    summary = f"{len(scored)} functions, {len(offenders)} above CRAP {limit:g}"
    return Result(GATE, not offenders, summary, [describe(f) for f in offenders], time.time() - started)


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
        "crap": complexity**2 * (1 - covered) ** 3 + complexity,
    }
