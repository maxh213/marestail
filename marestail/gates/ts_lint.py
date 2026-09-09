import json
import re
import time
from pathlib import Path

from marestail.context import Context
from marestail.report import Result
from marestail.shell import run

MAX_LINES = 60
NOISE = ("npm notice", "npm warn", "npm WARN")
TSC_LINE = re.compile(r"^(?P<path>[^()]+)\((?P<line>\d+),\d+\):\s*(?P<rest>.+)$")


def run_gate(ctx: Context) -> Result:
    started = time.time()
    findings = tsc_findings(ctx) + eslint_findings(ctx)
    summary = "tsc and eslint clean" if not findings else f"{len(findings)} problems"
    return Result("ts.lint", not findings, summary, findings[:MAX_LINES], time.time() - started)


def tsc_findings(ctx: Context) -> list[str]:
    name = str(ctx.ts("tsconfig", "tsconfig.app.json"))
    if not (ctx.ts_root() / name).exists():
        return [f"marestail.toml:1 [ts] tsconfig = {name!r} does not exist under {relative(str(ctx.ts_root()), ctx)}"]
    code, output = run(["npx", "tsc", "--noEmit", "-p", name], cwd=ctx.ts_root(), timeout=900)
    if code == 0:
        return []
    lines = meaningful(output)
    parsed = [tsc_finding(match, ctx) for match in map(TSC_LINE.match, (line.strip() for line in lines)) if match]
    return parsed or [f"tsc: {line}" for line in lines]


def tsc_finding(match: re.Match, ctx: Context) -> str:
    return f"{relative(match.group('path'), ctx)}:{match.group('line')} {match.group('rest').strip()[:300]}"


def eslint_findings(ctx: Context) -> list[str]:
    command = ["npx", "eslint", ".", "--max-warnings", "0", "--format", "json"]
    code, output = run(command, cwd=ctx.ts_root(), timeout=900)
    if code == 0:
        return []
    report = parse(output)
    if report is None:
        return [f"eslint: {line}" for line in meaningful(output)]
    findings = [describe(file, message, ctx) for file in report for message in file.get("messages", [])]
    return findings or [f"eslint: {line}" for line in meaningful(output)] or [f"marestail.toml:1 eslint exited {code} without a message"]


def parse(output: str) -> list | None:
    start = output.find("[")
    if start < 0:
        return None
    try:
        report, _ = json.JSONDecoder().raw_decode(output, start)
    except ValueError:
        return None
    return report if isinstance(report, list) else None


def describe(file: dict, message: dict, ctx: Context) -> str:
    text = str(message.get("message", "")).splitlines()[0] if message.get("message") else ""
    return f"{relative(file.get('filePath', ''), ctx)}:{message.get('line') or 1} {message.get('ruleId') or 'error'}: {text}"


def relative(path: str, ctx: Context) -> str:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = ctx.ts_root() / path
    try:
        return candidate.resolve().relative_to(ctx.root.resolve()).as_posix()
    except ValueError:
        return path


def meaningful(output: str) -> list[str]:
    return [line for line in output.splitlines() if line.strip() and not line.startswith(NOISE)]
