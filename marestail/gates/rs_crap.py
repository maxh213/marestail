import re
import time

from marestail import rust
from marestail.context import Context
from marestail.report import Result


def run_gate(ctx: Context) -> Result:
    started = time.time()
    coverage = rust.load_coverage(ctx)
    if coverage is None:
        return Result("rs.crap", False, "no coverage data; rs.tests must run first", [], 0.0)
    ignored = ctx.rust("coverage_ignore_regex")
    files = [path for path in rust.in_scope(ctx, rust.sources(ctx)) if not (ignored and re.search(ignored, str(path)))]
    if not files:
        return Result.skipped("rs.crap", "no files in scope")
    functions, error = rust.scan(ctx, "complexity", files)
    if error:
        return Result("rs.crap", False, "complexity scanner failed", [error], time.time() - started)
    limit = float(ctx.rust("crap_max", 4))
    scored = [score(fn, coverage["files"].get(rust.rel(ctx, fn["file"]), {}), ctx) for fn in functions]
    offenders = sorted((f for f in scored if f["crap"] > limit), key=lambda f: -f["crap"])
    summary = f"{len(scored)} functions, {len(offenders)} above CRAP {limit:g}"
    return Result("rs.crap", not offenders, summary, [describe(f) for f in offenders], time.time() - started)


def score(fn: dict, file_cov: dict, ctx: Context) -> dict:
    lines = file_cov.get("lines", {})
    measured = [hits for number, hits in lines.items() if fn["line"] <= int(number) <= fn["end"]]
    covered = sum(1 for hits in measured if hits > 0) / len(measured) if measured else 0.0
    complexity = fn["complexity"]
    return {
        "file": rust.rel(ctx, fn["file"]),
        "line": fn["line"],
        "name": fn["name"],
        "cc": complexity,
        "cov": covered,
        "crap": complexity**2 * (1 - covered) ** 3 + complexity,
    }


def describe(f: dict) -> str:
    return f"{f['file']}:{f['line']} {f['name']} crap={f['crap']:.1f} (cc={f['cc']}, coverage={f['cov']:.0%})"
