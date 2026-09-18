import itertools
import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

from marestail import java
from marestail.context import Context
from marestail.gates import java_lint
from marestail.report import Result
from tests.conftest import make_context

APP = "src/main/java/app/App.java"
TEST = "src/test/java/app/AppTest.java"
POM = (
    '<project xmlns="http://maven.apache.org/POM/4.0.0"><properties>'
    "<maven.compiler.release>17</maven.compiler.release></properties></project>"
)
SUPPRESSED = f"{APP}:2 warning suppressed in source; fix the code instead"
WARNING = {"file": APP, "line": 4, "kind": "WARNING", "code": "compiler.warn.possible.fall-through.into.case", "message": "fall"}
ERROR = {"file": TEST, "line": 9, "kind": "ERROR", "code": "compiler.err.cant.resolve", "message": "cannot find symbol"}


@pytest.fixture(autouse=True)
def clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "time", itertools.count(10.0, 0.5).__next__)


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def project(root: Path) -> Context:
    write(root / "pom.xml", POM)
    write(root / APP, 'package app;\n@SuppressWarnings("unchecked")\nclass App {\n}\n')
    write(root / TEST, "package app;\nclass AppTest {} // NOPMD\n")
    write(root / ".marestail" / "java-classpath.txt", " /m2/junit.jar \n")
    return make_context(root)


def fields(result: Result) -> tuple[str, bool, str, list[str], float]:
    return result.gate, result.ok, result.summary, result.findings, result.seconds


class Seams:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
        self.root = root
        self.scans: list[tuple[str, list[Path], list[str] | None]] = []
        self.diagnostics: tuple[Any, str | None] = ([], None)
        self.classpath: tuple[Path | None, str | None] = (root / ".marestail" / "java-classpath.txt", None)
        self.tools: tuple[str | None, str | None] = ("/m2/pmd.jar", None)
        monkeypatch.setattr(java, "classpath", lambda ctx: self.classpath)
        monkeypatch.setattr(java, "pmd_classpath", lambda ctx: self.tools)
        monkeypatch.setattr(java, "scan", self.scan)

    def scan(self, ctx: Context, mode: str, paths: list[Path], extra: list[str] | None = None) -> tuple[Any, str | None]:
        self.scans.append((mode, paths, extra))
        return self.diagnostics


