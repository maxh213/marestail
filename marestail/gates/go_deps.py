import fnmatch
import json
import time
from pathlib import Path
from typing import Any

from marestail import golang
from marestail.context import Context
from marestail.gates._coverage import finding_file
from marestail.gates._cycles import under
from marestail.report import Result, elapsed

GATE = "go.deps"
LAYERS_FILE = ".go-layers.json"
MAX_LINES = 60
NOT_READY = "go toolchain not ready"
KEPT = "layer contracts kept; the go toolchain rejects import cycles and internal/ imports itself"


def run_gate(ctx: Context) -> Result:
    started = time.time()
    missing = missing_contract(ctx)
    if missing is not None:
        return missing
    files = golang.sources(ctx)
    if not files:
        return Result.skipped(GATE, "no Go sources")
    problem = golang.preflight(ctx)
    if problem:
        return Result(GATE, False, NOT_READY, [problem], elapsed(started))
    return checked(ctx, files, started)


def missing_contract(ctx: Context) -> Result | None:
    if (ctx.root / LAYERS_FILE).is_file():
        return None
    return Result(GATE, False, f"no {LAYERS_FILE}; copy templates/go-layers.json and name the layers", [f"{LAYERS_FILE}:1 missing"])


def checked(ctx: Context, files: list[Any], started: float) -> Result:
    records, error = golang.scan(ctx, "imports", files)
    if error:
        return Result(GATE, False, "import scanner failed", [error], elapsed(started))
    layers = json.loads((ctx.root / LAYERS_FILE).read_text())["layers"]
    return verdict(kept(ctx, layer_findings(ctx, layers, golang.module(ctx), records or [])), started)


def kept(ctx: Context, findings: list[str]) -> list[str]:
    return ctx.on_changed_lines([finding for finding in findings if ctx.in_scope(finding_file(finding))])


def verdict(findings: list[str], started: float) -> Result:
    summary = KEPT if not findings else f"{len(findings)} layer breaks"
    return Result(GATE, not findings, summary, findings[:MAX_LINES], elapsed(started))


def layer_findings(ctx: Context, layers: list[dict[str, Any]], module: str, records: list[dict[str, Any]]) -> list[str]:
    return [finding for record in records for finding in record_breaks(ctx, layers, module, record)]


def record_breaks(ctx: Context, layers: list[dict[str, Any]], module: str, record: dict[str, Any]) -> list[str]:
    folder = str(Path(golang.rel(ctx, record["file"])).parent)
    target = golang.import_folder(ctx, module, record["path"]) or record["path"]
    return [break_line(ctx, record, layer, ban) for layer in owning(layers, folder) for ban in bans(layer, target)]


def owning(layers: list[dict[str, Any]], folder: str) -> list[dict[str, Any]]:
    return [layer for layer in layers if under(folder, layer["from"])]


def break_line(ctx: Context, record: dict[str, Any], layer: dict[str, Any], ban: str) -> str:
    return f"{golang.rel(ctx, record['file'])}:{record['line']} {layer['from']} must not depend on {ban} ({record['path']})"


def bans(layer: dict[str, Any], target: str) -> list[str]:
    return [*folder_bans(layer, target), *external_bans(layer, target)]


def folder_bans(layer: dict[str, Any], target: str) -> list[str]:
    return [ban for ban in layer.get("forbid", []) if under(target, ban)]


def external_bans(layer: dict[str, Any], target: str) -> list[str]:
    return [ban for ban in layer.get("forbid_external", []) if fnmatch.fnmatch(target, ban)]
