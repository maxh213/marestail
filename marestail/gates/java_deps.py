import fnmatch
import json
import time
from typing import Any

from marestail import java
from marestail.context import Context
from marestail.gates._coverage import finding_file
from marestail.gates._cycles import cycle_findings, under
from marestail.report import Result, elapsed

GATE = "java.deps"
LAYERS_FILE = ".java-layers.json"
MAX_LINES = 60
FROM = "from"
PATH = "path"


def run_gate(ctx: Context) -> Result:
    started = time.time()
    contract = ctx.root / LAYERS_FILE
    if not contract.exists():
        return Result(GATE, False, f"no {LAYERS_FILE}; copy templates/java-layers.json and name the layers", [f"{LAYERS_FILE}:1 missing"])
    layers = json.loads(contract.read_text())["layers"]
    files = java.sources(ctx)
    if not files:
        return Result.skipped(GATE, "no Java sources")
    data, error = java.scan(ctx, "deps", files)
    if error:
        return Result(GATE, False, error, [], elapsed(started))
    return verdict(scoped_findings(ctx, layer_findings(ctx, layers, data) + cycle_findings(data["edges"])), started)


def verdict(findings: list[str], started: float) -> Result:
    summary = "layer contracts kept" if not findings else f"{len(findings)} layer breaks"
    return Result(GATE, not findings, summary, findings[:MAX_LINES], elapsed(started))


def scoped_findings(ctx: Context, findings: list[str]) -> list[str]:
    if not ctx.scoped:
        return findings
    return [f for f in findings if ctx.in_scope(finding_file(f))]


def layer_findings(ctx: Context, layers: list[dict[str, Any]], data: dict[str, Any]) -> list[str]:
    prefix = java.root_prefix(ctx)
    packages = {record[PATH]: record["package"] for record in data["files"]}
    edges = [finding for edge in data["edges"] for finding in edge_breaks(layers, prefix, edge, packages.get(edge[FROM], ""))]
    return edges + import_breaks(layers, prefix, data["files"])


def owning(layers: list[dict[str, Any]], prefix: str, path: str, package: str) -> list[dict[str, Any]]:
    return [layer for layer in layers if inside(path, package, layer[FROM], prefix)]


def edge_breaks(layers: list[dict[str, Any]], prefix: str, edge: dict[str, Any], package: str) -> list[str]:
    return [
        f"{edge[FROM]}:{edge['line']} {layer[FROM]} must not depend on {ban} ({edge['symbol']})"
        for layer in owning(layers, prefix, edge[FROM], package)
        for ban in layer.get("forbid", [])
        if inside(edge["to"], edge["toPackage"], ban, prefix)
    ]


def import_breaks(layers: list[dict[str, Any]], prefix: str, records: list[dict[str, Any]]) -> list[str]:
    return [
        finding
        for record in records
        for layer in owning(layers, prefix, record[PATH], record["package"])
        for finding in forbidden_imports(record, layer)
    ]


def forbidden_imports(record: dict[str, Any], layer: dict[str, Any]) -> list[str]:
    return [
        f"{record[PATH]}:{imported['line']} {layer[FROM]} must not depend on {imported['name']} (matches {pattern})"
        for imported in record["imports"]
        for pattern in layer.get("forbid_external", [])
        if fnmatch.fnmatch(imported["name"], pattern)
    ]


def inside(path: str, package: str, layer: str, prefix: str) -> bool:
    if "/" in layer:
        return under(path, prefix + layer)
    return package == layer or package.startswith(layer + ".")
