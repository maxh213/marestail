import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from marestail import erlang
from marestail.context import Context
from marestail.gates import _coverage, er_tests
from marestail.report import Result
from tests.conftest import FakeRun, make_context

HINT = "erlang unavailable: install Erlang/OTP 25+ (erl, erlc, escript), or docker with `docker pull erlang:27`"
COVERAGE = {
    "totals": {"percent_covered": 87.5},
    "files": {"src/b.erl": {"missing_lines": [4, 9]}, "src/a.erl": {"missing_lines": [2]}},
}


def shape(result: Result) -> tuple[str, bool, str, list[str]]:
    return result.gate, result.ok, result.summary, result.findings


def project(root: Path, **fields: Any) -> Context:
    for name in ["src/a.erl", "src/b.erl", "test/a_tests.erl"]:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text("-module(x).\n" * 10)
    (root / ".marestail").mkdir()
    return make_context(root, {"erlang": {"erlc": "erlc", "escript": "escript"}}, **fields)


def eunit(code: int, output: str, coverage: dict[str, Any] | None = COVERAGE) -> Callable[[list[str]], tuple[int, str]]:
    def reply(command: list[str]) -> tuple[int, str]:
        if command[0] != "escript":
            return 0, ""
        if coverage is not None:
            Path(command[-1]).write_text(json.dumps(coverage))
        return code, output

    return reply


def test_skips_without_sources(tmp_path: Path) -> None:
    result = er_tests.run_gate(make_context(tmp_path))
    assert shape(result) == ("er.tests", True, "skipped: no erlang sources under [erlang] sources (default src/)", [])


def test_fails_without_tests(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.erl").write_text("")
    result = er_tests.run_gate(make_context(tmp_path))
    finding = "marestail.toml:1 no test files under [erlang] test_dirs (default test/, tests/) or *_tests.erl next to the sources"
    assert shape(result) == ("er.tests", False, "no eunit test files", [finding])
    assert result.seconds == 0.0


@pytest.mark.parametrize(
    ("failed", "summary", "findings"),
    [
        ((HINT, [HINT]), HINT, [HINT]),
        (("sources failed to compile", ["src/a.erl:1: syntax error"]), "sources failed to compile", ["src/a.erl:1: syntax error"]),
        (("tests failed to compile", ["test/a_tests.erl:2: bad"]), "tests failed to compile", ["test/a_tests.erl:2: bad"]),
    ],
)
def test_compile_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed: tuple[str, list[str]],
    summary: str,
    findings: list[str],
) -> None:
    monkeypatch.setattr(erlang, "compile_with_tests", lambda *args: failed)
    assert shape(er_tests.run_gate(project(tmp_path))) == ("er.tests", False, summary, findings)


@pytest.mark.parametrize(
    ("code", "output", "coverage", "summary", "findings"),
    [
        (127, "escript: not found (x)", None, HINT, [HINT]),
        (1, "Failed: 1.  Passed: 2.", None, "tests failed", ["Failed: 1.  Passed: 2."]),
        (3, "crash\n\n", None, "eunit run failed", ["crash"]),
        (0, "ok", None, "no coverage report written", ["ok"]),
    ],
)
def test_eunit_failures(
    tmp_path: Path,
    fake_run: Callable[..., FakeRun],
    code: int,
    output: str,
    coverage: dict[str, Any] | None,
    summary: str,
    findings: list[str],
) -> None:
    fake_run(erlang, eunit(code, output, coverage))
    assert shape(er_tests.run_gate(project(tmp_path))) == ("er.tests", False, summary, findings)


def test_stale_coverage_is_removed(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    ctx = project(tmp_path)
    (tmp_path / ".marestail" / "er-coverage.json").write_text("{}")
    fake_run(erlang, eunit(0, "", None))
    assert er_tests.run_gate(ctx).summary == "no coverage report written"


def test_reports_gaps(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake = fake_run(erlang, eunit(0, "  All 12 tests passed.\n"))
    result = er_tests.run_gate(project(tmp_path))
    summary = "12 passed, coverage 87.5%, 3 gaps (need 0)"
    assert shape(result) == ("er.tests", False, summary, ["src/a.erl:2 not covered", "src/b.erl:4 not covered", "src/b.erl:9 not covered"])
    work = tmp_path / ".marestail"
    assert fake.calls == [
        ["erlc", "+debug_info", "-o", str(work / "er-ebin"), str(tmp_path / "src/a.erl"), str(tmp_path / "src/b.erl")],
        [
            "erlc",
            "-DTEST",
            "+debug_info",
            "-pa",
            str(work / "er-ebin"),
            "-o",
            str(work / "er-test-ebin"),
            str(tmp_path / "test/a_tests.erl"),
        ],
        [
            "escript",
            str(erlang.SCRIPT_DIR / "eunit_cover.escript"),
            str(work / "er-ebin"),
            str(work / "er-test-ebin"),
            str(work / "er-coverage.json"),
        ],
    ]
    assert [options["timeout"] for options in fake.options] == [900, 900, 1800]


def test_passes_when_covered(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake_run(erlang, eunit(0, "  Test passed.\n", {"totals": {"percent_covered": 100}, "files": {"src/a.erl": {}}}))
    assert shape(er_tests.run_gate(project(tmp_path))) == ("er.tests", True, "1 passed, coverage 100.0%, 0 gaps (need 0)", [])


def test_scoped_to_changed_lines(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake_run(erlang, eunit(0, "no count"))
    ctx = project(tmp_path, scope_changed=True, changed={"src/b.erl"}, changed_lines_map={"src/b.erl": {9, 10}})
    result = er_tests.run_gate(ctx)
    assert shape(result) == ("er.tests", False, "? passed, coverage 87.5%, 1 gaps on changed files (need 0)", ["src/b.erl:9 not covered"])


def test_relative_path(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    assert _coverage.relative_path(str(tmp_path / "src" / "a.erl"), ctx) == "src/a.erl"
    assert _coverage.relative_path("/outside/a.erl", ctx) == "/outside/a.erl"


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("  There were no tests\n  All 3 tests passed.", "3"),
        ("Test passed.\n  All 3 tests passed.", "1"),
        ("  7 tests passed", "7"),
        ("", "?"),
        ("Failed: 2", "?"),
    ],
)
def test_count_tests(output: str, expected: str) -> None:
    assert er_tests.count_tests(output) == expected


def test_gated_missing() -> None:
    assert _coverage.gated_missing({"missing_lines": [1, 2, 3]}, None) == [1, 2, 3]
    assert _coverage.gated_missing({"missing_lines": [1, 2, 3]}, {2, 5}) == [2]
    assert _coverage.gated_missing({}, {2}) == []


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("x 12 tests passed", "12"),
        ("a1 tests passed, 3 tests passed", "1"),
        (chr(0x0663) + " tests passed", chr(0x0663)),
        (chr(0xB9) + " tests passed", ""),
        ("tests passed 4 tests passed", "4"),
    ],
)
def test_passed_count_digits(line: str, expected: str) -> None:
    assert er_tests.passed_count(line) == expected
