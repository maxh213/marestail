import json
import time
from pathlib import Path

from marestail.context import Context
from marestail.gleam import extract_coverage
from marestail.report import Result
from marestail.shell import run, tail

COVERAGE_JSON = "gl-coverage.json"


def run_gate(ctx: Context) -> Result:
    started = time.time()
    root = ctx.gleam_root()
    if not (root / "gleam.toml").exists():
        return Result("gl.tests", False, "no gleam.toml", [], time.time() - started)
    code, output = run(["gleam", "test"], cwd=root, timeout=1800)
    if code == 127:
        return Result("gl.tests", False, "gleam missing", ["install gleam from https://gleam.run"], time.time() - started)
    if code != 0:
        return Result("gl.tests", False, "tests failed", tail(output), time.time() - started)
    out_json = ctx.work / COVERAGE_JSON
    c_code, c_output = extract_coverage(ctx, out_json)
    if c_code != 0 or not out_json.exists():
        return Result(
            "gl.tests",
            False,
            "coverage extraction failed (Erlang cover after gleam test)",
            tail(c_output),
            time.time() - started,
        )
    coverage = json.loads(out_json.read_text())
    findings = coverage_findings(coverage, ctx)
    percent = coverage["totals"]["percent_covered"]
    scope = " on changed files" if ctx.scope_changed else ""
    summary = f"{count_tests(output)} passed, coverage {percent:.1f}% via Erlang cover, {len(findings)} gaps{scope} (need 0)"
    return Result("gl.tests", not findings, summary, findings, time.time() - started)


def coverage_findings(coverage: dict, ctx: Context) -> list[str]:
    findings = []
    for file, data in sorted(coverage["files"].items()):
        relative = relative_path(file, ctx)
        if ctx.scope_changed and relative not in ctx.changed:
            continue
        findings.extend(f"{relative}:{line} not covered" for line in data.get("missing_lines", []))
    return findings


def relative_path(file_str: str, ctx: Context) -> str:
    path = Path(file_str)
    if not path.is_absolute():
        path = ctx.gleam_root() / path
    try:
        return str(path.resolve().relative_to(ctx.root.resolve()))
    except ValueError:
        return file_str


def count_tests(output: str) -> str:
    for line in reversed(output.splitlines()):
        if "passed" in line:
            return line.strip().split()[0]
    return "?"
