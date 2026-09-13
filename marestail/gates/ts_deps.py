import re
import time
from pathlib import Path

from marestail.context import Context
from marestail.report import Result
from marestail.shell import run

VIOLATION = re.compile(r"^(?:error|warn|info|hint) \S+: (?P<path>\S+?)(?:\s+→.*)?$")


def run_gate(ctx: Context) -> Result:
    started = time.time()
    config = ctx.ts("depcruise_config", ".dependency-cruiser.cjs")
    source = ctx.ts("source", "src")
    command = ["npx", "depcruise", "--config", config, "--output-type", "err", source]
    code, output = run(command, cwd=ctx.ts_root(), timeout=600)
    if ctx.scoped:
        findings = scoped_findings(output, ctx)
        ok = not findings
    else:
        findings = [line for line in output.splitlines() if line.strip()][:60] if code != 0 else []
        ok = code == 0
    summary = "dependency rules kept" if ok else "dependency rules broken"
    return Result("ts.deps", ok, summary, findings, time.time() - started)


def scoped_findings(output: str, ctx: Context) -> list[str]:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    violations = [line for line in lines if VIOLATION.match(line)]
    if not violations:
        return lines[:60]
    return [line for line in violations if in_scope_violation(line, ctx)][:60]


def in_scope_violation(line: str, ctx: Context) -> bool:
    match = VIOLATION.match(line)
    return bool(match) and ctx.in_scope(relative(match.group("path"), ctx))


def relative(path: str, ctx: Context) -> str:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = ctx.ts_root() / path
    try:
        return candidate.resolve().relative_to(ctx.root.resolve()).as_posix()
    except ValueError:
        return path
