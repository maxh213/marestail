import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from marestail import elixir
from marestail.context import Context
from marestail.gates import ex_tests
from marestail.report import Result
from tests.conftest import FakeRun, make_context

Reply = tuple[int, str]
COVERAGE = {
    "totals": {"percent_covered": 75.0},
    "files": {
        "lib/b.ex": {"missing_lines": [5, 6], "covered": 3, "total": 5},
        "lib/a.ex": {"missing_lines": [], "covered": 4, "total": 4},
    },
}
MIX_OUTPUT = "Running ExUnit\n......\nFinished in 0.1 seconds\n6 tests, 0 failures, 0 skipped\n"


def shape(result: Result) -> tuple[str, bool, str, list[str]]:
    return result.gate, result.ok, result.summary, result.findings


def project(root: Path, coverdata: bool = True, **fields: Any) -> Context:
    (root / ".marestail").mkdir()
    if coverdata:
        (root / "cover").mkdir()
        (root / "cover" / "default.coverdata").write_text("")
    return make_context(root, {"elixir": {}}, **fields)


def mix(test: Reply, extract: Reply = (0, ""), coverage: dict[str, Any] | None = COVERAGE) -> Callable[[list[str]], Reply]:
    def reply(command: list[str]) -> Reply:
        if command[0] == "mix":
            return test
        if coverage is not None:
            Path(command[-1]).write_text(json.dumps(coverage))
        return extract

    return reply


@pytest.mark.parametrize(
    ("replies", "coverdata", "summary", "findings"),
    [
        (mix((2, "1 test, 1 failure\n\n  test fails")), True, "tests failed", ["1 test, 1 failure", "  test fails"]),
        (mix((0, "ok")), False, "no coverdata exported", ["ok"]),
        (mix((0, "ok"), (1, "extract crashed")), True, "coverage extraction failed", ["extract crashed"]),
        (mix((0, "ok"), (0, "no file"), None), True, "coverage extraction failed", ["no file"]),
    ],
)
def test_failures(
    tmp_path: Path,
    fake_run: Callable[..., FakeRun],
    replies: Callable[[list[str]], Reply],
    coverdata: bool,
    summary: str,
    findings: list[str],
) -> None:
    fake_run(ex_tests, replies)
    assert shape(ex_tests.run_gate(project(tmp_path, coverdata))) == ("ex.tests", False, summary, findings)


def test_reports_gaps(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake = fake_run(ex_tests, mix((0, "Finished\n6 passed\n0 failed\n")))
    result = ex_tests.run_gate(project(tmp_path))
    assert shape(result) == (
        "ex.tests",
        False,
        "6 passed, coverage 75.0%, 2 gaps (need 0)",
        ["lib/b.ex:5 not covered", "lib/b.ex:6 not covered"],
    )
    coverdata = str(tmp_path / "cover" / "default.coverdata")
    out_json = str(tmp_path / ".marestail" / "ex-coverage.json")
    assert fake.calls == [
        ["mix", "test", "--cover", "--export-coverage", "default"],
        ["elixir", str(elixir.COVERAGE), coverdata, out_json],
    ]
    assert fake.options == [{"cwd": tmp_path, "timeout": 1800}, {"cwd": tmp_path, "timeout": 300}]


def test_scoped_percent_on_changed_lines(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake_run(ex_tests, mix((0, MIX_OUTPUT)))
    ctx = project(tmp_path, scope_changed=True, changed={"lib/b.ex"}, changed_lines_map={"lib/b.ex": {6}})
    result = ex_tests.run_gate(ctx)
    assert shape(result) == ("ex.tests", False, "? passed, coverage 60.0%, 1 gaps on changed lines (need 0)", ["lib/b.ex:6 not covered"])


def test_clean_run(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    coverage = {"totals": {"percent_covered": 100.0}, "files": {"lib/a.ex": {"covered": 4, "total": 4}}}
    fake_run(ex_tests, mix((0, "Finished\n4 passed\n"), coverage=coverage))
    assert shape(ex_tests.run_gate(project(tmp_path))) == ("ex.tests", True, "4 passed, coverage 100.0%, 0 gaps (need 0)", [])


def test_scoped_percent(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, scope_changed=True, changed={"lib/c.ex"})
    assert ex_tests.scoped_percent(COVERAGE, ctx) == 100.0
    empty: dict[str, Any] = {"files": {"lib/c.ex": {}}}
    assert ex_tests.scoped_percent(empty, ctx) == 100.0
    both = make_context(tmp_path, scope_changed=True, changed={"lib/a.ex", "lib/b.ex"})
    assert ex_tests.scoped_percent(COVERAGE, both) == pytest.approx(700 / 9)


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("10 tests: 2 passed and 8 failed", "2"),
        ("2 passed, 1 failed", "?"),
        ("passed\n3 passed", "3"),
        ("passed first", "?"),
        ("everything passed ok\n  12  passed  ", "everything"),
        ("", "?"),
        ("5 tests, 0 failures", "?"),
    ],
)
def test_count_tests(output: str, expected: str) -> None:
    assert ex_tests.count_tests(output) == expected
