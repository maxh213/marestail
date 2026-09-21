import json
from pathlib import Path
from typing import Any

import pytest

from marestail import dotnet
from marestail.gates import cs_mutation
from marestail.report import Result
from tests.conftest import gate_shape, make_context

NO_BASE = "(no base origin/master; full run)"


def view(result: Result) -> tuple[str, bool, str, list[str]]:
    return gate_shape(result)


def project(root: Path, csproj: str = "<Project />", section: dict[str, Any] | None = None, **fields: Any) -> Any:
    files = {"App/App.csproj": csproj, "AppTests/AppTests.csproj": "", "App/A.cs": "", "App/B.cs": "", "AppTests/T.cs": ""}
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    ctx = make_context(root, {"dotnet": section or {}}, **fields)
    ctx.work.mkdir()
    return ctx


def mutant(status: str, line: int = 5, **fields: Any) -> dict[str, Any]:
    return {"status": status, "mutatorName": "Equality", "replacement": "a  !=\n b", "location": {"start": {"line": line}}, **fields}


@pytest.fixture(autouse=True)
def fresh_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dotnet, "PROJECTS", {})


class FakeDotnet:
    def __init__(self, restore: tuple[int, str], report: dict[str, Any] | None, reply: tuple[int, str] = (0, "")) -> None:
        self.restore = restore
        self.report = report
        self.reply = reply
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(self, ctx: Any, args: list[str], **options: Any) -> tuple[int, str]:
        if ctx is None:
            raise TypeError("ctx")
        self.calls.append((args, options))
        if args[0] == "tool":
            return self.restore
        if self.report is not None:
            path = ctx.work / cs_mutation.OUTPUT_DIR / cs_mutation.REPORT
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(self.report))
        return self.reply


def install(monkeypatch: pytest.MonkeyPatch, fake: FakeDotnet) -> FakeDotnet:
    monkeypatch.setattr(dotnet, "dotnet", fake)
    return fake


def report(root: Path, *mutants: dict[str, Any], name: str = "App/A.cs") -> dict[str, Any]:
    return {"files": {str(root / name): {"mutants": list(mutants)}}}


def test_reports_missing_projects(tmp_path: Path) -> None:
    result = cs_mutation.run_gate(make_context(tmp_path))
    assert result.ok is False
    assert result.summary.startswith("set [dotnet] project and test_project")
    assert result.findings == []


def test_needs_separate_test_project(tmp_path: Path) -> None:
    ctx = project(tmp_path, section={"test_project": "App/App.csproj"})
    assert view(cs_mutation.run_gate(ctx)) == (
        "cs.mutation",
        False,
        "stryker needs the tests in their own .csproj",
        ["App/App.csproj:1 holds both product and test code"],
    )


def test_sentry_needs_switch(tmp_path: Path) -> None:
    ctx = project(tmp_path, '<packagereference  include="Sentry.AspNetCore" />')
    assert view(cs_mutation.run_gate(ctx)) == (
        "cs.mutation",
        False,
        "stryker cannot roll back mutants in Sentry's generated code",
        [f"App/App.csproj:1 add {cs_mutation.SENTRY_SWITCH} to a <PropertyGroup>"],
    )


def test_sentry_with_switch_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = install(monkeypatch, FakeDotnet((0, ""), report(tmp_path, mutant("Killed"))))
    ctx = project(tmp_path, f'<PackageReference Include="Sentry" />{cs_mutation.SENTRY_SWITCH}')
    assert view(cs_mutation.run_gate(ctx)) == ("cs.mutation", True, f"all 1 mutants killed {NO_BASE}", [])
    assert fake.calls[0] == (["tool", "restore"], {"timeout": 900})
    assert fake.calls[1][1] == {"timeout": 7200}


def test_bad_mutation_scope(tmp_path: Path) -> None:
    ctx = project(tmp_path, section={"mutation_scope": "some"})
    assert view(cs_mutation.run_gate(ctx)) == ("cs.mutation", False, '[dotnet] mutation_scope must be "changed" or "all", got \'some\'', [])


