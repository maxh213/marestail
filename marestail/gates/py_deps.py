import re
import time
from pathlib import Path

from marestail.context import Context
from marestail.report import Result, elapsed
from marestail.shell import run

GATE = "py.deps"
VIOLATION = re.compile(r"^-\s+([\w.]+)\s*->")
BROKEN_MARKER = "Broken contracts"
BOX_DRAWING = set("─╔╗╚╝║━╺ ")


def run_gate(ctx: Context) -> Result:
    started = time.time()
    env = {"PYTHONPATH": str(ctx.python_root())}
    code, output = run([ctx.python_bin("lint-imports"), "--no-cache"], cwd=ctx.root, env=env, timeout=600)
    if code == 0:
        return Result(GATE, True, "import contracts kept", [], elapsed(started))
    if ctx.scoped and BROKEN_MARKER in output:
        return scoped_result(output, ctx, started)
    return Result(GATE, False, "import contracts broken", broken_lines(output), elapsed(started))


def scoped_result(output: str, ctx: Context, started: float) -> Result:
    findings = scoped_violations(output, ctx)
    summary = "import contracts kept in scope" if not findings else "import contracts broken in scope"
    return Result(GATE, not findings, summary, findings, elapsed(started))


def scoped_violations(output: str, ctx: Context) -> list[str]:
    findings = []
    for line in output.splitlines():
        match = VIOLATION.match(line.strip())
        if match and module_in_scope(match.group(1), ctx):
            findings.append(line.strip())
    return findings


def module_in_scope(module: str, ctx: Context) -> bool:
    existing = existing_paths(module, ctx)
    return not existing or any(ctx.in_scope(str(path.relative_to(ctx.root))) for path in existing)


def existing_paths(module: str, ctx: Context) -> list[Path]:
    return [path for path in module_paths(module, ctx) if path.is_file()]


def module_paths(module: str, ctx: Context) -> list[Path]:
    base = ctx.python_root() / module.replace(".", "/")
    return [base.with_suffix(".py").resolve(), (base / "__init__.py").resolve()]


def broken_lines(output: str) -> list[str]:
    interesting = [line for line in (raw.strip() for raw in output.splitlines()) if is_text(line)]
    return interesting[first_broken(interesting) :][:60]


def is_text(line: str) -> bool:
    return bool(line) and not set(line) <= BOX_DRAWING


def first_broken(lines: list[str]) -> int:
    return next((i for i, line in enumerate(lines) if is_break(line)), 0)


def is_break(line: str) -> bool:
    return "BROKEN" in line or "Error" in line
