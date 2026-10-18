import fnmatch
import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

from marestail.context import Context, under_benchmarks
from marestail.shell import run

PACKAGE = Path(__file__).resolve().parent
MINIMUM = (1, 25)
PINS = {
    "golangci-lint": ("github.com/golangci/golangci-lint/v2", "v2.13.2"),
    "gremlins": ("github.com/go-gremlins/gremlins", "v0.6.0"),
}
INSTALL = {
    "go": "install Go 1.25 or newer from https://go.dev/dl or your package manager, or set [go] go",
    "golangci-lint": "go install github.com/golangci/golangci-lint/v2/cmd/golangci-lint@v2.13.2",
    "gremlins": "go install github.com/go-gremlins/gremlins/cmd/gremlins@v0.6.0",
}
SCAN_DIR = PACKAGE / "go" / "scan"
SCAN_INPUTS = ("main.go", "go.mod")
SCAN_ENV = {"GOTOOLCHAIN": "local", "GOWORK": "off", "GOFLAGS": "", "CGO_ENABLED": "0"}
SKIP_NAMES = {"vendor", "testdata", ".marestail", ".git", "node_modules"}
GENERATED = re.compile(r"^// Code generated .* DO NOT EDIT\.$")
VERSION = re.compile(r"^go(\d+)\.(\d+)")
ERROR_TAIL = 300
MISSING_TOOL = 127
TOOLCHAIN = "go-toolchain"
MODULE_KEY = "go-module"
NO_MODULE = "no module version"
SLASH = "/"


def listify(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(part) for part in value]
    return [str(value)]


def go_bin(ctx: Context) -> list[str]:
    return listify(ctx.go("go", "go"))


def go_missing() -> str:
    return f"go is not installed: {INSTALL['go']}"


def output_tail(output: str) -> str:
    return output.strip()[-ERROR_TAIL:]


def json_object(output: str) -> dict[str, Any] | None:
    parsed = decoded(output)
    if isinstance(parsed, dict):
        return parsed
    return None


def decoded(output: str) -> Any:
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        return None


def toolchain(ctx: Context) -> dict[str, Any] | str:
    cached = ctx.memo.get(TOOLCHAIN)
    if isinstance(cached, dict):
        return cached
    if isinstance(cached, str):
        return cached
    loaded = read_toolchain(ctx)
    ctx.memo[TOOLCHAIN] = loaded
    return loaded


def read_toolchain(ctx: Context) -> dict[str, Any] | str:
    code, output = run([*go_bin(ctx), "env", "-json", "GOVERSION", "GOROOT", "GOBIN", "GOPATH"], cwd=ctx.go_root(), timeout=60)
    return toolchain_result(code, output)


def toolchain_result(code: int, output: str) -> dict[str, Any] | str:
    if code == MISSING_TOOL:
        return go_missing()
    return env_payload(code, output)


def env_payload(code: int, output: str) -> dict[str, Any] | str:
    parsed = json_object(output)
    if code != 0 or parsed is None:
        return f"go env failed: {output_tail(output)}"
    return checked_version(parsed)


def checked_version(parsed: dict[str, Any]) -> dict[str, Any] | str:
    problem = version_problem(parsed)
    if problem is None:
        return parsed
    return problem


def version_problem(parsed: dict[str, Any]) -> str | None:
    found = str(parsed.get("GOVERSION", ""))
    if version_ok(found):
        return None
    return f"go 1.25 or newer is required, found {found}: {INSTALL['go']}"


def version_ok(found: str) -> bool:
    match = VERSION.match(found)
    if match is None:
        return False
    return (int(match.group(1)), int(match.group(2))) >= MINIMUM


def module(ctx: Context) -> str:
    cached = ctx.memo.get(MODULE_KEY)
    if isinstance(cached, str):
        return cached
    loaded = read_module(ctx)
    ctx.memo[MODULE_KEY] = loaded
    return loaded


def read_module(ctx: Context) -> str:
    if not (ctx.go_root() / "go.mod").is_file():
        return missing_mod(ctx)
    code, output = run([*go_bin(ctx), "mod", "edit", "-json"], cwd=ctx.go_root(), timeout=60)
    return module_result(code, output)


def missing_mod(ctx: Context) -> str:
    return f"{root_prefix(ctx)}go.mod:1 missing; [go] root must name the folder that holds go.mod"


def module_result(code: int, output: str) -> str:
    if code == MISSING_TOOL:
        return go_missing()
    return module_path(code, output)


def module_path(code: int, output: str) -> str:
    found = path_of(json_object(output))
    if code != 0 or found is None:
        return f"go mod edit -json failed: {output_tail(output)}"
    return found


