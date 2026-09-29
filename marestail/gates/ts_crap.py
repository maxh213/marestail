import json
import re
import time
from pathlib import Path
from typing import Any

from marestail import javascript
from marestail.context import Context
from marestail.gates import ts_mutation
from marestail.gates._coverage import TS_COVERAGE_DIR
from marestail.gates._crap import DEFAULT as DEFAULT
from marestail.gates._crap import KEY as KEY
from marestail.gates._crap import above as above
from marestail.gates._crap import crap_score
from marestail.gates._crap import describe as describe
from marestail.gates._hyper_crap import Hyper, changed_in, innermost, judged, unit
from marestail.report import Result, elapsed
from marestail.shell import tail

GATE = "ts.crap"
IGNORED_LINE = re.compile(r"^[\s{}()\[\];,]*$")
UNPROVABLE = "{file}:{line} not provable by mutation"
ONLY_KILLED = {ts_mutation.KILLED}


def run_gate(ctx: Context) -> Result:
    started = time.time()
    if ctx.test_cmd:
        return mutation_crap(ctx, started)
    return coverage_crap(ctx, started)


def coverage_crap(ctx: Context, started: float) -> Result:
    coverage_path = ctx.work / TS_COVERAGE_DIR / "coverage-final.json"
    if not coverage_path.exists():
        return Result(GATE, False, "no coverage data; ts.tests must run first")
    coverage = json.loads(coverage_path.read_text())
    files = scoped_files(coverage, ctx)
    if not files:
        return Result.skipped(GATE, "no files in scope")
    code, output = javascript.scan(ctx, "complexity", files)
    if code != 0:
        return complexity_failed(output, started)
    return crap_result(ctx, coverage, json.loads(output), started)


def complexity_failed(output: str, started: float) -> Result:
    return Result(GATE, False, "complexity script failed", output.splitlines()[-10:], elapsed(started))


def scoped_files(coverage: dict[str, Any], ctx: Context) -> list[str]:
    return [file for file in coverage if ctx.in_scope(javascript.rel(file, ctx))]


def crap_result(ctx: Context, coverage: dict[str, Any], parsed: list[dict[str, Any]], started: float) -> Result:
    if ctx.hyper:
        return judged(ctx, hyper_rules(ctx), hyper_units(coverage, parsed, ctx), started)
    limit = crap_limit(ctx)
    functions = touched_scores(ctx, coverage, parsed)
    worst = above(functions, limit)
    summary = f"{len(functions)} functions, {len(worst)} above CRAP {limit:g}"
    return Result(GATE, not worst, summary, [describe(fn, "complexity") for fn in worst], elapsed(started))


