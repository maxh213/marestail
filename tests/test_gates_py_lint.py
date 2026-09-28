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


def test_findings_are_capped(tmp_path: Path, fake_run: Any) -> None:
    fake_run(py_lint, [(1, "\n".join(f"e{n}" for n in range(70))), (0, ""), (0, "")])
    result = checked(py_lint.run_gate(make_context(tmp_path)), py_lint.GATE)
    assert result.findings == [f"ruff: e{n}" for n in range(60)]


def hyper(root: Path) -> Any:
    return make_context(root, scope_changed=True, hyper=True, changed={"app/legacy.py"}, changed_lines_map={"app/legacy.py": {6}})


def test_hyper_keeps_only_findings_on_changed_lines(tmp_path: Path, fake_run: Any) -> None:
    ruff_out = (
        "app/legacy.py:1:1: I001 [*] Import block is un-sorted\n"
        "app/legacy.py:6:34: E711 Comparison to `None`\n"
        "[*] 2 fixable with the `--fix` option.\n"
    )
    format_out = "unformatted: File would be reformatted\n  --> app/legacy.py:14:13\n1 file would be reformatted\n"
    fake_run(py_lint, [(1, ruff_out), (1, format_out), (1, "app/legacy.py:6: error: bad\napp/legacy.py:9: error: old\n")])
    result = checked(py_lint.run_gate(hyper(tmp_path)), py_lint.GATE)
    assert result.findings == ["ruff: app/legacy.py:6:34: E711 Comparison to `None`", "mypy: app/legacy.py:6: error: bad"]
    assert (result.ok, result.summary) == (False, "2 problems")


def test_hyper_passes_when_only_old_lines_fail(tmp_path: Path, fake_run: Any) -> None:
    fake_run(py_lint, [(1, "app/legacy.py:1:8: F401 unused\n"), (0, ""), (0, "")])
    result = checked(py_lint.run_gate(hyper(tmp_path)), py_lint.GATE)
    assert (result.ok, result.summary, result.findings) == (True, "ruff, ruff format, mypy clean", [])


def test_hyper_keeps_crash_output_unfiltered(tmp_path: Path, fake_run: Any) -> None:
    missing = f"{tmp_path}/.venv/bin/ruff: not found (install it)"
    fake_run(py_lint, [(127, missing), (127, missing), (2, "app/legacy.py:1: error: config broken\n")])
    result = checked(py_lint.run_gate(hyper(tmp_path)), py_lint.GATE)
    assert result.findings == [f"ruff: {missing}", f"format: {missing}", "mypy: app/legacy.py:1: error: config broken"]
    assert (result.ok, result.summary) == (False, "3 problems")
