import fnmatch
import hashlib
import json
import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeGuard

from marestail.context import Context, live, under_benchmarks
from marestail.shell import run

PACKAGE = Path(__file__).resolve().parent
MARESTAIL_ROOT = PACKAGE.parent
SCAN_DIR = PACKAGE / "cs" / "scan"
SCAN_PROJECT = PACKAGE / "cs" / "scan" / "Scan.csproj"
PROGRAM_CS = "Program.cs"
PROJECT_FILE = "Scan.csproj"
SCAN_DLL = "marestail-cs-scan.dll"
SCAN_OUT = "cs-scan"
STAGE = "cs-scan-src"
IMAGE = "mcr.microsoft.com/dotnet/sdk:8.0"
DOTNET = "dotnet"
COVERAGE_JSON = "cs-coverage.json"
COVERAGE_EXCLUDE = "coverage_exclude"
MUTATION_EXCLUDE = "mutation_exclude"
SLASH = "/"
EMPTY: list[str] = []
REPLACE = "replace"
GENERATED_DIRS = {"obj", "bin", ".marestail", "node_modules", "Migrations"}
GENERATED_SUFFIXES = (".g.cs", ".Designer.cs", ".AssemblyInfo.cs")
TEST_SUFFIXES = ("Tests.cs", "Test.cs")
INSTALL_HINT = f"install the .NET 8 SDK, or docker with `docker pull {IMAGE}`"

HOST: dict[str, bool] = {}
PROJECTS: dict[str, tuple[Path | None, Path | None]] = {}


def listify(value: Any) -> list[str]:
    if value is None:
        return []
    return [str(part) for part in value] if isinstance(value, list) else [str(value)]


def configured_list(value: Any) -> list[str]:
    if value is None:
        raise TypeError("list")
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
    if DOTNET not in HOST:
        code, _ = run([DOTNET, "--version"], cwd=ctx.root, env=env(ctx), timeout=60)
        HOST[DOTNET] = code == 0
    return HOST[DOTNET]


def dotnet_bin(ctx: Context, cwd: Path, network: bool, extra: dict[str, str], program: str) -> list[str]:
    configured = listify(ctx.dotnet(DOTNET))
    if configured and program == DOTNET:
        return configured
    if host_dotnet(ctx):
        return [program]
    return docker_command(ctx, cwd, network, extra, program)


def docker_command(ctx: Context, cwd: Path, network: bool, extra: dict[str, str], program: str) -> list[str]:
    command = ["docker", "run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}", "-v", f"{ctx.root}:{ctx.root}"]
    if not MARESTAIL_ROOT.is_relative_to(ctx.root):
        command += ["-v", f"{MARESTAIL_ROOT}:{MARESTAIL_ROOT}"]
    if network:
        command += ["--network", "host"]
    for name, value in {**env(ctx), **extra}.items():
        command += ["-e", f"{name}={value}"]
    return [*command, "-w", str(cwd), str(ctx.dotnet("image", IMAGE)), program]


def dotnet(
    ctx: Context,
    args: list[str],
    cwd: Path | None = None,
    timeout: int = 1800,
    network: bool = False,
    extra: dict[str, str] | None = None,
    program: str = DOTNET,
) -> tuple[int, str]:
    ctx = live(ctx)
    folder = cwd or ctx.dotnet_root()
    variables = extra or {}
    return run(dotnet_bin(ctx, folder, network, variables, program) + args, cwd=folder, env={**env(ctx), **variables}, timeout=timeout)


def hint(code: int, output: str) -> str | None:
    if code == 127 or "cannot connect to the docker daemon" in output.lower():
        return f"dotnet unavailable: {INSTALL_HINT}"
    if "unable to find image" in output.lower():
        return f"docker image missing: docker pull {IMAGE}"
    return None


def failure(code: int, output: str, what: str) -> str:
    return hint(code, output) or f"{what}: {output.strip()[-300:]}"


def rel(ctx: Context, path: Path | str) -> str:
    try:
        return Path(path).resolve().relative_to(ctx.root.resolve()).as_posix()
    except ValueError:
        return str(path)


