from pathlib import Path
from typing import Any

from marestail.gates import py_lint
from tests.conftest import checked, make_context, untimed


def test_scoped_without_python_changes_skips(tmp_path: Path, fake_run: Any) -> None:
    fake = fake_run(py_lint)
    ctx = make_context(tmp_path, scope_changed=True, changed={"perf/bench.py", "README.md"})
    result = untimed(py_lint.run_gate(ctx), py_lint.GATE)
    assert (result.gate, result.ok, result.summary) == ("py.lint", True, "skipped: no changed python files")
    assert fake.calls == []


def test_clean_full_run(tmp_path: Path, fake_run: Any) -> None:
    fake = fake_run(py_lint)
    result = checked(py_lint.run_gate(make_context(tmp_path)), py_lint.GATE)
    assert (result.ok, result.summary, result.findings) == (True, "ruff, ruff format, mypy clean", [])
    ruff = f"{tmp_path}/.venv/bin/ruff"
    excluded = ["--extend-exclude", "perf/**", "--extend-exclude", "qa/**", "--extend-exclude", "features/**"]
    assert fake.calls == [
        [ruff, "check", "--output-format", "concise", *excluded, "."],
        [ruff, "format", "--check", *excluded, "."],
        [f"{tmp_path}/.venv/bin/mypy", "--no-error-summary", "--no-pretty"],
    ]
    assert fake.options == [{"cwd": tmp_path, "timeout": 900}] * 3


def test_findings_are_labelled(tmp_path: Path, fake_run: Any) -> None:
    ruff_out = "a.py:1:1: F401 unused\n\nFound 1 error.\nwarning: something\n"
    fake_run(py_lint, [(1, ruff_out), (0, "ignored"), (1, "b.py:2: error: bad\n")])
    result = checked(py_lint.run_gate(make_context(tmp_path)), py_lint.GATE)
    assert result.findings == ["ruff: a.py:1:1: F401 unused", "mypy: b.py:2: error: bad"]
    assert (result.ok, result.summary) == (False, "2 problems")


def test_scoped_targets_nested_root(tmp_path: Path, fake_run: Any) -> None:
    (tmp_path / "src").mkdir()
    fake = fake_run(py_lint)
    ctx = make_context(
        tmp_path, {"python": {"root": "src"}}, scope_changed=True, changed={"src/a.py", "src/perf/b.py", "other/c.py", "src/d.txt"}
    )
    checked(py_lint.run_gate(ctx), py_lint.GATE)
    ruff = f"{tmp_path}/.venv/bin/ruff"
    assert fake.calls == [
        [ruff, "check", "--output-format", "concise", "src/a.py", "src/perf/b.py"],
        [ruff, "format", "--check", "src/a.py", "src/perf/b.py"],
        [f"{tmp_path}/.venv/bin/mypy", "--no-error-summary", "--no-pretty", "src/a.py", "src/perf/b.py"],
    ]


def test_unscoped_nested_root_targets_the_folder(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"python": {"root": "src"}})
    assert py_lint.python_targets(ctx) == ["src"]
    assert py_lint.path_exclusions(ctx) == []
    assert py_lint.mypy_targets(ctx) == []


def test_path_exclusions_at_repo_root(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    assert py_lint.path_exclusions(ctx) == [
        "--extend-exclude",
        "perf/**",
        "--extend-exclude",
        "qa/**",
        "--extend-exclude",
        "features/**",
    ]


def test_relevant_caps_lines() -> None:
    output = "\n".join(f"e{n}" for n in range(70))
    assert py_lint.relevant(output) == [f"e{n}" for n in range(60)]
