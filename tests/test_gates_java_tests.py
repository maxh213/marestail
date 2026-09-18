import itertools
import json
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, cast

import pytest

from marestail import java
from marestail.context import Context
from marestail.gates import java_tests
from marestail.report import Result
from tests.conftest import make_context

APP = "src/main/java/app/App.java"
CORE = "src/main/java/app/core/Core.java"
TEST = "src/test/java/app/AppTest.java"
PASSING = """<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="app.AppTest" tests="3" failures="0" errors="0" skipped="1">
  <properties><property name="java.version" value="21"/></properties>
  <testcase name="adds" classname="app.AppTest" time="0.01"/>
  <testcase name="subtracts" classname="app.AppTest" time="0.01"><system-out>ok</system-out></testcase>
  <testcase name="later" classname="app.AppTest" time="0"><skipped message="disabled"/></testcase>
</testsuite>
"""
FAILING = """<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="app.AppTest" tests="3" failures="1" errors="1">
  <testcase name="adds" classname="app.AppTest"/>
  <testcase name="fails" classname="app.AppTest">
    <failure message="expected: &lt;1&gt; but was: &lt;2&gt;&#10;second line" type="org.opentest4j.AssertionFailedError">
org.opentest4j.AssertionFailedError: expected: &lt;1&gt; but was: &lt;2&gt;
\tat org.junit.jupiter.api.AssertEquals.failNotEqual(AssertEquals.java:197)
\tat app.AppTest.fails(AppTest.java:14)
</failure>
  </testcase>
  <testcase name="errors" classname="app.AppTest">
    <error type="java.lang.NullPointerException">java.lang.NullPointerException
\tat app.App.run(App.java:7)
\tat app.AppTest.errors(AppTest.java:20)</error>
  </testcase>
</testsuite>
"""
JACOCO = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<!DOCTYPE report PUBLIC "-//JACOCO//DTD Report 1.1//EN" "report.dtd">
<report name="app">
  <sessioninfo id="host-1" start="1" dump="2"/>
  <package name="app">
    <class name="app/App" sourcefilename="App.java"><counter type="LINE" missed="1" covered="3"/></class>
    <sourcefile name="App.java">
      <line nr="3" mi="0" ci="4" mb="0" cb="0"/>
      <line nr="5" mi="0" ci="3" mb="1" cb="1"/>
      <line nr="7" mi="2" ci="0" mb="2" cb="0"/>
      <line nr="9" mi="0" ci="1" mb="0" cb="2"/>
      <counter type="LINE" missed="1" covered="3"/>
    </sourcefile>
    <sourcefile name="Missing.java"><line nr="1" mi="1" ci="0" mb="0" cb="0"/></sourcefile>
  </package>
  <package name="app/core">
    <sourcefile name="Core.java"><line nr="2" mi="0" ci="5" mb="0" cb="0"/></sourcefile>
  </package>
