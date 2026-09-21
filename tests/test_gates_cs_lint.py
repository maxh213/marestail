import json
from pathlib import Path
from typing import Any

import pytest

from marestail import dotnet
from marestail.gates import cs_lint
from marestail.report import Result
from tests.conftest import gate_shape, make_context

SOURCES = {
    "App/App.csproj": "",
    "AppTests/AppTests.csproj": "",
    "App/A.cs": 'class A\n{\n#pragma warning disable CA1000\n    [SuppressMessage("x")]\n}\n',
    "App/B.cs": "class B {}\n",
    "AppTests/T.cs": "[ SuppressMessage]\n",
}


def view(result: Result) -> tuple[str, bool, str, list[str]]:
    return gate_shape(result)


def project(root: Path, **fields: Any) -> Any:
    for name, text in SOURCES.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    ctx = make_context(root, **fields)
    ctx.work.mkdir()
    return ctx


def diagnostic(uri: str | None, line: int | None = 7, **fields: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"ruleId": "CA1822", "message": {"text": "Member  'X'\n can be static"}, **fields}
    if uri is not None:
        physical: dict[str, Any] = {"artifactLocation": {"uri": uri}}
        if line is not None:
            physical["region"] = {"startLine": line}
        result["locations"] = [{"physicalLocation": physical}]
    return result


def sarif(*results: dict[str, Any], version: str = "2.1.0") -> str:
    return json.dumps({"version": version, "runs": [{"results": list(results)}]})


@pytest.fixture(autouse=True)
def fresh_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dotnet, "PROJECTS", {})


def install(monkeypatch: pytest.MonkeyPatch, logs: dict[str, str | None], reply: tuple[int, str] = (0, "")) -> list[Any]:
    calls: list[Any] = []

    def fake(ctx: Any, args: list[str], **options: Any) -> tuple[int, str]:
        if ctx is None:
            raise TypeError("ctx")
        calls.append((args, options))
        stem = Path(args[1]).stem
        target = Path(args[10].removeprefix("-p:ErrorLog=").removesuffix("%2cversion=2.1"))
        assert not target.exists()
        text = logs.get(stem)
        if text is not None:
            target.write_text(text)
        return reply

    monkeypatch.setattr(dotnet, "dotnet", fake)
    return calls


SUPPRESSED = [
    "App/A.cs:3 analyzer suppressed in source; fix the code instead",
    "App/A.cs:4 analyzer suppressed in source; fix the code instead",
    "AppTests/T.cs:1 analyzer suppressed in source; fix the code instead",
]


def test_skips_when_no_cs_changed(tmp_path: Path) -> None:
    ctx = project(tmp_path, scope_changed=True, changed={"README.md"})
    assert view(cs_lint.run_gate(ctx)) == ("cs.lint", True, "skipped: no changed C# files", [])


def test_reports_missing_projects(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    result = cs_lint.run_gate(ctx)
    assert view(result) == (
        "cs.lint",
        False,
        "set [dotnet] project and test_project in marestail.toml (.csproj files under .: none)",
        [],
    )


def test_builds_both_projects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = install(monkeypatch, {"App": sarif(), "AppTests": sarif()})
    ctx = project(tmp_path)
    (ctx.work / "cs-lint-App.sarif").write_text("stale")
    assert view(cs_lint.run_gate(ctx)) == ("cs.lint", False, "3 problems", SUPPRESSED)
    assert [args[1] for args, _ in calls] == [str(tmp_path / "App" / "App.csproj"), str(tmp_path / "AppTests" / "AppTests.csproj")]
    assert calls[0][1] == {"timeout": 900}


def test_clean_build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, {"App": sarif(), "AppTests": sarif()})
    ctx = project(tmp_path, scope_changed=True, changed={"App/B.cs"})
    assert view(cs_lint.run_gate(ctx)) == ("cs.lint", True, "analyzers clean (AnalysisLevel 8.0, Recommended)", [])


def test_single_project_built_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = install(monkeypatch, {"App": sarif()})
    ctx = project(tmp_path)
    monkeypatch.setattr(dotnet, "PROJECTS", {str(tmp_path): (tmp_path / "App" / "App.csproj",) * 2})
    assert cs_lint.run_gate(ctx).summary == "3 problems"
    assert len(calls) == 1


def test_missing_sarif_means_no_compile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, {"App": sarif()}, (1, "error CS1002: ; expected\n\nBuild FAILED.\n"))
    result = cs_lint.run_gate(project(tmp_path))
    assert view(result) == (
        "cs.lint",
        False,
        "no SARIF written for AppTests/AppTests.csproj; the build did not compile",
        ["error CS1002: ; expected", "Build FAILED."],
    )


def test_missing_sarif_with_hint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, {}, (127, ""))
    assert cs_lint.run_gate(project(tmp_path)).summary == f"dotnet unavailable: {dotnet.INSTALL_HINT}"


def test_findings_sorted_unique_and_capped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    results = [diagnostic("App/B.cs", line) for line in range(1, 71)]
    install(monkeypatch, {"App": sarif(*results), "AppTests": sarif(*results)})
    result = cs_lint.run_gate(project(tmp_path))
    assert result.summary == "73 problems"
    assert len(result.findings) == 60
    assert result.findings[0] == "App/A.cs:3 analyzer suppressed in source; fix the code instead"
    assert result.findings[2] == "App/B.cs:1 CA1822: Member 'X' can be static"
    assert result.findings[3] == "App/B.cs:10 CA1822: Member 'X' can be static"


