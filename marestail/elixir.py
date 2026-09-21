from collections.abc import Sequence
from pathlib import Path
from typing import Any

from marestail.context import CTX_ERROR, Context
from marestail.shell import run

SCANNERS = Path(__file__).resolve().parent / "ex"
COMMENTS = SCANNERS / "comments.exs"
COMPLEXITY = SCANNERS / "complexity.exs"
DEADCODE = SCANNERS / "deadcode.exs"
DEPTH = SCANNERS / "depth.exs"
COVERAGE = SCANNERS / "coverage.exs"
SCRIPTS = {"comments": COMMENTS, "complexity": COMPLEXITY, "deadcode": DEADCODE, "depth": DEPTH, "coverage": COVERAGE}
EMPTY: list[str] = []
IGNORE_MODULES = "--ignore-modules"
IGNORE = "--ignore"
PATH_ERROR = "path"


def script(mode: str) -> Path:
    return SCRIPTS[mode]


def scan(ctx: Context, mode: str, args: Sequence[Any], cwd: Path | None = None, timeout: int = 3600) -> tuple[int, str]:
    if type(ctx) is not Context:
        raise TypeError(CTX_ERROR)
    working = ctx.elixir_root() if cwd is None else cwd
    return run(["elixir", str(script(mode)), *map(str, args)], cwd=working, timeout=timeout)


def deadcode_command(ctx: Context, out: Path) -> list[str]:
    if not isinstance(out, Path):
        raise TypeError(PATH_ERROR)
    command = ["mix", "run", "--no-start", str(DEADCODE), "--out", str(out)]
    return command + deadcode_flags(ctx)


def deadcode_flags(ctx: Context) -> list[str]:
    flags = option("--preset", ctx.elixir("preset"))
    flags += joined(IGNORE_MODULES, ctx.elixir("deadcode_ignore_modules", EMPTY))
    return flags + joined(IGNORE, ctx.elixir("deadcode_ignore", EMPTY))


def option(flag: str, value: Any) -> list[str]:
    return [flag, str(value)] if value else []


def joined(flag: str, values: Any) -> list[str]:
    parts = list(values)
    return [flag, ",".join(parts)] if parts else []


def project_files(ctx: Context, root: Path, files: list[str]) -> list[str]:
    prefix = root.relative_to(ctx.root)
    relatives = [strip_prefix(path, prefix) for path in files]
    return sorted(str(path) for path in relatives if (root / path).is_file())


def strip_prefix(path: str, prefix: Path) -> Path:
    relative = Path(path)
    try:
        return relative.relative_to(prefix)
    except ValueError:
        return relative
