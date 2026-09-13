import json
import time

from marestail import rust
from marestail.context import Context
from marestail.report import Result

CLIPPY_ARGS = ["-D", "warnings", "-D", "clippy::pedantic"]
MAX_LINES = 60


def run_gate(ctx: Context) -> Result:
    started = time.time()
    if ctx.scope_changed and not ctx.changed_under(ctx.rust_root(), (".rs", "Cargo.toml")):
        return Result.skipped("rs.lint", "no changed rust files")
    code, output = rust.cargo(ctx, ["clippy", "--all-targets", "--message-format=json", "--", *rust.listify(ctx.rust("clippy_args", CLIPPY_ARGS))], timeout=1800)
    problem = rust.missing(code, output, "clippy")
    if problem:
        return Result("rs.lint", False, "clippy missing", [problem], time.time() - started)
    findings = clippy_findings(output, ctx)
    if code != 0 and not findings:
        findings = [f"cargo clippy failed: {output.strip()[-300:]}"]
    findings += format_findings(ctx)
    summary = "clippy and rustfmt clean" if not findings else f"{len(findings)} problems"
    return Result("rs.lint", not findings, summary, findings[:MAX_LINES], time.time() - started)


def clippy_findings(output: str, ctx: Context) -> list[str]:
    findings: list[str] = []
    for line in output.splitlines():
        if not line.startswith("{"):
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        message = record.get("message") if record.get("reason") == "compiler-message" else None
        if not message or message.get("level") not in ("warning", "error"):
            continue
        spans = [span for span in message.get("spans", []) if span.get("is_primary")]
        if not spans:
            continue
        where = rust.rel(ctx, spans[0]["file_name"])
        if ctx.scope_changed and where not in ctx.changed:
            continue
        rule = (message.get("code") or {}).get("code") or message["level"]
        finding = f"{where}:{spans[0]['line_start']} {rule}: {message['message']}"
        if finding not in findings:
            findings.append(finding)
    return findings


def format_findings(ctx: Context) -> list[str]:
    code, output = rust.cargo(ctx, ["fmt", "--check", "--message-format", "short"], timeout=300)
    problem = rust.missing(code, output, "clippy")
    if problem:
        return [problem]
    paths = [line.strip() for line in output.splitlines() if line.strip().endswith(".rs")]
    if code != 0 and not paths:
        return [f"cargo fmt --check failed: {output.strip()[-300:]}"]
    relative = [rust.rel(ctx, path) for path in paths]
    return [f"{path}:1 not rustfmt formatted; run cargo fmt" for path in relative if not ctx.scope_changed or path in ctx.changed]