def test_build_args(tmp_path: Path) -> None:
    assert cs_lint.build_args(Path("/r/App.csproj"), Path("/w/a.sarif")) == [
        "build",
        "/r/App.csproj",
        "-t:Rebuild",
        "-nologo",
        "-v:q",
        "-p:EnableNETAnalyzers=true",
        "-p:AnalysisLevel=8.0",
        "-p:AnalysisMode=Recommended",
        "-p:EnforceCodeStyleInBuild=true",
        "-p:TreatWarningsAsErrors=false",
        "-p:ErrorLog=/w/a.sarif%2cversion=2.1",
        "-p:GenerateDocumentationFile=true",
        "-p:NoWarn=CS1591%3bCS1573%3bCS1587%3bCS1712",
    ]


def test_suppression_findings_scoped(tmp_path: Path) -> None:
    ctx = project(tmp_path, scope_changed=True, changed={"AppTests/T.cs"})
    assert cs_lint.suppression_findings(ctx) == [SUPPRESSED[2]]


def test_sarif_version_must_be_2_1(tmp_path: Path) -> None:
    ctx = project(tmp_path)
    log = ctx.work / "x.sarif"
    log.write_text(json.dumps({"version": "1.0.0", "runs": []}))
    message = ".marestail/x.sarif:1 SARIF version 1.0.0 is not 2.1; the ErrorLog comma must be escaped as %2c"
    assert cs_lint.sarif_findings(ctx, log, tmp_path / "App" / "App.csproj") == [message]
    log.write_text(json.dumps({"runs": []}))
    assert cs_lint.sarif_findings(ctx, log, tmp_path / "App" / "App.csproj") == [
        ".marestail/x.sarif:1 SARIF version None is not 2.1; the ErrorLog comma must be escaped as %2c"
    ]


def test_sarif_findings(tmp_path: Path) -> None:
    ctx = project(tmp_path, scope_changed=True, changed={"App/A.cs", "App/A b.cs"})
    log = ctx.work / "x.sarif"
    results = [
        diagnostic(f"file://{tmp_path}/App/A.cs", level="error"),
        diagnostic(None, ruleId=None),
        diagnostic("App/A.cs", None, level="note"),
        diagnostic("App/B.cs"),
        diagnostic("App/A%20b.cs", 3),
        diagnostic("App/obj/G.cs"),
        diagnostic(f"file://{tmp_path.parent}/Else.cs"),
        {"ruleId": "CS1", "level": "error", "message": {"text": "x"}, "locations": []},
    ]
    del results[1]["ruleId"]
    log.write_text(sarif(*results))
    assert cs_lint.sarif_findings(ctx, log, tmp_path / "App" / "App.csproj") == [
        "App/A.cs:7 CA1822: Member 'X' can be static",
        "App/App.csproj:1 ?: Member 'X' can be static",
        "App/A b.cs:3 CA1822: Member 'X' can be static",
        "App/App.csproj:1 CS1: x",
    ]


def test_sarif_without_results(tmp_path: Path) -> None:
    ctx = project(tmp_path)
    log = ctx.work / "x.sarif"
    log.write_text(json.dumps({"version": "2.1.0", "runs": [{}]}))
    assert cs_lint.sarif_findings(ctx, log, tmp_path / "App" / "App.csproj") == []


def test_long_messages_truncated(tmp_path: Path) -> None:
    ctx = project(tmp_path)
    result = {"message": {"text": "y" * 300}, "locations": [{"physicalLocation": {"artifactLocation": {"uri": "App/B.cs"}}}]}
    assert cs_lint.result_finding(ctx, result, tmp_path / "App" / "App.csproj") == ["App/B.cs:1 ?: " + "y" * 200]


def test_location_outside_dotnet_root(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"dotnet": {"root": "cs"}})
    assert cs_lint.location(ctx, diagnostic("../App/A.cs"), tmp_path / "cs" / "App.csproj") is None
    assert cs_lint.location(ctx, diagnostic("App/A.cs"), tmp_path / "cs" / "App.csproj") == "cs/App/A.cs:7"


@pytest.mark.parametrize(
    ("where", "expected"),
    [("App/A.cs:3", True), ("App/B.cs:3", False), ("App/App.csproj:1", True)],
)
def test_file_in_scope(tmp_path: Path, where: str, expected: bool) -> None:
    ctx = make_context(tmp_path, scope_changed=True, changed={"App/A.cs"})
    assert cs_lint.file_in_scope(where, ctx) is expected


def test_mapping_text_uses_a_blank_when_missing() -> None:
    assert cs_lint.mapping_text({}, "version") == ""
    assert cs_lint.mapping_text({"version": "2.1.0"}, "version") == "2.1.0"


def test_path_of_finding_uses_the_last_colon() -> None:
    assert cs_lint.path_of_finding("App/A:b.cs:3") == "App/A:b.cs"
    assert cs_lint.path_of_finding("no-colon") == "no-colon"
    assert cs_lint.COLON == ":"
    assert cs_lint.SARIF_VERSION == "2.1"


@pytest.mark.parametrize(
    ("where", "expected"),
    [("App/A:b.cs:3", True), ("App/B.cs:3", False)],
)
def test_file_in_scope_with_colon_in_the_path(tmp_path: Path, where: str, expected: bool) -> None:
    ctx = make_context(tmp_path, scope_changed=True, changed={"App/A:b.cs"})
    assert cs_lint.file_in_scope(where, ctx) is expected


def test_project_findings_returns_the_no_sarif_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, {"App": None}, (1, "broken"))
    ctx = project(tmp_path)
    started = 0.0
    result = cs_lint.project_findings(ctx, tmp_path / "App" / "App.csproj", [], started)
    assert isinstance(result, Result)
    assert result.ok is False
