import time
from typing import Any

from marestail import java
from marestail.context import Context
from marestail.gates._crap import DEFAULT as DEFAULT
from marestail.gates._crap import KEY as KEY
from marestail.report import Result, elapsed

GATE = "java.crap"
FILE = "file"
START = "startLine"
END = "endLine"
CRAP = "crap"
COVERED = 1.0


def run_gate(ctx: Context) -> Result:
    started = time.time()
    coverage = java.load_coverage(ctx)
    if coverage is None:
        return Result(GATE, False, "no coverage data; java.tests must run first")
    files = java.in_scope(ctx, java.sources(ctx))
    if not files:
        return Result.skipped(GATE, "no Java files in scope")
    members, error = java.scan(ctx, "complexity", files)
    if error:
        return Result(GATE, False, error, [], elapsed(started))
    return judge(ctx, gated_members(ctx, members), coverage, started)


def gated_members(ctx: Context, members: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not ctx.scoped:
        return members
    return [member for member in members if touches_hunk(member, ctx)]


def judge(ctx: Context, members: list[dict[str, Any]], coverage: dict[str, Any], started: float) -> Result:
    limit = float(ctx.java(KEY, DEFAULT))
    scored = [score(ctx, member, coverage) for member in members]
    offenders = worst(scored, limit)
    summary = f"{len(scored)} methods, {len(offenders)} above CRAP {limit:g}"
    return Result(GATE, not offenders, summary, [describe(f) for f in offenders], elapsed(started))


def worst(scored: list[dict[str, Any]], limit: float) -> list[dict[str, Any]]:
    return sorted((f for f in scored if f[CRAP] > limit), key=lambda f: -f[CRAP])


def touches_hunk(member: dict[str, Any], ctx: Context) -> bool:
    gated = ctx.gated_lines(member[FILE])
    if gated is None:
        return True
    return any(member[START] <= line <= member[END] for line in gated)


def score(ctx: Context, member: dict[str, Any], coverage: dict[str, Any]) -> dict[str, Any]:
    complexity = member["complexity"]
    covered = (
        COVERED
        if java.coverage_excluded(ctx, member[FILE])
        else window(coverage["files"].get(member[FILE], {}), member[START], member[END])
    )
    return {
        FILE: member[FILE],
        "line": member["line"],
        "name": member["name"],
        "cc": complexity,
        "cov": covered,
        CRAP: complexity**2 * (1 - covered) ** 3 + complexity,
    }


def lines_in(file_cov: dict[str, Any], start: int, end: int) -> list[int]:
    return [hits for line, hits in file_cov.get("lines", {}).items() if start <= int(line) <= end]


def branches_missed(file_cov: dict[str, Any], start: int, end: int) -> int:
    return sum(missed for line, missed, _ in file_cov.get("missing_branches", []) if start <= line <= end)


def window(file_cov: dict[str, Any], start: int, end: int) -> float:
    rows = lines_in(file_cov, start, end)
    misses = branches_missed(file_cov, start, end)
    if not rows:
        return 0.0
    return sum(1 for hits in rows if hits > 0) / (len(rows) + misses)


def describe(f: dict[str, Any]) -> str:
    return f"{f[FILE]}:{f['line']} {f['name']} crap={f['crap']:.1f} (cc={f['cc']}, coverage={f['cov']:.0%})"
