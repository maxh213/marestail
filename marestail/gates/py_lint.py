import time

from marestail.context import Context
from marestail.report import Result
from marestail.shell import run

MAX_LINES = 60


def run_gate(ctx: Context) -> Result:
    started = time.time()
    if ctx.scope_changed and not ctx.changed_under(ctx.python_root(), (".py",)):
        return Result.skipped("py.lint", "no changed python files")
    findings: list[str] = []
    for label, command in commands(ctx):
        code, output = run(command, cwd=ctx.root, timeout=900)
        if code != 0:
            findings.extend(f"{label}: {line}" for line in relevant(output))
    summary = "ruff, ruff format, mypy clean" if not findings else f"{len(findings)} problems"
    return Result("py.lint", not findings, summary, findings, time.time() - started)


def commands(ctx: Context) -> list[tuple[str, list[str]]]:
    targets = python_targets(ctx)
    return [
        ("ruff", [ctx.python_bin("ruff"), "check", "--output-format", "concise", *targets]),
        ("format", [ctx.python_bin("ruff"), "format", "--check", *targets]),
        ("mypy", [ctx.python_bin("mypy"), "--no-error-summary", "--no-pretty", *mypy_targets(ctx)]),
    ]


def python_targets(ctx: Context) -> list[str]:
    if ctx.scope_changed:
        return ctx.changed_under(ctx.python_root(), (".py",))
    return [str(ctx.python_root().relative_to(ctx.root))]


def mypy_targets(ctx: Context) -> list[str]:
    if ctx.scope_changed:
        return ctx.changed_under(ctx.python_root(), (".py",))
    return []


def relevant(output: str) -> list[str]:
    lines = [line for line in output.splitlines() if line.strip() and not line.startswith(("Found ", "warning:"))]
    return lines[:MAX_LINES]


