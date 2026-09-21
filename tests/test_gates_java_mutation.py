import itertools
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest

from marestail import java
from marestail.context import Context
from marestail.gates import java_mutation
from marestail.report import Result, result_seconds
from tests.conftest import make_context

APP = "src/main/java/app/App.java"
CORE = "src/main/java/app/core/Core.java"
POM = "<project><build><plugins><plugin><artifactId>pitest-maven</artifactId></plugin></plugins></build></project>"
INSTALL = "declare org.pitest:pitest-maven with the org.pitest:pitest-junit5-plugin dependency in pom.xml; copy templates/java-pitest.xml"
ALL = {"java": {"mutation_scope": "all"}}


@pytest.fixture(autouse=True)
def clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "time", itertools.count(1.0, 0.25).__next__)


def write(path: Path, text: str = "class X {}\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def project(root: Path) -> None:
    write(root / "pom.xml", POM)
    write(root / APP)
    write(root / CORE)
    write(root / "src/test/java/app/AppTest.java")
    write(root / "src/test/java/app/core/CoreTest.java")
    (root / ".marestail").mkdir()


def fields(result: Result) -> tuple[str, bool, str, list[str], float]:
    return result.gate, result.ok, result.summary, result.findings, result_seconds(result)


def mutation(status: str, cls: str = "app.App", line: str = "5", source: str = "App.java") -> str:
    return (
        f"<mutation detected='false' status='{status}' numberOfTestsRun='1'><sourceFile>{source}</sourceFile>"
        f"<mutatedClass>{cls}</mutatedClass><mutatedMethod>run</mutatedMethod><methodDescription>()V</methodDescription>"
        f"<lineNumber>{line}</lineNumber><mutator>org.pitest.mutationtest.engine.gregor.mutators.VoidMethodCallMutator</mutator>"
        f"<description>removed call to helper</description></mutation>"
    )


def fake_mvn(monkeypatch: pytest.MonkeyPatch, mutations: list[str] | None, reply: tuple[int, str] = (0, "")) -> list[Any]:
    calls: list[Any] = []

    def mvn(ctx: Context, args: list[str], timeout: int = 1800, pom: Path | None = None) -> tuple[int, str]:
        calls.append((args, timeout))
        out = Path(next(arg for arg in args if arg.startswith("-DreportsDirectory=")).split("=", 1)[1])
        if mutations is not None:
            write(
                out / "mutations.xml", f'<?xml version="1.0" encoding="UTF-8"?>\n<mutations partial="true">{"".join(mutations)}</mutations>'
            )
        return reply

    monkeypatch.setattr(java, "mvn", mvn)
    return calls


def test_needs_pom(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_mvn(monkeypatch, [])
    result = java_mutation.run_gate(make_context(tmp_path))
    assert fields(result) == ("java.mutation", False, java.require_pom(make_context(tmp_path)), [], 0.0)
    assert calls == []


def test_needs_pit_in_pom(tmp_path: Path) -> None:
    write(tmp_path / "svc" / "pom.xml", "<project/>")
    result = java_mutation.run_gate(make_context(tmp_path, {"java": {"root": "svc"}}))
    assert fields(result) == ("java.mutation", False, "PIT is not in the pom", [f"svc/pom.xml:1 {INSTALL}"], 0.0)


def test_rejects_bad_scope_setting(tmp_path: Path) -> None:
    project(tmp_path)
    result = java_mutation.run_gate(make_context(tmp_path, {"java": {"mutation_scope": "some"}}))
    assert fields(result) == ("java.mutation", False, '[java] mutation_scope must be "changed" or "all", got \'some\'', [], 0.25)


def test_skips_without_changed_sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project(tmp_path)
    calls = fake_mvn(monkeypatch, [])
    result = java_mutation.run_gate(make_context(tmp_path, scope_changed=True, changed={"README.md"}))
    assert fields(result) == ("java.mutation", True, "skipped: no changed Java sources", [], 0.0)
    assert calls == []


def test_skips_without_sources(tmp_path: Path) -> None:
    write(tmp_path / "pom.xml", POM)
    result = java_mutation.run_gate(make_context(tmp_path, ALL))
    assert fields(result) == ("java.mutation", True, "skipped: no Java sources", [], 0.0)


def test_skips_excluded_sources(tmp_path: Path) -> None:
    project(tmp_path)
    ctx = make_context(tmp_path, {"java": {"mutation_scope": "all", "mutation_exclude": ["src/main/java"]}})
    assert fields(java_mutation.run_gate(ctx)) == ("java.mutation", True, "skipped: no Java sources", [], 0.0)


def test_all_killed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project(tmp_path)
    stale = write(tmp_path / ".marestail" / "pit" / "old.xml")
    calls = fake_mvn(monkeypatch, [mutation("KILLED"), mutation("TIMED_OUT"), mutation("NON_VIABLE")])
    ctx = make_context(tmp_path, {"java": {"mutation_scope": "all", "mutation_timeout": "60", "mutation_threads": 8}})
    assert fields(java_mutation.run_gate(ctx)) == ("java.mutation", True, "all 2 mutants killed", [], 0.25)
    out = tmp_path / ".marestail" / "pit"
    assert calls == [
        (
            [
                "test-compile",
                "org.pitest:pitest-maven:mutationCoverage",
                "-DtargetClasses=app.App,app.App$*,app.core.Core,app.core.Core$*",
                "-DoutputFormats=XML",
                "-DtimestampedReports=false",
                f"-DreportsDirectory={out}",
                "-Dthreads=8",
                "-DtargetTests=app.*,app.core.*",
            ],
            60,
        )
    ]
    assert not stale.exists()


def test_reports_survivors_for_changed_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project(tmp_path)
    mutants = [
        mutation("KILLED"),
        mutation("SURVIVED", "app.App$Inner", "9"),
        mutation("NO_COVERAGE", "app.gone.Gone", "", "Gone.java"),
    ]
    calls = fake_mvn(monkeypatch, mutants)
    result = java_mutation.run_gate(make_context(tmp_path, scope_changed=True, changed={APP}))
    assert fields(result) == (
        "java.mutation",
        False,
        "2 of 3 mutants not killed",
        [f"{APP}:9 run: removed call to helper survived", "app.gone.Gone:0 run: removed call to helper no coverage"],
        0.25,
    )
    assert calls[0][0][2] == "-DtargetClasses=app.App,app.App$*"
    assert calls[0][1] == 7200


def test_appends_scope_note(git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project(git_repo)
    fake_mvn(monkeypatch, [mutation("KILLED")])
    result = java_mutation.run_gate(make_context(git_repo))
    assert result.summary == "all 1 mutants killed (no base origin/master; full run)"


@pytest.mark.parametrize(
    ("reply", "summary"),
    [
        ((127, "sh: mvn: not found"), f"maven unavailable: {java.INSTALL['maven']}"),
        ((1, "[ERROR] No plugin found for prefix\nPitest plugin missing"), INSTALL),
        ((1, "Could not run any tests"), INSTALL),
        ((0, "\nNo mutations found\n"), "no mutants were generated"),
        ((1, "BUILD FAILURE"), "PIT produced no report (exit 1)"),
    ],
)
def test_reports_missing_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reply: tuple[int, str], summary: str) -> None:
    project(tmp_path)
    fake_mvn(monkeypatch, None, reply)
    result = java_mutation.run_gate(make_context(tmp_path, ALL))
    assert fields(result) == ("java.mutation", False, summary, [line for line in reply[1].splitlines() if line.strip()], 0.25)


def test_reports_no_viable_mutants(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project(tmp_path)
    fake_mvn(monkeypatch, [mutation("NON_VIABLE")], (0, "done\n\n"))
    result = java_mutation.run_gate(make_context(tmp_path, ALL))
    assert fields(result) == ("java.mutation", False, "no mutants were generated", ["done"], 0.25)


@pytest.mark.parametrize(
    ("survived", "note", "expected"),
    [(0, "", "all 4 mutants killed"), (2, "", "2 of 4 mutants not killed"), (1, "(note)", "1 of 4 mutants not killed (note)")],
)
def test_summarise(survived: int, note: str, expected: str) -> None:
    assert java_mutation.summarise(survived, 4, note) == expected


def test_command_without_tests(tmp_path: Path) -> None:
    write(tmp_path / APP)
    ctx = make_context(tmp_path)
    args = java_mutation.command(ctx, [tmp_path / APP, tmp_path / "elsewhere" / "X.java"], Path("/out"))
    assert args == [
        "test-compile",
        "org.pitest:pitest-maven:mutationCoverage",
        "-DtargetClasses=app.App,app.App$*",
        "-DoutputFormats=XML",
        "-DtimestampedReports=false",
        "-DreportsDirectory=/out",
        "-Dthreads=2",
    ]


def test_command_with_default_package_test(tmp_path: Path) -> None:
    write(tmp_path / "src/test/java/TopTest.java")
    write(tmp_path / "src/test/java/b/BTest.java")
    args = java_mutation.command(make_context(tmp_path), [], Path("/out"))
    assert args[2] == "-DtargetClasses="
    assert args[-1] == "-DtargetTests=*,b.*"


@pytest.mark.parametrize(("name", "globs"), [("a.B", ["a.B", "a.B$*"]), (None, []), ("", [])])
def test_class_globs(name: str | None, globs: list[str]) -> None:
    assert java_mutation.class_globs(name) == globs


@pytest.mark.parametrize(("name", "glob"), [("a.b.CTest", "a.b.*"), ("CTest", "*"), (None, "*")])
def test_test_glob(name: str | None, glob: str) -> None:
    assert java_mutation.test_glob(name) == glob


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("The PITEST PLUGIN is absent", INSTALL),
        ("could not run any tests", INSTALL),
        ("NO MUTATIONS FOUND", "no mutants were generated"),
        ("", "PIT produced no report (exit 3)"),
    ],
)
def test_missing(output: str, expected: str) -> None:
    assert java_mutation.missing(output, 3) == expected


def element(xml: str) -> ET.Element:
    return ET.fromstring(xml)


def test_describe_edge_cases(tmp_path: Path) -> None:
    write(tmp_path / "src/test/java/app/Helper.java")
    ctx = make_context(tmp_path)
    bare = element("<mutation><mutatedMethod>m</mutatedMethod><description>d</description></mutation>")
    assert java_mutation.describe(ctx, bare) == ":0 m: d ?"
    in_tests = element(mutation("MEMORY_ERROR", "app.Helper", "4", "Helper.java"))
    assert java_mutation.describe(ctx, in_tests) == "app.Helper:4 run: removed call to helper memory error"


def test_viable(tmp_path: Path) -> None:
    report = write(tmp_path / "m.xml", f"<mutations>{mutation('NON_VIABLE')}{mutation('SURVIVED')}{mutation('KILLED')}</mutations>")
    assert [m.get("status") for m in java_mutation.viable(report)] == ["SURVIVED", "KILLED"]
