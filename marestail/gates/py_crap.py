import fnmatch
import json
import time
import tomllib
from pathlib import Path
from typing import Any

from marestail.context import Context
from marestail.gates.py_tests import COVERAGE_JSON, scoped_lines
from marestail.report import Result
from marestail.shell import run

GATE = "py.crap"

Block = dict[str, Any]


def run_gate(ctx: Context) -> Result:
    started = time.time()
    coverage_path = ctx.work / COVERAGE_JSON
    if not coverage_path.exists():
        return Result(GATE, False, "no coverage data; py.tests must run first", [], 0.0)
    code, output = run(radon_command(ctx), cwd=ctx.python_root())
    if code != 0:
        return Result(GATE, False, "radon failed", output.splitlines()[-10:], time.time() - started)
    functions = scored(json.loads(output), json.loads(coverage_path.read_text()), ctx)
    limit = float(ctx.python("crap_max", 4))
    offenders = above(functions, limit)
    findings = list(map(describe, offenders))
    scope = " on changed functions" if ctx.scoped else ""
    summary = f"{len(functions)} functions, {len(offenders)} above CRAP {limit:g}{scope}"
    return Result(GATE, not offenders, summary, findings, time.time() - started)


def above(functions: list[Block], limit: float) -> list[Block]:
    return sorted((f for f in functions if f["crap"] > limit), key=crap_descending)


def crap_descending(function: Block) -> float:
    return -float(function["crap"])


def radon_command(ctx: Context) -> list[str]:
    sources = ctx.python("sources", ["."])
    return [ctx.python_bin("radon"), "cc", "-j", "-e", "mutants/*,.venv/*,__pycache__/*,perf/*", *sources]


def scored(radon: dict[str, list[Block]], coverage: dict[str, Any], ctx: Context) -> list[Block]:
    omitted = coverage_omits(ctx)
    result = []
    for file, blocks in radon.items():
        result.extend(scored_unless_omitted(file, blocks, coverage, ctx, omitted))
    return result


def scored_unless_omitted(file: str, blocks: list[Block], coverage: dict[str, Any], ctx: Context, omitted: list[str]) -> list[Block]:
    if omitted_file(file, omitted):
        return []
    return scored_file(file, blocks, coverage["files"].get(file, {}), scoped_lines(file, ctx))


def coverage_omits(ctx: Context) -> list[str]:
    return as_patterns(run_section(ctx).get("omit", []))


def run_section(ctx: Context) -> dict[str, Any]:
    return mapping(mapping(mapping(parsed_toml(ctx.root / "pyproject.toml"), "tool"), "coverage"), "run")


def parsed_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            loaded: Any = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    return as_table(loaded)


def as_table(loaded: Any) -> dict[str, Any]:
    return loaded if isinstance(loaded, dict) else {}


def mapping(data: Any, key: str) -> dict[str, Any]:
    value = data.get(key) if isinstance(data, dict) else None
    return value if isinstance(value, dict) else {}


def as_patterns(omit: Any) -> list[str]:
    return list(map(str, omit)) if isinstance(omit, list) else listed_omit(omit)


def listed_omit(omit: Any) -> list[str]:
    return [omit] if isinstance(omit, str) else []


def omitted_file(file: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(file, pattern) for pattern in patterns)


def scored_file(file: str, blocks: list[Block], file_coverage: dict[str, Any], gated: set[int] | None) -> list[Block]:
    by_line = functions_by_line(file_coverage)
    return [score(file, block, by_line.get(block["lineno"], 0.0)) for block in flatten(blocks) if gated_block(block, gated)]


def gated_block(block: Block, gated: set[int] | None) -> bool:
    return gated is None or intersects(block, gated)


def intersects(block: Block, lines: set[int]) -> bool:
    start: int = block["lineno"]
    return any(line in lines for line in range(start, block.get("endline", start) + 1))


def functions_by_line(file_coverage: dict[str, Any]) -> dict[int, float]:
    return {
        data["start_line"]: data["summary"]["percent_covered"] / 100 for name, data in file_coverage.get("functions", {}).items() if name
    }


def flatten(blocks: list[Block]) -> list[Block]:
    flat = []
    for block in blocks:
        if block["type"] in ("function", "method"):
            flat.append(block)
        flat.extend(flatten(block.get("methods", [])))
        flat.extend(flatten(block.get("closures", [])))
    return flat


def score(file: str, block: Block, covered: float) -> Block:
    complexity = block["complexity"]
    crap = complexity**2 * (1 - covered) ** 3 + complexity
    return {"file": file, "line": block["lineno"], "name": block["name"], "cc": complexity, "cov": covered, "crap": crap}


def describe(f: Block) -> str:
    return f"{f['file']}:{f['line']} {f['name']} crap={f['crap']:.1f} (cc={f['cc']}, coverage={f['cov']:.0%})"
