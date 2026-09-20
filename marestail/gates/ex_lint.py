import re
import time
from pathlib import Path

from marestail.context import Context
from marestail.elixir import project_files
from marestail.report import Result
from marestail.shell import run

scoped_sources = project_files
GATE = "ex.lint"
MAX_LINES = 60
FORMAT = ["mix", "format", "--check-formatted"]
COMPILE = ["mix", "compile", "--warnings-as-errors"]


def run_gate(ctx: Context) -> Result:
    started = time.time()
    root = ctx.elixir_root()
    if ctx.scoped:
        return scoped_run(ctx, root, started)
    findings = problems("format", run(FORMAT, cwd=root, timeout=300))
    findings += problems("compile", run(COMPILE, cwd=root, timeout=600))
    summary = f"{len(findings)} problems" if findings else "mix format, compile clean"
    return Result(GATE, not findings, summary, findings, time.time() - started)


def problems(label: str, outcome: tuple[int, str]) -> list[str]:
    code, output = outcome
    return [f"{label}: {line}" for line in relevant(output)] if code != 0 else []


def scoped_run(ctx: Context, root: Path, started: float) -> Result:
    files = scoped_sources(ctx, root, ctx.changed_under(root, (".ex", ".exs")))
    if not files:
        return Result.skipped(GATE, "no elixir files in scope")
    findings = problems("format", run([*FORMAT, *files], cwd=root, timeout=300))
    compile_code, compile_out = run(COMPILE, cwd=root, timeout=600)
    if compile_code != 0:
        findings.extend(compile_findings(compile_out, files))
    findings = findings[:MAX_LINES]
    summary = f"{len(findings)} problems in scope" if findings else "mix format, compile clean in scope"
    return Result(GATE, not findings, summary, findings, time.time() - started)


def compile_findings(output: str, files: list[str]) -> list[str]:
    return [f"compile: {line}" for block in scoped_blocks(output, files) for line in block.splitlines() if line.strip()]


def scoped_blocks(output: str, files: list[str]) -> list[str]:
    blocks = re.split(r"\n\s*\n", output)
    return [block for block in blocks if any(mentions(block, path) for path in files)]


def mentions(block: str, path: str) -> bool:
    return re.search(re.escape(path) + r"(?=[:=\s]|$)", block) is not None


def relevant(output: str) -> list[str]:
    lines = [line for line in output.splitlines() if line.strip() and not line.startswith("==>")]
    return lines[:MAX_LINES]
