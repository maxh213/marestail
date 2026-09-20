from pathlib import Path
from typing import Any

from marestail.context import Context

PY_COVERAGE = "py-coverage.json"
TS_COVERAGE_DIR = "ts-coverage"
EX_COVERAGE = "ex-coverage.json"
RB_COVERAGE = "rb-coverage.json"
ER_COVERAGE = "er-coverage.json"


def relative_path(file_str: str, ctx: Context) -> str:
    path = Path(file_str)
    try:
        return str(path.resolve().relative_to(ctx.root.resolve()))
    except ValueError:
        return file_str


def in_scope_findings(findings: list[str], ctx: Context) -> list[str]:
    return [finding for finding in findings if ctx.in_scope(finding.split(":", 1)[0])]


def coverage_findings(coverage: dict[str, Any], ctx: Context) -> list[str]:
    findings: list[str] = []
    for file_str, data in sorted(coverage["files"].items()):
        relative = relative_path(file_str, ctx)
        if ctx.in_scope(relative):
            findings.extend(f"{relative}:{line} not covered" for line in gated_missing(data, ctx.gated_lines(relative)))
    return findings


def gated_missing(data: dict[str, Any], gated: set[int] | None) -> list[int]:
    missing: list[int] = data.get("missing_lines", [])
    if gated is None:
        return missing
    return [line for line in missing if line in gated]


def scoped_lines(file: str, ctx: Context) -> set[int] | None:
    if not ctx.scoped:
        return None
    path = str((ctx.python_root() / file).resolve().relative_to(ctx.root))
    if not ctx.in_scope(path):
        return set()
    return ctx.gated_lines(path)
