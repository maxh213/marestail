import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest

from marestail import dotnet
from marestail.gates import cs_tests
from marestail.report import Result
from tests.conftest import gate_shape, make_context

NS = "http://microsoft.com/schemas/VisualStudio/TeamTest/2010"


def view(result: Result) -> tuple[str, bool, str, list[str]]:
    return gate_shape(result)


def project(root: Path, section: dict[str, Any] | None = None, extra: dict[str, str] | None = None, **fields: Any) -> Any:
    files = {"App/App.csproj": "", "AppTests/AppTests.csproj": "", "App/A.cs": "class A {}\n", "AppTests/T.cs": "", **(extra or {})}
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    ctx = make_context(root, {"dotnet": section or {}}, **fields)
    ctx.work.mkdir()
    return ctx


def result_xml(name: str, outcome: str, message: str | None = None, stack: str | None = None) -> str:
    parts = [
        f"<Message>{message}</Message>" if message is not None else "",
        f"<StackTrace>{stack}</StackTrace>" if stack is not None else "",
    ]
    return (
        f'<UnitTestResult testName="{name}" outcome="{outcome}"><Output><ErrorInfo>{"".join(parts)}</ErrorInfo></Output></UnitTestResult>'
    )


def trx(passed: int | None, *results: str) -> str:
    counters = f'<ResultSummary><Counters total="9" passed="{passed}" /></ResultSummary>' if passed is not None else ""
    return f'<TestRun xmlns="{NS}"><Results>{"".join(results)}</Results>{counters}</TestRun>'


def method(lines: dict[str, int], branches: list[tuple[int, int, int, int]] | None = None) -> dict[str, Any]:
    arms = [
        {"Line": line, "Offset": offset, "EndOffset": 0, "Path": path, "Ordinal": 0, "Hits": hits}
        for line, offset, path, hits in branches or []
    ]
    return {"Lines": lines, "Branches": arms}


def coverage(root: Path) -> dict[str, Any]:
    return {
        "App.dll": {
            str(root / "App" / "A.cs"): {
                "App.A": {"Run()": method({"3": 1, "4": 0}, [(4, 1, 0, 1), (4, 1, 1, 0)]), "Stop()": method({"4": 2, "9": 1})},
                "App.A/Inner": {"Go()": method({"12": 0}, [(12, 3, 0, 0)])},
            },
            str(root / "App" / "Empty.cs"): {"App.E": {"None()": {}}},
            str(root / "AppTests" / "T.cs"): {"T": {"Test()": method({"1": 1})}},
            str(root / "App" / "obj" / "G.cs"): {"G": {"M()": method({"1": 0})}},
        },
        "Lib.dll": {"/elsewhere/L.cs": {"L": {"M()": method({"1": 0})}}},
    }


@pytest.fixture(autouse=True)
def fresh_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dotnet, "PROJECTS", {})


class FakeDotnet:
    def __init__(self, reply: tuple[int, str], trx_text: str | None, report: dict[str, Any] | None = None) -> None:
        self.reply = reply
        self.trx_text = trx_text
        self.report = report
        self.calls: list[list[str]] = []

    def __call__(self, ctx: Any, args: list[str]) -> tuple[int, str]:
        if ctx is None:
            raise TypeError("ctx")
        self.calls.append(args)
        results = Path(args[7])
        assert not results.exists()
        results.mkdir()
        if self.trx_text is not None:
            (results / "tests.trx").write_text(self.trx_text)
        if self.report is not None:
            (results / "guid").mkdir()
            (results / "guid" / "coverage.json").write_text(json.dumps(self.report))
        return self.reply


def install(monkeypatch: pytest.MonkeyPatch, fake: FakeDotnet) -> FakeDotnet:
    monkeypatch.setattr(dotnet, "dotnet", fake)
    return fake


def test_reports_missing_projects(tmp_path: Path) -> None:
    result = cs_tests.run_gate(make_context(tmp_path))
    assert (result.gate, result.ok) == ("cs.tests", False)
    assert result.summary.startswith("set [dotnet] project and test_project")
    assert result.findings == []


