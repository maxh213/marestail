import json
import time
from pathlib import Path

from marestail.context import Context
from marestail.report import Result
from marestail.ruby import bundle
from marestail.shell import run

MAX_LINES = 60


def run_gate(ctx: Context) -> Result:
    started = time.time()
    root = ctx.ruby_root()
    if ctx.scope_changed and not ctx.changed_under(root, (".rb", ".rake", ".jbuilder")):
        return Result.skipped("rb.lint", "no changed ruby files")
    code, output = run(bundle(ctx, "rubocop", "--format", "json", "--force-exclusion"), cwd=root, timeout=900)
    if code == 127:
        return Result("rb.lint", False, "rubocop missing", ["rubocop is not installed: add gem 'rubocop' and bundle install"], time.time() - started)
    findings = parse(output, ctx) if output.strip() else ([f"rubocop failed: {output.strip()[-200:]}"] if code != 0 else [])
    summary = "rubocop clean" if not findings else f"{len(findings)} problems"
    return Result("rb.lint", not findings, summary, findings[:MAX_LINES], time.time() - started)


def parse(output: str, ctx: Context) -> list[str]:
    start = output.find("{")
    if start < 0:
        return [line for line in output.splitlines() if line.strip()][:MAX_LINES]
    try:
        report = json.loads(output[start:])
    except json.JSONDecodeError:
        return [line for line in output.splitlines() if line.strip()][:MAX_LINES]
    findings = []
    for file in report.get("files", []):
        rel = relative(file.get("path", ""), ctx)
        if ctx.scope_changed and rel not in ctx.changed:
            continue
        for offense in file.get("offenses", []):
            line = (offense.get("location") or {}).get("line", 0)
            cop = offense.get("cop_name") or "rubocop"
            findings.append(f"{rel}:{line} {cop}: {offense.get('message', '')}")
    return findings


def relative(path: str, ctx: Context) -> str:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = ctx.ruby_root() / path
    try:
        return str(candidate.resolve().relative_to(ctx.root.resolve()))
    except ValueError:
        return path
