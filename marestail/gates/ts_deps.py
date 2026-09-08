import time

from marestail.context import Context
from marestail.report import Result
from marestail.shell import run


def run_gate(ctx: Context) -> Result:
    started = time.time()
    config = ctx.ts("depcruise_config", ".dependency-cruiser.cjs")
    source = ctx.ts("source", "src")
    command = ["npx", "depcruise", "--config", config, "--output-type", "err", source]
    code, output = run(command, cwd=ctx.ts_root(), timeout=600)
    findings = [line for line in output.splitlines() if line.strip()][:60] if code != 0 else []
    summary = "dependency rules kept" if code == 0 else "dependency rules broken"
    return Result("ts.deps", code == 0, summary, findings, time.time() - started)