def test_rejects_exclusion_attribute(tmp_path: Path) -> None:
    extra = {"App/B.cs": "using X;\n[ExcludeFromCodeCoverage]\nclass B {}\n", "AppTests/U.cs": "[ExcludeFromCodeCoverage]\n"}
    ctx = project(tmp_path, extra=extra)
    assert view(cs_tests.run_gate(ctx)) == (
        "cs.tests",
        False,
        "1 [ExcludeFromCodeCoverage] in sources; exclusions belong in [dotnet] coverage_exclude",
        ["App/B.cs:2 [ExcludeFromCodeCoverage] hides code from the coverage ratio"],
    )


def test_attribute_findings_scoped(tmp_path: Path) -> None:
    ctx = project(tmp_path, extra={"App/B.cs": "[ExcludeFromCodeCoverage]\n"}, scope_changed=True, changed={"App/A.cs"})
    assert cs_tests.attribute_findings(ctx) == []


def test_runs_tests_and_writes_coverage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = install(monkeypatch, FakeDotnet((0, "ok"), trx(3), coverage(tmp_path)))
    ctx = project(tmp_path)
    results = ctx.work / "cs-tests"
    results.mkdir()
    assert view(cs_tests.run_gate(ctx)) == (
        "cs.tests",
        False,
        "3 passed, coverage 75.0%, 3 gaps (need 0)",
        ["App/A.cs:12 not covered", "App/A.cs:4 branch arm 1 not taken", "App/A.cs:12 branch arm 0 not taken"],
    )
    settings = ctx.work / "coverlet.runsettings"
    assert fake.calls == [
        [
            "test",
            str(tmp_path / "AppTests" / "AppTests.csproj"),
            "--nologo",
            "--collect:XPlat Code Coverage",
            "--settings",
            str(settings),
            "--results-directory",
            str(results),
            "--logger",
            "trx;LogFileName=tests.trx",
        ]
    ]
    assert json.loads((ctx.work / dotnet.COVERAGE_JSON).read_text()) == {
        "files": {
            "App/A.cs": {
                "lines": {"3": 1, "4": 2, "9": 1, "12": 0},
                "missing_lines": [12],
                "missing_branches": [[4, 1], [12, 0]],
            }
        },
        "totals": {"percent_covered": 75.0},
    }


def test_scoped_summary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, FakeDotnet((0, ""), trx(1), coverage(tmp_path)))
    ctx = project(tmp_path, scope_changed=True, changed={"App/A.cs"}, changed_lines_map={"App/A.cs": {4}})
    assert view(cs_tests.run_gate(ctx)) == (
        "cs.tests",
        False,
        "1 passed, coverage 75.0%, 1 gaps on changed files (need 0)",
        ["App/A.cs:4 branch arm 1 not taken"],
    )


def test_green_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    report = {"App.dll": {str(tmp_path / "App" / "A.cs"): {"A": {"M()": method({"1": 1})}}}}
    install(monkeypatch, FakeDotnet((0, ""), trx(2), report))
    assert view(cs_tests.run_gate(project(tmp_path))) == ("cs.tests", True, "2 passed, coverage 100.0%, 0 gaps (need 0)", [])


def test_failed_tests_are_listed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    results = [
        result_xml(
            "T.Fails",
            "Failed",
            "Assert.Equal() Failure\nExpected: 1",
            f"at X in /usr/lib/Y.cs:line 3\n at T.Fails() in {tmp_path}/AppTests/T.cs:line 12",
        ),
        result_xml("T.Passes", "Passed"),
        result_xml("T.Skipped", "NotExecuted"),
        result_xml("T.Bare", "Error"),
        result_xml("T.Long", "Failed", "m" * 250, ""),
    ]
    install(monkeypatch, FakeDotnet((1, "Failed!\n"), trx(1, *results)))
    assert view(cs_tests.run_gate(project(tmp_path))) == (
        "cs.tests",
        False,
        "tests failed",
        [
            "AppTests/T.cs:12 T.Fails failed: Assert.Equal() Failure",
            "AppTests/AppTests.csproj:1 T.Bare failed: no message",
            "AppTests/AppTests.csproj:1 T.Long failed: " + "m" * 200,
        ],
    )


