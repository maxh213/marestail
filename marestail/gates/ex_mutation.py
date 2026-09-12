import json
import time

from marestail.context import Context
from marestail.report import Result
from marestail.shell import run, tail

PASSING = {"killed", "invalid", "equivalent"}


def run_gate(ctx: Context) -> Result:
    started = time.time()
    code, output = run(["mix", "help", "muex"], cwd=ctx.elixir_root(), timeout=120)
    if code == 127:
        return Result("ex.mutation", False, "mix not available", ["mix is not installed: install Elixir"], time.time() - started)
    if code != 0:
        return Result("ex.mutation", False, "muex is not installed", ['add {:muex, "~> 0.9", only: [:dev, :test], runtime: false} to mix.exs and run mix deps.get'], time.time() - started)
    code, output = run(command(ctx), cwd=ctx.elixir_root(), env={"MIX_ENV": "test"}, timeout=7200)
    start = output.find("{")
    if start < 0:
        return Result("ex.mutation", False, "muex produced no report", tail(output), time.time() - started)
    try:
        report, _ = json.JSONDecoder().raw_decode(output[start:])
    except json.JSONDecodeError:
        return Result("ex.mutation", False, "muex report unreadable", tail(output), time.time() - started)
    mutations = report.get("mutations", [])
    if not mutations:
        return Result("ex.mutation", False, "no mutants were generated", tail(output), time.time() - started)
    findings = [describe(ctx, m) for m in mutations if m.get("status", "").lower() not in PASSING]
    counted = sum(1 for m in mutations if m.get("status", "").lower() != "invalid")
    summary = f"{len(findings)} of {counted} mutants not killed" if findings else f"all {counted} mutants killed"
    return Result("ex.mutation", not findings, summary, findings, time.time() - started)


def command(ctx: Context) -> list[str]:
    parts = ["mix", "muex", "--format", "json", "--fail-at", "0"]
    if not ctx.elixir("muex_filter", False):
        parts.append("--no-filter")
    if not ctx.elixir("muex_optimize", True):
        parts.append("--no-optimize")
    preset = ctx.elixir("muex_preset")
    if preset:
        parts += ["--preset", str(preset)]
    concurrency = ctx.elixir("muex_concurrency", 4)
    if concurrency:
        parts += ["--concurrency", str(concurrency)]
    max_mutations = ctx.elixir("muex_max_mutations")
    if max_mutations:
        parts += ["--max-mutations", str(max_mutations)]
    if ctx.scope_changed:
        parts += ["--since", str(ctx.config.get("git", "base", "origin/master"))]
    return parts


def describe(ctx: Context, mutation: dict) -> str:
    location = mutation.get("location", {})
    file = ctx.elixir_root().joinpath(location.get("file", "?")).resolve()
    try:
        label = str(file.relative_to(ctx.root))
    except ValueError:
        label = str(file)
    mutator = str(mutation.get("mutator", "")).rsplit(".", 1)[-1]
    return f"{label}:{location.get('line', 0)} {mutator} {mutation.get('status')}: {str(mutation.get('description', ''))[:80]}"
