import json
import time
from pathlib import Path
from typing import Any

from marestail import golang
from marestail.context import Context
from marestail.report import Result, elapsed
from marestail.shell import run

GATE = "go.lint"
MAX_LINES = 60
LINT_TIMEOUT = 1800
FORMAT_TIMEOUT = 300
OUTPUT_TAIL = 300
REPORT = "go-lint.json"
NOT_READY = "go toolchain not ready"
SUFFIXES = (".go", "go.mod", "go.sum")
ACCEPTED = {0, 1}


def run_gate(ctx: Context) -> Result:
    started = time.time()
    if nothing_changed(ctx):
        return Result.skipped(GATE, "no changed Go files")
    problem = golang.preflight(ctx)
    if problem:
        return Result(GATE, False, NOT_READY, [problem], elapsed(started))
    return with_tool(ctx, golang.tool(ctx, "golangci-lint"), started)


def nothing_changed(ctx: Context) -> bool:
    return ctx.scope_changed and not ctx.changed_under(ctx.go_root(), SUFFIXES)


def with_tool(ctx: Context, binary: str, started: float) -> Result:
    if binary.startswith("golangci-lint "):
        return Result(GATE, False, "golangci-lint missing", [binary], elapsed(started))
    return linted(ctx, binary, started)


def linted(ctx: Context, binary: str, started: float) -> Result:
    report = ctx.work / REPORT
    report.unlink(missing_ok=True)
    code, output = run(lint_command(ctx, binary, report), cwd=ctx.go_root(), env=lint_env(ctx), timeout=LINT_TIMEOUT)
    failed = lint_failure(code, output, report, started)
    if failed is not None:
        return failed
    return formatted(ctx, issue_findings(report, ctx), started)


def lint_env(ctx: Context) -> dict[str, str]:
    return {"GOLANGCI_LINT_CACHE": str(ctx.work / "golangci-cache")}


def lint_command(ctx: Context, binary: str, report: Path) -> list[str]:
    return [
        binary,
        "run",
        "--config",
        config_path(ctx),
        "--allow-parallel-runners",
        "--path-mode",
        "abs",
        "--show-stats=false",
        "--max-issues-per-linter=0",
        "--max-same-issues=0",
        "--output.json.path",
        str(report),
        *golang.tags(ctx, "--build-tags"),
        "./...",
    ]


def config_path(ctx: Context) -> str:
    configured = ctx.go("golangci_config")
    if configured:
        return str(ctx.root / str(configured))
    return str(golang.PACKAGE / "go" / "golangci.yml")


def lint_failure(code: int, output: str, report: Path, started: float) -> Result | None:
    if code in ACCEPTED and report.is_file():
        return None
    return Result(GATE, False, f"golangci-lint failed (exit {code})", [output.strip()[-OUTPUT_TAIL:]], elapsed(started))


def issue_findings(report: Path, ctx: Context) -> list[str]:
    payload = json.loads(report.read_text())
    found: list[str] = []
    for issue in payload.get("Issues") or []:
        keep_issue(found, issue_line(issue, ctx))
    return ctx.on_changed_lines(found)


def keep_issue(found: list[str], line: str | None) -> None:
    if line and line not in found:
        found.append(line)


def issue_line(issue: dict[str, Any], ctx: Context) -> str | None:
    pos = issue.get("Pos") or {}
    filename = pos.get("Filename")
    if not filename:
        return None
    where = golang.rel(ctx, str(filename))
    if not ctx.in_scope(where):
        return None
    return f"{where}:{pos.get('Line')} {issue.get('FromLinter')}: {first_line(issue.get('Text'))}"


def first_line(text: object) -> str:
    lines = str(text or "").splitlines()
    return lines[0] if lines else ""


def formatted(ctx: Context, findings: list[str], started: float) -> Result:
    extra, problem = format_findings(ctx)
    if problem:
        return Result(GATE, False, problem, [], elapsed(started))
    return finish(findings + extra, started)


def format_findings(ctx: Context) -> tuple[list[str], str | None]:
    paths = [str(path) for path in golang.in_scope(ctx, golang.files(ctx))]
    if not paths:
        return [], None
    code, output = run(gofmt_command(ctx, paths), cwd=ctx.go_root(), timeout=FORMAT_TIMEOUT)
    if code != 0:
        return [], f"gofmt failed: {output.strip()[-OUTPUT_TAIL:]}"
    return unformatted(output, ctx), None


def gofmt_command(ctx: Context, paths: list[str]) -> list[str]:
    return [str(Path(goroot(ctx)) / "bin" / "gofmt"), "-l", *paths]


def goroot(ctx: Context) -> str:
    loaded = golang.toolchain(ctx)
    if isinstance(loaded, str):
        return ""
    return str(loaded.get("GOROOT", ""))


def unformatted(output: str, ctx: Context) -> list[str]:
    return ctx.on_changed_lines(format_lines(ctx, listed(output)))


def listed(output: str) -> list[str]:
    return [line.strip() for line in output.splitlines() if line.strip()]


def format_lines(ctx: Context, lines: list[str]) -> list[str]:
    return [format_line(ctx, golang.rel(ctx, line)) for line in lines if ctx.in_scope(golang.rel(ctx, line))]


def format_line(ctx: Context, path: str) -> str:
    return f"{path}:{ctx.placeholder_line} not gofmt formatted; run gofmt -w {path}"


def finish(findings: list[str], started: float) -> Result:
    summary = f"{len(findings)} problems" if findings else "golangci-lint and gofmt clean"
    return Result(GATE, not findings, summary, findings[:MAX_LINES], elapsed(started))