def test_missing_trx_uses_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, FakeDotnet((0, "line one\n\nline two\n"), None))
    assert view(cs_tests.run_gate(project(tmp_path))) == ("cs.tests", False, "tests failed", ["line one", "line two"])


def test_failure_hint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, FakeDotnet((127, "sh: dotnet: not found"), trx(0)))
    assert view(cs_tests.run_gate(project(tmp_path))) == (
        "cs.tests",
        False,
        f"dotnet unavailable: {dotnet.INSTALL_HINT}",
        ["sh: dotnet: not found"],
    )


@pytest.mark.parametrize("text", [trx(0), trx(None)])
def test_nothing_passed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    install(monkeypatch, FakeDotnet((0, "Total: 0"), text))
    assert view(cs_tests.run_gate(project(tmp_path))) == (
        "cs.tests",
        False,
        "no test passed; a run that executes nothing is not green",
        ["Total: 0"],
    )


def test_no_coverage_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, FakeDotnet((0, "out"), trx(1)))
    assert view(cs_tests.run_gate(project(tmp_path))) == (
        "cs.tests",
        False,
        "no coverage report; reference coverlet.collector from the test project",
        ["out"],
    )


def test_coverage_names_no_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, FakeDotnet((0, "out"), trx(1), {"App.dll": {"/elsewhere/L.cs": {"L": {"M()": method({"1": 1})}}}}))
    ctx = project(tmp_path)
    assert view(cs_tests.run_gate(ctx)) == (
        "cs.tests",
        False,
        "coverage report names no source file; check [dotnet] root and coverage_exclude",
        ["out"],
    )
    assert not (ctx.work / dotnet.COVERAGE_JSON).exists()


def test_runsettings_for_separate_projects(tmp_path: Path) -> None:
    ctx = project(tmp_path, section={"coverage_exclude": ["/Gen/", "A&B"]})
    path = cs_tests.write_runsettings(ctx, tmp_path / "App" / "App.csproj", tmp_path / "AppTests" / "AppTests.csproj")
    assert path == ctx.work / "coverlet.runsettings"
    text = path.read_text()
    assert text.startswith('<?xml version="1.0" encoding="utf-8"?>\n<RunSettings>\n')
    assert "          <IncludeTestAssembly>false</IncludeTestAssembly>\n" in text
    assert f"          <ExcludeByFile>{tmp_path}/Gen,{tmp_path}/A&amp;B,{tmp_path}/AppTests/**/*.cs</ExcludeByFile>\n" in text
    assert text.endswith("  </DataCollectionRunSettings>\n</RunSettings>\n")


def test_runsettings_for_shared_project(tmp_path: Path) -> None:
    ctx = project(tmp_path)
    csproj = tmp_path / "App" / "App.csproj"
    text = cs_tests.write_runsettings(ctx, csproj, csproj).read_text()
    assert "<IncludeTestAssembly>true</IncludeTestAssembly>" in text
    assert "<ExcludeByFile></ExcludeByFile>" in text
    assert "<Format>json,opencover</Format>" in text


def test_runsettings_template_is_stable() -> None:
    assert cs_tests.RUNSETTINGS.format(include="x", exclude="y") == (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        "<RunSettings>\n"
        "  <DataCollectionRunSettings>\n"
        "    <DataCollectors>\n"
        '      <DataCollector friendlyName="XPlat code coverage">\n'
        "        <Configuration>\n"
        "          <Format>json,opencover</Format>\n"
        "          <IncludeTestAssembly>x</IncludeTestAssembly>\n"
        "          <ExcludeByFile>y</ExcludeByFile>\n"
        "          <SkipAutoProps>true</SkipAutoProps>\n"
        "          <UseSourceLink>false</UseSourceLink>\n"
        "        </Configuration>\n"
        "      </DataCollector>\n"
        "    </DataCollectors>\n"
        "  </DataCollectionRunSettings>\n"
        "</RunSettings>\n"
    )


