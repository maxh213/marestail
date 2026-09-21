import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from marestail import erlang
from marestail.context import Context
from marestail.gates import _crap, er_crap
from marestail.report import Result
from tests.conftest import FakeRun, checked, gate_shape, make_context, untimed

HINT = "erlang unavailable: install Erlang/OTP 25+ (erl, erlc, escript), or docker with `docker pull erlang:27`"


def shape(result: Result) -> tuple[str, bool, str, list[str]]:
    return gate_shape(result)


def project(root: Path, coverage: dict[str, Any] | None = None, raw: dict[str, Any] | None = None, **fields: Any) -> Context:
    (root / "src").mkdir()
    (root / "src" / "a.erl").write_text("line\n" * 20)
    (root / "src" / "b.erl").write_text("line\n" * 5)
    (root / ".marestail").mkdir()
    files = {str(root / "src/a.erl"): {"percent_covered": 50.0}, str(root / "src/b.erl"): {}, str(root / "gone.erl"): {}}
    (root / ".marestail" / "er-coverage.json").write_text(json.dumps(coverage or {"files": files}))
    config = {"erlang": {"escript": "escript", **(raw or {})}}
    return make_context(root, config, **fields)


def functions(root: Path) -> str:
    return json.dumps(
        [
            {"file": str(root / "src/a.erl"), "line": 2, "name": "small/0", "complexity": 1},
            {"file": str(root / "src/a.erl"), "line": 10, "name": "big/2", "complexity": 5},
            {"file": str(root / "src/a.erl"), "line": 5, "name": "mid/1", "complexity": 4},
            {"file": str(root / "src/b.erl"), "line": 1, "name": "plain/0", "complexity": 6},
        ]
    )


def complexity(command: list[str]) -> tuple[int, str]:
    root = Path(command[2]).parent.parent
    return 0, json.dumps([fn for fn in json.loads(functions(root)) if fn["file"] in command[2:]])


def test_needs_coverage(tmp_path: Path) -> None:
    (tmp_path / ".marestail").mkdir()
    result = untimed(er_crap.run_gate(make_context(tmp_path)), er_crap.GATE)
    assert shape(result) == ("er.crap", False, "no coverage data; er.tests must run first", [])
    assert result.seconds == 0.0


def test_skips_without_files(tmp_path: Path) -> None:
    ctx = project(tmp_path, coverage={"files": {"/nowhere/x.erl": {}}})
    assert shape(untimed(er_crap.run_gate(ctx), er_crap.GATE)) == ("er.crap", True, "skipped: no files in scope", [])


def test_skips_empty_coverage(tmp_path: Path) -> None:
    ctx = project(tmp_path, coverage={"totals": {}})
    assert untimed(er_crap.run_gate(ctx), er_crap.GATE).summary == "skipped: no files in scope"


@pytest.mark.parametrize(
    ("code", "output", "summary", "findings"),
    [
        (127, "escript: not found (x)", HINT, [HINT]),
        (1, "\n".join(str(n) for n in range(12)), "complexity script failed", [str(n) for n in range(2, 12)]),
    ],
)
def test_script_failures(
    tmp_path: Path, fake_run: Callable[..., FakeRun], code: int, output: str, summary: str, findings: list[str]
) -> None:
    fake_run(erlang, [(code, output)])
    assert shape(checked(er_crap.run_gate(project(tmp_path)), er_crap.GATE)) == ("er.crap", False, summary, findings)