def path_of(parsed: dict[str, Any] | None) -> str | None:
    if not isinstance(parsed, dict):
        return None
    owner = parsed.get("Module")
    if not isinstance(owner, dict):
        return None
    found = owner.get("Path")
    if isinstance(found, str):
        return found
    return None


def preflight(ctx: Context) -> str | None:
    found = module(ctx)
    if module_problem(found):
        return found
    return toolchain_problem(toolchain(ctx))


def module_problem(found: str) -> bool:
    return " " in found


def toolchain_problem(loaded: dict[str, Any] | str) -> str | None:
    if isinstance(loaded, str):
        return loaded
    return None


def tool(ctx: Context, name: str) -> str:
    path = tool_path(ctx, name)
    if path is None:
        return f"{name} is not installed: {INSTALL[name]}"
    return pinned(ctx, name, path)


def tool_path(ctx: Context, name: str) -> str | None:
    configured = ctx.go(name)
    if configured:
        return str(configured)
    return shutil.which(name) or installed_tool(ctx, name)


def installed_tool(ctx: Context, name: str) -> str | None:
    for folder in bin_dirs(ctx):
        candidate = folder / name
        if candidate.is_file():
            return str(candidate)
    return None


def bin_dirs(ctx: Context) -> list[Path]:
    loaded = toolchain(ctx)
    if isinstance(loaded, str):
        return []
    return [Path(str(loaded.get("GOBIN", ""))), *gopath_bins(str(loaded.get("GOPATH", "")))]


def gopath_bins(gopath: str) -> list[Path]:
    first = gopath.split(os.pathsep)[0]
    if not first:
        return []
    return [Path(first) / "bin"]


def pinned(ctx: Context, name: str, path: str) -> str:
    found = mod_identity(version_text(ctx, path))
    module_name, version = PINS[name]
    if found == f"{module_name} {version}":
        return path
    return pin_problem(name, path, found, module_name, version)


def version_text(ctx: Context, path: str) -> str:
    return run([*go_bin(ctx), "version", "-m", path], cwd=ctx.go_root(), timeout=60)[1]


def pin_problem(name: str, path: str, found: str, module_name: str, version: str) -> str:
    return f"{name} at {path} is {found}; the gate is pinned to {module_name} {version}: {INSTALL[name]}"


def mod_identity(output: str) -> str:
    for line in output.splitlines():
        found = mod_parts(line)
        if found is not None:
            return found
    return NO_MODULE


def mod_parts(line: str) -> str | None:
    parts = filled(line.split("\t"))
    if mod_line(parts):
        return f"{parts[1]} {parts[2]}"
    return None


def filled(parts: list[str]) -> list[str]:
    return [part for part in parts if part]


def mod_line(parts: list[str]) -> bool:
    return len(parts) >= 3 and parts[0] == "mod"


def rel(ctx: Context, path: str | Path) -> str:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = ctx.go_root() / candidate
    return relative_to_root(ctx, candidate, path)


def relative_to_root(ctx: Context, candidate: Path, original: str | Path) -> str:
    try:
        return candidate.resolve().relative_to(ctx.root.resolve()).as_posix()
    except ValueError:
        return str(original)


def root_prefix(ctx: Context) -> str:
    prefix = rel(ctx, ctx.go_root())
    if prefix == ".":
        return ""
    return prefix + SLASH


def in_scope(ctx: Context, paths: list[Path]) -> list[Path]:
    if not ctx.scope_changed:
        return paths
    return [path for path in paths if ctx.in_scope(rel(ctx, path))]


def excluded(ctx: Context, relative: str, key: str) -> bool:
    prefix = root_prefix(ctx)
    return any(matches(relative, prefix + pattern.strip(SLASH)) for pattern in listify(ctx.go(key)))


def matches(relative: str, pattern: str) -> bool:
    return relative == pattern or relative.startswith(pattern + SLASH) or fnmatch.fnmatch(relative, pattern)


def mutation_excluded(ctx: Context, relative: str) -> bool:
    key = "coverage_exclude"
    if ctx.go("mutation_exclude") is not None:
        key = "mutation_exclude"
    return excluded(ctx, relative, key)


def tags(ctx: Context, flag: str) -> list[str]:
    values = listify(ctx.go("build_tags"))
    if not values:
        return []
    return [flag, ",".join(values)]


def sources(ctx: Context) -> list[Path]:
    return [path for path in files(ctx) if not path.name.endswith("_test.go")]


def tests(ctx: Context) -> list[Path]:
    return [path for path in files(ctx) if path.name.endswith("_test.go")]


def files(ctx: Context) -> list[Path]:
    root = ctx.go_root()
    if not root.is_dir():
        return []
    return sorted(path for path in root.rglob("*.go") if kept(ctx, path))