def test_skips_without_changed_sources(tmp_path: Path) -> None:
    ctx = project(tmp_path, scope_changed=True, changed={"AppTests/T.cs"})
    assert view(cs_mutation.run_gate(ctx)) == ("cs.mutation", True, "skipped: no changed C# sources", [])


def test_restore_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, FakeDotnet((1, "nope\n"), None))
    ctx = project(tmp_path, section={"mutation_scope": "all"})
    stale = ctx.work / "stryker" / "old.txt"
    stale.parent.mkdir()
    stale.write_text("")
    result = cs_mutation.run_gate(ctx)
    assert view(result) == ("cs.mutation", False, f"dotnet tool restore failed: {cs_mutation.INSTALL}", ["nope"])
    assert not stale.exists()


def test_restore_failure_with_hint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, FakeDotnet((127, ""), None))
    result = cs_mutation.run_gate(project(tmp_path, section={"mutation_scope": "all"}))
    assert result.summary == f"dotnet unavailable: {dotnet.INSTALL_HINT}"


@pytest.mark.parametrize(
    ("reply", "summary"),
    [
        ((1, "Cannot find a tool in the manifest file"), cs_mutation.INSTALL),
        ((1, "dotnet-stryker was not found"), cs_mutation.INSTALL),
        ((3, "crash"), "stryker produced no report (exit 3)"),
        ((127, ""), f"dotnet unavailable: {dotnet.INSTALL_HINT}"),
    ],
)
def test_missing_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reply: tuple[int, str], summary: str) -> None:
    install(monkeypatch, FakeDotnet((0, ""), None, reply))
    result = cs_mutation.run_gate(project(tmp_path, section={"mutation_scope": "all"}))
    assert (result.ok, result.summary, result.findings) == (False, summary, [line for line in [reply[1]] if line])


def test_no_mutants(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, FakeDotnet((0, ""), {"files": {}}, (0, "done")))
    result = cs_mutation.run_gate(project(tmp_path, section={"mutation_scope": "all"}))
    assert view(result) == ("cs.mutation", False, "no mutants were generated", ["done"])


def test_reports_surviving_mutants(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mutants = [mutant("Killed"), mutant("Survived", 7), mutant("NoCoverage", 9), mutant("Ignored"), mutant("Timeout", 11)]
    data = report(tmp_path, *mutants)
    data["files"][str(tmp_path / "App" / "Gen" / "X.cs")] = {"mutants": [mutant("Survived")]}
    data["files"][str(tmp_path / "App" / "B.cs")] = {}
    fake = install(monkeypatch, FakeDotnet((0, ""), data))
    ctx = project(tmp_path, section={"mutation_scope": "all", "coverage_exclude": ["App/Gen"]})
    assert view(cs_mutation.run_gate(ctx)) == (
        "cs.mutation",
        False,
        "3 of 5 mutants not killed",
        [
            "App/A.cs:7 Equality Survived: a != b",
            "App/A.cs:9 Equality NoCoverage: a != b",
            "App/A.cs:11 Equality Timeout: a != b",
        ],
    )
    assert fake.calls[1][0][-2:] == ["-m", "!**/App/Gen"]
    assert fake.calls[1][0][3:7] == ["--test-project", str(tmp_path / "AppTests" / "AppTests.csproj"), "--project", "App.csproj"]


def test_scoped_run_targets_changed_sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = install(monkeypatch, FakeDotnet((0, ""), report(tmp_path, mutant("Killed"))))
    ctx = project(tmp_path, scope_changed=True, changed={"App/B.cs", "AppTests/T.cs"})
    assert view(cs_mutation.run_gate(ctx)) == ("cs.mutation", True, "all 1 mutants killed", [])
    assert fake.calls[1][0][-2:] == ["-m", "**/App/B.cs"]


def test_mutation_targets(tmp_path: Path) -> None:
    ctx = project(tmp_path)
    assert cs_mutation.mutation_targets(ctx, None) == []
    assert cs_mutation.mutation_targets(ctx, ["AppTests/T.cs", "App/B.cs", "App/A.cs", "Gone.cs"]) == ["App/A.cs", "App/B.cs"]


def test_command(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"dotnet": {"root": "cs", "mutation_exclude": ["/cs/Gen/", "Migr"]}})
    product, tests, out = tmp_path / "cs" / "App.csproj", tmp_path / "cs" / "T" / "T.csproj", tmp_path / "out"
    assert cs_mutation.command(ctx, product, tests, out, ["cs/A.cs", "other/B.cs"]) == [
        "stryker",
        "--skip-version-check",
        "--break-on-initial-test-failure",
        "--test-project",
        str(tests),
        "--project",
        "App.csproj",
        "-O",
        str(out),
        "-r",
        "json",
        "-r",
        "progress",
        "-m",
        "!**/Gen",
        "-m",
        "!**/Migr",
        "-m",
        "**/A.cs",
        "-m",
        "**/other/B.cs",
    ]