</report>
"""
APP_COVERAGE = {
    "lines": {"3": 4, "5": 3, "7": 0, "9": 1},
    "missing_lines": [7],
    "missing_branches": [[5, 1, 2], [7, 2, 2]],
}
CORE_COVERAGE = {"lines": {"2": 5}, "missing_lines": [], "missing_branches": []}
GAPS = [f"{APP}:7 not covered", f"{APP}:5 1 of 2 branches not taken", f"{APP}:7 2 of 2 branches not taken"]


@pytest.fixture(autouse=True)
def clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "time", itertools.count(3.0, 0.75).__next__)


def write(path: Path, text: str = "class X {}\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def project(root: Path) -> None:
    write(root / "pom.xml", "<project/>")
    for relative in (APP, CORE, TEST):
        write(root / relative)
    (root / ".marestail").mkdir()


def fields(result: Result) -> tuple[str, bool, str, list[str], float]:
    return result.gate, result.ok, result.summary, result.findings, result.seconds


def fake_mvn(monkeypatch: pytest.MonkeyPatch, suites: list[str], jacoco: str | None, reply: tuple[int, str] = (0, "")) -> list[Any]:
    calls: list[Any] = []

    def mvn(ctx: Context, args: list[str], timeout: int = 1800, pom: Path | None = None) -> tuple[int, str]:
        calls.append(args)
        build = java.build_dir(ctx)
        for number, suite in enumerate(suites):
            write(build / "surefire-reports" / f"TEST-app.Suite{number}.xml", suite)
        if jacoco is not None:
            write(build / "site" / "jacoco" / "jacoco.xml", jacoco)
        return reply

    monkeypatch.setattr(java, "mvn", mvn)
    return calls


def test_needs_pom(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_mvn(monkeypatch, [], None)
    assert fields(java_tests.run_gate(make_context(tmp_path))) == ("java.tests", False, java.require_pom(make_context(tmp_path)), [], 0.0)
    assert calls == []


def test_green_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project(tmp_path)
    stale = [
        write(tmp_path / "target/surefire-reports/TEST-old.xml", FAILING),
        write(tmp_path / "target/site/jacoco/index.html"),
        write(tmp_path / ".marestail/java-jacoco.exec", "old"),
    ]
    calls = fake_mvn(monkeypatch, [PASSING, PASSING], JACOCO, (0, "BUILD SUCCESS"))
    result = java_tests.run_gate(make_context(tmp_path))
    assert fields(result) == ("java.tests", False, "4 passed, coverage 80.0%, 3 gaps (need 0)", GAPS, 0.75)
    data = tmp_path / ".marestail" / "java-jacoco.exec"
    plugin = "org.jacoco:jacoco-maven-plugin:0.8.15"
    assert calls == [[f"{plugin}:prepare-agent", "test", f"{plugin}:report", f"-Djacoco.destFile={data}", f"-Djacoco.dataFile={data}"]]
    assert [path.exists() for path in stale] == [False, False, False]
    assert (tmp_path / ".marestail" / "java-jacoco.xml").read_text() == JACOCO
    saved = json.loads((tmp_path / ".marestail" / "java-coverage.json").read_text())
    assert saved == {"files": {APP: APP_COVERAGE, CORE: CORE_COVERAGE}, "totals": {"percent_covered": 80.0}}


def test_scoped_run_with_configured_build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project(tmp_path)
    calls = fake_mvn(monkeypatch, [PASSING], JACOCO)
    ctx = make_context(
        tmp_path,
        {"java": {"build_dir": "out", "jacoco_version": "0.8.99"}},
        scope_changed=True,
        changed={CORE},
        changed_lines_map={CORE: {2}},
    )
    assert fields(java_tests.run_gate(ctx)) == ("java.tests", True, "2 passed, coverage 80.0%, 0 gaps on changed files (need 0)", [], 0.75)
    assert calls[0][0] == "org.jacoco:jacoco-maven-plugin:0.8.99:prepare-agent"
    assert (tmp_path / "out" / "site" / "jacoco" / "jacoco.xml").exists()


FAILURES = [
    f"{TEST}:14 app.AppTest.fails failed: expected: <1> but was: <2>",
    f"{APP}:7 app.AppTest.errors failed: java.lang.NullPointerException",
]


@pytest.mark.parametrize(
    ("reply", "suites", "summary", "findings"),
    [
        ((1, "[ERROR] Tests run: 3\n\nBUILD FAILURE"), [FAILING], "build or tests failed", FAILURES),
        ((0, "odd"), [FAILING], "build or tests failed", FAILURES),
        ((1, "[ERROR] COMPILATION ERROR\n\nBUILD FAILURE"), [], "build or tests failed", ["[ERROR] COMPILATION ERROR", "BUILD FAILURE"]),
        ((127, "sh: mvn: not found"), [], f"maven unavailable: {java.INSTALL['maven']}", ["sh: mvn: not found"]),
        ((0, "Tests run: 0\n"), [], "no test passed; a run that executes nothing is not green", ["Tests run: 0"]),
    ],
)
def test_failed_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reply: tuple[int, str], suites: list[str], summary: str, findings: list[str]
) -> None:
    project(tmp_path)
    fake_mvn(monkeypatch, suites, JACOCO, reply)
    assert fields(java_tests.run_gate(make_context(tmp_path))) == ("java.tests", False, summary, findings, 0.75)


def test_needs_jacoco_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project(tmp_path)
    fake_mvn(monkeypatch, [PASSING], None, (0, "done"))
    assert fields(java_tests.run_gate(make_context(tmp_path))) == (
        "java.tests",
        False,
        "no JaCoCo report; if the pom sets the surefire <argLine>, start it with @{argLine}",
        ["done"],
        0.75,
    )


def test_needs_covered_sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project(tmp_path)
    fake_mvn(monkeypatch, [PASSING], JACOCO, (0, "done"))
    ctx = make_context(tmp_path, {"java": {"coverage_exclude": ["src/main/java/app"]}})
    assert fields(java_tests.run_gate(ctx)) == (
        "java.tests",
        False,
        "coverage report names no source file; check [java] root, sources and coverage_exclude",
        ["done"],
        0.75,
    )
    assert not (tmp_path / ".marestail" / "java-coverage.json").exists()


def test_test_problem_passes_green_runs() -> None:
    assert java_tests.test_problem(0, "", 1, [], 0.0) is None


def test_read_suites(tmp_path: Path) -> None:
    project(tmp_path)
    suites = [write(tmp_path / "a.xml", PASSING), write(tmp_path / "b.xml", FAILING)]
    assert java_tests.read_suites(make_context(tmp_path), suites) == (3, FAILURES)
    assert java_tests.read_suites(make_context(tmp_path), []) == (0, [])


def problem(attributes: str, text: str = "") -> ET.Element:
    return ET.fromstring(f"<failure {attributes}>{text}</failure>")


@pytest.mark.parametrize(
    ("attributes", "expected"),
    [
        ('message="boom"', "boom"),
        ('message="first&#10;second"', "first"),
        ('message="" type="java.lang.AssertionError"', "java.lang.AssertionError"),
        ('type="T"', "T"),
        ("", "no message"),
    ],
)
def test_first_message(attributes: str, expected: str) -> None:
    assert java_tests.first_message(problem(attributes)) == expected


def test_failure_line_truncates_message(tmp_path: Path) -> None:
    write(tmp_path / "pom.xml", "<project/>")
    case = ET.fromstring('<testcase name="t" classname="x.Y"/>')
    line = java_tests.failure_line(make_context(tmp_path), case, problem(f'message="{"m" * 250}"'))
    assert line == f"pom.xml:1 x.Y.t failed: {'m' * 200}"


def test_first_problem() -> None:
    case = ET.fromstring("<testcase><system-out/><error type='E'/><failure type='F'/></testcase>")
    assert cast(ET.Element, java_tests.first_problem(case)).get("type") == "E"
    assert java_tests.first_problem(ET.fromstring("<testcase><skipped/></testcase>")) is None


@pytest.mark.parametrize(
    ("xml", "passed"),
    [("<testcase/>", True), ("<testcase><skipped/></testcase>", False), ("<testcase><failure/></testcase>", False)],
)
def test_passed_case(xml: str, passed: bool) -> None:
    assert java_tests.passed_case(ET.fromstring(xml)) is passed


@pytest.mark.parametrize(
    ("classname", "trace", "expected"),
    [
        ("app.AppTest", "\tat app.core.Core.go(Core.java:3)\n\tat app.App.run(App.java:7)", f"{CORE}:3"),
        ("app.AppTest", "\tat org.lib.Lib.x(Lib.java:1)\n\tat app.AppTest$Nested.run(AppTest.java:22)", f"{TEST}:22"),
        ("app.AppTest$Nested", "\tat org.lib.Lib.x(Lib.java:1)", f"{TEST}:1"),
        ("app.Gone", "no frames", "pom.xml:1"),
        (None, "", "pom.xml:1"),
    ],
)
def test_where(tmp_path: Path, classname: str | None, trace: str, expected: str) -> None:
    project(tmp_path)
    case = ET.Element("testcase", {} if classname is None else {"classname": classname})
    assert java_tests.where(make_context(tmp_path), case, trace) == expected


def test_normalise(tmp_path: Path) -> None:
    project(tmp_path)
    report = ET.fromstring(JACOCO.split("\n", 2)[2])
    ctx = make_context(tmp_path, {"java": {"coverage_exclude": ["src/main/java/app/core"]}})
    assert java_tests.normalise(ctx, report) == {"files": {APP: APP_COVERAGE}, "totals": {"percent_covered": 75.0}}


def test_normalise_empty(tmp_path: Path) -> None:
    empty = ET.fromstring("<report><package name='app'><sourcefile name='Nope.java'/></package><package/></report>")
    assert java_tests.normalise(make_context(tmp_path), empty) == {"files": {}, "totals": {"percent_covered": 0.0}}


def test_source_coverage_defaults() -> None:
    source = ET.fromstring("<sourcefile><line/><line nr='4' ci='1' mb='3'/></sourcefile>")
    assert java_tests.source_coverage(source) == {"lines": {"0": 0, "4": 1}, "missing_lines": [0], "missing_branches": [[4, 3, 3]]}


def test_percent_covered() -> None:
    files: dict[str, dict[str, Any]] = {"a": {"lines": {"1": 0, "2": 2, "3": 1}}, "b": {"lines": {}}}
    assert java_tests.covered_lines(files) == 2
    assert java_tests.percent_covered(files) == pytest.approx(200 / 3)
    assert java_tests.percent_covered({"b": {"lines": {}}}) == 0.0


def test_coverage_findings_scoped(tmp_path: Path) -> None:
    coverage = {"files": {CORE: {**CORE_COVERAGE, "missing_lines": [4]}, APP: APP_COVERAGE}}
    ctx = make_context(tmp_path, scope_changed=True, changed={APP, CORE}, changed_lines_map={APP: {5}})
    assert java_tests.coverage_findings(coverage, ctx) == [f"{APP}:5 1 of 2 branches not taken", f"{CORE}:4 not covered"]
    unscoped = java_tests.coverage_findings(coverage, make_context(tmp_path))
    assert unscoped == [*GAPS, f"{CORE}:4 not covered"]
    outside = make_context(tmp_path, scope_changed=True, changed={"other"})
    assert java_tests.coverage_findings(coverage, outside) == []


@pytest.mark.parametrize(("line", "gated", "expected"), [(3, None, True), (3, {3}, True), (3, {4}, False), (3, set(), False)])
def test_gated_in(line: int, gated: set[int] | None, expected: bool) -> None:
    assert java_tests.gated_in(line, gated) is expected
