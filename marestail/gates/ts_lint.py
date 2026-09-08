import time

from marestail.context import Context
from marestail.report import Result
from marestail.shell import run

MAX_LINES = 60


def run_gate(ctx: Context) -> Result:
    started = time.time()
    findings: list[str] = []
    for label, command in commands(ctx):
        code, output = run(command, cwd=ctx.ts_root(), timeout=900)
        if code != 0:
            findings.extend(f"{label}: {line}" for line in relevant(output))
    summary = "tsc and eslint clean" if not findings else f"{len(findings)} problems"
    return Result("ts.lint", not findings, summary, findings, time.time() - started)


def commands(ctx: Context) -> list[tuple[str, list[str]]]:
    return [
        ("tsc", ["npx", "tsc", "--noEmit", "-p", ctx.ts("tsconfig", "tsconfig.json")]),
        ("eslint", ["npx", "eslint", ".", "--max-warnings", "0", "--format", "unix"]),
    ]


def relevant(output: str) -> list[str]:
    lines = [line for line in output.splitlines() if line.strip() and "problem" not in line]
    return lines[:MAX_LINES]


