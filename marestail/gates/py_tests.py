import json
import time
from pathlib import Path

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
    scope = " on changed files" if ctx.scope_changed else ""
    summary = f"{count_tests(output)} passed, coverage {percent:.1f}%, {len(findings)} gaps{scope} (need 0)"
    return Result("py.tests", not findings, summary, findings, time.time() - started)


def pytest_command(ctx: Context) -> list[str]:
    work = ctx.work
    return [
        ctx.python_bin("python"), "-m", "pytest", "-q", "-p", "no:cacheprovider",
        "--cov", "--cov-branch",
        f"--cov-report=json:{work / COVERAGE_JSON}",
        f"--cov-report=xml:{work / COVERAGE_XML}",
    ]


def load_coverage(ctx: Context) -> dict:
    return json.loads((ctx.work / COVERAGE_JSON).read_text())


def coverage_findings(coverage: dict, ctx: Context) -> list[str]:
    findings: list[str] = []
    for file, data in sorted(coverage["files"].items()):
        if ctx.scope_changed and not in_scope(file, ctx):
            continue
        findings.extend(f"{file}:{line} not covered" for line in data["missing_lines"])
        findings.extend(f"{file}:{start} branch to {end} not taken" for start, end in data["missing_branches"])
    return findings


def in_scope(file: str, ctx: Context) -> bool:
    relative = (ctx.python_root() / file).resolve().relative_to(ctx.root)
    return str(relative) in ctx.changed


def count_tests(output: str) -> str:
    for line in reversed(output.splitlines()):
        if "passed" in line:
            return line.strip().split(" passed")[0].split()[-1]
    return "?"


