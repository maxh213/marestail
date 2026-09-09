import time

from marestail.context import Context
from marestail.report import Result
from marestail.shell import run

MAX_LINES = 60


def run_gate(ctx: Context) -> Result:
    started = time.time()
    root = ctx.gleam_root()
    if ctx.scope_changed and not ctx.changed_under(root, (".gleam",)):
        return Result.skipped("gl.lint", "no changed gleam files")
    findings = []
    format_code, format_out = run(["gleam", "format", "--check"], cwd=root, timeout=300)
    if format_code == 127:
        return Result("gl.lint", False, "gleam missing", ["install gleam from https://gleam.run"], time.time() - started)
    if format_code != 0:
        findings.extend(f"format: {line}" for line in relevant(format_out))
    build_code, build_out = run(["gleam", "build", "--warnings-as-errors"], cwd=root, timeout=600)
    if build_code != 0:
        findings.extend(f"build: {line}" for line in relevant(build_out))
    summary = "gleam format, build clean" if not findings else f"{len(findings)} problems"
    return Result("gl.lint", not findings, summary, findings[:MAX_LINES], time.time() - started)


def relevant(output: str) -> list[str]:
    lines = [line for line in output.splitlines() if line.strip()]
    return lines[:MAX_LINES]
