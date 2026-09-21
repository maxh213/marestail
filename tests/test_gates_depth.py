from pathlib import Path

import pytest

from marestail import depth
from marestail.config import Config
from marestail.depth import Module
from marestail.gates import depth as depth_gate
from tests.conftest import checked, make_context


def module(path: str, public: int = 1, statements: int = 10, breaks: int = 0) -> Module:
    return Module(
        path, [f"p{n}" for n in range(public)], statements, [f"{path} forwards {n}" for n in range(breaks)], [f"{path} private"] * breaks
    )


def fake_analyse(monkeypatch: pytest.MonkeyPatch, modules: list[Module]) -> list[Config]:
    seen: list[Config] = []

    def analyse(config: Config) -> list[Module]:
        seen.append(config)
        return modules

    monkeypatch.setattr(depth, "analyse", analyse)
    return seen


def test_no_modules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_analyse(monkeypatch, [])
    result = checked(depth_gate.run_gate(make_context(tmp_path)), "depth")
    assert (result.gate, result.ok, result.summary, result.findings) == ("depth", True, "no modules", [])


def test_counts_and_findings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = make_context(tmp_path)
    seen = fake_analyse(monkeypatch, [module("a.py", public=4, breaks=1), module("b.py"), module("c.py", public=5, statements=10)])
    result = checked(depth_gate.run_gate(ctx), "depth")
    assert seen == [ctx.config]
    assert (result.ok, result.summary) == (False, "3 modules, 2 shallow, 2 rule breaks")
    assert result.findings == ["a.py forwards 0", "a.py private"]


def test_scoped_filters_modules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_analyse(monkeypatch, [module("a.py", breaks=1), module("b.py", public=4)])
    result = checked(depth_gate.run_gate(make_context(tmp_path, scope_changed=True, changed={"b.py"})), "depth")
    assert (result.ok, result.summary, result.findings) == (True, "1 modules, 1 shallow, 0 rule breaks", [])


def test_summary() -> None:
    assert depth_gate.summary([], []) == "no modules"
    assert depth_gate.summary([module("a"), module("b", public=4)], ["x"]) == "2 modules, 1 shallow, 1 rule breaks"
