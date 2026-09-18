import json
import time
from typing import Any

from marestail import rust
from marestail.context import Context
from marestail.report import Result

GATE = "rs.deps"

Edge = dict[str, Any]


def run_gate(ctx: Context) -> Result:
    started = time.time()
    files = rust.sources(ctx)
    if not files:
        return Result.skipped(GATE, "no rust sources")
    edges, error = rust.scan(ctx, "deps", files, extra=["--root", str(ctx.rust_root())])
    if error:
        return Result(GATE, False, "dependency scanner failed", [error], time.time() - started)
    relative = relative_edges(ctx, edges)
    findings = layer_findings(ctx, relative) + cycle_findings(ctx, relative)
    summary = f"{len(findings)} dependency breaks" if findings else "layer contracts kept, no module cycles"
    return Result(GATE, not findings, summary, findings[:60], time.time() - started)


def relative_edges(ctx: Context, edges: list[Edge] | None) -> list[Edge]:
    return [{**edge, "from": rust.rel(ctx, edge["from"]), "to": rust.rel(ctx, edge["to"])} for edge in edges or []]


def layer_findings(ctx: Context, edges: list[Edge]) -> list[str]:
    return [finding for edge in edges if ctx.in_scope(edge["from"]) for finding in edge_breaks(ctx, edge)]


def edge_breaks(ctx: Context, edge: Edge) -> list[str]:
    return [layer_message(edge) for layer in load_layers(ctx) if breaks(edge, layer)]


def breaks(edge: Edge, layer: dict[str, Any]) -> bool:
    return under(edge["from"], layer["from"]) and any(under(edge["to"], ban) for ban in layer["forbid"])


def layer_message(edge: Edge) -> str:
    return f"{edge['from']}:{edge['line']} {edge['from']} must not depend on {edge['to']} ({edge['symbol']})"


def under(path: str, prefix: str) -> bool:
    prefix = prefix.rstrip("/")
    return path == prefix or path.startswith(prefix + "/") or path.startswith(prefix + ".")


def load_layers(ctx: Context) -> list[dict[str, Any]]:
    configured: list[dict[str, Any]] | None = ctx.rust("layers")
    if configured:
        return configured
    path = ctx.root / ctx.rust("layers_file", ".rust-layers.json")
    layers: list[dict[str, Any]] = json.loads(path.read_text()) if path.exists() else []
    return layers


def edge_graph(edges: list[Edge]) -> dict[str, list[Edge]]:
    graph: dict[str, list[Edge]] = {}
    for edge in edges:
        graph.setdefault(edge["from"], []).append(edge)
    return graph


def cycle_findings(ctx: Context, edges: list[Edge]) -> list[str]:
    graph = edge_graph(edges)
    return [cycle_message(graph, component) for component in components(graph) if touches_scope(ctx, component)]


def touches_scope(ctx: Context, component: list[str]) -> bool:
    return any(ctx.in_scope(path) for path in component)


def cycle_message(graph: dict[str, list[Edge]], component: list[str]) -> str:
    first = min(component)
    edge = next(e for e in graph[first] if e["to"] in component)
    return f"{first}:{edge['line']} module cycle: {' -> '.join([*sorted(component), first])}"


class Tarjan:
    def __init__(self, graph: dict[str, list[Edge]]) -> None:
        self.graph = graph
        self.index: dict[str, int] = {}
        self.low: dict[str, int] = {}
        self.stack: list[str] = []
        self.found: list[list[str]] = []

    def visit(self, node: str) -> None:
        self.index[node] = self.low[node] = len(self.index)
        self.stack.append(node)
        for edge in self.graph.get(node, []):
            self.follow(node, edge["to"])
        if self.low[node] == self.index[node]:
            self.close(node)

    def follow(self, node: str, target: str) -> None:
        if target not in self.index:
            self.visit(target)
            self.low[node] = min(self.low[node], self.low[target])
        elif target in self.stack:
            self.low[node] = min(self.low[node], self.index[target])

    def close(self, node: str) -> None:
        position = self.stack.index(node)
        component = self.stack[position:][::-1]
        del self.stack[position:]
        if len(component) > 1:
            self.found.append(component)


def components(graph: dict[str, list[Edge]]) -> list[list[str]]:
    search = Tarjan(graph)
    for node in sorted(graph):
        if node not in search.index:
            search.visit(node)
    return search.found
