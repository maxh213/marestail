from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from marestail.context import Context
from marestail.gates import ex_deps
from marestail.report import Result
from tests.conftest import FakeRun, gate_shape, make_context

XREF = """Compiling 3 files (.ex)
Cycle of length 2:

    lib/a.ex
    lib/b.ex

Cycle of length 3:

    lib/c.ex
    lib/d.ex
    lib/e.exs

** (Mix) Too many cycles (found: 2, permitted: 0)
"""


def shape(result: Result) -> tuple[str, bool, str, list[str]]:
    return gate_shape(result)


def project(root: Path, elixir_root: str = ".", **fields: Any) -> Context:
    return make_context(root, {"elixir": {"root": elixir_root}}, **fields)


def test_acyclic(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake = fake_run(ex_deps, [(0, "")])
    assert shape(ex_deps.run_gate(project(tmp_path, "app"))) == ("ex.deps", True, "dependency graph acyclic", [])
    assert fake.calls == [["mix", "xref", "graph", "--format", "cycles", "--fail-above", "0"]]
    assert fake.options == [{"cwd": tmp_path / "app", "timeout": 600}]


def test_unscoped_lists_cycle_lines(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake_run(ex_deps, [(1, XREF)])
    findings = ["Cycle of length 2:", "lib/a.ex", "lib/b.ex", "Cycle of length 3:", "lib/c.ex", "lib/d.ex", "lib/e.exs"]
    assert shape(ex_deps.run_gate(project(tmp_path))) == ("ex.deps", False, "dependency cycles found", findings)


def test_unscoped_caps_lines(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake_run(ex_deps, [(1, "\n".join(f"lib/m{n}.ex" for n in range(70)))])
    assert len(ex_deps.run_gate(project(tmp_path)).findings) == 60


def test_scoped_without_cycles_output(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake_run(ex_deps, [(1, "** (Mix) could not compile\n\nerror")])
    result = ex_deps.run_gate(project(tmp_path, scope_changed=True, changed={"lib/a.ex"}))
    assert shape(result) == ("ex.deps", False, "xref failed", ["** (Mix) could not compile", "error"])


@pytest.mark.parametrize(
    ("elixir_root", "changed", "ok", "summary", "findings"),
    [
        (".", {"lib/d.ex"}, False, "1 dependency cycles in scope", ["cycle: lib/c.ex, lib/d.ex, lib/e.exs"]),
        (
            "app",
            {"app/lib/a.ex", "app/lib/e.exs"},
            False,
            "2 dependency cycles in scope",
            ["cycle: lib/a.ex, lib/b.ex", "cycle: lib/c.ex, lib/d.ex, lib/e.exs"],
        ),
        ("app", {"lib/a.ex"}, True, "no dependency cycles in scope", []),
    ],
)
def test_scoped_cycles(
    tmp_path: Path, fake_run: Callable[..., FakeRun], elixir_root: str, changed: set[str], ok: bool, summary: str, findings: list[str]
) -> None:
    fake_run(ex_deps, [(1, XREF)])
    result = ex_deps.run_gate(project(tmp_path, elixir_root, scope_changed=True, changed=changed))
    assert shape(result) == ("ex.deps", ok, summary, findings)


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("", []),
        ("Cycle of length 2:\n\n\tlib/a.ex\n  lib/b.exs\n", [["lib/a.ex", "lib/b.exs"]]),
        ("Cycle of length 2:\n  lib/a.ex\nlib/b.ex\n  lib/c.ex\n", [["lib/a.ex"]]),
        ("Cycle of length 2:\n  lib/a.ex\n  lib/readme.md\n  lib/c.ex\n", [["lib/a.ex"]]),
        ("  lib/a.ex\nCycle of length 1:\n", [[]]),
        ("Cycle of length 1:\n lib/a.ex\nCycle of length 1:\n lib/b.ex", [["lib/a.ex"], ["lib/b.ex"]]),
    ],
)
def test_parse_cycles(output: str, expected: list[list[str]]) -> None:
    assert ex_deps.parse_cycles(output) == expected


@pytest.mark.parametrize(("line", "expected"), [("", False), ("   ", False), ("Cycle x", True), ("  lib/a.ex", True), ("other", False)])
def test_cycle_line(line: str, expected: bool) -> None:
    assert ex_deps.cycle_line(line) is expected


def test_repo_path_uses_the_elixir_root(tmp_path: Path) -> None:
    nested = tmp_path / "app"
    nested.mkdir()
    ctx = make_context(tmp_path, {"elixir": {"root": "app"}})
    assert ex_deps.repo_path("lib/a.ex", ctx, nested) == "app/lib/a.ex"
    assert ex_deps.repo_path("lib/a.ex", ctx, tmp_path) == "lib/a.ex"


def test_xref_timeout() -> None:
    assert ex_deps.XREF_TIMEOUT == 600
