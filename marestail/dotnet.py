import fnmatch
import hashlib
import json
import os
from pathlib import Path

from marestail.context import Context
from marestail.shell import run

MARESTAIL_ROOT = Path(__file__).resolve().parent.parent
SCAN_DIR = Path(__file__).resolve().parent / "cs" / "scan"
SCAN_DLL = "marestail-cs-scan.dll"
IMAGE = "mcr.microsoft.com/dotnet/sdk:8.0"
COVERAGE_JSON = "cs-coverage.json"
GENERATED_DIRS = {"obj", "bin", ".marestail", "node_modules", "Migrations"}
GENERATED_SUFFIXES = (".g.cs", ".Designer.cs", ".AssemblyInfo.cs")
TEST_SUFFIXES = ("Tests.cs", "Test.cs")
INSTALL_HINT = f"install the .NET 8 SDK, or docker with `docker pull {IMAGE}`"

_host: dict[str, bool] = {}
_projects: dict[str, tuple[Path | None, Path | None]] = {}


def listify(value) -> list[str]:
    if value is None:
        return []
    return [str(part) for part in value] if isinstance(value, list) else [str(value)]


def env(ctx: Context) -> dict[str, str]:
    home = ctx.work / "dotnethome"
    packages = ctx.work / "nuget"
    home.mkdir(exist_ok=True)
    packages.mkdir(exist_ok=True)
    return {
        "HOME": str(home),
        "NUGET_PACKAGES": str(packages),
        "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
        "DOTNET_NOLOGO": "1",
        "DOTNET_SKIP_FIRST_TIME_EXPERIENCE": "1",
    }


def host_dotnet(ctx: Context) -> bool:
    if "dotnet" not in _host:
        code, _ = run(["dotnet", "--version"], cwd=ctx.root, env=env(ctx), timeout=60)
        _host["dotnet"] = code == 0
    return _host["dotnet"]


def dotnet_bin(ctx: Context, cwd: Path, network: bool, extra: dict[str, str], program: str) -> list[str]:
    configured = listify(ctx.dotnet("dotnet"))
    if configured and program == "dotnet":
        return configured
    if host_dotnet(ctx):
        return [program]
    command = ["docker", "run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}", "-v", f"{ctx.root}:{ctx.root}"]
    if not MARESTAIL_ROOT.is_relative_to(ctx.root):
        command += ["-v", f"{MARESTAIL_ROOT}:{MARESTAIL_ROOT}"]
    if network:
        command += ["--network", "host"]
    for name, value in {**env(ctx), **extra}.items():
        command += ["-e", f"{name}={value}"]
    return [*command, "-w", str(cwd), str(ctx.dotnet("image", IMAGE)), program]


def dotnet(ctx: Context, args: list[str], cwd: Path | None = None, timeout: int = 1800, network: bool = False, extra: dict[str, str] | None = None, program: str = "dotnet") -> tuple[int, str]:
    cwd = cwd or ctx.dotnet_root()
    extra = extra or {}
    return run(dotnet_bin(ctx, cwd, network, extra, program) + args, cwd=cwd, env={**env(ctx), **extra}, timeout=timeout)


def hint(code: int, output: str) -> str | None:
    if code == 127 or "cannot connect to the docker daemon" in output.lower():
        return f"dotnet unavailable: {INSTALL_HINT}"
    if "unable to find image" in output.lower():
        return f"docker image missing: docker pull {IMAGE}"
    return None


def rel(ctx: Context, path) -> str:
    try:
        return Path(path).resolve().relative_to(ctx.root.resolve()).as_posix()
    except ValueError:
        return str(path)


def generated(ctx: Context, path: Path) -> bool:
    parts = path.relative_to(ctx.dotnet_root()).parts
    return bool(set(parts[:-1]) & GENERATED_DIRS) or path.name.endswith(GENERATED_SUFFIXES)


def csprojs(ctx: Context) -> list[Path]:
    return sorted(path for path in ctx.dotnet_root().rglob("*.csproj") if not generated(ctx, path))


def test_named(path: Path) -> bool:
    return path.stem.endswith(("Tests", "Test"))


