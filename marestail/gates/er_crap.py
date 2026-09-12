import json
import time
from pathlib import Path

from marestail import erlang
from marestail.context import Context
from marestail.gates.er_tests import COVERAGE_JSON, relative_path
from marestail.report import Result


def run_gate(ctx: Context) -> Result:
    started = time.time()
    coverage_path = ctx.work / COVERAGE_JSON
    if not coverage_path.exists():
        return Result("er.crap", False, "no coverage data; er.tests must run first", [], 0.0)
    coverage = json.loads(coverage_path.read_text())
    files = [Path(f) for f in coverage.get("files", {}) if Path(f).exists()]
    if ctx.scope_changed:
        files = [f for f in files if relative_path(str(f), ctx) in ctx.changed]
    if not files:
        return Result.skipped("er.crap", "no files in scope")
    code, output = erlang.escript(ctx, "complexity.escript", list(map(str, files)), timeout=600)
    problem = erlang.hint(code, output)
    if problem:
        return Result("er.crap", False, problem, [problem], time.time() - started)
    if code != 0:
        return Result("er.crap", False, "complexity script failed", output.splitlines()[-10:], time.time() - started)
    functions = json.loads(output)
    limit = float(ctx.erlang("crap_max", 4))
    scored_functions = [score(fn, coverage["files"].get(fn["file"], {}), ctx) for fn in functions]
    offenders = sorted((f for f in scored_functions if f["crap"] > limit), key=lambda f: -f["crap"])
    summary = f"{len(scored_functions)} functions, {len(offenders)} above CRAP {limit:g}"
    return Result("er.crap", not offenders, summary, [describe(f) for f in offenders], time.time() - started)


def score(fn: dict, file_cov: dict, ctx: Context) -> dict:
    complexity = fn["complexity"]
    covered_ratio = file_cov.get("percent_covered", 100.0) / 100.0
    crap = complexity**2 * (1 - covered_ratio) ** 3 + complexity
    return {
        "file": relative_path(fn["file"], ctx),
        "line": fn["line"],
        "name": fn["name"],
        "complexity": complexity,
        "cov": covered_ratio,
        "crap": crap,
    }


def describe(f: dict) -> str:
    return f"{f['file']}:{f['line']} {f['name']} crap={f['crap']:.1f} (cc={f['complexity']}, coverage={f['cov']:.0%})"
