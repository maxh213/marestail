import json
import time
from pathlib import Path

from marestail.context import Context
from marestail.gates.ts_tests import COVERAGE_DIR
from marestail.report import Result
from marestail.shell import run

SCRIPT = Path(__file__).resolve().parent.parent / "js" / "ts_complexity.mjs"


def run_gate(ctx: Context) -> Result:
    started = time.time()
    coverage_path = ctx.work / COVERAGE_DIR / "coverage-final.json"
    if not coverage_path.exists():
        return Result("ts.crap", False, "no coverage data; ts.tests must run first", [], 0.0)
    coverage = json.loads(coverage_path.read_text())
    files = [file for file in coverage if not ctx.scope_changed or relative(file, ctx) in ctx.changed]
    if not files:
        return Result.skipped("ts.crap", "no files in scope")
    code, output = run(["node", str(SCRIPT), str(ctx.ts_root()), *files], cwd=ctx.ts_root())
    if code != 0:
        return Result("ts.crap", False, "complexity script failed", output.splitlines()[-10:], time.time() - started)
    limit = float(ctx.ts("crap_max", 6))
    functions = [score(fn, coverage[fn["file"]], ctx) for fn in json.loads(output)]
    offenders = sorted((f for f in functions if f["crap"] > limit), key=lambda f: -f["crap"])
    summary = f"{len(functions)} functions, {len(offenders)} above CRAP {limit:g}"
    return Result("ts.crap", not offenders, summary, [describe(f) for f in offenders], time.time() - started)


def relative(file: str, ctx: Context) -> str:
    return str(Path(file).resolve().relative_to(ctx.root))


def score(fn: dict, data: dict, ctx: Context) -> dict:
    covered = function_coverage(fn, data)
    complexity = fn["complexity"]
    crap = complexity**2 * (1 - covered) ** 3 + complexity
    return {**fn, "file": relative(fn["file"], ctx), "cov": covered, "crap": crap}


def function_coverage(fn: dict, data: dict) -> float:
    inside = lambda loc: fn["line"] <= loc["start"]["line"] <= fn["endLine"]
    statements = [hits for key, hits in data["s"].items() if inside(data["statementMap"][key])]
    arms = [hit for key, hits in data["b"].items() if inside(data["branchMap"][key]["loc"]) for hit in hits]
    total = len(statements) + len(arms)
    if total == 0:
        return 1.0
    return (sum(1 for h in statements if h > 0) + sum(1 for h in arms if h > 0)) / total


def describe(f: dict) -> str:
    return f"{f['file']}:{f['line']} {f['name']} crap={f['crap']:.1f} (cc={f['complexity']}, coverage={f['cov']:.0%})"


