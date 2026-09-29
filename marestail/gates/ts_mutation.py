import json
import shutil
import time
from pathlib import Path

from marestail import remote
from marestail.context import Context
from marestail.perf.scope import is_benchmark
from marestail.report import Result
from marestail.shell import tail

REPORT = "reports/mutation/mutation.json"
INCREMENTAL = "reports/stryker-incremental.json"
TEMP_DIR = ".stryker-tmp"
TIMEOUT = 7200
BAD = {"Survived", "NoCoverage", "RuntimeError"}


def run_gate(ctx: Context) -> Result:
    started = time.time()
    scope = ctx.mutation_files("ts", ctx.ts_root(), (".ts", ".tsx"))
    if scope.mode == "error":
        return Result("ts.mutation", False, scope.note, [], time.time() - started)
    mutate = changed_sources(ctx, scope.files or [])
    if scope.mode != "full" and not mutate:
        return Result.skipped("ts.mutation", "no changed typescript sources")
    incremental = bool(ctx.ts("mutation_incremental", False))
    temp = ctx.ts_root() / TEMP_DIR
    report = ctx.ts_root() / REPORT
    shutil.rmtree(temp, ignore_errors=True)
    report.unlink(missing_ok=True)
    try:
        pull = tuple(str((ctx.ts_root() / path).relative_to(ctx.root)) for path in (REPORT, INCREMENTAL))
        outcome = remote.run_mutation(ctx, "ts.mutation", lambda on_remote: mutation_command(mutate, concurrency(ctx, on_remote), incremental), ctx.ts_root(), timeout=TIMEOUT, pull=pull)
        if not report.exists():
            return Result("ts.mutation", False, f"stryker produced no report (exit {outcome.code}){outcome.where}", tail(outcome.output), time.time() - started)
        data = json.loads(report.read_text())
        survivors = surviving(data, ctx)
        timed_out = [m for m in in_scope(data, ctx) if m[1]["status"] == "Timeout"]
    finally:
        shutil.rmtree(temp, ignore_errors=True)
    if ctx.timeouts_fail("ts"):
        survivors += [finding(name, mutant) for name, mutant in timed_out]
    summary = f"{len(survivors)} surviving mutants" if survivors else "all mutants killed"
    summary += f" ({len(timed_out)} killed by timeout)" if timed_out and not ctx.timeouts_fail("ts") else ""
    summary += f" {scope.note}" if scope.note else ""
    summary += outcome.where
    return Result("ts.mutation", not survivors, summary, survivors, time.time() - started)


def concurrency(ctx: Context, on_remote: bool) -> int | None:
    configured = ctx.ts("mutation_concurrency")
    return remote.workers(ctx, "ts.mutation", int(configured) if configured else 0, on_remote) or None


def mutation_command(mutate: list[str], concurrency: int | None = None, incremental: bool = False) -> list[str]:
    command = ["npx", "stryker", "run", "--reporters", "json,progress", "--tempDirName", TEMP_DIR, "--cleanTempDir", "always"]
    command += ["--dryRunTimeoutMinutes", str(TIMEOUT // 60)]
    if concurrency:
        command += ["--concurrency", str(concurrency)]
    if incremental:
        command += ["--incremental", "--incrementalFile", INCREMENTAL]
    if mutate:
        command += ["--mutate", ",".join(mutate)]
    return command


def changed_sources(ctx: Context, files: list[str]) -> list[str]:
    root = ctx.ts_root().relative_to(ctx.root)
    return [str(Path(file).relative_to(root)) for file in files if ".test." not in file and ".spec." not in file and not is_benchmark(file)]


def surviving(report: dict, ctx: Context) -> list[str]:
    return [finding(name, mutant) for name, mutant in in_scope(report, ctx) if mutant["status"] in BAD]


def in_scope(report: dict, ctx: Context) -> list[tuple[str, dict]]:
    found = []
    for file, data in report.get("files", {}).items():
        name = relative(file, ctx)
        if ctx.in_scope(name):
            found += [(name, mutant) for mutant in data.get("mutants", [])]
    return found


def finding(name: str, mutant: dict) -> str:
    line = mutant["location"]["start"]["line"]
    return f"{name}:{line} {mutant['mutatorName']} {mutant['status']}: {str(mutant.get('replacement', ''))[:60]}"


def relative(path: str, ctx: Context) -> str:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = ctx.ts_root() / path
    try:
        return candidate.resolve().relative_to(ctx.root.resolve()).as_posix()
    except ValueError:
        return path
