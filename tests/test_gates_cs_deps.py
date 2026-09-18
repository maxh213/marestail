import json
from pathlib import Path
from typing import Any

import pytest

from marestail import dotnet
from marestail.gates import cs_deps
from marestail.report import Result
from tests.conftest import make_context

LAYERS = {
    "layers": [
        {"from": "Domain", "forbid": ["Web", "Data"], "forbid_external": ["Microsoft.AspNetCore*"]},
        {"from": "Data/", "forbid": ["Web"]},
    ]
}


def edge(source: str, target: str, line: int, symbol: str = "Thing") -> dict[str, Any]:
    return {"from": source, "to": target, "line": line, "symbol": symbol}


DATA = {
    "edges": [
        edge("cs/Domain/Order.cs", "cs/Web/Api.cs", 4, "Api"),
        edge("cs/Domain/Order.cs", "cs/Data/Repo.cs", 5, "Repo"),
        edge("cs/Data/Repo.cs", "cs/Domain/Order.cs", 9, "Order"),
        edge("cs/Web/Api.cs", "cs/Domain/Order.cs", 2, "Order"),
    ],
    "files": [
        {"path": "cs/Domain/Order.cs", "usings": [{"name": "Microsoft.AspNetCore.Mvc", "line": 1}, {"name": "System", "line": 2}]},
        {"path": "cs/Web/Api.cs", "usings": [{"name": "Microsoft.AspNetCore.Mvc", "line": 1}]},
    ],
}

FINDINGS = [
    "cs/Domain/Order.cs:4 Domain must not depend on Web (Api)",
    "cs/Domain/Order.cs:5 Domain must not depend on Data (Repo)",
    "cs/Domain/Order.cs:1 Domain must not depend on Microsoft.AspNetCore.Mvc (matches Microsoft.AspNetCore*)",
    "cs/Data/Repo.cs:9 dependency cycle: cs/Data/Repo.cs <-> cs/Domain/Order.cs <-> cs/Web/Api.cs",
]


def view(result: Result) -> tuple[str, bool, str, list[str]]:
    return result.gate, result.ok, result.summary, result.findings


def project(root: Path, layers: dict[str, Any] | None = LAYERS, **fields: Any) -> Any:
    for name in ("cs/App.csproj", "cs/AppTests.csproj", "cs/Domain/Order.cs"):
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text("")
    if layers is not None:
        (root / cs_deps.LAYERS_FILE).write_text(json.dumps(layers))
    ctx = make_context(root, {"dotnet": {"root": "cs"}}, **fields)
    ctx.work.mkdir()
    return ctx


@pytest.fixture(autouse=True)
def fresh_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dotnet, "PROJECTS", {})


def install(monkeypatch: pytest.MonkeyPatch, reply: tuple[Any, str | None]) -> list[tuple[str, list[Path]]]:
    calls: list[tuple[str, list[Path]]] = []

    def scan(ctx: Any, mode: str, paths: list[Path]) -> tuple[Any, str | None]:
        calls.append((mode, paths))
        return reply

    monkeypatch.setattr(dotnet, "scan", scan)
    return calls


def test_needs_layers_file(tmp_path: Path) -> None:
    result = cs_deps.run_gate(project(tmp_path, None))
    summary = "no .dotnet-layers.json; copy templates/dotnet-layers.json and name the layers"
    assert view(result) == ("cs.deps", False, summary, [".dotnet-layers.json:1 missing"])
    assert result.seconds == 0.0


def test_skips_without_sources(tmp_path: Path) -> None:
    ctx = project(tmp_path)
    (tmp_path / "cs" / "Domain" / "Order.cs").unlink()
    assert view(cs_deps.run_gate(ctx)) == ("cs.deps", True, "skipped: no C# sources", [])


def test_reports_scan_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = install(monkeypatch, (None, "C# scanner failed (deps): x"))
    assert view(cs_deps.run_gate(project(tmp_path))) == ("cs.deps", False, "C# scanner failed (deps): x", [])
    assert calls == [("deps", [tmp_path / "cs" / "Domain" / "Order.cs"])]


