import json
import time

from marestail.context import Context
from marestail.report import Result
from marestail.shell import run, tail

COVERAGE_DIR = "ts-coverage"


def run_gate(ctx: Context) -> Result:
    started = time.time()
    code, output = run(vitest_command(ctx), cwd=ctx.ts_root(), timeout=1800)
    if code != 0:
        return Result("ts.tests", False, "tests failed", tail(output), time.time() - started)
    coverage = load_coverage(ctx)
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
    from pathlib import Path

    return str(Path(file).resolve().relative_to(ctx.root))


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


