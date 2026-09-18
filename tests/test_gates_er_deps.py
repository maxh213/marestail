import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from marestail import erlang
from marestail.context import Context
from marestail.gates import er_deps
from marestail.report import Result
from tests.conftest import FakeRun, make_context

HINT = "erlang unavailable: install Erlang/OTP 25+ (erl, erlc, escript), or docker with `docker pull erlang:27`"
EDGES = [
    {"from": "b", "to": "a", "line": 7},
    {"from": "a", "to": "b", "line": 4},
    {"from": "a", "to": "b", "line": 2},
    {"from": "a", "to": "lists", "line": 1},
    {"from": "c", "to": "a", "line": 3},
    {"from": "ext", "to": "c", "line": 9},
]


def shape(result: Result) -> tuple[str, bool, str, list[str]]:
    return result.gate, result.ok, result.summary, result.findings


def project(root: Path, **fields: Any) -> Context:
    (root / "src").mkdir()
    for name in "abc":
        (root / "src" / f"{name}.erl").write_text("")
    (root / ".marestail").mkdir()
    return make_context(root, {"erlang": {"erlc": "erlc", "escript": "escript"}}, **fields)


def toolchain(edges: list[dict[str, Any]], scanner: tuple[int, str] | None = None) -> Callable[[list[str]], tuple[int, str]]:
    def reply(command: list[str]) -> tuple[int, str]:
        if command[0] == "erlc":
            ebin = Path(command[command.index("-o") + 1])
            for source in command[command.index("-o") + 2 :]:
                (ebin / f"{Path(source).stem}.beam").write_text("")
            return 0, ""
        return scanner or (0, json.dumps(edges))

    return reply


def test_skips_without_sources(tmp_path: Path) -> None:
    result = er_deps.run_gate(make_context(tmp_path))
    assert shape(result) == ("er.deps", True, "skipped: no erlang sources under [erlang] sources (default src/)", [])


@pytest.mark.parametrize(
    ("reply", "summary", "findings"),
    [
        ((127, "erlc: not found (x)"), HINT, [HINT]),
        ((1, "src/a.erl:1: syntax error\n\n"), "sources failed to compile", ["src/a.erl:1: syntax error"]),
    ],
)
def test_compile_failures(
    tmp_path: Path, fake_run: Callable[..., FakeRun], reply: tuple[int, str], summary: str, findings: list[str]
) -> None:
    fake_run(erlang, [reply])
    assert shape(er_deps.run_gate(project(tmp_path))) == ("er.deps", False, summary, findings)


def test_scanner_failure(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    output = "\n".join(f"l{n}" for n in range(15))
    fake_run(erlang, toolchain([], (2, output)))
    result = er_deps.run_gate(project(tmp_path))
    assert shape(result) == ("er.deps", False, "dependency scanner failed", [f"l{n}" for n in range(5, 15)])


def test_reports_cycles(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake = fake_run(erlang, toolchain(EDGES))
    result = er_deps.run_gate(project(tmp_path))
    assert shape(result) == ("er.deps", False, "1 dependency cycles", ["src/a.erl:2 dependency cycle: src/a.erl <-> src/b.erl"])
    ebin = tmp_path / ".marestail" / "er-deps-ebin"
    sources = [str(tmp_path / "src" / f"{name}.erl") for name in "abc"]
    beams = [str(ebin / f"{name}.beam") for name in "abc"]
    assert fake.calls == [["erlc", "+debug_info", "-o", str(ebin), *sources], ["escript", str(erlang.SCRIPT_DIR / "deps.escript"), *beams]]
    assert [options["timeout"] for options in fake.options] == [900, 600]


def test_acyclic(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake_run(erlang, toolchain([{"from": "a", "to": "b", "line": 1}]))
    assert shape(er_deps.run_gate(project(tmp_path))) == ("er.deps", True, "dependency graph acyclic", [])


@pytest.mark.parametrize(("changed", "ok"), [({"src/a.erl"}, False), ({"src/b.erl"}, True)])
def test_scoped_to_cycle_owner(tmp_path: Path, fake_run: Callable[..., FakeRun], changed: set[str], ok: bool) -> None:
    fake_run(erlang, toolchain(EDGES))
    assert er_deps.run_gate(project(tmp_path, scope_changed=True, changed=changed)).ok is ok


def test_findings_are_capped(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    edges = [
        edge
        for n in range(70)
        for edge in ({"from": f"m{n:02}", "to": f"n{n:02}", "line": 1}, {"from": f"n{n:02}", "to": f"m{n:02}", "line": 2})
    ]
    result = er_deps.cycles_result(ctx, edges, 0.0)
    assert (result.summary, len(result.findings), result.findings[0]) == ("70 dependency cycles", 60, "m00:1 dependency cycle: m00 <-> n00")


def test_project_edges_drop_external_modules() -> None:
    modules = {"a": "src/a.erl", "b": "src/b.erl"}
    assert er_deps.project_edges(EDGES, modules) == [
        {"from": "src/b.erl", "to": "src/a.erl", "line": 7},
        {"from": "src/a.erl", "to": "src/b.erl", "line": 4},
        {"from": "src/a.erl", "to": "src/b.erl", "line": 2},
    ]


def test_cycle_findings_pick_first_module_line() -> None:
    edges = [
        {"from": "z", "to": "y", "line": 1},
        {"from": "y", "to": "x", "line": 5},
        {"from": "x", "to": "z", "line": 8},
        {"from": "x", "to": "w", "line": 3},
        {"from": "x", "to": "y", "line": 6},
    ]
    assert er_deps.cycle_findings(edges) == ["x:6 dependency cycle: x <-> y <-> z"]
