import json
import time
from pathlib import Path
from typing import Any

from marestail import erlang
from marestail.context import Context
from marestail.gates._cycles import cycle_findings
from marestail.report import Result, elapsed

GATE = "er.deps"
MAX_LINES = 60
COMPILE_TIMEOUT = 900
SCAN_TIMEOUT = 600


def run_gate(ctx: Context) -> Result:
    started = time.time()
    sources = erlang.source_files(ctx)
    if not sources:
        return Result.skipped(GATE, erlang.NO_SOURCES)
    ebin = erlang.fresh_dir(ctx.work / "er-deps-ebin")
    code, output = erlang.erlc(ctx, ["+debug_info", "-o", str(ebin), *map(str, sources)], timeout=COMPILE_TIMEOUT)
    failed = erlang.trouble(code, output, "sources failed to compile")
    if failed:
        return Result(GATE, False, *failed, elapsed(started))
    code, output = erlang.escript(ctx, "deps.escript", beam_paths(ebin), timeout=SCAN_TIMEOUT)
    if code != 0:
        return Result(GATE, False, "dependency scanner failed", output.splitlines()[-10:], elapsed(started))
    return cycles_result(ctx, project_edges(json.loads(output), module_paths(ctx, sources)), started)


def beam_paths(ebin: Path) -> list[str]:
    return sorted(str(beam) for beam in ebin.glob("*.beam"))


def module_paths(ctx: Context, sources: list[Path]) -> dict[str, str]:
    return {path.stem: erlang.rel(ctx, path) for path in sources}


def project_edges(edges: list[dict[str, Any]], modules: dict[str, str]) -> list[dict[str, Any]]:
    return [
        {"from": modules[edge["from"]], "to": modules[edge["to"]], "line": edge["line"]}
        for edge in edges
        if edge["from"] in modules and edge["to"] in modules
    ]


def cycles_result(ctx: Context, edges: list[dict[str, Any]], started: float) -> Result:
    findings = erlang.in_scope_findings(ctx, cycle_findings(edges))
    summary = f"{len(findings)} dependency cycles" if findings else "dependency graph acyclic"
    return Result(GATE, not findings, summary, findings[:MAX_LINES], elapsed(started))
