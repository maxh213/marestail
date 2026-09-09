import json
import re
import time
from pathlib import Path

from marestail.context import Context
from marestail.report import Result
from marestail.shell import run

VULTURE_LINE = re.compile(r"^(.+?):(\d+): (unused \w+|unreachable code) (.+?) \((\d+)% confidence\)$")
PYTHON_KINDS = ["unused function", "unused method", "unused class", "unused import", "unused property", "unreachable code"]
PYTHON_DECORATORS = ["@*.route", "@*.before_request", "@*.after_request", "@*.errorhandler", "@*.teardown_appcontext", "@*.cli.command", "@*.command"]
PYTHON_EXCLUDES = ["*/tests/*", "*/test/*", "*/mutants/*", "*/.venv/*", "*/__pycache__/*"]
TS_KINDS = ["files", "exports", "types"]


def run_gate(ctx: Context) -> Result:
    started = time.time()
    findings = python_findings(ctx) + ts_findings(ctx) + elixir_findings(ctx) + ruby_findings(ctx)
    if ctx.scope_changed:
        findings = [f for f in findings if f.split(":")[0] in ctx.changed]
    summary = ("nothing unreachable" if not findings else f"{len(findings)} dead definitions") + elixir_note(ctx)
    return Result("deadcode", not findings, summary, findings, time.time() - started)


def python_findings(ctx: Context) -> list[str]:
    if ctx.config.section("python") is None:
        return []
    root = ctx.python_root()
    kinds = set(ctx.config.get("deadcode", "python_kinds", PYTHON_KINDS))
    code, output = run(vulture_command(ctx), cwd=root, timeout=600)
    if code == 127:
        return ["vulture is not installed: uv pip install --python .venv/bin/python vulture"]
    findings = []
    for line in output.splitlines():
        match = VULTURE_LINE.match(line.strip())
        if match and match.group(3) in kinds:
            path = (root / match.group(1)).resolve().relative_to(ctx.root)
            findings.append(f"{path}:{match.group(2)} {match.group(3)} {match.group(4)}")
    if code not in (0, 3) and not findings:
        return [f"vulture failed: {output.strip()[-200:]}"]
    return findings


def vulture_command(ctx: Context) -> list[str]:
    get = ctx.config.get
    return [
        ctx.python_bin("vulture"), *ctx.python("sources", ["."]),
        "--min-confidence", str(get("deadcode", "min_confidence", 60)),
        "--exclude", ",".join(get("deadcode", "python_exclude", PYTHON_EXCLUDES)),
        "--ignore-decorators", ",".join(get("deadcode", "python_decorators", PYTHON_DECORATORS)),
        *ignore_names(get("deadcode", "python_ignore_names", [])),
    ]


def ignore_names(names: list[str]) -> list[str]:
    return ["--ignore-names", ",".join(names)] if names else []


def ts_findings(ctx: Context) -> list[str]:
    if ctx.config.section("ts") is None:
        return []
    ts_root = ctx.ts_root()
    kinds = ctx.config.get("deadcode", "ts_kinds", TS_KINDS)
    code, output = run(["npx", "--yes", "knip", "--reporter", "json", "--no-progress"], cwd=ts_root, timeout=900)
    start = output.find("{")
    if start < 0:
        return [f"knip produced no report: {output.strip()[-200:]}"]
    report, _ = json.JSONDecoder().raw_decode(output[start:])
    prefix = ts_root.relative_to(ctx.root)
    findings = [f"{prefix / file} unused file" for file in report.get("files", []) if "files" in kinds]
    for issue in report.get("issues", []):
        file = prefix / issue.get("file", "")
        for kind in kinds:
            if kind == "files":
                continue
            for item in issue.get(kind, []):
                findings.append(describe(file, kind, item))
    return findings


def describe(file: Path, kind: str, item) -> str:
    name = item.get("name", "") if isinstance(item, dict) else str(item)
    line = item.get("line", 0) if isinstance(item, dict) else 0
    return f"{file}:{line} unused {kind.rstrip('s')} '{name}'"


def ruby_findings(ctx: Context) -> list[str]:
    if ctx.config.section("ruby") is None:
        return []
    from marestail.gates.rb_crap import ruby_sources
    from marestail.ruby import scan

    files = ruby_sources(ctx)
    if not files:
        return []
    code, output = scan(ctx, "dead", files)
    if code != 0:
        return [f"ruby deadcode scanner failed: {output.strip()[-200:]}"]
    return json.loads(output or "[]")


def elixir_findings(ctx: Context) -> list[str]:
    return []


def elixir_note(ctx: Context) -> str:
    if ctx.config.section("elixir") is None:
        return ""
    return "; elixir not checked, no reachability tool"
