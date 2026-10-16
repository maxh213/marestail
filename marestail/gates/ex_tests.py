import json
import time
from pathlib import Path
from typing import Any

from marestail.context import Context
from marestail.elixir import COVERAGE
from marestail.gates._coverage import EX_COVERAGE
from marestail.gates._coverage import coverage_findings as coverage_findings
from marestail.gates._coverage import relative_path as relative_path
from marestail.report import Result, elapsed
from marestail.shell import run, tail

COVERAGE_SCRIPT = COVERAGE
COVERAGE_JSON = EX_COVERAGE
GATE = "ex.tests"
MISSING_COUNT = ""


def run_gate(ctx: Context) -> Result:
    started = time.time()
    root = ctx.elixir_root()
    coverdata = root / "cover" / "default.coverdata"
    code, output = run(["mix", "test", "--cover", "--export-coverage", "default"], cwd=root, timeout=1800)
    if code != 0:
        return Result(GATE, False, "tests failed", tail(output), elapsed(started))
    if not coverdata.exists():
        return Result(GATE, False, "no coverdata exported", tail(output), elapsed(started))
    extracted, c_output = export_coverage(ctx, root, coverdata)
    if not extracted:
        return Result(GATE, False, "coverage extraction failed", tail(c_output), elapsed(started))
    return coverage_result(ctx, json.loads((ctx.work / COVERAGE_JSON).read_text()), output, started)


def export_coverage(ctx: Context, root: Path, coverdata: Path) -> tuple[bool, str]:
    out_json = ctx.work / COVERAGE_JSON
    code, output = run(["elixir", str(COVERAGE_SCRIPT), str(coverdata), str(out_json)], cwd=root, timeout=300)
    return code == 0 and out_json.exists(), output


def coverage_result(ctx: Context, coverage: dict[str, Any], output: str, started: float) -> Result:
    findings = coverage_findings(coverage, ctx)
    total = coverage["totals"]["percent_covered"]
    percent = scoped_percent(coverage, ctx) if ctx.scoped else total
    scope = " on changed lines" if ctx.scoped else ""
    summary = f"{count_tests(output)} passed, coverage {percent:.1f}%, {len(findings)} gaps{scope} (need 0)"
    return Result(GATE, not findings, summary, findings, elapsed(started))


def scoped_percent(coverage: dict[str, Any], ctx: Context) -> float:
    covered = 0
    total = 0
    for file_str, data in coverage["files"].items():
        if ctx.in_scope(relative_path(file_str, ctx)):
            covered += counted(data, "covered")
            total += counted(data, "total")
    return (covered / total) * 100.0 if total else 100.0


def count_tests(output: str) -> str:
    return next((count for count in map(passed_before, output.splitlines()) if count), "?")


def counted(data: dict[str, Any], key: str) -> int:
    return int(data[key]) if key in data else 0


def passed_before(line: str) -> str:
    parts = line.split()
    return next((parts[index - 1] for index in range(1, len(parts)) if parts[index] == "passed"), MISSING_COUNT)
