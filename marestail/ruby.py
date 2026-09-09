from pathlib import Path

from marestail.context import Context
from marestail.shell import run

SCRIPT = Path(__file__).resolve().parent / "rb" / "scan.rb"
MARESTAIL_ROOT = Path(__file__).resolve().parent.parent
IMAGE = "ruby:3.2-slim"


def listify(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(part) for part in value]
    return [str(value)]


def bundle(ctx: Context, *args: str) -> list[str]:
    prefix = listify(ctx.ruby("exec", ["bundle", "exec"]))
    return [*prefix, *args]


def scan(ctx: Context, mode: str, files: list[Path], extra: list[str] | None = None) -> tuple[int, str]:
    if not files:
        return 0, "[]"
    command = ruby_bin(ctx) + [str(SCRIPT), mode, *(extra or []), *map(str, files)]
    return run(command, cwd=ctx.root, timeout=600)


def ruby_bin(ctx: Context) -> list[str]:
    configured = ctx.ruby("ruby")
    if configured:
        return listify(configured)
    code, _ = run(["ruby", "-e", ""], cwd=ctx.root, timeout=30)
    if code == 0:
        return ["ruby"]
    return [
        "docker", "run", "--rm",
        "-v", f"{ctx.root}:{ctx.root}",
        "-v", f"{MARESTAIL_ROOT}:{MARESTAIL_ROOT}",
        "-w", str(ctx.root),
        ctx.ruby("image", IMAGE),
        "ruby",
    ]
