import time
from typing import Any

from marestail.context import NO_CHANGED_MUTANTS, Context, MutationScope
from marestail.gates._stryker import (
    COMMAND_SUFFIXES,
    KILLED,
    TS_SUFFIXES,
    Placed,
    Proof,
    all_mutants,
    command_proof,
    execute,
    mutation_targets,
)
from marestail.javascript import config_flag, tool
from marestail.report import Result, elapsed

REPORT = "reports/mutation/mutation.json"
TEMP_DIR = ".stryker-tmp"
BAD = {"Survived", "NoCoverage", "Timeout", "RuntimeError", "CompileError"}
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
    return stryker_result(ctx, scope, mutation_command(ctx, mutate), started)


def suffixes(ctx: Context) -> tuple[str, ...]:
    return COMMAND_SUFFIXES if ctx.test_cmd else TS_SUFFIXES


def proven(ctx: Context, proof: Proof, started: float) -> Result:
    if proof.failed:
        return proof.failure(GATE, started)
    return proof_verdict(placed(proof.report or {}, ctx), started)


def proof_verdict(mutants: list[Placed], started: float) -> Result:
    if not mutants:
        return Result(GATE, True, NO_CHANGED_MUTANTS, [], elapsed(started))
    result = survivor_result(survivors_of(mutants), PROOF, started)
    result.findings = hint(mutants, result.findings) + result.findings
    return result


def hint(mutants: list[Placed], survivors: list[str]) -> list[str]:
    killed = any(mutant["status"] == KILLED for _, mutant in mutants)
    return [HINT] if survivors and not killed else []


def stryker_result(ctx: Context, scope: MutationScope, command: list[str], started: float) -> Result:
    proof = execute(ctx, command, ctx.ts_root() / TEMP_DIR, ctx.ts_root() / REPORT)
    if proof.report is None:
        return proof.failure(GATE, started)
    return verdict(ctx, proof.report, scope.note, started)


def verdict(ctx: Context, report: dict[str, Any], note: str, started: float) -> Result:
    mutants = placed(report, ctx)
    if ctx.hyper and not mutants:
        return Result(GATE, True, NO_CHANGED_MUTANTS, [], elapsed(started))
    return survivor_result(survivors_of(mutants), note, started)


def survivor_result(survivors: list[str], note: str, started: float) -> Result:
    summary = f"{len(survivors)} surviving mutants" if survivors else "all mutants killed"
    summary += f" {note}" if note else ""
    return Result(GATE, not survivors, summary, survivors, elapsed(started))


def mutation_command(ctx: Context, mutate: list[str]) -> list[str]:
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
    ]
    if mutate:
        command += ["--mutate", ",".join(mutate)]
    return command


def placed(report: dict[str, Any], ctx: Context) -> list[Placed]:
    found = [(name, mutant) for name, mutant in all_mutants(report, ctx) if ctx.in_scope(name)]
    return ctx.on_changed_lines(found, where)


def where(entry: Placed) -> str:
    return f"{entry[0]}:{entry[1]['location']['start']['line']}"


def survivors_of(mutants: list[Placed]) -> list[str]:
    return [survivor(name, mutant) for name, mutant in mutants if mutant["status"] in BAD]


def replacement_text(mutant: dict[str, Any]) -> str:
    if "replacement" not in mutant:
        return EMPTY
    return str(mutant["replacement"])[:60]


def survivor(name: str, mutant: dict[str, Any]) -> str:
    line = mutant["location"]["start"]["line"]
    return f"{name}:{line} {mutant['mutatorName']} {mutant['status']}: {replacement_text(mutant)}"
