import ast
import io
import json
import re
import time
import tokenize
from pathlib import Path

from marestail.context import Context
from marestail.report import Result
from marestail.shell import run

SCRIPT = Path(__file__).resolve().parent.parent / "js" / "ts_comments.mjs"
EX_SCRIPT = Path(__file__).resolve().parent.parent / "ex" / "comments.exs"
SKIP_DIRS = {"node_modules", ".venv", "venv", "dist", "build", "_build", "deps", "mutants", ".marestail", ".git", ".scannerwork", "coverage", "cover", "reports", "__pycache__", "vendor", "tmp", "log"}
TS_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
MARKUP = re.compile(r"<!--|\{#")


def run_gate(ctx: Context) -> Result:
    started = time.time()
    findings = python_findings(ctx) + ts_findings(ctx) + elixir_findings(ctx) + ruby_findings(ctx) + dotnet_findings(ctx) + markup_findings(ctx)
    summary = "no comments" if not findings else f"{len(findings)} comments or docstrings"
    return Result("comments", not findings, summary, findings, time.time() - started)


def files(ctx: Context, suffixes: tuple[str, ...]) -> list[Path]:
    roots = [ctx.root / folder for folder in ctx.config.get("comments", "paths", ["."])]
    found = sorted({path for root in roots for path in root.rglob("*") if path.suffix in suffixes and not skipped(path, ctx)})
    if ctx.scope_changed:
        found = [path for path in found if str(path.relative_to(ctx.root)) in ctx.changed]
    return found


def skipped(path: Path, ctx: Context) -> bool:
    return any(part in SKIP_DIRS for part in path.relative_to(ctx.root).parts)


def python_findings(ctx: Context) -> list[str]:
    findings = []
    for path in files(ctx, (".py",)):
        text = path.read_text()
        label = str(path.relative_to(ctx.root))
        findings.extend(f"{label}:{line} comment: {snippet}" for line, snippet in python_comments(text))
        findings.extend(f"{label}:{line} docstring" for line in docstrings(text))
    return findings


def python_comments(text: str) -> list[tuple[int, str]]:
    found = []
    try:
        tokens = tokenize.generate_tokens(io.StringIO(text).readline)
        for token in tokens:
            if token.type == tokenize.COMMENT and not (token.start[0] == 1 and token.string.startswith("#!")):
                found.append((token.start[0], token.string[:80]))
    except (tokenize.TokenError, SyntaxError):
        found.append((0, "could not tokenize"))
    return found


def docstrings(text: str) -> list[int]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    nodes = [tree, *[n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]]
    return [node.body[0].lineno for node in nodes if ast.get_docstring(node, clean=False) is not None]


def ts_findings(ctx: Context) -> list[str]:
    ts_root = ctx.config.get("ts", "root")
    paths = files(ctx, TS_SUFFIXES)
    if ts_root is None or not paths:
        return []
    code, output = run(["node", str(SCRIPT), str(ctx.root / ts_root), *map(str, paths)], cwd=ctx.root)
    if code != 0:
        return [f"comment scanner failed: {output.strip()[-200:]}"]
    return [f"{Path(c['file']).relative_to(ctx.root)}:{c['line']} comment: {c['text']}" for c in json.loads(output)]


def elixir_findings(ctx: Context) -> list[str]:
    paths = files(ctx, (".ex", ".exs"))
    if not paths:
        return []
    code, output = run(["elixir", str(EX_SCRIPT), *map(str, paths)], cwd=ctx.root)
    if code != 0:
        return [f"elixir comment scanner failed: {output.strip()[-200:]}"]
    return [f"{Path(c['file']).relative_to(ctx.root)}:{c['line']} comment: {c['text']}" for c in json.loads(output)]


def ruby_findings(ctx: Context) -> list[str]:
    if ctx.config.section("ruby") is None:
        return []
    paths = files(ctx, (".rb", ".rake"))
    if not paths:
        return []
    from marestail.ruby import scan

    code, output = scan(ctx, "comments", paths)
    if code != 0:
        return [f"ruby comment scanner failed: {output.strip()[-200:]}"]
    return [f"{Path(c['file']).relative_to(ctx.root)}:{c['line']} comment: {c['text']}" for c in json.loads(output or "[]")]


def dotnet_findings(ctx: Context) -> list[str]:
    if ctx.config.section("dotnet") is None:
        return []
    from marestail import dotnet

    paths = dotnet.in_scope(ctx, dotnet.files(ctx))
    if not paths:
        return []
    data, error = dotnet.scan(ctx, "comments", paths)
    if error:
        return [f"C# comment scanner failed: {error}"]
    return [f"{c['file']}:{c['line']} comment: {c['text']}" for c in data]



def markup_findings(ctx: Context) -> list[str]:
    findings = []
    for path in files(ctx, (".html", ".jinja", ".j2", ".css")):
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            if MARKUP.search(line) or (path.suffix == ".css" and "/*" in line):
                findings.append(f"{path.relative_to(ctx.root)}:{number} comment: {line.strip()[:80]}")
    return findings


