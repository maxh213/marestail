import json
import time
from typing import Any

from marestail import javascript
from marestail.context import Context
from marestail.gates._coverage import TS_COVERAGE_DIR
from marestail.gates._crap import above as above
from marestail.gates._crap import describe as describe
from marestail.report import Result

GATE = "ts.crap"


def run_gate(ctx: Context) -> Result:
    started = time.time()
    coverage_path = ctx.work / TS_COVERAGE_DIR / "coverage-final.json"
    if not coverage_path.exists():
        return Result(GATE, False, "no coverage data; ts.tests must run first", [], 0.0)
    coverage = json.loads(coverage_path.read_text())
    files = scoped_files(coverage, ctx)
    if not files:
        return Result.skipped(GATE, "no files in scope")
    code, output = javascript.scan(ctx, "complexity", files)
    if code != 0:
        return Result(GATE, False, "complexity script failed", output.splitlines()[-10:], time.time() - started)
    return crap_result(ctx, coverage, json.loads(output), started)


def scoped_files(coverage: dict[str, Any], ctx: Context) -> list[str]:
    return [file for file in coverage if ctx.in_scope(javascript.rel(file, ctx))]


def crap_result(ctx: Context, coverage: dict[str, Any], parsed: list[dict[str, Any]], started: float) -> Result:
    limit = float(ctx.ts("crap_max", 4))
    functions = [score(fn, coverage[fn["file"]], ctx) for fn in parsed if touches_hunk(fn, ctx)]
    worst = above(functions, limit)
    summary = f"{len(functions)} functions, {len(worst)} above CRAP {limit:g}"
    return Result(GATE, not worst, summary, [describe(fn, "complexity") for fn in worst], time.time() - started)


def touches_hunk(fn: dict[str, Any], ctx: Context) -> bool:
    gated = ctx.gated_lines(javascript.rel(fn["file"], ctx))
    if gated is None:
        return True
    return any(fn["line"] <= line <= fn["endLine"] for line in gated)


def score(fn: dict[str, Any], data: dict[str, Any], ctx: Context) -> dict[str, Any]:
    covered = function_coverage(fn, data)
    complexity = fn["complexity"]
    crap = complexity**2 * (1 - covered) ** 3 + complexity
    return {**fn, "file": javascript.rel(fn["file"], ctx), "cov": covered, "crap": crap}


def function_coverage(fn: dict[str, Any], data: dict[str, Any]) -> float:
    return ratio(statement_hits(fn, data) + arm_hits(fn, data))


def inside(fn: dict[str, Any], loc: dict[str, Any]) -> bool:
    return bool(fn["line"] <= loc["start"]["line"] <= fn["endLine"])


def statement_hits(fn: dict[str, Any], data: dict[str, Any]) -> list[int]:
    return [hits for key, hits in data["s"].items() if inside(fn, data["statementMap"][key])]


def arm_hits(fn: dict[str, Any], data: dict[str, Any]) -> list[int]:
    return [hit for key, hits in data["b"].items() if inside(fn, data["branchMap"][key]["loc"]) for hit in hits]


def ratio(hits: list[int]) -> float:
    if not hits:
        return 1.0
    return sum(1 for hit in hits if hit > 0) / len(hits)
