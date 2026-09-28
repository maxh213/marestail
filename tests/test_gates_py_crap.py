import json
from pathlib import Path
from typing import Any

import pytest

from marestail.gates import _hyper_crap, py_crap
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


HYPER_RADON = {
    "app/p.py": [
        {
            "type": "function",
            "name": "outer",
            "lineno": 1,
            "endline": 20,
            "complexity": 4,
            "closures": [{"type": "function", "name": "inner", "lineno": 3, "endline": 12, "complexity": 9}],
        },
        {
            "type": "class",
            "name": "K",
            "lineno": 22,
            "endline": 30,
            "complexity": 6,
            "methods": [{"type": "method", "name": "meth", "lineno": 23, "endline": 30, "complexity": 6, "classname": "K"}],
        },
    ]
}
HYPER_COVERAGE = {
    "files": {
        "app/p.py": {
            "missing_lines": [8, 25],
            "functions": {
                "outer": {"start_line": 1, "summary": {"percent_covered": 57.0}},
                "outer.inner": {"start_line": 3, "summary": {"percent_covered": 21.0}},
            },
        }
    }
}


def hyper_run(tmp_path: Path, fake_run: Any, monkeypatch: Any, base: Any, lines: set[int]) -> Any:
    (tmp_path / ".marestail").mkdir()
    (tmp_path / ".marestail" / "py-coverage.json").write_text(json.dumps(HYPER_COVERAGE))
    monkeypatch.setattr(_hyper_crap, "base_text", lambda root, ref, path: "base source")

    def reply(command: list[str]) -> tuple[int, str]:
        if "-e" in command:
            return 0, json.dumps(HYPER_RADON)
        assert Path(command[-1]).read_text() == "base source"
        return (1, "boom") if base == "fail" else (0, json.dumps({command[-1]: base}))

    fake = fake_run(py_crap, reply)
    ctx = make_context(tmp_path, scope_changed=True, hyper=True, changed={"app/p.py"}, changed_lines_map={"app/p.py": lines})
    return checked(py_crap.run_gate(ctx), py_crap.GATE), fake


def test_hyper_gates_the_innermost_function_against_base(tmp_path: Path, fake_run: Any, monkeypatch: Any) -> None:
    base = [
        {**HYPER_RADON["app/p.py"][0], "closures": [{"type": "function", "name": "inner", "lineno": 3, "endline": 12, "complexity": 8}]}
    ]
    result, fake = hyper_run(tmp_path, fake_run, monkeypatch, base, {5, 19, 25})
    assert result.findings == [
        "app/p.py:3 inner complexity rose from 8 to 9; move the new condition into its own function",
        "app/p.py:23 meth crap=42.0 (cc=6, coverage=0%)",
    ]
    assert result.summary == "2 innermost changed functions, 2 above CRAP 4, 0 of them no worse than base"
    assert fake.calls[1][:3] == [f"{tmp_path}/.venv/bin/radon", "cc", "-j"]
    assert fake.options[1]["cwd"] == tmp_path


def test_hyper_passes_a_method_no_worse_than_base(tmp_path: Path, fake_run: Any, monkeypatch: Any) -> None:
    result, _ = hyper_run(tmp_path, fake_run, monkeypatch, HYPER_RADON["app/p.py"], {5, 24})
    assert (result.ok, result.findings) == (True, [])
    assert result.summary == "2 innermost changed functions, 2 above CRAP 4, 2 of them no worse than base"


def test_hyper_reports_uncovered_changed_lines(tmp_path: Path, fake_run: Any, monkeypatch: Any) -> None:
    result, _ = hyper_run(tmp_path, fake_run, monkeypatch, HYPER_RADON["app/p.py"], {5, 8})
    assert result.findings == ["app/p.py:3 inner crap=48.9 (cc=9, coverage=21%); changed lines not covered: 8"]


@pytest.mark.parametrize("base", ["fail", {"error": "invalid syntax"}])
def test_hyper_unreadable_base_falls_back_to_crap_max(tmp_path: Path, fake_run: Any, monkeypatch: Any, base: Any) -> None:
    result, _ = hyper_run(tmp_path, fake_run, monkeypatch, base, {5})
    assert (
        result.summary
        == "1 innermost changed functions, 1 above CRAP 4, 0 of them no worse than base; no base complexity for app/p.py, crap_max only"
    )
    assert result.findings == ["app/p.py:3 inner crap=48.9 (cc=9, coverage=21%)"]


def test_unit_labels_methods_with_their_class() -> None:
    method = {"name": "meth", "lineno": 4, "endline": 6, "complexity": 2, "classname": "K"}
    assert py_crap.unit("a.py", method) == {"file": "a.py", "line": 4, "start": 4, "end": 6, "name": "meth", "label": "K.meth", "cc": 2}
    closure = py_crap.unit("a.py", {"name": "f", "lineno": 7, "complexity": 1})
    assert (closure["label"], closure["end"]) == ("f", 7)
