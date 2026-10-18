import json
import re
import time
from pathlib import Path
from typing import Any

from marestail import golang, jobs
from marestail.context import NO_CHANGED_MUTANTS, Context, MutationScope
from marestail.report import Result, elapsed, finding_order, mutation_summary
from marestail.shell import run, tail

GATE = "go.mutation"
REPORT = "go-mutation.json"
NOT_READY = "go toolchain not ready"
NO_MUTANTS = "no mutants were generated"
KILLED = {"KILLED", "TIMED OUT"}
IGNORED = {"NOT VIABLE", "SKIPPED"}
NOT_COVERED = "NOT COVERED"
MAX_LINES = 60
FLAGS = [
    "--arithmetic-base=true",
    "--conditionals-boundary=true",
    "--conditionals-negation=true",
    "--increment-decrement=true",
    "--invert-negatives=true",
    "--invert-logical=true",
    "--invert-loopctrl=true",
    "--invert-assignments=false",
    "--invert-bitwise=false",
    "--invert-bwassign=false",
    "--remove-self-assignments=false",
]


def run_gate(ctx: Context) -> Result:
    started = time.time()
    scope = ctx.mutation_files("go", ctx.go_root(), (".go",))
    if scope.mode == "error":
        return Result(GATE, False, scope.note, [], elapsed(started))
    return run_scope(ctx, scope, started)


def run_scope(ctx: Context, scope: MutationScope, started: float) -> Result:
    targets = mutation_targets(ctx, scope)
    if not targets:
        return Result.skipped(GATE, "no changed Go sources" if scope.files is not None else "no Go sources")
    problem = golang.preflight(ctx)
    if problem:
        return Result(GATE, False, NOT_READY, [problem], elapsed(started))
    return with_tool(ctx, targets, scope.note, started)


def mutation_targets(ctx: Context, scope: MutationScope) -> list[Path]:
    wanted = None if scope.files is None else set(scope.files)
    return [path for path in golang.sources(ctx) if wanted_source(ctx, path, wanted)]


def wanted_source(ctx: Context, path: Path, wanted: set[str] | None) -> bool:
    relative = golang.rel(ctx, path)
    if wanted is not None and relative not in wanted:
        return False
    return not golang.mutation_excluded(ctx, relative)


def with_tool(ctx: Context, targets: list[Path], note: str, started: float) -> Result:
    binary = golang.tool(ctx, "gremlins")
    if binary.startswith("gremlins "):
        return Result(GATE, False, "gremlins missing", [binary], elapsed(started))
    return mutate(ctx, binary, targets, note, started)


def mutate(ctx: Context, binary: str, targets: list[Path], note: str, started: float) -> Result:
    report = ctx.work / REPORT
    report.unlink(missing_ok=True)
    code, output = run(command(ctx, binary, targets), cwd=ctx.go_root(), timeout=int(ctx.go("mutation_timeout", 7200)))
    if not report.is_file():
        return Result(GATE, False, missing_report(code), tail(output), elapsed(started))
    return judged(ctx, mutants_of(json.loads(report.read_text())), note, started)


def command(ctx: Context, binary: str, targets: list[Path]) -> list[str]:
    return [
        binary,
        "unleash",
        "--config",
        "/dev/null",
        "--output",
        str(ctx.work / REPORT),
        *FLAGS,
        *worker_args(jobs.pick(ctx.config, "go", "mutation_workers", None)),
        *golang.tags(ctx, "--tags"),
        *exclusions(ctx, targets),
        ".",
    ]


def worker_args(count: int | None) -> list[str]:
    if count is None:
        return []
    return ["--workers", str(count)]


def exclusions(ctx: Context, targets: list[Path]) -> list[str]:
    return excluded_flags(sorted(outside_targets(ctx, targets)))


def outside_targets(ctx: Context, targets: list[Path]) -> list[str]:
    chosen = {path.resolve() for path in targets}
    return [rel_of(ctx, path) for path in golang.sources(ctx) if path.resolve() not in chosen]


def excluded_flags(relative: list[str]) -> list[str]:
    found: list[str] = []
    for item in relative:
        found.extend(flag_pair(item))
    return found


def flag_pair(item: str) -> list[str]:
    return ["-E", anchored(item)]


def rel_of(ctx: Context, path: Path) -> str:
    return path.resolve().relative_to(ctx.go_root().resolve()).as_posix()


def anchored(relative: str) -> str:
    return "^" + re.escape(relative) + "$"


def missing_report(code: int) -> str:
    return f"gremlins wrote no report (exit {code}); it writes none when it finds no mutants or its test run fails"


def mutants_of(data: dict[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for item in data.get("files") or []:
        found.extend(tagged(item))
    return found


def tagged(item: dict[str, Any]) -> list[dict[str, Any]]:
    name = str(item.get("file_name") or "")
    return [{**mutation, "file_name": name} for mutation in item.get("mutations") or []]


def judged(ctx: Context, mutants: list[dict[str, Any]], note: str, started: float) -> Result:
    viable = viable_mutants(mutants)
    if not viable:
        return Result(GATE, False, NO_MUTANTS, [], elapsed(started))
    return judged_viable(ctx, viable, note, started)


def viable_mutants(mutants: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [mutant for mutant in mutants if status(mutant) not in IGNORED]


def judged_viable(ctx: Context, viable: list[dict[str, Any]], note: str, started: float) -> Result:
    kept = ctx.on_changed_lines(viable, lambda mutant: describe(ctx, mutant))
    return survivor_result(ctx, kept, surviving(kept), note, started)


def surviving(kept: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [mutant for mutant in kept if status(mutant) not in KILLED]


def survivor_result(ctx: Context, kept: list[dict[str, Any]], survivors: list[dict[str, Any]], note: str, started: float) -> Result:
    if not survivors and ctx.hyper:
        return Result(GATE, True, NO_CHANGED_MUTANTS, [], elapsed(started))
    return capped_survivors(ctx, kept, survivors, note, started)


def capped_survivors(ctx: Context, kept: list[dict[str, Any]], survivors: list[dict[str, Any]], note: str, started: float) -> Result:
    findings = sorted((describe(ctx, mutant) for mutant in survivors), key=finding_order)
    return Result(GATE, not findings, mutation_summary(len(findings), len(kept), note), findings[:MAX_LINES], elapsed(started))


def status(mutant: dict[str, Any]) -> str:
    return str(mutant.get("status") or "")


def describe(ctx: Context, mutant: dict[str, Any]) -> str:
    where = f"{golang.rel(ctx, ctx.go_root() / str(mutant.get('file_name') or ''))}:{mutant.get('line')}"
    ending = "not covered by its package's tests" if status(mutant) == NOT_COVERED else "survived"
    return f"{where} {mutant.get('type')} at column {mutant.get('column')} {ending}"
