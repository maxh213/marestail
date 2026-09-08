import time

from marestail.context import Context
from marestail.report import Result
from marestail.shell import run


def run_gate(ctx: Context) -> Result:
    started = time.time()
    env = {"PYTHONPATH": str(ctx.python_root())}
    code, output = run([ctx.python_bin("lint-imports"), "--no-cache"], cwd=ctx.root, env=env, timeout=600)
    findings = broken_lines(output) if code != 0 else []
    summary = "import contracts kept" if code == 0 else "import contracts broken"
    return Result("py.deps", code == 0, summary, findings, time.time() - started)


def broken_lines(output: str) -> list[str]:
    lines = [line.strip() for line in output.splitlines()]
    interesting = [line for line in lines if line and not set(line) <= set("─╔╗╚╝║━╺ ")]
    start = next((i for i, line in enumerate(interesting) if "BROKEN" in line or "Error" in line), 0)
    return interesting[start:][:60]


