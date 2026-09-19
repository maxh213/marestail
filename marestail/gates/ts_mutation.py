import json
import shutil
import time
from pathlib import Path
from typing import Any

from marestail.context import Context, MutationScope, is_benchmark
from marestail.gates.ts_tests import relative
from marestail.report import Result
from marestail.shell import run, tail

REPORT = "reports/mutation/mutation.json"
TEMP_DIR = ".stryker-tmp"
BAD = {"Survived", "NoCoverage", "Timeout", "RuntimeError", "CompileError"}
GATE = "ts.mutation"


def run_gate(ctx: Context) -> Result:
    started = time.time()
    scope = ctx.mutation_files("ts", ctx.ts_root(), (".ts", ".tsx"))
    if scope.mode == "error":
        return Result(GATE, False, scope.note, [], time.time() - started)
    return mutated(ctx, scope, started)


def mutated(ctx: Context, scope: MutationScope, started: float) -> Result:
    mutate = changed_sources(ctx, scope.files or [])
    if scope.mode != "full" and not mutate:
        return Result.skipped(GATE, "no changed typescript sources")
    return stryker_result(ctx, scope, mutation_command(mutate), started)


def stryker_result(ctx: Context, scope: MutationScope, command: list[str], started: float) -> Result:
    temp = ctx.ts_root() / TEMP_DIR
    report = ctx.ts_root() / REPORT
    shutil.rmtree(temp, ignore_errors=True)
    report.unlink(missing_ok=True)
    try:
        code, output = run(command, cwd=ctx.ts_root(), timeout=7200)
        if not report.exists():
            return Result(GATE, False, f"stryker produced no report (exit {code})", tail(output), time.time() - started)
        survivors = surviving(json.loads(report.read_text()), ctx)
    finally:
        shutil.rmtree(temp, ignore_errors=True)
    return survivor_result(survivors, scope.note, started)


def survivor_result(survivors: list[str], note: str, started: float) -> Result:
    summary = f"{len(survivors)} surviving mutants" if survivors else "all mutants killed"
    summary += f" {note}" if note else ""
    return Result(GATE, not survivors, summary, survivors, time.time() - started)


def mutation_command(mutate: list[str]) -> list[str]:
    command = ["npx", "stryker", "run", "--reporters", "json,progress", "--tempDirName", TEMP_DIR, "--cleanTempDir", "always"]
    if mutate:
        command += ["--mutate", ",".join(mutate)]
    return command


def changed_sources(ctx: Context, files: list[str]) -> list[str]:
    root = ctx.ts_root().relative_to(ctx.root)
    return [str(Path(file).relative_to(root)) for file in files if mutable(file)]


def mutable(file: str) -> bool:
    return ".test." not in file and ".spec." not in file and not is_benchmark(file)


def surviving(report: dict[str, Any], ctx: Context) -> list[str]:
    return [finding for name, data in named_files(report, ctx) if ctx.in_scope(name) for finding in file_survivors(name, data)]


def named_files(report: dict[str, Any], ctx: Context) -> list[tuple[str, dict[str, Any]]]:
    return [(relative(file, ctx), data) for file, data in report.get("files", {}).items()]


def file_survivors(name: str, data: dict[str, Any]) -> list[str]:
    return [survivor(name, mutant) for mutant in data.get("mutants", []) if mutant["status"] in BAD]


def survivor(name: str, mutant: dict[str, Any]) -> str:
    line = mutant["location"]["start"]["line"]
    return f"{name}:{line} {mutant['mutatorName']} {mutant['status']}: {str(mutant.get('replacement', ''))[:60]}"
