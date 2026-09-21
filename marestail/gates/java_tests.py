import json
import re
import shutil
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from marestail import java
from marestail.context import Context
from marestail.report import Result, elapsed
from marestail.shell import tail

GATE = "java.tests"
JACOCO = "org.jacoco:jacoco-maven-plugin"
JACOCO_VERSION = "0.8.15"
EXEC = "java-jacoco.exec"
FRAME = re.compile(r"at ([\w.$]+)\.[\w$<>]+\(([\w$]+\.java):(\d+)\)")
NO_MESSAGE = "no message"
LINES = "lines"
FILES = "files"
EMPTY = ""
PACKAGE = "package"
JAVA_SUFFIX = ".java"


def run_gate(ctx: Context) -> Result:
    started = time.time()
    error = java.require_pom(ctx)
    if error:
        return Result(GATE, False, error)
    build = java.build_dir(ctx)
    reports, site = build / "surefire-reports", build / "site" / "jacoco"
    code, output = run_maven(ctx, reports, site)
    passed, failures = read_suites(ctx, sorted(reports.glob("TEST-*.xml")))
    problem = test_problem(code, output, passed, failures, started)
    if problem is not None:
        return problem
    return check_coverage(ctx, site / "jacoco.xml", passed, output, started)


def run_maven(ctx: Context, reports: Path, site: Path) -> tuple[int, str]:
    data = ctx.work / EXEC
    shutil.rmtree(reports, ignore_errors=True)
    shutil.rmtree(site, ignore_errors=True)
    data.unlink(missing_ok=True)
    plugin = f"{JACOCO}:{ctx.java('jacoco_version', JACOCO_VERSION)}"
    return java.mvn(ctx, [f"{plugin}:prepare-agent", "test", f"{plugin}:report", f"-Djacoco.destFile={data}", f"-Djacoco.dataFile={data}"])


def failed(summary: str, findings: list[str], started: float) -> Result:
    return Result(GATE, False, summary, findings, elapsed(started))


def test_problem(code: int, output: str, passed: int, failures: list[str], started: float) -> Result | None:
    if code != 0 or failures:
        return build_failure(code, output, failures, started)
    if passed == 0:
        return failed("no test passed; a run that executes nothing is not green", tail(output), started)
    return None


def build_failure(code: int, output: str, failures: list[str], started: float) -> Result:
    return failed(java.maven_hint(code, output) or "build or tests failed", failures or tail(output), started)


def check_coverage(ctx: Context, report: Path, passed: int, output: str, started: float) -> Result:
    if not report.exists():
        return failed("no JaCoCo report; if the pom sets the surefire <argLine>, start it with @{argLine}", tail(output), started)
    coverage = normalise(ctx, ET.parse(report).getroot())
    if not coverage[FILES]:
        return failed("coverage report names no source file; check [java] root, sources and coverage_exclude", tail(output), started)
    shutil.copy(report, ctx.work / "java-jacoco.xml")
    (ctx.work / "java-coverage.json").write_text(json.dumps(coverage))
    findings = coverage_findings(coverage, ctx)
    scope = " on changed files" if ctx.scoped else ""
    summary = f"{passed} passed, coverage {coverage['totals']['percent_covered']:.1f}%, {len(findings)} gaps{scope} (need 0)"
    return Result(GATE, not findings, summary, findings, elapsed(started))


def read_suites(ctx: Context, suites: list[Path]) -> tuple[int, list[str]]:
    cases = test_cases(suites)
    return sum(1 for case in cases if passed_case(case)), failure_lines(ctx, cases)


def test_cases(suites: list[Path]) -> list[ET.Element]:
    return [case for suite in suites for case in ET.parse(suite).getroot().iter("testcase")]


def first_problem(case: ET.Element) -> ET.Element | None:
    return next((child for child in case if child.tag in ("failure", "error")), None)


def passed_case(case: ET.Element) -> bool:
    return first_problem(case) is None and case.find("skipped") is None


def failure_lines(ctx: Context, cases: list[ET.Element]) -> list[str]:
    return [failure_line(ctx, case, problem) for case in cases if (problem := first_problem(case)) is not None]


def failure_line(ctx: Context, case: ET.Element, problem: ET.Element) -> str:
    return f"{where(ctx, case, xml_text(problem.text))} {xml_attr(case, 'classname')}.{xml_attr(case, 'name')} failed: {first_message(problem)[:200]}"


