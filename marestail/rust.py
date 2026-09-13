import fnmatch
import hashlib
import json
import os
import shutil
from pathlib import Path

from marestail.context import Context
from marestail.shell import run

SCAN_DIR = Path(__file__).resolve().parent / "rs" / "scan"
SCAN_BIN = Path("rs-scan") / "release" / "marestail-rs-scan"
SKIP_DIRS = {"target", ".marestail", ".git", "node_modules", "mutants.out", "mutants.out.old"}
USE_DIRS = ("tests", "examples", "benches")
INSTALL = {
    "cargo": "install Rust with cargo: https://rustup.rs or your package manager",
    "llvm-cov": "cargo install --locked cargo-llvm-cov (and rustup component add llvm-tools, or set LLVM_COV and LLVM_PROFDATA to an LLVM matching rustc -vV)",
    "mutants": "cargo install --locked cargo-mutants",
    "clippy": "rustup component add clippy rustfmt, or install your distribution's rust package",
}


def listify(value) -> list[str]:
    if value is None:
        return []
    return [str(part) for part in value] if isinstance(value, list) else [str(value)]


def rel(ctx: Context, path) -> str:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = ctx.rust_root() / candidate
    try:
        return candidate.resolve().relative_to(ctx.root.resolve()).as_posix()
    except ValueError:
        return str(path)


def env(ctx: Context) -> dict[str, str]:
    found = {}
    for name, tool in (("LLVM_COV", "llvm-cov"), ("LLVM_PROFDATA", "llvm-profdata")):
        configured = ctx.rust(name.lower())
        if configured:
            found[name] = str(configured)
        elif name not in os.environ and shutil.which(tool) and not shutil.which("rustup"):
            found[name] = str(shutil.which(tool))
    return found


def cargo(ctx: Context, args: list[str], timeout: int = 1800, cwd: Path | None = None) -> tuple[int, str]:
    return run([*listify(ctx.rust("cargo", "cargo")), *args], cwd=cwd or ctx.rust_root(), env=env(ctx), timeout=timeout)


def missing(code: int, output: str, tool: str) -> str | None:
    if code == 127:
        return f"cargo is not installed: {INSTALL['cargo']}"
    if "no such command" in output.lower() or "is not installed for the toolchain" in output.lower():
        return f"cargo {tool} is not installed: {INSTALL[tool]}"
    return None


def skipped(ctx: Context, path: Path) -> bool:
    return any(part in SKIP_DIRS for part in path.relative_to(ctx.root).parts)


def sources(ctx: Context) -> list[Path]:
    root = ctx.rust_root()
    found = set()
    for pattern in listify(ctx.rust("sources", ["src"])):
        for folder in root.glob(pattern):
            found.update(path for path in folder.rglob("*.rs") if not skipped(ctx, path))
    return sorted(path for path in found if not excluded(ctx, rel(ctx, path), "source_exclude"))


def use_files(ctx: Context) -> list[Path]:
    crates = {path.parent for path in ctx.rust_root().rglob("Cargo.toml") if not skipped(ctx, path)}
    found = {path for crate in crates for folder in USE_DIRS for path in (crate / folder).rglob("*.rs") if not skipped(ctx, path)}
    return sorted(found)


def in_scope(ctx: Context, paths: list[Path]) -> list[Path]:
    if not ctx.scope_changed:
        return paths
    return [path for path in paths if rel(ctx, path) in ctx.changed]


def excluded(ctx: Context, relative: str, key: str) -> bool:
    prefix = rel(ctx, ctx.rust_root())
    prefix = "" if prefix == "." else prefix + "/"
    patterns = [prefix + pattern.strip("/") for pattern in listify(ctx.rust(key, []))]
    return any(relative == p or relative.startswith(p + "/") or fnmatch.fnmatch(relative, p) for p in patterns)


def build_scanner(ctx: Context) -> str | None:
    binary, stamp = ctx.work / SCAN_BIN, ctx.work / "rs-scan" / "stamp"
    digest = hashlib.sha256(b"".join((SCAN_DIR / name).read_bytes() for name in ("main.rs", "Cargo.toml", "Cargo.lock"))).hexdigest()
    if binary.exists() and stamp.exists() and stamp.read_text() == digest:
        return None
    code, output = run(
        [*listify(ctx.rust("cargo", "cargo")), "build", "--release", "--locked", "--quiet", "--manifest-path", str(SCAN_DIR / "Cargo.toml")],
        cwd=ctx.root,
        env={"CARGO_TARGET_DIR": str(ctx.work / "rs-scan")},
        timeout=900,
    )
    if code != 0 or not binary.exists():
        return missing(code, output, "clippy") or f"rust scanner build failed: {output.strip()[-300:]}"
    stamp.write_text(digest)
    return None


def scan(ctx: Context, mode: str, paths: list[Path], extra: list[str] | None = None, uses: list[Path] | None = None) -> tuple[list | None, str | None]:
    if not paths:
        return [], None
    error = build_scanner(ctx)
    if error:
        return None, error
    tail = ["--uses", *map(str, uses)] if uses else []
    code, output = run([str(ctx.work / SCAN_BIN), mode, *(extra or []), *map(str, paths), *tail], cwd=ctx.root, timeout=600)
    if code != 0:
        return None, f"rust scanner failed ({mode}): {output.strip()[-300:]}"
    return json.loads(output or "[]"), None


def load_coverage(ctx: Context) -> dict | None:
    path = ctx.work / "rs-coverage.json"
    return json.loads(path.read_text()) if path.exists() else None
