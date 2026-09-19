import fnmatch
import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

from marestail.context import Context, under_benchmarks
from marestail.shell import run

CARGO_TOML = "Cargo.toml"
SCAN_DIR = Path(__file__).resolve().parent / "rs" / "scan"
SCAN_BIN = Path("rs-scan") / "release" / "marestail-rs-scan"
SCAN_INPUTS = ("main.rs", CARGO_TOML, "Cargo.lock")
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


def rel(ctx: Context, path: str | Path) -> str:
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


def cargo(ctx: Context, args: list[str], timeout: int = 1800, cwd: Path | None = None) -> tuple[int, str]:
    return run([*cargo_bin(ctx), *args], cwd=cwd or ctx.rust_root(), env=env(ctx), timeout=timeout)


def missing(code: int, output: str, tool: str) -> str | None:
    if code == 127:
        return f"cargo is not installed: {INSTALL['cargo']}"
    if "no such command" in output.lower() or "is not installed for the toolchain" in output.lower():
        return f"cargo {tool} is not installed: {INSTALL[tool]}"
    return None


def skipped(ctx: Context, path: Path) -> bool:
    return any(part in SKIP_DIRS for part in path.relative_to(ctx.root).parts) or under_benchmarks(ctx.root, path)


def rust_files(ctx: Context, root: Path, pattern: str) -> set[Path]:
    return {path for folder in root.glob(pattern) for path in folder.rglob("*.rs") if not skipped(ctx, path)}


def sources(ctx: Context) -> list[Path]:
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
    return [prefix + pattern.strip("/") for pattern in listify(ctx.rust(key, []))]


def matches(relative: str, pattern: str) -> bool:
    return relative == pattern or relative.startswith(pattern + "/") or fnmatch.fnmatch(relative, pattern)


def excluded(ctx: Context, relative: str, key: str) -> bool:
    return any(matches(relative, pattern) for pattern in exclude_patterns(ctx, key))


def scanner_digest() -> str:
    return hashlib.sha256(b"".join((SCAN_DIR / name).read_bytes() for name in SCAN_INPUTS)).hexdigest()


def scanner_fresh(binary: Path, stamp: Path, digest: str) -> bool:
    return binary.exists() and stamp.exists() and stamp.read_text() == digest


def build_error(code: int, output: str, binary: Path) -> str | None:
    if code == 0 and binary.exists():
        return None
    return missing(code, output, "clippy") or f"rust scanner build failed: {output.strip()[-300:]}"


def build_scanner(ctx: Context) -> str | None:
    binary, stamp = ctx.work / SCAN_BIN, ctx.work / "rs-scan" / "stamp"
    digest = scanner_digest()
    if scanner_fresh(binary, stamp, digest):
        return None
    code, output = run(
        [*cargo_bin(ctx), "build", "--release", "--locked", "--quiet", "--manifest-path", str(SCAN_DIR / CARGO_TOML)],
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
        return None, f"rust scanner failed ({mode}): {output.strip()[-300:]}"
    found: list[Any] = json.loads(output or "[]")
    return found, None


def scan(
    ctx: Context, mode: str, paths: list[Path], extra: list[str] | None = None, uses: list[Path] | None = None
) -> tuple[list[Any] | None, str | None]:
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
