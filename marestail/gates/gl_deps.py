import json
import time
from collections import defaultdict
from pathlib import Path

from marestail.context import Context
from marestail.gleam import gleam_sources, scan
from marestail.report import Result


def run_gate(ctx: Context) -> Result:
    started = time.time()
    files = gleam_sources(ctx)
    if not files:
        return Result.skipped("gl.deps", "no gleam sources")
    code, output = scan(ctx, "deps", files)
    if code != 0:
        return Result("gl.deps", False, "dependency scanner failed", output.splitlines()[-10:], time.time() - started)
    try:
        edges = json.loads(output or "[]")
    except json.JSONDecodeError:
        return Result("gl.deps", False, "dependency scanner produced non-JSON", output.splitlines()[-10:], time.time() - started)
    graph: dict[str, set[str]] = defaultdict(set)
    for edge in edges:
        src = relative(edge.get("from", ""), ctx)
        if ctx.scope_changed and src not in ctx.changed:
            continue
        graph[src].add(edge.get("to", ""))
    findings = cycle_findings(graph)
    summary = "dependency graph acyclic" if not findings else f"{len(findings)} dependency problems"
    return Result("gl.deps", not findings, summary, findings[:60], time.time() - started)


def cycle_findings(graph: dict[str, set[str]]) -> list[str]:
    findings = []
    visiting: set[str] = set()
    visited: set[str] = set()
    stack: list[str] = []

    def dfs(node: str) -> None:
        if node in visited:
            return
        if node in visiting:
            if node in stack:
                cycle = stack[stack.index(node) :] + [node]
                findings.append(f"{cycle[0]}:1 import cycle: {' -> '.join(cycle)}")
            return
        visiting.add(node)
        stack.append(node)
        for nxt in sorted(graph.get(node, ())):
            dfs(nxt)
        stack.pop()
        visiting.remove(node)
        visited.add(node)

    for node in sorted(graph):
        dfs(node)
    return findings


def relative(path: str, ctx: Context) -> str:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = ctx.gleam_root() / path if not candidate.exists() else candidate
    try:
        return str(candidate.resolve().relative_to(ctx.root.resolve()))
    except ValueError:
        return path
