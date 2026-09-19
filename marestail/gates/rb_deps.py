import json
import time
from pathlib import Path
from typing import Any

from marestail.context import Context
from marestail.report import Result
from marestail.ruby import scan, scanned, sources

GATE = "rb.deps"
CONTROLLERS = "app/controllers"
DEFAULT_LAYERS = [
    {
        "from": "app/models",
        "forbid": [CONTROLLERS, "app/helpers", "app/jobs", "app/mailers", "app/channels", "app/graphql", "app/views"],
    },
    {"from": "app/services", "forbid": [CONTROLLERS, "app/helpers", "app/views"]},
    {"from": "lib", "forbid": [CONTROLLERS]},
]


def run_gate(ctx: Context) -> Result:
    started = time.time()
    files = sources(ctx)
    if not files:
        return Result.skipped(GATE, "no ruby sources")
    code, output = scan(ctx, "deps", files, extra=[str(ctx.root)])
    if code != 0:
        return Result(GATE, False, "dependency scanner failed", output.splitlines()[-10:], time.time() - started)
    findings = violations(scanned(output), load_layers(ctx), ctx)
    summary = f"{len(findings)} layer breaks" if findings else "layer contracts kept"
    return Result(GATE, not findings, summary, findings[:60], time.time() - started)


def violations(edges: list[dict[str, Any]], layers: list[dict[str, Any]], ctx: Context) -> list[str]:
    return [finding for edge in edges for finding in edge_violations(edge, layers, ctx)]


def edge_violations(edge: dict[str, Any], layers: list[dict[str, Any]], ctx: Context) -> list[str]:
    src = relative(edge.get("from", ""), ctx)
    dst = relative(edge.get("to", ""), ctx)
    if not ctx.in_scope(src):
        return []
    message = f"{src}:{edge.get('line', 1)} {src} must not depend on {dst} ({edge.get('constant', '')})"
    return [message for layer in layers if breaks(src, dst, layer)]


def breaks(src: str, dst: str, layer: dict[str, Any]) -> bool:
    return in_layer(src, layer["from"]) and forbidden(dst, layer["forbid"])


def in_layer(src: str, layer: str) -> bool:
    return src.startswith(layer.rstrip("/") + "/") or src.startswith(layer)


def forbidden(dst: str, bans: list[str]) -> bool:
    return any(dst.startswith(ban.rstrip("/") + "/") or dst == ban for ban in bans)


def load_layers(ctx: Context) -> list[dict[str, Any]]:
    configured: list[dict[str, Any]] | None = ctx.ruby("layers")
    if configured:
        return configured
    path = ctx.root / ctx.ruby("layers_file", ".ruby-layers.json")
    if not path.exists():
        return DEFAULT_LAYERS
    layers: list[dict[str, Any]] = json.loads(path.read_text())
    return layers


def relative(path: str, ctx: Context) -> str:
    try:
        return str(Path(path).resolve().relative_to(ctx.root.resolve()))
    except ValueError:
        return path
