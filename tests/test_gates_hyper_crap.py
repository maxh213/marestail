from pathlib import Path
from typing import Any

import pytest

from marestail.gates import _hyper_crap
from marestail.gates._hyper_crap import Hyper
from tests.conftest import make_context

Fn = dict[str, Any]


def unit(name: str, span: tuple[int, int], cc: int, cov: float = 0.0, file: str = "a.py", **extra: Any) -> Fn:
    start, end = span
    fields = {"file": file, "line": start, "start": start, "end": end, "name": name, "label": name, "cc": cc, "cov": cov, "missing": set()}
    return {**fields, **extra}


def hyper_ctx(root: Path, lines: dict[str, set[int]], **raw: Any) -> Any:
    return make_context(root, raw, scope_changed=True, hyper=True, changed=set(lines), changed_lines_map=lines)


def bases(monkeypatch: pytest.MonkeyPatch, texts: dict[str, str | None]) -> list[tuple[Path, str, str]]:
    asked: list[tuple[Path, str, str]] = []

    def base_text(root: Path, base: str, path: str) -> str | None:
        asked.append((root, base, path))
        return texts.get(path)

    monkeypatch.setattr(_hyper_crap, "base_text", base_text)
    return asked


def scanner(found: dict[str, list[Fn] | None], seen: list[tuple[Path, str]] | None = None) -> Any:
    def scan(copy: Path) -> list[Fn] | None:
        (seen if seen is not None else []).append((copy, copy.read_text()))
        return found[copy.read_text()]

    return scan


def test_encloses_needs_same_file_and_a_strictly_larger_range() -> None:
    outer = unit("outer", (1, 10), 1)
    assert _hyper_crap.encloses(outer, unit("inner", (3, 5), 1))
    assert _hyper_crap.encloses(outer, unit("inner", (1, 5), 1))
    assert _hyper_crap.encloses(outer, unit("inner", (3, 10), 1))
    assert not _hyper_crap.encloses(outer, unit("twin", (1, 10), 1))
    assert not _hyper_crap.encloses(outer, unit("other", (3, 5), 1, file="b.py"))
    assert not _hyper_crap.encloses(outer, unit("after", (8, 12), 1))
    assert not _hyper_crap.encloses(unit("inner", (3, 5), 1), outer)


def test_innermost_drops_functions_around_a_touched_one(tmp_path: Path) -> None:
    outer, inner, other, quiet = (
        unit("outer", (1, 20), 4),
        unit("inner", (5, 12), 9),
        unit("other", (14, 16), 1),
        unit("quiet", (30, 40), 1),
    )
    ctx = hyper_ctx(tmp_path, {"a.py": {7, 15, 19}})
    assert _hyper_crap.innermost([outer, inner, other, quiet], ctx) == [inner, other]
    assert _hyper_crap.innermost([outer, quiet], ctx) == [outer]
    assert _hyper_crap.innermost([outer], hyper_ctx(tmp_path, {})) == []


def test_innermost_ignores_functions_in_other_files(tmp_path: Path) -> None:
    outer, inner = unit("outer", (1, 20), 4), unit("inner", (5, 12), 9, file="b.py")
    ctx = hyper_ctx(tmp_path, {"a.py": {7}, "b.py": {7}})
    assert _hyper_crap.innermost([outer, inner], ctx) == [outer, inner]


def test_changed_in_keeps_lines_inside_the_range(tmp_path: Path) -> None:
    ctx = hyper_ctx(tmp_path, {"a.py": {1, 3, 5, 6}})
    assert _hyper_crap.changed_in(unit("f", (3, 5), 1), ctx) == {3, 5}


def test_nesting_path_lists_enclosing_labels_outermost_first() -> None:
    top, middle, leaf = unit("top", (1, 30), 1), unit("middle", (2, 20), 1), unit("leaf", (5, 6), 1, label="K.leaf")
    functions = [leaf, middle, top, unit("elsewhere", (5, 6), 1, file="b.py")]
    assert _hyper_crap.nesting_path(leaf, functions) == "top.middle.K.leaf"
    assert _hyper_crap.nesting_path(top, functions) == "top"


def test_nesting_path_puts_the_wider_of_two_same_start_functions_first() -> None:
    top, middle, leaf = unit("top", (1, 30), 1), unit("middle", (1, 20), 1), unit("leaf", (5, 6), 1)
    assert _hyper_crap.nesting_path(leaf, [middle, top, leaf]) == "top.middle.leaf"


def test_complexity_by_path_keeps_the_highest_of_twins() -> None:
    functions = [unit("f", (1, 3), 4), unit("f", (5, 9), 7), unit("f", (11, 12), 2), unit("g", (20, 21), 1)]
    assert _hyper_crap.complexity_by_path(functions) == {"f": 7, "g": 1}


def test_finding_names_the_rule_that_failed() -> None:
    scored = {"file": "a.py", "line": 9, "name": "f", "cc": 9, "cov": 0.21, "crap": 48.6}
    described = "a.py:9 f crap=48.6 (cc=9, coverage=21%)"
    assert _hyper_crap.finding(scored, None, [4]) == described
    assert _hyper_crap.finding(scored, 8, [4]) == "a.py:9 f complexity rose from 8 to 9; move the new condition into its own function"
    assert _hyper_crap.finding(scored, 9, [4, 12]) == described + "; changed lines not covered: 4, 12"
    assert _hyper_crap.finding(scored, 10, []) == ""


