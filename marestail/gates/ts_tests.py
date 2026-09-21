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
EMPTY = ""
EMPTY_LIST: list[str] = []


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


def empty_failures(_ctx: Context) -> list[str]:
    return EMPTY_LIST


def failures(ctx: Context, jest: bool) -> list[str]:
    chosen = (empty_failures, jest_failures)[jest]
    return chosen(ctx)


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


def json_list(data: dict[str, Any], key: str) -> list[Any]:
    if key not in data:
        return EMPTY_LIST
    found: list[Any] = data[key]
    return found


def jest_failures(ctx: Context) -> list[str]:
    path = ctx.work / JEST_RESULTS
    if not path.exists():
        return EMPTY_LIST
    return [finding for suite in json_list(json.loads(path.read_text()), "testResults") for finding in suite_failures(suite, ctx)]


def suite_failures(suite: dict[str, Any], ctx: Context) -> list[str]:
    file = relative_path(suite["name"], ctx)
    failed = failed_cases(suite)
    return suite_broken(file, suite, failed) + [case_failure(file, case) for case in failed]


def failed_cases(suite: dict[str, Any]) -> list[dict[str, Any]]:
    return [case for case in suite.get("assertionResults", []) if case["status"] == "failed"]


def suite_message(suite: dict[str, Any]) -> str:
    if "message" not in suite:
        return EMPTY
    found: str = suite["message"]
    return found


def suite_broken(file: str, suite: dict[str, Any], failed: list[dict[str, Any]]) -> list[str]:
    if not failed and suite.get("status") == "failed":
        return [f"{file}:1 suite failed to run: {first_line(suite_message(suite))}"]
    return EMPTY_LIST


def case_failure(file: str, case: dict[str, Any]) -> str:
    line = (case.get("location") or {}).get("line", 1)
    return f"{file}:{line} {case['fullName']} failed: {first_line(' '.join(case.get('failureMessages', [])))}"


def first_line(text: str) -> str:
    return next((line.strip()[:200] for line in text.splitlines() if line.strip()), "no message")


def file_gaps(file: str, data: dict[str, Any], gated: set[int] | None) -> list[str]:
    return uncovered_statements(file, data, gated) + uncovered_branches(file, data, gated)


def scoped_gaps(file: str, data: dict[str, Any], ctx: Context) -> list[str]:
    relative = relative_path(file, ctx)
    if not ctx.in_scope(relative):
        return EMPTY_LIST
    return file_gaps(relative, data, ctx.gated_lines(relative))


def coverage_findings(coverage: dict[str, Any], ctx: Context) -> list[str]:
    return [finding for file, data in sorted(coverage.items()) for finding in scoped_gaps(file, data, ctx)]


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


def passed_count(line: str) -> str:
    return line.split("passed")[0].split()[-1]


def count_tests(output: str) -> str:
    for line in output.splitlines():
        if "Tests" in line and "passed" in line:
            return passed_count(line)
    return "?"
