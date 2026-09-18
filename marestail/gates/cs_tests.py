import json
import re
import shutil
import time
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from marestail import dotnet
from marestail.context import Context
from marestail.report import Result
from marestail.shell import tail

GATE = "cs.tests"
RESULTS_DIR = "cs-tests"
TRX_FILE = "tests.trx"
ANY_XML_NAMESPACE = "{*}"
ATTRIBUTE = "ExcludeFromCodeCoverage"
FRAME = re.compile(r"(/\S+?\.cs):line (\d+)")
RUNSETTINGS = """<?xml version="1.0" encoding="utf-8"?>
<RunSettings>
  <DataCollectionRunSettings>
    <DataCollectors>
      <DataCollector friendlyName="XPlat code coverage">
        <Configuration>
          <Format>json,opencover</Format>
          <IncludeTestAssembly>{include}</IncludeTestAssembly>
          <ExcludeByFile>{exclude}</ExcludeByFile>
          <SkipAutoProps>true</SkipAutoProps>
          <UseSourceLink>false</UseSourceLink>
        </Configuration>
      </DataCollector>
    </DataCollectors>
  </DataCollectionRunSettings>
</RunSettings>
"""


def run_gate(ctx: Context) -> Result:
    started = time.time()
    found = dotnet.project_pair(ctx)
    if isinstance(found, str):
        return Result(GATE, False, found, [], time.time() - started)
    findings = attribute_findings(ctx)
    if findings:
        summary = f"{len(findings)} [{ATTRIBUTE}] in sources; exclusions belong in [dotnet] coverage_exclude"
        return Result(GATE, False, summary, findings, time.time() - started)
    return run_tests(ctx, found, started)


def failed(message: str, output: str, started: float) -> Result:
    return Result(GATE, False, message, tail(output), time.time() - started)


def run_tests(ctx: Context, pair: tuple[Path, Path], started: float) -> Result:
    product, tests = pair
    results = ctx.work / RESULTS_DIR
    shutil.rmtree(results, ignore_errors=True)
    settings = write_runsettings(ctx, product, tests)
    code, output = dotnet.dotnet(ctx, test_args(tests, settings, results))
    if code != 0 or not (results / TRX_FILE).exists():
        return test_failure(ctx, (code, output), tests, started)
    return passed_run(ctx, results, output, started)


def test_failure(ctx: Context, outcome: tuple[int, str], tests: Path, started: float) -> Result:
    code, output = outcome
    findings = failed_tests(ctx, ctx.work / RESULTS_DIR / TRX_FILE, tests) or tail(output)
    return Result(GATE, False, dotnet.hint(code, output) or "tests failed", findings, time.time() - started)


def test_args(tests: Path, settings: Path, results: Path) -> list[str]:
    return [
        "test",
        str(tests),
        "--nologo",
        "--collect:XPlat Code Coverage",
        "--settings",
        str(settings),
        "--results-directory",
        str(results),
        "--logger",
        f"trx;LogFileName={TRX_FILE}",
    ]


def passed_run(ctx: Context, results: Path, output: str, started: float) -> Result:
    passed = passed_count(results / TRX_FILE)
    if passed == 0:
        return failed("no test passed; a run that executes nothing is not green", output, started)
    reports = sorted(results.glob("*/coverage.json"))
    if not reports:
        return failed("no coverage report; reference coverlet.collector from the test project", output, started)
    return coverage_run(ctx, normalise(ctx, json.loads(reports[0].read_text())), (passed, output), started)


def passed_count(trx: Path) -> int:
    counters = ET.parse(trx).getroot().find(trx_path("t:ResultSummary/t:Counters"))
    return int(counters.get("passed", 0)) if counters is not None else 0


def coverage_run(ctx: Context, coverage: dict[str, Any], outcome: tuple[int, str], started: float) -> Result:
    passed, output = outcome
    if not coverage["files"]:
        return failed("coverage report names no source file; check [dotnet] root and coverage_exclude", output, started)
    (ctx.work / dotnet.COVERAGE_JSON).write_text(json.dumps(coverage))
    findings = coverage_findings(coverage, ctx)
    scope = " on changed files" if ctx.scoped else ""
    summary = f"{passed} passed, coverage {coverage['totals']['percent_covered']:.1f}%, {len(findings)} gaps{scope} (need 0)"
    return Result(GATE, not findings, summary, findings, time.time() - started)


def has_attribute(line: str) -> bool:
    return ATTRIBUTE in line


def attribute_findings(ctx: Context) -> list[str]:
    return [
        f"{dotnet.rel(ctx, path)}:{number} [{ATTRIBUTE}] hides code from the coverage ratio"
        for path in dotnet.in_scope(ctx, dotnet.sources(ctx))
        for number in dotnet.matching_lines(path, has_attribute)
    ]


def write_runsettings(ctx: Context, product: Path, tests: Path) -> Path:
    root = ctx.dotnet_root().resolve()
    excludes = [f"{root}/{pattern.strip('/')}" for pattern in dotnet.listify(ctx.dotnet("coverage_exclude", []))]
    if tests.parent != product.parent:
        excludes.append(f"{tests.parent.resolve()}/**/*.cs")
    include = str(tests.resolve() == product.resolve()).lower()
    path = ctx.work / "coverlet.runsettings"
    path.write_text(RUNSETTINGS.format(include=include, exclude=escape(",".join(excludes))))
    return path


