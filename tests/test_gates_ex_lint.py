from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from marestail import elixir
from marestail.context import Context
from marestail.gates import ex_lint
from marestail.report import Result
from tests.conftest import FakeRun, gate_shape, make_context

COMPILE_OUTPUT = """==> app
Compiling 2 files (.ex)
warning: variable "x" is unused
  lib/a.ex:3: A.f/0

warning: unused alias B
  lib/b.ex:1

error: undefined function g/0
  lib/a.exs:9
"""


def shape(result: Result) -> tuple[str, bool, str, list[str]]:
    return gate_shape(result)


def project(root: Path, elixir_root: str = ".", **fields: Any) -> Context:
    for name in ["lib/a.ex", "lib/b.ex", "test/a_test.exs"]:
        (root / elixir_root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / elixir_root / name).write_text("")
    return make_context(root, {"elixir": {"root": elixir_root}}, **fields)


def test_clean(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake = fake_run(ex_lint, [(0, ""), (0, "")])
    assert shape(ex_lint.run_gate(project(tmp_path, "app"))) == ("ex.lint", True, "mix format, compile clean", [])
    assert fake.calls == [["mix", "format", "--check-formatted"], ["mix", "compile", "--warnings-as-errors"]]
    assert fake.options == [{"cwd": tmp_path / "app", "timeout": 300}, {"cwd": tmp_path / "app", "timeout": 600}]


def test_problems(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake_run(ex_lint, [(1, "==> app\n** (Mix) mix format failed\n  lib/a.ex\n"), (1, "==> app\nwarning: x\n\n  lib/a.ex:3\n")])
    result = ex_lint.run_gate(project(tmp_path))
    findings = ["format: ** (Mix) mix format failed", "format:   lib/a.ex", "compile: warning: x", "compile:   lib/a.ex:3"]
    assert shape(result) == ("ex.lint", False, "4 problems", findings)


def test_unscoped_caps_each_tool(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    lines = "\n".join(f"line {n}" for n in range(70))
    fake_run(ex_lint, [(1, lines), (1, lines)])
    assert ex_lint.run_gate(project(tmp_path)).summary == "120 problems"


def test_scoped_skips_without_files(tmp_path: Path) -> None:
    ctx = project(tmp_path, scope_changed=True, changed={"lib/gone.ex", "README.md"})
    assert shape(ex_lint.run_gate(ctx)) == ("ex.lint", True, "skipped: no elixir files in scope", [])


def test_scoped_clean(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake = fake_run(ex_lint, [(0, ""), (1, COMPILE_OUTPUT)])
    ctx = project(tmp_path, "app", scope_changed=True, changed={"app/test/a_test.exs", "app/lib/a.ex"})
    result = ex_lint.run_gate(ctx)
    assert shape(result)[:3] == ("ex.lint", False, "4 problems in scope")
    assert fake.calls[0] == ["mix", "format", "--check-formatted", "lib/a.ex", "test/a_test.exs"]


def test_scoped_findings(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake_run(ex_lint, [(1, "==> app\nlib/b.ex is not formatted\n"), (1, COMPILE_OUTPUT)])
    result = ex_lint.run_gate(project(tmp_path, scope_changed=True, changed={"lib/b.ex"}))
    findings = ["format: lib/b.ex is not formatted", "compile: warning: unused alias B", "compile:   lib/b.ex:1"]
    assert shape(result) == ("ex.lint", False, "3 problems in scope", findings)


def test_scoped_passes(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake_run(ex_lint, [(0, ""), (0, COMPILE_OUTPUT)])
    result = ex_lint.run_gate(project(tmp_path, focus={"lib"}))
    assert shape(result) == ("ex.lint", True, "mix format, compile clean in scope", [])


def test_scoped_caps_findings(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake_run(ex_lint, [(1, "\n".join(f"lib/a.ex bad {n}" for n in range(50))), (1, "\n".join(f"lib/a.ex:{n}" for n in range(50)))])
    result = ex_lint.run_gate(project(tmp_path, scope_changed=True, changed={"lib/a.ex"}))
    assert (result.summary, len(result.findings), result.findings[-1]) == ("60 problems in scope", 60, "compile: lib/a.ex:9")


@pytest.mark.parametrize(
    ("block", "path", "expected"),
    [
        ("  lib/a.ex:3", "lib/a.ex", True),
        ("lib/a.ex", "lib/a.ex", True),
        ("see lib/a.ex here", "lib/a.ex", True),
        ("file=lib/a.ex", "lib/a.ex", True),
        ("lib/a.exs:3", "lib/a.ex", False),
        ("lib/a_ex:3", "lib/a.ex", False),
    ],
)
def test_mentions(block: str, path: str, expected: bool) -> None:
    assert ex_lint.mentions(block, path) is expected


def test_scoped_blocks() -> None:
    blocks = ex_lint.scoped_blocks("a\n  lib/a.ex:1\n \nb\n  lib/b.ex:2\n\nc lib/a.ex", ["lib/a.ex"])
    assert blocks == ["a\n  lib/a.ex:1", "c lib/a.ex"]


def test_scoped_sources(tmp_path: Path) -> None:
    project(tmp_path, "app")
    ctx = make_context(tmp_path, {"elixir": {"root": "app"}})
    assert elixir.project_files(ctx, tmp_path / "app", ["app/lib/b.ex", "app/lib/a.ex", "app/lib/zz.ex"]) == ["lib/a.ex", "lib/b.ex"]
    assert elixir.project_files(ctx, tmp_path, ["app/lib/a.ex", "lib/a.ex"]) == ["app/lib/a.ex"]


def test_relevant() -> None:
    assert ex_lint.relevant("==> app\n\n  \nkeep\n  also\n") == ["keep", "  also"]