def test_summary() -> None:
    assert cs_mutation.summary(0, 4, "") == "all 4 mutants killed"
    assert cs_mutation.summary(2, 4, "(note)") == "2 of 4 mutants not killed (note)"


def test_describe_defaults() -> None:
    assert cs_mutation.describe("A.cs", {"status": "Survived"}) == "A.cs:0 mutant Survived: "


def test_trim_slash_pattern_only_strips_slashes() -> None:
    assert cs_mutation.trim_slash_pattern("/XGen/", "cs/") == "XGen"
    assert cs_mutation.trim_slash_pattern("cs/Gen/", "cs/") == "Gen"


def test_precondition_reads_invalid_utf8(tmp_path: Path) -> None:
    ctx = project(tmp_path)
    product = tmp_path / "App" / "App.csproj"
    product.write_bytes(b"<Project />\xff")
    tests = tmp_path / "AppTests" / "AppTests.csproj"
    assert cs_mutation.precondition(ctx, product, tests, 0.0) is None
    long = {"status": "Timeout", "mutatorName": "String", "replacement": "x" * 80, "location": {"start": {"line": 3}}}
    assert cs_mutation.describe("A.cs", long) == "A.cs:3 String Timeout: " + "x" * 60


def test_mapping_uses_an_empty_dict_when_the_key_is_missing() -> None:
    assert cs_mutation.mapping({}, cs_mutation.FILES) == {}
    assert cs_mutation.mapping({"files": {"a": {}}}, cs_mutation.FILES) == {"a": {}}
    assert cs_mutation.mapping({"files": []}, cs_mutation.FILES) == {}


def test_mutants_of_missing_and_non_list() -> None:
    assert cs_mutation.mutants_of({}) == []
    assert cs_mutation.mutants_of({"mutants": [{"status": "Killed"}]}) == [{"status": "Killed"}]
    assert cs_mutation.mutants_of({"mutants": {}}) == []
    assert cs_mutation.listed_mutants({}) == []
    assert cs_mutation.listed_mutants({"mutants": [1]}) == [1]


def test_load_mutants_without_files_key(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    report.write_text("{}")
    ctx = project(tmp_path)
    assert cs_mutation.load_mutants(ctx, report) == []


def test_command_rejects_a_missing_product(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    with pytest.raises(TypeError, match=r"^path$"):
        cs_mutation.command(ctx, None, tmp_path / "T.csproj", tmp_path / "out", [])  # type: ignore[arg-type]


def test_command_rejects_a_missing_tests_project(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    with pytest.raises(TypeError, match=r"^path$"):
        cs_mutation.command(ctx, tmp_path / "A.csproj", None, tmp_path / "out", [])  # type: ignore[arg-type]


def test_command_rejects_a_missing_output_dir(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    with pytest.raises(TypeError, match=r"^path$"):
        cs_mutation.command(ctx, tmp_path / "A.csproj", tmp_path / "T.csproj", None, [])  # type: ignore[arg-type]


def test_required_path_rejects_none() -> None:
    with pytest.raises(TypeError, match=r"^path$"):
        cs_mutation.required_path(None)  # type: ignore[arg-type]
