from pathlib import Path

import pytest

from marestail.perf import scope


@pytest.mark.parametrize(
    ("relative", "expected"),
    [("perf/bench_a.py", True), (Path("perf"), True), ("perfx/a.py", False), ("src/perf/a.py", False), ("", False)],
)
def test_is_benchmark(relative: str | Path, expected: bool) -> None:
    assert scope.is_benchmark(relative) is expected


def test_under_benchmarks(tmp_path: Path) -> None:
    assert scope.under_benchmarks(tmp_path, tmp_path / "perf" / "x.py") is True
    assert scope.under_benchmarks(tmp_path, tmp_path / "src" / "x.py") is False