def touched_scores(ctx: Context, coverage: dict[str, Any], parsed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [score(fn, coverage[fn["file"]], ctx) for fn in parsed if touches_hunk(fn, ctx)]


def touches_hunk(fn: dict[str, Any], ctx: Context) -> bool:
    gated = ctx.gated_lines(javascript.rel(fn["file"], ctx))
    if gated is None:
        return True
    return any(fn["line"] <= line <= fn["endLine"] for line in gated)


def score(fn: dict[str, Any], data: dict[str, Any], ctx: Context) -> dict[str, Any]:
    covered = function_coverage(fn, data)
    complexity = fn["complexity"]
    crap = crap_score(complexity, covered)
    return {**fn, "file": javascript.rel(fn["file"], ctx), "cov": covered, "crap": crap}


def function_coverage(fn: dict[str, Any], data: dict[str, Any]) -> float:
    return ratio(statement_hits(fn, data) + arm_hits(fn, data))


def inside(fn: dict[str, Any], loc: dict[str, Any]) -> bool:
    return bool(fn["line"] <= loc["start"]["line"] <= fn["endLine"])


def statement_hits(fn: dict[str, Any], data: dict[str, Any]) -> list[int]:
    return [hits for key, hits in data["s"].items() if inside(fn, data["statementMap"][key])]


def arm_hits(fn: dict[str, Any], data: dict[str, Any]) -> list[int]:
    return [hit for key, hits in data["b"].items() if inside(fn, data["branchMap"][key]["loc"]) for hit in hits]


def ratio(hits: list[int]) -> float:
    if not hits:
        return 1.0
    return sum(1 for hit in hits if hit > 0) / len(hits)


def hyper_units(coverage: dict[str, Any], parsed: list[dict[str, Any]], ctx: Context) -> list[dict[str, Any]]:
    return [{**ts_unit(javascript.rel(fn["file"], ctx), fn), **covered_by(fn, coverage[fn["file"]])} for fn in parsed]


def ts_unit(file: str, fn: dict[str, Any]) -> dict[str, Any]:
    return unit(file, fn, fn["line"], fn["endLine"])


def covered_by(fn: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    return {"cov": function_coverage(fn, data), "missing": missed_lines(data)}


def missed_lines(data: dict[str, Any]) -> set[int]:
    return {data["statementMap"][key]["start"]["line"] for key, hits in data["s"].items() if hits == 0}


def base_units(ctx: Context, copy: Path) -> list[dict[str, Any]] | None:
    code, output = javascript.scan(ctx, "complexity", [copy])
    if code != 0:
        return None
    return [ts_unit("", fn) for fn in json.loads(output)]


def crap_limit(ctx: Context) -> float:
    return float(ctx.ts(KEY, DEFAULT))


def hyper_rules(ctx: Context) -> Hyper:
    return Hyper(GATE, crap_limit(ctx), lambda copy: base_units(ctx, copy))


def mutation_crap(ctx: Context, started: float) -> Result:
    mutate = ts_mutation.command_targets(ctx)
    if not mutate:
        return Result.skipped(GATE, "no files in scope")
    proof = ts_mutation.command_proof(ctx, mutate)
    if proof.failed:
        return Result(GATE, False, ts_mutation.no_report(proof.code), tail(proof.output), elapsed(started))
    return scanned_crap(ctx, mutated_files(mutate), proof, started)


def mutated_files(mutate: list[str]) -> list[str]:
    return sorted({target.rsplit(":", 1)[0] for target in mutate})


def scanned_crap(ctx: Context, files: list[str], proof: ts_mutation.Proof, started: float) -> Result:
    code, output = javascript.scan(ctx, "complexity", files)
    if code != 0:
        return complexity_failed(output, started)
    statuses = ts_mutation.line_statuses(proof.report or {}, ctx)
    functions = [proved(ctx, ts_unit(javascript.rel(fn["file"], ctx), fn), statuses) for fn in json.loads(output)]
    result = judged(ctx, hyper_rules(ctx), functions, started)
    return with_unprovable(result, unprovable(ctx, functions))


def proved(ctx: Context, fn: dict[str, Any], statuses: dict[str, dict[int, set[str]]]) -> dict[str, Any]:
    lines = provable_lines(ctx, fn)
    by_line = statuses.get(fn["file"], {})
    return {**fn, **line_proof(covered_lines(lines, by_line), missing_lines(lines, by_line), lines)}


def covered_lines(lines: set[int], by_line: dict[int, set[str]]) -> set[int]:
    return {line for line in lines if by_line.get(line) == ONLY_KILLED}


def missing_lines(lines: set[int], by_line: dict[int, set[str]]) -> set[int]:
    return {line for line in lines if by_line.get(line, ONLY_KILLED) - ONLY_KILLED}


def line_proof(covered: set[int], missing: set[int], lines: set[int]) -> dict[str, Any]:
    proven = covered | missing
    return {"cov": len(covered) / len(proven) if proven else 1.0, "missing": missing, "scored": bool(proven), "unprovable": lines - proven}


def provable_lines(ctx: Context, fn: dict[str, Any]) -> set[int]:
    text = (ctx.root / fn["file"]).read_text().splitlines()
    return {line for line in changed_in(fn, ctx) if not IGNORED_LINE.match(text[line - 1])}


def unprovable(ctx: Context, functions: list[dict[str, Any]]) -> list[str]:
    places = {(fn["file"], line) for fn in innermost(functions, ctx) for line in fn["unprovable"]}
    return [UNPROVABLE.format(file=file, line=line) for file, line in sorted(places)]


def with_unprovable(result: Result, findings: list[str]) -> Result:
    if findings:
        result.summary += f"; {len(findings)} changed lines not provable by mutation"
        result.findings += findings
    return result
