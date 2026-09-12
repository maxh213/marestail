import json
import time

from marestail import erlang
from marestail.context import Context
from marestail.report import Result
from marestail.shell import tail

MAX_LINES = 60


def run_gate(ctx: Context) -> Result:
    started = time.time()
    sources = erlang.source_files(ctx)
    if not sources:
        return Result.skipped("er.deps", "no erlang sources under [erlang] sources (default src/)")
    ebin = erlang.fresh_dir(ctx.work / "er-deps-ebin")
    code, output = erlang.erlc(ctx, ["+debug_info", "-o", str(ebin), *map(str, sources)], timeout=900)
    problem = erlang.hint(code, output)
    if problem:
        return Result("er.deps", False, problem, [problem], time.time() - started)
    if code != 0:
        return Result("er.deps", False, "sources failed to compile", tail(output), time.time() - started)
    beams = sorted(str(beam) for beam in ebin.glob("*.beam"))
    code, output = erlang.escript(ctx, "deps.escript", beams, timeout=600)
    if code != 0:
        return Result("er.deps", False, "dependency scanner failed", output.splitlines()[-10:], time.time() - started)
    edges = json.loads(output)
    modules = {path.stem: erlang.rel(ctx, path) for path in sources}
    project = [
        {"from": modules[edge["from"]], "to": modules[edge["to"]], "line": edge["line"]}
        for edge in edges
        if edge["from"] in modules and edge["to"] in modules
    ]
    findings = cycle_findings(project)
    if ctx.scope_changed:
        findings = [f for f in findings if f.split(":")[0] in ctx.changed]
    summary = "dependency graph acyclic" if not findings else f"{len(findings)} dependency cycles"
    return Result("er.deps", not findings, summary, findings[:MAX_LINES], time.time() - started)


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
