import json
import shutil
import time
from pathlib import Path
from typing import Any

from marestail import rust
from marestail.context import Context
from marestail.report import Result
from marestail.shell import tail

GATE = "rs.mutation"
OUTPUT = "mutants.out"
KILLED = {"CaughtMutant", "Timeout"}
MAX_FINDINGS = 60

Outcome = dict[str, Any]


def run_gate(ctx: Context) -> Result:
    started = time.time()
    files = rust.in_scope(ctx, rust.sources(ctx))
    if ctx.scope_changed and not files:
        return Result.skipped(GATE, "no changed rust sources")
    return mutants_problem(ctx, started) or mutate(ctx, files, started)


def mutants_problem(ctx: Context, started: float) -> Result | None:
    code, output = rust.cargo(ctx, ["mutants", "--version"], timeout=60)
    problem = rust.missing(code, output, "mutants")
    if problem or code != 0:
        finding = problem or f"cargo mutants --version failed: {output.strip()[-200:]}"
        return Result(GATE, False, "cargo-mutants missing", [finding], time.time() - started)
    return None


def mutate(ctx: Context, files: list[Path], started: float) -> Result:
    shutil.rmtree(ctx.work / OUTPUT, ignore_errors=True)
    code, output = rust.cargo(ctx, command(ctx, files), timeout=int(ctx.rust("mutation_timeout", 7200)))
    report = ctx.work / OUTPUT / "outcomes.json"
    if not report.exists():
        return Result(GATE, False, f"cargo mutants produced no outcomes.json (exit {code})", tail(output), time.time() - started)
    return verdict(ctx, json.loads(report.read_text()), output, started)


def command(ctx: Context, files: list[Path]) -> list[str]:
    scoped = [arg for path in files for arg in ("--file", str(path.relative_to(ctx.rust_root())))] if ctx.scope_changed else []
    jobs = str(ctx.rust("mutation_jobs", 2))
    return [
        "mutants",
        "--output",
        str(ctx.work),
        "--no-shuffle",
        "--colors",
        "never",
        "--jobs",
        jobs,
        *scoped,
        *rust.listify(ctx.rust("mutation_args", [])),
    ]


def baseline_failed(outcomes: list[Outcome]) -> bool:
    baseline = next((o for o in outcomes if o.get("scenario") == "Baseline"), None)
    return bool(baseline and baseline.get("summary") != "Success")


def is_mutant(outcome: Outcome) -> bool:
    return isinstance(outcome.get("scenario"), dict) and "Mutant" in outcome["scenario"]


def viable_mutants(outcomes: list[Outcome]) -> list[Outcome]:
    return [o for o in outcomes if is_mutant(o) and o.get("summary") != "Unviable"]


def survivors(viable: list[Outcome]) -> list[Outcome]:
    return [o for o in viable if o.get("summary") not in KILLED]


def mutation_summary(survived: list[Outcome], viable: list[Outcome]) -> str:
    return f"{len(survived)} of {len(viable)} mutants not killed" if survived else f"all {len(viable)} mutants killed"


def verdict(ctx: Context, report: dict[str, Any], output: str, started: float) -> Result:
    outcomes = report.get("outcomes", [])
    if baseline_failed(outcomes):
        return Result(GATE, False, "tests fail before any mutation", tail(output), time.time() - started)
    viable = viable_mutants(outcomes)
    if not viable:
        return Result(GATE, False, "no viable mutants were generated", tail(output), time.time() - started)
    survived = survivors(viable)
    findings = [describe(ctx, o) for o in survived]
    return Result(GATE, not survived, mutation_summary(survived, viable), findings[:MAX_FINDINGS], time.time() - started)


def describe(ctx: Context, outcome: Outcome) -> str:
    mutant = outcome["scenario"]["Mutant"]
    line = mutant.get("span", {}).get("start", {}).get("line", 0)
    function = (mutant.get("function") or {}).get("function_name", "?")
    change = mutant.get("name", "").split(": ", 1)[-1].removesuffix(f" in {function}")
    state = "survived" if outcome.get("summary") == "MissedMutant" else outcome.get("summary", "?")
    return f"{rust.rel(ctx, mutant['file'])}:{line} {function}: {change} {state}"
