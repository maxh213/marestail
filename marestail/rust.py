import fnmatch
import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

from marestail.context import Context, live, under_benchmarks
from marestail.shell import run

PACKAGE = Path(__file__).resolve().parent
CARGO_TOML = "Cargo.toml"
SLASH = "/"
EMPTY: list[str] = []
CLIPPY = "clippy"
ERROR_TAIL = 300
DIGEST_JOIN = b""
SCAN_DIR = PACKAGE / "rs" / "scan"
SCAN_MANIFEST = PACKAGE / "rs" / "scan" / CARGO_TOML
SCAN_BIN = Path("rs-scan") / "release" / "marestail-rs-scan"
SCAN_INPUTS = ("main.rs", CARGO_TOML, "Cargo.lock")
STAGE = "rs-scan-src"
SKIP_DIRS = {"target", ".marestail", ".git", "node_modules", "mutants.out", "mutants.out.old"}
USE_DIRS = ("tests", "examples", "benches")
LLVM_TOOLS = (("LLVM_COV", "llvm-cov"), ("LLVM_PROFDATA", "llvm-profdata"))
INSTALL = {
    "cargo": "install Rust with cargo: https://rustup.rs or your package manager",
    "llvm-cov": "cargo install --locked cargo-llvm-cov (and rustup component add llvm-tools, or set LLVM_COV and LLVM_PROFDATA to an LLVM matching rustc -vV)",
    "mutants": "cargo install --locked cargo-mutants",
    "clippy": "rustup component add clippy rustfmt, or install your distribution's rust package",
}


def listify(value: Any) -> list[str]:
    if value is None:
        return []
    return [str(part) for part in value] if isinstance(value, list) else [str(value)]


def configured_list(value: Any) -> list[str]:
    if value is None:
        raise TypeError("list")
    if type(value) is list:
        return [str(part) for part in value]
    return [str(value)]


def rel(ctx: Context, path: str | Path) -> str:
    ctx = live(ctx)
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = ctx.rust_root() / candidate
    try:
        return candidate.resolve().relative_to(ctx.root.resolve()).as_posix()
    except ValueError:
        return str(path)


def env(ctx: Context) -> dict[str, str]:
    return {name: found for name, tool in LLVM_TOOLS if (found := llvm_tool(ctx, name, tool))}


def llvm_tool(ctx: Context, name: str, tool: str) -> str | None:
    configured = ctx.rust(name.lower())
    if configured:
        return str(configured)
    if name in os.environ or shutil.which("rustup"):
        return None
    return shutil.which(tool)


def cargo_bin(ctx: Context) -> list[str]:
    return listify(ctx.rust("cargo", "cargo"))


def require_timeout(timeout: int) -> int:
    if type(timeout) is not int:
        raise TypeError("timeout")
    return timeout


def cargo(ctx: Context, args: list[str], cwd: Path | None = None, *, timeout: int) -> tuple[int, str]:
    return run([*cargo_bin(ctx), *args], cwd=cwd or ctx.rust_root(), env=env(ctx), timeout=require_timeout(timeout))


def missing(code: int, output: str, tool: str) -> str | None:
    require_missing(code, tool)
    if code == 127:
        return f"cargo is not installed: {INSTALL['cargo']}"
    if "no such command" in output.lower() or "is not installed for the toolchain" in output.lower():
        return f"cargo {tool} is not installed: {INSTALL[tool]}"
    return None


def require_missing(code: object, tool: object) -> None:
    if type(code) is not int or type(tool) is not str:
        raise TypeError("missing")


def skipped(ctx: Context, path: Path) -> bool:
    return any(part in SKIP_DIRS for part in path.relative_to(ctx.root).parts) or under_benchmarks(ctx.root, path)


def rust_files(ctx: Context, root: Path, pattern: str) -> set[Path]:
    return {path for folder in root.glob(pattern) for path in folder.rglob("*.rs") if not skipped(ctx, path)}


def sources(ctx: Context) -> list[Path]:
    ctx = live(ctx)
    root = ctx.rust_root()
    found: set[Path] = set()
    for pattern in listify(ctx.rust("sources", ["src"])):
        found.update(rust_files(ctx, root, pattern))
    return sorted(path for path in found if not excluded(ctx, rel(ctx, path), "source_exclude"))


def crates(ctx: Context) -> set[Path]:
    return {path.parent for path in ctx.rust_root().rglob(CARGO_TOML) if not skipped(ctx, path)}


