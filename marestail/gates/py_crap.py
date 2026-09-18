import json
import time

from marestail.context import Context
from marestail.gates.py_tests import COVERAGE_JSON
from marestail.report import Result
from marestail.shell import run


def run_gate(ctx: Context) -> Result:
    started = time.time()
    coverage_path = ctx.work / COVERAGE_JSON
    if not coverage_path.exists():
        return Result("py.crap", False, "no coverage data; py.tests must run first", [], 0.0)
    code, output = run(radon_command(ctx), cwd=ctx.python_root())
    if code != 0:
        return Result("py.crap", False, "radon failed", output.splitlines()[-10:], time.time() - started)
    functions = scored(json.loads(output), json.loads(coverage_path.read_text()), ctx)
    limit = float(ctx.python("crap_max", 4))
    offenders = [f for f in functions if f["crap"] > limit]
    findings = [describe(f) for f in sorted(offenders, key=lambda f: -f["crap"])]
    scope = " on changed functions" if ctx.scoped else ""
    summary = f"{len(functions)} functions, {len(offenders)} above CRAP {limit:g}{scope}"
    return Result("py.crap", not offenders, summary, findings, time.time() - started)


def radon_command(ctx: Context) -> list[str]:
    sources = ctx.python("sources", ["."])
    return [ctx.python_bin("radon"), "cc", "-j", "-e", "mutants/*,.venv/*,__pycache__/*,perf/*", *sources]


def scored(radon: dict, coverage: dict, ctx: Context) -> list[dict]:
    result = []
    for file, blocks in radon.items():
        gated = scoped_lines(file, ctx)
        by_line = functions_by_line(coverage["files"].get(file, {}))
        for block in flatten(blocks):
            if gated is not None and not intersects(block, gated):
                continue
            covered = by_line.get(block["lineno"], 0.0)
            result.append(score(file, block, covered))
    return result


def scoped_lines(file: str, ctx: Context) -> set[int] | None:
    if not ctx.scoped:
        return None
    relative = (ctx.python_root() / file).resolve().relative_to(ctx.root)
    path = str(relative)
    if not ctx.in_scope(path):
        return set()
    return ctx.gated_lines(path)


def intersects(block: dict, lines: set[int]) -> bool:
    start = block["lineno"]
    return any(line in lines for line in range(start, block.get("endline", start) + 1))


def functions_by_line(file_coverage: dict) -> dict[int, float]:
    return {
        data["start_line"]: data["summary"]["percent_covered"] / 100 for name, data in file_coverage.get("functions", {}).items() if name
    }


def flatten(blocks: list[dict]) -> list[dict]:
    flat = []
    for block in blocks:
        if block["type"] in ("function", "method"):
            flat.append(block)
        flat.extend(flatten(block.get("methods", [])))
        flat.extend(flatten(block.get("closures", [])))
    return flat


def score(file: str, block: dict, covered: float) -> dict:
    complexity = block["complexity"]
    crap = complexity**2 * (1 - covered) ** 3 + complexity
    return {"file": file, "line": block["lineno"], "name": block["name"], "cc": complexity, "cov": covered, "crap": crap}


def describe(f: dict) -> str:
    return f"{f['file']}:{f['line']} {f['name']} crap={f['crap']:.1f} (cc={f['cc']}, coverage={f['cov']:.0%})"
