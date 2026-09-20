import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from marestail import elixir
from marestail.context import Context
from marestail.gates import ex_crap
from marestail.report import Result
from tests.conftest import FakeRun, gate_shape, make_context

FUNCTIONS = [
    {"file": "lib/a.ex", "line": 2, "end_line": 4, "name": "small/0", "complexity": 1},
    {"file": "lib/a.ex", "line": 6, "end_line": 20, "name": "big/2", "complexity": 5},
    {"file": "lib/b.ex", "line": 1, "name": "plain/0", "complexity": 6},
]


def shape(result: Result) -> tuple[str, bool, str, list[str]]:
    return gate_shape(result)


def project(root: Path, coverage: dict[str, Any] | None = None, raw: dict[str, Any] | None = None, **fields: Any) -> Context:
    (root / "app" / "lib").mkdir(parents=True)
    (root / "app" / "lib" / "a.ex").write_text("")
    (root / "b.ex").write_text("")
    (root / ".marestail").mkdir()
    files = {"lib/a.ex": {"percent_covered": 50.0}, str(root / "b.ex"): {}, "lib/gone.ex": {}}
    (root / ".marestail" / "ex-coverage.json").write_text(json.dumps(coverage or {"files": files}))
    return make_context(root, {"elixir": {"root": "app", **(raw or {})}}, **fields)


def test_needs_coverage(tmp_path: Path) -> None:
    (tmp_path / ".marestail").mkdir()
    result = ex_crap.run_gate(make_context(tmp_path))
    assert shape(result) == ("ex.crap", False, "no coverage data; ex.tests must run first", [])
    assert result.seconds == 0.0


@pytest.mark.parametrize("coverage", [{"files": {"lib/none.ex": {}}}, {"totals": {}}])
def test_skips_without_files(tmp_path: Path, coverage: dict[str, Any]) -> None:
    assert shape(ex_crap.run_gate(project(tmp_path, coverage))) == ("ex.crap", True, "skipped: no files in scope", [])


def test_script_failure(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake_run(elixir, [(1, "\n".join(str(n) for n in range(12)))])
    result = ex_crap.run_gate(project(tmp_path))
    assert shape(result) == ("ex.crap", False, "complexity script failed", [str(n) for n in range(2, 12)])


def test_scores_functions(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake = fake_run(elixir, [(0, json.dumps(FUNCTIONS))])
    result = ex_crap.run_gate(project(tmp_path))
    findings = ["lib/a.ex:6 big/2 crap=8.1 (cc=5, coverage=50%)", "lib/b.ex:1 plain/0 crap=6.0 (cc=6, coverage=100%)"]
    assert shape(result) == ("ex.crap", False, "3 functions, 2 above CRAP 4", findings)
    assert fake.calls == [["elixir", str(elixir.COMPLEXITY), "lib/a.ex", str(tmp_path / "b.ex")]]
    assert fake.options == [{"cwd": tmp_path / "app", "timeout": 600}]


def test_configured_limit(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake_run(elixir, [(0, json.dumps(FUNCTIONS))])
    assert ex_crap.run_gate(project(tmp_path, raw={"crap_max": 10})).summary == "3 functions, 0 above CRAP 10"


def test_scoped(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake = fake_run(elixir, [(0, json.dumps(FUNCTIONS[:2]))])
    ctx = project(tmp_path, scope_changed=True, changed={"lib/a.ex"}, changed_lines_map={"lib/a.ex": {3}})
    assert shape(ex_crap.run_gate(ctx)) == ("ex.crap", True, "1 functions, 0 above CRAP 4", [])
    assert fake.calls[0][2:] == ["lib/a.ex"]


@pytest.mark.parametrize(
    ("fn", "gated", "expected"),
    [
        ({"line": 5}, None, True),
        ({"line": 5, "end_line": 9}, {9}, True),
        ({"line": 5, "end_line": 9}, {4, 10}, False),
        ({"line": 5}, {5}, True),
        ({"line": 5}, {6}, False),
        ({}, {0}, True),
        ({"line": None, "end_line": 2}, {1}, True),
        ({}, {1}, False),
    ],
)
def test_in_hunks(fn: dict[str, Any], gated: set[int] | None, expected: bool) -> None:
    assert ex_crap.in_hunks(fn, gated) is expected
