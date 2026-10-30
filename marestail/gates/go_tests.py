import json
import re
import time
from typing import Any

from marestail import golang
from marestail.context import Context
from marestail.report import Result, elapsed
from marestail.shell import run, tail

GATE = "go.tests"
NOT_READY = "go toolchain not ready"
PROFILE = "go-cover.out"
COVERAGE = "go-coverage.json"
SONAR = "go-sonar-cover.out"
BLOCK = re.compile(r"^(.+):(\d+)\.(\d+),(\d+)\.(\d+) (\d+) (\d+)$")
FILES = "files"
BLOCKS = "blocks"
MISSING = 127
Counts = dict[tuple[int, ...], int]


def run_gate(ctx: Context) -> Result:
    started = time.time()
    clear_reports(ctx)
    problem = golang.preflight(ctx)
    if problem:
        return Result(GATE, False, NOT_READY, [problem], elapsed(started))
    code, output = run(test_command(ctx), cwd=ctx.go_root(), timeout=3600)
    return covered(ctx, code, output, started)


def clear_reports(ctx: Context) -> None:
    for name in (PROFILE, COVERAGE, SONAR):
        (ctx.work / name).unlink(missing_ok=True)


def test_command(ctx: Context) -> list[str]:
    return [
        *golang.go_bin(ctx),
        "test",
        "-json",
        "-count=1",
        "-covermode=set",
        "-coverprofile",
        str(ctx.work / PROFILE),
        *golang.tags(ctx, "-tags"),
        *golang.listify(ctx.go("test_args")),
        "./...",
    ]


def covered(ctx: Context, code: int, output: str, started: float) -> Result:
    failed = test_failure(code, output, started)
    if failed is not None:
        return failed
    return profile_result(ctx, output, passed_count(events_of(output)), started)


def test_failure(code: int, output: str, started: float) -> Result | None:
    if code == MISSING:
        return Result(GATE, False, NOT_READY, [golang.go_missing()], elapsed(started))
    if code != 0:
        return Result(GATE, False, "tests failed", tail(rendered(output)), elapsed(started))
    return None


def events_of(output: str) -> list[dict[str, Any]]:
    return [record for line in output.splitlines() if (record := object_line(line)) is not None]


def object_line(line: str) -> dict[str, Any] | None:
    if not line.startswith("{"):
        return None
    try:
        record: dict[str, Any] = json.loads(line)
    except json.JSONDecodeError:
        return None
    return record


def passed_count(events: list[dict[str, Any]]) -> int:
    return sum(1 for event in events if event.get("Action") == "pass" and "Test" in event)


def rendered(output: str) -> str:
    return "".join(chunk_of(line) for line in output.splitlines())


def chunk_of(line: str) -> str:
    record = object_line(line)
    if record is None:
        return line + "\n"
    output = record.get("Output")
    return output if isinstance(output, str) else ""


def profile_result(ctx: Context, output: str, passed: int, started: float) -> Result:
    path = ctx.work / PROFILE
    if not path.is_file():
        return Result(GATE, False, "go test wrote no coverage profile", tail(output), elapsed(started))
    merged = merge_profile(ctx, path.read_text(), golang.module(ctx))
    if not merged:
        return Result(GATE, False, "coverage report lists no source files", tail(output), elapsed(started))
    return written(ctx, merged, passed, started)


def written(ctx: Context, merged: dict[str, Any], passed: int, started: float) -> Result:
    write_reports(ctx, merged)
    findings = gap_findings(ctx, merged[FILES])
    scope = " on changed files" if ctx.scoped else ""
    summary = f"{passed} passed, statement coverage {merged['totals']['percent_covered']:.1f}%, {len(findings)} gaps{scope} (need 0)"
    return Result(GATE, not findings, summary, findings, elapsed(started))


def merge_profile(ctx: Context, text: str, module: str) -> dict[str, Any]:
    files: dict[str, Counts] = {}
    for line in text.splitlines():
        add_block(ctx, files, module, line)
    ready = finalise(files)
    if not ready:
        return {}
    return {FILES: ready, "totals": {"percent_covered": percent(ready)}}


