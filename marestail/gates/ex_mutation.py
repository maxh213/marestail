import json
import time
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import Any

from marestail import remote
from marestail.context import NO_CHANGED_MUTANTS, Context, MutationScope
from marestail.elixir import project_files
from marestail.report import Result, elapsed
from marestail.shell import run, tail

scoped_sources = project_files
GATE = "ex.mutation"
PASSING = {"killed", "invalid", "equivalent"}
DISABLED_TIMEOUTS = {"0", "none", "false", "off"}
MUEX_DEPENDENCY = 'add {:muex, "~> 0.11", only: [:dev, :test], runtime: false} to mix.exs and run mix deps.get'
EX = ".ex"
EXS = ".exs"
ELIXIR_SUFFIXES = (EX, EXS)
MUTATIONS_KEY = "mutations"
MUTATOR_KEY = "mutator"
DOT = "."
TIMEOUT = "timeout"
RUN_LIMIT = 300
WATCHDOG = r"""
limit=$1; poll=$2; shift 2
pattern="mix test --max""-failures"
"$@" &
main=$!
descends() {
  local pid=$1
  while [ "$pid" -gt 1 ] 2>/dev/null; do
    [ "$pid" = "$main" ] && return 0
    pid=$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')
    [ -n "$pid" ] || return 1
  done
  return 1
}
reap() {
  local child
  for child in $(pgrep -P "$1"); do reap "$child"; done
  kill -9 "$1" 2>/dev/null
}
while kill -0 "$main" 2>/dev/null; do
  sleep "$poll"
  for run in $(pgrep -f -- "$pattern"); do
    age=$(ps -o etimes= -p "$run" 2>/dev/null | tr -d ' ')
    if [ -n "$age" ] && [ "$age" -gt "$limit" ] && descends "$run"; then
      echo "marestail: killed a muex test run after ${age}s (limit ${limit}s)" >&2
      reap "$run"
    fi
  done
done
wait "$main"
"""


def run_gate(ctx: Context) -> Result:
    started = time.time()
    root = ctx.elixir_root()
    scope = ctx.mutation_files("elixir", root, ELIXIR_SUFFIXES)
    if scope.mode == "error":
        return Result(GATE, False, scope.note, [], elapsed(started))
    files = scoped_sources(ctx, root, scope.files or [])
    if nothing_changed(scope, files):
        return Result.skipped(GATE, "no changed elixir sources")
    return with_muex(ctx, root, scope, files, started)


def nothing_changed(scope: MutationScope, files: list[str]) -> bool:
    return scope.mode != "full" and not files


def with_muex(ctx: Context, root: Path, scope: MutationScope, files: list[str], started: float) -> Result:
    missing = muex_missing(root)
    if missing:
        return Result(GATE, False, *missing, elapsed(started))
    outcome = remote.run_mutation(
        ctx,
        GATE,
        lambda on_remote: guarded(ctx, command(ctx, files, on_remote)),
        root,
        env={"MIX_ENV": "test"},
        timeout=mutation_timeout(ctx),
    )
    report = read_report(outcome.output)
    if isinstance(report, str):
        return Result(GATE, False, report + outcome.where, tail(outcome.output), elapsed(started))
    return report_result(ctx, located(scope, outcome.where), mutation_list(report), outcome.output, started)


def located(scope: MutationScope, where: str) -> MutationScope:
    return scope if not where else replace(scope, note=(scope.note + where).strip())


def mutation_list(report: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        found = report[MUTATIONS_KEY]
    except KeyError:
        return []
    return list(found)


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
        return Result(GATE, False, "no mutants were generated", tail(output), elapsed(started))
    mutations = ctx.on_changed_lines(mutations, partial(describe, ctx))
    if not mutations:
        return Result(GATE, True, NO_CHANGED_MUTANTS, [], elapsed(started))
    return judged(ctx, scope.note, mutations, started)


def judged(ctx: Context, note: str, mutations: list[dict[str, Any]], started: float) -> Result:
    passing = passing_statuses(ctx)
    findings = failures(ctx, mutations, passing)
    summary = mutation_summary(len(findings), counted(mutations), note, timed_out(mutations, passing))
    return Result(GATE, not findings, summary, findings, elapsed(started))


def passing_statuses(ctx: Context) -> set[str]:
    return PASSING if ctx.timeouts_fail("elixir") else PASSING | {TIMEOUT}


def timed_out(mutations: list[dict[str, Any]], passing: set[str]) -> int:
    return sum(1 for mutation in mutations if status(mutation) == TIMEOUT) if TIMEOUT in passing else 0


def failures(ctx: Context, mutations: list[dict[str, Any]], passing: set[str]) -> list[str]:
    return [describe(ctx, mutation) for mutation in mutations if status(mutation) not in passing]


def status(mutation: dict[str, Any]) -> str:
    return str(mutation.get("status", "").lower())


def counted(mutations: list[dict[str, Any]]) -> int:
    return sum(1 for mutation in mutations if status(mutation) != "invalid")


def mutation_summary(failing: int, total: int, note: str, timed_out: int) -> str:
    summary = f"{failing} of {total} mutants not killed" if failing else f"all {total} mutants killed"
    summary += f" ({timed_out} killed by timeout)" if timed_out else ""
    return summary + (f" {note}" if note else "")


def mutation_timeout(ctx: Context) -> int | None:
    value = ctx.elixir("mutation_timeout", 7200)
    if not value or str(value).lower() in DISABLED_TIMEOUTS:
        return None
    return int(value)


def guarded(ctx: Context, parts: list[str], poll: int = 15) -> list[str]:
    limit = int(ctx.elixir("muex_run_limit", RUN_LIMIT))
    return ["bash", "-c", WATCHDOG, "muex-watchdog", str(limit), str(poll), *parts]


def command(ctx: Context, files: list[str], on_remote: bool) -> list[str]:
    parts = ["mix", "muex", "--format", "json", "--fail-at", "0", *switches(ctx)]
    parts += option("--preset", ctx.elixir("muex_preset"))
    parts += option("--concurrency", remote.workers(ctx, GATE, ctx.elixir("muex_concurrency", 4), on_remote))
    parts += option("--max-mutations", ctx.elixir("muex_max_mutations"))
    parts += mirror_option(ctx.elixir("muex_mirror"))
    if files:
        parts += ["--files", ",".join(files)]
    return parts


def switches(ctx: Context) -> list[str]:
    flags = []
    if flag_off(ctx.elixir("muex_filter", False)):
        flags.append("--no-filter")
    if flag_off(ctx.elixir("muex_optimize")):
        flags.append("--no-optimize")
    return flags


def flag_off(value: object) -> bool:
    return value is False


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
    mutator = mutator_name(str(mutation.get(MUTATOR_KEY, "")))
    return f"{label(ctx, file)}:{location.get('line', 0)} {mutator} {mutation.get('status')}: {str(mutation.get('description', ''))[:80]}"


def mutator_name(text: str) -> str:
    return text[text.rfind(DOT) + 1 :]


def label(ctx: Context, file: Path) -> str:
    try:
        return str(file.relative_to(ctx.root))
    except ValueError:
        return str(file)
