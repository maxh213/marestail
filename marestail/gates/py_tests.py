import json
import time
from typing import Any

from marestail.context import Context
from marestail.gates._coverage import PY_COVERAGE
from marestail.gates._coverage import scoped_lines as scoped_lines
from marestail.report import Result
from marestail.shell import run, tail

COVERAGE_JSON = PY_COVERAGE
GATE = "py.tests"
COVERAGE_XML = "py-coverage.xml"


def run_gate(ctx: Context) -> Result:
    started = time.time()
    code, output = run(pytest_command(ctx), cwd=ctx.python_root(), timeout=1800)
    if code != 0:
        return Result(GATE, False, "tests failed", tail(output), time.time() - started)
    coverage = load_coverage(ctx)
    findings = coverage_findings(coverage, ctx)
    percent = coverage["totals"]["percent_covered"]
    scope = " on changed lines" if ctx.scoped else ""
    summary = f"{count_tests(output)} passed, coverage {percent:.1f}%, {len(findings)} gaps{scope} (need 0)"
    return Result(GATE, not findings, summary, findings, time.time() - started)


def pytest_command(ctx: Context) -> list[str]:
    work = ctx.work
    return [
        ctx.python_bin("python"),
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "--cov",
        "--cov-branch",
        f"--cov-report=json:{work / COVERAGE_JSON}",
        f"--cov-report=xml:{work / COVERAGE_XML}",
    ]


def load_coverage(ctx: Context) -> dict[str, Any]:
    coverage: dict[str, Any] = json.loads((ctx.work / COVERAGE_JSON).read_text())
    return coverage


def coverage_findings(coverage: dict[str, Any], ctx: Context) -> list[str]:
    findings: list[str] = []
    for file, data in sorted(coverage["files"].items()):
        findings.extend(file_findings(file, data, scoped_lines(file, ctx)))
    return findings


def file_findings(file: str, data: dict[str, Any], gated: set[int] | None) -> list[str]:
    lines = [f"{file}:{line} not covered" for line in gated_intersect(data["missing_lines"], gated)]
    branches = [f"{file}:{start} branch to {end} not taken" for start, end in data["missing_branches"] if in_gate(start, gated)]
    return lines + branches


def in_gate(line: int, gated: set[int] | None) -> bool:
    return gated is None or line in gated


def gated_intersect(lines: list[int], gated: set[int] | None) -> list[int]:
    if gated is None:
        return lines
    return sorted(set(lines) & gated)


def count_tests(output: str) -> str:
    for line in reversed(output.splitlines()):
        if "passed" in line:
            return line.strip().split(" passed")[0].split()[-1]
    return "?"
