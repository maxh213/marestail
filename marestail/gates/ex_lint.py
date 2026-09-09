import time

from marestail.context import Context
from marestail.report import Result
from marestail.shell import run

MAX_LINES = 60


def run_gate(ctx: Context) -> Result:
    started = time.time()
    root = ctx.elixir_root()
    if ctx.scope_changed and not ctx.changed_under(root, (".ex", ".exs")):
        return Result.skipped("ex.lint", "no changed elixir files")
    findings = []
    format_code, format_out = run(["mix", "format", "--check-formatted"], cwd=root, timeout=300)
    if format_code != 0:
        findings.extend(f"format: {line}" for line in relevant(format_out))
    compile_code, compile_out = run(["mix", "compile", "--warnings-as-errors"], cwd=root, timeout=600)
    if compile_code != 0:
        findings.extend(f"compile: {line}" for line in relevant(compile_out))
    summary = "mix format, compile clean" if not findings else f"{len(findings)} problems"
    return Result("ex.lint", not findings, summary, findings, time.time() - started)


def relevant(output: str) -> list[str]:
    lines = [line for line in output.splitlines() if line.strip() and not line.startswith("==>")]
    return lines[:MAX_LINES]
