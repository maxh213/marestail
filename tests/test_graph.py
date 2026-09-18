import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from marestail import dotnet, erlang, graph, java, ruby, rust
from marestail.config import Config
from marestail.context import Context
from marestail.gates import rb_crap
from tests.conftest import FakeRun, make_context

EDGES = [{"from": "A", "to": "B", "symbol": "s", "fun": "f/1"}, {"from": "B", "to": "C", "symbol": "t", "fun": "g/0"}]


def config(root: Path, raw: dict[str, Any] | None = None) -> Config:
    return make_context(root, raw).config


def sources(files: list[Path]) -> Callable[[Context], list[Path]]:
    return lambda ctx: files


def test_render_without_sections_is_empty(tmp_path: Path) -> None:
    assert graph.render(config(tmp_path)) == ""


def test_render_joins_present_graphs(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake_run(graph, lambda command: (0, "a -> b\n") if command[0] != "mix" else (0, "==> app\nlib/x.ex\n"))
    assert graph.render(config(tmp_path, {"python": {}, "elixir": {}})) == "## Python modules\na -> b\n\n## Elixir modules\nlib/x.ex"


def test_python_graph(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    (tmp_path / "pyproject.toml").write_text('[tool.importlinter]\nroot_packages = ["pkg", "other"]\n')
    fake = fake_run(graph, [(1, "  pkg.a -> pkg.b\n")])
    raw = {"python": {"root": "src", "venv": "env"}}
    assert graph.python_graph(config(tmp_path, raw)) == "## Python modules\npkg.a -> pkg.b"
    assert fake.calls == [[str(tmp_path / "env" / "bin" / "python"), "-c", graph.PYTHON_GRAPH, "pkg", "other"]]
    assert fake.options == [{"cwd": tmp_path / "src", "env": {"PYTHONPATH": str(tmp_path / "src")}}]


def test_python_graph_defaults(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake = fake_run(graph, [(0, "")])
    assert graph.python_graph(config(tmp_path, {"python": {}})) == "## Python modules\n"
    assert fake.calls == [[str(tmp_path / ".venv" / "bin" / "python"), "-c", graph.PYTHON_GRAPH]]
    assert fake.options[0]["cwd"] == tmp_path


@pytest.mark.parametrize(
    ("text", "expected"),
    [("", []), ("[project]\nname = 'x'\n", []), ("[tool.importlinter]\nroot_packages = ['a']\n", ["a"])],
)
def test_root_packages(tmp_path: Path, text: str, expected: list[str]) -> None:
    if text:
        (tmp_path / "pyproject.toml").write_text(text)
    assert graph.root_packages(tmp_path) == expected


def test_ts_graph(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake = fake_run(graph, [(0, "npm notice x\nsrc/a.ts -> src/b.ts\n  \nsrc/b.ts\n")])
    raw = {"ts": {"root": "web", "depcruise_config": "dc.cjs", "source": "lib"}}
    assert graph.ts_graph(config(tmp_path, raw)) == "## TypeScript modules\nsrc/a.ts -> src/b.ts\nsrc/b.ts"
    assert fake.calls == [["npx", "depcruise", "--config", "dc.cjs", "--output-type", "text", "lib"]]
    assert fake.options == [{"cwd": tmp_path / "web"}]


def test_ts_graph_defaults(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake = fake_run(graph, [(0, "")])
    assert graph.ts_graph(config(tmp_path, {"ts": {}})) == "## TypeScript modules\n"
    assert fake.calls == [["npx", "depcruise", "--config", ".dependency-cruiser.cjs", "--output-type", "text", "src"]]
    assert fake.options == [{"cwd": tmp_path}]


def test_elixir_graph(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake = fake_run(graph, [(0, "==> app\nlib/a.ex\n└── lib/b.ex\n")])
    assert graph.elixir_graph(config(tmp_path, {"elixir": {"root": "app"}})) == "## Elixir modules\nlib/a.ex\n└── lib/b.ex"
    assert fake.calls == [["mix", "xref", "graph"]]
    assert fake.options == [{"cwd": tmp_path / "app"}]


@pytest.mark.parametrize("name", ["python", "ts", "elixir", "ruby", "dotnet", "rust", "java", "erlang"])
def test_graphs_skip_missing_sections(tmp_path: Path, name: str) -> None:
    assert getattr(graph, f"{name}_graph")(config(tmp_path, {"other": {}})) == ""


def test_kept_lines() -> None:
    assert graph.kept_lines("keep\n\n  \nnoise here\n noise kept\n", "noise") == "keep\n noise kept"


def test_edge_lines() -> None:
    assert graph.edge_lines(EDGES, "symbol") == "A -> B (s)\nB -> C (t)"
    assert graph.edge_lines([], "symbol") == ""


def test_scanned_graph(tmp_path: Path) -> None:
    seen: list[tuple[Context, list[Path]]] = []

    def body(ctx: Context, files: list[Path]) -> str:
        seen.append((ctx, files))
        return "edges"

    cfg = config(tmp_path, {"lang": {}})
    assert graph.scanned_graph(cfg, "lang", "Lang", sources([tmp_path]), body) == "## Lang modules\nedges"
    assert seen[0][0].config == cfg
    assert seen[0][1] == [tmp_path]
    assert graph.scanned_graph(cfg, "lang", "Lang", sources([]), body) == ""
    assert graph.scanned_graph(cfg, "missing", "Lang", sources([tmp_path]), body) == ""
    assert len(seen) == 1


def test_ruby_graph(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, list[Path], list[str] | None]] = []

    def scan(ctx: Context, mode: str, files: list[Path], extra: list[str] | None = None) -> tuple[int, str]:
        calls.append((mode, files, extra))
        return 0, json.dumps([{"from": "A", "to": "B", "constant": "B"}, {"from": "C"}])

    monkeypatch.setattr(rb_crap, "ruby_sources", sources([tmp_path / "a.rb"]))
    monkeypatch.setattr(ruby, "scan", scan)
    assert graph.ruby_graph(config(tmp_path, {"ruby": {}})) == "## Ruby modules\nA -> B (B)\nC -> None (None)"
    assert calls == [("deps", [tmp_path / "a.rb"], [str(tmp_path)])]


@pytest.mark.parametrize(("output", "expected"), [("", "## Ruby modules\n"), (" not json \n", "## Ruby modules\nnot json")])
def test_ruby_graph_odd_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, output: str, expected: str) -> None:
    monkeypatch.setattr(rb_crap, "ruby_sources", sources([tmp_path / "a.rb"]))
    monkeypatch.setattr(ruby, "scan", lambda *args, **kwargs: (1, output))
    assert graph.ruby_graph(config(tmp_path, {"ruby": {}})) == expected


def test_ruby_graph_without_sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rb_crap, "ruby_sources", sources([]))
    assert graph.ruby_graph(config(tmp_path, {"ruby": {}})) == ""


@pytest.mark.parametrize(("module", "section", "heading"), [(dotnet, "dotnet", "C#"), (java, "java", "Java")])
def test_deps_graphs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, module: Any, section: str, heading: str) -> None:
    calls: list[tuple[str, list[Path]]] = []

    def scan(ctx: Context, mode: str, files: list[Path]) -> tuple[dict[str, Any], None]:
        calls.append((mode, files))
        return {"files": [], "edges": EDGES}, None

    monkeypatch.setattr(module, "sources", sources([tmp_path / "a"]))
    monkeypatch.setattr(module, "scan", scan)
    render = getattr(graph, f"{module.__name__.rsplit('.', 1)[1]}_graph")
    assert render(config(tmp_path, {section: {}})) == f"## {heading} modules\nA -> B (s)\nB -> C (t)"
    assert calls == [("deps", [tmp_path / "a"])]


@pytest.mark.parametrize(("module", "section", "heading"), [(dotnet, "dotnet", "C#"), (java, "java", "Java")])
def test_deps_graph_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, module: Any, section: str, heading: str) -> None:
    monkeypatch.setattr(module, "sources", sources([tmp_path / "a"]))
    monkeypatch.setattr(module, "scan", lambda ctx, mode, files: (None, "scanner broke"))
    render = getattr(graph, f"{module.__name__.rsplit('.', 1)[1]}_graph")
    assert render(config(tmp_path, {section: {}})) == f"## {heading} modules\nscanner broke"


def test_rust_graph(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, list[Path], list[str] | None]] = []

    def scan(ctx: Context, mode: str, files: list[Path], extra: list[str] | None = None) -> tuple[list[dict[str, str]], None]:
        calls.append((mode, files, extra))
        return [{"from": str(tmp_path / "src" / "a.rs"), "to": "src/b.rs", "symbol": "B"}], None

    monkeypatch.setattr(rust, "sources", sources([tmp_path / "a.rs"]))
    monkeypatch.setattr(rust, "scan", scan)
    cfg = config(tmp_path, {"rust": {}})
    assert graph.rust_graph(cfg) == "## Rust modules\nsrc/a.rs -> src/b.rs (B)"
    assert calls == [("deps", [tmp_path / "a.rs"], ["--root", str(Context(config=cfg).rust_root())])]


