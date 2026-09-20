import json
import re
import time
from pathlib import Path
from typing import Any

from marestail.context import Context
from marestail.gates._coverage import RB_COVERAGE as COVERAGE_JSON
from marestail.gates._coverage import relative_path
from marestail.report import Result
from marestail.ruby import bundle
from marestail.shell import run, tail

GATE = "rb.tests"
RESULTSET = Path("coverage/.resultset.json")
BRANCH_SPAN = re.compile(r"\[\s*:\w+\s*,\s*\d+\s*,\s*(\d+)\s*,\s*\d+\s*,\s*(\d+)\s*,\s*\d+\s*\]")


def run_gate(ctx: Context) -> Result:
    started = time.time()
    root = ctx.ruby_root()
    code, output = run(bundle(ctx, "rspec"), cwd=root, timeout=1800)
    if code != 0:
        return Result(GATE, False, "tests failed", tail(output), time.time() - started)
    resultset = root / RESULTSET
    if not resultset.exists():
        return Result(GATE, False, "no coverage/.resultset.json; SimpleCov must run with rspec", tail(output), time.time() - started)
    coverage = load_resultset(resultset, ctx)
    percent = percent_covered(coverage, ctx)
    if ctx.scoped:
        coverage["totals"]["scoped_percent_covered"] = percent
    (ctx.work / COVERAGE_JSON).write_text(json.dumps(coverage))
    findings = coverage_findings(coverage, ctx)
    summary = f"{count_examples(output)} passed, coverage {percent:.1f}%, {len(findings)} gaps{scope_note(ctx)} (need 0)"
    return Result(GATE, not findings, summary, findings, time.time() - started)


def scope_note(ctx: Context) -> str:
    return f" scoped ({ctx.scope_summary()})" if ctx.scoped else ""


def load_resultset(path: Path, ctx: Context) -> dict[str, Any]:
    raw = json.loads(path.read_text())
    files: dict[str, Any] = {}
    for suite in raw.values():
        for file, data in (suite.get("coverage") or {}).items():
            files[relative_path(file, ctx)] = file_entry(data, ctx)
    return {"files": files, "totals": {"percent_covered": overall_percent(files)}}


def line_hits(data: Any) -> list[Any]:
    lines = data.get("lines") if isinstance(data, dict) else data
    return lines or []


def file_entry(data: Any, ctx: Context) -> dict[str, Any]:
    lines = line_hits(data)
    branches, spans = missing_branches(data, ctx)
    entry: dict[str, Any] = {
        "missing_lines": [index for index, hits in enumerate(lines, start=1) if hits == 0],
        "missing_branches": branches,
        "lines": lines,
    }
    if ctx.scoped:
        entry["branch_lines"] = spans
    return entry


def branch_table(data: Any) -> dict[str, Any]:
    table: dict[str, Any] = (data.get("branches") or {}) if isinstance(data, dict) else {}
    return table


def missed_arms(arms: Any) -> list[str]:
    if not isinstance(arms, dict):
        return []
    return [name for name, hits in arms.items() if hits == 0]


def missing_branches(data: Any, ctx: Context) -> tuple[list[str], dict[str, list[int]]]:
    branches: list[str] = []
    spans: dict[str, list[int]] = {}
    for condition, arms in branch_table(data).items():
        for name in missed_arms(arms):
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


def coverage_findings(coverage: dict[str, Any], ctx: Context) -> list[str]:
    return [
        finding
        for file, data in sorted(coverage["files"].items())
        if ctx.in_scope(file)
        for finding in file_findings(file, data, ctx.gated_lines(file))
    ]


def file_findings(file: str, data: dict[str, Any], gated: set[int] | None) -> list[str]:
    lines = [f"{file}:{line} not covered" for line in data.get("missing_lines", []) if line_gated(line, gated)]
    return lines + untaken_arms(file, data, gated)


def untaken_arms(file: str, data: dict[str, Any], gated: set[int] | None) -> list[str]:
    spans = data.get("branch_lines", {})
    return [f"{file} branch {arm} not taken" for arm in data.get("missing_branches", []) if arm_gated(spans.get(arm, []), gated)]


def line_gated(line: int, gated: set[int] | None) -> bool:
    return gated is None or line in gated


def arm_gated(lines: list[int], gated: set[int] | None) -> bool:
    return gated is None or not lines or not gated.isdisjoint(lines)


def is_hit(hits: Any) -> bool:
    return isinstance(hits, int) and hits > 0


def counts_line(index: int, hits: Any, gated: set[int] | None) -> bool:
    return hits is not None and line_gated(index, gated)


def measured(lines: list[Any], gated: set[int] | None) -> list[Any]:
    return [hits for index, hits in enumerate(lines, start=1) if counts_line(index, hits, gated)]


def percent_of(hits_list: list[Any]) -> float:
    covered = sum(1 for hits in hits_list if is_hit(hits))
    return (covered / len(hits_list) * 100.0) if hits_list else 100.0


def overall_percent(files: dict[str, Any]) -> float:
    return percent_of([hits for entry in files.values() for hits in measured(entry["lines"], None)])


def file_hits(file: str, data: dict[str, Any], ctx: Context) -> list[Any]:
    return measured(data.get("lines") or [], ctx.gated_lines(file))


def percent_covered(coverage: dict[str, Any], ctx: Context) -> float:
    if not ctx.scoped:
        percent: float = coverage["totals"]["percent_covered"]
        return percent
    return percent_of(scoped_hits(coverage, ctx))


def scoped_hits(coverage: dict[str, Any], ctx: Context) -> list[Any]:
    return [hits for file, data in coverage["files"].items() if ctx.in_scope(file) for hits in file_hits(file, data, ctx)]


def count_examples(output: str) -> str:
    for line in reversed(output.splitlines()):
        if "example" in line and "failure" in line:
            return line.strip().split(" example")[0].split()[-1]
    return "?"
