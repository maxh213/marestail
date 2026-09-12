import re
import time

from marestail import erlang
from marestail.context import Context
from marestail.report import Result

MAX_LINES = 60
ERLC_LINE = re.compile(r"^(.+?\.(?:erl|hrl)):(\d+):(?:\d+:)?\s*(.*)$")
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
    root = ctx.erlang_root()
    if ctx.scope_changed and not ctx.changed_under(root, (".erl", ".hrl")):
        return Result.skipped("er.lint", "no changed erlang files")
    sources = erlang.source_files(ctx)
    tests = erlang.test_files(ctx)
    if not sources and not tests:
        return Result.skipped("er.lint", "no erlang sources under [erlang] sources (default src/)")
    ebin = erlang.fresh_dir(ctx.work / "er-lint-ebin")
    findings = []
    batches = []
    if sources:
        batches.append(["+debug_info", *STRONG_WARNINGS, "-o", str(ebin), *map(str, sources)])
    if tests:
        batches.append(["-DTEST", "+debug_info", *STRONG_WARNINGS, "-pa", str(ebin), "-o", str(ebin), *map(str, tests)])
    for args in batches:
        code, output = erlang.erlc(ctx, args, timeout=900)
        problem = erlang.hint(code, output)
        if problem:
            return Result("er.lint", False, problem, [problem], time.time() - started)
        if code != 0:
            findings.extend(lint_findings(output, ctx))
    if ctx.scope_changed:
        findings = [f for f in findings if f.split(":")[0] in ctx.changed]
    summary = "erlc strong warnings clean" if not findings else f"{len(findings)} problems"
    return Result("er.lint", not findings, summary, findings[:MAX_LINES], time.time() - started)


def lint_findings(output: str, ctx: Context) -> list[str]:
    findings = []
    for line in output.splitlines():
        match = ERLC_LINE.match(line)
        if match:
            findings.append(f"{erlang.rel(ctx, match.group(1))}:{match.group(2)} {match.group(3)}")
    if not findings:
        findings.extend(line for line in output.splitlines() if line.strip())
    return findings
