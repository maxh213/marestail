from pathlib import Path
from typing import Any

import pytest

from marestail.gates import ts_deps
from tests.conftest import checked, make_context

TS = {"ts": {"root": "web"}}
REPORT = "\n".join(
    [
        "",
        "  error no-circular: src/a.ts → src/b.ts → src/a.ts",
        "warn not-to-test: src/b.ts",
        "info x: /outside/c.ts",
        "",
        "x 3 dependency violations (3 errors, 0 warnings). 20 modules cruised.",
    ]
)


def test_default_command_and_clean_run(tmp_path: Path, fake_run: Any) -> None:
    fake = fake_run(ts_deps, [(0, "no violations\n")])

    result = checked(ts_deps.run_gate(make_context(tmp_path, TS)), "ts.deps")

    assert (result.gate, result.ok, result.summary, result.findings) == ("ts.deps", True, "dependency rules kept", [])
    assert fake.calls == [["npx", "depcruise", "--config", ".dependency-cruiser.cjs", "--output-type", "err", "src"]]
    assert fake.options == [{"cwd": tmp_path / "web", "timeout": 600}]


def test_configured_command(tmp_path: Path, fake_run: Any) -> None:
    fake = fake_run(ts_deps, [(0, "")])

    checked(ts_deps.run_gate(make_context(tmp_path, {"ts": {"depcruise_config": "deps.cjs", "source": "lib"}})), "ts.deps")

    assert fake.calls == [["npx", "depcruise", "--config", "deps.cjs", "--output-type", "err", "lib"]]


def test_unscoped_failure_lists_nonblank_lines(tmp_path: Path, fake_run: Any) -> None:
    fake_run(ts_deps, [(1, REPORT)])

    result = checked(ts_deps.run_gate(make_context(tmp_path, TS)), "ts.deps")

    assert (result.ok, result.summary) == (False, "dependency rules broken")
    assert result.findings == [line for line in REPORT.splitlines() if line.strip()]


def test_unscoped_failure_is_capped(tmp_path: Path, fake_run: Any) -> None:
    fake_run(ts_deps, [(1, "\n".join(f"line {n}" for n in range(70)))])

    assert checked(ts_deps.run_gate(make_context(tmp_path, TS)), "ts.deps").findings == [f"line {n}" for n in range(60)]


def test_scoped_run_keeps_violations_in_scope(tmp_path: Path, fake_run: Any) -> None:
    fake_run(ts_deps, [(1, REPORT)])

    result = checked(ts_deps.run_gate(make_context(tmp_path, TS, scope_changed=True, changed={"web/src/a.ts"})), "ts.deps")

    assert (result.ok, result.summary, result.findings) == (
        False,
        "dependency rules broken",
        ["error no-circular: src/a.ts → src/b.ts → src/a.ts"],
    )


def test_scoped_run_passes_when_violations_are_elsewhere(tmp_path: Path, fake_run: Any) -> None:
    fake_run(ts_deps, [(1, REPORT)])

    result = checked(ts_deps.run_gate(make_context(tmp_path, TS, scope_changed=True, changed={"web/src/z.ts"})), "ts.deps")

    assert (result.ok, result.summary, result.findings) == (True, "dependency rules kept", [])


@pytest.mark.parametrize(("code", "expected"), [(0, []), (2, ["config broke", "badly"])])
def test_scoped_run_without_violations(tmp_path: Path, fake_run: Any, code: int, expected: list[str]) -> None:
    fake_run(ts_deps, [(code, "  config broke  \n\n badly\n")])

    result = checked(ts_deps.run_gate(make_context(tmp_path, TS, scope_changed=True)), "ts.deps")

    assert (result.ok, result.findings) == (not expected, expected)


def test_scoped_violations_are_capped(tmp_path: Path) -> None:
    lines = [f"error rule: src/f{n}.ts" for n in range(70)]
    ctx = make_context(tmp_path, TS, scope_changed=True, focus={"web"})

    assert ts_deps.scoped_findings("\n".join(lines), ctx, 1) == lines[:60]


@pytest.mark.parametrize(("line", "expected"), [("hint r: src/a.ts", True), ("hint r: src/b.ts", False), ("nonsense", False)])
def test_in_scope_violation(tmp_path: Path, line: str, expected: bool) -> None:
    ctx = make_context(tmp_path, TS, scope_changed=True, changed={"web/src/a.ts"})

    assert ts_deps.in_scope_violation(line, ctx) is expected