def add_block(ctx: Context, files: dict[str, Counts], module: str, line: str) -> None:
    parsed = BLOCK.match(line)
    if parsed is not None:
        keep_block(ctx, files, module, parsed)


def keep_block(ctx: Context, files: dict[str, Counts], module: str, parsed: re.Match[str]) -> None:
    relative = placed(ctx, module, parsed.group(1))
    if relative is not None:
        store_block(files, relative, numbers(parsed))


def placed(ctx: Context, module: str, imported: str) -> str | None:
    if not imported.startswith(module + "/"):
        return None
    path = ctx.go_root() / imported[len(module) + 1 :]
    if golang.generated(path):
        return None
    relative = golang.rel(ctx, path)
    if golang.excluded(ctx, relative, "coverage_exclude"):
        return None
    return relative


def numbers(parsed: re.Match[str]) -> tuple[int, int, int, int, int, int]:
    return (
        int(parsed.group(2)),
        int(parsed.group(3)),
        int(parsed.group(4)),
        int(parsed.group(5)),
        int(parsed.group(6)),
        int(parsed.group(7)),
    )


def store_block(files: dict[str, Counts], relative: str, block: tuple[int, int, int, int, int, int]) -> None:
    counts = files.setdefault(relative, {})
    counts[block[:5]] = max(counts.get(block[:5], 0), block[5])


def finalise(files: dict[str, Counts]) -> dict[str, dict[str, Any]]:
    return {name: file_entry([[*place, count] for place, count in files[name].items()]) for name in sorted(files)}


def file_entry(blocks: list[list[int]]) -> dict[str, Any]:
    return {BLOCKS: blocks, "lines": line_map(blocks)}


def line_map(blocks: list[list[int]]) -> dict[str, int]:
    lines: dict[str, int] = {}
    for block in blocks:
        key = str(block[0])
        lines[key] = min(lines.get(key, block[5]), block[5])
    return lines


def percent(files: dict[str, dict[str, Any]]) -> float:
    total = statement_total(files)
    if total == 0:
        return 0.0
    return float(f"{covered_total(files) / total * 100:.1f}")


def statement_total(files: dict[str, dict[str, Any]]) -> int:
    return sum(block[4] for data in files.values() for block in data[BLOCKS])


def covered_total(files: dict[str, dict[str, Any]]) -> int:
    return sum(covered_statements(block) for data in files.values() for block in data[BLOCKS])


def covered_statements(block: list[int]) -> int:
    if block[5] > 0:
        return block[4]
    return 0


def write_reports(ctx: Context, merged: dict[str, Any]) -> None:
    (ctx.work / COVERAGE).write_text(json.dumps(merged) + "\n")
    (ctx.work / SONAR).write_text(sonar_text(merged[FILES]))


def sonar_text(files: dict[str, dict[str, Any]]) -> str:
    lines = ["mode: set"]
    for name in sorted(files):
        lines.extend(sonar_line(name, block) for block in files[name][BLOCKS])
    return "\n".join(lines) + "\n"


def sonar_line(name: str, block: list[int]) -> str:
    return f"{name}:{block[0]}.{block[1]},{block[2]}.{block[3]} {block[4]} {block[5]}"


def gap_findings(ctx: Context, files: dict[str, dict[str, Any]]) -> list[str]:
    found: list[str] = []
    for name in sorted(files):
        if ctx.in_scope(name):
            found.extend(file_gaps(name, files[name][BLOCKS], ctx.gated_lines(name)))
    return found


def file_gaps(name: str, blocks: list[list[int]], gated: set[int] | None) -> list[str]:
    return [f"{name}:{line} not covered" for line in unique_lines(blocks, gated)]


def unique_lines(blocks: list[list[int]], gated: set[int] | None) -> list[int]:
    seen: list[int] = []
    for block in blocks:
        remember(seen, uncovered_line(block, gated))
    return seen


def remember(seen: list[int], line: int | None) -> None:
    if line is not None and line not in seen:
        seen.append(line)


def uncovered_line(block: list[int], gated: set[int] | None) -> int | None:
    if block[5] != 0 or not spans(block, gated):
        return None
    return block[0]


def spans(block: list[int], gated: set[int] | None) -> bool:
    if gated is None:
        return True
    return any(line in gated for line in range(block[0], block[2] + 1))
