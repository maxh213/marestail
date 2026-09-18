import json
import time
from pathlib import Path
from typing import Any

from marestail.context import Context
from marestail.report import Result
from marestail.ruby import bundle, relative
from marestail.shell import run

GATE = "rb.lint"
MAX_LINES = 60
RUBY_SUFFIXES = (".rb", ".rake", ".jbuilder")


def run_gate(ctx: Context) -> Result:
    started = time.time()
    root = ctx.ruby_root()
    if nothing_changed(ctx, root):
        return Result.skipped(GATE, "no changed ruby files")
    code, output = run(bundle(ctx, "rubocop", "--format", "json", "--force-exclusion"), cwd=root, timeout=900)
    if code == 127:
        return Result(
            GATE, False, "rubocop missing", ["rubocop is not installed: add gem 'rubocop' and bundle install"], time.time() - started
        )
    findings = lint_findings(code, output, ctx)
    summary = f"{len(findings)} problems" if findings else "rubocop clean"
    return Result(GATE, not findings, summary, findings[:MAX_LINES], time.time() - started)


def nothing_changed(ctx: Context, root: Path) -> bool:
    return ctx.scoped and not ctx.changed_under(root, RUBY_SUFFIXES)


def lint_findings(code: int, output: str, ctx: Context) -> list[str]:
    if output.strip():
        return parse(output, ctx)
    return [f"rubocop failed: {output.strip()[-200:]}"] if code != 0 else []


def parse(output: str, ctx: Context) -> list[str]:
    report = decode_report(output)
    if report is None:
        return nonblank(output)
    return all_offenses(report, ctx)


def nonblank(output: str) -> list[str]:
    return [line for line in output.splitlines() if line.strip()][:MAX_LINES]


def all_offenses(report: dict[str, Any], ctx: Context) -> list[str]:
    return [finding for file in report.get("files", []) for finding in file_offenses(file, ctx)]


def decode_report(output: str) -> dict[str, Any] | None:
    start = output.find("{")
    if start < 0:
        return None
    try:
        report: dict[str, Any] = json.loads(output[start:])
    except json.JSONDecodeError:
        return None
    return report


def file_offenses(file: dict[str, Any], ctx: Context) -> list[str]:
    rel = relative(file.get("path", ""), ctx)
    if not ctx.in_scope(rel):
        return []
    return [offense_line(rel, offense) for offense in file.get("offenses", [])]


def offense_line(rel: str, offense: dict[str, Any]) -> str:
    line = (offense.get("location") or {}).get("line", 0)
    cop = offense.get("cop_name") or "rubocop"
    return f"{rel}:{line} {cop}: {offense.get('message', '')}"
