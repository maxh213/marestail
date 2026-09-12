import re
import time
from pathlib import Path

from marestail.context import Context
from marestail.report import Result
from marestail.shell import run

MAX_LINES = 60


def run_gate(ctx: Context) -> Result:
    started = time.time()
    root = ctx.elixir_root()
    if ctx.scoped:
        return scoped_run(ctx, root, started)
    findings = []
    format_code, format_out = run(["mix", "format", "--check-formatted"], cwd=root, timeout=300)
    if format_code != 0:
        findings.extend(f"format: {line}" for line in relevant(format_out))
    compile_code, compile_out = run(["mix", "compile", "--warnings-as-errors"], cwd=root, timeout=600)
    if compile_code != 0:
        findings.extend(f"compile: {line}" for line in relevant(compile_out))
    summary = "mix format, compile clean" if not findings else f"{len(findings)} problems"
    return Result("ex.lint", not findings, summary, findings, time.time() - started)


def scoped_run(ctx: Context, root: Path, started: float) -> Result:
    files = scoped_sources(ctx, root)
    if not files:
        return Result.skipped("ex.lint", "no elixir files in scope")
    findings = []
    format_code, format_out = run(["mix", "format", "--check-formatted", *files], cwd=root, timeout=300)
    if format_code != 0:
        findings.extend(f"format: {line}" for line in relevant(format_out))
    compile_code, compile_out = run(["mix", "compile", "--warnings-as-errors"], cwd=root, timeout=600)
    if compile_code != 0:
        for block in scoped_blocks(compile_out, files):
            findings.extend(f"compile: {line}" for line in block.splitlines() if line.strip())
    findings = findings[:MAX_LINES]
    summary = "mix format, compile clean in scope" if not findings else f"{len(findings)} problems in scope"
    return Result("ex.lint", not findings, summary, findings, time.time() - started)


def scoped_sources(ctx: Context, root: Path) -> list[str]:
    prefix = root.relative_to(ctx.root)
    relatives = [strip_prefix(path, prefix) for path in ctx.changed_under(root, (".ex", ".exs"))]
    return sorted(str(path) for path in relatives if (root / path).is_file())


def strip_prefix(path: str, prefix: Path) -> Path:
    relative = Path(path)
    if str(prefix) == ".":
        return relative
    return relative.relative_to(prefix)


def scoped_blocks(output: str, files: list[str]) -> list[str]:
    blocks = re.split(r"\n\s*\n", output)
    return [block for block in blocks if any(mentions(block, path) for path in files)]


def mentions(block: str, path: str) -> bool:
    return re.search(re.escape(path) + r"(?=[:=\s]|$)", block) is not None


def relevant(output: str) -> list[str]:
    lines = [line for line in output.splitlines() if line.strip() and not line.startswith("==>")]
    return lines[:MAX_LINES]
