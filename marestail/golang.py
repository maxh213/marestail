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
_GO_MOD = "go.mod"
SCAN_DIR = PACKAGE / "go" / "scan"
SCAN_INPUTS = ("main.go", _GO_MOD)
SCAN_ENV = {"GOTOOLCHAIN": "local", "GOWORK": "off", "GOFLAGS": "", "CGO_ENABLED": "0"}
_SKIP_NAMES = {"vendor", "testdata", ".marestail", ".git", "node_modules"}
_GENERATED = re.compile(r"^// Code generated .* DO NOT EDIT\.$")
_VERSION = re.compile(r"^go(\d+)\.(\d+)")
_ERROR_TAIL = 300
_MISSING_TOOL = 127
TOOLCHAIN = "go-toolchain"
MODULE_KEY = "go-module"
_NO_MODULE = "no module version"
_SLASH = "/"

__all__ = [
    "INSTALL",
    "MINIMUM",
    "MODULE_KEY",
    "PACKAGE",
    "PINS",
    "SCAN_DIR",
    "SCAN_ENV",
    "SCAN_INPUTS",
    "TOOLCHAIN",
    "build_scanner",
    "excluded",
    "files",
    "generated",
    "go_bin",
    "go_missing",
    "import_folder",
    "in_scope",
    "kept",
    "listify",
    "load_coverage",
    "module",
    "mutation_excluded",
    "preflight",
    "rel",
    "scan",
    "scanner_digest",
    "scanner_paths",
    "sources",
    "tags",
    "tests",
    "tool",
    "toolchain",
]


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


def _output_tail(output: str) -> str:
    return output.strip()[-_ERROR_TAIL:]


def _json_object(output: str) -> dict[str, Any] | None:
    parsed = _decoded(output)
    if isinstance(parsed, dict):
        return parsed
    return None


def _decoded(output: str) -> Any:
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
    loaded = _read_toolchain(ctx)
    ctx.memo[TOOLCHAIN] = loaded
    return loaded


def _read_toolchain(ctx: Context) -> dict[str, Any] | str:
    code, output = run([*go_bin(ctx), "env", "-json", "GOVERSION", "GOROOT", "GOBIN", "GOPATH"], cwd=ctx.go_root(), timeout=60)
    return _toolchain_result(code, output)


def _toolchain_result(code: int, output: str) -> dict[str, Any] | str:
    if code == _MISSING_TOOL:
        return go_missing()
    return _env_payload(code, output)


def _env_payload(code: int, output: str) -> dict[str, Any] | str:
    parsed = _json_object(output)
    if code != 0 or parsed is None:
        return f"go env failed: {_output_tail(output)}"
    return _checked_version(parsed)


def _checked_version(parsed: dict[str, Any]) -> dict[str, Any] | str:
    problem = _version_problem(parsed)
    if problem is None:
        return parsed
    return problem


def _version_problem(parsed: dict[str, Any]) -> str | None:
    found = str(parsed.get("GOVERSION", ""))
    if _version_ok(found):
        return None
    return f"go 1.25 or newer is required, found {found}: {INSTALL['go']}"


def _version_ok(found: str) -> bool:
    match = _VERSION.match(found)
    if match is None:
        return False
    return (int(match.group(1)), int(match.group(2))) >= MINIMUM


def module(ctx: Context) -> str:
    cached = ctx.memo.get(MODULE_KEY)
    if isinstance(cached, str):
        return cached
    loaded = _read_module(ctx)
    ctx.memo[MODULE_KEY] = loaded
    return loaded


def _read_module(ctx: Context) -> str:
    if not (ctx.go_root() / _GO_MOD).is_file():
        return _missing_mod(ctx)
    code, output = run([*go_bin(ctx), "mod", "edit", "-json"], cwd=ctx.go_root(), timeout=60)
    return _module_result(code, output)


def _missing_mod(ctx: Context) -> str:
    return f"{_root_prefix(ctx)}{_GO_MOD}:1 missing; [go] root must name the folder that holds {_GO_MOD}"


def _module_result(code: int, output: str) -> str:
    if code == _MISSING_TOOL:
        return go_missing()
    return _module_path(code, output)


def _module_path(code: int, output: str) -> str:
    found = _path_of(_json_object(output))
    if code != 0 or found is None:
        return f"go mod edit -json failed: {_output_tail(output)}"
    return found


