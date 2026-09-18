import json
import time
from pathlib import Path
from typing import Any

from marestail import erlang
from marestail.context import Context
from marestail.gates.er_tests import COVERAGE_JSON, relative_path
from marestail.report import Result

GATE = "er.crap"


def run_gate(ctx: Context) -> Result:
    started = time.time()
    coverage_path = ctx.work / COVERAGE_JSON
    if not coverage_path.exists():
        return Result(GATE, False, "no coverage data; er.tests must run first", [], 0.0)
    coverage = json.loads(coverage_path.read_text())
    files = files_in_scope(coverage, ctx)
    if not files:
        return Result.skipped(GATE, "no files in scope")
    code, output = erlang.escript(ctx, "complexity.escript", list(map(str, files)), timeout=600)
    failed = erlang.trouble(code, output, "complexity script failed", output.splitlines()[-10:])
    if failed:
        return Result(GATE, False, *failed, time.time() - started)
    functions = scoped_functions(json.loads(output), ctx)
    return crap_result(GATE, scored_functions(functions, coverage, ctx), float(ctx.erlang("crap_max", 4)), started)


def files_in_scope(coverage: dict[str, Any], ctx: Context) -> list[Path]:
    return [path for path in existing_files(coverage) if ctx.in_scope(relative_path(str(path), ctx))]


def existing_files(coverage: dict[str, Any]) -> list[Path]:
    return [Path(name) for name in coverage.get("files", {}) if Path(name).exists()]


def scored_functions(functions: list[dict[str, Any]], coverage: dict[str, Any], ctx: Context) -> list[dict[str, Any]]:
    return [score(fn, coverage["files"].get(fn["file"], {}), ctx) for fn in functions]


def crap_result(gate: str, scored: list[dict[str, Any]], limit: float, started: float) -> Result:
    offenders = sorted((entry for entry in scored if entry["crap"] > limit), key=crap_order)
    summary = f"{len(scored)} functions, {len(offenders)} above CRAP {limit:g}"
    return Result(gate, not offenders, summary, [describe(entry) for entry in offenders], time.time() - started)


def crap_order(entry: dict[str, Any]) -> float:
    return float(-entry["crap"])


def scoped_functions(functions: list[dict[str, Any]], ctx: Context) -> list[dict[str, Any]]:
    if not ctx.scoped:
        return functions
    ends = function_ends(functions)
    return [fn for fn in functions if touches_hunk(fn, ends, ctx)]


def function_ends(functions: list[dict[str, Any]]) -> dict[tuple[str, int], int]:
    by_file: dict[str, list[int]] = {}
    for fn in functions:
        by_file.setdefault(fn["file"], []).append(fn["line"])
    ends: dict[tuple[str, int], int] = {}
    for file, lines in by_file.items():
        ends.update(file_ends(file, lines))
    return ends


def file_ends(file: str, lines: list[int]) -> dict[tuple[str, int], int]:
    ordered = sorted(lines)
    total = len(Path(file).read_text(errors="replace").splitlines())
    bounds = [line - 1 for line in ordered[1:]] + [total]
    return {(file, start): end for start, end in zip(ordered, bounds, strict=True)}


def touches_hunk(fn: dict[str, Any], ends: dict[tuple[str, int], int], ctx: Context) -> bool:
    gated = ctx.gated_lines(relative_path(fn["file"], ctx))
    if gated is None:
        return True
    end = ends[(fn["file"], fn["line"])]
    return any(fn["line"] <= line <= end for line in gated)


def score(fn: dict[str, Any], file_cov: dict[str, Any], ctx: Context) -> dict[str, Any]:
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


def describe(entry: dict[str, Any]) -> str:
    return (
        f"{entry['file']}:{entry['line']} {entry['name']} crap={entry['crap']:.1f} (cc={entry['complexity']}, coverage={entry['cov']:.0%})"
    )
