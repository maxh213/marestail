import json
from pathlib import Path
from typing import Any

import pytest

from marestail import ruby
from marestail.gates import rb_crap
from tests.conftest import make_context

SOURCE = "class User\n  def a\n    1\n  end\n\n  def b\n    2\n  end\nend\n"


def write_tree(root: Path) -> None:
    for name in ("app/models/user.rb", "lib/tool.rb", "app/spec/x_spec.rb", "vendor/g.rb", "app/notes.txt"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(SOURCE)
    (root / ".marestail").mkdir()


def write_coverage(root: Path) -> None:
    coverage = {"files": {"app/models/user.rb": {"lines": [1, 1, 0, None, None, 0, 0, None, None]}}}
    (root / ".marestail" / "rb-coverage.json").write_text(json.dumps(coverage))


def methods(root: Path) -> str:
    user = str(root / "app" / "models" / "user.rb")
    return json.dumps(
        [
            {"file": user, "line": 2, "name": "User#a", "complexity": 3},
            {"file": user, "line": 6, "name": "User#b", "complexity": 3},
            {"file": str(root / "lib" / "tool.rb"), "line": 1, "name": "Tool#big", "complexity": 6},
        ]
    )


def test_needs_coverage(tmp_path: Path, fake_run: Any) -> None:
    fake = fake_run(ruby)
    result = rb_crap.run_gate(make_context(tmp_path))
    assert (result.gate, result.ok, result.summary, result.findings, result.seconds) == (
        "rb.crap",
        False,
        "no coverage data; rb.tests must run first",
        [],
        0.0,
    )
    assert fake.calls == []


def test_no_files_in_scope(tmp_path: Path, fake_run: Any) -> None:
    (tmp_path / ".marestail").mkdir()
    write_coverage(tmp_path)
    result = rb_crap.run_gate(make_context(tmp_path))
    assert (result.ok, result.summary) == (True, "skipped: no files in scope")


def test_scanner_failure(tmp_path: Path, fake_run: Any) -> None:
    write_tree(tmp_path)
    write_coverage(tmp_path)
    fake_run(ruby, [(1, "\n".join(f"err {n}" for n in range(15)))])
    result = rb_crap.run_gate(make_context(tmp_path, {"ruby": {"ruby": "rb"}}))
    assert (result.ok, result.summary, result.findings) == (False, "complexity scanner failed", [f"err {n}" for n in range(5, 15)])


def test_scores_methods(tmp_path: Path, fake_run: Any) -> None:
    write_tree(tmp_path)
    write_coverage(tmp_path)
    fake = fake_run(ruby, [(0, methods(tmp_path))])
    result = rb_crap.run_gate(make_context(tmp_path, {"ruby": {"ruby": "rb"}}))
    assert fake.calls == [["rb", str(ruby.SCRIPT), "complexity", str(tmp_path / "app/models/user.rb"), str(tmp_path / "lib/tool.rb")]]
    assert (result.ok, result.summary) == (False, "3 methods, 2 above CRAP 4")
    assert result.findings == [
        "app/models/user.rb:6 User#b crap=12.0 (cc=3, coverage=0%)",
        "lib/tool.rb:1 Tool#big crap=6.0 (cc=6, coverage=100%)",
    ]


def test_scoped_scores_touched_methods(tmp_path: Path, fake_run: Any) -> None:
    write_tree(tmp_path)
    write_coverage(tmp_path)
    fake_run(ruby, [(0, methods(tmp_path))])
    ctx = make_context(
        tmp_path,
        {"ruby": {"ruby": "rb", "crap_max": 2.5}},
        scope_changed=True,
        changed={"app/models/user.rb"},
        changed_lines_map={"app/models/user.rb": {3}},
    )
    result = rb_crap.run_gate(ctx)
    assert (result.ok, result.summary, result.findings) == (
        False,
        "1 methods, 1 above CRAP 2.5",
        ["app/models/user.rb:2 User#a crap=3.0 (cc=3, coverage=100%)"],
    )


def test_sources_in_scope(tmp_path: Path) -> None:
    write_tree(tmp_path)
    assert ruby.sources(make_context(tmp_path)) == [tmp_path / "app/models/user.rb", tmp_path / "lib/tool.rb"]
    ctx = make_context(tmp_path, focus={"lib"})
    assert rb_crap.sources_in_scope(ctx) == [tmp_path / "lib/tool.rb"]


def test_functions_in_scope(tmp_path: Path) -> None:
    write_tree(tmp_path)
    functions = json.loads(methods(tmp_path))
    assert rb_crap.functions_in_scope(functions, make_context(tmp_path)) == functions
    changed = make_context(tmp_path, scope_changed=True, changed={"lib/tool.rb"})
    assert rb_crap.functions_in_scope(functions, changed) == [functions[2]]
    gated = make_context(tmp_path, scope_changed=True, changed={"app/models/user.rb"}, changed_lines_map={"app/models/user.rb": {7}})
    assert rb_crap.functions_in_scope(functions, gated) == [functions[1]]


def test_file_text(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    (tmp_path / "a.rb").write_bytes(b"x\xff")
    assert rb_crap.file_text(ctx, "a.rb") == "x�"
    assert rb_crap.file_text(ctx, "missing.rb") == ""


@pytest.mark.parametrize(
    ("start", "end", "gated", "expected"),
    [(0, 5, set(), True), (5, 4, set(), True), (2, 4, {4}, True), (2, 4, {5, 1}, False)],
)
def test_body_touched(start: int, end: int, gated: set[int], expected: bool) -> None:
    assert rb_crap.body_touched(start, end, gated) is expected


def test_method_ranges() -> None:
    text = "def a\n  x.each do |i|\n    puts 'end'\n  end\nend\ndef b\n  if x\n"
    assert rb_crap.method_ranges(text, [6, 1, 0]) == {1: 5, 6: 7}
    assert rb_crap.method_ranges("def a\n  x\n", [1]) == {1: 2}


def test_method_end_clamps() -> None:
    assert rb_crap.method_end([1, 1], 1, 5) == 2
    assert rb_crap.method_end([1, 1, 1], 2, 1) == 2
    assert rb_crap.method_end([0, 1, -1], 2, 3) == 3


@pytest.mark.parametrize(
    ("line", "delta"),
    [
        ("  def x", 1),
        ("  items.map do |item|", 1),
        ("  loop do", 1),
        ("  end", -1),
        ("  end end", -2),
        ('  puts "end"', 0),
        ("  x # end", 0),
        ("=end", 0),
        ("  if a then b end", 0),
        ("  foo(bar)", 0),
    ],
)
def test_line_delta(line: str, delta: int) -> None:
    assert rb_crap.line_delta(line) == delta


@pytest.mark.parametrize(
    ("line", "file_cov", "covered"),
    [
        (2, {"lines": [None, 3]}, 1.0),
        (2, {"lines": [None, 0]}, 0.0),
        (1, {"lines": [None], "missing_lines": [1]}, 0.0),
        (5, {"lines": [1], "missing_lines": [4]}, 1.0),
        (0, {"lines": [1], "missing_lines": [0]}, 0.0),
        (3, {}, 1.0),
        (1, {"lines": [True]}, 1.0),
    ],
)
def test_line_covered(line: int, file_cov: dict[str, Any], covered: float) -> None:
    assert rb_crap.line_covered(line, file_cov) == covered


def test_score_and_describe(tmp_path: Path) -> None:
    fn = {"file": str(tmp_path / "a.rb"), "line": 1, "name": "A#x", "complexity": 2}
    scored = rb_crap.score(fn, {"lines": [0]}, make_context(tmp_path))
    assert scored == {"file": "a.rb", "line": 1, "name": "A#x", "cc": 2, "cov": 0.0, "crap": 6.0}
    assert rb_crap.describe(scored) == "a.rb:1 A#x crap=6.0 (cc=2, coverage=0%)"


def test_above_sorts_worst_first() -> None:
    scored = [{"crap": 5.0}, {"crap": 4.0}, {"crap": 9.0}]
    assert rb_crap.above(scored, 4.0) == [{"crap": 9.0}, {"crap": 5.0}]
