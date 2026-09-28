import json
from pathlib import Path
from typing import Any

import pytest

from marestail import javascript
from marestail.gates import _hyper_crap, ts_crap
from tests.conftest import checked, make_context, untimed

TS = {"ts": {"root": "web"}}


def file_coverage(statements: dict[int, int], arms: dict[int, list[int]]) -> dict[str, Any]:
    return {
        "s": {str(line): hits for line, hits in statements.items()},
        "statementMap": {str(line): {"start": {"line": line}} for line in statements},
        "b": {str(line): hits for line, hits in arms.items()},
        "branchMap": {str(line): {"loc": {"start": {"line": line}}} for line in arms},
    }


def setup(root: Path, coverage: dict[str, Any]) -> None:
    folder = root / ".marestail" / "ts-coverage"
    folder.mkdir(parents=True)
    (folder / "coverage-final.json").write_text(json.dumps(coverage))


def function(file: str, name: str, line: int, end: int, complexity: int) -> dict[str, Any]:
    return {"file": file, "name": name, "line": line, "endLine": end, "complexity": complexity}


def test_missing_coverage_fails(tmp_path: Path) -> None:
    result = untimed(ts_crap.run_gate(make_context(tmp_path, TS)), ts_crap.GATE)

    assert (result.gate, result.ok, result.summary, result.findings, result.seconds) == (
        "ts.crap",
        False,
        "no coverage data; ts.tests must run first",
        [],
        0.0,
    )


def test_nothing_in_scope_is_skipped(tmp_path: Path, fake_run: Any) -> None:
    setup(tmp_path, {str(tmp_path / "web" / "a.ts"): file_coverage({}, {})})
    fake = fake_run(javascript)

    result = untimed(ts_crap.run_gate(make_context(tmp_path, TS, scope_changed=True, changed={"web/b.ts"})), ts_crap.GATE)

    assert (result.ok, result.summary) == (True, "skipped: no files in scope")
    assert fake.calls == []


def test_script_failure_shows_the_last_lines(tmp_path: Path, fake_run: Any) -> None:
    setup(tmp_path, {str(tmp_path / "web" / "a.ts"): file_coverage({}, {})})
    fake_run(javascript, [(1, "\n".join(str(n) for n in range(15)))])

    result = checked(ts_crap.run_gate(make_context(tmp_path, TS)), ts_crap.GATE)

    assert (result.ok, result.summary, result.findings) == (False, "complexity script failed", [str(n) for n in range(5, 15)])


def test_scores_functions_and_sorts_offenders(tmp_path: Path, fake_run: Any) -> None:
    source = str(tmp_path / "web" / "a.ts")
    setup(tmp_path, {source: file_coverage({1: 1, 2: 0, 10: 0, 20: 1}, {3: [1, 0], 11: [0, 0]})})
    functions = [
        function(source, "half", 1, 5, 5),
        function(source, "none", 10, 15, 3),
        function(source, "full", 20, 25, 4),
        function(source, "empty", 30, 35, 1),
    ]
    fake = fake_run(javascript, [(0, json.dumps(functions))])

    result = checked(ts_crap.run_gate(make_context(tmp_path, TS)), ts_crap.GATE)

    assert (result.ok, result.summary) == (False, "4 functions, 2 above CRAP 4")
    assert result.findings == ["web/a.ts:10 none crap=12.0 (cc=3, coverage=0%)", "web/a.ts:1 half crap=8.1 (cc=5, coverage=50%)"]
    assert fake.calls == [["node", str(javascript.COMPLEXITY), str(tmp_path / "web"), source]]
    assert fake.options == [{"cwd": tmp_path / "web"}]


def test_limit_is_configurable_and_scope_limits_functions(tmp_path: Path, fake_run: Any) -> None:
    source = str(tmp_path / "web" / "a.ts")
    setup(tmp_path, {source: file_coverage({1: 0, 10: 0}, {})})
    functions = [function(source, "touched", 1, 5, 2), function(source, "untouched", 10, 15, 9)]
    fake_run(javascript, [(0, json.dumps(functions))])
    ctx = make_context(
        tmp_path, {"ts": {"root": "web", "crap_max": 5.5}}, scope_changed=True, changed={"web/a.ts"}, changed_lines_map={"web/a.ts": {5}}
    )

    result = checked(ts_crap.run_gate(ctx), ts_crap.GATE)

    assert (result.ok, result.summary, result.findings) == (
        False,
        "1 functions, 1 above CRAP 5.5",
        ["web/a.ts:1 touched crap=6.0 (cc=2, coverage=0%)"],
    )


