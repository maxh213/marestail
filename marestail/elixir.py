from collections.abc import Sequence
from pathlib import Path
from typing import Any

from marestail.context import Context
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


def script(mode: str) -> Path:
    return SCRIPTS[mode]


def scan(ctx: Context, mode: str, args: Sequence[Any], cwd: Path | None = None, timeout: int = 3600) -> tuple[int, str]:
    return run(["elixir", str(script(mode)), *map(str, args)], cwd=cwd or ctx.elixir_root(), timeout=timeout)


def deadcode_command(ctx: Context, out: Path) -> list[str]:
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
