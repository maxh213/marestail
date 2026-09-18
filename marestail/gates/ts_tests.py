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
RUNNERS = ("vitest", "jest", "playwright")
PLAYWRIGHT_JSON = "PLAYWRIGHT_JSON_OUTPUT_NAME"
FAILED_STATUSES = {"failed", "timedOut", "interrupted"}


def run_gate(ctx: Context) -> Result:
    started = time.time()
    runner = ctx.ts("runner", "vitest")
    if runner not in RUNNERS:
        return Result("ts.tests", False, f"unknown [ts] runner {runner!r}; use vitest, jest or playwright", [], time.time() - started)
    missing = missing_tools(ctx, runner)
    if missing:
        return Result("ts.tests", False, missing, [], time.time() - started)
    env = playwright_env(ctx) if runner == "playwright" else None
    code, output = run(command_for(ctx, runner), cwd=ctx.ts_root(), env=env, timeout=1800)
    if code != 0:
        findings = failure_findings(ctx, runner)
        return Result("ts.tests", False, "tests failed", findings or tail(output), time.time() - started)
    skipped = UNINSTRUMENTED.findall(output)
    uninstrumented = [f"{relative_path(name, ctx)}:1 not instrumented" for name in skipped]
    if ctx.scoped:
        uninstrumented = [finding for finding in uninstrumented if ctx.in_scope(finding.split(":", 1)[0])]
    if uninstrumented:
        return Result("ts.tests", False, f"{len(uninstrumented)} files could not be instrumented", uninstrumented, time.time() - started)
    report = ctx.work / COVERAGE_DIR / "coverage-final.json"
    coverage = json.loads(report.read_text()) if report.exists() else {}
    if not coverage:
        return Result("ts.tests", False, "no coverage report; check [ts] runner and sources", tail(output), time.time() - started)
    findings = coverage_findings(coverage, ctx)
    summary = f"{count_tests(output, ctx, runner)} passed, {len(findings)} uncovered lines/branches (need 0)"
    return Result("ts.tests", not findings, summary, findings, time.time() - started)


def missing_tools(ctx: Context, runner: str) -> str:
    if runner != "playwright":
        return ""
    root = ctx.ts_root()
    if not (root / "node_modules" / ".bin" / "playwright").exists():
        return "playwright is not installed at node_modules/.bin/playwright; add @playwright/test"
    if not (root / "node_modules" / ".bin" / "c8").exists():
        return "c8 is not installed at node_modules/.bin/c8; add c8 so playwright coverage can be collected"
    return ""


def command_for(ctx: Context, runner: str) -> list[str]:
    if runner == "jest":
        return jest_command(ctx)
    if runner == "playwright":
        return playwright_command(ctx)
    return vitest_command(ctx)


def failure_findings(ctx: Context, runner: str) -> list[str]:
    if runner == "jest":
        return jest_failures(ctx)
    if runner == "playwright":
        return playwright_failures(ctx)
    return []


def playwright_env(ctx: Context) -> dict[str, str]:
    return {PLAYWRIGHT_JSON: str(ctx.work / JEST_RESULTS)}


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


def playwright_command(ctx: Context) -> list[str]:
    root = ctx.ts_root()
    coverage = ctx.work / COVERAGE_DIR
    coverage.mkdir(parents=True, exist_ok=True)
    sources = ctx.ts("sources") or [ctx.ts("source", "src")]
    command = [
        str(root / "node_modules" / ".bin" / "c8"),
        "--reporter=json",
        "--reporter=lcov",
        f"--reports-dir={coverage}",
        "--all",
    ]
    for folder in sources:
        command.append(f"--include={folder}/**")
    command += [
        "--exclude=**/*.spec.ts",
        "--exclude=**/*.spec.tsx",
        "--exclude=**/*.test.ts",
        "--exclude=**/*.test.tsx",
        "--exclude=**/*.spec.js",
        "--exclude=**/*.test.js",
        str(root / "node_modules" / ".bin" / "playwright"),
        "test",
        "--reporter=line",
        "--reporter=json",
    ]
    return command


