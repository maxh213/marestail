from collections.abc import Iterator
from typing import Any


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
        for _ in range(len(self.graph) * len(self.graph) + 1):
            if not self.work:
                break
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
