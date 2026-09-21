import hashlib
from pathlib import Path

FOLDER = "perf"
COMPILED = (".pyc", ".pyo")
CACHE = "__pycache__"
ROOT_ERROR = "root"
BENCH_ERROR = "bench"
JOIN = "\n"
ERRORS = "ignore"
SIZE_WIDTH = 8


def is_scratch(relative: Path) -> bool:
    return relative.suffix in COMPILED or any(part == CACHE or scratch_name(part) for part in relative.parts)


def scratch_name(name: str) -> bool:
    return name.startswith("_") and not (name.startswith("__") and Path(name).stem.endswith("__"))


def empty_entries(_folder: Path) -> list[Path]:
    return []


def listed_entries(folder: Path) -> list[Path]:
    return sorted(folder.rglob("*"))


def perf_entries(root: Path) -> list[Path]:
    folder = root / FOLDER
    chosen = (empty_entries, listed_entries)[folder.is_dir()]
    return chosen(folder)


def perf_files(root: Path) -> list[Path]:
    return [path for path in perf_entries(root) if path.is_file()]


def kept_text(root: Path, files: list[Path]) -> str:
    return JOIN.join(path.read_text(errors=ERRORS) for path in files if not is_scratch(path.relative_to(root)))


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


def checked_root(root: Path) -> None:
    if not isinstance(root, Path):
        raise TypeError(ROOT_ERROR)


def checked_bench(bench: str) -> None:
    if type(bench) is not str:
        raise TypeError(BENCH_ERROR)


def fingerprint(root: Path, bench: str) -> str:
    checked_root(root)
    checked_bench(bench)
    digest = hashlib.sha256()
    for path in harness_files(root, bench):
        for part in (path.relative_to(root).as_posix().encode(), path.read_bytes()):
            digest.update(len(part).to_bytes(SIZE_WIDTH))
            digest.update(part)
    return digest.hexdigest()[:16]


def depth_of(path: Path) -> int:
    return len(path.parts)


def deepest_first(root: Path) -> list[Path]:
    return sorted((path for path in perf_entries(root) if path.is_dir()), key=depth_of, reverse=True)


def remove_empty_scratch_directories(root: Path) -> None:
    for directory in deepest_first(root):
        if is_scratch(directory.relative_to(root)) and not any(directory.iterdir()):
            directory.rmdir()


def drop_scratch(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return


def discard_scratch(root: Path) -> list[str]:
    removed = scratch_files(root)
    for path in removed:
        drop_scratch(path)
    remove_empty_scratch_directories(root)
    return [path.relative_to(root).as_posix() for path in removed]
