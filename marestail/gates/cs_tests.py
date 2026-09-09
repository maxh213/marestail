import json
import re
import shutil
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.sax.saxutils import escape

from marestail import dotnet
from marestail.context import Context
from marestail.report import Result
from marestail.shell import tail

RESULTS_DIR = "cs-tests"
TRX = {"t": "http://microsoft.com/schemas/VisualStudio/TeamTest/2010"}
ATTRIBUTE = "ExcludeFromCodeCoverage"
FRAME = re.compile(r"(/\S+?\.cs):line (\d+)")


def run_gate(ctx: Context) -> Result:
    started = time.time()
    product, tests, error = dotnet.projects(ctx)
    if error:
        return Result("cs.tests", False, error, [], time.time() - started)
    findings = attribute_findings(ctx)
    if findings:
        return Result("cs.tests", False, f"{len(findings)} [{ATTRIBUTE}] in sources; exclusions belong in [dotnet] coverage_exclude", findings, time.time() - started)
    results = ctx.work / RESULTS_DIR
    shutil.rmtree(results, ignore_errors=True)
    settings = write_runsettings(ctx, product, tests)
    code, output = dotnet.dotnet(ctx, [
        "test", str(tests), "--nologo", "--collect:XPlat Code Coverage",
        "--settings", str(settings), "--results-directory", str(results), "--logger", "trx;LogFileName=tests.trx",
    ])
    trx = results / "tests.trx"
    if code != 0 or not trx.exists():
        return Result("cs.tests", False, dotnet.hint(code, output) or "tests failed", failed_tests(ctx, trx) or tail(output), time.time() - started)
    counters = ET.parse(trx).getroot().find("t:ResultSummary/t:Counters", TRX)
    passed = int(counters.get("passed", 0)) if counters is not None else 0
    if passed == 0:
        return Result("cs.tests", False, "no test passed; a run that executes nothing is not green", tail(output), time.time() - started)
    reports = sorted(results.glob("*/coverage.json"))
    if not reports:
        return Result("cs.tests", False, "no coverage report; reference coverlet.collector from the test project", tail(output), time.time() - started)
    coverage = normalise(ctx, json.loads(reports[0].read_text()))
    if not coverage["files"]:
        return Result("cs.tests", False, "coverage report names no source file; check [dotnet] root and coverage_exclude", tail(output), time.time() - started)
    (ctx.work / dotnet.COVERAGE_JSON).write_text(json.dumps(coverage))
    findings = coverage_findings(coverage, ctx)
    scope = " on changed files" if ctx.scope_changed else ""
    summary = f"{passed} passed, coverage {coverage['totals']['percent_covered']:.1f}%, {len(findings)} gaps{scope} (need 0)"
    return Result("cs.tests", not findings, summary, findings, time.time() - started)


def attribute_findings(ctx: Context) -> list[str]:
    findings = []
    for path in dotnet.sources(ctx):
        for number, line in enumerate(path.read_text(errors="replace").splitlines(), start=1):
            if ATTRIBUTE in line:
                findings.append(f"{dotnet.rel(ctx, path)}:{number} [{ATTRIBUTE}] hides code from the coverage ratio")
    return findings


def write_runsettings(ctx: Context, product: Path, tests: Path) -> Path:
    root = ctx.dotnet_root().resolve()
    excludes = [f"{root}/{pattern.strip('/')}" for pattern in dotnet.listify(ctx.dotnet("coverage_exclude", []))]
    if tests.parent != product.parent:
        excludes.append(f"{tests.parent.resolve()}/**/*.cs")
    body = f"""<?xml version="1.0" encoding="utf-8"?>
<RunSettings>
  <DataCollectionRunSettings>
    <DataCollectors>
      <DataCollector friendlyName="XPlat code coverage">
        <Configuration>
          <Format>json,opencover</Format>
          <IncludeTestAssembly>{str(tests.resolve() == product.resolve()).lower()}</IncludeTestAssembly>
          <ExcludeByFile>{escape(",".join(excludes))}</ExcludeByFile>
          <SkipAutoProps>true</SkipAutoProps>
          <UseSourceLink>false</UseSourceLink>
        </Configuration>
      </DataCollector>
    </DataCollectors>
  </DataCollectionRunSettings>
</RunSettings>
"""
    path = ctx.work / "coverlet.runsettings"
    path.write_text(body)
    return path


def failed_tests(ctx: Context, trx: Path) -> list[str]:
    if not trx.exists():
        return []
    findings = []
    for result in ET.parse(trx).getroot().findall(".//t:UnitTestResult", TRX):
        if result.get("outcome") in ("Passed", "NotExecuted"):
            continue
        message = (text_of(result, "t:Message").splitlines() or ["no message"])[0]
        frames = [(path, line) for path, line in FRAME.findall(text_of(result, "t:StackTrace")) if Path(path).is_relative_to(ctx.root)]
        where = f"{dotnet.rel(ctx, frames[0][0])}:{frames[0][1]}" if frames else f"{dotnet.rel(ctx, dotnet.test_project(ctx))}:1"
        findings.append(f"{where} {result.get('testName')} failed: {message[:200]}")
    return findings


def text_of(result: ET.Element, tag: str) -> str:
    element = result.find(f".//{tag}", TRX)
    return (element.text or "").strip() if element is not None else ""


def normalise(ctx: Context, raw: dict) -> dict:
    lines: dict[str, dict[int, int]] = {}
    branches: dict[str, dict[tuple[int, int, int], int]] = {}
    for documents in raw.values():
        for document, classes in documents.items():
            relative = dotnet.rel(ctx, document)
            path = ctx.root / relative
            if not path.is_relative_to(ctx.dotnet_root()) or dotnet.generated(ctx, path) or dotnet.is_test(ctx, path) or dotnet.coverage_excluded(ctx, relative):
                continue
            for methods in classes.values():
                for data in methods.values():
                    for number, hits in data.get("Lines", {}).items():
                        lines.setdefault(relative, {})[int(number)] = max(lines.get(relative, {}).get(int(number), 0), int(hits))
                    for branch in data.get("Branches", []):
                        key = (int(branch["Line"]), int(branch["Offset"]), int(branch["Path"]))
                        branches.setdefault(relative, {})[key] = max(branches.get(relative, {}).get(key, 0), int(branch["Hits"]))
    files = {}
    for relative in sorted(lines):
        files[relative] = {
            "lines": {str(number): hits for number, hits in sorted(lines[relative].items())},
            "missing_lines": sorted(number for number, hits in lines[relative].items() if hits == 0),
            "missing_branches": [[line, arm] for (line, _, arm), hits in sorted(branches.get(relative, {}).items()) if hits == 0],
        }
    total = sum(len(data["lines"]) for data in files.values())
    covered = sum(1 for data in files.values() for hits in data["lines"].values() if hits > 0)
    return {"files": files, "totals": {"percent_covered": covered / total * 100.0 if total else 0.0}}


def coverage_findings(coverage: dict, ctx: Context) -> list[str]:
    findings = []
    for file, data in sorted(coverage["files"].items()):
        if ctx.scope_changed and file not in ctx.changed:
            continue
        findings.extend(f"{file}:{line} not covered" for line in data["missing_lines"])
        findings.extend(f"{file}:{line} branch arm {arm} not taken" for line, arm in data["missing_branches"])
    return findings