def test_scores_all_functions(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake = fake_run(erlang, [(0, functions(tmp_path))])
    result = checked(er_crap.run_gate(project(tmp_path)), er_crap.GATE)
    assert shape(result) == (
        "er.crap",
        False,
        "4 functions, 3 above CRAP 4",
        [
            "src/a.erl:10 big/2 crap=8.1 (cc=5, coverage=50%)",
            "src/a.erl:5 mid/1 crap=6.0 (cc=4, coverage=50%)",
            "src/b.erl:1 plain/0 crap=6.0 (cc=6, coverage=100%)",
        ],
    )
    assert fake.calls == [
        ["escript", str(erlang.SCRIPT_DIR / "complexity.escript"), str(tmp_path / "src/a.erl"), str(tmp_path / "src/b.erl")]
    ]
    assert fake.options[0]["timeout"] == 600


def test_configured_limit_passes(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake_run(erlang, [(0, functions(tmp_path))])
    result = checked(er_crap.run_gate(project(tmp_path, raw={"crap_max": 8.5})), er_crap.GATE)
    assert shape(result) == ("er.crap", True, "4 functions, 0 above CRAP 8.5", [])


def test_scoped_keeps_functions_touching_changes(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake = fake_run(erlang, complexity)
    ctx = project(tmp_path, scope_changed=True, changed={"src/a.erl"}, changed_lines_map={"src/a.erl": {7}})
    result = checked(er_crap.run_gate(ctx), er_crap.GATE)
    assert shape(result) == ("er.crap", False, "1 functions, 1 above CRAP 4", ["src/a.erl:5 mid/1 crap=6.0 (cc=4, coverage=50%)"])
    assert fake.calls[0][2:] == [str(tmp_path / "src/a.erl")]


def test_scoped_focus_keeps_whole_file(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake_run(erlang, complexity)
    ctx = project(tmp_path, focus={"src/b.erl"})
    assert checked(er_crap.run_gate(ctx), er_crap.GATE).findings == ["src/b.erl:1 plain/0 crap=6.0 (cc=6, coverage=100%)"]


def test_function_ends(tmp_path: Path) -> None:
    source = tmp_path / "a.erl"
    source.write_text("x\n" * 12)
    found = [{"file": str(source), "line": line} for line in (9, 1, 4)]
    assert er_crap.function_ends(found) == {(str(source), 1): 3, (str(source), 4): 8, (str(source), 9): 12}


def test_touches_hunk_unscoped(tmp_path: Path) -> None:
    fn = {"file": str(tmp_path / "a.erl"), "line": 3}
    assert er_crap.touches_hunk(fn, {}, make_context(tmp_path)) is True


@pytest.mark.parametrize(("lines", "expected"), [({3}, True), ({6}, True), ({2, 7}, False), (set(), False)])
def test_touches_hunk_bounds(tmp_path: Path, lines: set[int], expected: bool) -> None:
    fn: dict[str, Any] = {"file": str(tmp_path / "a.erl"), "line": 3}
    ctx = make_context(tmp_path, scope_changed=True, changed={"a.erl"}, changed_lines_map={"a.erl": lines})
    assert er_crap.touches_hunk(fn, {(fn["file"], 3): 6}, ctx) is expected


def test_score(tmp_path: Path) -> None:
    fn = {"file": str(tmp_path / "a.erl"), "line": 3, "name": "f/0", "complexity": 2}
    scored = _crap.file_percent_score(fn, {"percent_covered": 0.0}, make_context(tmp_path))
    assert scored == {"file": "a.erl", "line": 3, "name": "f/0", "complexity": 2, "cov": 0.0, "crap": 6.0}
    assert _crap.file_percent_score(fn, {}, make_context(tmp_path))["cov"] == 1.0


def test_crap_result_orders_by_score() -> None:
    scored = [
        {"file": "a", "line": 1, "name": "x", "complexity": 5, "cov": 1.0, "crap": 5.0},
        {"file": "b", "line": 2, "name": "y", "complexity": 9, "cov": 0.25, "crap": 9.5},
        {"file": "c", "line": 3, "name": "z", "complexity": 4, "cov": 1.0, "crap": 4.0},
    ]
    result = _crap.crap_result("g", scored, 4.0, 0.0)
    expected = ["b:2 y crap=9.5 (cc=9, coverage=25%)", "a:1 x crap=5.0 (cc=5, coverage=100%)"]
    assert shape(result) == ("g", False, "3 functions, 2 above CRAP 4", expected)


def test_paired_ends_rejects_mismatched_lengths() -> None:
    with pytest.raises(ValueError, match=r"^ends$"):
        er_crap.paired_ends("a.erl", [1], [2, 3])


def test_paired_ends_keeps_matching_bounds() -> None:
    assert er_crap.paired_ends("a.erl", [1, 4], [3, 9]) == {("a.erl", 1): 3, ("a.erl", 4): 9}
