import fnmatch
import json
import time

from marestail import java
from marestail.context import Context
from marestail.gates.cs_deps import cycle_findings, under
from marestail.report import Result

LAYERS_FILE = ".java-layers.json"
MAX_LINES = 60


def run_gate(ctx: Context) -> Result:
    started = time.time()
    contract = ctx.root / LAYERS_FILE
    if not contract.exists():
        return Result("java.deps", False, f"no {LAYERS_FILE}; copy templates/java-layers.json and name the layers", [f"{LAYERS_FILE}:1 missing"], 0.0)
    layers = json.loads(contract.read_text())["layers"]
    files = java.sources(ctx)
    if not files:
        return Result.skipped("java.deps", "no Java sources")
    data, error = java.scan(ctx, "deps", files)
    if error:
        return Result("java.deps", False, error, [], time.time() - started)
    findings = layer_findings(ctx, layers, data) + cycle_findings(data["edges"])
    if ctx.scoped:
        findings = [f for f in findings if ctx.in_scope(f.split(":", 1)[0])]
    summary = "layer contracts kept" if not findings else f"{len(findings)} layer breaks"
    return Result("java.deps", not findings, summary, findings[:MAX_LINES], time.time() - started)


def layer_findings(ctx: Context, layers: list[dict], data: dict) -> list[str]:
    prefix = java.rel(ctx, ctx.java_root())
    prefix = "" if prefix == "." else prefix + "/"
    packages = {record["path"]: record["package"] for record in data["files"]}
    findings = []
    for edge in data["edges"]:
        for layer in layers:
            if not inside(edge["from"], packages.get(edge["from"], ""), layer["from"], prefix):
                continue
            for ban in layer.get("forbid", []):
                if inside(edge["to"], edge["toPackage"], ban, prefix):
                    findings.append(f"{edge['from']}:{edge['line']} {layer['from']} must not depend on {ban} ({edge['symbol']})")
    for record in data["files"]:
        for layer in layers:
            if not inside(record["path"], record["package"], layer["from"], prefix):
                continue
            for imported in record["imports"]:
                for pattern in layer.get("forbid_external", []):
                    if fnmatch.fnmatch(imported["name"], pattern):
                        findings.append(f"{record['path']}:{imported['line']} {layer['from']} must not depend on {imported['name']} (matches {pattern})")
    return findings


def inside(path: str, package: str, layer: str, prefix: str) -> bool:
    if "/" in layer:
        return under(path, prefix + layer)
    return package == layer or package.startswith(layer + ".")
