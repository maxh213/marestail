import json
import re
import time
from pathlib import Path

from marestail import erlang
from marestail.context import Context
from marestail.report import Result
from marestail.shell import tail

COVERAGE_JSON = "er-coverage.json"
TESTS_PASSED = re.compile(r"(\d+) tests passed")


def run_gate(ctx: Context) -> Result:
    started = time.time()
    sources = erlang.source_files(ctx)
    if not sources:
        return Result.skipped("er.tests", "no erlang sources under [erlang] sources (default src/)")
    tests = erlang.test_files(ctx)
    if not tests:
        return Result("er.tests", False, "no eunit test files", ["marestail.toml:1 no test files under [erlang] test_dirs (default test/, tests/) or *_tests.erl next to the sources"], 0.0)
    ebin = erlang.fresh_dir(ctx.work / "er-ebin")
    code, output = erlang.erlc(ctx, ["+debug_info", "-o", str(ebin), *map(str, sources)], timeout=900)
    problem = erlang.hint(code, output)
    if problem:
        return Result("er.tests", False, problem, [problem], time.time() - started)
    if code != 0:
        return Result("er.tests", False, "sources failed to compile", tail(output), time.time() - started)
    test_ebin = erlang.fresh_dir(ctx.work / "er-test-ebin")
    code, output = erlang.erlc(ctx, ["-DTEST", "+debug_info", "-pa", str(ebin), "-o", str(test_ebin), *map(str, tests)], timeout=900)
    if code != 0:
        return Result("er.tests", False, "tests failed to compile", tail(output), time.time() - started)
    out_json = ctx.work / COVERAGE_JSON
    out_json.unlink(missing_ok=True)
    code, output = erlang.escript(ctx, "eunit_cover.escript", [str(ebin), str(test_ebin), str(out_json)], timeout=1800)
    problem = erlang.hint(code, output)
    if problem:
        return Result("er.tests", False, problem, [problem], time.time() - started)
    if code == 1:
        return Result("er.tests", False, "tests failed", tail(output), time.time() - started)
    if code != 0:
        return Result("er.tests", False, "eunit run failed", tail(output), time.time() - started)
    if not out_json.exists():
        return Result("er.tests", False, "no coverage report written", tail(output), time.time() - started)
    coverage = json.loads(out_json.read_text())
    findings = coverage_findings(coverage, ctx)
    percent = coverage["totals"]["percent_covered"]
    scope = " on changed files" if ctx.scope_changed else ""
    summary = f"{count_tests(output)} passed, coverage {percent:.1f}%, {len(findings)} gaps{scope} (need 0)"
    return Result("er.tests", not findings, summary, findings, time.time() - started)


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
        match = TESTS_PASSED.search(line)
        if match:
            return match.group(1)
        if "Test passed" in line:
            return "1"
    return "?"
