import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from marestail import elixir
from marestail.context import Context
from marestail.gates import _hyper_crap, ex_crap
from marestail.report import Result
from tests.conftest import FakeRun, checked, gate_shape, make_context, untimed

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
    result = untimed(ex_crap.run_gate(make_context(tmp_path)), ex_crap.GATE)
    assert shape(result) == ("ex.crap", False, "no coverage data; ex.tests must run first", [])
    assert result.seconds == 0.0


@pytest.mark.parametrize("coverage", [{"files": {"lib/none.ex": {}}}, {"totals": {}}])
def test_skips_without_files(tmp_path: Path, coverage: dict[str, Any]) -> None:
    assert shape(untimed(ex_crap.run_gate(project(tmp_path, coverage)), ex_crap.GATE)) == (
        "ex.crap",
        True,
        "skipped: no files in scope",
        [],
    )


def test_script_failure(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake_run(elixir, [(1, "\n".join(str(n) for n in range(12)))])
    result = checked(ex_crap.run_gate(project(tmp_path)), ex_crap.GATE)
    assert shape(result) == ("ex.crap", False, "complexity script failed", [str(n) for n in range(2, 12)])


def test_scores_functions(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake = fake_run(elixir, [(0, json.dumps(FUNCTIONS))])
    result = checked(ex_crap.run_gate(project(tmp_path)), ex_crap.GATE)
    findings = ["lib/a.ex:6 big/2 crap=8.1 (cc=5, coverage=50%)", "lib/b.ex:1 plain/0 crap=6.0 (cc=6, coverage=100%)"]
    assert shape(result) == ("ex.crap", False, "3 functions, 2 above CRAP 4", findings)
    assert fake.calls == [["elixir", str(elixir.COMPLEXITY), "lib/a.ex", str(tmp_path / "b.ex")]]
    assert fake.options == [{"cwd": tmp_path / "app", "timeout": 600}]


def test_configured_limit(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake_run(elixir, [(0, json.dumps(FUNCTIONS))])
    assert checked(ex_crap.run_gate(project(tmp_path, raw={"crap_max": 10})), ex_crap.GATE).summary == "3 functions, 0 above CRAP 10"


def test_scoped(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake = fake_run(elixir, [(0, json.dumps(FUNCTIONS[:2]))])
    ctx = project(tmp_path, scope_changed=True, changed={"lib/a.ex"}, changed_lines_map={"lib/a.ex": {3}})
    assert shape(checked(ex_crap.run_gate(ctx), ex_crap.GATE)) == ("ex.crap", True, "1 functions, 0 above CRAP 4", [])
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


def hyper_ex(tmp_path: Path, fake_run: Callable[..., FakeRun], monkeypatch: pytest.MonkeyPatch, base_reply: tuple[int, str]) -> Result:
    source = str(tmp_path / "app" / "lib" / "a.ex")
    coverage = {"files": {source: {"percent_covered": 50.0, "missing_lines": [3, 12]}}}
    ctx = project(tmp_path, coverage, scope_changed=True, hyper=True, changed={"app/lib/a.ex"}, changed_lines_map={"app/lib/a.ex": {3}})
    monkeypatch.setattr(_hyper_crap, "base_text", lambda root, ref, path: "defmodule A do\nend\n")
    head = [{"file": source, "line": 2, "end_line": 8, "name": "big/2", "complexity": 5}]
    run = fake_run(elixir, [(0, json.dumps(head)), base_reply])
    result = checked(ex_crap.run_gate(ctx), ex_crap.GATE)
    assert run.calls[1][:2] == ["elixir", str(elixir.script("complexity"))]
    assert Path(run.calls[1][2]).relative_to(ctx.work).parts[1:] == ("app", "lib", "a.ex")
    assert run.options[1]["timeout"] == 600
    return result


def test_hyper_names_uncovered_changed_lines(tmp_path: Path, fake_run: Callable[..., FakeRun], monkeypatch: pytest.MonkeyPatch) -> None:
    base = [{"file": "copy", "line": 2, "end_line": 8, "name": "big/2", "complexity": 5}]
    result = hyper_ex(tmp_path, fake_run, monkeypatch, (0, json.dumps(base)))
    assert shape(result) == (
        "ex.crap",
        False,
        "1 innermost changed functions, 1 above CRAP 4, 0 of them no worse than base",
        ["app/lib/a.ex:2 big/2 crap=8.1 (cc=5, coverage=50%); changed lines not covered: 3"],
    )


def test_hyper_base_script_failure_falls_back(tmp_path: Path, fake_run: Callable[..., FakeRun], monkeypatch: pytest.MonkeyPatch) -> None:
    result = hyper_ex(tmp_path, fake_run, monkeypatch, (1, "boom"))
    assert result.summary.endswith("; no base complexity for app/lib/a.ex, crap_max only")


def test_base_units_leave_the_file_to_the_caller(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake_run(elixir, [(0, json.dumps([{"file": "copy.ex", "line": 2, "end_line": 5, "name": "f/0", "complexity": 3}]))])
    units = ex_crap.base_units(project(tmp_path), tmp_path / "copy.ex")
    assert units == [{"file": "", "line": 2, "start": 2, "end": 5, "name": "f/0", "label": "f/0", "cc": 3}]


def test_hyper_units_of_a_file_without_coverage_miss_no_lines(tmp_path: Path) -> None:
    found = [{"file": "lib/a.ex", "line": 2, "end_line": 4, "name": "small/0", "complexity": 1}]
    expected = {
        "file": "lib/a.ex",
        "line": 2,
        "start": 2,
        "end": 4,
        "name": "small/0",
        "label": "small/0",
        "cc": 1,
        "cov": 1.0,
        "missing": set(),
    }
    assert ex_crap.hyper_units(project(tmp_path), {"files": {}}, found) == [expected]