def generated(ctx: Context, path: Path) -> bool:
    parts = path.relative_to(ctx.dotnet_root()).parts
    return bool(set(parts[:-1]) & GENERATED_DIRS) or path.name.endswith(GENERATED_SUFFIXES) or under_benchmarks(ctx.root, path)


def csprojs(ctx: Context) -> list[Path]:
    return sorted(path for path in ctx.dotnet_root().rglob("*.csproj") if not generated(ctx, path))


def test_named(path: Path) -> bool:
    return path.stem.endswith(("Tests", "Test"))


def named_csprojs(ctx: Context, tests: bool) -> list[Path]:
    return [path for path in csprojs(ctx) if test_named(path) == tests]


def project(ctx: Context) -> Path | None:
    configured = ctx.dotnet("project")
    if configured:
        return ctx.dotnet_root() / str(configured)
    candidates = named_csprojs(ctx, False)
    return candidates[0] if len(candidates) == 1 else None


def test_project(ctx: Context) -> Path | None:
    configured = ctx.dotnet("test_project")
    if configured:
        return ctx.dotnet_root() / str(configured)
    candidates = named_csprojs(ctx, True)
    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        return None
    return project(ctx)


def cached_projects(ctx: Context) -> tuple[Path | None, Path | None]:
    key = str(ctx.root)
    if key not in PROJECTS:
        PROJECTS[key] = (project(ctx), test_project(ctx))
    return PROJECTS[key]


def present(path: Path | None) -> TypeGuard[Path]:
    return path is not None and path.exists()


def missing_projects(ctx: Context) -> str:
    found = ", ".join(rel(ctx, path) for path in csprojs(ctx)) or "none"
    return f"set [dotnet] project and test_project in marestail.toml (.csproj files under {rel(ctx, ctx.dotnet_root())}: {found})"


def projects(ctx: Context) -> tuple[Path | None, Path | None, str | None]:
    ctx = live(ctx)
    product, tests = cached_projects(ctx)
    if present(product) and present(tests):
        return product, tests, None
    return None, None, missing_projects(ctx)


def project_pair(ctx: Context) -> tuple[Path, Path] | str:
    product, tests, error = projects(ctx)
    if error is not None:
        return str(error)
    return paths_or_raise(product, tests)


def paths_or_raise(product: Path | None, tests: Path | None) -> tuple[Path, Path]:
    if product is None or tests is None:
        raise TypeError("pair")
    return product, tests


def in_separate_test_project(ctx: Context, path: Path) -> bool:
    found = project_pair(ctx)
    if isinstance(found, str):
        return False
    product, tests = found
    return tests.parent != product.parent and path.is_relative_to(tests.parent)


def test_file(ctx: Context, path: Path) -> bool:
    folders = path.relative_to(ctx.dotnet_root()).parts[:-1]
    return path.name.endswith(TEST_SUFFIXES) or any(part.lower() in ("test", "tests") for part in folders)


def is_test(ctx: Context, path: Path) -> bool:
    return in_separate_test_project(ctx, path) or test_file(ctx, path)


def files(ctx: Context) -> list[Path]:
    ctx = live(ctx)
    return sorted(path for path in ctx.dotnet_root().rglob("*.cs") if not generated(ctx, path))


def sources(ctx: Context) -> list[Path]:
    return [path for path in files(ctx) if not is_test(ctx, path)]


def in_scope(ctx: Context, paths: list[Path]) -> list[Path]:
    ctx = live(ctx)
    if not ctx.scoped:
        return paths
    return [path for path in paths if ctx.in_scope(rel(ctx, path))]


def matching_lines(path: Path, predicate: Callable[[str], object]) -> list[int]:
    return [number for number, line in enumerate(path.read_text(errors=REPLACE).splitlines(), start=1) if predicate(line)]


def root_prefix(ctx: Context) -> str:
    prefix = rel(ctx, ctx.dotnet_root())
    return "" if prefix == "." else prefix + "/"


