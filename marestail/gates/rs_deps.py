import json
import time

from marestail import rust
from marestail.context import Context
from marestail.report import Result


def run_gate(ctx: Context) -> Result:
    started = time.time()
    files = rust.sources(ctx)
    if not files:
        return Result.skipped("rs.deps", "no rust sources")
    edges, error = rust.scan(ctx, "deps", files, extra=["--root", str(ctx.rust_root())])
    if error:
        return Result("rs.deps", False, "dependency scanner failed", [error], time.time() - started)
    edges = [{**edge, "from": rust.rel(ctx, edge["from"]), "to": rust.rel(ctx, edge["to"])} for edge in edges]
    findings = layer_findings(ctx, edges) + cycle_findings(ctx, edges)
    summary = "layer contracts kept, no module cycles" if not findings else f"{len(findings)} dependency breaks"
    return Result("rs.deps", not findings, summary, findings[:60], time.time() - started)


def layer_findings(ctx: Context, edges: list[dict]) -> list[str]:
    findings = []
    for edge in edges:
        if ctx.scope_changed and edge["from"] not in ctx.changed:
            continue
        for layer in load_layers(ctx):
            if under(edge["from"], layer["from"]) and any(under(edge["to"], ban) for ban in layer["forbid"]):
                findings.append(f"{edge['from']}:{edge['line']} {edge['from']} must not depend on {edge['to']} ({edge['symbol']})")
    return findings


def under(path: str, prefix: str) -> bool:
    prefix = prefix.rstrip("/")
    return path == prefix or path.startswith(prefix + "/") or path.startswith(prefix + ".")


def load_layers(ctx: Context) -> list[dict]:
    configured = ctx.rust("layers")
    if configured:
        return configured
    path = ctx.root / ctx.rust("layers_file", ".rust-layers.json")
    return json.loads(path.read_text()) if path.exists() else []


def cycle_findings(ctx: Context, edges: list[dict]) -> list[str]:
    graph: dict[str, list[dict]] = {}
    for edge in edges:
        graph.setdefault(edge["from"], []).append(edge)
    findings = []
    for component in components(graph):
        if ctx.scope_changed and not set(component) & ctx.changed:
            continue
        first = min(component)
        edge = next(e for e in graph[first] if e["to"] in component)
        findings.append(f"{first}:{edge['line']} module cycle: {' -> '.join([*sorted(component), first])}")
    return findings


def components(graph: dict[str, list[dict]]) -> list[list[str]]:
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    found: list[list[str]] = []

    def visit(node: str) -> None:
        index[node] = low[node] = len(index)
        stack.append(node)
        for edge in graph.get(node, []):
            target = edge["to"]
            if target not in index:
                visit(target)
                low[node] = min(low[node], low[target])
            elif target in stack:
                low[node] = min(low[node], index[target])
        if low[node] == index[node]:
            component = []
            while True:
                member = stack.pop()
                component.append(member)
                if member == node:
                    break
            if len(component) > 1:
                found.append(component)

    for node in sorted(graph):
        if node not in index:
            visit(node)
    return found
