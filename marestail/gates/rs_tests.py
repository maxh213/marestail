import json
import re
import time

from marestail import rust
from marestail.context import Context
from marestail.report import Result
from marestail.shell import tail

RAW_JSON = "rs-llvm-cov.json"
LCOV = "rs-lcov.info"
CODE_REGION = 0
PASSED = re.compile(r"^test result: \w+\. (\d+) passed", re.MULTILINE)


def run_gate(ctx: Context) -> Result:
    started = time.time()
    ignore = ["--ignore-filename-regex", ctx.rust("coverage_ignore_regex")] if ctx.rust("coverage_ignore_regex") else []
    code, output = rust.cargo(ctx, ["llvm-cov", "--no-report", *rust.listify(ctx.rust("test_args", []))], timeout=3600)
    problem = rust.missing(code, output, "llvm-cov")
    if problem:
        return Result("rs.tests", False, "cargo llvm-cov missing", [problem], time.time() - started)
    if code != 0:
        return Result("rs.tests", False, "tests failed", tail(output), time.time() - started)
    for flag, name in (("--json", RAW_JSON), ("--lcov", LCOV)):
        (ctx.work / name).unlink(missing_ok=True)
        report_code, report_output = rust.cargo(
            ctx, ["llvm-cov", "report", flag, *ignore, "--output-path", str(ctx.work / name)], timeout=600
        )
        if report_code != 0 or not (ctx.work / name).exists():
            return Result("rs.tests", False, f"cargo llvm-cov report {flag} produced no report", tail(report_output), time.time() - started)
    coverage = merge(ctx)
    if not coverage["files"]:
        return Result("rs.tests", False, "coverage report lists no source files", tail(output), time.time() - started)
    (ctx.work / "rs-coverage.json").write_text(json.dumps(coverage))
    findings = coverage_findings(coverage, ctx)
    passed = sum(int(count) for count in PASSED.findall(output))
    scope = " on changed files" if ctx.scope_changed else ""
    summary = f"{passed} passed, line coverage {coverage['totals']['percent_covered']:.1f}%, {len(findings)} gaps{scope} (need 0)"
    return Result("rs.tests", not findings, summary, findings, time.time() - started)


def merge(ctx: Context) -> dict:
    files: dict[str, dict] = {}
    current = None
    for line in (ctx.work / LCOV).read_text().splitlines():
        if line.startswith("SF:"):
            current = files.setdefault(rust.rel(ctx, line[3:]), {"lines": {}, "regions": {}})
        elif line.startswith("DA:") and current is not None:
            number, hits = line[3:].split(",")[:2]
            current["lines"][number] = max(current["lines"].get(number, 0), int(hits))
    for export in json.loads((ctx.work / RAW_JSON).read_text()).get("data", []):
        for function in export.get("functions", []):
            names = function.get("filenames", [])
            for start, column, end, _end_column, count, file_id, _expanded, kind in function.get("regions", []):
                if kind != CODE_REGION or file_id >= len(names):
                    continue
                entry = files.setdefault(rust.rel(ctx, names[file_id]), {"lines": {}, "regions": {}})
                key = f"{start}:{column}"
                entry["regions"][key] = max(entry["regions"].get(key, 0), count)
    total = sum(len(data["lines"]) for data in files.values())
    covered = sum(1 for data in files.values() for hits in data["lines"].values() if hits > 0)
    return {"files": files, "totals": {"percent_covered": covered / total * 100.0 if total else 100.0}}


def coverage_findings(coverage: dict, ctx: Context) -> list[str]:
    findings = []
    for file, data in sorted(coverage["files"].items()):
        if not ctx.in_scope(file):
            continue
        missing = sorted(int(number) for number, hits in data["lines"].items() if hits == 0)
        findings.extend(f"{file}:{number} not covered" for number in missing)
        for key in sorted((k for k, hits in data["regions"].items() if hits == 0), key=lambda k: tuple(map(int, k.split(":")))):
            number, column = map(int, key.split(":"))
            if number not in missing:
                findings.append(f"{file}:{number} code at column {column} never runs")
    return findings
