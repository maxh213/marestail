from pathlib import Path

from marestail.context import Context
from marestail.shell import run

GL_DIR = Path(__file__).resolve().parent / "gl"
SCAN_DIR = GL_DIR
COVERAGE = GL_DIR / "coverage.escript"


def package_name(root: Path) -> str:
    path = root / "gleam.toml"
    for line in path.read_text().splitlines():
        if line.startswith("name"):
            _, _, value = line.partition("=")
            return value.strip().strip("\"'")
    raise ValueError(f"no name in {path}")


def gleam_sources(ctx: Context) -> list[Path]:
    root = ctx.gleam_root()
    sources = ctx.gleam("sources", ["src"])
    skip = {"build", "test", ".git", ".marestail", "node_modules"}
    files = []
    for folder in sources:
        for path in (root / folder).rglob("*.gleam"):
            if not any(part in skip for part in path.relative_to(ctx.root).parts):
                files.append(path)
    return sorted(files)


def scan(ctx: Context, mode: str, files: list[Path] | None = None) -> tuple[int, str]:
    paths = files if files is not None else gleam_sources(ctx)
    if not paths:
        return 0, "[]"
    ensure_scan_built()
    command = ["gleam", "run", "--no-print-progress", "--", mode, *map(str, paths)]
    return run(command, cwd=SCAN_DIR, timeout=600)


def ensure_scan_built() -> None:
    marker = SCAN_DIR / "build" / "dev" / "erlang" / "marestail_gl_scan" / "ebin" / "marestail_gl_scan.beam"
    if not marker.exists():
        run(["gleam", "build", "--no-print-progress"], cwd=SCAN_DIR, timeout=300)


def extract_coverage(ctx: Context, out_json: Path) -> tuple[int, str]:
    root = ctx.gleam_root()
    name = package_name(root)
    return run(["escript", str(COVERAGE), str(root), name, str(out_json)], cwd=root, timeout=1800)
