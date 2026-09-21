import json
from pathlib import Path
from typing import Any

import pytest

from marestail.gates import _coverage, rb_tests
from tests.conftest import Clock, gate_shape, make_context

RSPEC_OK = "Randomized with seed 1\n\nFinished in 0.4 seconds\n12 examples, 0 failures\n"
THEN_ARM = "[:then, 1, 4, 6, 4, 20]"
ELSE_ARM = "[:else, 2, 7, 6, 8, 20]"
CONDITION = "[:if, 0, 3, 4, 8, 7]"


def user_data() -> dict[str, Any]:
    return {
        "lines": [1, 1, 0, 0, None, 2, 0, 1],
        "branches": {CONDITION: {THEN_ARM: 0, ELSE_ARM: 0}, "[:case, 3, 9, 4, 9, 7]": {"[:when, 4, 9, 4, 9, 7]": 5}},
    }


def write_resultset(root: Path) -> None:
    coverage = {
        str(root / "app" / "models" / "user.rb"): user_data(),
        str(root / "lib" / "legacy.rb"): [1, 0, None],
        "/gems/other.rb": {"lines": [1]},
    }
    path = root / "coverage" / ".resultset.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"RSpec": {"coverage": coverage, "timestamp": 1}, "Minitest": {"timestamp": 2}}))
    (root / ".marestail").mkdir()


def run(ctx: Any, fake_run: Any, reply: tuple[int, str]) -> Any:
    fake = fake_run(rb_tests, [reply])
    result = rb_tests.run_gate(ctx)
    assert fake.calls == [["bundle", "exec", "rspec"]]
    assert fake.options == [{"cwd": ctx.ruby_root(), "timeout": 1800}]
    gate_shape(result)
    return result


def test_failing_tests(tmp_path: Path, fake_run: Any) -> None:
    result = run(make_context(tmp_path), fake_run, (1, "\n".join(f"line {n}" for n in range(40))))
    assert (result.gate, result.ok, result.summary) == ("rb.tests", False, "tests failed")
    assert result.findings == [f"line {n}" for n in range(10, 40)]


