import json
import time
from pathlib import Path

from marestail.context import Context
from marestail.report import Result
from marestail.shell import run, tail

COVERAGE_SCRIPT = Path(__file__).resolve().parent.parent / "ex" / "coverage.exs"
COVERAGE_JSON = "ex-coverage.json"


def run_gate(ctx: Context) -> Result:
    started = time.time()
    root = ctx.elixir_root()
    coverdata = root / "cover" / "default.coverdata"
    code, output = run(["mix", "test", "--cover", "--export-coverage", "default"], cwd=root, timeout=1800)
    if code != 0:
        return Result("ex.tests", False, "tests failed", tail(output), time.time() - started)
    if not coverdata.exists():
        return Result("ex.tests", False, "no coverdata exported", tail(output), time.time() - started)
    out_json = ctx.work / COVERAGE_JSON
    c_code, c_output = run(["elixir", str(COVERAGE_SCRIPT), str(coverdata), str(out_json)], cwd=root, timeout=300)
    if c_code != 0 or not out_json.exists():
        return Result("ex.tests", False, "coverage extraction failed", tail(c_output), time.time() - started)
    coverage = json.loads(out_json.read_text())
    findings = coverage_findings(coverage, ctx)
    percent = coverage["totals"]["percent_covered"]
    scope = " on changed files" if ctx.scope_changed else ""
    summary = f"{count_tests(output)} passed, coverage {percent:.1f}%, {len(findings)} gaps{scope} (need 0)"
    return Result("ex.tests", not findings, summary, findings, time.time() - started)


def coverage_findings(coverage: dict, ctx: Context) -> list[str]:
    findings = []
    for file_str, data in sorted(coverage["files"].items()):
        relative = relative_path(file_str, ctx)
        if ctx.scope_changed and relative not in ctx.changed:
            continue
        findings.extend(f"{relative}:{line} not covered" for line in data.get("missing_lines", []))
    return findings


def relative_path(file_str: str, ctx: Context) -> str:
    path = Path(file_str)
    try:
        return str(path.resolve().relative_to(ctx.root.resolve()))
    except ValueError:
        return file_str


def count_tests(output: str) -> str:
    for line in output.splitlines():
        if "passed" in line:
            parts = line.strip().split()
            for i, p in enumerate(parts):
                if p == "passed" and i > 0:
                    return parts[i - 1]
    return "?"
