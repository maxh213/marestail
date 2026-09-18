import json
import time

from marestail.context import Context
from marestail.report import Result
from marestail.shell import run, tail

COVERAGE_JSON = "py-coverage.json"
COVERAGE_XML = "py-coverage.xml"


def run_gate(ctx: Context) -> Result:
    started = time.time()
    code, output = run(pytest_command(ctx), cwd=ctx.python_root(), timeout=1800)
    if code != 0:
        return Result("py.tests", False, "tests failed", tail(output), time.time() - started)
    findings = coverage_findings(load_coverage(ctx), ctx)
    percent = load_coverage(ctx)["totals"]["percent_covered"]
    scope = " on changed lines" if ctx.scoped else ""
    summary = f"{count_tests(output)} passed, coverage {percent:.1f}%, {len(findings)} gaps{scope} (need 0)"
    return Result("py.tests", not findings, summary, findings, time.time() - started)


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


def load_coverage(ctx: Context) -> dict:
    return json.loads((ctx.work / COVERAGE_JSON).read_text())


def coverage_findings(coverage: dict, ctx: Context) -> list[str]:
    findings: list[str] = []
    for file, data in sorted(coverage["files"].items()):
        gated = scoped_lines(file, ctx)
        findings.extend(f"{file}:{line} not covered" for line in gated_intersect(data["missing_lines"], gated))
        branches = [pair for pair in data["missing_branches"] if gated is None or pair[0] in gated]
        findings.extend(f"{file}:{start} branch to {end} not taken" for start, end in branches)
    return findings


def scoped_lines(file: str, ctx: Context) -> set[int] | None:
    if not ctx.scoped:
        return None
    relative = (ctx.python_root() / file).resolve().relative_to(ctx.root)
    path = str(relative)
    if not ctx.in_scope(path):
        return set()
    return ctx.gated_lines(path)


def gated_intersect(lines: list[int], gated: set[int] | None) -> list[int]:
    if gated is None:
        return lines
    return sorted(set(lines) & gated)


def count_tests(output: str) -> str:
    for line in reversed(output.splitlines()):
        if "passed" in line:
            return line.strip().split(" passed")[0].split()[-1]
    return "?"