def matches(relative: str, pattern: str) -> bool:
    return relative == pattern or relative.startswith(pattern + "/") or fnmatch.fnmatch(relative, pattern)


def matches_any(relative: str, patterns: list[str]) -> bool:
    return any(matches(relative, pattern) for pattern in patterns)


def coverage_excluded(ctx: Context, relative: str) -> bool:
    prefix = root_prefix(ctx)
    return matches_any(relative, [prefix + pattern.strip(SLASH) for pattern in configured_list(ctx.dotnet(COVERAGE_EXCLUDE, EMPTY))])


def mutation_patterns(ctx: Context) -> list[str]:
    return configured_list(ctx.dotnet(MUTATION_EXCLUDE, EMPTY)) or configured_list(ctx.dotnet(COVERAGE_EXCLUDE, EMPTY))


def mutation_excluded(ctx: Context, relative: str) -> bool:
    prefix = root_prefix(ctx)
    return matches_any(relative, [p if p.startswith(prefix) else prefix + p.strip(SLASH) for p in mutation_patterns(ctx)])


def load_coverage(ctx: Context) -> dict[str, Any] | None:
    path = ctx.work / COVERAGE_JSON
    return json.loads(path.read_text()) if path.exists() else None


def scanner_source() -> Path:
    return SCAN_DIR / PROGRAM_CS


def bundled_project() -> Path:
    return SCAN_DIR / PROJECT_FILE


def scanner_project() -> Path:
    bundled = bundled_project()
    return bundled if bundled.is_file() else SCAN_PROJECT


def scanner_digest() -> str:
    return hashlib.sha256(scanner_source().read_bytes() + scanner_project().read_bytes()).hexdigest()


def scanner_current(out: Path, digest: str) -> bool:
    stamp = out / "stamp"
    return (out / SCAN_DLL).exists() and stamp.exists() and stamp.read_text() == digest


def colocated(source: Path, project: Path) -> bool:
    return source.parent == project.parent


def staged_project(ctx: Context, source: Path, project: Path) -> Path:
    stage = ctx.work / STAGE
    stage.mkdir(parents=True, exist_ok=True)
    shutil.copy(project, stage / PROJECT_FILE)
    shutil.copy(source, stage / PROGRAM_CS)
    return stage / PROJECT_FILE


def build_project(ctx: Context) -> Path:
    source, project = scanner_source(), scanner_project()
    return project if colocated(source, project) else staged_project(ctx, source, project)


def scanner_build_args(out: Path, project: Path) -> list[str]:
    return [
        "build",
        str(project),
        "-c",
        "Release",
        "-nologo",
        "-v",
        "q",
        f"-p:BaseIntermediateOutputPath={out}/obj/",
        f"-p:BaseOutputPath={out}/bin/",
        "-o",
        str(out),
    ]


def produced(code: int, path: Path) -> bool:
    return code == 0 and path.exists()


def build_scanner(ctx: Context) -> str | None:
    out = ctx.work / SCAN_OUT
    digest = scanner_digest()
    if scanner_current(out, digest):
        return None
    code, output = dotnet(ctx, scanner_build_args(out, build_project(ctx)), cwd=ctx.root, timeout=900)
    if not produced(code, out / SCAN_DLL):
        return failure(code, output, "C# scanner build failed")
    (out / "stamp").write_text(digest)
    return None


def scan(ctx: Context, mode: str, paths: list[Path]) -> tuple[Any, str | None]:
    ctx = live(ctx)
    error = build_scanner(ctx)
    if error:
        return None, error
    out = ctx.work / f"cs-{mode}.json"
    out.unlink(missing_ok=True)
    listing = ctx.work / f"cs-{mode}.txt"
    listing.write_text("".join(f"{path}\n" for path in paths))
    code, output = dotnet(
        ctx,
        [str(ctx.work / SCAN_OUT / SCAN_DLL), mode, "--root", str(ctx.root), "--out", str(out), f"@{listing}"],
        cwd=ctx.root,
        timeout=600,
    )
    if not produced(code, out):
        return None, failure(code, output, f"C# scanner failed ({mode})")
    return json.loads(out.read_text()), None