def _path_of(parsed: dict[str, Any] | None) -> str | None:
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
    if _module_problem(found):
        return found
    return _toolchain_problem(toolchain(ctx))


def _module_problem(found: str) -> bool:
    return " " in found


def _toolchain_problem(loaded: dict[str, Any] | str) -> str | None:
    if isinstance(loaded, str):
        return loaded
    return None


def tool(ctx: Context, name: str) -> str:
    path = _tool_path(ctx, name)
    if path is None:
        return f"{name} is not installed: {INSTALL[name]}"
    return _pinned(ctx, name, path)


def _tool_path(ctx: Context, name: str) -> str | None:
    configured = ctx.go(name.replace("-", "_"))
    if configured:
        return str(configured)
    return shutil.which(name) or _installed_tool(ctx, name)


def _installed_tool(ctx: Context, name: str) -> str | None:
    for folder in _bin_dirs(ctx):
        candidate = folder / name
        if candidate.is_file():
            return str(candidate)
    return None


def _bin_dirs(ctx: Context) -> list[Path]:
    loaded = toolchain(ctx)
    if isinstance(loaded, str):
        return []
    return [*_folder(_env_text(loaded, "GOBIN")), *_gopath_bins(_env_text(loaded, "GOPATH"))]


def _env_text(loaded: dict[str, Any], key: str) -> str:
    return str(loaded.get(key) or "")


def _folder(text: str) -> list[Path]:
    return [Path(text)] if text else []


def _gopath_bins(gopath: str) -> list[Path]:
    first = gopath.split(os.pathsep)[0]
    if not first:
        return []
    return [Path(first) / "bin"]


def _pinned(ctx: Context, name: str, path: str) -> str:
    found = _mod_identity(_version_text(ctx, path))
    module_name, version = PINS[name]
    if found == f"{module_name} {version}":
        return path
    return _pin_problem(name, path, found, module_name, version)


def _version_text(ctx: Context, path: str) -> str:
    return run([*go_bin(ctx), "version", "-m", path], cwd=ctx.go_root(), timeout=60)[1]


def _pin_problem(name: str, path: str, found: str, module_name: str, version: str) -> str:
    return f"{name} at {path} is {found}; the gate is pinned to {module_name} {version}: {INSTALL[name]}"


def _mod_identity(output: str) -> str:
    for line in output.splitlines():
        found = _mod_parts(line)
        if found is not None:
            return found
    return _NO_MODULE


def _mod_parts(line: str) -> str | None:
    parts = _filled(line.split("\t"))
    if _mod_line(parts):
        return f"{parts[1]} {parts[2]}"
    return None


def _filled(parts: list[str]) -> list[str]:
    return [part for part in parts if part]


def _mod_line(parts: list[str]) -> bool:
    return len(parts) >= 3 and parts[0] == "mod"


def rel(ctx: Context, path: str | Path) -> str:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = ctx.go_root() / candidate
    return _relative_to_root(ctx, candidate, path)


def _relative_to_root(ctx: Context, candidate: Path, original: str | Path) -> str:
    try:
        return candidate.resolve().relative_to(ctx.root.resolve()).as_posix()
    except ValueError:
        return str(original)


def _root_prefix(ctx: Context) -> str:
    prefix = rel(ctx, ctx.go_root())
    if prefix == ".":
        return ""
    return prefix + _SLASH


def in_scope(ctx: Context, paths: list[Path]) -> list[Path]:
    if not ctx.scope_changed:
        return paths
    return [path for path in paths if ctx.in_scope(rel(ctx, path))]


def excluded(ctx: Context, relative: str, key: str) -> bool:
    prefix = _root_prefix(ctx)
    return any(_matches(relative, prefix + pattern.strip(_SLASH)) for pattern in listify(ctx.go(key)))


def _matches(relative: str, pattern: str) -> bool:
    return relative == pattern or relative.startswith(pattern + _SLASH) or fnmatch.fnmatch(relative, pattern)


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
    return not _skipped(ctx, path) and not generated(path)


def _skipped(ctx: Context, path: Path) -> bool:
    relative = _below_root(ctx, path)
    if relative is None:
        return True
    return _bad_tree(ctx, path, relative)


def _below_root(ctx: Context, path: Path) -> Path | None:
    try:
        return path.resolve().relative_to(ctx.go_root().resolve())
    except ValueError:
        return None


