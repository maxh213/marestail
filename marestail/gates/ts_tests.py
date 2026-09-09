import json
import re
import time
from pathlib import Path

from marestail.context import Context
from marestail.report import Result
from marestail.shell import run, tail

COVERAGE_DIR = "ts-coverage"
JEST_RESULTS = "ts-tests.json"
UNINSTRUMENTED = re.compile(r"^Failed to collect coverage from (.+)$", re.M)


def run_gate(ctx: Context) -> Result:
    started = time.time()
    jest = ctx.ts("runner", "vitest") == "jest"
    code, output = run(jest_command(ctx) if jest else vitest_command(ctx), cwd=ctx.ts_root(), timeout=1800)
    if code != 0:
        findings = jest_failures(ctx) if jest else []
        return Result("ts.tests", False, "tests failed", findings or tail(output), time.time() - started)
    skipped = UNINSTRUMENTED.findall(output)
    if skipped:
        return Result("ts.tests", False, f"{len(skipped)} files could not be instrumented", [f"{relative_path(name, ctx)}:1 not instrumented" for name in skipped], time.time() - started)
    report = ctx.work / COVERAGE_DIR / "coverage-final.json"
    coverage = json.loads(report.read_text()) if report.exists() else {}
    if not coverage:
        return Result("ts.tests", False, "no coverage report; check [ts] runner and sources", tail(output), time.time() - started)
    findings = coverage_findings(coverage, ctx)
    summary = f"{count_tests(output)} passed, {len(findings)} uncovered lines/branches (need 0)"
    return Result("ts.tests", not findings, summary, findings, time.time() - started)


def vitest_command(ctx: Context) -> list[str]:
    report_dir = ctx.work / COVERAGE_DIR
    return [
        "npx", "vitest", "run", "--coverage.enabled=true", "--coverage.all=true",
        "--coverage.reporter=json", "--coverage.reporter=lcov",
        f"--coverage.reportsDirectory={report_dir}",
    ]


def jest_command(ctx: Context) -> list[str]:
    sources = ctx.ts("sources") or [ctx.ts("source", "src")]
    return [
        str(ctx.ts_root() / "node_modules" / ".bin" / "jest"), "--ci", "--coverage", "--coverageProvider=babel",
        "--coverageReporters=json", "--coverageReporters=lcov", f"--coverageDirectory={ctx.work / COVERAGE_DIR}",
        "--json", f"--outputFile={ctx.work / JEST_RESULTS}", "--testLocationInResults",
        *[f"--collectCoverageFrom={folder}/**/*.{{ts,tsx,js,jsx}}" for folder in sources],
    ]


def jest_failures(ctx: Context) -> list[str]:
    path = ctx.work / JEST_RESULTS
    if not path.exists():
        return []
    findings = []
    for suite in json.loads(path.read_text()).get("testResults", []):
        file = relative_path(suite["name"], ctx)
        failed = [case for case in suite.get("assertionResults", []) if case["status"] == "failed"]
        if not failed and suite.get("status") == "failed":
            findings.append(f"{file}:1 suite failed to run: {first_line(suite.get('message', ''))}")
        for case in failed:
            line = (case.get("location") or {}).get("line", 1)
            findings.append(f"{file}:{line} {case['fullName']} failed: {first_line(' '.join(case.get('failureMessages', [])))}")
    return findings


def first_line(text: str) -> str:
    return next((line.strip()[:200] for line in text.splitlines() if line.strip()), "no message")


def load_coverage(ctx: Context) -> dict:
    return json.loads((ctx.work / COVERAGE_DIR / "coverage-final.json").read_text())


def coverage_findings(coverage: dict, ctx: Context) -> list[str]:
    findings: list[str] = []
    for file, data in sorted(coverage.items()):
        relative = relative_path(file, ctx)
        if ctx.scope_changed and relative not in ctx.changed:
            continue
        findings.extend(uncovered_statements(relative, data))
        findings.extend(uncovered_branches(relative, data))
    return findings


def relative_path(file: str, ctx: Context) -> str:
    path = Path(file.strip())
    if not path.is_absolute():
        path = ctx.ts_root() / path
    try:
        return path.resolve().relative_to(ctx.root.resolve()).as_posix()
    except ValueError:
        return file


def uncovered_statements(file: str, data: dict) -> list[str]:
    lines = sorted({data["statementMap"][key]["start"]["line"] for key, hits in data["s"].items() if hits == 0})
    return [f"{file}:{line} not covered" for line in lines]


def uncovered_branches(file: str, data: dict) -> list[str]:
    findings = []
    for key, arms in data["b"].items():
        branch = data["branchMap"][key]
        for index, hits in enumerate(arms):
            if hits == 0:
                findings.append(f"{file}:{branch['loc']['start']['line']} branch arm {index} not taken")
    return findings


def count_tests(output: str) -> str:
    for line in output.splitlines():
        if "Tests" in line and "passed" in line:
            return line.split("passed")[0].split()[-1]
    return "?"
