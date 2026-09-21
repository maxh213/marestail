import json
import time
from typing import Any

from marestail import rust
from marestail.context import Context
from marestail.report import Result, elapsed

GATE = "rs.lint"
CLIPPY_ARGS = ["-D", "warnings", "-D", "clippy::pedantic"]
MAX_LINES = 60
CLIPPY_TIMEOUT = 1800
FORMAT_TIMEOUT = 300
OUTPUT_TAIL = 300
CLIPPY = "clippy"
RS = ".rs"
CARGO_TOML = "Cargo.toml"
RUST_SUFFIXES = (RS, CARGO_TOML)


def run_gate(ctx: Context) -> Result:
    started = time.time()
    if nothing_changed(ctx):
        return Result.skipped(GATE, "no changed rust files")
    code, output = rust.cargo(ctx, clippy_command(ctx), timeout=CLIPPY_TIMEOUT)
    problem = rust.missing(code, output, CLIPPY)
    if problem:
        return Result(GATE, False, "clippy missing", [problem], elapsed(started))
    findings = clippy_or_failure(code, output, ctx) + format_findings(ctx)
    summary = f"{len(findings)} problems" if findings else "clippy and rustfmt clean"
    return Result(GATE, not findings, summary, findings[:MAX_LINES], elapsed(started))


def nothing_changed(ctx: Context) -> bool:
    return ctx.scope_changed and not ctx.changed_under(ctx.rust_root(), RUST_SUFFIXES)


def clippy_command(ctx: Context) -> list[str]:
    return ["clippy", "--all-targets", "--message-format=json", "--", *rust.listify(ctx.rust("clippy_args", CLIPPY_ARGS))]


def clippy_or_failure(code: int, output: str, ctx: Context) -> list[str]:
    findings = clippy_findings(output, ctx)
    if code != 0 and not findings:
        return [f"cargo clippy failed: {output.strip()[-OUTPUT_TAIL:]}"]
    return findings


def clippy_findings(output: str, ctx: Context) -> list[str]:
    findings: list[str] = []
    for line in output.splitlines():
        finding = clippy_line(line, ctx)
        if finding and finding not in findings:
            findings.append(finding)
    return findings


def json_record(line: str) -> dict[str, Any]:
    if not line.startswith("{"):
        return {}
    try:
        record: dict[str, Any] = json.loads(line)
    except json.JSONDecodeError:
        return {}
    return record


def compiler_message(line: str) -> dict[str, Any] | None:
    record = json_record(line)
    if record.get("reason") != "compiler-message":
        return None
    message = record.get("message")
    if not isinstance(message, dict) or message.get("level") not in ("warning", "error"):
        return None
    return message


def primary_span(message: dict[str, Any]) -> dict[str, Any] | None:
    spans = message.get("spans")
    if not isinstance(spans, list):
        spans = []
    return next((span for span in spans if span.get("is_primary")), None)


def rule_of(message: dict[str, Any]) -> str:
    rule: str = (message.get("code") or {}).get("code") or message["level"]
    return rule


def located_message(line: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    message = compiler_message(line)
    if message is None:
        return None
    span = primary_span(message)
    return None if span is None else (message, span)


def clippy_line(line: str, ctx: Context) -> str | None:
    found = located_message(line)
    if found is None:
        return None
    message, span = found
    where = rust.rel(ctx, span["file_name"])
    return f"{where}:{span['line_start']} {rule_of(message)}: {message['message']}" if ctx.in_scope(where) else None


def formatted_paths(output: str) -> list[str]:
    return [line.strip() for line in output.splitlines() if line.strip().endswith(".rs")]


def format_findings(ctx: Context) -> list[str]:
    code, output = rust.cargo(ctx, ["fmt", "--check", "--message-format", "short"], timeout=FORMAT_TIMEOUT)
    problem = rust.missing(code, output, CLIPPY)
    if problem:
        return [problem]
    paths = formatted_paths(output)
    if code != 0 and not paths:
        return [f"cargo fmt --check failed: {output.strip()[-OUTPUT_TAIL:]}"]
    return unformatted(paths, ctx)


def unformatted(paths: list[str], ctx: Context) -> list[str]:
    relative = [rust.rel(ctx, path) for path in paths]
    return [f"{path}:1 not rustfmt formatted; run cargo fmt" for path in relative if ctx.in_scope(path)]
