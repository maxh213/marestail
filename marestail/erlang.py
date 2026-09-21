import os
import shutil
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from marestail.context import CTX_ERROR, Context, live
from marestail.shell import run, tail

MARESTAIL_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIR = Path(__file__).resolve().parent / "erl"
IMAGE = "erlang:27"
INSTALL_HINT = f"install Erlang/OTP 25+ (erl, erlc, escript), or docker with `docker pull {IMAGE}`"
UNAVAILABLE = f"erlang unavailable: {INSTALL_HINT}"
TOOL_TIMEOUT = 600
ERLC_TIMEOUT = 900
PATH_SEP = ":"
EMPTY = ""
GENERATED_DIRS = {"_build", ".marestail", "node_modules", ".git"}
NO_SOURCES = "no erlang sources under [erlang] sources (default src/)"
NO_TESTS = "marestail.toml:1 no test files under [erlang] test_dirs (default test/, tests/) or *_tests.erl next to the sources"

host_checks: dict[str, bool] = {}


def listify(value: Any) -> list[str]:
    if value is None:
        return []
    return [str(part) for part in value] if isinstance(value, list) else [str(value)]


def host_erlang(ctx: Context) -> bool:
    if "erlang" not in host_checks:
        code, _ = run(["erl", "-noshell", "-eval", "halt()."], cwd=ctx.root, timeout=60)
        host_checks["erlang"] = code == 0
    return host_checks["erlang"]


def erlang_bin(ctx: Context, cwd: Path, program: str) -> list[str]:
    configured = listify(ctx.erlang(program))
    if configured:
        return configured
    if host_erlang(ctx):
        return [program]
    return docker_bin(ctx, cwd, program)


def docker_bin(ctx: Context, cwd: Path, program: str) -> list[str]:
    command = ["docker", "run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}", "-v", f"{ctx.root}:{ctx.root}"]
    if not MARESTAIL_ROOT.is_relative_to(ctx.root):
        command += ["-v", f"{MARESTAIL_ROOT}:{MARESTAIL_ROOT}"]
    return [*command, "-w", str(cwd), str(ctx.erlang("image", IMAGE)), program]


def tool(ctx: Context, program: str, args: list[str], cwd: Path | None = None, timeout: int = TOOL_TIMEOUT) -> tuple[int, str]:
    ctx = live(ctx)
    cwd = cwd or ctx.erlang_root()
    return run(erlang_bin(ctx, cwd, program) + args, cwd=cwd, timeout=timeout)


def escript(ctx: Context, script: str, args: list[str], cwd: Path | None = None, timeout: int = TOOL_TIMEOUT) -> tuple[int, str]:
    ctx = live(ctx)
    return tool(ctx, "escript", [str(SCRIPT_DIR / script), *args], cwd, timeout)


def erlc(ctx: Context, args: list[str], cwd: Path | None = None, timeout: int = ERLC_TIMEOUT) -> tuple[int, str]:
    return tool(ctx, "erlc", args, cwd, timeout)


def hint(code: int, output: str) -> str | None:
    require_hint(code, output)
    if unavailable(code, output):
        return UNAVAILABLE
    if "unable to find image" in output.lower():
        return f"docker image missing: docker pull {IMAGE}"
    return None


def require_hint(code: int, output: str) -> None:
    if code is None or output is None:
        raise TypeError(CTX_ERROR)


def unavailable(code: int, output: str) -> bool:
    return program_missing(code, output) or "cannot connect to the docker daemon" in output.lower()


def program_missing(code: int, output: str) -> bool:
    first = output.splitlines()[0] if output.strip() else EMPTY
    return code == 127 and ": not found (" in first


def trouble(code: int, output: str, message: str, lines: list[str] | None = None) -> tuple[str, list[str]] | None:
    problem = hint(code, output)
    if problem:
        return problem, [problem]
    if code != 0:
        return message, tail(output) if lines is None else lines
    return None


def compile_with_tests(ctx: Context, sources: list[Path], tests: list[Path], ebin: Path, test_ebin: Path) -> tuple[str, list[str]] | None:
    code, output = erlc(ctx, ["+debug_info", "-o", str(fresh_dir(ebin)), *map(str, sources)])
    failed = trouble(code, output, "sources failed to compile")
    if failed:
        return failed
    code, output = erlc(ctx, ["-DTEST", "+debug_info", "-pa", str(ebin), "-o", str(fresh_dir(test_ebin)), *map(str, tests)])
    return ("tests failed to compile", tail(output)) if code != 0 else None


def rel(ctx: Context, path: str | Path) -> str:
    ctx = live(ctx)
    try:
        return Path(path).resolve().relative_to(ctx.root.resolve()).as_posix()
    except ValueError:
        return str(path)


def in_scope_findings(ctx: Context, findings: list[str]) -> list[str]:
    return [finding for finding in findings if ctx.in_scope(finding.partition(PATH_SEP)[0])]


def fresh_dir(path: Path) -> Path:
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)
    return path


def generated(root: Path, path: Path) -> bool:
    return bool(set(path.relative_to(root).parts) & GENERATED_DIRS)


def source_dirs(ctx: Context) -> list[Path]:
    return [ctx.erlang_root() / folder for folder in listify(ctx.erlang("sources", ["src"]))]


def test_dirs(ctx: Context) -> list[Path]:
    return [ctx.erlang_root() / folder for folder in listify(ctx.erlang("test_dirs", ["test", "tests"]))]


def found(folders: Iterable[Path], pattern: str) -> list[Path]:
    return [path for folder in folders if folder.is_dir() for path in folder.rglob(pattern)]


def erl_files(root: Path, folders: Iterable[Path], pattern: str) -> list[Path]:
    return [path for path in found(folders, pattern) if not generated(root, path)]


def source_files(ctx: Context) -> list[Path]:
    ctx = live(ctx)
    return sorted(path for path in erl_files(ctx.erlang_root(), source_dirs(ctx), "*.erl") if not path.name.endswith("_tests.erl"))


def test_files(ctx: Context) -> list[Path]:
    root = ctx.erlang_root()
    tests = set(erl_files(root, test_dirs(ctx), "*.erl"))
    return sorted(tests | set(erl_files(root, source_dirs(ctx), "*_tests.erl")))
