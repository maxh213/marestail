import json
from pathlib import Path
from typing import Any

from marestail.gates import py_crap
from tests.conftest import checked, make_context, untimed

RADON = {
    "m.py": [
        {"type": "function", "name": "low", "lineno": 1, "endline": 3, "complexity": 2},
        {
            "type": "class",
            "name": "K",
            "lineno": 5,
            "endline": 30,
            "complexity": 9,
            "methods": [{"type": "method", "name": "meth", "lineno": 6, "endline": 12, "complexity": 5}],
        },
        {
            "type": "function",
            "name": "outer",
            "lineno": 20,
            "endline": 29,
            "complexity": 3,
            "closures": [{"type": "function", "name": "inner", "lineno": 22, "complexity": 6}],
        },
    ],
    "n.py": [{"type": "function", "name": "untested", "lineno": 4, "endline": 8, "complexity": 3}],
}
COVERAGE = {
    "files": {
        "m.py": {
            "functions": {
                "": {"start_line": 1, "summary": {"percent_covered": 0.0}},
                "low": {"start_line": 1, "summary": {"percent_covered": 100.0}},
                "K.meth": {"start_line": 6, "summary": {"percent_covered": 50.0}},
                "outer": {"start_line": 20, "summary": {"percent_covered": 100.0}},
            }
        }
    }
}


def write_coverage(root: Path) -> None:
    (root / ".marestail").mkdir()
    (root / ".marestail" / "py-coverage.json").write_text(json.dumps(COVERAGE))


def test_needs_coverage_first(tmp_path: Path, fake_run: Any) -> None:
    fake = fake_run(py_crap)
    result = untimed(py_crap.run_gate(make_context(tmp_path)), py_crap.GATE)
    assert (result.gate, result.ok, result.summary, result.findings, result.seconds) == (
        "py.crap",
        False,
        "no coverage data; py.tests must run first",
        [],
        0.0,
    )
    assert fake.calls == []


def test_radon_failure(tmp_path: Path, fake_run: Any) -> None:
    write_coverage(tmp_path)
    fake = fake_run(py_crap, [(2, "\n".join(str(n) for n in range(15)))])
    result = checked(py_crap.run_gate(make_context(tmp_path, {"python": {"root": "src"}})), py_crap.GATE)
    assert (result.ok, result.summary) == (False, "radon failed")
    assert result.findings == [str(n) for n in range(5, 15)]
    assert fake.options[0]["cwd"] == tmp_path / "src"


def test_radon_command(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"python": {"sources": ["pkg", "tools"]}})
    assert py_crap.radon_command(ctx) == [
        f"{tmp_path}/.venv/bin/radon",
        "cc",
        "-j",
        "-e",
        "mutants/*,.venv/*,__pycache__/*,perf/*",
        "pkg",
        "tools",
    ]
    assert py_crap.radon_command(make_context(tmp_path))[-1] == "."


def test_scores_every_function(tmp_path: Path, fake_run: Any) -> None:
    write_coverage(tmp_path)
    fake_run(py_crap, [(0, json.dumps(RADON))])
    result = checked(py_crap.run_gate(make_context(tmp_path)), py_crap.GATE)
    assert result.findings == [
        "m.py:22 inner crap=42.0 (cc=6, coverage=0%)",
        "n.py:4 untested crap=12.0 (cc=3, coverage=0%)",
        "m.py:6 meth crap=8.1 (cc=5, coverage=50%)",
    ]
    assert (result.ok, result.summary) == (False, "5 functions, 3 above CRAP 4")


def test_custom_limit_passes(tmp_path: Path, fake_run: Any) -> None:
    write_coverage(tmp_path)
    fake_run(py_crap, [(0, json.dumps(RADON))])
    result = checked(py_crap.run_gate(make_context(tmp_path, {"python": {"crap_max": 42}})), py_crap.GATE)
    assert (result.ok, result.summary, result.findings) == (True, "5 functions, 0 above CRAP 42", [])


def test_limit_is_inclusive(tmp_path: Path, fake_run: Any) -> None:
    write_coverage(tmp_path)
    fake_run(py_crap, [(0, json.dumps(RADON))])
    result = checked(py_crap.run_gate(make_context(tmp_path, {"python": {"crap_max": 12}})), py_crap.GATE)
    assert result.summary == "5 functions, 1 above CRAP 12"


def test_scoped_to_changed_lines(tmp_path: Path, fake_run: Any) -> None:
    write_coverage(tmp_path)
    fake_run(py_crap, [(0, json.dumps(RADON))])
    ctx = make_context(tmp_path, scope_changed=True, changed={"m.py"}, changed_lines_map={"m.py": {12, 22}})
    result = checked(py_crap.run_gate(ctx), py_crap.GATE)
    assert result.findings == ["m.py:22 inner crap=42.0 (cc=6, coverage=0%)", "m.py:6 meth crap=8.1 (cc=5, coverage=50%)"]
    assert result.summary == "3 functions, 2 above CRAP 4 on changed functions"


def test_block_without_endline_covers_its_first_line() -> None:
    assert py_crap.intersects({"lineno": 7}, {7})
    assert not py_crap.intersects({"lineno": 7}, {8})
    assert py_crap.intersects({"lineno": 7, "endline": 9}, {9})
    assert not py_crap.intersects({"lineno": 7, "endline": 9}, {10, 6})


def test_score() -> None:
    assert py_crap.score("f.py", {"lineno": 3, "name": "g", "complexity": 4}, 0.5) == {
        "file": "f.py",
        "line": 3,
        "name": "g",
        "cc": 4,
        "cov": 0.5,
        "crap": 6.0,
    }


def test_uncovered_file_is_still_scored(tmp_path: Path, fake_run: Any) -> None:
    write_coverage(tmp_path)
    fake_run(
        py_crap, [(0, json.dumps({**RADON, "marestail/tui/app.py": [{"type": "function", "name": "draw", "lineno": 1, "complexity": 15}]}))]
    )
    result = checked(py_crap.run_gate(make_context(tmp_path)), py_crap.GATE)
    assert "marestail/tui/app.py:1 draw crap=240.0 (cc=15, coverage=0%)" in result.findings
    assert result.summary == "6 functions, 4 above CRAP 4"
