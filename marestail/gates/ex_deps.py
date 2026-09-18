import time
from pathlib import Path

from marestail.context import Context
from marestail.report import Result
from marestail.shell import run, tail

GATE = "ex.deps"


def run_gate(ctx: Context) -> Result:
    started = time.time()
    root = ctx.elixir_root()
    code, output = run(["mix", "xref", "graph", "--format", "cycles", "--fail-above", "0"], cwd=root, timeout=600)
    if code == 0:
        return Result(GATE, True, "dependency graph acyclic", [], time.time() - started)
    if not ctx.scoped:
        return Result(GATE, False, "dependency cycles found", cycle_lines(output), time.time() - started)
    return scoped_result(ctx, root, output, started)


def cycle_lines(output: str) -> list[str]:
    return [line.strip() for line in output.splitlines() if cycle_line(line)][:60]


def cycle_line(line: str) -> bool:
    return bool(line.strip()) and ("Cycle" in line or "lib/" in line)


def scoped_result(ctx: Context, root: Path, output: str, started: float) -> Result:
    cycles = parse_cycles(output)
    if not cycles:
        return Result(GATE, False, "xref failed", tail(output), time.time() - started)
    findings = cycle_findings(cycles, ctx, root)
    summary = f"{len(findings)} dependency cycles in scope" if findings else "no dependency cycles in scope"
    return Result(GATE, not findings, summary, findings, time.time() - started)


def cycle_findings(cycles: list[list[str]], ctx: Context, root: Path) -> list[str]:
    return [f"cycle: {', '.join(cycle)}" for cycle in cycles if in_scope_cycle(cycle, ctx, root)]


def parse_cycles(output: str) -> list[list[str]]:
    cycles: list[list[str]] = []
    current: list[str] | None = None
    for line in output.splitlines():
        current = next_cycle(line, current, cycles)
    return cycles


def next_cycle(line: str, current: list[str] | None, cycles: list[list[str]]) -> list[str] | None:
    if line.startswith("Cycle of length"):
        cycles.append([])
        return cycles[-1]
    if current is None or not line.strip():
        return current
    return extend_cycle(line, current)


def extend_cycle(line: str, current: list[str]) -> list[str] | None:
    if not cycle_file(line):
        return None
    current.append(line.strip())
    return current


def cycle_file(line: str) -> bool:
    return line.startswith((" ", "\t")) and line.strip().endswith((".ex", ".exs"))


def in_scope_cycle(cycle: list[str], ctx: Context, root: Path) -> bool:
    return any(ctx.in_scope(repo_path(file, ctx, root)) for file in cycle)


def repo_path(file: str, ctx: Context, root: Path) -> str:
    prefix = root.relative_to(ctx.root)
    if str(prefix) == ".":
        return file
    return str(prefix / file)
