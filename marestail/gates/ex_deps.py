import time
from pathlib import Path

from marestail.context import Context
from marestail.report import Result
from marestail.shell import run, tail


def run_gate(ctx: Context) -> Result:
    started = time.time()
    root = ctx.elixir_root()
    code, output = run(["mix", "xref", "graph", "--format", "cycles", "--fail-above", "0"], cwd=root, timeout=600)
    if code == 0:
        return Result("ex.deps", True, "dependency graph acyclic", [], time.time() - started)
    if not ctx.scoped:
        findings = [line.strip() for line in output.splitlines() if line.strip() and ("Cycle" in line or "lib/" in line)][:60]
        return Result("ex.deps", False, "dependency cycles found", findings, time.time() - started)
    cycles = parse_cycles(output)
    if not cycles:
        return Result("ex.deps", False, "xref failed", tail(output), time.time() - started)
    offending = [cycle for cycle in cycles if in_scope_cycle(cycle, ctx, root)]
    findings = [f"cycle: {', '.join(cycle)}" for cycle in offending]
    summary = "no dependency cycles in scope" if not findings else f"{len(findings)} dependency cycles in scope"
    return Result("ex.deps", not findings, summary, findings, time.time() - started)


def parse_cycles(output: str) -> list[list[str]]:
    cycles: list[list[str]] = []
    current: list[str] | None = None
    for line in output.splitlines():
        if line.startswith("Cycle of length"):
            current = []
            cycles.append(current)
        elif current is not None and line.strip():
            if line.startswith((" ", "\t")) and line.strip().endswith((".ex", ".exs")):
                current.append(line.strip())
            else:
                current = None
    return cycles


def in_scope_cycle(cycle: list[str], ctx: Context, root: Path) -> bool:
    return any(ctx.in_scope(repo_path(file, ctx, root)) for file in cycle)


def repo_path(file: str, ctx: Context, root: Path) -> str:
    prefix = root.relative_to(ctx.root)
    if str(prefix) == ".":
        return file
    return str(prefix / file)
