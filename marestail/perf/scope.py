from pathlib import Path

DIRECTORY = "perf"


def is_benchmark(relative: str | Path) -> bool:
    return Path(relative).parts[:1] == (DIRECTORY,)


def under_benchmarks(root: Path, path: Path) -> bool:
    return is_benchmark(path.relative_to(root))
