from pathlib import Path

import pytest

from marestail.perf import hygiene


def write(root: Path, relative: str, text: str = "") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest.mark.parametrize(
    ("relative", "expected"),
    [
        ("perf/bench_a.py", False),
        ("perf/_scratch.py", True),
        ("perf/__init__.py", False),
        ("perf/__main__.py", False),
        ("perf/__odd.py", True),
        ("perf/_tmp/data.json", True),
        ("perf/__pycache__/x.py", True),
        ("perf/lib.pyc", True),
        ("perf/lib.pyo", True),
        ("perf/lib/helper.py", False),
    ],
)
def test_is_scratch(relative: str, expected: bool) -> None:
    assert hygiene.is_scratch(Path(relative)) is expected


def test_perf_files_without_folder(tmp_path: Path) -> None:
    assert hygiene.perf_files(tmp_path) == []
    assert hygiene.discard_scratch(tmp_path) == []


def test_perf_files_sorted_files_only(tmp_path: Path) -> None:
    write(tmp_path, "perf/b.py")
    write(tmp_path, "perf/a/c.py")
    write(tmp_path, "other/d.py")
    assert hygiene.perf_files(tmp_path) == [tmp_path / "perf/a/c.py", tmp_path / "perf/b.py"]


def test_scratch_files_skip_referenced(tmp_path: Path) -> None:
    write(tmp_path, "perf/bench_a.py", "import _helper\n")
    write(tmp_path, "perf/_helper.py", "x = 1\n")
    write(tmp_path, "perf/_junk.py", "import _helper\n")
    write(tmp_path, "perf/__pycache__/bench_a.cpython.pyc", "bench_a")
    write(tmp_path, "perf/_helper.pyc", "")
    assert hygiene.scratch_files(tmp_path) == [
        tmp_path / "perf/__pycache__/bench_a.cpython.pyc",
        tmp_path / "perf/_helper.pyc",
        tmp_path / "perf/_junk.py",
    ]


def test_fingerprint_follows_harness(tmp_path: Path) -> None:
    write(tmp_path, "perf/bench_a.py", "a")
    write(tmp_path, "perf/lib.py", "shared")
    first = hygiene.fingerprint(tmp_path, "perf/bench_a.py")
    assert len(first) == 16
    write(tmp_path, "perf/bench_b.py", "b")
    write(tmp_path, "perf/_scratch.py", "s")
    assert hygiene.fingerprint(tmp_path, "perf/bench_a.py") == first
    write(tmp_path, "perf/lib.py", "changed")
    assert hygiene.fingerprint(tmp_path, "perf/bench_a.py") != first


def test_fingerprint_exact_digest(tmp_path: Path) -> None:
    write(tmp_path, "perf/bench_a.py", "a")
    assert hygiene.fingerprint(tmp_path, "perf/bench_a.py") == "1cafd1892ced16f1"


def test_harness_files(tmp_path: Path) -> None:
    write(tmp_path, "perf/bench_a.py")
    write(tmp_path, "perf/bench_b.py")
    write(tmp_path, "perf/_used.py")
    write(tmp_path, "perf/_unused.py")
    write(tmp_path, "perf/lib.py", "import _used")
    assert hygiene.harness_files(tmp_path, "perf/bench_a.py") == [
        tmp_path / "perf/_used.py",
        tmp_path / "perf/bench_a.py",
        tmp_path / "perf/lib.py",
    ]


def test_discard_scratch(tmp_path: Path) -> None:
    write(tmp_path, "perf/bench_a.py", "import _kept")
    write(tmp_path, "perf/_kept.py")
    write(tmp_path, "perf/_tmp/deep/x.json")
    write(tmp_path, "perf/_tmp/y.json")
    write(tmp_path, "perf/__pycache__/bench_a.pyc")
    (tmp_path / "perf/empty").mkdir()
    (tmp_path / "perf/_empty").mkdir()
    assert hygiene.discard_scratch(tmp_path) == ["perf/__pycache__/bench_a.pyc", "perf/_tmp/deep/x.json", "perf/_tmp/y.json"]
    remaining = sorted(path.relative_to(tmp_path).as_posix() for path in (tmp_path / "perf").rglob("*"))
    assert remaining == ["perf/_kept.py", "perf/bench_a.py", "perf/empty"]


def test_discard_keeps_non_empty_scratch_directory(tmp_path: Path) -> None:
    write(tmp_path, "perf/bench_a.py", "load table")
    write(tmp_path, "perf/_data/table.csv")
    assert hygiene.discard_scratch(tmp_path) == []
    assert (tmp_path / "perf/_data/table.csv").exists()


def test_kept_text_joins_with_newlines(tmp_path: Path) -> None:
    write(tmp_path, "perf/a.py", "one")
    write(tmp_path, "perf/b.py", "two")
    files = [tmp_path / "perf/a.py", tmp_path / "perf/b.py"]
    assert hygiene.kept_text(tmp_path, files) == "one\ntwo"


def test_deepest_first_orders_by_depth(tmp_path: Path) -> None:
    (tmp_path / "perf" / "a" / "deep").mkdir(parents=True)
    (tmp_path / "perf" / "z").mkdir(parents=True)
    found = hygiene.deepest_first(tmp_path)
    assert [path.relative_to(tmp_path).as_posix() for path in found] == ["perf/a/deep", "perf/a", "perf/z"]


def test_kept_text_ignores_undecodable_bytes(tmp_path: Path) -> None:
    write(tmp_path, "perf/a.py", "one")
    rogue = tmp_path / "perf" / "b.py"
    rogue.write_bytes(b"tw\xffo")
    assert hygiene.kept_text(tmp_path, [tmp_path / "perf/a.py", rogue]) == "one\ntwo"


def test_drop_scratch_ignores_missing(tmp_path: Path) -> None:
    hygiene.drop_scratch(tmp_path / "missing")