def test_rust_graph_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rust, "sources", sources([tmp_path / "a.rs"]))
    monkeypatch.setattr(rust, "scan", lambda *args, **kwargs: (None, "cargo broke"))
    assert graph.rust_graph(config(tmp_path, {"rust": {}})) == "## Rust modules\ncargo broke"


class FakeErlang:
    def __init__(self, erlc: tuple[int, str], escript: tuple[int, str]) -> None:
        self.replies = {"erlc": erlc, "escript": escript}
        self.calls: list[tuple[str, list[str]]] = []

    def erlc(self, ctx: Context, args: list[str]) -> tuple[int, str]:
        self.calls.append(("erlc", args))
        (Path(args[2]) / "b.beam").write_text("")
        (Path(args[2]) / "a.beam").write_text("")
        return self.replies["erlc"]

    def escript(self, ctx: Context, script: str, args: list[str]) -> tuple[int, str]:
        self.calls.append((script, args))
        return self.replies["escript"]


def fake_erlang(monkeypatch: pytest.MonkeyPatch, root: Path, erlc: tuple[int, str], escript: tuple[int, str]) -> FakeErlang:
    fake = FakeErlang(erlc, escript)
    monkeypatch.setattr(erlang, "source_files", sources([root / "a.erl", root / "b.erl"]))
    monkeypatch.setattr(erlang, "erlc", fake.erlc)
    monkeypatch.setattr(erlang, "escript", fake.escript)
    return fake