def project(ctx: Context) -> Path | None:
    configured = ctx.dotnet("project")
    if configured:
        return ctx.dotnet_root() / str(configured)
    candidates = [path for path in csprojs(ctx) if not test_named(path)]
    return candidates[0] if len(candidates) == 1 else None


def test_project(ctx: Context) -> Path | None:
    configured = ctx.dotnet("test_project")
    if configured:
        return ctx.dotnet_root() / str(configured)
    candidates = [path for path in csprojs(ctx) if test_named(path)]
    if len(candidates) == 1:
        return candidates[0]
    return project(ctx) if not candidates else None


def projects(ctx: Context) -> tuple[Path | None, Path | None, str | None]:
    key = str(ctx.root)
    if key not in _projects:
        _projects[key] = (project(ctx), test_project(ctx))
    product, tests = _projects[key]
    if product is None or tests is None or not product.exists() or not tests.exists():
        found = ", ".join(rel(ctx, path) for path in csprojs(ctx)) or "none"
        return None, None, f"set [dotnet] project and test_project in marestail.toml (.csproj files under {rel(ctx, ctx.dotnet_root())}: {found})"
    return product, tests, None


def is_test(ctx: Context, path: Path) -> bool:
    product, tests, _ = projects(ctx)
    if tests is not None and product is not None and tests.parent != product.parent and path.is_relative_to(tests.parent):
        return True
    return path.name.endswith(TEST_SUFFIXES) or any(part.lower() in ("test", "tests") for part in path.relative_to(ctx.dotnet_root()).parts[:-1])


def files(ctx: Context) -> list[Path]:
    return sorted(path for path in ctx.dotnet_root().rglob("*.cs") if not generated(ctx, path))


def sources(ctx: Context) -> list[Path]:
    return [path for path in files(ctx) if not is_test(ctx, path)]


def in_scope(ctx: Context, paths: list[Path]) -> list[Path]:
    if not ctx.scope_changed:
        return paths
    return [path for path in paths if rel(ctx, path) in ctx.changed]


def coverage_excluded(ctx: Context, relative: str) -> bool:
    prefix = rel(ctx, ctx.dotnet_root())
    prefix = "" if prefix == "." else prefix + "/"
    patterns = [prefix + pattern.strip("/") for pattern in listify(ctx.dotnet("coverage_exclude", []))]
    return any(relative == p or relative.startswith(p + "/") or fnmatch.fnmatch(relative, p) for p in patterns)


def load_coverage(ctx: Context) -> dict | None:
    path = ctx.work / COVERAGE_JSON
    return json.loads(path.read_text()) if path.exists() else None


def build_scanner(ctx: Context) -> str | None:
    out = ctx.work / "cs-scan"
    dll, stamp = out / SCAN_DLL, out / "stamp"
    digest = hashlib.sha256((SCAN_DIR / "Program.cs").read_bytes() + (SCAN_DIR / "Scan.csproj").read_bytes()).hexdigest()
    if dll.exists() and stamp.exists() and stamp.read_text() == digest:
        return None
    code, output = dotnet(ctx, [
        "build", str(SCAN_DIR / "Scan.csproj"), "-c", "Release", "-nologo", "-v", "q",
        f"-p:BaseIntermediateOutputPath={out}/obj/", f"-p:BaseOutputPath={out}/bin/", "-o", str(out),
    ], cwd=ctx.root, timeout=900)
    if code != 0 or not dll.exists():
        return hint(code, output) or f"C# scanner build failed: {output.strip()[-300:]}"
    stamp.write_text(digest)
    return None


def scan(ctx: Context, mode: str, paths: list[Path]) -> tuple[list | dict | None, str | None]:
    error = build_scanner(ctx)
    if error:
        return None, error
    out = ctx.work / f"cs-{mode}.json"
    out.unlink(missing_ok=True)
    listing = ctx.work / f"cs-{mode}.txt"
    listing.write_text("".join(f"{path}\n" for path in paths))
    code, output = dotnet(ctx, [str(ctx.work / "cs-scan" / SCAN_DLL), mode, "--root", str(ctx.root), "--out", str(out), f"@{listing}"], cwd=ctx.root, timeout=600)
    if code != 0 or not out.exists():
        return None, hint(code, output) or f"C# scanner failed ({mode}): {output.strip()[-300:]}"
    return json.loads(out.read_text()), None
