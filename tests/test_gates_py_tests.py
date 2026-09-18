import json
from pathlib import Path
from typing import Any

import pytest

from marestail.gates import py_tests
from tests.conftest import make_context

COVERAGE = {
    "totals": {"percent_covered": 87.25},
    "files": {
        "b.py": {"missing_lines": [3, 9], "missing_branches": [[4, 6], [10, 12]]},
        "a.py": {"missing_lines": [1], "missing_branches": []},
    },
}


def write_coverage(root: Path, coverage: dict[str, Any]) -> None:
    (root / ".marestail").mkdir(exist_ok=True)
    (root / ".marestail" / "py-coverage.json").write_text(json.dumps(coverage))


def test_pytest_command(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"python": {"venv": "env"}})
    assert py_tests.pytest_command(ctx) == [
        f"{tmp_path}/env/bin/python",
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "--cov",
        "--cov-branch",
        f"--cov-report=json:{tmp_path}/.marestail/py-coverage.json",
        f"--cov-report=xml:{tmp_path}/.marestail/py-coverage.xml",
    ]


def test_failing_tests(tmp_path: Path, fake_run: Any) -> None:
    lines = "\n".join(f"line {n}" for n in range(40))
    fake = fake_run(py_tests, [(1, lines)])
    result = py_tests.run_gate(make_context(tmp_path, {"python": {"root": "src"}}))
    assert (result.gate, result.ok, result.summary) == ("py.tests", False, "tests failed")
    assert result.findings == [f"line {n}" for n in range(10, 40)]
    assert fake.options[0]["cwd"] == tmp_path / "src"
    assert fake.options[0]["timeout"] == 1800
    assert fake.calls[0][1:3] == ["-m", "pytest"]


def test_unscoped_gaps(tmp_path: Path, fake_run: Any) -> None:
    write_coverage(tmp_path, COVERAGE)
    fake_run(py_tests, [(0, "....\n12 passed in 0.3s\n")])
    result = py_tests.run_gate(make_context(tmp_path))
    assert result.findings == [
        "a.py:1 not covered",
        "b.py:3 not covered",
        "b.py:9 not covered",
        "b.py:4 branch to 6 not taken",
        "b.py:10 branch to 12 not taken",
    ]
    assert result.summary == "12 passed, coverage 87.2%, 5 gaps (need 0)"
    assert not result.ok


def test_clean_run(tmp_path: Path, fake_run: Any) -> None:
    write_coverage(tmp_path, {"totals": {"percent_covered": 100.0}, "files": {"a.py": {"missing_lines": [], "missing_branches": []}}})
    fake_run(py_tests, [(0, "3 passed")])
    result = py_tests.run_gate(make_context(tmp_path))
    assert (result.ok, result.summary, result.findings) == (True, "3 passed, coverage 100.0%, 0 gaps (need 0)", [])


def test_scoped_gaps(tmp_path: Path, fake_run: Any) -> None:
    write_coverage(tmp_path, COVERAGE)
    fake_run(py_tests, [(0, "7 passed")])
    ctx = make_context(tmp_path, scope_changed=True, changed={"b.py"}, changed_lines_map={"b.py": {9, 10}})
    result = py_tests.run_gate(ctx)
    assert result.findings == ["b.py:9 not covered", "b.py:10 branch to 12 not taken"]
    assert result.summary == "7 passed, coverage 87.2%, 2 gaps on changed lines (need 0)"


def test_scoped_lines_resolve_against_python_root(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"python": {"root": "pkg"}}, scope_changed=True, changed={"pkg/m.py"}, changed_lines_map={"pkg/m.py": {5}})
    assert py_tests.scoped_lines("m.py", ctx) == {5}
    assert py_tests.scoped_lines("other.py", ctx) == set()
    assert py_tests.scoped_lines("m.py", make_context(tmp_path)) is None


@pytest.mark.parametrize(
    ("lines", "gated", "expected"),
    [([3, 1], None, [3, 1]), ([3, 1, 2], {2, 3, 7}, [2, 3]), ([4], set(), [])],
)
def test_gated_intersect(lines: list[int], gated: set[int] | None, expected: list[int]) -> None:
    assert py_tests.gated_intersect(lines, gated) == expected


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("", "?"),
        ("nothing here\n", "?"),
        ("5 passed\n=== 1 failed, 42 passed, 2 warnings in 1s ===\nlast", "42"),
        ("  9 passed in 0.1s  ", "9"),
    ],
)
def test_count_tests(output: str, expected: str) -> None:
    assert py_tests.count_tests(output) == expected
