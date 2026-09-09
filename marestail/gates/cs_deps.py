import fnmatch
import json
import time
from pathlib import Path

from marestail import dotnet
from marestail.context import Context
from marestail.report import Result

LAYERS_FILE = ".dotnet-layers.json"
MAX_LINES = 60


def run_gate(ctx: Context) -> Result:
    started = time.time()
    contract = ctx.root / LAYERS_FILE
    if not contract.exists():
        return Result("cs.deps", False, f"no {LAYERS_FILE}; copy templates/dotnet-layers.json and name the layers", [f"{LAYERS_FILE}:1 missing"], 0.0)
    layers = json.loads(contract.read_text())["layers"]
    files = dotnet.sources(ctx)
    if not files:
        return Result.skipped("cs.deps", "no C# sources")
    data, error = dotnet.scan(ctx, "deps", files)
    if error:
        return Result("cs.deps", False, error, [], time.time() - started)
    findings = layer_findings(ctx, layers, data) + cycle_findings(data["edges"])
    if ctx.scope_changed:
        findings = [f for f in findings if f.split(":")[0] in ctx.changed]
    summary = "layer contracts kept" if not findings else f"{len(findings)} layer breaks"
    return Result("cs.deps", not findings, summary, findings[:MAX_LINES], time.time() - started)


def layer_findings(ctx: Context, layers: list[dict], data: dict) -> list[str]:
    prefix = dotnet.rel(ctx, ctx.dotnet_root())
    prefix = "" if prefix == "." else prefix + "/"
    findings = []
    for edge in data["edges"]:
        for layer in layers:
            if not under(edge["from"], prefix + layer["from"]):
                continue
            for ban in layer.get("forbid", []):
                if under(edge["to"], prefix + ban):
                    findings.append(f"{edge['from']}:{edge['line']} {layer['from']} must not depend on {ban} ({edge['symbol']})")
    for record in data["files"]:
        for layer in layers:
            if not under(record["path"], prefix + layer["from"]):
                continue
            for using in record["usings"]:
                for pattern in layer.get("forbid_external", []):
                    if fnmatch.fnmatch(using["name"], pattern):
                        findings.append(f"{record['path']}:{using['line']} {layer['from']} must not depend on {using['name']} (matches {pattern})")
    return findings


def under(path: str, folder: str) -> bool:
    folder = folder.rstrip("/")
    return path == folder or path.startswith(folder + "/")


def cycle_findings(edges: list[dict]) -> list[str]:
    graph: dict[str, set[str]] = {}
    for edge in edges:
        graph.setdefault(edge["from"], set()).add(edge["to"])
        graph.setdefault(edge["to"], set())
    findings = []
    for component in strongly_connected(graph):
        first = sorted(component)[0]
        line = min(edge["line"] for edge in edges if edge["from"] == first and edge["to"] in component)
        findings.append(f"{first}:{line} dependency cycle: {' <-> '.join(sorted(component))}")
    return findings


def strongly_connected(graph: dict[str, set[str]]) -> list[set[str]]:
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    components = []
    for start in sorted(graph):
        if start in index:
            continue
        work = [(start, iter(sorted(graph[start])))]
        index[start] = low[start] = len(index)
        stack.append(start)
        while work:
            node, children = work[-1]
            child = next(children, None)
            if child is not None:
                if child not in index:
                    index[child] = low[child] = len(index)
                    stack.append(child)
                    work.append((child, iter(sorted(graph[child]))))
                elif child in stack:
                    low[node] = min(low[node], index[child])
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[node])
            if low[node] == index[node]:
                component = set(stack[stack.index(node):])
                del stack[stack.index(node):]
                if len(component) > 1:
                    components.append(component)
    return components
