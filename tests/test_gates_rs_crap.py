import json
from pathlib import Path
from typing import Any

import pytest

from marestail import rust
from marestail.gates import rs_crap
from tests.conftest import checked, make_context, untimed


def setup_crate(root: Path) -> None:
    for name in ("src/lib.rs", "src/gen/out.rs"):
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text("fn x() {}\n")
    binary = root / ".marestail" / rust.SCAN_BIN
    binary.parent.mkdir(parents=True)
    binary.write_text("")
    (root / ".marestail" / "rs-scan" / "stamp").write_text(rust.scanner_digest())
    coverage = {"files": {"src/lib.rs": {"lines": {"1": 1, "2": 0, "3": 0, "4": 5, "9": 0}}}}
    (root / ".marestail" / "rs-coverage.json").write_text(json.dumps(coverage))


def functions(root: Path) -> str:
    lib = str(root / "src" / "lib.rs")
    return json.dumps(
        [
            {"file": lib, "line": 1, "end": 4, "name": "half", "complexity": 4},
            {"file": lib, "line": 9, "end": 9, "name": "cold", "complexity": 1},
            {"file": "src/gen/out.rs", "line": 1, "end": 1, "name": "gen", "complexity": 2},
        ]
    )


def test_needs_coverage(tmp_path: Path) -> None:
    result = untimed(rs_crap.run_gate(make_context(tmp_path)), rs_crap.GATE)
    assert (result.gate, result.ok, result.summary, result.findings, result.seconds) == (
        "rs.crap",
        False,
        "no coverage data; rs.tests must run first",
        [],
        0.0,
    )


def test_no_files(tmp_path: Path) -> None:
    setup_crate(tmp_path)
    result = untimed(rs_crap.run_gate(make_context(tmp_path, {"rust": {"coverage_ignore_regex": "src/"}})), rs_crap.GATE)
    assert (result.ok, result.summary) == (True, "skipped: no files in scope")


def test_scanner_failure(tmp_path: Path, fake_run: Any) -> None:
    setup_crate(tmp_path)
    fake_run(rust, [(1, "bad\n")])
    result = checked(rs_crap.run_gate(make_context(tmp_path)), rs_crap.GATE)
    assert (result.ok, result.summary, result.findings) == (False, "complexity scanner failed", ["rust scanner failed (complexity): bad"])


def test_scores(tmp_path: Path, fake_run: Any) -> None:
    setup_crate(tmp_path)
    fake = fake_run(rust, [(0, functions(tmp_path))])
    result = checked(rs_crap.run_gate(make_context(tmp_path, {"rust": {"coverage_ignore_regex": "gen/"}})), rs_crap.GATE)
    assert fake.calls == [[str(tmp_path / ".marestail" / rust.SCAN_BIN), "complexity", str(tmp_path / "src" / "lib.rs")]]
    assert (result.ok, result.summary) == (False, "3 functions, 2 above CRAP 4")
    assert result.findings == ["src/lib.rs:1 half crap=6.0 (cc=4, coverage=50%)", "src/gen/out.rs:1 gen crap=6.0 (cc=2, coverage=0%)"]


def test_clean_scores(tmp_path: Path, fake_run: Any) -> None:
    setup_crate(tmp_path)
    fake_run(rust, [(0, "[]")])
    result = checked(rs_crap.run_gate(make_context(tmp_path, {"rust": {"crap_max": 1.5}})), rs_crap.GATE)
    assert (result.ok, result.summary, result.findings) == (True, "0 functions, 0 above CRAP 1.5", [])


def test_crap_result_without_functions(tmp_path: Path) -> None:
    result = rs_crap.crap_result(make_context(tmp_path), {"files": {}}, None, 0.0)
    assert result.summary == "0 functions, 0 above CRAP 4"


@pytest.mark.parametrize(("ignored", "expected"), [(None, False), ("", False), ("gen", True), ("^src", False)])
def test_ignored_path(ignored: str | None, expected: bool) -> None:
    assert rs_crap.ignored_path(ignored, Path("/r/src/gen/a.rs")) is expected


@pytest.mark.parametrize(("hits", "share"), [([], 0.0), ([0, 2, 3, 0], 0.5), ([1], 1.0)])
def test_covered_share(hits: list[int], share: float) -> None:
    assert rs_crap.covered_share(hits) == share


def test_measured() -> None:
    assert rs_crap.measured({"1": 1, "5": 2, "10": 3, "11": 4}, 5, 10) == [2, 3]


def test_score(tmp_path: Path) -> None:
    fn = {"file": "src/a.rs", "line": 1, "end": 2, "name": "f", "complexity": 3}
    assert rs_crap.score(fn, {}, make_context(tmp_path)) == {"file": "src/a.rs", "line": 1, "name": "f", "cc": 3, "cov": 0.0, "crap": 12.0}
