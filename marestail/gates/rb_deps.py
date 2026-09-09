import json
import time
from pathlib import Path

from marestail.context import Context
from marestail.gates.rb_crap import ruby_sources
from marestail.report import Result
from marestail.ruby import scan

DEFAULT_LAYERS = [
    {"from": "app/models", "forbid": ["app/controllers", "app/helpers", "app/jobs", "app/mailers", "app/channels", "app/graphql", "app/views"]},
    {"from": "app/services", "forbid": ["app/controllers", "app/helpers", "app/views"]},
    {"from": "lib", "forbid": ["app/controllers"]},
]


def run_gate(ctx: Context) -> Result:
    started = time.time()
    files = ruby_sources(ctx)
    if not files:
        return Result.skipped("rb.deps", "no ruby sources")
    code, output = scan(ctx, "deps", files, extra=[str(ctx.root)])
    if code != 0:
        return Result("rb.deps", False, "dependency scanner failed", output.splitlines()[-10:], time.time() - started)
    edges = json.loads(output or "[]")
    layers = load_layers(ctx)
    findings = []
    for edge in edges:
        src = relative(edge.get("from", ""), ctx)
        dst = relative(edge.get("to", ""), ctx)
        if ctx.scope_changed and src not in ctx.changed:
            continue
        for layer in layers:
            if src.startswith(layer["from"].rstrip("/") + "/") or src.startswith(layer["from"]):
                if any(dst.startswith(ban.rstrip("/") + "/") or dst == ban for ban in layer["forbid"]):
                    findings.append(f"{src}:{edge.get('line', 1)} {src} must not depend on {dst} ({edge.get('constant', '')})")
    summary = "layer contracts kept" if not findings else f"{len(findings)} layer breaks"
    return Result("rb.deps", not findings, summary, findings[:60], time.time() - started)


def load_layers(ctx: Context) -> list[dict]:
    configured = ctx.ruby("layers")
    if configured:
        return configured
    path = ctx.root / ctx.ruby("layers_file", ".ruby-layers.json")
    if not path.exists():
        return DEFAULT_LAYERS
    return json.loads(path.read_text())


def relative(path: str, ctx: Context) -> str:
    try:
        return str(Path(path).resolve().relative_to(ctx.root.resolve()))
    except ValueError:
        return path