@pytest.fixture
def seams(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Seams:
    return Seams(monkeypatch, tmp_path)


def pmd_report(report: dict[str, Any], code: int = 4, output: str = "") -> Any:
    def reply(command: list[str]) -> tuple[int, str]:
        write(Path(command[command.index("-r") + 1]), json.dumps(report))
        return code, output

    return reply


def violation(line: int, rule: str, description: str) -> dict[str, Any]:
    return {"beginline": line, "begincolumn": 1, "endline": line, "endcolumn": 9, "description": description, "rule": rule, "priority": 3}


def test_skips_when_no_java_changed(tmp_path: Path, seams: Seams, fake_run: Any) -> None:
    project(tmp_path)
    fake = fake_run(java_lint)
    result = java_lint.run_gate(make_context(tmp_path, scope_changed=True, changed={"README.md", "pom.xml"}))
    assert fields(result) == ("java.lint", True, "skipped: no changed Java files", [], 0.0)
    assert (fake.calls, seams.scans) == ([], [])


def test_needs_pom(tmp_path: Path, seams: Seams) -> None:
    result = java_lint.run_gate(make_context(tmp_path))
    assert fields(result) == ("java.lint", False, java.require_pom(make_context(tmp_path)), [], 0.0)


def test_skips_without_sources(tmp_path: Path, seams: Seams) -> None:
    write(tmp_path / "pom.xml", POM)
    assert fields(java_lint.run_gate(make_context(tmp_path))) == ("java.lint", True, "skipped: no Java sources", [], 0.0)


def test_reports_classpath_error(tmp_path: Path, seams: Seams) -> None:
    ctx = project(tmp_path)
    seams.classpath = (None, "maven unavailable")
    assert fields(java_lint.run_gate(ctx)) == ("java.lint", False, "maven unavailable", [], 0.5)
    assert seams.scans == []


def test_reports_scan_error(tmp_path: Path, seams: Seams) -> None:
    ctx = project(tmp_path)
    seams.diagnostics = (None, "java scanner failed (lint): boom")
    stale = write(tmp_path / ".marestail" / "java-lint-classes" / "Old.class", "x")
    assert fields(java_lint.run_gate(ctx)) == ("java.lint", False, "java scanner failed (lint): boom", [], 0.5)
    classes = tmp_path / ".marestail" / "java-lint-classes"
    cp = tmp_path / ".marestail" / "java-classpath.txt"
    assert seams.scans == [
        ("lint", [tmp_path / APP, tmp_path / TEST], ["--classpath", str(cp), "--classes", str(classes), "--release", "17"])
    ]
    assert not stale.exists()


def test_stops_when_code_does_not_compile(tmp_path: Path, seams: Seams, fake_run: Any) -> None:
    ctx = project(tmp_path)
    seams.diagnostics = ([ERROR, WARNING, WARNING], None)
    fake = fake_run(java_lint)
    assert fields(java_lint.run_gate(ctx)) == (
        "java.lint",
        False,
        "does not compile",
        [
            SUPPRESSED,
            f"{APP}:4 javac warn.possible.fall-through.into.case: fall",
            f"{TEST}:2 warning suppressed in source; fix the code instead",
            f"{TEST}:9 javac err.cant.resolve: cannot find symbol",
        ],
        0.5,
    )
    assert fake.calls == []


def test_reports_pmd_error_with_unsorted_findings(tmp_path: Path, seams: Seams, fake_run: Any) -> None:
    ctx = project(tmp_path)
    seams.diagnostics = ([WARNING], None)
    seams.tools = (None, "maven could not fetch PMD: offline")
    assert fields(java_lint.run_gate(ctx)) == (
        "java.lint",
        False,
        "maven could not fetch PMD: offline",
        [
            SUPPRESSED,
            f"{TEST}:2 warning suppressed in source; fix the code instead",
            f"{APP}:4 javac warn.possible.fall-through.into.case: fall",
        ],
        0.5,
    )


def test_clean_run(tmp_path: Path, seams: Seams, fake_run: Any) -> None:
    write(tmp_path / "pom.xml", "<project/>")
    write(tmp_path / APP, "package app;\nclass App {}\n")
    write(tmp_path / ".marestail" / "java-classpath.txt", "/m2/a.jar")
    fake = fake_run(java_lint, pmd_report({"files": []}, code=0))
    result = java_lint.run_gate(make_context(tmp_path))
    assert fields(result) == ("java.lint", True, "javac -Xlint:all and PMD clean", [], 0.5)
    assert seams.scans[0][2] == [
        "--classpath",
        str(tmp_path / ".marestail" / "java-classpath.txt"),
        "--classes",
        str(tmp_path / ".marestail" / "java-lint-classes"),
    ]
    assert "--use-version" not in fake.calls[0]


def test_merges_javac_and_pmd(tmp_path: Path, seams: Seams, fake_run: Any) -> None:
    ctx = project(tmp_path)
    seams.diagnostics = ([WARNING, WARNING], None)
    report = {
        "files": [{"filename": str(tmp_path / APP), "violations": [violation(3, "UnusedPrivateField", "Avoid unused\n  field 'x'.")]}]
    }
    fake_run(java_lint, pmd_report(report))
    assert fields(java_lint.run_gate(ctx)) == (
        "java.lint",
        False,
        "4 problems",
        [
            SUPPRESSED,
            f"{APP}:3 PMD UnusedPrivateField: Avoid unused field 'x'.",
            f"{APP}:4 javac warn.possible.fall-through.into.case: fall",
            f"{TEST}:2 warning suppressed in source; fix the code instead",
        ],
        0.5,
    )


def test_findings_are_capped(tmp_path: Path, seams: Seams, fake_run: Any) -> None:
    ctx = project(tmp_path)
    violations = [violation(n, "R", "d") for n in range(100, 170)]
    fake_run(java_lint, pmd_report({"files": [{"filename": str(tmp_path / APP), "violations": violations}]}))
    result = java_lint.run_gate(ctx)
    assert (result.summary, len(result.findings), result.findings[0]) == ("72 problems", 60, f"{APP}:100 PMD R: d")


def test_untouched(tmp_path: Path) -> None:
    write(tmp_path / APP, "class App {}\n")
    assert not java_lint.untouched(make_context(tmp_path))
    assert java_lint.untouched(make_context(tmp_path, scope_changed=True, changed={"a.py"}))
    assert not java_lint.untouched(make_context(tmp_path, scope_changed=True, changed={APP}))
    assert not java_lint.untouched(make_context(tmp_path, focus={APP}))


def test_failure_caps_findings() -> None:
    result = java_lint.failure("broken", [str(n) for n in range(70)], 9.0)
    assert (result.ok, result.summary, len(result.findings), result.findings[-1], result.seconds) == (False, "broken", 60, "59", 1.0)


@pytest.mark.parametrize(("release", "tail"), [("17", ["--release", "17"]), (None, [])])
def test_scan_args(release: str | None, tail: list[str]) -> None:
    assert java_lint.scan_args(Path("/cp.txt"), Path("/classes"), release) == ["--classpath", "/cp.txt", "--classes", "/classes", *tail]


@pytest.mark.parametrize(("kinds", "expected"), [(["WARNING", "NOTE"], False), (["WARNING", "ERROR"], True), ([], False)])
def test_compile_failed(kinds: list[str], expected: bool) -> None:
    assert java_lint.compile_failed([{"kind": kind} for kind in kinds]) is expected


def test_suppression_findings(tmp_path: Path) -> None:
    files = [
        write(tmp_path / "A.java", '@SuppressWarnings("x")\nint a; //NOPMD\n@SuppressWarningsX\n'),
        write(tmp_path / "B.java", "@SuppressFBWarnings\n// nopmd\n//   NOPMD reason\n"),
    ]
    message = "warning suppressed in source; fix the code instead"
    assert java_lint.suppression_findings(make_context(tmp_path), files) == [
        f"A.java:1 {message}",
        f"A.java:2 {message}",
        f"B.java:1 {message}",
        f"B.java:3 {message}",
    ]
    assert java_lint.suppression_findings(make_context(tmp_path, scope_changed=True, changed={"B.java"}), files) == [
        f"B.java:1 {message}",
        f"B.java:3 {message}",
    ]


def test_javac_findings(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"java": {"root": "svc"}}, scope_changed=True, changed={APP})
    diagnostics: list[dict[str, Any]] = [
        {"file": None, "line": 0, "kind": "WARNING", "code": "compiler.warn.source.no.bootclasspath", "message": "boot"},
        {"file": "", "line": 1, "kind": "MANDATORY_WARNING", "code": None, "message": "x" * 250},
        {"file": TEST, "line": 2, "kind": "ERROR", "code": "compiler.err.x", "message": "skip"},
        {"file": APP, "line": 3, "kind": "NOTE", "code": "note.compiler.x", "message": "kept"},
    ]
    assert java_lint.javac_findings(ctx, diagnostics) == [
        "svc/pom.xml:0 javac warn.source.no.bootclasspath: boot",
        f"svc/pom.xml:1 javac MANDATORY_WARNING: {'x' * 200}",
        f"{APP}:3 javac note.compiler.x: kept",
    ]


