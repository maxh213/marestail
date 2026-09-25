import time

from marestail.context import Context, is_benchmark
from marestail.report import Result, elapsed
from marestail.shell import run

GATE = "py.lint"
MAX_LINES = 60
RUFF = "ruff"
FORMAT = "format"
MYPY = "mypy"


def run_gate(ctx: Context) -> Result:
    started = time.time()
    if ctx.scoped and not changed_python(ctx):
        return Result.skipped(GATE, "no changed python files")
    findings = lint_findings(ctx)
    summary = "ruff, ruff format, mypy clean" if not findings else f"{len(findings)} problems"
    return Result(GATE, not findings, summary, findings, elapsed(started))


def lint_findings(ctx: Context) -> list[str]:
    findings: list[str] = []
    for label, command in commands(ctx):
        findings.extend(command_findings(label, command, ctx))
    return findings


def command_findings(label: str, command: list[str], ctx: Context) -> list[str]:
    code, output = run(command, cwd=ctx.root, timeout=900)
    if code == 0:
        return []
    return [f"{label}: {line}" for line in relevant(output)]


def commands(ctx: Context) -> list[tuple[str, list[str]]]:
    targets = python_targets(ctx)
    excluded = path_exclusions(ctx)
    return [
        (RUFF, [ctx.python_bin(RUFF), "check", "--output-format", "concise", *excluded, *targets]),
        (FORMAT, [ctx.python_bin(RUFF), FORMAT, "--check", *excluded, *targets]),
        (MYPY, [ctx.python_bin(MYPY), "--no-error-summary", "--no-pretty", *mypy_targets(ctx)]),
    ]


def path_exclusions(ctx: Context) -> list[str]:
    if ctx.python_root().resolve() != ctx.root.resolve():
        return []
    flags: list[str] = []
    for pattern in ("perf/**", "qa/**", "features/**"):
        flags.extend(["--extend-exclude", pattern])
    return flags


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