def crate_uses(ctx: Context, crate: Path) -> set[Path]:
    return {path for folder in USE_DIRS for path in (crate / folder).rglob("*.rs") if not skipped(ctx, path)}


def use_files(ctx: Context) -> list[Path]:
    ctx = live(ctx)
    found: set[Path] = set()
    for crate in crates(ctx):
        found.update(crate_uses(ctx, crate))
    return sorted(found)


def in_scope(ctx: Context, paths: list[Path]) -> list[Path]:
    if not ctx.scope_changed:
        return paths
    return [path for path in paths if ctx.in_scope(rel(ctx, path))]


def exclude_patterns(ctx: Context, key: str) -> list[str]:
    prefix = rel(ctx, ctx.rust_root())
    prefix = "" if prefix == "." else prefix + "/"
    return [prefix + pattern.strip(SLASH) for pattern in configured_list(ctx.rust(key, EMPTY))]


def matches(relative: str, pattern: str) -> bool:
    return relative == pattern or relative.startswith(pattern + "/") or fnmatch.fnmatch(relative, pattern)


def excluded(ctx: Context, relative: str, key: str) -> bool:
    return any(matches(relative, pattern) for pattern in exclude_patterns(ctx, key))


def scan_input(name: str) -> Path:
    bundled = SCAN_DIR / name
    if bundled.is_file():
        return bundled
    if name == CARGO_TOML:
        return SCAN_MANIFEST
    return bundled


def scanner_digest() -> str:
    return hashlib.sha256(DIGEST_JOIN.join(scan_input(name).read_bytes() for name in SCAN_INPUTS)).hexdigest()


def scanner_fresh(binary: Path, stamp: Path, digest: str) -> bool:
    return binary.exists() and stamp.exists() and stamp.read_text() == digest


def build_error(code: int, output: str, binary: Path) -> str | None:
    if code == 0 and binary.exists():
        return None
    return missing(code, output, CLIPPY) or f"rust scanner build failed: {output.strip()[-ERROR_TAIL:]}"


def staged_crate(ctx: Context) -> Path:
    ctx = live(ctx)
    manifest = scan_input(CARGO_TOML)
    if manifest.parent == SCAN_DIR:
        return SCAN_DIR
    stage = ctx.work / STAGE
    stage.mkdir(parents=True, exist_ok=True)
    for name in SCAN_INPUTS:
        shutil.copy(scan_input(name), stage / name)
    return stage


def build_scanner(ctx: Context) -> str | None:
    binary, stamp = ctx.work / SCAN_BIN, ctx.work / "rs-scan" / "stamp"
    digest = scanner_digest()
    if scanner_fresh(binary, stamp, digest):
        return None
    code, output = run(
        [*cargo_bin(ctx), "build", "--release", "--locked", "--quiet", "--manifest-path", str(staged_crate(ctx) / CARGO_TOML)],
        cwd=ctx.root,
        env={"CARGO_TARGET_DIR": str(ctx.work / "rs-scan")},
        timeout=900,
    )
    error = build_error(code, output, binary)
    if error is None:
        stamp.write_text(digest)
    return error


def uses_args(uses: list[Path] | None) -> list[str]:
    return ["--uses", *map(str, uses)] if uses else []


def run_scanner(ctx: Context, mode: str, args: list[str]) -> tuple[list[Any] | None, str | None]:
    code, output = run([str(ctx.work / SCAN_BIN), mode, *args], cwd=ctx.root, timeout=600)
    if code != 0:
        return None, f"rust scanner failed ({mode}): {output.strip()[-ERROR_TAIL:]}"
    found: list[Any] = json.loads(output or "[]")
    return found, None


def scan(
    ctx: Context, mode: str, paths: list[Path], extra: list[str] | None = None, uses: list[Path] | None = None
) -> tuple[list[Any] | None, str | None]:
    ctx = live(ctx)
    if not paths:
        return [], None
    error = build_scanner(ctx)
    if error:
        return None, error
    return run_scanner(ctx, mode, [*(extra or []), *map(str, paths), *uses_args(uses)])


def load_coverage(ctx: Context) -> dict[str, Any] | None:
    path = ctx.work / "rs-coverage.json"
    loaded: dict[str, Any] | None = json.loads(path.read_text()) if path.exists() else None
    return loaded
