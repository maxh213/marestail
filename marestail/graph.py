import json
import tomllib
from pathlib import Path

from marestail.config import Config
from marestail.shell import run

PYTHON_GRAPH = """
import grimp, sys
graph = grimp.build_graph(*sys.argv[1:])
for module in sorted(graph.modules):
    imports = sorted(graph.find_modules_directly_imported_by(module))
    if imports:
        print(module + " -> " + ", ".join(imports))
"""


def render(config: Config) -> str:
    return "\n\n".join(part for part in [python_graph(config), ts_graph(config), elixir_graph(config), ruby_graph(config), gleam_graph(config)] if part)


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
    data = tomllib.loads(path.read_text())
    return data.get("tool", {}).get("importlinter", {}).get("root_packages", [])


def ts_graph(config: Config) -> str:
    if config.section("ts") is None:
        return ""
    ts_root = config.root / config.get("ts", "root", ".")
    command = ["npx", "depcruise", "--config", config.get("ts", "depcruise_config", ".dependency-cruiser.cjs"), "--output-type", "text", config.get("ts", "source", "src")]
    _, output = run(command, cwd=ts_root)
    lines = [line for line in output.splitlines() if line.strip() and not line.startswith("npm notice")]
    return "## TypeScript modules\n" + "\n".join(lines)


def ruby_graph(config: Config) -> str:
    if config.section("ruby") is None:
        return ""
    from marestail.context import Context
    from marestail.gates.rb_crap import ruby_sources
    from marestail.ruby import scan

    ctx = Context(config=config)
    files = ruby_sources(ctx)
    if not files:
        return ""
    _, output = scan(ctx, "deps", files, extra=[str(config.root)])
    try:
        edges = json.loads(output or "[]")
    except json.JSONDecodeError:
        return "## Ruby modules\n" + output.strip()
    lines = [f"{e.get('from')} -> {e.get('to')} ({e.get('constant')})" for e in edges]
    return "## Ruby modules\n" + "\n".join(lines)


def elixir_graph(config: Config) -> str:
    if config.section("elixir") is None:
        return ""
    root = config.root / config.get("elixir", "root", ".")
    _, output = run(["mix", "xref", "graph"], cwd=root)
    lines = [line for line in output.splitlines() if line.strip() and not line.startswith("==>")]
    return "## Elixir modules\n" + "\n".join(lines)


def gleam_graph(config: Config) -> str:
    if config.section("gleam") is None:
        return ""
    from marestail.context import Context
    from marestail.gleam import gleam_sources, scan

    ctx = Context(config=config)
    files = gleam_sources(ctx)
    if not files:
        return ""
    _, output = scan(ctx, "deps", files)
    try:
        edges = json.loads(output or "[]")
    except json.JSONDecodeError:
        return "## Gleam modules\n" + output.strip()
    lines = [f"{e.get('from')} -> {e.get('to')}" for e in edges]
    return "## Gleam modules\n" + "\n".join(lines)
