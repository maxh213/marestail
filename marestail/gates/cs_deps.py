import fnmatch
import json
import time
from collections.abc import Iterator
from typing import Any

from marestail import dotnet
from marestail.context import Context
from marestail.report import Result

GATE = "cs.deps"
LAYERS_FILE = ".dotnet-layers.json"
MAX_LINES = 60


def run_gate(ctx: Context) -> Result:
    started = time.time()
    contract = ctx.root / LAYERS_FILE
    if not contract.exists():
        return Result(
            GATE, False, f"no {LAYERS_FILE}; copy templates/dotnet-layers.json and name the layers", [f"{LAYERS_FILE}:1 missing"], 0.0
        )
    layers = json.loads(contract.read_text())["layers"]
    files = dotnet.sources(ctx)
    if not files:
        return Result.skipped(GATE, "no C# sources")
    data, error = dotnet.scan(ctx, "deps", files)
    if error:
        return Result(GATE, False, error, [], time.time() - started)
    findings = scoped(ctx, layer_findings(ctx, layers, data) + cycle_findings(data["edges"]))
    return Result(GATE, not findings, summary(findings), findings[:MAX_LINES], time.time() - started)


def scoped(ctx: Context, findings: list[str]) -> list[str]:
    if not ctx.scoped:
        return findings
    return [f for f in findings if ctx.in_scope(f.split(":", 1)[0])]


def summary(findings: list[str]) -> str:
    return f"{len(findings)} layer breaks" if findings else "layer contracts kept"


def layer_findings(ctx: Context, layers: list[dict[str, Any]], data: dict[str, Any]) -> list[str]:
    prefix = dotnet.root_prefix(ctx)
    return edge_findings(prefix, layers, data["edges"]) + using_findings(prefix, layers, data["files"])


def governing(prefix: str, layers: list[dict[str, Any]], path: str) -> list[dict[str, Any]]:
    return [layer for layer in layers if under(path, prefix + layer["from"])]


def edge_findings(prefix: str, layers: list[dict[str, Any]], edges: list[dict[str, Any]]) -> list[str]:
    return [found for edge in edges for layer in governing(prefix, layers, edge["from"]) for found in edge_breaks(prefix, layer, edge)]


def edge_breaks(prefix: str, layer: dict[str, Any], edge: dict[str, Any]) -> list[str]:
    return [
        f"{edge['from']}:{edge['line']} {layer['from']} must not depend on {ban} ({edge['symbol']})"
        for ban in layer.get("forbid", [])
        if under(edge["to"], prefix + ban)
    ]


def using_findings(prefix: str, layers: list[dict[str, Any]], records: list[dict[str, Any]]) -> list[str]:
    return [found for record in records for layer in governing(prefix, layers, record["path"]) for found in using_breaks(layer, record)]


def using_breaks(layer: dict[str, Any], record: dict[str, Any]) -> list[str]:
    return [
        f"{record['path']}:{using['line']} {layer['from']} must not depend on {using['name']} (matches {pattern})"
        for using in record["usings"]
        for pattern in layer.get("forbid_external", [])
        if fnmatch.fnmatch(using["name"], pattern)
    ]


def under(path: str, folder: str) -> bool:
    folder = folder.rstrip("/")
    return path == folder or path.startswith(folder + "/")


def cycle_findings(edges: list[dict[str, Any]]) -> list[str]:
    return [cycle_finding(edges, component) for component in strongly_connected(dependency_graph(edges))]


def dependency_graph(edges: list[dict[str, Any]]) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {}
    for edge in edges:
        graph.setdefault(edge["from"], set()).add(edge["to"])
        graph.setdefault(edge["to"], set())
    return graph


def cycle_finding(edges: list[dict[str, Any]], component: set[str]) -> str:
    first = min(component)
    line = min(edge["line"] for edge in edges if edge["from"] == first and edge["to"] in component)
    return f"{first}:{line} dependency cycle: {' <-> '.join(sorted(component))}"


class Tarjan:
    def __init__(self, graph: dict[str, set[str]]) -> None:
        self.graph = graph
        self.index: dict[str, int] = {}
        self.low: dict[str, int] = {}
        self.stack: list[str] = []
        self.work: list[tuple[str, Iterator[str]]] = []
        self.components: list[set[str]] = []

    def collect(self) -> list[set[str]]:
        for start in sorted(self.graph):
            if start not in self.index:
                self.visit(start)
                self.drain()
        return self.components

    def visit(self, node: str) -> None:
        self.index[node] = self.low[node] = len(self.index)
        self.stack.append(node)
        self.work.append((node, iter(sorted(self.graph[node]))))

    def drain(self) -> None:
        while self.work:
            node, children = self.work[-1]
            child = next(children, None)
            if child is None:
                self.finish(node)
            else:
                self.step(node, child)

    def step(self, node: str, child: str) -> None:
        if child not in self.index:
            self.visit(child)
        elif child in self.stack:
            self.low[node] = min(self.low[node], self.index[child])

    def finish(self, node: str) -> None:
        self.work.pop()
        if self.work:
            parent = self.work[-1][0]
            self.low[parent] = min(self.low[parent], self.low[node])
        if self.low[node] == self.index[node]:
            self.close(node)

    def close(self, node: str) -> None:
        at = self.stack.index(node)
        component = set(self.stack[at:])
        del self.stack[at:]
        if len(component) > 1:
            self.components.append(component)


def strongly_connected(graph: dict[str, set[str]]) -> list[set[str]]:
    return Tarjan(graph).collect()
