import json
import re
import time
from pathlib import Path

from marestail.context import Context
from marestail.gates.rb_tests import COVERAGE_JSON, relative_path
from marestail.report import Result
from marestail.ruby import scan

STRING = re.compile(r"'[^'\\]*(?:\\.[^'\\]*)*'|\"[^\"\\]*(?:\\.[^\"\\]*)*\"")
OPENER = re.compile(r"^\s*(?:def|class|module|if|unless|case|while|until|for|begin)\b")
BLOCK_DO = re.compile(r"\bdo\b(?:\s*\|[^|]*\|)?\s*$")
CLOSER = re.compile(r"\bend\b")


def run_gate(ctx: Context) -> Result:
    started = time.time()
    coverage_path = ctx.work / COVERAGE_JSON
    if not coverage_path.exists():
        return Result("rb.crap", False, "no coverage data; rb.tests must run first", [], 0.0)
    coverage = json.loads(coverage_path.read_text())
    files = sources_in_scope(ctx)
    if not files:
        return Result.skipped("rb.crap", "no files in scope")
    code, output = scan(ctx, "complexity", files)
    if code != 0:
        return Result("rb.crap", False, "complexity scanner failed", output.splitlines()[-10:], time.time() - started)
    functions = functions_in_scope(json.loads(output or "[]"), ctx)
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


def sources_in_scope(ctx: Context) -> list[Path]:
    files = ruby_sources(ctx)
    if not ctx.scoped:
        return files
    return [path for path in files if ctx.in_scope(str(path.relative_to(ctx.root)))]


def functions_in_scope(functions: list[dict], ctx: Context) -> list[dict]:
    if not ctx.scoped:
        return functions
    kept = []
    for file, group in by_file(functions, ctx).items():
        if not ctx.in_scope(file):
            continue
        gated = ctx.gated_lines(file)
        if gated is None:
            kept.extend(group)
            continue
        ends = method_ranges(file_text(ctx, file), [fn["line"] for fn in group])
        kept.extend(fn for fn in group if body_touched(fn["line"], ends.get(fn["line"], fn["line"]), gated))
    return kept


def by_file(functions: list[dict], ctx: Context) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for fn in functions:
        groups.setdefault(relative_path(fn["file"], ctx), []).append(fn)
    return groups


def file_text(ctx: Context, file: str) -> str:
    try:
        return (ctx.root / file).read_text(errors="replace")
    except OSError:
        return ""


def body_touched(start: int, end: int, gated: set[int]) -> bool:
    if start < 1 or end < start:
        return True
    return not gated.isdisjoint(range(start, end + 1))


def method_ranges(text: str, starts: list[int]) -> dict[int, int]:
    deltas = [line_delta(line) for line in text.splitlines()]
    ordered = sorted(starts)
    return {
        start: method_end(deltas, start, (ordered[index + 1] - 1) if index + 1 < len(ordered) else len(deltas))
        for index, start in enumerate(ordered)
        if start >= 1
    }


def method_end(deltas: list[int], start: int, boundary: int) -> int:
    depth = 0
    for cursor in range(start - 1, len(deltas)):
        depth += deltas[cursor]
        if depth <= 0:
            return cursor + 1
    return max(start, min(boundary, len(deltas)))


def line_delta(line: str) -> int:
    code = STRING.sub("", line).split("#", 1)[0]
    if code.lstrip().startswith("="):
        return 0
    opens = 1 if OPENER.match(code) else (1 if BLOCK_DO.search(code) else 0)
    return opens - len(CLOSER.findall(code))


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