def playwright_failures(ctx: Context) -> list[str]:
    path = ctx.work / JEST_RESULTS
    if not path.exists():
        return []
    try:
        report = json.loads(path.read_text())
    except json.JSONDecodeError:
        return [f"{JEST_RESULTS}:1 playwright JSON report is not valid JSON"]
    findings = []
    for spec in playwright_specs(report.get("suites") or []):
        findings.extend(playwright_spec_findings(spec, ctx))
    for error in report.get("errors") or []:
        findings.append(playwright_error_finding(error, ctx))
    return findings


def playwright_specs(suites: list) -> list:
    specs = []
    for suite in suites:
        specs.extend(suite.get("specs") or [])
        specs.extend(playwright_specs(suite.get("suites") or []))
    return specs


def playwright_spec_findings(spec: dict, ctx: Context) -> list[str]:
    if spec.get("ok", True):
        return []
    title = spec.get("title") or "test"
    findings = []
    for case in spec.get("tests") or []:
        for result in case.get("results") or []:
            if result.get("status") not in FAILED_STATUSES:
                continue
            error = result.get("error") or {}
            findings.append(playwright_case_finding(spec, title, error, result.get("status"), ctx))
    if not findings:
        findings.append(playwright_case_finding(spec, title, {}, "failed", ctx))
    return findings


def playwright_case_finding(spec: dict, title: str, error: dict, status: str, ctx: Context) -> str:
    loc = error.get("location") or {}
    file = loc.get("file") or spec.get("file") or "playwright"
    line = loc.get("line") or spec.get("line") or 1
    message = first_line(error.get("message") or status or "failed")
    return f"{relative_path(str(file), ctx)}:{line} {title} failed: {message}"


def playwright_error_finding(error: dict | str, ctx: Context) -> str:
    if not isinstance(error, dict):
        return f"playwright:1 {first_line(str(error))}"
    loc = error.get("location") or {}
    file = loc.get("file") or "playwright"
    line = loc.get("line") or 1
    return f"{relative_path(str(file), ctx)}:{line} {first_line(error.get('message') or 'playwright error')}"


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
        if not ctx.in_scope(relative):
            continue
        gated = ctx.gated_lines(relative)
        findings.extend(uncovered_statements(relative, data, gated))
        findings.extend(uncovered_branches(relative, data, gated))
    return findings


def relative_path(file: str, ctx: Context) -> str:
    path = Path(file.strip())
    if not path.is_absolute():
        path = ctx.ts_root() / path
    try:
        return path.resolve().relative_to(ctx.root.resolve()).as_posix()
    except ValueError:
        return file


def uncovered_statements(file: str, data: dict, gated: set[int] | None) -> list[str]:
    lines = sorted({data["statementMap"][key]["start"]["line"] for key, hits in data["s"].items() if hits == 0})
    if gated is not None:
        lines = [line for line in lines if line in gated]
    return [f"{file}:{line} not covered" for line in lines]


def uncovered_branches(file: str, data: dict, gated: set[int] | None) -> list[str]:
    findings = []
    for key, arms in data["b"].items():
        branch = data["branchMap"][key]
        for index, hits in enumerate(arms):
            if hits == 0 and arm_gated(branch, index, gated):
                findings.append(f"{file}:{branch['loc']['start']['line']} branch arm {index} not taken")
    return findings


def arm_gated(branch: dict, index: int, gated: set[int] | None) -> bool:
    if gated is None:
        return True
    lines = {branch["loc"]["start"]["line"]}
    locations = branch.get("locations") or []
    if index < len(locations):
        lines.add((locations[index].get("start") or {}).get("line"))
    return bool(lines & gated)


def count_tests(output: str, ctx: Context | None = None, runner: str = "") -> str:
    if runner == "playwright" and ctx is not None:
        path = ctx.work / JEST_RESULTS
        if path.exists():
            try:
                stats = json.loads(path.read_text()).get("stats") or {}
            except json.JSONDecodeError:
                stats = {}
            if "expected" in stats:
                return str(stats["expected"])
    for line in output.splitlines():
        if "Tests" in line and "passed" in line:
            return line.split("passed")[0].split()[-1]
        stripped = line.strip()
        if " passed" in stripped:
            token = stripped.split(" passed", 1)[0].split()[-1]
            if token.isdigit():
                return token
    return "?"
