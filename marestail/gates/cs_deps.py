import fnmatch
import json
import time
from typing import Any

from marestail import dotnet
from marestail.context import Context
from marestail.gates._cycles import cycle_findings as cycle_findings
from marestail.gates._cycles import dependency_graph as dependency_graph
from marestail.gates._cycles import strongly_connected as strongly_connected
from marestail.gates._cycles import under as under
from marestail.report import Result, elapsed

GATE = "cs.deps"
LAYERS_FILE = ".dotnet-layers.json"
MAX_LINES = 60


def run_gate(ctx: Context) -> Result:
    started = time.time()
    contract = ctx.root / LAYERS_FILE
    if not contract.exists():
        return Result(
            GATE, False, f"no {LAYERS_FILE}; copy templates/dotnet-layers.json and name the layers", [f"{LAYERS_FILE}:1 missing"], 0.0
        )
    layers = json.loads(contract.read_text())["layers"]
    files = dotnet.sources(ctx)
    if not files:
        return Result.skipped(GATE, "no C# sources")
    data, error = dotnet.scan(ctx, "deps", files)
    if error:
        return Result(GATE, False, error, [], elapsed(started))
    findings = scoped(ctx, layer_findings(ctx, layers, data) + cycle_findings(data["edges"]))
    return Result(GATE, not findings, summary(findings), findings[:MAX_LINES], elapsed(started))


def scoped(ctx: Context, findings: list[str]) -> list[str]:
    if not ctx.scoped:
        return findings
    return [f for f in findings if ctx.in_scope(f.split(":", 1)[0])]


def summary(findings: list[str]) -> str:
    return f"{len(findings)} layer breaks" if findings else "layer contracts kept"


def layer_findings(ctx: Context, layers: list[dict[str, Any]], data: dict[str, Any]) -> list[str]:
    prefix = dotnet.root_prefix(ctx)
    return edge_findings(prefix, layers, data["edges"]) + using_findings(prefix, layers, data["files"])


def governing(prefix: str, layers: list[dict[str, Any]], path: str) -> list[dict[str, Any]]:
    return [layer for layer in layers if under(path, prefix + layer["from"])]


def edge_findings(prefix: str, layers: list[dict[str, Any]], edges: list[dict[str, Any]]) -> list[str]:
    return [found for edge in edges for layer in governing(prefix, layers, edge["from"]) for found in edge_breaks(prefix, layer, edge)]


def edge_breaks(prefix: str, layer: dict[str, Any], edge: dict[str, Any]) -> list[str]:
    return [
        f"{edge['from']}:{edge['line']} {layer['from']} must not depend on {ban} ({edge['symbol']})"
        for ban in layer.get("forbid", [])
        if under(edge["to"], prefix + ban)
    ]


def using_findings(prefix: str, layers: list[dict[str, Any]], records: list[dict[str, Any]]) -> list[str]:
    return [found for record in records for layer in governing(prefix, layers, record["path"]) for found in using_breaks(layer, record)]


def using_breaks(layer: dict[str, Any], record: dict[str, Any]) -> list[str]:
    return [
        f"{record['path']}:{using['line']} {layer['from']} must not depend on {using['name']} (matches {pattern})"
        for using in record["usings"]
        for pattern in layer.get("forbid_external", [])
        if fnmatch.fnmatch(using["name"], pattern)
    ]
