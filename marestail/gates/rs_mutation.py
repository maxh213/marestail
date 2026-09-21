import json
import shutil
import time
from pathlib import Path
from typing import Any

from marestail import rust
from marestail.context import Context
from marestail.report import Result, elapsed
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
        return Result(GATE, False, "cargo-mutants missing", [finding], elapsed(started))
    return None


def mutate(ctx: Context, files: list[Path], started: float) -> Result:
    clear_outcomes(ctx.work / OUTPUT)
    code, output = rust.cargo(ctx, command(ctx, files), timeout=int(ctx.rust("mutation_timeout", 7200)))
    report = ctx.work / OUTPUT / "outcomes.json"
    if not report.exists():
        return Result(GATE, False, f"cargo mutants produced no outcomes.json (exit {code})", tail(output), elapsed(started))
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
        *rust.listify(ctx.rust("mutation_args")),
    ]


IGNORE_MISSING = True


def clear_outcomes(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=IGNORE_MISSING)


def baseline_failed(outcomes: list[Outcome]) -> bool:
    baseline = next((o for o in outcomes if o.get("scenario") == "Baseline"), None)
    return bool(baseline and baseline.get("summary") != "Success")


def is_mutant(outcome: Outcome) -> bool:
    scenario = outcome.get("scenario")
    return isinstance(scenario, dict) and "Mutant" in scenario


def viable_mutants(outcomes: list[Outcome]) -> list[Outcome]:
    return [o for o in outcomes if is_mutant(o) and o.get("summary") != "Unviable"]


def survivors(viable: list[Outcome]) -> list[Outcome]:
    return [o for o in viable if o.get("summary") not in KILLED]


def mutation_summary(survived: list[Outcome], viable: list[Outcome]) -> str:
    return f"{len(survived)} of {len(viable)} mutants not killed" if survived else f"all {len(viable)} mutants killed"


def verdict(ctx: Context, report: dict[str, Any], output: str, started: float) -> Result:
    outcomes = report.get("outcomes", [])
    if baseline_failed(outcomes):
        return Result(GATE, False, "tests fail before any mutation", tail(output), elapsed(started))
    viable = viable_mutants(outcomes)
    if not viable:
        return Result(GATE, False, "no viable mutants were generated", tail(output), elapsed(started))
    survived = survivors(viable)
    findings = [describe(ctx, o) for o in survived]
    return Result(GATE, not survived, mutation_summary(survived, viable), findings[:MAX_FINDINGS], elapsed(started))


def describe(ctx: Context, outcome: Outcome) -> str:
    mutant = outcome["scenario"]["Mutant"]
    line = mutant.get("span", {}).get("start", {}).get("line", 0)
    function = (mutant.get("function") or {}).get("function_name", "?")
    change = after_colon(text_or_empty(mutant.get("name")), function)
    state = "survived" if outcome.get("summary") == "MissedMutant" else outcome.get("summary", "?")
    return f"{rust.rel(ctx, mutant['file'])}:{line} {function}: {change} {state}"


def text_or_empty(value: object) -> str:
    return value if isinstance(value, str) else ""


COLON_SPACE = ": "


def after_colon(name: str, function: str) -> str:
    rest = name
    if COLON_SPACE in name:
        rest = name[name.index(COLON_SPACE) + len(COLON_SPACE) :]
    return rest.removesuffix(f" in {function}")
