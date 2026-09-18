import time
from typing import Any

from marestail import dotnet
from marestail.context import Context
from marestail.report import Result

GATE = "cs.crap"


def run_gate(ctx: Context) -> Result:
    started = time.time()
    coverage = dotnet.load_coverage(ctx)
    if coverage is None:
        return Result(GATE, False, "no coverage data; cs.tests must run first", [], 0.0)
    files = dotnet.in_scope(ctx, dotnet.sources(ctx))
    if not files:
        return Result.skipped(GATE, "no C# files in scope")
    members, error = dotnet.scan(ctx, "complexity", files)
    if error:
        return Result(GATE, False, error, [], time.time() - started)
    return verdict(ctx, scoped_members(ctx, members), coverage, started)


def scoped_members(ctx: Context, members: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not ctx.scoped:
        return members
    return [member for member in members if touches_hunk(member, ctx)]


def verdict(ctx: Context, members: list[dict[str, Any]], coverage: dict[str, Any], started: float) -> Result:
    limit = float(ctx.dotnet("crap_max", 4))
    scored = [score(ctx, member, coverage) for member in members]
    worst = offenders(scored, limit)
    summary = f"{len(scored)} members, {len(worst)} above CRAP {limit:g}"
    return Result(GATE, not worst, summary, [describe(f) for f in worst], time.time() - started)


def offenders(scored: list[dict[str, Any]], limit: float) -> list[dict[str, Any]]:
    return sorted((f for f in scored if f["crap"] > limit), key=lambda f: -f["crap"])


def touches_hunk(member: dict[str, Any], ctx: Context) -> bool:
    gated = ctx.gated_lines(member["file"])
    if gated is None:
        return True
    return any(member["startLine"] <= line <= member["endLine"] for line in gated)


def score(ctx: Context, member: dict[str, Any], coverage: dict[str, Any]) -> dict[str, Any]:
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


def window(file_cov: dict[str, Any], start: int, end: int) -> float:
    rows = [hits for line, hits in file_cov.get("lines", {}).items() if start <= int(line) <= end]
    if not rows:
        return 0.0
    return hit_count(rows) / (len(rows) + missed_branches(file_cov, start, end))


def hit_count(rows: list[int]) -> int:
    return sum(1 for hits in rows if hits > 0)


def missed_branches(file_cov: dict[str, Any], start: int, end: int) -> int:
    return sum(1 for line, _ in file_cov.get("missing_branches", []) if start <= line <= end)


def describe(f: dict[str, Any]) -> str:
    return f"{f['file']}:{f['line']} {f['name']} crap={f['crap']:.1f} (cc={f['cc']}, coverage={f['cov']:.0%})"