def test_judged_applies_both_rules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    asked = bases(monkeypatch, {"a.py": "base a", "b.py": "base b", "c.py": "broken"})
    seen: list[tuple[Path, str]] = []
    base_a = [unit("outer", (1, 60), 4), unit("legacy", (2, 20), 9), unit("old", (22, 28), 6), unit("rising", (40, 50), 5)]
    scan = scanner({"base a": base_a, "base b": [unit("slow", (1, 5), 3)], "broken": None}, seen)
    functions = [
        unit("outer", (1, 60), 4, cov=0.5),
        unit("legacy", (2, 20), 9, cov=0.2, missing={3}),
        unit("old", (22, 28), 6, cov=0.0, missing={25}),
        unit("rising", (40, 50), 6, cov=0.0, missing={45}),
        unit("fresh", (52, 58), 1, cov=1.0),
        unit("slow", (1, 5), 3, cov=0.0, file="b.py"),
        unit("new", (1, 4), 2, cov=0.0, file="n.py"),
        unit("draft", (1, 3), 4, cov=0.0, file="c.py"),
    ]
    ctx = hyper_ctx(tmp_path, {"a.py": {5, 25, 45, 55}, "b.py": {2}, "n.py": {1}, "c.py": {1}}, git={"base": "main"})
    result = _hyper_crap.judged(ctx, Hyper("py.crap", 4.0, scan), functions, 0.0)
    assert result.findings == [
        "a.py:22 old crap=42.0 (cc=6, coverage=0%); changed lines not covered: 25",
        "a.py:40 rising complexity rose from 5 to 6; move the new condition into its own function",
        "c.py:1 draft crap=20.0 (cc=4, coverage=0%)",
        "n.py:1 new crap=6.0 (cc=2, coverage=0%)",
    ]
    summary = "7 innermost changed functions, 6 above CRAP 4, 2 of them no worse than base; no base complexity for c.py, crap_max only"
    assert (result.gate, result.ok, result.summary) == ("py.crap", False, summary)
    assert [path for _, _, path in asked] == ["a.py", "b.py", "c.py", "n.py"]
    assert {base for _, base, _ in asked} == {"main"}
    assert [(copy.relative_to(ctx.work).parts[1:], text) for copy, text in seen] == [
        (("a.py",), "base a"),
        (("b.py",), "base b"),
        (("c.py",), "broken"),
    ]
    assert not seen[0][0].exists()
    assert list(ctx.work.iterdir()) == []


def test_judged_passes_when_every_offender_is_no_worse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bases(monkeypatch, {"app/x.py": "base"})
    scan = scanner({"base": [unit("legacy", (1, 9), 9, file="")]})
    functions = [unit("legacy", (1, 9), 9, file="app/x.py", missing={8})]
    ctx = hyper_ctx(tmp_path, {"app/x.py": {3}})
    result = _hyper_crap.judged(ctx, Hyper("ts.crap", 4.0, scan), functions, 0.0)
    assert (result.ok, result.summary, result.findings) == (
        True,
        "1 innermost changed functions, 1 above CRAP 4, 1 of them no worse than base",
        [],
    )


def test_base_defaults_to_origin_master(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    asked = bases(monkeypatch, {})
    _hyper_crap.judged(hyper_ctx(tmp_path, {"a.py": {1}}), Hyper("py.crap", 4.0, scanner({})), [unit("f", (1, 2), 1)], 0.0)
    assert asked == [(tmp_path, "origin/master", "a.py")]


def test_crap_equal_to_the_limit_is_not_above_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bases(monkeypatch, {})
    ctx = hyper_ctx(tmp_path, {"a.py": {1}})
    result = _hyper_crap.judged(ctx, Hyper("py.crap", 6.0, scanner({})), [unit("f", (1, 2), 2)], 0.0)
    assert (result.ok, result.summary, result.findings) == (
        True,
        "1 innermost changed functions, 0 above CRAP 6, 0 of them no worse than base",
        [],
    )


def test_every_unreadable_base_file_gets_its_own_note(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bases(monkeypatch, {"a.py": "broken", "b.py": "broken"})
    ctx = hyper_ctx(tmp_path, {"a.py": {1}, "b.py": {1}})
    functions = [unit("f", (1, 2), 1, cov=1.0), unit("g", (1, 2), 1, cov=1.0, file="b.py")]
    result = _hyper_crap.judged(ctx, Hyper("py.crap", 4.0, scanner({"broken": None})), functions, 0.0)
    notes = "; no base complexity for a.py, crap_max only; no base complexity for b.py, crap_max only"
    assert result.summary == "2 innermost changed functions, 0 above CRAP 4, 0 of them no worse than base" + notes


def test_base_functions_nest_whatever_file_the_scanner_names(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bases(monkeypatch, {"a.py": "base"})
    scan = scanner({"base": [unit("outer", (1, 10), 1, file="copy"), unit("inner", (2, 5), 9, file="elsewhere")]})
    functions = [unit("outer", (1, 10), 1, cov=1.0), unit("inner", (2, 5), 9)]
    result = _hyper_crap.judged(hyper_ctx(tmp_path, {"a.py": {3}}), Hyper("py.crap", 4.0, scan), functions, 0.0)
    assert (result.ok, result.findings) == (True, [])


def test_covered_member_without_line_data_misses_nothing() -> None:
    member = {"file": "A.cs", "line": 3, "name": "M", "startLine": 3, "endLine": 5, "complexity": 2}
    expected = {"file": "A.cs", "line": 3, "start": 3, "end": 5, "name": "M", "label": "M", "cc": 2, "cov": 0.0, "missing": set()}
    assert _hyper_crap.covered_member(member, {"files": {}}, 0.0) == expected
    assert _hyper_crap.covered_member(member, {"files": {"A.cs": {}}}, 0.0) == expected
