import json
import re
import shutil
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from marestail import java
from marestail.context import Context
from marestail.report import Result
from marestail.shell import tail

JACOCO = "org.jacoco:jacoco-maven-plugin"
JACOCO_VERSION = "0.8.15"
EXEC = "java-jacoco.exec"
FRAME = re.compile(r"at ([\w.$]+)\.[\w$<>]+\(([\w$]+\.java):(\d+)\)")


def run_gate(ctx: Context) -> Result:
    started = time.time()
    error = java.require_pom(ctx)
    if error:
        return Result("java.tests", False, error, [], 0.0)
    build = java.build_dir(ctx)
    reports, site, data = build / "surefire-reports", build / "site" / "jacoco", ctx.work / EXEC
    shutil.rmtree(reports, ignore_errors=True)
    shutil.rmtree(site, ignore_errors=True)
    data.unlink(missing_ok=True)
    plugin = f"{JACOCO}:{ctx.java('jacoco_version', JACOCO_VERSION)}"
    code, output = java.mvn(
        ctx, [f"{plugin}:prepare-agent", "test", f"{plugin}:report", f"-Djacoco.destFile={data}", f"-Djacoco.dataFile={data}"]
    )
    passed, failures = read_suites(ctx, sorted(reports.glob("TEST-*.xml")))
    if code != 0 or failures:
        return Result(
            "java.tests", False, java.maven_hint(code, output) or "build or tests failed", failures or tail(output), time.time() - started
        )
    if passed == 0:
        return Result("java.tests", False, "no test passed; a run that executes nothing is not green", tail(output), time.time() - started)
    report = site / "jacoco.xml"
    if not report.exists():
        return Result(
            "java.tests",
            False,
            "no JaCoCo report; if the pom sets the surefire <argLine>, start it with @{argLine}",
            tail(output),
            time.time() - started,
        )
    coverage = normalise(ctx, ET.parse(report).getroot())
    if not coverage["files"]:
        return Result(
            "java.tests",
            False,
            "coverage report names no source file; check [java] root, sources and coverage_exclude",
            tail(output),
            time.time() - started,
        )
    shutil.copy(report, ctx.work / "java-jacoco.xml")
    (ctx.work / "java-coverage.json").write_text(json.dumps(coverage))
    findings = coverage_findings(coverage, ctx)
    scope = " on changed files" if ctx.scoped else ""
    summary = f"{passed} passed, coverage {coverage['totals']['percent_covered']:.1f}%, {len(findings)} gaps{scope} (need 0)"
    return Result("java.tests", not findings, summary, findings, time.time() - started)


def read_suites(ctx: Context, suites: list[Path]) -> tuple[int, list[str]]:
    passed, findings = 0, []
    for suite in suites:
        for case in ET.parse(suite).getroot().iter("testcase"):
            problem = next((child for child in case if child.tag in ("failure", "error")), None)
            if problem is None:
                passed += 0 if case.find("skipped") is not None else 1
                continue
            message = ((problem.get("message") or problem.get("type") or "no message").splitlines() or ["no message"])[0]
            findings.append(f"{where(ctx, case, problem.text or '')} {case.get('classname')}.{case.get('name')} failed: {message[:200]}")
    return passed, findings


def where(ctx: Context, case: ET.Element, trace: str) -> str:
    for owner, file_name, line in FRAME.findall(trace):
        path = java.locate(ctx, package_dir(owner), file_name)
        if path is not None:
            return f"{java.rel(ctx, path)}:{line}"
    owner = case.get("classname") or ""
    path = java.locate(ctx, package_dir(owner), owner.rsplit(".", 1)[-1].split("$", 1)[0] + ".java")
    return f"{java.rel(ctx, path)}:1" if path is not None else f"{java.rel(ctx, java.pom(ctx))}:1"


def package_dir(owner: str) -> str:
    return "/".join(owner.split(".")[:-1])


def normalise(ctx: Context, report: ET.Element) -> dict:
    files = {}
    for package in report.iter("package"):
        for source in package.findall("sourcefile"):
            path = java.locate(ctx, package.get("name", ""), source.get("name", ""), java.source_roots(ctx))
            if path is None or java.coverage_excluded(ctx, java.rel(ctx, path)):
                continue
            lines, missing_lines, missing_branches = {}, [], []
            for row in source.findall("line"):
                number, covered = int(row.get("nr", 0)), int(row.get("ci", 0))
                missed_branches, covered_branches = int(row.get("mb", 0)), int(row.get("cb", 0))
                lines[str(number)] = covered
                if covered == 0:
                    missing_lines.append(number)
                if missed_branches:
                    missing_branches.append([number, missed_branches, missed_branches + covered_branches])
            files[java.rel(ctx, path)] = {"lines": lines, "missing_lines": missing_lines, "missing_branches": missing_branches}
    total = sum(len(data["lines"]) for data in files.values())
    covered = sum(1 for data in files.values() for hits in data["lines"].values() if hits > 0)
    return {"files": files, "totals": {"percent_covered": covered / total * 100.0 if total else 0.0}}


def coverage_findings(coverage: dict, ctx: Context) -> list[str]:
    findings = []
    for file, data in sorted(coverage["files"].items()):
        if not ctx.in_scope(file):
            continue
        gated = ctx.gated_lines(file)
        findings.extend(f"{file}:{line} not covered" for line in data["missing_lines"] if gated is None or line in gated)
        findings.extend(
            f"{file}:{line} {missed} of {total} branches not taken"
            for line, missed, total in data["missing_branches"]
            if gated is None or line in gated
        )
    return findings
