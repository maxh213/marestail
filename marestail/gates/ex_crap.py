import json
import time
from pathlib import Path

from marestail.context import Context
from marestail.gates.ex_tests import COVERAGE_JSON, relative_path
from marestail.report import Result
from marestail.shell import run

SCRIPT = Path(__file__).resolve().parent.parent / "ex" / "complexity.exs"


def run_gate(ctx: Context) -> Result:
    started = time.time()
    coverage_path = ctx.work / COVERAGE_JSON
    if not coverage_path.exists():
        return Result("ex.crap", False, "no coverage data; ex.tests must run first", [], 0.0)
    coverage = json.loads(coverage_path.read_text())
    root = ctx.elixir_root()
    files = [Path(f) for f in coverage.get("files", {}) if (root / f).exists() or Path(f).exists()]
    if ctx.scope_changed:
        files = [f for f in files if relative_path(str(f), ctx) in ctx.changed]
    if not files:
        return Result.skipped("ex.crap", "no files in scope")
    code, output = run(["elixir", str(SCRIPT), *map(str, files)], cwd=root, timeout=600)
    if code != 0:
        return Result("ex.crap", False, "complexity script failed", output.splitlines()[-10:], time.time() - started)
    functions = json.loads(output)
    limit = float(ctx.elixir("crap_max", 4))
    scored_functions = [score(fn, coverage["files"].get(fn["file"], {}), ctx) for fn in functions]
    offenders = sorted((f for f in scored_functions if f["crap"] > limit), key=lambda f: -f["crap"])
    summary = f"{len(scored_functions)} functions, {len(offenders)} above CRAP {limit:g}"
    return Result("ex.crap", not offenders, summary, [describe(f) for f in offenders], time.time() - started)


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
