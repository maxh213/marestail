import json
import math
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from marestail import erlang
from marestail.context import Context, MutationScope
from marestail.report import Result, elapsed
from marestail.shell import tail

GATE = "er.mutation"
SCRIPT = "mutation.escript"
SCRATCH = "er-mutation"
MANIFEST = "mutants.json"
RESULTS = "er-mutation.json"
BUDGET_SECONDS = 7200
REMAINING_FLOOR = 120
GENERATE_TIMEOUT = 900
BASELINE_TIMEOUT = 1800
COMPILE_TIMEOUT = 300
MUTANTS_KEY = "mutants"
STATUS_KEY = "status"
FULL_MODE = "full"
NOTES_JOIN = "; "
REPORT_INDENT = 2
EXCLUDED = {"invalid", "skipped"}
NOT_CHECKED = "not checked"
SURVIVED = "survived"
SKIPPED = "skipped"
BASELINE_FAILURES = {1: "test suite fails on unmutated sources; fix the suite first"}
RUN_STATUS = {0: SURVIVED, 1: "killed", 124: "timeout"}
NOTES = ((NOT_CHECKED, "unchecked (time budget)"), ("invalid", "failed to compile"), (SKIPPED, "skipped by mutation_max"))


@dataclass
class Job:
    ctx: Context
    started: float
    scope: MutationScope
    sources: list[Path]
    mutate: list[Path]
    tests: list[Path] = field(default_factory=list)
    scratch: Path = field(default_factory=Path)
    baseline_seconds: float = 0.0

    @property
    def ebin_base(self) -> Path:
        return self.scratch / "ebin-base"

    @property
    def ebin_test(self) -> Path:
        return self.scratch / "ebin-test"

    def elapsed(self) -> float:
        return time.time() - self.started

    def fail(self, summary: str, findings: list[str]) -> Result:
        return Result(GATE, False, summary, findings, self.elapsed())


def run_gate(ctx: Context) -> Result:
    started = time.time()
    sources = erlang.source_files(ctx)
    if not sources:
        return Result.skipped(GATE, erlang.NO_SOURCES)
    scope = ctx.mutation_files("erlang", ctx.erlang_root(), (".erl",))
    if scope.mode == "error":
        return Result(GATE, False, scope.note, [], elapsed(started))
    return plan(Job(ctx, started, scope, sources, mutate_files(ctx, sources, scope.files)))


def plan(job: Job) -> Result:
    if job.scope.mode != FULL_MODE and not job.mutate:
        return Result.skipped(GATE, "no changed erlang sources")
    job.tests = erlang.test_files(job.ctx)
    if not job.tests:
        return job.fail("no eunit test files", [erlang.NO_TESTS])
    return generate(job)


def generate(job: Job) -> Result:
    job.scratch = erlang.fresh_dir(job.ctx.work / SCRATCH)
    code, output = erlang.escript(job.ctx, SCRIPT, ["mutants", str(job.scratch), *map(str, job.mutate)], timeout=GENERATE_TIMEOUT)
    failed = erlang.trouble(code, output, "mutant generation failed")
    if failed:
        return job.fail(*failed)
    manifest = job.scratch / MANIFEST
    if not manifest.exists():
        return job.fail("no mutant manifest written", tail(output))
    mutants = loaded_mutants(manifest)
    apply_cap(mutants, job.ctx)
    if not mutants:
        return nothing_to_mutate(job)
    return execute(job, mutants)


def loaded_mutants(manifest: Path) -> list[dict[str, Any]]:
    payload = json.loads(manifest.read_text())
    try:
        found = payload[MUTANTS_KEY]
    except KeyError:
        return []
    return list(found)


def nothing_to_mutate(job: Job) -> Result:
    where = " in the changed erlang sources" if job.scope.mode != FULL_MODE else " in the erlang sources"
    return job.fail("no mutants were generated", [f"no mutable comparison, arithmetic or boolean operators found{where}"])


def execute(job: Job, mutants: list[dict[str, Any]]) -> Result:
    failed = erlang.compile_with_tests(job.ctx, job.sources, job.tests, job.ebin_base, job.ebin_test)
    if failed:
        return job.fail(*failed)
    failed = baseline(job)
    if failed:
        return job.fail(*failed)
    run_mutants(job, mutants)
    write_report(job.ctx, mutants)
    return verdict(job, mutants)


def baseline(job: Job) -> tuple[str, list[str]] | None:
    began = time.time()
    base, test = str(job.ebin_base), str(job.ebin_test)
    code, output = erlang.escript(job.ctx, SCRIPT, ["run", base, base, test], timeout=BASELINE_TIMEOUT)
    job.baseline_seconds = time.time() - began
    return erlang.trouble(code, output, BASELINE_FAILURES.get(code, "eunit run failed on unmutated sources"))


def run_mutants(job: Job, mutants: list[dict[str, Any]]) -> None:
    per_mutant_timeout = max(60, min(1800, math.ceil(job.baseline_seconds * 10)))
    for mutant in mutants:
        if mutant.get("status") != SKIPPED:
            check_mutant(job, mutant, per_mutant_timeout)


