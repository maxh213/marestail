import time

from marestail.context import Context
from marestail.perf.scope import is_benchmark
from marestail.report import Result
from marestail.shell import run

MAX_LINES = 60


def run_gate(ctx: Context) -> Result:
    started = time.time()
    if ctx.scoped and not changed_python(ctx):
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
    excluded = benchmark_exclusion(ctx)
    return [
        ("ruff", [ctx.python_bin("ruff"), "check", "--output-format", "concise", *excluded, *targets]),
        ("format", [ctx.python_bin("ruff"), "format", "--check", *excluded, *targets]),
        ("mypy", [ctx.python_bin("mypy"), "--no-error-summary", "--no-pretty", *mypy_targets(ctx)]),
    ]


def benchmark_exclusion(ctx: Context) -> list[str]:
    return ["--extend-exclude", "perf/**"] if ctx.python_root().resolve() == ctx.root.resolve() else []


def changed_python(ctx: Context) -> list[str]:
    return [path for path in ctx.changed_under(ctx.python_root(), (".py",)) if not is_benchmark(path)]


def python_targets(ctx: Context) -> list[str]:
    if ctx.scoped:
        return changed_python(ctx)
    return [str(ctx.python_root().relative_to(ctx.root))]


def mypy_targets(ctx: Context) -> list[str]:
    if ctx.scoped:
        return changed_python(ctx)
    return []


def relevant(output: str) -> list[str]:
    lines = [line for line in output.splitlines() if line.strip() and not line.startswith(("Found ", "warning:"))]
    return lines[:MAX_LINES]