def failed_tests(ctx: Context, trx: Path, tests: Path) -> list[str]:
    if not trx.exists():
        return []
    return [
        failure_line(ctx, result, tests)
        for result in ET.parse(trx).getroot().findall(trx_path(".//t:UnitTestResult"))
        if result.get("outcome") not in ("Passed", "NotExecuted")
    ]


def failure_line(ctx: Context, result: ET.Element, tests: Path) -> str:
    message = (text_of(result, "t:Message").splitlines() or ["no message"])[0]
    return f"{failure_where(ctx, result, tests)} {result.get('testName')} failed: {message[:200]}"


def failure_where(ctx: Context, result: ET.Element, tests: Path) -> str:
    frames = [(path, line) for path, line in FRAME.findall(text_of(result, "t:StackTrace")) if Path(path).is_relative_to(ctx.root)]
    if frames:
        return f"{dotnet.rel(ctx, frames[0][0])}:{frames[0][1]}"
    return f"{dotnet.rel(ctx, tests)}:1"


def text_of(result: ET.Element, tag: str) -> str:
    element = result.find(trx_path(f".//{tag}"))
    return (element.text or "").strip() if element is not None else ""


def trx_path(path: str) -> str:
    nested = path.startswith(".//")
    names = [part.split(":")[-1] for part in path.removeprefix(".//").split("/")]
    body = "/".join(ANY_XML_NAMESPACE + name for name in names)
    return f".//{body}" if nested else body


def normalise(ctx: Context, raw: dict[str, Any]) -> dict[str, Any]:
    lines: dict[str, dict[Any, int]] = {}
    branches: dict[str, dict[Any, int]] = {}
    for relative, classes in documents(ctx, raw):
        for data in methods_of(classes):
            merge(lines, branches, relative, data)
    files = {relative: file_summary(lines[relative], branches.get(relative, {})) for relative in sorted(lines)}
    return {"files": files, "totals": {"percent_covered": percent(files)}}


def documents(ctx: Context, raw: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    for per_module in raw.values():
        for document, classes in per_module.items():
            relative = dotnet.rel(ctx, document)
            if counted(ctx, relative):
                yield relative, classes


def counted(ctx: Context, relative: str) -> bool:
    path = ctx.root / relative
    return (
        path.is_relative_to(ctx.dotnet_root())
        and not dotnet.generated(ctx, path)
        and not dotnet.is_test(ctx, path)
        and not dotnet.coverage_excluded(ctx, relative)
    )


def methods_of(classes: dict[str, Any]) -> list[dict[str, Any]]:
    return [data for methods in classes.values() for data in methods.values()]


def raise_to(tables: dict[str, dict[Any, int]], relative: str, key: Any, hits: int) -> None:
    table = tables.setdefault(relative, {})
    table[key] = max(table.get(key, 0), hits)


def merge(lines: dict[str, dict[Any, int]], branches: dict[str, dict[Any, int]], relative: str, data: dict[str, Any]) -> None:
    for number, hits in data.get("Lines", {}).items():
        raise_to(lines, relative, int(number), int(hits))
    for branch in data.get("Branches", []):
        raise_to(branches, relative, (int(branch["Line"]), int(branch["Offset"]), int(branch["Path"])), int(branch["Hits"]))


def file_summary(table: dict[Any, int], arms: dict[Any, int]) -> dict[str, Any]:
    return {
        "lines": {str(number): hits for number, hits in sorted(table.items())},
        "missing_lines": sorted(number for number, hits in table.items() if hits == 0),
        "missing_branches": untaken(arms),
    }


def untaken(arms: dict[Any, int]) -> list[list[int]]:
    return [[line, arm] for (line, _, arm), hits in sorted(arms.items()) if hits == 0]


def percent(files: dict[str, Any]) -> float:
    total = sum(len(data["lines"]) for data in files.values())
    return covered_lines(files) / total * 100.0 if total else 0.0


def covered_lines(files: dict[str, Any]) -> int:
    return sum(1 for data in files.values() for hits in data["lines"].values() if hits > 0)


def coverage_findings(coverage: dict[str, Any], ctx: Context) -> list[str]:
    return [
        found
        for file, data in sorted(coverage["files"].items())
        if ctx.in_scope(file)
        for found in file_findings(file, data, ctx.gated_lines(file))
    ]


def gated_in(line: int, gated: set[int] | None) -> bool:
    return gated is None or line in gated


def file_findings(file: str, data: dict[str, Any], gated: set[int] | None) -> list[str]:
    return line_gaps(file, data["missing_lines"], gated) + branch_gaps(file, data["missing_branches"], gated)


def line_gaps(file: str, missing_lines: list[int], gated: set[int] | None) -> list[str]:
    return [f"{file}:{line} not covered" for line in missing_lines if gated_in(line, gated)]


def branch_gaps(file: str, missing_branches: list[list[int]], gated: set[int] | None) -> list[str]:
    return [f"{file}:{line} branch arm {arm} not taken" for line, arm in missing_branches if gated_in(line, gated)]
