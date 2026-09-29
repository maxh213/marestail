import json
import shutil
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from marestail.context import Context, is_benchmark
from marestail.javascript import rel as relative
from marestail.javascript import tool
from marestail.report import Result, elapsed
from marestail.shell import run, tail

TS_SUFFIXES = (".ts", ".tsx")
COMMAND_SUFFIXES = (".js", ".mjs", ".cjs", *TS_SUFFIXES)
COMMAND_CONFIG = "stryker/command.config.json"
COMMAND_REPORT = "stryker/mutation.json"
COMMAND_TEMP = ".marestail/stryker-tmp"
KILLED = "Killed"
MEMO = "stryker.command"
EMPTY_LIST: list[Any] = []
EMPTY_MAP: dict[str, Any] = {}
Placed = tuple[str, dict[str, Any]]
LineStatuses = dict[str, dict[int, set[str]]]


@dataclass(frozen=True)
class Proof:
    code: int
    output: str
    report: dict[str, Any] | None

    @property
    def failed(self) -> bool:
        return self.report is None and self.code != 0

    def failure(self, gate: str, started: float) -> Result:
        return Result(gate, False, f"stryker produced no report (exit {self.code})", tail(self.output), elapsed(started))

    def line_statuses(self, ctx: Context) -> LineStatuses:
        found: LineStatuses = defaultdict(lambda: defaultdict(set))
        for name, mutant in all_mutants(self.report or EMPTY_MAP, ctx):
            found[name][mutant["location"]["start"]["line"]].add(mutant["status"])
        return found


def execute(ctx: Context, command: list[str], temp: Path, report: Path) -> Proof:
    drop_tree(temp)
    report.unlink(missing_ok=True)
    try:
        code, output = run(command, cwd=ctx.ts_root(), timeout=7200)
        return Proof(code, output, json.loads(report.read_text()) if report.exists() else None)
    finally:
        drop_tree(temp)


def command_targets(ctx: Context) -> list[str]:
    return mutation_targets(ctx, ctx.changed_under(ctx.ts_root(), COMMAND_SUFFIXES))


def command_proof(ctx: Context, mutate: list[str]) -> Proof:
    if MEMO not in ctx.memo:
        command = [*tool(ctx, "stryker"), "run", str(command_config(ctx, mutate))]
        ctx.memo[MEMO] = execute(ctx, command, ctx.ts_root() / COMMAND_TEMP, ctx.work / COMMAND_REPORT)
    found: Proof = ctx.memo[MEMO]
    return found


def command_config(ctx: Context, mutate: list[str]) -> Path:
    path = ctx.work / COMMAND_CONFIG
    path.parent.mkdir(parents=True, exist_ok=True)
    settings = {
        "testRunner": "command",
        "commandRunner": {"command": ctx.test_cmd},
        "coverageAnalysis": "off",
        "reporters": ["json", "progress"],
        "jsonReporter": {"fileName": str(ctx.work / COMMAND_REPORT)},
        "tempDirName": COMMAND_TEMP,
        "cleanTempDir": "always",
        "ignorePatterns": [".marestail"],
        "mutate": mutate,
    }
    path.write_text(json.dumps(settings, indent=2) + "\n")
    return path


def drop_tree(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


def mutation_targets(ctx: Context, files: list[str]) -> list[str]:
    return targets(ctx, changed_sources(ctx, files))


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


def all_mutants(report: dict[str, Any], ctx: Context) -> list[Placed]:
    return [(name, mutant) for name, data in named_files(report, ctx) for mutant in json_list(data, "mutants")]


def named_files(report: dict[str, Any], ctx: Context) -> list[tuple[str, dict[str, Any]]]:
    return [(relative(file, ctx), data) for file, data in json_map(report, "files").items()]


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