def _bad_tree(ctx: Context, path: Path, relative: Path) -> bool:
    return _bad_parts(relative.parts[:-1]) or _nested_module(ctx, path) or under_benchmarks(ctx.root, path)


def _bad_parts(parts: tuple[str, ...]) -> bool:
    return any(_bad_part(part) for part in parts)


def _bad_part(part: str) -> bool:
    return part in _SKIP_NAMES or part.startswith(".") or part.startswith("_")


def _nested_module(ctx: Context, path: Path) -> bool:
    root = ctx.go_root().resolve()
    return any((folder / _GO_MOD).is_file() for folder in path.resolve().parents if root in folder.parents)


def generated(path: Path) -> bool:
    if not path.is_file():
        return False
    return _generated_text(path.read_text(errors="replace"))


def _generated_text(text: str) -> bool:
    for line in text.splitlines():
        if line.startswith("package "):
            return False
        if _GENERATED.match(line):
            return True
    return False


def import_folder(ctx: Context, module_path: str, imported: str) -> str | None:
    if _outside_module(imported, module_path):
        return None
    return _joined_folder(ctx, module_path, imported)


def _outside_module(imported: str, module_path: str) -> bool:
    return imported != module_path and not imported.startswith(module_path + _SLASH)


def _joined_folder(ctx: Context, module_path: str, imported: str) -> str:
    if imported == module_path:
        return _root_prefix(ctx).rstrip(_SLASH) or "."
    return _root_prefix(ctx) + imported[len(module_path) + 1 :]


def scanner_digest() -> str:
    return hashlib.sha256(b"".join(_scan_input(name).read_bytes() for name in SCAN_INPUTS)).hexdigest()


def _scan_input(name: str) -> Path:
    return SCAN_DIR / name


def scanner_paths(ctx: Context) -> tuple[Path, Path]:
    folder = ctx.work / "go-scan"
    return folder / "marestail-go-scan", folder / "stamp"


def build_scanner(ctx: Context) -> str | None:
    binary, stamp = scanner_paths(ctx)
    digest = scanner_digest()
    if _scanner_fresh(binary, stamp, digest):
        return None
    return _compile_scanner(ctx, binary, stamp, digest)


def _scanner_fresh(binary: Path, stamp: Path, digest: str) -> bool:
    return binary.is_file() and stamp.is_file() and stamp.read_text() == digest


def _compile_scanner(ctx: Context, binary: Path, stamp: Path, digest: str) -> str | None:
    binary.parent.mkdir(parents=True, exist_ok=True)
    code, output = run([*go_bin(ctx), "build", "-trimpath", "-o", str(binary), "."], cwd=SCAN_DIR, env=SCAN_ENV, timeout=300)
    error = _build_failure(code, output, binary)
    if error is None:
        stamp.write_text(digest)
    return error


def _build_failure(code: int, output: str, binary: Path) -> str | None:
    if code == 0 and binary.is_file():
        return None
    if code == _MISSING_TOOL:
        return go_missing()
    return f"go scanner build failed: {_output_tail(output)}"


def scan(ctx: Context, mode: str, paths: list[Path], uses: list[Path] | None = None) -> tuple[list[Any] | None, str | None]:
    if not paths:
        return [], None
    error = build_scanner(ctx)
    if error:
        return None, error
    return _run_scanner(ctx, mode, _scan_args(mode, paths, uses))


def _scan_args(mode: str, paths: list[Path], uses: list[Path] | None) -> list[str]:
    args = [mode, *map(str, paths)]
    if uses:
        return [*args, "--uses", *map(str, uses)]
    return args


def _run_scanner(ctx: Context, mode: str, args: list[str]) -> tuple[list[Any] | None, str | None]:
    binary, _stamp = scanner_paths(ctx)
    code, output = run([str(binary), *args], cwd=ctx.root, timeout=600)
    if code != 0:
        return None, f"go scanner failed ({mode}): {_output_tail(output)}"
    found: list[Any] = json.loads(output or "[]")
    return found, None


def load_coverage(ctx: Context) -> dict[str, Any] | None:
    path = ctx.work / "go-coverage.json"
    if path.exists():
        loaded: dict[str, Any] = json.loads(path.read_text())
        return loaded
    return None