def check_mutant(job: Job, mutant: dict[str, Any], per_mutant_timeout: int) -> None:
    remaining = BUDGET_SECONDS - job.elapsed()
    if remaining < REMAINING_FLOOR:
        mutant["status"] = NOT_CHECKED
        return
    run_mutant(job, mutant, min(per_mutant_timeout, math.ceil(remaining)))


def run_mutant(job: Job, mutant: dict[str, Any], timeout: int) -> None:
    ebin = erlang.fresh_dir(job.scratch / f"ebin-{mutant['id']}")
    include = str(Path(mutant["file"]).parent)
    code, _ = erlang.erlc(job.ctx, ["+debug_info", "-I", include, "-o", str(ebin), mutant["mutant"]], timeout=COMPILE_TIMEOUT)
    if code != 0:
        mutant["status"] = "invalid"
    else:
        code, _ = erlang.escript(job.ctx, SCRIPT, ["run", str(ebin), str(job.ebin_base), str(job.ebin_test)], timeout=timeout)
        mutant["status"] = RUN_STATUS.get(code, "error")
    remove_ebin(ebin)


def remove_ebin(ebin: Path) -> None:
    shutil.rmtree(ebin, ignore_errors=True)
    shutil.rmtree(ebin, ignore_errors=True)


def verdict(job: Job, mutants: list[dict[str, Any]]) -> Result:
    counted = len(runnable(mutants))
    if not counted:
        return job.fail(f"no runnable mutants: all {len(mutants)} failed to compile or were capped", [])
    findings = mutant_findings(job.ctx, mutants)
    survived = len(with_status(mutants, SURVIVED))
    base = f"{survived} of {counted} mutants not killed" if findings else f"all {counted} mutants killed"
    summary = base + notes_suffix(mutants) + (f" {job.scope.note}" if job.scope.note else "")
    return Result(GATE, not findings, summary, findings, job.elapsed())


def runnable(mutants: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [mutant for mutant in mutants if mutant.get("status") not in EXCLUDED]


def with_status(mutants: list[dict[str, Any]], status: str) -> list[dict[str, Any]]:
    return [mutant for mutant in mutants if mutant.get("status") == status]


def mutant_findings(ctx: Context, mutants: list[dict[str, Any]]) -> list[str]:
    survived = [describe(ctx, mutant, SURVIVED) for mutant in with_status(mutants, SURVIVED)]
    return survived + [describe(ctx, mutant, "not checked (time budget)") for mutant in with_status(mutants, NOT_CHECKED)]


def notes(mutants: list[dict[str, Any]]) -> list[str]:
    counts = [(len(with_status(mutants, status)), label) for status, label in NOTES]
    return [f"{count} {label}" for count, label in counts if count]


def notes_suffix(mutants: list[dict[str, Any]]) -> str:
    found = notes(mutants)
    return f" ({NOTES_JOIN.join(found)})" if found else ""


def mutate_files(ctx: Context, sources: list[Path], files: list[str] | None) -> list[Path]:
    if files is None:
        return sources
    wanted = set(files)
    return [path for path in sources if erlang.rel(ctx, path) in wanted]


def apply_cap(mutants: list[dict[str, Any]], ctx: Context) -> None:
    cap = mutation_cap(ctx)
    keep = kept_ids(mutants, cap) if cap > 0 else {mutant["id"] for mutant in mutants}
    mark_skipped(mutants, keep)


def mutation_cap(ctx: Context) -> int:
    value = ctx.erlang("mutation_max", 0)
    return int(value or 0)


def kept_ids(mutants: list[dict[str, Any]], cap: int) -> set[Any]:
    step = len(mutants) / cap
    return {mutants[int(index * step)]["id"] for index in range(cap)}


def mark_skipped(mutants: list[dict[str, Any]], keep: set[Any]) -> None:
    for mutant in mutants:
        if mutant["id"] not in keep:
            mutant["status"] = SKIPPED


def describe(ctx: Context, mutant: dict[str, Any], status: str) -> str:
    path = erlang.rel(ctx, mutant["file"])
    return f"{path}:{mutant['line']} {mutant['operator']} mutant {status}: {mutant['original']} -> {mutant['replacement']}"


def write_report(ctx: Context, mutants: list[dict[str, Any]]) -> None:
    entries = [
        {
            "file": erlang.rel(ctx, mutant["file"]),
            "line": mutant["line"],
            "operator": mutant["operator"],
            "original": mutant["original"],
            "replacement": mutant["replacement"],
            "status": mutant_status(mutant),
        }
        for mutant in mutants
    ]
    (ctx.work / RESULTS).write_text(json.dumps({MUTANTS_KEY: entries}, indent=REPORT_INDENT) + "\n")


def mutant_status(mutant: dict[str, Any]) -> str:
    return str(mutant[STATUS_KEY]) if STATUS_KEY in mutant else NOT_CHECKED