def kept(ctx: Context, path: Path) -> bool:
    return not skipped(ctx, path) and not generated(path)


def skipped(ctx: Context, path: Path) -> bool:
    relative = below_root(ctx, path)
    if relative is None:
        return True
    return bad_tree(ctx, path, relative)


def below_root(ctx: Context, path: Path) -> Path | None:
    try:
        return path.resolve().relative_to(ctx.go_root().resolve())
    except ValueError:
        return None


def bad_tree(ctx: Context, path: Path, relative: Path) -> bool:
    return bad_parts(relative.parts[:-1]) or nested_module(ctx, path) or under_benchmarks(ctx.root, path)


def bad_parts(parts: tuple[str, ...]) -> bool:
    return any(bad_part(part) for part in parts)


def bad_part(part: str) -> bool:
    return part in SKIP_NAMES or part.startswith(".") or part.startswith("_")


def nested_module(ctx: Context, path: Path) -> bool:
    root = ctx.go_root().resolve()
    current = path.resolve().parent
    while current != root and root in current.parents:
        if (current / "go.mod").is_file():
            return True
        current = current.parent
    return False


def generated(path: Path) -> bool:
    if not path.is_file():
        return False
    return generated_text(path.read_text(errors="replace"))


def generated_text(text: str) -> bool:
    for line in text.splitlines():
        if line.startswith("package "):
            return False
        if GENERATED.match(line):
            return True
    return False


def import_folder(ctx: Context, module_path: str, imported: str) -> str | None:
    if outside_module(imported, module_path):
        return None
    return joined_folder(ctx, module_path, imported)


def outside_module(imported: str, module_path: str) -> bool:
    return imported != module_path and not imported.startswith(module_path + SLASH)


def joined_folder(ctx: Context, module_path: str, imported: str) -> str:
    if imported == module_path:
        return root_prefix(ctx).rstrip(SLASH) or "."
    return root_prefix(ctx) + imported[len(module_path) + 1 :]


def scanner_digest() -> str:
    return hashlib.sha256(b"".join(scan_input(name).read_bytes() for name in SCAN_INPUTS)).hexdigest()


def scan_input(name: str) -> Path:
    return SCAN_DIR / name


def scanner_paths(ctx: Context) -> tuple[Path, Path]:
    folder = ctx.work / "go-scan"
    return folder / "marestail-go-scan", folder / "stamp"


def build_scanner(ctx: Context) -> str | None:
    binary, stamp = scanner_paths(ctx)
    digest = scanner_digest()
    if scanner_fresh(binary, stamp, digest):
        return None
    return compile_scanner(ctx, binary, stamp, digest)


def scanner_fresh(binary: Path, stamp: Path, digest: str) -> bool:
    return binary.is_file() and stamp.is_file() and stamp.read_text() == digest


def compile_scanner(ctx: Context, binary: Path, stamp: Path, digest: str) -> str | None:
    binary.parent.mkdir(parents=True, exist_ok=True)
    code, output = run([*go_bin(ctx), "build", "-trimpath", "-o", str(binary), "."], cwd=SCAN_DIR, env=SCAN_ENV, timeout=300)
    error = build_failure(code, output, binary)
    if error is None:
        stamp.write_text(digest)
    return error


def build_failure(code: int, output: str, binary: Path) -> str | None:
    if code == 0 and binary.is_file():
        return None
    if code == MISSING_TOOL:
        return go_missing()
    return f"go scanner build failed: {output_tail(output)}"


def scan(ctx: Context, mode: str, paths: list[Path], uses: list[Path] | None = None) -> tuple[list[Any] | None, str | None]:
    if not paths:
        return [], None
    error = build_scanner(ctx)
    if error:
        return None, error
    return run_scanner(ctx, mode, scan_args(mode, paths, uses))


def scan_args(mode: str, paths: list[Path], uses: list[Path] | None) -> list[str]:
    args = [mode, *map(str, paths)]
    if uses:
        return [*args, "--uses", *map(str, uses)]
    return args


def run_scanner(ctx: Context, mode: str, args: list[str]) -> tuple[list[Any] | None, str | None]:
    binary, _stamp = scanner_paths(ctx)
    code, output = run([str(binary), *args], cwd=ctx.root, timeout=600)
    if code != 0:
        return None, f"go scanner failed ({mode}): {output_tail(output)}"
    found: list[Any] = json.loads(output or "[]")
    return found, None


def load_coverage(ctx: Context) -> dict[str, Any] | None:
    path = ctx.work / "go-coverage.json"
    if path.exists():
        loaded: dict[str, Any] = json.loads(path.read_text())
        return loaded
    return None
