import json
import tomllib
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from marestail.config import Config
from marestail.context import Context
from marestail.shell import run

PYTHON_GRAPH = """
import grimp, sys
graph = grimp.build_graph(*sys.argv[1:])
for module in sorted(graph.modules):
    imports = sorted(graph.find_modules_directly_imported_by(module))
    if imports:
        print(module + " -> " + ", ".join(imports))
"""
DEPS = "deps"
EDGES = "edges"
SYMBOL = "symbol"
ERLANG_TAIL = 300

Sources = Callable[[Context], list[Path]]
Body = Callable[[Context, list[Path]], str]


def render(config: Config) -> str:
    return "\n\n".join(
        part
        for part in [
            python_graph(config),
            ts_graph(config),
            elixir_graph(config),
            erlang_graph(config),
            ruby_graph(config),
            dotnet_graph(config),
            rust_graph(config),
            java_graph(config),
        ]
        if part
    )


def python_graph(config: Config) -> str:
    if config.section("python") is None:
        return ""
    root = config.root / config.get("python", "root", ".")
    packages = root_packages(config.root)
    python = config.root / config.get("python", "venv", ".venv") / "bin" / "python"
    _, output = run([str(python), "-c", PYTHON_GRAPH, *packages], cwd=root, env={"PYTHONPATH": str(root)})
    return "## Python modules\n" + output.strip()


def root_packages(root: Path) -> list[str]:
    path = root / "pyproject.toml"
    if not path.exists():
        return []
    packages: list[str] = tomllib.loads(path.read_text()).get("tool", {}).get("importlinter", {}).get("root_packages", [])
    return packages


def ts_graph(config: Config) -> str:
    if config.section("ts") is None:
        return ""
    ts_root = config.root / config.get("ts", "root", ".")
    command = [
        "npx",
        "depcruise",
        "--config",
        config.get("ts", "depcruise_config", ".dependency-cruiser.cjs"),
        "--output-type",
        "text",
        config.get("ts", "source", "src"),
    ]
    _, output = run(command, cwd=ts_root)
    return "## TypeScript modules\n" + kept_lines(output, "npm notice")


def elixir_graph(config: Config) -> str:
    if config.section("elixir") is None:
        return ""
    root = config.root / config.get("elixir", "root", ".")
    _, output = run(["mix", "xref", "graph"], cwd=root)
    return "## Elixir modules\n" + kept_lines(output, "==>")


def kept_lines(output: str, noise: str) -> str:
    return "\n".join(line for line in output.splitlines() if line.strip() and not line.startswith(noise))


def scanned_graph(config: Config, section: str, heading: str, sources: Sources, body: Body) -> str:
    if config.section(section) is None:
        return ""
    ctx = Context(config=config)
    files = sources(ctx)
    return f"## {heading} modules\n{body(ctx, files)}" if files else ""


def edge_lines(edges: Iterable[dict[str, Any]], label: str) -> str:
    return "\n".join(f"{edge['from']} -> {edge['to']} ({edge[label]})" for edge in edges)


def ruby_graph(config: Config) -> str:
    from marestail.ruby import sources

    return scanned_graph(config, "ruby", "Ruby", sources, ruby_body)


def ruby_body(ctx: Context, files: list[Path]) -> str:
    from marestail.ruby import scan

    _, output = scan(ctx, DEPS, files, extra=[str(ctx.config.root)])
    try:
        edges = json.loads(output or "[]")
    except json.JSONDecodeError:
        return output.strip()
    return "\n".join(f"{edge.get('from')} -> {edge.get('to')} ({edge.get('constant')})" for edge in edges)


def dotnet_graph(config: Config) -> str:
    from marestail import dotnet

    return scanned_graph(config, "dotnet", "C#", dotnet.sources, dotnet_body)


def dotnet_body(ctx: Context, files: list[Path]) -> str:
    from marestail import dotnet

    data, error = dotnet.scan(ctx, DEPS, files)
    return error or deps_lines(data)


def deps_lines(data: Any) -> str:
    return edge_lines(data[EDGES], SYMBOL)


def rust_graph(config: Config) -> str:
    from marestail import rust

    return scanned_graph(config, "rust", "Rust", rust.sources, rust_body)


def rust_body(ctx: Context, files: list[Path]) -> str:
    from marestail import rust

    edges, error = rust.scan(ctx, DEPS, files, extra=["--root", str(ctx.rust_root())])
    return error or rust_lines(ctx, edges)


def rust_lines(ctx: Context, edges: Any) -> str:
    from marestail import rust

    return "\n".join(f"{rust.rel(ctx, edge['from'])} -> {rust.rel(ctx, edge['to'])} ({edge[SYMBOL]})" for edge in edges)


def java_graph(config: Config) -> str:
    from marestail import java

    return scanned_graph(config, "java", "Java", java.sources, java_body)


def java_body(ctx: Context, files: list[Path]) -> str:
    from marestail import java

    data, error = java.scan(ctx, DEPS, files)
    return error or deps_lines(data)


def erlang_graph(config: Config) -> str:
    from marestail import erlang

    return scanned_graph(config, "erlang", "Erlang", erlang.source_files, erlang_body)


def erlang_body(ctx: Context, files: list[Path]) -> str:
    from marestail import erlang

    ebin = erlang.fresh_dir(ctx.work / "er-graph-ebin")
    code, output = erlang.erlc(ctx, ["+debug_info", "-o", str(ebin), *map(str, files)])
    if code != 0:
        return output.strip()[-ERLANG_TAIL:]
    code, output = erlang.escript(ctx, "deps.escript", sorted(str(beam) for beam in ebin.glob("*.beam")))
    return output.strip()[-ERLANG_TAIL:] if code != 0 else edge_lines(json.loads(output), "fun")
