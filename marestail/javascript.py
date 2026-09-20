from collections.abc import Sequence
from pathlib import Path

from marestail.context import Context
from marestail.shell import run

SCANNERS = Path(__file__).resolve().parent.parent / "scanners" / "js"
COMMENTS = SCANNERS / "ts_comments.mjs"
COMPLEXITY = SCANNERS / "ts_complexity.mjs"
DEPTH = SCANNERS / "ts_depth.mjs"
SCRIPTS = {"comments": COMMENTS, "complexity": COMPLEXITY, "depth": DEPTH}


def script(mode: str) -> Path:
    return SCRIPTS[mode]


def scan(ctx: Context, mode: str, files: Sequence[Path | str], cwd: Path | None = None) -> tuple[int, str]:
    root = ctx.ts_root()
    return run(["node", str(script(mode)), str(root), *map(str, files)], cwd=cwd or root)


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
