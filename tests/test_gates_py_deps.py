from pathlib import Path
from typing import Any

import pytest

from marestail.gates import py_deps
from tests.conftest import make_context

BROKEN = """
╔══════╗
║ Import Linter ║
╚══════╝
Contracts
Analyzed 12 files.
gates layer BROKEN

Broken contracts
━━━━━━━━━━━━━━━━

- pkg.gates -> pkg.runner (l.3)
- pkg.gone -> pkg.cli (l.9)
- pkg.other -> pkg.cli (l.4)
"""


def make_package(root: Path) -> None:
    (root / "pkg" / "gates").mkdir(parents=True)
    (root / "pkg" / "gates" / "__init__.py").write_text("")
    (root / "pkg" / "other.py").write_text("")


def test_contracts_kept(tmp_path: Path, fake_run: Any) -> None:
    fake = fake_run(py_deps, [(0, "ok")])
    result = py_deps.run_gate(make_context(tmp_path, {"python": {"root": "src"}}))
    assert (result.gate, result.ok, result.summary, result.findings) == ("py.deps", True, "import contracts kept", [])
    assert fake.calls == [[f"{tmp_path}/.venv/bin/lint-imports", "--no-cache"]]
    assert fake.options == [{"cwd": tmp_path, "env": {"PYTHONPATH": str(tmp_path / "src")}, "timeout": 600}]


def test_contracts_broken(tmp_path: Path, fake_run: Any) -> None:
    fake_run(py_deps, [(1, BROKEN)])
    result = py_deps.run_gate(make_context(tmp_path))
    assert (result.ok, result.summary) == (False, "import contracts broken")
    assert result.findings == [
        "gates layer BROKEN",
        "Broken contracts",
        "- pkg.gates -> pkg.runner (l.3)",
        "- pkg.gone -> pkg.cli (l.9)",
        "- pkg.other -> pkg.cli (l.4)",
    ]


def test_scoped_without_marker_reports_everything(tmp_path: Path, fake_run: Any) -> None:
    fake_run(py_deps, [(1, "Error: no config\nmore")])
    result = py_deps.run_gate(make_context(tmp_path, scope_changed=True))
    assert (result.ok, result.summary, result.findings) == (False, "import contracts broken", ["Error: no config", "more"])


def test_scoped_violations(tmp_path: Path, fake_run: Any) -> None:
    make_package(tmp_path)
    fake_run(py_deps, [(1, BROKEN)])
    result = py_deps.run_gate(make_context(tmp_path, scope_changed=True, changed={"pkg/gates/__init__.py"}))
    assert (result.ok, result.summary) == (False, "import contracts broken in scope")
    assert result.findings == ["- pkg.gates -> pkg.runner (l.3)", "- pkg.gone -> pkg.cli (l.9)"]


def test_scoped_clean(tmp_path: Path, fake_run: Any) -> None:
    make_package(tmp_path)
    fake_run(py_deps, [(1, BROKEN.replace("pkg.gone", "pkg.other"))])
    result = py_deps.run_gate(make_context(tmp_path, scope_changed=True, changed={"unrelated.py"}))
    assert (result.ok, result.summary, result.findings) == (True, "import contracts kept in scope", [])


def test_module_paths(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"python": {"root": "src"}})
    assert py_deps.module_paths("a.b", ctx) == [(tmp_path / "src/a/b.py").resolve(), (tmp_path / "src/a/b/__init__.py").resolve()]


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("", []),
        ("intro\n  ║  \nsomething Error here\nnext\n", ["something Error here", "next"]),
        ("intro\nsecond\n", ["intro", "second"]),
        ("\n".join(f"x{n}" for n in range(70)), [f"x{n}" for n in range(60)]),
    ],
)
def test_broken_lines(output: str, expected: list[str]) -> None:
    assert py_deps.broken_lines(output) == expected