def first_message(problem: ET.Element) -> str:
    return ((problem.get("message") or problem.get("type") or NO_MESSAGE).splitlines() or [NO_MESSAGE])[0]


def where(ctx: Context, case: ET.Element, trace: str) -> str:
    return trace_location(ctx, trace) or class_location(ctx, xml_attr(case, "classname"))


def trace_location(ctx: Context, trace: str) -> str | None:
    for owner, file_name, line in FRAME.findall(trace):
        path = java.locate(java.all_roots(ctx), java.package_dir(owner), file_name)
        if path is not None:
            return f"{java.rel(ctx, path)}:{line}"
    return None


def class_location(ctx: Context, owner: str) -> str:
    path = java.locate(java.all_roots(ctx), java.package_dir(owner), java_file_name(owner))
    return f"{java.rel(ctx, path)}:1" if path is not None else f"{java.rel(ctx, java.pom(ctx))}:1"


def java_file_name(owner: str) -> str:
    return before_mark(after_last(owner, "."), "$") + JAVA_SUFFIX


def after_last(text: str, mark: str) -> str:
    if mark not in text:
        return text
    return text[text.rindex(mark) + 1 :]


def before_mark(text: str, mark: str) -> str:
    if mark not in text:
        return text
    return text[: text.index(mark)]


def xml_text(value: str | None) -> str:
    return value if value is not None else EMPTY


def xml_attr(node: ET.Element, key: str) -> str:
    value = node.get(key)
    return value if value is not None else EMPTY


def package_nodes(report: ET.Element) -> list[ET.Element]:
    return list(report.iter(PACKAGE))


def normalise(ctx: Context, report: ET.Element) -> dict[str, Any]:
    files: dict[str, dict[str, Any]] = {}
    for package in package_nodes(report):
        files.update(package_files(ctx, package))
    return {FILES: files, "totals": {"percent_covered": percent_covered(files)}}


def package_files(ctx: Context, package: ET.Element) -> list[tuple[str, dict[str, Any]]]:
    found = []
    roots = java.source_roots(ctx)
    for source in package.findall("sourcefile"):
        path = java.locate(roots, xml_attr(package, "name"), xml_attr(source, "name"))
        if path is not None and not java.coverage_excluded(ctx, java.rel(ctx, path)):
            found.append((java.rel(ctx, path), source_coverage(source)))
    return found


def source_coverage(source: ET.Element) -> dict[str, Any]:
    lines: dict[str, int] = {}
    missing_lines: list[int] = []
    missing_branches: list[list[int]] = []
    for row in source.findall("line"):
        number, covered = int(row.get("nr", 0)), int(row.get("ci", 0))
        missed_branches, covered_branches = int(row.get("mb", 0)), int(row.get("cb", 0))
        lines[str(number)] = covered
        if covered == 0:
            missing_lines.append(number)
        if missed_branches:
            missing_branches.append([number, missed_branches, missed_branches + covered_branches])
    return {LINES: lines, "missing_lines": missing_lines, "missing_branches": missing_branches}


def covered_lines(files: dict[str, dict[str, Any]]) -> int:
    return sum(1 for data in files.values() for hits in data[LINES].values() if hits > 0)


def percent_covered(files: dict[str, dict[str, Any]]) -> float:
    total = sum(len(data[LINES]) for data in files.values())
    return covered_lines(files) / total * 100.0 if total else 0.0


def coverage_findings(coverage: dict[str, Any], ctx: Context) -> list[str]:
    return [
        finding
        for file, data in sorted(coverage[FILES].items())
        if ctx.in_scope(file)
        for finding in file_gaps(file, data, ctx.gated_lines(file))
    ]


def gated_in(line: int, gated: set[int] | None) -> bool:
    return gated is None or line in gated


def file_gaps(file: str, data: dict[str, Any], gated: set[int] | None) -> list[str]:
    return line_gaps(file, data["missing_lines"], gated) + branch_gaps(file, data["missing_branches"], gated)


def line_gaps(file: str, missing_lines: list[int], gated: set[int] | None) -> list[str]:
    return [f"{file}:{line} not covered" for line in missing_lines if gated_in(line, gated)]


def branch_gaps(file: str, missing_branches: list[list[int]], gated: set[int] | None) -> list[str]:
    return [f"{file}:{line} {missed} of {total} branches not taken" for line, missed, total in missing_branches if gated_in(line, gated)]
