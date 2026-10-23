import time
from functools import partial
from typing import Any

from marestail import jobs
from marestail.context import NO_CHANGED_MUTANTS, Context, MutationScope
from marestail.gates._stryker import (
    COMMAND_SUFFIXES,
    KILLED,
    RUN_SECONDS,
    TS_SUFFIXES,
    Placed,
    Proof,
    all_mutants,
    command_proof,
    execute,
)
from marestail.gates._stryker import mutation_targets as mutation_targets
from marestail.javascript import config_flag, tool
from marestail.report import Result, elapsed

REPORT = "reports/mutation/mutation.json"
INCREMENTAL = "reports/stryker-incremental.json"
TEMP_DIR = ".stryker-tmp"
BAD = {"Survived", "NoCoverage", "RuntimeError"}
TIMED_OUT = "Timeout"
GATE = "ts.mutation"
PROOF = "(proof: mutation via [hyper] test_cmd)"
HINT = (
    "hint: no mutant was killed; a test that loads code with vm must pass process into the sandbox, "
    "or no assertion depends on the changed lines"
)
EMPTY = ""


def run_gate(ctx: Context) -> Result:
    started = time.time()
    scope = ctx.mutation_files("ts", ctx.ts_root(), suffixes(ctx))
    if scope.mode == "error":
        return Result(GATE, False, scope.note, [], elapsed(started))
    return mutated(ctx, scope, started)


def mutated(ctx: Context, scope: MutationScope, started: float) -> Result:
    mutate = mutation_targets(ctx, scope.files or [])
    if scope.mode != "full" and not mutate:
        return Result.skipped(GATE, "no changed typescript sources")
    return mutation_run(ctx, scope, mutate, started)


def mutation_run(ctx: Context, scope: MutationScope, mutate: list[str], started: float) -> Result:
    if ctx.test_cmd:
        return proven(ctx, command_proof(ctx, mutate), started)
    return stryker_result(ctx, scope, mutate, started)


def suffixes(ctx: Context) -> tuple[str, ...]:
    return COMMAND_SUFFIXES if ctx.test_cmd else TS_SUFFIXES


def proven(ctx: Context, proof: Proof, started: float) -> Result:
    if proof.failed:
        return proof.failure(GATE, started)
    return proof_verdict(ctx, placed(proof.report or {}, ctx), started)


def proof_verdict(ctx: Context, mutants: list[Placed], started: float) -> Result:
    if not mutants:
        return Result(GATE, True, NO_CHANGED_MUTANTS, [], elapsed(started))
    result = judged(ctx, mutants, PROOF, started)
    result.findings = hint(mutants, result.findings) + result.findings
    return result


def hint(mutants: list[Placed], survivors: list[str]) -> list[str]:
    killed = any(mutant["status"] == KILLED for _, mutant in mutants)
    return [HINT] if survivors and not killed else []


def stryker_result(ctx: Context, scope: MutationScope, mutate: list[str], started: float) -> Result:
    pull = tuple(str((ctx.ts_root() / path).relative_to(ctx.root)) for path in (REPORT, INCREMENTAL))
    build = partial(mutation_command, ctx, mutate)
    proof = execute(ctx, build, ctx.ts_root() / TEMP_DIR, ctx.ts_root() / REPORT, GATE, local_concurrency(ctx), pull)
    if proof.report is None:
        return proof.failure(GATE, started)
    return verdict(ctx, proof.report, (scope.note + proof.where).strip(), started)


def verdict(ctx: Context, report: dict[str, Any], note: str, started: float) -> Result:
    mutants = placed(report, ctx)
    if ctx.hyper and not mutants:
        return Result(GATE, True, NO_CHANGED_MUTANTS, [], elapsed(started))
    return judged(ctx, mutants, note, started)


def judged(ctx: Context, mutants: list[Placed], note: str, started: float) -> Result:
    strict = ctx.timeouts_fail("ts")
    survivors = survivors_of(mutants, strict)
    timed_out = 0 if strict else sum(1 for _, mutant in mutants if mutant["status"] == TIMED_OUT)
    return survivor_result(survivors, note, started, timed_out)


def survivor_result(survivors: list[str], note: str, started: float, timed_out: int = 0) -> Result:
    summary = f"{len(survivors)} surviving mutants" if survivors else "all mutants killed"
    summary += f" ({timed_out} killed by timeout)" if timed_out else ""
    summary += f" {note}" if note else ""
    return Result(GATE, not survivors, summary, survivors, elapsed(started))


def mutation_command(ctx: Context, mutate: list[str], workers: int) -> list[str]:
    command = [
        *tool(ctx, "stryker"),
        "run",
        *config_flag(ctx, "stryker.config.json"),
        "--reporters",
        "json,progress",
        "--tempDirName",
        TEMP_DIR,
        "--cleanTempDir",
        "always",
        "--dryRunTimeoutMinutes",
        str(RUN_SECONDS // 60),
    ]
    if workers:
        command += ["--concurrency", str(workers)]
    if ctx.ts("mutation_incremental"):
        command += ["--incremental", "--incrementalFile", INCREMENTAL]
    if mutate:
        command += ["--mutate", ",".join(mutate)]
    return command


def local_concurrency(ctx: Context) -> int:
    shared = jobs.run_jobs(ctx.config)
    if shared is not None:
        return shared
    configured = ctx.ts("mutation_concurrency")
    return int(configured) if configured else 0


def placed(report: dict[str, Any], ctx: Context) -> list[Placed]:
    found = [(name, mutant) for name, mutant in all_mutants(report, ctx) if ctx.in_scope(name)]
    return ctx.on_changed_lines(found, where)


def where(entry: Placed) -> str:
    return f"{entry[0]}:{entry[1]['location']['start']['line']}"


def survivors_of(mutants: list[Placed], strict: bool = False) -> list[str]:
    return [survivor(name, mutant) for name, mutant in mutants if failed(mutant, strict)]


def failed(mutant: dict[str, Any], strict: bool) -> bool:
    return mutant["status"] in BAD or (strict and mutant["status"] == TIMED_OUT)


def replacement_text(mutant: dict[str, Any]) -> str:
    if "replacement" not in mutant:
        return EMPTY
    return str(mutant["replacement"])[:60]


def survivor(name: str, mutant: dict[str, Any]) -> str:
    line = mutant["location"]["start"]["line"]
    return f"{name}:{line} {mutant['mutatorName']} {mutant['status']}: {replacement_text(mutant)}"
