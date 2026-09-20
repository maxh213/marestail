import json
import re
import time
from typing import Any

from marestail.context import Context
from marestail.gates._coverage import in_scope_findings
from marestail.javascript import rel as relative
from marestail.report import Result
from marestail.shell import run

MAX_LINES = 60
NOISE = ("npm notice", "npm warn", "npm WARN")
TSC_LINE = re.compile(r"^(?P<path>[^()]+)\((?P<line>\d+),\d+\):\s*(?P<rest>\S.*)$")
FILE_PATH = "filePath"


def run_gate(ctx: Context) -> Result:
    started = time.time()
    if ctx.scoped and not ctx.changed_under(ctx.ts_root(), (".ts", ".tsx", ".js", ".jsx")):
        return Result.skipped("ts.lint", "no changed typescript files")
    findings = tsc_findings(ctx) + eslint_findings(ctx)
    summary = "tsc and eslint clean" if not findings else f"{len(findings)} problems"
    return Result("ts.lint", not findings, summary, findings[:MAX_LINES], time.time() - started)


def tsc_findings(ctx: Context) -> list[str]:
    name = str(ctx.ts("tsconfig", "tsconfig.app.json"))
    if not (ctx.ts_root() / name).exists():
        return [f"marestail.toml:1 [ts] tsconfig = {name!r} does not exist under {relative(str(ctx.ts_root()), ctx)}"]
    code, output = run(["npx", "tsc", "--noEmit", "-p", name], cwd=ctx.ts_root(), timeout=900)
    return [] if code == 0 else tsc_report(output, ctx)


def tsc_report(output: str, ctx: Context) -> list[str]:
    lines = meaningful(output)
    matches = tsc_matches(lines)
    if not matches:
        return [f"tsc: {line}" for line in lines]
    return in_scope_findings([tsc_finding(match, ctx) for match in matches], ctx)


def tsc_matches(lines: list[str]) -> list[re.Match[str]]:
    return [match for match in map(TSC_LINE.match, (line.strip() for line in lines)) if match]


def tsc_finding(match: re.Match[str], ctx: Context) -> str:
    return f"{relative(match.group('path'), ctx)}:{match.group('line')} {match.group('rest').strip()[:300]}"


def eslint_findings(ctx: Context) -> list[str]:
    code, output = run(eslint_command(ctx), cwd=ctx.ts_root(), timeout=900)
    return [] if code == 0 else eslint_report(ctx, code, output)


def eslint_report(ctx: Context, code: int, output: str) -> list[str]:
    report = parse(output)
    if report is None:
        return eslint_lines(output)
    return scoped_messages(report, ctx) if ctx.scoped else unscoped_messages(report, ctx, code, output)


def scoped_messages(report: list[dict[str, Any]], ctx: Context) -> list[str]:
    return messages([file for file in report if ctx.in_scope(relative(file.get(FILE_PATH, ""), ctx))], ctx)


def unscoped_messages(report: list[dict[str, Any]], ctx: Context, code: int, output: str) -> list[str]:
    return messages(report, ctx) or eslint_lines(output) or [f"marestail.toml:1 eslint exited {code} without a message"]


def messages(report: list[dict[str, Any]], ctx: Context) -> list[str]:
    return [describe(file, message, ctx) for file in report for message in file.get("messages", [])]


def eslint_lines(output: str) -> list[str]:
    return [f"eslint: {line}" for line in meaningful(output)]


def eslint_command(ctx: Context) -> list[str]:
    benchmarks = ["--ignore-pattern", "perf/"] if ctx.ts_root().resolve() == ctx.root.resolve() else []
    return ["npx", "eslint", ".", *benchmarks, "--max-warnings", "0", "--format", "json"]


def parse(output: str) -> list[Any] | None:
    start = output.find("[")
    if start < 0:
        return None
    try:
        report, _ = json.JSONDecoder().raw_decode(output, start)
    except ValueError:
        return None
    return report if isinstance(report, list) else None


def describe(file: dict[str, Any], message: dict[str, Any], ctx: Context) -> str:
    text = str(message.get("message", "")).splitlines()[0] if message.get("message") else ""
    return f"{relative(file.get(FILE_PATH, ''), ctx)}:{message.get('line') or 1} {message.get('ruleId') or 'error'}: {text}"


def meaningful(output: str) -> list[str]:
    return [line for line in output.splitlines() if line.strip() and not line.startswith(NOISE)]
