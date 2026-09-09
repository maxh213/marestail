import time

from marestail import dotnet
from marestail.context import Context
from marestail.report import Result


def run_gate(ctx: Context) -> Result:
    started = time.time()
    coverage = dotnet.load_coverage(ctx)
    if coverage is None:
        return Result("cs.crap", False, "no coverage data; cs.tests must run first", [], 0.0)
    files = dotnet.in_scope(ctx, dotnet.sources(ctx))
    if not files:
        return Result.skipped("cs.crap", "no C# files in scope")
    members, error = dotnet.scan(ctx, "complexity", files)
    if error:
        return Result("cs.crap", False, error, [], time.time() - started)
    limit = float(ctx.dotnet("crap_max", 4))
    scored = [score(ctx, member, coverage) for member in members]
    offenders = sorted((f for f in scored if f["crap"] > limit), key=lambda f: -f["crap"])
    summary = f"{len(scored)} members, {len(offenders)} above CRAP {limit:g}"
    return Result("cs.crap", not offenders, summary, [describe(f) for f in offenders], time.time() - started)


def score(ctx: Context, member: dict, coverage: dict) -> dict:
    complexity = member["complexity"]
    covered = window(coverage["files"].get(member["file"], {}), member["startLine"], member["endLine"])
    if dotnet.coverage_excluded(ctx, member["file"]) or not member["hasBody"]:
        covered = 1.0
    return {
        "file": member["file"],
        "line": member["line"],
        "name": member["name"],
        "cc": complexity,
        "cov": covered,
        "crap": complexity**2 * (1 - covered) ** 3 + complexity,
    }


def window(file_cov: dict, start: int, end: int) -> float:
    rows = [hits for line, hits in file_cov.get("lines", {}).items() if start <= int(line) <= end]
    misses = sum(1 for line, _ in file_cov.get("missing_branches", []) if start <= line <= end)
    if not rows:
        return 0.0
    return sum(1 for hits in rows if hits > 0) / (len(rows) + misses)


def describe(f: dict) -> str:
    return f"{f['file']}:{f['line']} {f['name']} crap={f['crap']:.1f} (cc={f['cc']}, coverage={f['cov']:.0%})"
