import hashlib
from pathlib import Path

FOLDER = "perf"
COMPILED = (".pyc", ".pyo")
CACHE = "__pycache__"


def is_scratch(relative: Path) -> bool:
    return relative.suffix in COMPILED or any(part == CACHE or scratch_name(part) for part in relative.parts)


def scratch_name(name: str) -> bool:
    return name.startswith("_") and not (name.startswith("__") and Path(name).stem.endswith("__"))


def perf_entries(root: Path) -> list[Path]:
    folder = root / FOLDER
    return sorted(folder.rglob("*")) if folder.is_dir() else []


def perf_files(root: Path) -> list[Path]:
    return [path for path in perf_entries(root) if path.is_file()]


def kept_text(root: Path, files: list[Path]) -> str:
    return "\n".join(path.read_text(errors="ignore") for path in files if not is_scratch(path.relative_to(root)))


def scratch_files(root: Path) -> list[Path]:
    files = perf_files(root)
    kept = kept_text(root, files)
    return [path for path in files if is_scratch(path.relative_to(root)) and not referenced(path, kept)]


def referenced(path: Path, text: str) -> bool:
    return path.suffix not in COMPILED and CACHE not in path.parts and path.stem in text


def harness_file(path: Path, root: Path, bench: str, scratch: set[Path]) -> bool:
    return path.relative_to(root).as_posix() == bench or not (path.name.startswith("bench_") or path in scratch)


def harness_files(root: Path, bench: str) -> list[Path]:
    scratch = set(scratch_files(root))
    return [path for path in perf_files(root) if harness_file(path, root, bench, scratch)]


def fingerprint(root: Path, bench: str) -> str:
    digest = hashlib.sha256()
    for path in harness_files(root, bench):
        for part in (path.relative_to(root).as_posix().encode(), path.read_bytes()):
            digest.update(len(part).to_bytes(8, "big"))
            digest.update(part)
    return digest.hexdigest()[:16]


def deepest_first(root: Path) -> list[Path]:
    return sorted((path for path in perf_entries(root) if path.is_dir()), key=lambda path: len(path.parts), reverse=True)


def remove_empty_scratch_directories(root: Path) -> None:
    for directory in deepest_first(root):
        if is_scratch(directory.relative_to(root)) and not any(directory.iterdir()):
            directory.rmdir()


def discard_scratch(root: Path) -> list[str]:
    removed = scratch_files(root)
    for path in removed:
        path.unlink(missing_ok=True)
    remove_empty_scratch_directories(root)
    return [path.relative_to(root).as_posix() for path in removed]
