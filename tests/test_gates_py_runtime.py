import sys
from pathlib import Path

import pytest

from marestail.gates import py_runtime
from tests.conftest import make_context

CURRENT = f"{sys.version_info[0]}.{sys.version_info[1]}"


def test_missing_root_skips(tmp_path: Path) -> None:
    result = py_runtime.run_gate(make_context(tmp_path, {"python": {"root": "absent"}}))
    assert (result.gate, result.ok, result.summary) == ("py.runtime", True, "skipped: no python root")


def test_nothing_declared_skips(tmp_path: Path) -> None:
    result = py_runtime.run_gate(make_context(tmp_path))
    assert (result.ok, result.summary) == (True, "skipped: nothing declares the interpreter that ships")


def test_clean_run(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n")
    result = py_runtime.run_gate(make_context(tmp_path, {"python": {"runtime": "python 3.12"}}))
    assert (result.ok, result.findings) == (True, [])
    assert result.summary == "3.12 from marestail.toml [python] runtime; tooling agrees and every source parses"


def test_scoped_summary_carries_global_note(tmp_path: Path) -> None:
    result = py_runtime.run_gate(make_context(tmp_path, {"python": {"runtime": "3.12"}}, scope_changed=True))
    assert result.summary.endswith("every source parses (global gate — scope: changed)")


def test_disagreements_and_parse_errors(tmp_path: Path) -> None:
    (tmp_path / "Dockerfile").write_text("FROM node:20 AS web\nFROM python:3.11-slim\nfrom busybox\n")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nrequires-python = ">=3.12"\n[tool.ruff]\ntarget-version = "py313"\n[tool.mypy]\npython_version = "3.11"\n'
    )
    (tmp_path / "tool.py").write_text("#!/usr/bin/env python3.10\nx = 1\n")
    (tmp_path / "new.py").write_text("type Alias = int\n")
    result = py_runtime.run_gate(make_context(tmp_path))
    assert result.findings == [
        "pyproject.toml [project] requires-python says Python 3.12 but Dockerfile ships 3.11",
        "pyproject.toml [tool.ruff] target-version says Python 3.13 but Dockerfile ships 3.11",
        "tool.py shebang says Python 3.10 but Dockerfile ships 3.11",
        "new.py:1 will not parse on Python 3.11: Type statement is only supported in Python 3.12 and greater",
    ]
    assert (result.ok, result.summary) == (False, "4 findings against 3.11 from Dockerfile")


def test_future_interpreter(tmp_path: Path) -> None:
    result = py_runtime.run_gate(make_context(tmp_path, {"python": {"runtime": "3.99"}}))
    assert result.findings == ["this interpreter cannot check syntax for Python 3.99; run the gate on 3.99 or newer"]


def test_current_interpreter_is_understood(tmp_path: Path) -> None:
    result = py_runtime.run_gate(make_context(tmp_path, {"python": {"runtime": CURRENT}}))
    assert result.ok


@pytest.mark.parametrize(
    ("files", "dockerfile", "expected"),
    [
        (["Dockerfile"], "FROM alpine\n", None),
        (["missing", "deploy/Containerfile"], "FROM python:3.10\nFROM scratch\n", ((3, 10), "deploy/Containerfile")),
        (["deploy/Containerfile"], "FROM python-3.9\nFROM python3.8\n", ((3, 8), "deploy/Containerfile")),
    ],
)
def test_image_version(tmp_path: Path, files: list[str], dockerfile: str, expected: object) -> None:
    (tmp_path / "Dockerfile").write_text(dockerfile)
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy" / "Containerfile").write_text(dockerfile)
    assert py_runtime.image_version(make_context(tmp_path, {"python": {"deploy_files": files}})) == expected


def test_unparseable_runtime_falls_back_to_image(tmp_path: Path) -> None:
    (tmp_path / "Dockerfile").write_text("FROM python:3.12\n")
    assert py_runtime.deployed_version(make_context(tmp_path, {"python": {"runtime": "latest"}})) == ((3, 12), "Dockerfile")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", []),
        ('[project]\nrequires-python = 3\n[tool.ruff]\ntarget-version = "latest"\n', []),
        ('[tool.mypy]\npython_version = "any"\n[tool.ruff]\ntarget-version = 312\n', []),
    ],
)
def test_pyproject_claims_ignore_unusable_values(tmp_path: Path, text: str, expected: list[object]) -> None:
    (tmp_path / "pyproject.toml").write_text(text)
    assert py_runtime.pyproject_claims(make_context(tmp_path)) == expected


def test_sources_skip_tooling_folders(tmp_path: Path) -> None:
    for relative in ("a.py", "pkg/b.py", ".venv/c.py", "perf/d.py", "node_modules/x/e.py", "notes.txt"):
        (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / relative).write_text("")
    assert py_runtime.sources(make_context(tmp_path), tmp_path) == [tmp_path / "a.py", tmp_path / "pkg" / "b.py"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [(">=3.11", (3, 11)), ("v.3.12.1", (3, 12)), ("3.x.4.5", (4, 5)), ("3.", None), (chr(0x06F1) + "." + chr(0x06F2), (1, 2))],
)
def test_floor_version(text: str, expected: tuple[int, int] | None) -> None:
    assert py_runtime.floor_version(text) == expected
