import json
import time
from pathlib import Path
from typing import Any, cast

from marestail.context import Context, MutationScope
from marestail.gates.ex_lint import scoped_sources
from marestail.report import Result
from marestail.shell import run, tail

GATE = "ex.mutation"
PASSING = {"killed", "invalid", "equivalent"}
DISABLED_TIMEOUTS = {"0", "none", "false", "off"}
MUEX_DEPENDENCY = 'add {:muex, "~> 0.11", only: [:dev, :test], runtime: false} to mix.exs and run mix deps.get'


def run_gate(ctx: Context) -> Result:
    started = time.time()
    root = ctx.elixir_root()
    scope = ctx.mutation_files("elixir", root, (".ex", ".exs"))
    if scope.mode == "error":
        return Result(GATE, False, scope.note, [], time.time() - started)
    files = scoped_sources(ctx, root, scope.files or [])
    if nothing_changed(scope, files):
        return Result.skipped(GATE, "no changed elixir sources")
    return with_muex(ctx, root, scope, files, started)


def nothing_changed(scope: MutationScope, files: list[str]) -> bool:
    return scope.mode != "full" and not files


def with_muex(ctx: Context, root: Path, scope: MutationScope, files: list[str], started: float) -> Result:
    missing = muex_missing(root)
    if missing:
        return Result(GATE, False, *missing, time.time() - started)
    _, output = run(command(ctx, files), cwd=root, env={"MIX_ENV": "test"}, timeout=cast(int, mutation_timeout(ctx)))
    report = read_report(output)
    if isinstance(report, str):
        return Result(GATE, False, report, tail(output), time.time() - started)
    return report_result(ctx, scope, report.get("mutations", []), output, started)


def muex_missing(root: Path) -> tuple[str, list[str]] | None:
    code, _ = run(["mix", "help", "muex"], cwd=root, timeout=120)
    if code == 127:
        return "mix not available", ["mix is not installed: install Elixir"]
    if code != 0:
        return "muex is not installed", [MUEX_DEPENDENCY]
    return None


def read_report(output: str) -> dict[str, Any] | str:
    start = output.find("{")
    if start < 0:
        return "muex produced no report"
    try:
        report, _ = json.JSONDecoder().raw_decode(output[start:])
    except json.JSONDecodeError:
        return "muex report unreadable"
    return dict(report)


def report_result(ctx: Context, scope: MutationScope, mutations: list[dict[str, Any]], output: str, started: float) -> Result:
    if not mutations:
        return Result(GATE, False, "no mutants were generated", tail(output), time.time() - started)
    findings = [describe(ctx, mutation) for mutation in mutations if status(mutation) not in PASSING]
    summary = mutation_summary(len(findings), counted(mutations), scope.note)
    return Result(GATE, not findings, summary, findings, time.time() - started)


def status(mutation: dict[str, Any]) -> str:
    return str(mutation.get("status", "").lower())


def counted(mutations: list[dict[str, Any]]) -> int:
    return sum(1 for mutation in mutations if status(mutation) != "invalid")


def mutation_summary(failing: int, total: int, note: str) -> str:
    summary = f"{failing} of {total} mutants not killed" if failing else f"all {total} mutants killed"
    return summary + (f" {note}" if note else "")


def mutation_timeout(ctx: Context) -> int | None:
    value = ctx.elixir("mutation_timeout", 7200)
    if not value or str(value).lower() in DISABLED_TIMEOUTS:
        return None
    return int(value)


def command(ctx: Context, files: list[str]) -> list[str]:
    parts = ["mix", "muex", "--format", "json", "--fail-at", "0", *switches(ctx)]
    parts += option("--preset", ctx.elixir("muex_preset"))
    parts += option("--concurrency", ctx.elixir("muex_concurrency", 4))
    parts += option("--max-mutations", ctx.elixir("muex_max_mutations"))
    parts += mirror_option(ctx.elixir("muex_mirror"))
    if files:
        parts += ["--files", ",".join(files)]
    return parts


def switches(ctx: Context) -> list[str]:
    flags = []
    if not ctx.elixir("muex_filter", False):
        flags.append("--no-filter")
    if not ctx.elixir("muex_optimize", True):
        flags.append("--no-optimize")
    return flags


def option(flag: str, value: Any) -> list[str]:
    return [flag, str(value)] if value else []


def mirror_option(mirror: Any) -> list[str]:
    if not mirror:
        return []
    dirs = mirror if isinstance(mirror, list) else [mirror]
    return ["--mirror", ",".join(str(folder) for folder in dirs)]


def describe(ctx: Context, mutation: dict[str, Any]) -> str:
    location = mutation.get("location", {})
    file = ctx.elixir_root().joinpath(location.get("file", "?")).resolve()
    mutator = str(mutation.get("mutator", "")).rsplit(".", 1)[-1]
    return f"{label(ctx, file)}:{location.get('line', 0)} {mutator} {mutation.get('status')}: {str(mutation.get('description', ''))[:80]}"


def label(ctx: Context, file: Path) -> str:
    try:
        return str(file.relative_to(ctx.root))
    except ValueError:
        return str(file)
