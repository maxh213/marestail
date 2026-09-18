import configparser
import json
from pathlib import Path
from typing import Any

import pytest

from marestail.gates import py_crap
from tests.conftest import make_context

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
    result = py_crap.run_gate(make_context(tmp_path))
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
    result = py_crap.run_gate(make_context(tmp_path, {"python": {"root": "src"}}))
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
    result = py_crap.run_gate(make_context(tmp_path))
    assert result.findings == [
        "m.py:22 inner crap=42.0 (cc=6, coverage=0%)",
        "n.py:4 untested crap=12.0 (cc=3, coverage=0%)",
        "m.py:6 meth crap=8.1 (cc=5, coverage=50%)",
    ]
    assert (result.ok, result.summary) == (False, "5 functions, 3 above CRAP 4")


def test_custom_limit_passes(tmp_path: Path, fake_run: Any) -> None:
    write_coverage(tmp_path)
    fake_run(py_crap, [(0, json.dumps(RADON))])
    result = py_crap.run_gate(make_context(tmp_path, {"python": {"crap_max": 42}}))
    assert (result.ok, result.summary, result.findings) == (True, "5 functions, 0 above CRAP 42", [])


def test_limit_is_inclusive(tmp_path: Path, fake_run: Any) -> None:
    write_coverage(tmp_path)
    fake_run(py_crap, [(0, json.dumps(RADON))])
    result = py_crap.run_gate(make_context(tmp_path, {"python": {"crap_max": 12}}))
    assert result.summary == "5 functions, 1 above CRAP 12"


def test_scoped_to_changed_lines(tmp_path: Path, fake_run: Any) -> None:
    write_coverage(tmp_path)
    fake_run(py_crap, [(0, json.dumps(RADON))])
    ctx = make_context(tmp_path, scope_changed=True, changed={"m.py"}, changed_lines_map={"m.py": {12, 22}})
    result = py_crap.run_gate(ctx)
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


def write_omit(root: Path, body: str) -> None:
    (root / "pyproject.toml").write_text(body)


def test_omitted_files_are_not_scored(tmp_path: Path, fake_run: Any) -> None:
    write_coverage(tmp_path)
    write_omit(tmp_path, '[tool.coverage.run]\nomit = ["n.py", "marestail/tui/*"]\n')
    fake_run(
        py_crap, [(0, json.dumps({**RADON, "marestail/tui/app.py": [{"type": "function", "name": "draw", "lineno": 1, "complexity": 15}]}))]
    )
    result = py_crap.run_gate(make_context(tmp_path))
    assert result.findings == ["m.py:22 inner crap=42.0 (cc=6, coverage=0%)", "m.py:6 meth crap=8.1 (cc=5, coverage=50%)"]
    assert (result.ok, result.summary) == (False, "4 functions, 2 above CRAP 4")


def test_omit_string_skips_matching_files(tmp_path: Path, fake_run: Any) -> None:
    write_coverage(tmp_path)
    write_omit(tmp_path, '[tool.coverage.run]\nomit = "n.py"\n')
    fake_run(py_crap, [(0, json.dumps(RADON))])
    result = py_crap.run_gate(make_context(tmp_path))
    assert result.summary == "4 functions, 2 above CRAP 4"
    assert all("n.py" not in finding for finding in result.findings)


def test_coverage_omits_missing_pyproject(tmp_path: Path) -> None:
    assert py_crap.coverage_omits(make_context(tmp_path)) == []


def test_coverage_omits_reads_list(tmp_path: Path) -> None:
    write_omit(tmp_path, '[tool.coverage.run]\nomit = ["marestail/tui/*", "marestail/__init__.py"]\n')
    assert py_crap.coverage_omits(make_context(tmp_path)) == ["marestail/tui/*", "marestail/__init__.py"]


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("[tool.coverage.run]\nomit = 1\n", []),
        ("[tool]\ncoverage = 1\n", []),
        ("[tool.coverage]\nrun = 1\n", []),
        ("[tool.poetry]\nname = 'x'\n", []),
        ("omit = ['nope']\n", []),
        ("[[[", []),
    ],
)
def test_coverage_omits_ignores_unusable_config(tmp_path: Path, body: str, expected: list[str]) -> None:
    write_omit(tmp_path, body)
    assert py_crap.coverage_omits(make_context(tmp_path)) == expected


def test_parsed_toml_missing_and_directory(tmp_path: Path) -> None:
    missing = tmp_path / "nope.toml"
    directory = tmp_path / "pyproject.toml"
    directory.mkdir()
    assert py_crap.parsed_toml(missing) == {}
    assert py_crap.parsed_toml(directory) == {}


def test_as_table() -> None:
    assert py_crap.as_table({"a": 1}) == {"a": 1}
    assert py_crap.as_table(["not", "a", "table"]) == {}
    assert py_crap.as_table(None) == {}
    assert py_crap.as_table("tool") == {}


def test_mapping() -> None:
    assert py_crap.mapping("x", "k") == {}
    assert py_crap.mapping({"k": 1}, "k") == {}
    assert py_crap.mapping({}, "k") == {}
    assert py_crap.mapping({"k": {"a": 1}}, "k") == {"a": 1}


def test_as_patterns() -> None:
    assert py_crap.as_patterns(["a", 2]) == ["a", "2"]
    assert py_crap.as_patterns("tui/*") == ["tui/*"]
    assert py_crap.as_patterns(None) == []
    assert py_crap.as_patterns(3) == []


def test_listed_omit() -> None:
    assert py_crap.listed_omit("tui/*") == ["tui/*"]
    assert py_crap.listed_omit(["tui/*"]) == []
    assert py_crap.listed_omit(None) == []


def test_omitted_file() -> None:
    patterns = ["marestail/tui/*", "marestail/__init__.py"]
    assert py_crap.omitted_file("marestail/tui/app.py", patterns)
    assert py_crap.omitted_file("marestail/__init__.py", patterns)
    assert not py_crap.omitted_file("marestail/cli.py", patterns)
    assert not py_crap.omitted_file("marestail/tui/app.py", [])


def test_scored_unless_omitted_returns_empty_for_omitted(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    blocks = [{"type": "function", "name": "draw", "lineno": 1, "complexity": 15}]
    assert py_crap.scored_unless_omitted("marestail/tui/app.py", blocks, {"files": {}}, ctx, ["marestail/tui/*"]) == []
    scored = py_crap.scored_unless_omitted("m.py", blocks, {"files": {}}, ctx, ["marestail/tui/*"])
    assert scored[0]["name"] == "draw"
    assert scored[0]["crap"] == 240.0


def test_run_section_without_coverage_table(tmp_path: Path) -> None:
    write_omit(tmp_path, "[tool.ruff]\nline-length = 140\n")
    assert py_crap.run_section(make_context(tmp_path)) == {}


def test_this_repo_omits_tui_from_crap() -> None:
    root = Path(__file__).resolve().parent.parent
    patterns = py_crap.coverage_omits(make_context(root))
    assert py_crap.omitted_file("marestail/tui/app.py", patterns)
    assert py_crap.omitted_file("marestail/tui/collect.py", patterns)
    assert py_crap.omitted_file("marestail/__init__.py", patterns)
    assert not py_crap.omitted_file("marestail/gates/py_crap.py", patterns)


def test_this_repo_radon_cfg_ignores_tui() -> None:
    parser = configparser.ConfigParser()
    read = parser.read(Path(__file__).resolve().parent.parent / "radon.cfg")
    assert read
    assert parser.get("radon", "ignore") == "tui"
