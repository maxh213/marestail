import json
import time
from itertools import takewhile
from typing import Any

from marestail import erlang
from marestail.context import Context
from marestail.gates._coverage import ER_COVERAGE
from marestail.gates._coverage import coverage_findings as coverage_findings
from marestail.report import Result, elapsed
from marestail.shell import tail

COVERAGE_JSON = ER_COVERAGE
GATE = "er.tests"
TESTS_PASSED = " tests passed"
EMPTY = ""
EBIN = "er-ebin"
TEST_EBIN = "er-test-ebin"
EUNIT_FAILURES = {1: "tests failed"}


def run_gate(ctx: Context) -> Result:
    started = time.time()
    sources = erlang.source_files(ctx)
    if not sources:
        return Result.skipped(GATE, erlang.NO_SOURCES)
    tests = erlang.test_files(ctx)
    if not tests:
        return Result(GATE, False, "no eunit test files", [erlang.NO_TESTS])
    failed = erlang.compile_with_tests(ctx, sources, tests, ctx.work / EBIN, ctx.work / TEST_EBIN)
    if failed:
        return Result(GATE, False, *failed, elapsed(started))
    return run_eunit(ctx, started)


def run_eunit(ctx: Context, started: float) -> Result:
    out_json = ctx.work / COVERAGE_JSON
    out_json.unlink(missing_ok=True)
    ebin, test_ebin = ctx.work / EBIN, ctx.work / TEST_EBIN
    code, output = erlang.escript(ctx, "eunit_cover.escript", [str(ebin), str(test_ebin), str(out_json)], timeout=1800)
    failed = erlang.trouble(code, output, EUNIT_FAILURES.get(code, "eunit run failed"))
    if failed:
        return Result(GATE, False, *failed, elapsed(started))
    if not out_json.exists():
        return Result(GATE, False, "no coverage report written", tail(output), elapsed(started))
    return coverage_result(ctx, json.loads(out_json.read_text()), output, started)


def coverage_result(ctx: Context, coverage: dict[str, Any], output: str, started: float) -> Result:
    findings = coverage_findings(coverage, ctx)
    percent = coverage["totals"]["percent_covered"]
    scope = " on changed files" if ctx.scoped else ""
    summary = f"{count_tests(output)} passed, coverage {percent:.1f}%, {len(findings)} gaps{scope} (need 0)"
    return Result(GATE, not findings, summary, findings, elapsed(started))


def count_tests(output: str) -> str:
    return next((count for count in map(passed_count, output.splitlines()) if count), "?")


def passed_count(line: str) -> str:
    count = tests_passed(line)
    if count:
        return count
    return "1" if "Test passed" in line else ""


def tests_passed(line: str) -> str:
    chunks = line.split(TESTS_PASSED)[:-1]
    return next(filter(None, map(trailing_digits, chunks)), EMPTY)


def trailing_digits(text: str) -> str:
    return "".join(takewhile(str.isdecimal, reversed(text)))[::-1]
