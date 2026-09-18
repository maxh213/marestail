import re
import time
from pathlib import Path

from marestail.context import Context
from marestail.report import Result
from marestail.shell import run

VIOLATION = re.compile(r"^-\s+([\w.]+)\s*->")
BROKEN_MARKER = "Broken contracts"


def run_gate(ctx: Context) -> Result:
    started = time.time()
    env = {"PYTHONPATH": str(ctx.python_root())}
    code, output = run([ctx.python_bin("lint-imports"), "--no-cache"], cwd=ctx.root, env=env, timeout=600)
    if code == 0:
        return Result("py.deps", True, "import contracts kept", [], time.time() - started)
    if ctx.scoped and BROKEN_MARKER in output:
        findings = scoped_violations(output, ctx)
        summary = "import contracts kept in scope" if not findings else "import contracts broken in scope"
        return Result("py.deps", not findings, summary, findings, time.time() - started)
    return Result("py.deps", False, "import contracts broken", broken_lines(output), time.time() - started)


def scoped_violations(output: str, ctx: Context) -> list[str]:
    findings = []
    for line in output.splitlines():
        match = VIOLATION.match(line.strip())
        if match and module_in_scope(match.group(1), ctx):
            findings.append(line.strip())
    return findings


def module_in_scope(module: str, ctx: Context) -> bool:
    existing = [path for path in module_paths(module, ctx) if path.is_file()]
    if not existing:
        return True
    return any(ctx.in_scope(str(path.relative_to(ctx.root))) for path in existing)


def module_paths(module: str, ctx: Context) -> list[Path]:
    base = ctx.python_root() / module.replace(".", "/")
    return [base.with_suffix(".py").resolve(), (base / "__init__.py").resolve()]


def broken_lines(output: str) -> list[str]:
    lines = [line.strip() for line in output.splitlines()]
    interesting = [line for line in lines if line and not set(line) <= set("─╔╗╚╝║━╺ ")]
    start = next((i for i, line in enumerate(interesting) if "BROKEN" in line or "Error" in line), 0)
    return interesting[start:][:60]
