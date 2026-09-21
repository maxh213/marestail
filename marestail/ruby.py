import json
from pathlib import Path
from typing import Any

from marestail.context import CTX_ERROR, Context
from marestail.shell import run

SCRIPT = Path(__file__).resolve().parent / "rb" / "scan.rb"
MARESTAIL_ROOT = Path(__file__).resolve().parent.parent
IMAGE = "ruby:3.2-slim"
SKIP_DIRS = {"vendor", "spec", "test", "tmp", "log", "node_modules", ".git", "coverage"}
SOURCES_KEY = "sources"
DEFAULT_FOLDERS = ["app", "lib"]


def listify(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(part) for part in value]
    return [str(value)]


def bundle(ctx: Context, *args: str) -> list[str]:
    prefix = listify(ctx.ruby("exec", ["bundle", "exec"]))
    return [*prefix, *args]


def scan(ctx: Context, mode: str, files: list[Path], extra: list[str] | None = None) -> tuple[int, str]:
    if type(ctx) is not Context:
        raise TypeError(CTX_ERROR)
    if not files:
        return 0, "[]"
    command = [*ruby_bin(ctx), str(SCRIPT), mode, *(extra or []), *map(str, files)]
    return run(command, cwd=ctx.root, timeout=600)


def scanned(output: str) -> list[dict[str, Any]]:
    decoded: list[dict[str, Any]] = json.loads(output or "[]")
    return decoded


def ruby_bin(ctx: Context) -> list[str]:
    configured = ctx.ruby("ruby")
    if configured:
        return listify(configured)
    code, _ = run(["ruby", "-e", ""], cwd=ctx.root, timeout=30)
    if code == 0:
        return ["ruby"]
    return [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{ctx.root}:{ctx.root}",
        "-v",
        f"{MARESTAIL_ROOT}:{MARESTAIL_ROOT}",
        "-w",
        str(ctx.root),
        ctx.ruby("image", IMAGE),
        "ruby",
    ]


def relative(path: str, ctx: Context) -> str:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = ctx.ruby_root() / path
    try:
        return str(candidate.resolve().relative_to(ctx.root.resolve()))
    except ValueError:
        return path


def kept_source(ctx: Context, path: Path) -> bool:
    return not any(part in SKIP_DIRS for part in path.relative_to(ctx.root).parts)


def sources(ctx: Context) -> list[Path]:
    root = ctx.ruby_root()
    folders = ctx.ruby(SOURCES_KEY, DEFAULT_FOLDERS)
    return sorted(path for folder in folders for path in (root / folder).rglob("*.rb") if kept_source(ctx, path))