def test_failing_tests_measure_elapsed(tmp_path: Path, fake_run: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("marestail.gates.rb_tests.time.time", Clock())
    result = run(make_context(tmp_path), fake_run, (1, "failed"))
    assert result.seconds == 0.25


def test_missing_resultset(tmp_path: Path, fake_run: Any) -> None:
    result = run(make_context(tmp_path), fake_run, (0, RSPEC_OK))
    assert (result.ok, result.summary) == (False, "no coverage/.resultset.json; SimpleCov must run with rspec")
    assert result.findings == ["Randomized with seed 1", "Finished in 0.4 seconds", "12 examples, 0 failures"]


def test_unscoped_run(tmp_path: Path, fake_run: Any) -> None:
    write_resultset(tmp_path)
    result = run(make_context(tmp_path), fake_run, (0, RSPEC_OK))
    assert result.summary == "12 passed, coverage 60.0%, 6 gaps (need 0)"
    assert result.findings == [
        "app/models/user.rb:3 not covered",
        "app/models/user.rb:4 not covered",
        "app/models/user.rb:7 not covered",
        f"app/models/user.rb branch {THEN_ARM} not taken",
        f"app/models/user.rb branch {ELSE_ARM} not taken",
        "lib/legacy.rb:2 not covered",
    ]
    saved = json.loads((tmp_path / ".marestail" / "rb-coverage.json").read_text())
    assert saved["totals"] == {"percent_covered": 60.0}
    assert saved["files"]["app/models/user.rb"] == {
        "missing_lines": [3, 4, 7],
        "missing_branches": [THEN_ARM, ELSE_ARM],
        "lines": user_data()["lines"],
    }
    assert saved["files"]["/gems/other.rb"] == {"missing_lines": [], "missing_branches": [], "lines": [1]}


def scoped_context(root: Path) -> Any:
    return make_context(root, scope_changed=True, changed={"app/models/user.rb"}, changed_lines_map={"app/models/user.rb": {4, 6}})


def test_scoped_run(tmp_path: Path, fake_run: Any) -> None:
    write_resultset(tmp_path)
    result = run(scoped_context(tmp_path), fake_run, (0, RSPEC_OK))
    assert result.summary == "12 passed, coverage 50.0%, 2 gaps scoped (changed (1 files, 2 lines)) (need 0)"
    assert result.findings == ["app/models/user.rb:4 not covered", f"app/models/user.rb branch {THEN_ARM} not taken"]
    saved = json.loads((tmp_path / ".marestail" / "rb-coverage.json").read_text())
    assert saved["totals"] == {"percent_covered": 60.0, "scoped_percent_covered": 50.0}
    assert saved["files"]["app/models/user.rb"]["branch_lines"] == {THEN_ARM: [3, 4], ELSE_ARM: [3, 7, 8]}


def test_passing_run(tmp_path: Path, fake_run: Any) -> None:
    path = tmp_path / "coverage" / ".resultset.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"RSpec": {"coverage": {str(tmp_path / "a.rb"): {"lines": [1, None]}}}}))
    (tmp_path / ".marestail").mkdir()
    result = run(make_context(tmp_path), fake_run, (0, "3 examples, 0 failures"))
    assert (result.ok, result.summary, result.findings) == (True, "3 passed, coverage 100.0%, 0 gaps (need 0)", [])


def test_load_resultset_handles_empty_suite(tmp_path: Path) -> None:
    path = tmp_path / "r.json"
    path.write_text(json.dumps({"RSpec": {"coverage": None}, "Other": {"coverage": {str(tmp_path / "x.rb"): {"lines": None}}}}))
    loaded = rb_tests.load_resultset(path, make_context(tmp_path))
    assert loaded == {"files": {"x.rb": {"missing_lines": [], "missing_branches": [], "lines": []}}, "totals": {"percent_covered": 100.0}}


def test_missing_branches_ignores_odd_shapes(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    assert rb_tests.missing_branches([1, 0], ctx) == ([], {})
    assert rb_tests.missing_branches({"branches": None}, ctx) == ([], {})
    assert rb_tests.missing_branches({"branches": {"c": [0], "d": {"arm": 0, "hit": 1}}}, ctx) == (["arm"], {})


@pytest.mark.parametrize(
    ("key", "lines", "start"),
    [("[:then, 1, 9, 2, 5, 3]", {5, 6, 7, 8, 9}, {9}), ("[ :else , 3 , 2 , 4 , 2 , 1 ]", {2}, {2}), ("nonsense", set(), set())],
)
def test_branch_spans(key: str, lines: set[int], start: set[int]) -> None:
    assert rb_tests.branch_lines(key) == lines
    assert rb_tests.start_line(key) == start


@pytest.mark.parametrize(
    ("lines", "gated", "expected"),
    [([], {1}, True), ([2, 3], {3}, True), ([2, 3], {4}, False), ([2], None, True)],
)
def test_arm_gated(lines: list[int], gated: set[int] | None, expected: bool) -> None:
    assert rb_tests.arm_gated(lines, gated) is expected


def test_percent_covered_scoped(tmp_path: Path) -> None:
    coverage = {"files": {"a.rb": {"lines": [1, 0, True, "x"]}, "b.rb": {"lines": [0]}, "c.rb": {}}, "totals": {"percent_covered": 1.0}}
    assert rb_tests.percent_covered(coverage, make_context(tmp_path)) == 1.0
    ctx = make_context(tmp_path, focus={"a.rb", "c.rb"})
    (tmp_path / "a.rb").write_text("a\nb\nc\nd\n")
    assert rb_tests.percent_covered(coverage, ctx) == 50.0
    assert rb_tests.percent_covered(coverage, make_context(tmp_path, focus={"c.rb"})) == 100.0


def test_coverage_findings_skips_out_of_scope(tmp_path: Path) -> None:
    coverage = {"files": {"a.rb": {"missing_lines": [1]}, "b.rb": {"missing_lines": [2], "missing_branches": ["arm"]}}}
    ctx = make_context(tmp_path, scope_changed=True, changed={"b.rb"}, changed_lines_map={"b.rb": {5}})
    assert rb_tests.coverage_findings(coverage, ctx) == ["b.rb branch arm not taken"]
    assert rb_tests.coverage_findings(coverage, make_context(tmp_path)) == [
        "a.rb:1 not covered",
        "b.rb:2 not covered",
        "b.rb branch arm not taken",
    ]


def test_relative_path(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    assert _coverage.relative_path(str(tmp_path / "app" / "a.rb"), ctx) == "app/a.rb"
    assert _coverage.relative_path("/outside/a.rb", ctx) == "/outside/a.rb"


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("5 examples, 1 failure\nmore", "5"),
        ("Finished\n  7 examples, 0 failures, 2 pending  \n", "7"),
        ("1 example, 0 failures", "1"),
        ("examples only\nno failure words", "?"),
        ("", "?"),
    ],
)
def test_count_examples(output: str, expected: str) -> None:
    assert rb_tests.count_examples(output) == expected


def test_list_field_defaults_missing_keys() -> None:
    assert rb_tests.list_field({}, "missing_lines") == []
    assert rb_tests.list_field({"missing_lines": [1]}, "missing_lines") == [1]


def test_list_field_rejects_a_non_list() -> None:
    with pytest.raises(TypeError, match=r"^list$"):
        rb_tests.list_field({"missing_lines": {}}, "missing_lines")


def test_span_lines_defaults_missing_arms() -> None:
    assert rb_tests.span_lines({}, "arm") == []
    assert rb_tests.span_lines({"arm": [1, 2]}, "arm") == [1, 2]


def test_span_lines_rejects_a_non_list() -> None:
    with pytest.raises(TypeError, match=r"^list$"):
        rb_tests.span_lines({"arm": "x"}, "arm")


def test_mapping_field_defaults_and_rejects_a_non_map() -> None:
    assert rb_tests.mapping_field({}, "branch_lines") == {}
    assert rb_tests.mapping_field({"branch_lines": {"a": [1]}}, "branch_lines") == {"a": [1]}


def test_mapping_field_rejects_a_non_map() -> None:
    with pytest.raises(TypeError, match=r"^map$"):
        rb_tests.mapping_field({"branch_lines": []}, "branch_lines")
