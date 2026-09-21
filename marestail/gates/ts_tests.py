import json
import re
import time
from typing import Any

from marestail.context import Context
from marestail.gates._coverage import TS_COVERAGE_DIR
from marestail.gates._coverage import in_scope_findings as in_scope_findings
from marestail.javascript import labelled
from marestail.report import Result, elapsed
from marestail.shell import run, tail

COVERAGE_DIR = TS_COVERAGE_DIR
relative_path = labelled

JEST_RESULTS = "ts-tests.json"
UNINSTRUMENTED = re.compile(r"^Failed to collect coverage from (.+)$", re.M)
GATE = "ts.tests"
VITEST = "vitest"
JEST = "jest"
RUNNER_ERROR = "runner"


def chosen_runner(ctx: Context) -> str:
    runner = ctx.ts("runner", VITEST)
    if type(runner) is not str:
        raise TypeError(RUNNER_ERROR)
    return runner


def run_gate(ctx: Context) -> Result:
    started = time.time()
    runner = chosen_runner(ctx)
    command = jest_command(ctx) if runner == JEST else vitest_command(ctx)
    code, output = run(command, cwd=ctx.ts_root(), timeout=1800)
    if code != 0:
        return Result(GATE, False, "tests failed", failures(ctx, runner == JEST) or tail(output), elapsed(started))
    return passed(ctx, output, started)


def failures(ctx: Context, jest: bool) -> list[str]:
    return jest_failures(ctx) if jest else []


def passed(ctx: Context, output: str, started: float) -> Result:
    uninstrumented = in_scope_findings([f"{relative_path(name, ctx)}:1 not instrumented" for name in UNINSTRUMENTED.findall(output)], ctx)
    if uninstrumented:
        return Result(GATE, False, f"{len(uninstrumented)} files could not be instrumented", uninstrumented, elapsed(started))
    coverage = read_coverage(ctx)
    if not coverage:
        return Result(GATE, False, "no coverage report; check [ts] runner and sources", tail(output), elapsed(started))
    findings = coverage_findings(coverage, ctx)
    summary = f"{count_tests(output)} passed, {len(findings)} uncovered lines/branches (need 0)"
    return Result(GATE, not findings, summary, findings, elapsed(started))


def read_coverage(ctx: Context) -> dict[str, Any]:
    report = ctx.work / COVERAGE_DIR / "coverage-final.json"
    coverage: dict[str, Any] = json.loads(report.read_text()) if report.exists() else {}
    return coverage


def vitest_command(ctx: Context) -> list[str]:
    report_dir = ctx.work / COVERAGE_DIR
    return [
        "npx",
        chosen_runner(ctx),
        "run",
        "--coverage.enabled=true",
        "--coverage.all=true",
        "--coverage.reporter=json",
        "--coverage.reporter=lcov",
        f"--coverage.reportsDirectory={report_dir}",
    ]


def jest_command(ctx: Context) -> list[str]:
    sources = ctx.ts("sources") or [ctx.ts("source", "src")]
    return [
        str(ctx.ts_root() / "node_modules" / ".bin" / "jest"),
        "--ci",
        "--coverage",
        "--coverageProvider=babel",
        "--coverageReporters=json",
        "--coverageReporters=lcov",
        f"--coverageDirectory={ctx.work / COVERAGE_DIR}",
        "--json",
        f"--outputFile={ctx.work / JEST_RESULTS}",
        "--testLocationInResults",
        *[f"--collectCoverageFrom={folder}/**/*.{{ts,tsx,js,jsx}}" for folder in sources],
    ]


def jest_failures(ctx: Context) -> list[str]:
    path = ctx.work / JEST_RESULTS
    if not path.exists():
        return []
    return [finding for suite in json.loads(path.read_text()).get("testResults", []) for finding in suite_failures(suite, ctx)]


def suite_failures(suite: dict[str, Any], ctx: Context) -> list[str]:
    file = relative_path(suite["name"], ctx)
    failed = failed_cases(suite)
    return suite_broken(file, suite, failed) + [case_failure(file, case) for case in failed]


def failed_cases(suite: dict[str, Any]) -> list[dict[str, Any]]:
    return [case for case in suite.get("assertionResults", []) if case["status"] == "failed"]


def suite_broken(file: str, suite: dict[str, Any], failed: list[dict[str, Any]]) -> list[str]:
    if not failed and suite.get("status") == "failed":
        return [f"{file}:1 suite failed to run: {first_line(suite.get('message', ''))}"]
    return []


def case_failure(file: str, case: dict[str, Any]) -> str:
    line = (case.get("location") or {}).get("line", 1)
    return f"{file}:{line} {case['fullName']} failed: {first_line(' '.join(case.get('failureMessages', [])))}"


def first_line(text: str) -> str:
    return next((line.strip()[:200] for line in text.splitlines() if line.strip()), "no message")


def coverage_findings(coverage: dict[str, Any], ctx: Context) -> list[str]:
    findings: list[str] = []
    for file, data in sorted(coverage.items()):
        relative = relative_path(file, ctx)
        if not ctx.in_scope(relative):
            continue
        gated = ctx.gated_lines(relative)
        findings.extend(uncovered_statements(relative, data, gated))
        findings.extend(uncovered_branches(relative, data, gated))
    return findings


def uncovered_statements(file: str, data: dict[str, Any], gated: set[int] | None) -> list[str]:
    lines = sorted({data["statementMap"][key]["start"]["line"] for key, hits in data["s"].items() if hits == 0})
    return [f"{file}:{line} not covered" for line in gated_only(lines, gated)]


def gated_only(lines: list[int], gated: set[int] | None) -> list[int]:
    return lines if gated is None else [line for line in lines if line in gated]


def uncovered_branches(file: str, data: dict[str, Any], gated: set[int] | None) -> list[str]:
    return [finding for key, arms in data["b"].items() for finding in branch_findings(file, data["branchMap"][key], arms, gated)]


def branch_findings(file: str, branch: dict[str, Any], arms: list[int], gated: set[int] | None) -> list[str]:
    return [
        f"{file}:{branch['loc']['start']['line']} branch arm {index} not taken"
        for index, hits in enumerate(arms)
        if hits == 0 and arm_gated(branch, index, gated)
    ]


def arm_gated(branch: dict[str, Any], index: int, gated: set[int] | None) -> bool:
    return gated is None or bool(arm_lines(branch, index) & gated)


def arm_lines(branch: dict[str, Any], index: int) -> set[Any]:
    lines = {branch["loc"]["start"]["line"]}
    locations = branch.get("locations") or []
    if index < len(locations):
        lines.add((locations[index].get("start") or {}).get("line"))
    return lines


def count_tests(output: str) -> str:
    for line in output.splitlines():
        if "Tests" in line and "passed" in line:
            return line.split("passed")[0].split()[-1]
    return "?"
