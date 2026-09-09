import json
import time
from pathlib import Path

from marestail.context import Context
from marestail.gates.gl_tests import COVERAGE_JSON, relative_path
from marestail.gleam import gleam_sources, scan
from marestail.report import Result


def run_gate(ctx: Context) -> Result:
    started = time.time()
    coverage_path = ctx.work / COVERAGE_JSON
    if not coverage_path.exists():
        return Result("gl.crap", False, "no coverage data; gl.tests must run first", [], 0.0)
    coverage = json.loads(coverage_path.read_text())
    files = gleam_sources(ctx)
    if ctx.scope_changed:
        files = [path for path in files if str(path.relative_to(ctx.root)) in ctx.changed]
    if not files:
        return Result.skipped("gl.crap", "no files in scope")
    code, output = scan(ctx, "complexity", files)
    if code != 0:
        return Result("gl.crap", False, "complexity scanner failed", output.splitlines()[-10:], time.time() - started)
    try:
        functions = json.loads(output or "[]")
    except json.JSONDecodeError:
        return Result("gl.crap", False, "complexity scanner produced non-JSON", output.splitlines()[-10:], time.time() - started)
    limit = float(ctx.gleam("crap_max", 4))
    scored = [score(fn, coverage["files"], ctx) for fn in functions]
    offenders = sorted((row for row in scored if row["crap"] > limit), key=lambda row: -row["crap"])
    summary = f"{len(scored)} functions, {len(offenders)} above CRAP {limit:g}"
    return Result("gl.crap", not offenders, summary, [describe(row) for row in offenders], time.time() - started)


def score(fn: dict, files: dict, ctx: Context) -> dict:
    complexity = int(fn["complexity"])
    rel = relative_path(fn["file"], ctx)
    file_cov = files.get(rel) or files.get(str(Path(fn["file"]))) or {}
    covered_ratio = function_coverage(fn, file_cov)
    crap = complexity**2 * (1 - covered_ratio) ** 3 + complexity
    return {
        "file": rel,
        "line": fn["line"],
        "name": fn["name"],
        "cc": complexity,
        "cov": covered_ratio,
        "crap": crap,
    }


def function_coverage(fn: dict, file_cov: dict) -> float:
    start = int(fn.get("line", 1))
    end = int(fn.get("end_line", start))
    hits = file_cov.get("line_hits") or []
    relevant = [row for row in hits if start <= row.get("line", 0) <= end]
    if relevant:
        covered = sum(1 for row in relevant if row.get("hits", 0) > 0)
        return covered / len(relevant)
    if not file_cov:
        return 0.0
    missing = set(file_cov.get("missing_lines") or [])
    span = list(range(start, end + 1))
    span_missing = [line for line in span if line in missing]
    if span_missing:
        return max(0.0, 1.0 - len(span_missing) / max(len(span), 1))
    return file_cov.get("percent_covered", 100.0) / 100.0


def describe(row: dict) -> str:
    return f"{row['file']}:{row['line']} {row['name']} crap={row['crap']:.1f} (cc={row['cc']}, coverage={row['cov']:.0%})"
