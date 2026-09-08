import shlex
import time

from marestail.context import Context
from marestail.report import Result
from marestail.shell import run, tail


def run_gate(ctx: Context) -> Result:
    started = time.time()
    command = ctx.config.get("qa", "cmd")
    if not command:
        return Result.skipped("qa", "no [qa] cmd configured")
    cwd = ctx.root / ctx.config.get("qa", "cwd", ".")
    code, output = run(["bash", "-lc", command], cwd=cwd, timeout=3600)
    summary = "qa passed" if code == 0 else f"qa failed (exit {code})"
    return Result("qa", code == 0, summary, tail(output, 40) if code else [], time.time() - started)


