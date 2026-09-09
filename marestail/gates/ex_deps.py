import time

from marestail.context import Context
from marestail.report import Result
from marestail.shell import run


def run_gate(ctx: Context) -> Result:
    started = time.time()
    root = ctx.elixir_root()
    code, output = run(["mix", "xref", "graph", "--format", "cycles", "--fail-above", "0"], cwd=root, timeout=600)
    findings = [line.strip() for line in output.splitlines() if line.strip() and ("Cycle" in line or "lib/" in line)][:60] if code != 0 else []
    summary = "dependency graph acyclic" if code == 0 else "dependency cycles found"
    return Result("ex.deps", code == 0, summary, findings, time.time() - started)
