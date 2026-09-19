import json
import re
import time
from pathlib import Path
from typing import Any

from marestail.context import Context
from marestail.gates.rb_tests import COVERAGE_JSON, relative_path
from marestail.report import Result
from marestail.ruby import scan, scanned, sources

GATE = "rb.crap"
STRING = re.compile(r"'[^'\\]*(?:\\.[^'\\]*)*'|\"[^\"\\]*(?:\\.[^\"\\]*)*\"")
OPENER = re.compile(r"^\s*(?:def|class|module|if|unless|case|while|until|for|begin)\b")
BLOCK_DO = re.compile(r"\bdo\b(?:\s*\|[^|]*\|)?\s*$")
CLOSER = re.compile(r"\bend\b")


def run_gate(ctx: Context) -> Result:
    started = time.time()
    coverage_path = ctx.work / COVERAGE_JSON
    if not coverage_path.exists():
        return Result(GATE, False, "no coverage data; rb.tests must run first", [], 0.0)
    coverage = json.loads(coverage_path.read_text())
    files = sources_in_scope(ctx)
    if not files:
        return Result.skipped(GATE, "no files in scope")
    code, output = scan(ctx, "complexity", files)
    if code != 0:
        return Result(GATE, False, "complexity scanner failed", output.splitlines()[-10:], time.time() - started)
    return crap_result(ctx, coverage, functions_in_scope(scanned(output), ctx), started)


def crap_result(ctx: Context, coverage: dict[str, Any], functions: list[dict[str, Any]], started: float) -> Result:
    limit = float(ctx.ruby("crap_max", 4))
    scored = [score(fn, coverage["files"].get(relative_path(fn["file"], ctx), {}), ctx) for fn in functions]
    offenders = above(scored, limit)
    summary = f"{len(scored)} methods, {len(offenders)} above CRAP {limit:g}"
    return Result(GATE, not offenders, summary, [describe(f) for f in offenders], time.time() - started)


def above(scored: list[dict[str, Any]], limit: float) -> list[dict[str, Any]]:
    return sorted((f for f in scored if f["crap"] > limit), key=lambda f: -f["crap"])


def sources_in_scope(ctx: Context) -> list[Path]:
    files = sources(ctx)
    if not ctx.scoped:
        return files
    return [path for path in files if ctx.in_scope(str(path.relative_to(ctx.root)))]


def functions_in_scope(functions: list[dict[str, Any]], ctx: Context) -> list[dict[str, Any]]:
    if not ctx.scoped:
        return functions
    return [fn for file, group in by_file(functions, ctx).items() for fn in touched(ctx, file, group)]


def touched(ctx: Context, file: str, group: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not ctx.in_scope(file):
        return []
    gated = ctx.gated_lines(file)
    return group if gated is None else touched_methods(ctx, file, group, gated)


def touched_methods(ctx: Context, file: str, group: list[dict[str, Any]], gated: set[int]) -> list[dict[str, Any]]:
    ends = method_ranges(file_text(ctx, file), [fn["line"] for fn in group])
    return [fn for fn in group if body_touched(fn["line"], ends.get(fn["line"], fn["line"]), gated)]


def by_file(functions: list[dict[str, Any]], ctx: Context) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
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


def boundary(ordered: list[int], index: int, total: int) -> int:
    return (ordered[index + 1] - 1) if index + 1 < len(ordered) else total


def method_ranges(text: str, starts: list[int]) -> dict[int, int]:
    deltas = [line_delta(line) for line in text.splitlines()]
    ordered = sorted(starts)
    return {start: method_end(deltas, start, boundary(ordered, index, len(deltas))) for index, start in enumerate(ordered) if start >= 1}


def method_end(deltas: list[int], start: int, limit: int) -> int:
    depth = 0
    for cursor in range(start - 1, len(deltas)):
        depth += deltas[cursor]
        if depth <= 0:
            return cursor + 1
    return max(start, min(limit, len(deltas)))


def opens(code: str) -> int:
    return 1 if OPENER.match(code) or BLOCK_DO.search(code) else 0


def line_delta(line: str) -> int:
    code = STRING.sub("", line).split("#", 1)[0]
    if code.lstrip().startswith("="):
        return 0
    return opens(code) - len(CLOSER.findall(code))


def recorded_hits(line: int, lines: list[Any]) -> int | None:
    if 1 <= line <= len(lines) and isinstance(lines[line - 1], int):
        hits: int = lines[line - 1]
        return hits
    return None


def line_covered(line: int, file_cov: dict[str, Any]) -> float:
    hits = recorded_hits(line, file_cov.get("lines") or [])
    if hits is None:
        return float(line not in (file_cov.get("missing_lines") or []))
    return float(hits > 0)


def score(fn: dict[str, Any], file_cov: dict[str, Any], ctx: Context) -> dict[str, Any]:
    complexity = fn["complexity"]
    line = fn["line"]
    covered = line_covered(line, file_cov)
    return {
        "file": relative_path(fn["file"], ctx),
        "line": line,
        "name": fn["name"],
        "cc": complexity,
        "cov": covered,
        "crap": complexity**2 * (1 - covered) ** 3 + complexity,
    }


def describe(f: dict[str, Any]) -> str:
    return f"{f['file']}:{f['line']} {f['name']} crap={f['crap']:.1f} (cc={f['cc']}, coverage={f['cov']:.0%})"
