import json
import shutil
import time

from marestail import rust
from marestail.context import Context
from marestail.report import Result
from marestail.shell import tail

OUTPUT = "mutants.out"
KILLED = {"CaughtMutant", "Timeout"}
MAX_FINDINGS = 60


def run_gate(ctx: Context) -> Result:
    started = time.time()
    files = rust.in_scope(ctx, rust.sources(ctx))
    if ctx.scope_changed and not files:
        return Result.skipped("rs.mutation", "no changed rust sources")
    code, output = rust.cargo(ctx, ["mutants", "--version"], timeout=60)
    problem = rust.missing(code, output, "mutants")
    if problem or code != 0:
        return Result(
            "rs.mutation",
            False,
            "cargo-mutants missing",
            [problem or f"cargo mutants --version failed: {output.strip()[-200:]}"],
            time.time() - started,
        )
    shutil.rmtree(ctx.work / OUTPUT, ignore_errors=True)
    code, output = rust.cargo(ctx, command(ctx, files), timeout=int(ctx.rust("mutation_timeout", 7200)))
    report = ctx.work / OUTPUT / "outcomes.json"
    if not report.exists():
        return Result("rs.mutation", False, f"cargo mutants produced no outcomes.json (exit {code})", tail(output), time.time() - started)
    return verdict(ctx, json.loads(report.read_text()), output, started)


def command(ctx: Context, files: list) -> list[str]:
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


def verdict(ctx: Context, report: dict, output: str, started: float) -> Result:
    outcomes = report.get("outcomes", [])
    baseline = next((o for o in outcomes if o.get("scenario") == "Baseline"), None)
    if baseline and baseline.get("summary") != "Success":
        return Result("rs.mutation", False, "tests fail before any mutation", tail(output), time.time() - started)
    mutants = [o for o in outcomes if isinstance(o.get("scenario"), dict) and "Mutant" in o["scenario"]]
    viable = [o for o in mutants if o.get("summary") != "Unviable"]
    if not viable:
        return Result("rs.mutation", False, "no viable mutants were generated", tail(output), time.time() - started)
    survivors = [o for o in viable if o.get("summary") not in KILLED]
    findings = [describe(ctx, o) for o in survivors]
    summary = f"{len(survivors)} of {len(viable)} mutants not killed" if survivors else f"all {len(viable)} mutants killed"
    return Result("rs.mutation", not survivors, summary, findings[:MAX_FINDINGS], time.time() - started)


def describe(ctx: Context, outcome: dict) -> str:
    mutant = outcome["scenario"]["Mutant"]
    line = mutant.get("span", {}).get("start", {}).get("line", 0)
    function = (mutant.get("function") or {}).get("function_name", "?")
    change = mutant.get("name", "").split(": ", 1)[-1].removesuffix(f" in {function}")
    state = "survived" if outcome.get("summary") == "MissedMutant" else outcome.get("summary", "?")
    return f"{rust.rel(ctx, mutant['file'])}:{line} {function}: {change} {state}"
