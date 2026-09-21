import json
import re
import time
from typing import Any

from marestail import rust
from marestail.context import Context
from marestail.report import Result, elapsed
from marestail.shell import tail

GATE = "rs.tests"
RAW_JSON = "rs-llvm-cov.json"
LCOV = "rs-lcov.info"
REPORTS = (("--json", RAW_JSON), ("--lcov", LCOV))
CODE_REGION = 0
PASSED = re.compile(r"^test result: \w+\. (\d+) passed", re.MULTILINE)

Entry = dict[str, dict[str, int]]


def extra_args(ctx: Context) -> list[str]:
    return rust.configured_list(ctx.rust("test_args", rust.EMPTY))


def run_gate(ctx: Context) -> Result:
    started = time.time()
    ignore = ignore_args(ctx)
    code, output = rust.cargo(ctx, ["llvm-cov", "--no-report", *extra_args(ctx)], timeout=3600)
    return test_failure(code, output, started) or report_failure(ctx, ignore, started) or coverage_result(ctx, output, started)


def ignore_args(ctx: Context) -> list[str]:
    pattern = ctx.rust("coverage_ignore_regex")
    return ["--ignore-filename-regex", pattern] if pattern else []


def test_failure(code: int, output: str, started: float) -> Result | None:
    problem = rust.missing(code, output, "llvm-cov")
    if problem:
        return Result(GATE, False, "cargo llvm-cov missing", [problem], elapsed(started))
    if code != 0:
        return Result(GATE, False, "tests failed", tail(output), elapsed(started))
    return None


def report_failure(ctx: Context, ignore: list[str], started: float) -> Result | None:
    return next((failure for flag, name in REPORTS if (failure := write_report(ctx, flag, name, ignore, started))), None)


def write_report(ctx: Context, flag: str, name: str, ignore: list[str], started: float) -> Result | None:
    path = ctx.work / name
    path.unlink(missing_ok=True)
    code, output = rust.cargo(ctx, ["llvm-cov", "report", flag, *ignore, "--output-path", str(path)], timeout=600)
    if code != 0 or not path.exists():
        return Result(GATE, False, f"cargo llvm-cov report {flag} produced no report", tail(output), elapsed(started))
    return None


def coverage_result(ctx: Context, output: str, started: float) -> Result:
    coverage = merge(ctx)
    if not coverage["files"]:
        return Result(GATE, False, "coverage report lists no source files", tail(output), elapsed(started))
    (ctx.work / "rs-coverage.json").write_text(json.dumps(coverage))
    findings = coverage_findings(coverage, ctx)
    passed = sum(int(count) for count in PASSED.findall(output))
    scope = " on changed files" if ctx.scope_changed else ""
    summary = f"{passed} passed, line coverage {coverage['totals']['percent_covered']:.1f}%, {len(findings)} gaps{scope} (need 0)"
    return Result(GATE, not findings, summary, findings, elapsed(started))


def merge(ctx: Context) -> dict[str, Any]:
    files: dict[str, Entry] = {}
    merge_lcov(ctx, files)
    merge_regions(ctx, files)
    return {"files": files, "totals": {"percent_covered": line_percent(files)}}


def entry_for(files: dict[str, Entry], file: str) -> Entry:
    return files.setdefault(file, {"lines": {}, "regions": {}})


def keep_max(table: dict[str, int], key: str, value: int) -> None:
    table[key] = max(table.get(key, 0), value)


def merge_lcov(ctx: Context, files: dict[str, Entry]) -> None:
    current: Entry | None = None
    for line in (ctx.work / LCOV).read_text().splitlines():
        current = lcov_line(ctx, files, current, line)


SF = "SF:"
DA = "DA:"
COMMA = ","


def da_hits(line: str) -> tuple[str, str]:
    body = line[len(DA) :]
    index = body.index(COMMA)
    rest = body[index + 1 :]
    if COMMA in rest:
        rest = rest[: rest.index(COMMA)]
    return body[:index], rest


def lcov_line(ctx: Context, files: dict[str, Entry], current: Entry | None, line: str) -> Entry | None:
    if line.startswith(SF):
        return entry_for(files, rust.rel(ctx, line[len(SF) :]))
    if line.startswith(DA) and current is not None:
        number, hits = da_hits(line)
        keep_max(current["lines"], number, int(hits))
    return current


def merge_regions(ctx: Context, files: dict[str, Entry]) -> None:
    for export in json.loads((ctx.work / RAW_JSON).read_text()).get("data", []):
        for function in export.get("functions", []):
            merge_function(ctx, files, function)


def merge_function(ctx: Context, files: dict[str, Entry], function: dict[str, Any]) -> None:
    names = function.get("filenames", [])
    for start, column, _end, _end_column, count, file_id, _expanded, kind in function.get("regions", []):
        if kind == CODE_REGION and file_id < len(names):
            keep_max(entry_for(files, rust.rel(ctx, names[file_id]))["regions"], f"{start}:{column}", count)


def covered_lines(files: dict[str, Entry]) -> int:
    return sum(1 for data in files.values() for hits in data["lines"].values() if hits > 0)


def line_percent(files: dict[str, Entry]) -> float:
    total = sum(len(data["lines"]) for data in files.values())
    return covered_lines(files) / total * 100.0 if total else 100.0


def coverage_findings(coverage: dict[str, Any], ctx: Context) -> list[str]:
    return [finding for file, data in sorted(coverage["files"].items()) if ctx.in_scope(file) for finding in file_gaps(file, data)]


def file_gaps(file: str, data: Entry) -> list[str]:
    missing = sorted(int(number) for number, hits in data["lines"].items() if hits == 0)
    return [f"{file}:{number} not covered" for number in missing] + region_gaps(file, data, missing)


def region_gaps(file: str, data: Entry, missing: list[int]) -> list[str]:
    return [f"{file}:{number} code at column {column} never runs" for number, column in dead_regions(data) if number not in missing]


def region_position(key: str) -> tuple[int, int]:
    number, column = key.split(":")
    return int(number), int(column)


def dead_regions(data: Entry) -> list[tuple[int, int]]:
    return sorted(region_position(key) for key, hits in data["regions"].items() if hits == 0)
