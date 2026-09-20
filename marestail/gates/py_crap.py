import json
import time
from typing import Any

from marestail.context import Context
from marestail.gates._coverage import PY_COVERAGE
from marestail.gates._coverage import scoped_lines as scoped_lines
from marestail.gates._crap import DEFAULT as DEFAULT
from marestail.gates._crap import KEY as KEY
from marestail.gates._crap import above as above
from marestail.gates._crap import describe as describe
from marestail.report import Result, elapsed
from marestail.shell import run

COVERAGE_JSON = PY_COVERAGE
GATE = "py.crap"

Block = dict[str, Any]


def run_gate(ctx: Context) -> Result:
    started = time.time()
    coverage_path = ctx.work / COVERAGE_JSON
    if not coverage_path.exists():
        return Result(GATE, False, "no coverage data; py.tests must run first", [])
    code, output = run(radon_command(ctx), cwd=ctx.python_root())
    if code != 0:
        return Result(GATE, False, "radon failed", output.splitlines()[-10:], elapsed(started))
    functions = scored(json.loads(output), json.loads(coverage_path.read_text()), ctx)
    limit = float(ctx.python(KEY, DEFAULT))
    offenders = above(functions, limit)
    findings = list(map(describe, offenders))
    scope = " on changed functions" if ctx.scoped else ""
    summary = f"{len(functions)} functions, {len(offenders)} above CRAP {limit:g}{scope}"
    return Result(GATE, not offenders, summary, findings, elapsed(started))


def radon_command(ctx: Context) -> list[str]:
    sources = ctx.python("sources", ["."])
    return [ctx.python_bin("radon"), "cc", "-j", "-e", "mutants/*,.venv/*,__pycache__/*,perf/*", *sources]


def scored(radon: dict[str, list[Block]], coverage: dict[str, Any], ctx: Context) -> list[Block]:
    result = []
    for file, blocks in radon.items():
        result.extend(scored_file(file, blocks, coverage["files"].get(file, {}), scoped_lines(file, ctx)))
    return result


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
