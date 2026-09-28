import json
import shutil
import time
from pathlib import Path
from typing import Any

from marestail.context import NO_CHANGED_MUTANTS, Context, MutationScope, is_benchmark
from marestail.javascript import config_flag, tool
from marestail.javascript import rel as relative
from marestail.report import Result, elapsed
from marestail.shell import run, tail

REPORT = "reports/mutation/mutation.json"
TEMP_DIR = ".stryker-tmp"
BAD = {"Survived", "NoCoverage", "Timeout", "RuntimeError", "CompileError"}
GATE = "ts.mutation"
TS_SUFFIXES = (".ts", ".tsx")
EMPTY = ""
EMPTY_LIST: list[Any] = []
EMPTY_MAP: dict[str, Any] = {}
Placed = tuple[str, dict[str, Any]]


def run_gate(ctx: Context) -> Result:
    started = time.time()
    scope = ctx.mutation_files("ts", ctx.ts_root(), TS_SUFFIXES)
    if scope.mode == "error":
        return Result(GATE, False, scope.note, [], elapsed(started))
    return mutated(ctx, scope, started)


def mutated(ctx: Context, scope: MutationScope, started: float) -> Result:
    mutate = targets(ctx, changed_sources(ctx, scope.files or []))
    if scope.mode != "full" and not mutate:
        return Result.skipped(GATE, "no changed typescript sources")
    return stryker_result(ctx, scope, mutation_command(ctx, mutate), started)


def drop_tree(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


def stryker_result(ctx: Context, scope: MutationScope, command: list[str], started: float) -> Result:
    temp = ctx.ts_root() / TEMP_DIR
    report = ctx.ts_root() / REPORT
    drop_tree(temp)
    report.unlink(missing_ok=True)
    try:
        code, output = run(command, cwd=ctx.ts_root(), timeout=7200)
        if not report.exists():
            return Result(GATE, False, f"stryker produced no report (exit {code})", tail(output), elapsed(started))
        data = json.loads(report.read_text())
    finally:
        drop_tree(temp)
    return verdict(ctx, data, scope.note, started)


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


def targets(ctx: Context, sources: list[str]) -> list[str]:
    if not ctx.hyper:
        return sources
    prefix = ctx.ts_root().relative_to(ctx.root)
    return [target for source in sources for target in line_ranges(source, ctx.changed_lines_map.get(str(prefix / source), set()))]


def line_ranges(source: str, lines: set[int]) -> list[str]:
    return [f"{source}:{start}-{end}" for start, end in spans(sorted(lines))]


def spans(lines: list[int]) -> list[list[int]]:
    found: list[list[int]] = []
    for line in lines:
        if found and found[-1][1] == line - 1:
            found[-1][1] = line
        else:
            found.append([line, line])
    return found


def changed_sources(ctx: Context, files: list[str]) -> list[str]:
    root = ctx.ts_root().relative_to(ctx.root)
    return [str(Path(file).relative_to(root)) for file in files if mutable(file)]


def mutable(file: str) -> bool:
    return ".test." not in file and ".spec." not in file and not is_benchmark(file)


def placed(report: dict[str, Any], ctx: Context) -> list[Placed]:
    found = [(name, mutant) for name, data in named_files(report, ctx) if ctx.in_scope(name) for mutant in json_list(data, "mutants")]
    return ctx.on_changed_lines(found, where)


def where(entry: Placed) -> str:
    return f"{entry[0]}:{entry[1]['location']['start']['line']}"


def survivors_of(mutants: list[Placed]) -> list[str]:
    return [survivor(name, mutant) for name, mutant in mutants if mutant["status"] in BAD]


def json_map(data: dict[str, Any], key: str) -> dict[str, Any]:
    if key not in data:
        return EMPTY_MAP
    found: dict[str, Any] = data[key]
    return found


def json_list(data: dict[str, Any], key: str) -> list[Any]:
    if key not in data:
        return EMPTY_LIST
    found: list[Any] = data[key]
    return found


def named_files(report: dict[str, Any], ctx: Context) -> list[tuple[str, dict[str, Any]]]:
    return [(relative(file, ctx), data) for file, data in json_map(report, "files").items()]


def replacement_text(mutant: dict[str, Any]) -> str:
    if "replacement" not in mutant:
        return EMPTY
    return str(mutant["replacement"])[:60]


def survivor(name: str, mutant: dict[str, Any]) -> str:
    line = mutant["location"]["start"]["line"]
    return f"{name}:{line} {mutant['mutatorName']} {mutant['status']}: {replacement_text(mutant)}"
