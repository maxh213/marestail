from collections.abc import Sequence
from pathlib import Path

from marestail.context import Context
from marestail.shell import run

SCANNERS = Path(__file__).resolve().parent / "js"
COMMENTS = SCANNERS / "ts_comments.mjs"
COMPLEXITY = SCANNERS / "ts_complexity.mjs"
DEPTH = SCANNERS / "ts_depth.mjs"
SCRIPTS = {"comments": COMMENTS, "complexity": COMPLEXITY, "depth": DEPTH}
NPX = ("npx",)


def script(mode: str) -> Path:
    return SCRIPTS[mode]


def scan(ctx: Context, mode: str, files: Sequence[Path | str], cwd: Path | None = None) -> tuple[int, str]:
    working = ctx.ts_root() if cwd is None else cwd
    typescript = tooling(ctx) or ctx.ts_root()
    return run(["node", str(script(mode)), str(typescript), *map(str, files)], cwd=working)


def located(path: str, ctx: Context) -> str | None:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = ctx.ts_root() / path
    try:
        return candidate.resolve().relative_to(ctx.root.resolve()).as_posix()
    except ValueError:
        return None


def rel(path: str, ctx: Context) -> str:
    return located(path, ctx) or path


def labelled(path: str, ctx: Context) -> str:
    return located(path.strip(), ctx) or path


def tooling(ctx: Context) -> Path | None:
    folder = ctx.ts("tooling")
    return None if folder is None else ctx.root / folder


def tool(ctx: Context, name: str, npx: tuple[str, ...] = NPX) -> list[str]:
    folder = tooling(ctx)
    if folder is None:
        return [*npx, name]
    return [str(folder / "node_modules" / ".bin" / name)]


def tooling_file(ctx: Context, name: str) -> Path | None:
    folder = tooling(ctx)
    if folder is None:
        return None
    path = folder / name
    return path if path.exists() else None


def config_flag(ctx: Context, flag: str, name: str) -> list[str]:
    path = tooling_file(ctx, name)
    return [] if path is None else [flag, str(path)]


def config_or(ctx: Context, name: str, fallback: str) -> str:
    path = tooling_file(ctx, name)
    return fallback if path is None else str(path)