def test_erlang_graph(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = fake_erlang(monkeypatch, tmp_path, (0, ""), (0, json.dumps(EDGES)))
    ebin = tmp_path / ".marestail" / "er-graph-ebin"
    ebin.mkdir(parents=True)
    (ebin / "stale.beam").write_text("")
    assert graph.erlang_graph(config(tmp_path, {"erlang": {}})) == "## Erlang modules\nA -> B (f/1)\nB -> C (g/0)"
    assert fake.calls == [
        ("erlc", ["+debug_info", "-o", str(ebin), str(tmp_path / "a.erl"), str(tmp_path / "b.erl")]),
        ("deps.escript", [str(ebin / "a.beam"), str(ebin / "b.beam")]),
    ]


@pytest.mark.parametrize(
    ("erlc", "escript", "expected"),
    [((1, "  " + "x" * 400 + " \n"), (0, "[]"), "x" * 300), ((0, ""), (2, " " + "y" * 301 + "\n"), "y" * 300), ((0, ""), (0, "[]"), "")],
)
def test_erlang_graph_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, erlc: tuple[int, str], escript: tuple[int, str], expected: str
) -> None:
    fake_erlang(monkeypatch, tmp_path, erlc, escript)
    assert graph.erlang_graph(config(tmp_path, {"erlang": {}})) == "## Erlang modules\n" + expected


def test_erlang_graph_without_sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(erlang, "source_files", sources([]))
    assert graph.erlang_graph(config(tmp_path, {"erlang": {}})) == ""