@pytest.mark.parametrize(
    ("text", "expected"),
    [("  a \n b\tc  ", "a b c"), ("", ""), ("y" * 300, "y" * 200)],
)
def test_squash(text: str, expected: str) -> None:
    assert java_lint.squash(text) == expected


def run_pmd(tmp_path: Path, ctx: Context, release: str | None) -> tuple[list[str], str | None]:
    classes = tmp_path / "classes"
    return java_lint.pmd_findings(ctx, [tmp_path / APP, tmp_path / TEST], tmp_path / ".marestail" / "java-classpath.txt", classes, release)


def test_pmd_findings_command(tmp_path: Path, seams: Seams, fake_run: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JAVA_HOME", raising=False)
    ctx = project(tmp_path)
    work = tmp_path / ".marestail"
    write(work / "java-pmd.json", '{"files": [{"filename": "stale"}]}')
    fake = fake_run(java_lint, pmd_report({}, code=5))
    assert run_pmd(tmp_path, ctx, "21") == ([], None)
    assert fake.calls == [
        [
            "java",
            "-cp",
            "/m2/pmd.jar",
            "net.sourceforge.pmd.cli.PmdCli",
            "check",
            "--no-cache",
            "--no-progress",
            "--no-fail-on-violation",
            "-R",
            str(java.PMD_RULESET),
            "-f",
            "json",
            "-r",
            str(work / "java-pmd.json"),
            "--aux-classpath",
            f"{tmp_path / 'classes'}{os.pathsep}/m2/junit.jar",
            "--file-list",
            str(work / "java-pmd-files.txt"),
            "--use-version",
            "java-21",
        ]
    ]
    assert fake.options == [{"cwd": tmp_path, "timeout": 1800}]
    assert (work / "java-pmd-files.txt").read_text() == f"{tmp_path / APP}\n{tmp_path / TEST}\n"


def test_pmd_findings_tool_error(tmp_path: Path, seams: Seams, fake_run: Any) -> None:
    ctx = project(tmp_path)
    seams.tools = (None, "maven unavailable")
    fake = fake_run(java_lint)
    assert run_pmd(tmp_path, ctx, None) == ([], "maven unavailable")
    assert fake.calls == []


@pytest.mark.parametrize(
    ("code", "writes", "expected"),
    [(1, True, "PMD failed (exit 1): trace"), (0, False, "PMD failed (exit 0): trace"), (4, False, "PMD failed (exit 4): trace")],
)
def test_pmd_findings_failures(tmp_path: Path, seams: Seams, fake_run: Any, code: int, writes: bool, expected: str) -> None:
    ctx = project(tmp_path)
    write(tmp_path / ".marestail" / "java-pmd.json", "{}")
    reply = pmd_report({}, code, " trace \n") if writes else (lambda command: (code, " trace \n"))
    fake_run(java_lint, reply)
    assert run_pmd(tmp_path, ctx, None) == ([], expected)


def test_read_pmd(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"java": {"root": "svc"}}, scope_changed=True, changed={APP})
    data = {
        "files": [
            {"filename": str(tmp_path / APP), "violations": [violation(5, "A", "one"), violation(6, "B", "two")]},
            {"filename": str(tmp_path / TEST), "violations": [violation(1, "C", "hidden")]},
            {"filename": APP},
        ],
        "processingErrors": [{"filename": str(tmp_path / TEST), "message": "Parse\n  error"}, {"message": "m" * 300}],
        "configurationErrors": [{"rule": "X", "message": "bad"}, {"filename": ""}],
    }
    assert java_lint.read_pmd(ctx, data) == [
        f"{APP}:5 PMD A: one",
        f"{APP}:6 PMD B: two",
        f"{TEST}:1 PMD could not analyse: Parse error",
        f"svc/pom.xml:1 PMD could not analyse: {'m' * 200}",
        "svc/pom.xml:1 PMD could not analyse: bad",
        "svc/pom.xml:1 PMD could not analyse: ",
    ]
    assert java_lint.read_pmd(ctx, {}) == []


def test_pmd_command_configured_ruleset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JAVA_HOME", "/jdk")
    ctx = make_context(tmp_path, {"java": {"root": "svc", "pmd_ruleset": "rules.xml"}})
    command = java_lint.pmd_command(ctx, "/t.jar", Path("/l.txt"), Path("/r.json"), "/aux", None)
    assert command[0] == "/jdk/bin/java"
    assert command[9] == str(tmp_path / "svc" / "rules.xml")
    assert command[-2:] == ["--file-list", "/l.txt"]