def test_failed_tests_without_trx(tmp_path: Path) -> None:
    assert cs_tests.failed_tests(make_context(tmp_path), tmp_path / "missing.trx", tmp_path / "T.csproj") == []


def test_normalise_excludes_configured_files(tmp_path: Path) -> None:
    ctx = project(tmp_path, section={"coverage_exclude": ["App/A.cs"]})
    assert cs_tests.normalise(ctx, coverage(tmp_path)) == {"files": {}, "totals": {"percent_covered": 0.0}}


def test_normalise_outside_dotnet_root(tmp_path: Path) -> None:
    ctx = project(tmp_path, section={"root": "App"})
    raw = {
        "x": {
            str(tmp_path / "AppTests" / "T2.cs"): {"T": {"M()": method({"1": 0})}},
            str(tmp_path / "App" / "A.cs"): {"A": {"M()": method({"2": 0})}},
        }
    }
    assert cs_tests.normalise(ctx, raw)["files"] == {"App/A.cs": {"lines": {"2": 0}, "missing_lines": [2], "missing_branches": []}}


def test_coverage_findings_scope(tmp_path: Path) -> None:
    data = {
        "files": {
            "B.cs": {"missing_lines": [1, 2], "missing_branches": [[2, 0], [3, 1]]},
            "A.cs": {"missing_lines": [5], "missing_branches": []},
            "C.cs": {"missing_lines": [9], "missing_branches": []},
        }
    }
    assert cs_tests.coverage_findings(data, make_context(tmp_path)) == [
        "A.cs:5 not covered",
        "B.cs:1 not covered",
        "B.cs:2 not covered",
        "B.cs:2 branch arm 0 not taken",
        "B.cs:3 branch arm 1 not taken",
        "C.cs:9 not covered",
    ]
    scoped = make_context(tmp_path, scope_changed=True, changed={"B.cs", "C.cs"}, changed_lines_map={"B.cs": {2}})
    assert cs_tests.coverage_findings(data, scoped) == ["B.cs:2 not covered", "B.cs:2 branch arm 0 not taken", "C.cs:9 not covered"]


def test_text_of() -> None:
    element = ET.fromstring(f'<R xmlns="{NS}"><Output><Message>  hi  </Message><StackTrace /></Output></R>')
    assert cs_tests.text_of(element, "t:Message") == "hi"
    assert cs_tests.text_of(element, "t:StackTrace") == ""
    assert cs_tests.text_of(element, "t:Missing") == ""


def test_passed_attribute_without_passed() -> None:
    counters = ET.fromstring("<Counters total='9' />")
    assert cs_tests.passed_attribute(counters) == 0
    assert cs_tests.passed_attribute(None) == 0
    counters.set("passed", "4")
    assert cs_tests.passed_attribute(counters) == 4


def test_local_name_and_nested_trx() -> None:
    assert cs_tests.local_name("t:Counters") == "Counters"
    assert cs_tests.local_name("a:b:c") == "c"
    assert cs_tests.local_name("plain") == "plain"
    assert cs_tests.local_name(":name") == "name"
    assert cs_tests.nested_trx(True, "body") == ".//body"
    assert cs_tests.nested_trx(False, "body") == "body"
    assert cs_tests.trx_path("t:ResultSummary/t:Counters") == "{*}ResultSummary/{*}Counters"
    assert cs_tests.trx_path(".//t:UnitTestResult") == ".//{*}UnitTestResult"


def test_trim_glob_only_strips_slashes() -> None:
    assert cs_tests.trim_glob("/XGen/") == "XGen"
    assert cs_tests.trim_glob("Gen") == "Gen"


def test_existing_or_zero() -> None:
    assert cs_tests.existing_or_zero({}, 3) == 0
    assert cs_tests.existing_or_zero({3: 9}, 3) == 9


def test_raise_to_keeps_the_higher_hit() -> None:
    tables: dict[str, dict[Any, int]] = {}
    cs_tests.raise_to(tables, "A.cs", 3, 2)
    cs_tests.raise_to(tables, "A.cs", 3, 1)
    assert tables == {"A.cs": {3: 2}}
