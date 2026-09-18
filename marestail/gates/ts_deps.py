import re
import time

from marestail.context import Context
from marestail.gates.ts_tests import relative
from marestail.report import Result
from marestail.shell import run

VIOLATION = re.compile(r"^(?:error|warn|info|hint) \S+: (?P<path>\S+?)(?:\s+→.*)?$")
MAX_LINES = 60


def run_gate(ctx: Context) -> Result:
    started = time.time()
    config = ctx.ts("depcruise_config", ".dependency-cruiser.cjs")
    source = ctx.ts("source", "src")
    command = ["npx", "depcruise", "--config", config, "--output-type", "err", source]
    code, output = run(command, cwd=ctx.ts_root(), timeout=600)
    findings, ok = outcome(output, ctx, code)
    summary = "dependency rules kept" if ok else "dependency rules broken"
    return Result("ts.deps", ok, summary, findings, time.time() - started)


def outcome(output: str, ctx: Context, code: int) -> tuple[list[str], bool]:
    if ctx.scoped:
        findings = scoped_findings(output, ctx, code)
        return findings, not findings
    return failure_lines([line for line in output.splitlines() if line.strip()], code), code == 0


def failure_lines(lines: list[str], code: int) -> list[str]:
    return lines[:MAX_LINES] if code != 0 else []


def scoped_findings(output: str, ctx: Context, code: int) -> list[str]:
    lines = stripped_lines(output)
    violations = [line for line in lines if VIOLATION.match(line)]
    return in_scope_violations(violations, ctx) if violations else failure_lines(lines, code)


def stripped_lines(output: str) -> list[str]:
    return [line.strip() for line in output.splitlines() if line.strip()]


def in_scope_violations(violations: list[str], ctx: Context) -> list[str]:
    return [line for line in violations if in_scope_violation(line, ctx)][:MAX_LINES]


def in_scope_violation(line: str, ctx: Context) -> bool:
    match = VIOLATION.match(line)
    return match is not None and ctx.in_scope(relative(match.group("path"), ctx))
