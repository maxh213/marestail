import re
import time
from pathlib import Path

from marestail import erlang
from marestail.context import Context
from marestail.report import Result, elapsed

GATE = "er.lint"
MAX_LINES = 60
ERL = ".erl"
HRL = ".hrl"
ERLANG_SUFFIXES = (ERL, HRL)
LINT_TIMEOUT = 900
ERLC_LOCATION = re.compile(r"\.[eh]rl:(\d+):(?:\d+:)?")
STRONG_WARNINGS = [
    "+warn_export_all",
    "+warn_export_vars",
    "+warn_shadow_vars",
    "+warn_obsolete_guard",
    "+warn_unused_import",
    "-Werror",
]


def run_gate(ctx: Context) -> Result:
    started = time.time()
    if unchanged(ctx):
        return Result.skipped(GATE, "no changed erlang files")
    sources = erlang.source_files(ctx)
    tests = erlang.test_files(ctx)
    if not sources and not tests:
        return Result.skipped(GATE, erlang.NO_SOURCES)
    ebin = erlang.fresh_dir(ctx.work / "er-lint-ebin")
    return lint(ctx, compile_batches(sources, tests, ebin), started)


def unchanged(ctx: Context) -> bool:
    return ctx.scoped and not ctx.changed_under(ctx.erlang_root(), ERLANG_SUFFIXES)


def compile_batches(sources: list[Path], tests: list[Path], ebin: Path) -> list[list[str]]:
    batches = []
    if sources:
        batches.append(["+debug_info", *STRONG_WARNINGS, "-o", str(ebin), *map(str, sources)])
    if tests:
        batches.append(["-DTEST", "+debug_info", *STRONG_WARNINGS, "-pa", str(ebin), "-o", str(ebin), *map(str, tests)])
    return batches


def lint(ctx: Context, batches: list[list[str]], started: float) -> Result:
    findings: list[str] = []
    for args in batches:
        code, output = erlang.erlc(ctx, args, timeout=LINT_TIMEOUT)
        problem = erlang.hint(code, output)
        if problem:
            return Result(GATE, False, problem, [problem], elapsed(started))
        findings.extend(batch_findings(code, output, ctx))
    summary = f"{len(findings)} problems" if findings else "erlc strong warnings clean"
    return Result(GATE, not findings, summary, findings[:MAX_LINES], elapsed(started))


def batch_findings(code: int, output: str, ctx: Context) -> list[str]:
    return failed_findings(code, lint_findings(output, ctx))


def failed_findings(code: int, findings: list[str]) -> list[str]:
    return [] if code == 0 else findings


def lint_findings(output: str, ctx: Context) -> list[str]:
    findings = erlc_findings(output, ctx)
    if not findings:
        return [line for line in output.splitlines() if line.strip()]
    return erlang.in_scope_findings(ctx, findings)


def erlc_findings(output: str, ctx: Context) -> list[str]:
    return [finding for finding in (erlc_finding(line, ctx) for line in output.splitlines()) if finding]


def erlc_finding(line: str, ctx: Context) -> str:
    match = ERLC_LOCATION.search(line, 1)
    if match is None:
        return ""
    return f"{erlang.rel(ctx, line[: match.start() + 4])}:{match.group(1)} {line[match.end() :].lstrip()}"
