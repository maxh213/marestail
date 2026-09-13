import json
import re
import time
from pathlib import Path

from marestail.context import Context
from marestail.report import Result
from marestail.ruby import bundle
from marestail.shell import run, tail

COVERAGE_JSON = "rb-coverage.json"
RESULTSET = Path("coverage/.resultset.json")
BRANCH_SPAN = re.compile(r"\[\s*:\w+\s*,\s*\d+\s*,\s*(\d+)\s*,\s*\d+\s*,\s*(\d+)\s*,\s*\d+\s*\]")


def run_gate(ctx: Context) -> Result:
    started = time.time()
    root = ctx.ruby_root()
    code, output = run(bundle(ctx, "rspec"), cwd=root, timeout=1800)
    if code != 0:
        return Result("rb.tests", False, "tests failed", tail(output), time.time() - started)
    resultset = root / RESULTSET
    if not resultset.exists():
        return Result("rb.tests", False, "no coverage/.resultset.json; SimpleCov must run with rspec", tail(output), time.time() - started)
    coverage = load_resultset(resultset, ctx)
    percent = percent_covered(coverage, ctx)
    if ctx.scoped:
        coverage["totals"]["scoped_percent_covered"] = percent
    (ctx.work / COVERAGE_JSON).write_text(json.dumps(coverage))
    findings = coverage_findings(coverage, ctx)
    scope = f" scoped ({ctx.scope_summary()})" if ctx.scoped else ""
    summary = f"{count_examples(output)} passed, coverage {percent:.1f}%, {len(findings)} gaps{scope} (need 0)"
    return Result("rb.tests", not findings, summary, findings, time.time() - started)


def load_resultset(path: Path, ctx: Context) -> dict:
    raw = json.loads(path.read_text())
    files: dict = {}
    for suite in raw.values():
        for file, data in (suite.get("coverage") or {}).items():
            relative = relative_path(file, ctx)
            lines = data.get("lines") if isinstance(data, dict) else data
            missing = [i for i, hits in enumerate(lines or [], start=1) if hits == 0]
            branches, spans = missing_branches(data, ctx)
            entry = {"missing_lines": missing, "missing_branches": branches, "lines": lines or []}
            if ctx.scoped:
                entry["branch_lines"] = spans
            files[relative] = entry
    total = sum(1 for f in files.values() for hits in f["lines"] if hits is not None)
    covered = sum(1 for f in files.values() for hits in f["lines"] if isinstance(hits, int) and hits > 0)
    percent = (covered / total * 100.0) if total else 100.0
    return {"files": files, "totals": {"percent_covered": percent}}


def missing_branches(data, ctx: Context) -> tuple[list[str], dict[str, list[int]]]:
    branches: list[str] = []
    spans: dict[str, list[int]] = {}
    if not isinstance(data, dict):
        return branches, spans
    for condition, arms in (data.get("branches") or {}).items():
        if not isinstance(arms, dict):
            continue
        for name, hits in arms.items():
            if hits == 0:
                branches.append(name)
                if ctx.scoped:
                    spans[name] = sorted(branch_lines(name) | start_line(condition))
    return branches, spans


def start_line(key: str) -> set[int]:
    match = BRANCH_SPAN.search(key)
    return {int(match.group(1))} if match else set()


def branch_lines(key: str) -> set[int]:
    match = BRANCH_SPAN.search(key)
    if not match:
        return set()
    start, end = int(match.group(1)), int(match.group(2))
    return set(range(min(start, end), max(start, end) + 1))


def coverage_findings(coverage: dict, ctx: Context) -> list[str]:
    findings = []
    for file, data in sorted(coverage["files"].items()):
        if ctx.scoped and not ctx.in_scope(file):
            continue
        gated = ctx.gated_lines(file) if ctx.scoped else None
        spans = data.get("branch_lines", {})
        findings.extend(f"{file}:{line} not covered" for line in data.get("missing_lines", []) if gated is None or line in gated)
        findings.extend(
            f"{file} branch {arm} not taken"
            for arm in data.get("missing_branches", [])
            if gated is None or arm_gated(spans.get(arm, []), gated)
        )
    return findings


def arm_gated(lines: list[int], gated: set[int]) -> bool:
    return not lines or not gated.isdisjoint(lines)


def percent_covered(coverage: dict, ctx: Context) -> float:
    if not ctx.scoped:
        return coverage["totals"]["percent_covered"]
    total = 0
    covered = 0
    for file, data in coverage["files"].items():
        if not ctx.in_scope(file):
            continue
        gated = ctx.gated_lines(file)
        for index, hits in enumerate(data.get("lines") or [], start=1):
            if hits is None or (gated is not None and index not in gated):
                continue
            total += 1
            covered += 1 if isinstance(hits, int) and hits > 0 else 0
    return (covered / total * 100.0) if total else 100.0


def relative_path(file: str, ctx: Context) -> str:
    path = Path(file)
    try:
        return str(path.resolve().relative_to(ctx.root.resolve()))
    except ValueError:
        return file


def count_examples(output: str) -> str:
    for line in reversed(output.splitlines()):
        if "example" in line and "failure" in line:
            return line.strip().split(" example")[0].split()[-1]
    return "?"
