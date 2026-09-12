import os
import shutil
from pathlib import Path

from marestail.context import Context
from marestail.shell import run

MARESTAIL_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIR = Path(__file__).resolve().parent / "erl"
IMAGE = "erlang:27"
INSTALL_HINT = f"install Erlang/OTP 25+ (erl, erlc, escript), or docker with `docker pull {IMAGE}`"
GENERATED_DIRS = {"_build", ".marestail", "node_modules", ".git"}

_host: dict[str, bool] = {}


def listify(value) -> list[str]:
    if value is None:
        return []
    return [str(part) for part in value] if isinstance(value, list) else [str(value)]


def host_erlang(ctx: Context) -> bool:
    if "erlang" not in _host:
        code, _ = run(["erl", "-noshell", "-eval", "halt()."], cwd=ctx.root, timeout=60)
        _host["erlang"] = code == 0
    return _host["erlang"]


def erlang_bin(ctx: Context, cwd: Path, program: str) -> list[str]:
    configured = listify(ctx.erlang(program))
    if configured:
        return configured
    if host_erlang(ctx):
        return [program]
    command = ["docker", "run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}", "-v", f"{ctx.root}:{ctx.root}"]
    if not MARESTAIL_ROOT.is_relative_to(ctx.root):
        command += ["-v", f"{MARESTAIL_ROOT}:{MARESTAIL_ROOT}"]
    return [*command, "-w", str(cwd), str(ctx.erlang("image", IMAGE)), program]


def tool(ctx: Context, program: str, args: list[str], cwd: Path | None = None, timeout: int = 600) -> tuple[int, str]:
    cwd = cwd or ctx.erlang_root()
    return run(erlang_bin(ctx, cwd, program) + args, cwd=cwd, timeout=timeout)


def escript(ctx: Context, script: str, args: list[str], cwd: Path | None = None, timeout: int = 600) -> tuple[int, str]:
    return tool(ctx, "escript", [str(SCRIPT_DIR / script), *args], cwd, timeout)


def erlc(ctx: Context, args: list[str], cwd: Path | None = None, timeout: int = 900) -> tuple[int, str]:
    return tool(ctx, "erlc", args, cwd, timeout)


def hint(code: int, output: str) -> str | None:
    first = output.splitlines()[0] if output.strip() else ""
    if code == 127 and ": not found (" in first:
        return f"erlang unavailable: {INSTALL_HINT}"
    if "cannot connect to the docker daemon" in output.lower():
        return f"erlang unavailable: {INSTALL_HINT}"
    if "unable to find image" in output.lower():
        return f"docker image missing: docker pull {IMAGE}"
    return None


def rel(ctx: Context, path) -> str:
    try:
        return Path(path).resolve().relative_to(ctx.root.resolve()).as_posix()
    except ValueError:
        return str(path)


def fresh_dir(path: Path) -> Path:
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    return path


def generated(root: Path, path: Path) -> bool:
    return bool(set(path.relative_to(root).parts) & GENERATED_DIRS)


def source_dirs(ctx: Context) -> list[Path]:
    return [ctx.erlang_root() / folder for folder in listify(ctx.erlang("sources", ["src"]))]


def test_dirs(ctx: Context) -> list[Path]:
    return [ctx.erlang_root() / folder for folder in listify(ctx.erlang("test_dirs", ["test", "tests"]))]


def source_files(ctx: Context) -> list[Path]:
    root = ctx.erlang_root()
    files = []
    for folder in source_dirs(ctx):
        if folder.is_dir():
            files.extend(path for path in folder.rglob("*.erl") if not generated(root, path) and not path.name.endswith("_tests.erl"))
    return sorted(files)


def test_files(ctx: Context) -> list[Path]:
    root = ctx.erlang_root()
    files = set()
    for folder in test_dirs(ctx):
        if folder.is_dir():
            files.update(path for path in folder.rglob("*.erl") if not generated(root, path))
    for folder in source_dirs(ctx):
        if folder.is_dir():
            files.update(path for path in folder.rglob("*_tests.erl") if not generated(root, path))
    return sorted(files)
