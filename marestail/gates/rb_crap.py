import json
import time
from pathlib import Path

from marestail.context import Context
from marestail.gates.rb_tests import COVERAGE_JSON, relative_path
from marestail.report import Result
from marestail.ruby import scan


def run_gate(ctx: Context) -> Result:
    started = time.time()
    coverage_path = ctx.work / COVERAGE_JSON
    if not coverage_path.exists():
        return Result("rb.crap", False, "no coverage data; rb.tests must run first", [], 0.0)
    coverage = json.loads(coverage_path.read_text())
    files = ruby_sources(ctx)
    if ctx.scope_changed:
        files = [f for f in files if str(f.relative_to(ctx.root)) in ctx.changed]
    if not files:
        return Result.skipped("rb.crap", "no files in scope")
    code, output = scan(ctx, "complexity", files)
    if code != 0:
        return Result("rb.crap", False, "complexity scanner failed", output.splitlines()[-10:], time.time() - started)
    functions = json.loads(output or "[]")
    limit = float(ctx.ruby("crap_max", 4))
    scored = [score(fn, coverage["files"].get(relative_path(fn["file"], ctx), {}), ctx) for fn in functions]
    offenders = sorted((f for f in scored if f["crap"] > limit), key=lambda f: -f["crap"])
    summary = f"{len(scored)} methods, {len(offenders)} above CRAP {limit:g}"
    return Result("rb.crap", not offenders, summary, [describe(f) for f in offenders], time.time() - started)


def ruby_sources(ctx: Context) -> list[Path]:
    root = ctx.ruby_root()
    sources = ctx.ruby("sources", ["app", "lib"])
    skip = {"vendor", "spec", "test", "tmp", "log", "node_modules", ".git", "coverage"}
    files = []
    for folder in sources:
        for path in (root / folder).rglob("*.rb"):
            if not any(part in skip for part in path.relative_to(ctx.root).parts):
                files.append(path)
    return sorted(files)


def score(fn: dict, file_cov: dict, ctx: Context) -> dict:
    complexity = fn["complexity"]
    lines = file_cov.get("lines") or []
    line = fn["line"]
    covered = 1.0
    if 1 <= line <= len(lines) and isinstance(lines[line - 1], int):
        covered = 1.0 if lines[line - 1] > 0 else 0.0
    elif file_cov.get("missing_lines") and line in file_cov["missing_lines"]:
        covered = 0.0
    crap = complexity**2 * (1 - covered) ** 3 + complexity
    return {
        "file": relative_path(fn["file"], ctx),
        "line": line,
        "name": fn["name"],
        "cc": complexity,
        "cov": covered,
        "crap": crap,
    }


def describe(f: dict) -> str:
    return f"{f['file']}:{f['line']} {f['name']} crap={f['crap']:.1f} (cc={f['cc']}, coverage={f['cov']:.0%})"
