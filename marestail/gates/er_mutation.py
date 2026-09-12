import json
import math
import shutil
import time
from pathlib import Path

from marestail import erlang
from marestail.context import Context
from marestail.report import Result
from marestail.shell import tail

SCRATCH = "er-mutation"
MANIFEST = "mutants.json"
RESULTS = "er-mutation.json"
BUDGET_SECONDS = 7200
EXCLUDED = {"invalid", "skipped"}


def run_gate(ctx: Context) -> Result:
    started = time.time()
    sources = erlang.source_files(ctx)
    if not sources:
        return Result.skipped("er.mutation", "no erlang sources under [erlang] sources (default src/)")
    mutate = sources
    if ctx.scope_changed:
        changed = set(ctx.changed_under(ctx.erlang_root(), (".erl",)))
        mutate = [path for path in sources if erlang.rel(ctx, path) in changed]
        if not mutate:
            return Result.skipped("er.mutation", "no changed erlang sources")
    tests = erlang.test_files(ctx)
    if not tests:
        return Result("er.mutation", False, "no eunit test files", ["marestail.toml:1 no test files under [erlang] test_dirs (default test/, tests/) or *_tests.erl next to the sources"], time.time() - started)
    scratch = erlang.fresh_dir(ctx.work / SCRATCH)
    code, output = erlang.escript(ctx, "mutation.escript", ["mutants", str(scratch), *map(str, mutate)], timeout=900)
    problem = erlang.hint(code, output)
    if problem:
        return Result("er.mutation", False, problem, [problem], time.time() - started)
    if code != 0:
        return Result("er.mutation", False, "mutant generation failed", tail(output), time.time() - started)
    manifest = scratch / MANIFEST
    if not manifest.exists():
        return Result("er.mutation", False, "no mutant manifest written", tail(output), time.time() - started)
    mutants = json.loads(manifest.read_text()).get("mutants", [])
    if not mutants:
        where = " in the changed erlang sources" if ctx.scope_changed else " in the erlang sources"
        return Result("er.mutation", False, "no mutants were generated", [f"no mutable comparison, arithmetic or boolean operators found{where}"], time.time() - started)
    apply_cap(mutants, ctx)
    ebin_base = scratch / "ebin-base"
    ebin_base.mkdir()
    code, output = erlang.erlc(ctx, ["+debug_info", "-o", str(ebin_base), *map(str, sources)], timeout=900)
    problem = erlang.hint(code, output)
    if problem:
        return Result("er.mutation", False, problem, [problem], time.time() - started)
    if code != 0:
        return Result("er.mutation", False, "sources failed to compile", tail(output), time.time() - started)
    ebin_test = scratch / "ebin-test"
    ebin_test.mkdir()
    code, output = erlang.erlc(ctx, ["-DTEST", "+debug_info", "-pa", str(ebin_base), "-o", str(ebin_test), *map(str, tests)], timeout=900)
    if code != 0:
        return Result("er.mutation", False, "tests failed to compile", tail(output), time.time() - started)
    baseline_started = time.time()
    code, output = erlang.escript(ctx, "mutation.escript", ["run", str(ebin_base), str(ebin_base), str(ebin_test)], timeout=1800)
    baseline_seconds = time.time() - baseline_started
    problem = erlang.hint(code, output)
    if problem:
        return Result("er.mutation", False, problem, [problem], time.time() - started)
    if code == 1:
        return Result("er.mutation", False, "test suite fails on unmutated sources; fix the suite first", tail(output), time.time() - started)
    if code != 0:
        return Result("er.mutation", False, "eunit run failed on unmutated sources", tail(output), time.time() - started)
    per_mutant_timeout = max(60, min(1800, math.ceil(baseline_seconds * 10)))
    for mutant in mutants:
        if mutant.get("status") == "skipped":
            continue
        remaining = BUDGET_SECONDS - (time.time() - started)
        if remaining < 120:
            mutant["status"] = "not checked"
            continue
        run_mutant(ctx, scratch, ebin_base, ebin_test, mutant, min(per_mutant_timeout, math.ceil(remaining)))
    write_report(ctx, mutants)
    survived = [m for m in mutants if m.get("status") == "survived"]
    unchecked = [m for m in mutants if m.get("status") == "not checked"]
    invalid = [m for m in mutants if m.get("status") == "invalid"]
    capped = [m for m in mutants if m.get("status") == "skipped"]
    counted = [m for m in mutants if m.get("status") not in EXCLUDED]
    if not counted:
        return Result("er.mutation", False, f"no runnable mutants: all {len(mutants)} failed to compile or were capped", [], time.time() - started)
    findings = [describe(ctx, m, "survived") for m in survived]
    findings += [describe(ctx, m, "not checked (time budget)") for m in unchecked]
    notes = []
    if unchecked:
        notes.append(f"{len(unchecked)} unchecked (time budget)")
    if invalid:
        notes.append(f"{len(invalid)} failed to compile")
    if capped:
        notes.append(f"{len(capped)} skipped by mutation_max")
    base = f"{len(survived)} of {len(counted)} mutants not killed" if findings else f"all {len(counted)} mutants killed"
    summary = base + (f" ({'; '.join(notes)})" if notes else "")
    return Result("er.mutation", not findings, summary, findings, time.time() - started)


def apply_cap(mutants: list[dict], ctx: Context) -> None:
    cap = int(ctx.erlang("mutation_max", 0) or 0)
    if cap <= 0 or len(mutants) <= cap:
        return
    step = len(mutants) / cap
    keep = {mutants[int(index * step)]["id"] for index in range(cap)}
    for mutant in mutants:
        if mutant["id"] not in keep:
            mutant["status"] = "skipped"


def run_mutant(ctx: Context, scratch: Path, ebin_base: Path, ebin_test: Path, mutant: dict, timeout: int) -> None:
    ebin = erlang.fresh_dir(scratch / f"ebin-{mutant['id']}")
    include = str(Path(mutant["file"]).parent)
    code, _ = erlang.erlc(ctx, ["+debug_info", "-I", include, "-o", str(ebin), mutant["mutant"]], timeout=300)
    if code != 0:
        mutant["status"] = "invalid"
        shutil.rmtree(ebin, ignore_errors=True)
        return
    code, _ = erlang.escript(ctx, "mutation.escript", ["run", str(ebin), str(ebin_base), str(ebin_test)], timeout=timeout)
    if code == 0:
        mutant["status"] = "survived"
    elif code == 1:
        mutant["status"] = "killed"
    elif code == 124:
        mutant["status"] = "timeout"
    else:
        mutant["status"] = "error"
    shutil.rmtree(ebin, ignore_errors=True)


def describe(ctx: Context, mutant: dict, status: str) -> str:
    path = erlang.rel(ctx, mutant["file"])
    return f"{path}:{mutant['line']} {mutant['operator']} mutant {status}: {mutant['original']} -> {mutant['replacement']}"


def write_report(ctx: Context, mutants: list[dict]) -> None:
    entries = [
        {
            "file": erlang.rel(ctx, m["file"]),
            "line": m["line"],
            "operator": m["operator"],
            "original": m["original"],
            "replacement": m["replacement"],
            "status": m.get("status", "not checked"),
        }
        for m in mutants
    ]
    (ctx.work / RESULTS).write_text(json.dumps({"mutants": entries}, indent=2) + "\n")
