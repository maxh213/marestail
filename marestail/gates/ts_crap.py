import json
import time
from pathlib import Path
from typing import Any

from marestail.context import Context
from marestail.gates.ts_tests import COVERAGE_DIR
from marestail.report import Result
from marestail.shell import run

SCRIPT = Path(__file__).resolve().parent.parent.parent / "scanners" / "js" / "ts_complexity.mjs"
GATE = "ts.crap"


def run_gate(ctx: Context) -> Result:
    started = time.time()
    coverage_path = ctx.work / COVERAGE_DIR / "coverage-final.json"
    if not coverage_path.exists():
        return Result(GATE, False, "no coverage data; ts.tests must run first", [], 0.0)
    coverage = json.loads(coverage_path.read_text())
    files = scoped_files(coverage, ctx)
    if not files:
        return Result.skipped(GATE, "no files in scope")
    code, output = run(["node", str(SCRIPT), str(ctx.ts_root()), *files], cwd=ctx.ts_root())
    if code != 0:
        return Result(GATE, False, "complexity script failed", output.splitlines()[-10:], time.time() - started)
    return crap_result(ctx, coverage, json.loads(output), started)


def scoped_files(coverage: dict[str, Any], ctx: Context) -> list[str]:
    return [file for file in coverage if ctx.in_scope(relative(file, ctx))]


def crap_result(ctx: Context, coverage: dict[str, Any], parsed: list[dict[str, Any]], started: float) -> Result:
    limit = float(ctx.ts("crap_max", 4))
    functions = [score(fn, coverage[fn["file"]], ctx) for fn in parsed if touches_hunk(fn, ctx)]
    worst = offenders(functions, limit)
    summary = f"{len(functions)} functions, {len(worst)} above CRAP {limit:g}"
    return Result(GATE, not worst, summary, [describe(f) for f in worst], time.time() - started)


def offenders(functions: list[dict[str, Any]], limit: float) -> list[dict[str, Any]]:
    return sorted((f for f in functions if f["crap"] > limit), key=lambda f: -f["crap"])


def touches_hunk(fn: dict[str, Any], ctx: Context) -> bool:
    gated = ctx.gated_lines(relative(fn["file"], ctx))
    if gated is None:
        return True
    return any(fn["line"] <= line <= fn["endLine"] for line in gated)


def relative(file: str, ctx: Context) -> str:
    return str(Path(file).resolve().relative_to(ctx.root))


def score(fn: dict[str, Any], data: dict[str, Any], ctx: Context) -> dict[str, Any]:
    covered = function_coverage(fn, data)
    complexity = fn["complexity"]
    crap = complexity**2 * (1 - covered) ** 3 + complexity
    return {**fn, "file": relative(fn["file"], ctx), "cov": covered, "crap": crap}


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


def describe(f: dict[str, Any]) -> str:
    return f"{f['file']}:{f['line']} {f['name']} crap={f['crap']:.1f} (cc={f['complexity']}, coverage={f['cov']:.0%})"
