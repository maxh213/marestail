import json
import time

from marestail.context import Context
from marestail.report import Result
from marestail.shell import run

MAX_LINES = 60


def run_gate(ctx: Context) -> Result:
    started = time.time()
    findings = tsc_findings(ctx) + eslint_findings(ctx)
    summary = "tsc and eslint clean" if not findings else f"{len(findings)} problems"
    return Result("ts.lint", not findings, summary, findings[:MAX_LINES], time.time() - started)


def tsc_findings(ctx: Context) -> list[str]:
    command = ["npx", "tsc", "--noEmit", "-p", ctx.ts("tsconfig", "tsconfig.app.json")]
    code, output = run(command, cwd=ctx.ts_root(), timeout=900)
    return [f"tsc: {line}" for line in meaningful(output)] if code != 0 else []


def eslint_findings(ctx: Context) -> list[str]:
    command = ["npx", "eslint", ".", "--max-warnings", "0", "--format", "json"]
    code, output = run(command, cwd=ctx.ts_root(), timeout=900)
    if code == 0:
        return []
    try:
        report = json.loads(output[output.index("[") :])
    except (ValueError, json.JSONDecodeError):
        return [f"eslint: {line}" for line in meaningful(output)]
    return [describe(file, message) for file in report for message in file["messages"]]


def describe(file: dict, message: dict) -> str:
    return f"eslint: {file['filePath']}:{message['line']} {message.get('ruleId') or 'error'}: {message['message']}"


def meaningful(output: str) -> list[str]:
    return [line for line in output.splitlines() if line.strip() and not line.startswith("npm notice")]
