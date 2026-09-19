import ast
import io
import json
import re
import time
import tokenize
from collections.abc import Callable
from functools import partial
from pathlib import Path
from types import ModuleType
from typing import Any

from marestail.context import Context, under_benchmarks
from marestail.report import Result
from marestail.shell import run

SCRIPT = Path(__file__).resolve().parent.parent / "js" / "ts_comments.mjs"
EX_SCRIPT = Path(__file__).resolve().parent.parent / "ex" / "comments.exs"
SKIP_DIRS = {
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
    "_build",
    "deps",
    "mutants",
    ".marestail",
    ".git",
    ".scannerwork",
    "coverage",
    "cover",
    "reports",
    "__pycache__",
    "vendor",
    "tmp",
    "log",
    "target",
}
TS_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
MARKUP = re.compile(r"<!--|\{#")
UNTOKENIZABLE = (0, "could not tokenize")


def run_gate(ctx: Context) -> Result:
    started = time.time()
    findings = [finding for scanner in SCANNERS for finding in scanner(ctx)]
    summary = "no comments" if not findings else f"{len(findings)} comments or docstrings"
    return Result("comments", not findings, summary, findings, time.time() - started)


def files(ctx: Context, suffixes: tuple[str, ...]) -> list[Path]:
    return [path for path in wanted(ctx, suffixes) if ctx.in_scope(str(path.relative_to(ctx.root)))]


def wanted(ctx: Context, suffixes: tuple[str, ...]) -> list[Path]:
    return sorted({path for path in candidates(ctx) if path.suffix in suffixes and not skipped(path, ctx)})


def candidates(ctx: Context) -> list[Path]:
    return [path for folder in ctx.config.get("comments", "paths", ["."]) for path in (ctx.root / folder).rglob("*")]


def skipped(path: Path, ctx: Context) -> bool:
    return any(part in SKIP_DIRS for part in path.relative_to(ctx.root).parts) or under_benchmarks(ctx.root, path)


def comment(label: object, line: object, text: object) -> str:
    return f"{label}:{line} comment: {text}"


def failed(label: str, output: str) -> str:
    return f"{label}: {output.strip()[-200:]}"


def python_findings(ctx: Context) -> list[str]:
    return [finding for path in files(ctx, (".py",)) for finding in python_file_findings(path, ctx)]


def python_file_findings(path: Path, ctx: Context) -> list[str]:
    text = path.read_text()
    label = str(path.relative_to(ctx.root))
    found = [comment(label, line, snippet) for line, snippet in python_comments(text)]
    return found + [f"{label}:{line} docstring" for line in docstrings(text)]


def python_comments(text: str) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    try:
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if is_comment(token):
                found.append((token.start[0], token.string[:80]))
    except (tokenize.TokenError, SyntaxError):
        found.append(UNTOKENIZABLE)
    return found


def is_comment(token: tokenize.TokenInfo) -> bool:
    return token.type == tokenize.COMMENT and not (token.start[0] == 1 and token.string.startswith("#!"))


def docstrings(text: str) -> list[int]:
    tree = parsed(text)
    if tree is None:
        return []
    return [node.body[0].lineno for node in documentable(tree) if ast.get_docstring(node, clean=False) is not None]


def parsed(text: str) -> ast.Module | None:
    try:
        return ast.parse(text)
    except SyntaxError:
        return None


def documentable(tree: ast.Module) -> list[ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef]:
    return [tree, *[node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]]


def scanned(ctx: Context, code: int, output: str, failure: str, fallback: str = "") -> list[str]:
    if code != 0:
        return [failed(failure, output)]
    return [comment(Path(c["file"]).relative_to(ctx.root), c["line"], c["text"]) for c in json.loads(output or fallback)]


def ts_findings(ctx: Context) -> list[str]:
    ts_root = ctx.config.get("ts", "root")
    paths = files(ctx, TS_SUFFIXES)
    if ts_root is None or not paths:
        return []
    code, output = run(["node", str(SCRIPT), str(ctx.root / ts_root), *map(str, paths)], cwd=ctx.root)
    return scanned(ctx, code, output, "comment scanner failed")


def elixir_findings(ctx: Context) -> list[str]:
    paths = files(ctx, (".ex", ".exs"))
    if not paths:
        return []
    code, output = run(["elixir", str(EX_SCRIPT), *map(str, paths)], cwd=ctx.root)
    return scanned(ctx, code, output, "elixir comment scanner failed")


def erlang_findings(ctx: Context) -> list[str]:
    paths = files(ctx, (".erl", ".hrl"))
    if not paths:
        return []
    from marestail import erlang

    code, output = erlang.escript(ctx, "comments.escript", list(map(str, paths)))
    problem = erlang.hint(code, output) if code != 0 else None
    return [problem] if problem else scanned(ctx, code, output, "erlang comment scanner failed")


def ruby_findings(ctx: Context) -> list[str]:
    if ctx.config.section("ruby") is None:
        return []
    paths = files(ctx, (".rb", ".rake"))
    if not paths:
        return []
    from marestail.ruby import scan

    code, output = scan(ctx, "comments", paths)
    return scanned(ctx, code, output, "ruby comment scanner failed", "[]")


def structured(ctx: Context, module: ModuleType, paths: list[Path], failure: str, relabel: Callable[[Any], str] = str) -> list[str]:
    if not paths:
        return []
    data, error = module.scan(ctx, "comments", paths)
    if error:
        return [f"{failure}: {error}"]
    return [comment(relabel(c["file"]), c["line"], c["text"]) for c in data]


def dotnet_findings(ctx: Context) -> list[str]:
    if ctx.config.section("dotnet") is None:
        return []
    from marestail import dotnet

    return structured(ctx, dotnet, dotnet.in_scope(ctx, dotnet.files(ctx)), "C# comment scanner failed")


def rust_findings(ctx: Context) -> list[str]:
    if ctx.config.section("rust") is None:
        return []
    from marestail import rust

    return structured(ctx, rust, files(ctx, (".rs",)), "rust comment scanner failed", partial(rust.rel, ctx))


def java_findings(ctx: Context) -> list[str]:
    if ctx.config.section("java") is None:
        return []
    from marestail import java

    return structured(ctx, java, files(ctx, (".java",)), "java comment scanner failed")


def markup_findings(ctx: Context) -> list[str]:
    return [finding for path in files(ctx, (".html", ".jinja", ".j2", ".css")) for finding in markup_file_findings(path, ctx)]


def markup_file_findings(path: Path, ctx: Context) -> list[str]:
    lines = enumerate(path.read_text().splitlines(), start=1)
    return [comment(path.relative_to(ctx.root), number, line.strip()[:80]) for number, line in lines if markup_comment(path, line)]


def markup_comment(path: Path, line: str) -> bool:
    return bool(MARKUP.search(line)) or (path.suffix == ".css" and "/*" in line)


SCANNERS = [
    python_findings,
    ts_findings,
    elixir_findings,
    erlang_findings,
    ruby_findings,
    dotnet_findings,
    rust_findings,
    java_findings,
    markup_findings,
]