def test_passing_run(tmp_path: Path, fake_run: Any) -> None:
    source = str(tmp_path / "web" / "a.ts")
    setup(tmp_path, {source: file_coverage({1: 1}, {})})
    fake_run(javascript, [(0, json.dumps([function(source, "f", 1, 2, 4)]))])

    result = checked(ts_crap.run_gate(make_context(tmp_path, TS)), ts_crap.GATE)

    assert (result.ok, result.summary, result.findings) == (True, "1 functions, 0 above CRAP 4", [])


@pytest.mark.parametrize(("gated", "expected"), [(None, True), ({4}, False), ({5}, True), ({9}, True), ({10}, False)])
def test_touches_hunk(tmp_path: Path, gated: set[int] | None, expected: bool) -> None:
    lines = {} if gated is None else {"a.ts": gated}
    ctx = make_context(tmp_path, scope_changed=gated is not None, changed={"a.ts"}, changed_lines_map=lines)

    assert ts_crap.touches_hunk(function(str(tmp_path / "a.ts"), "f", 5, 9, 1), ctx) is expected


@pytest.mark.parametrize(("hits", "expected"), [([], 1.0), ([0, 3], 0.5), ([1, 1, 0, 0], 0.5), ([2], 1.0)])
def test_ratio(hits: list[int], expected: float) -> None:
    assert ts_crap.ratio(hits) == expected


def test_function_coverage_only_counts_code_inside_the_function() -> None:
    data = file_coverage({4: 0, 5: 1, 9: 1, 10: 0}, {5: [1, 1, 0], 11: [0]})

    assert ts_crap.function_coverage({"line": 5, "endLine": 9}, data) == 0.8


def hyper_ts(tmp_path: Path, fake_run: Any, monkeypatch: pytest.MonkeyPatch, base_reply: tuple[int, str], lines: set[int]) -> Any:
    source = str(tmp_path / "web" / "p.ts")
    setup(tmp_path, {source: file_coverage({2: 1, 10: 1, 11: 0, 12: 0}, {10: [1, 0]})})
    head = [function(source, "outer", 1, 30, 4), function(source, "inner", 9, 27, 9)]
    monkeypatch.setattr(_hyper_crap, "base_text", lambda root, ref, path: "base")
    fake = fake_run(javascript, [(0, json.dumps(head)), base_reply])
    ctx = make_context(tmp_path, TS, scope_changed=True, hyper=True, changed={"web/p.ts"}, changed_lines_map={"web/p.ts": lines})
    return checked(ts_crap.run_gate(ctx), ts_crap.GATE), fake


def test_hyper_passes_an_inner_function_no_worse_than_base(tmp_path: Path, fake_run: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    base = [function("x", "outer", 1, 30, 4), function("x", "inner", 9, 27, 9)]
    result, fake = hyper_ts(tmp_path, fake_run, monkeypatch, (0, json.dumps(base)), {10})
    assert (result.ok, result.summary, result.findings) == (
        True,
        "1 innermost changed functions, 1 above CRAP 4, 1 of them no worse than base",
        [],
    )
    assert fake.calls[1][-1].endswith("/web/p.ts")
    assert ".marestail" in fake.calls[1][-1]


def test_hyper_reports_statements_with_no_hits(tmp_path: Path, fake_run: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    base = [function("x", "outer", 1, 30, 4), function("x", "inner", 9, 27, 9)]
    result, _ = hyper_ts(tmp_path, fake_run, monkeypatch, (0, json.dumps(base)), {10, 12})
    assert result.findings == ["web/p.ts:9 inner crap=26.5 (cc=9, coverage=40%); changed lines not covered: 12"]


def test_hyper_scanner_failure_on_base_falls_back(tmp_path: Path, fake_run: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    result, _ = hyper_ts(tmp_path, fake_run, monkeypatch, (1, "boom"), {10})
    assert result.summary.endswith("; no base complexity for web/p.ts, crap_max only")
    assert result.ok is False


def test_missed_lines_are_statement_starts_with_no_hits() -> None:
    assert ts_crap.missed_lines(file_coverage({1: 0, 2: 3, 5: 0}, {})) == {1, 5}


def test_base_units_leave_the_file_to_the_caller(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    found = [{"file": "copy.ts", "line": 1, "endLine": 30, "name": "loadPaymentPopup", "complexity": 4}]
    monkeypatch.setattr(javascript, "scan", lambda ctx, mode, files: (0, json.dumps(found)))
    units = ts_crap.base_units(make_context(tmp_path, TS), tmp_path / "copy.ts")
    assert units == [{"file": "", "line": 1, "start": 1, "end": 30, "name": "loadPaymentPopup", "label": "loadPaymentPopup", "cc": 4}]