def test_reports_layer_breaks_and_cycles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, (DATA, None))
    assert view(cs_deps.run_gate(project(tmp_path))) == ("cs.deps", False, "4 layer breaks", FINDINGS)


def test_clean_contract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, ({"edges": [], "files": []}, None))
    assert view(cs_deps.run_gate(project(tmp_path))) == ("cs.deps", True, "layer contracts kept", [])


def test_scoped_keeps_changed_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, (DATA, None))
    ctx = project(tmp_path, scope_changed=True, changed={"cs/Data/Repo.cs"})
    assert view(cs_deps.run_gate(ctx)) == ("cs.deps", False, "1 layer breaks", [FINDINGS[3]])


def test_caps_findings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    edges = [edge("cs/Domain/A.cs", "cs/Web/B.cs", line) for line in range(1, 71)]
    install(monkeypatch, ({"edges": edges, "files": []}, None))
    result = cs_deps.run_gate(project(tmp_path))
    assert result.summary == "70 layer breaks"
    assert len(result.findings) == 60
    assert result.findings[-1] == "cs/Domain/A.cs:60 Domain must not depend on Web (Thing)"


def test_layer_findings_at_repo_root(tmp_path: Path) -> None:
    data = {"edges": [edge("Domain/A.cs", "Web/B.cs", 3)], "files": [{"path": "Web/B.cs", "usings": [{"name": "X", "line": 1}]}]}
    assert cs_deps.layer_findings(make_context(tmp_path), LAYERS["layers"], data) == ["Domain/A.cs:3 Domain must not depend on Web (Thing)"]


@pytest.mark.parametrize(
    ("path", "folder", "expected"),
    [("a/b.cs", "a", True), ("a/b.cs", "a/", True), ("a", "a/", True), ("ab/c.cs", "a", False)],
)
def test_under(path: str, folder: str, expected: bool) -> None:
    assert cs_deps.under(path, folder) is expected


def test_cycle_findings() -> None:
    edges = [edge("a", "b", 3), edge("a", "b", 1), edge("b", "c", 2), edge("c", "a", 7), edge("d", "d", 1), edge("x", "y", 1)]
    assert cs_deps.cycle_findings(edges) == ["a:1 dependency cycle: a <-> b <-> c"]


def test_strongly_connected() -> None:
    graph = {"a": {"b"}, "b": {"a", "c"}, "c": {"d"}, "d": {"c", "e"}, "e": set(), "f": {"a"}}
    assert cs_deps.strongly_connected(graph) == [{"c", "d"}, {"a", "b"}]


def test_strongly_connected_nested_cycle() -> None:
    graph = {"a": {"b"}, "b": {"c"}, "c": {"a", "d"}, "d": {"b"}}
    assert cs_deps.strongly_connected(graph) == [{"a", "b", "c", "d"}]


def test_dependency_graph() -> None:
    assert cs_deps.dependency_graph([edge("a", "b", 1), edge("a", "c", 2)]) == {"a": {"b", "c"}, "b": set(), "c": set()}


@pytest.mark.parametrize(
    ("graph", "expected"),
    [
        ({}, []),
        ({"a": {"a"}}, []),
        ({"a": {"b"}, "b": set()}, []),
        ({"a": {"b"}, "b": {"a"}}, [{"a", "b"}]),
        ({"a": {"b"}, "b": {"c"}, "c": {"a", "d"}, "d": {"e"}, "e": {"d"}}, [{"d", "e"}, {"a", "b", "c"}]),
        ({"a": {"b", "c"}, "b": {"a"}, "c": {"d"}, "d": {"c", "b"}}, [{"a", "b", "c", "d"}]),
        ({"a": {"c"}, "b": {"c"}, "c": {"a"}, "x": {"y"}, "y": {"x", "a"}}, [{"a", "c"}, {"x", "y"}]),
        ({"a": {"b"}, "b": {"c"}, "c": {"b"}, "d": {"b"}}, [{"b", "c"}]),
    ],
)
def test_strongly_connected_cases(graph: dict[str, set[str]], expected: list[set[str]]) -> None:
    assert cs_deps.strongly_connected(graph) == expected
