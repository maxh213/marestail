from typing import Any

from marestail.context import Context
from marestail.gates._coverage import relative_path
from marestail.report import Result, elapsed

Scored = dict[str, Any]
KEY = "crap_max"
DEFAULT = 4


def above(scored: list[Scored], limit: float) -> list[Scored]:
    return sorted((entry for entry in scored if entry["crap"] > limit), key=crap_order)


def crap_order(entry: Scored) -> float:
    return float(-entry["crap"])


def describe(entry: Scored, complexity: str = "cc") -> str:
    return f"{entry['file']}:{entry['line']} {entry['name']} crap={entry['crap']:.1f} (cc={entry[complexity]}, coverage={entry['cov']:.0%})"


def crap_result(gate: str, scored: list[Scored], limit: float, started: float) -> Result:
    offenders = above(scored, limit)
    summary = f"{len(scored)} functions, {len(offenders)} above CRAP {limit:g}"
    return Result(gate, not offenders, summary, [describe(entry, "complexity") for entry in offenders], elapsed(started))


def scored_functions(functions: list[Scored], coverage: dict[str, Any], ctx: Context) -> list[Scored]:
    return [file_percent_score(fn, coverage["files"].get(fn["file"], {}), ctx) for fn in functions]


def file_percent_score(fn: Scored, file_cov: dict[str, Any], ctx: Context) -> Scored:
    complexity = fn["complexity"]
    covered = file_cov.get("percent_covered", 100.0) / 100.0
    return {
        "file": relative_path(fn["file"], ctx),
        "line": fn["line"],
        "name": fn["name"],
        "complexity": complexity,
        "cov": covered,
        "crap": complexity**2 * (1 - covered) ** 3 + complexity,
    }
