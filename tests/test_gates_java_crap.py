import itertools
import json
import time
from pathlib import Path
from typing import Any

import pytest

from marestail import java
from marestail.context import Context
from marestail.gates import java_crap
from marestail.report import Result
from tests.conftest import checked, make_context, reject_none, untimed

APP = "src/main/java/app/App.java"
CORE = "src/main/java/app/Core.java"
COVERAGE = {
    "files": {
        APP: {"lines": {"3": 1, "4": 1, "5": 0, "6": 0, "10": 2}, "missing_lines": [5, 6], "missing_branches": [[4, 1, 2]]},
        CORE: {"lines": {"2": 0}, "missing_lines": [2], "missing_branches": []},
    }
}
MEMBERS = [
    {"file": APP, "line": 3, "startLine": 3, "endLine": 6, "name": "App.run", "complexity": 5},
    {"file": APP, "line": 10, "startLine": 10, "endLine": 10, "name": "App.ok", "complexity": 1},
    {"file": CORE, "line": 2, "startLine": 2, "endLine": 3, "name": "Core.bad", "complexity": 7},
]


@pytest.fixture(autouse=True)
def clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "time", itertools.count(100.0, 1.5).__next__)


def project(root: Path, coverage: dict[str, Any] | None = COVERAGE) -> None:
    for relative in (APP, CORE):
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_text("class X {}\n")
    (root / ".marestail").mkdir()
    if coverage is not None:
        (root / ".marestail" / "java-coverage.json").write_text(json.dumps(coverage))


def fake_scan(monkeypatch: pytest.MonkeyPatch, reply: tuple[Any, str | None]) -> list[tuple[str, list[Path]]]:
    seen: list[tuple[str, list[Path]]] = []

    def scan(ctx: Context, mode: str, paths: list[Path], extra: list[str] | None = None) -> tuple[Any, str | None]:
        seen.append((mode, paths))
        return reply

    monkeypatch.setattr(java, "scan", reject_none(scan))
    return seen


def fields(result: Result) -> tuple[str, bool, str, list[str], float]:
    return result.gate, result.ok, result.summary, result.findings, result.seconds


def test_needs_coverage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project(tmp_path, None)
    seen = fake_scan(monkeypatch, ([], None))
    result = untimed(java_crap.run_gate(make_context(tmp_path)), java_crap.GATE)
    assert fields(result) == ("java.crap", False, "no coverage data; java.tests must run first", [], 0.0)
    assert seen == []


def test_skips_without_files_in_scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project(tmp_path)
    seen = fake_scan(monkeypatch, ([], None))
    result = untimed(java_crap.run_gate(make_context(tmp_path, scope_changed=True, changed={"README.md"})), java_crap.GATE)
    assert fields(result) == ("java.crap", True, "skipped: no Java files in scope", [], 0.0)
    assert seen == []


def test_reports_scan_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project(tmp_path)
    seen = fake_scan(monkeypatch, (None, "java not found"))
    result = checked(java_crap.run_gate(make_context(tmp_path)), java_crap.GATE)
    assert fields(result) == ("java.crap", False, "java not found", [], 1.5)
    assert seen == [("complexity", [tmp_path / APP, tmp_path / CORE])]


def test_scores_every_member(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project(tmp_path)
    fake_scan(monkeypatch, (MEMBERS, None))
    result = checked(java_crap.run_gate(make_context(tmp_path)), java_crap.GATE)
    assert fields(result) == (
        "java.crap",
        False,
        "3 methods, 2 above CRAP 4",
        [
            f"{CORE}:2 Core.bad crap=56.0 (cc=7, coverage=0%)",
            f"{APP}:3 App.run crap=10.4 (cc=5, coverage=40%)",
        ],
        1.5,
    )


def test_configured_limit_and_exclusions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project(tmp_path)
    fake_scan(monkeypatch, (MEMBERS, None))
    ctx = make_context(tmp_path, {"java": {"crap_max": 10.5, "coverage_exclude": ["src/main/java/app/Core.java"]}})
    result = checked(java_crap.run_gate(ctx), java_crap.GATE)
    assert fields(result) == ("java.crap", True, "3 methods, 0 above CRAP 10.5", [], 1.5)


def test_scoped_run_keeps_touched_members(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project(tmp_path)
    seen = fake_scan(monkeypatch, (MEMBERS, None))
    ctx = make_context(tmp_path, scope_changed=True, changed={APP}, changed_lines_map={APP: {6}})
    result = checked(java_crap.run_gate(ctx), java_crap.GATE)
    assert seen == [("complexity", [tmp_path / APP])]
    assert fields(result) == (
        "java.crap",
        False,
        "2 methods, 2 above CRAP 4",
        [f"{CORE}:2 Core.bad crap=56.0 (cc=7, coverage=0%)", f"{APP}:3 App.run crap=10.4 (cc=5, coverage=40%)"],
        1.5,
    )


def test_gated_members_unscoped_keeps_all(tmp_path: Path) -> None:
    assert java_crap.gated_members(make_context(tmp_path), MEMBERS) is MEMBERS


@pytest.mark.parametrize(("lines", "expected"), [({3}, True), ({6}, True), ({2, 7}, False), (set(), False)])
def test_touches_hunk(tmp_path: Path, lines: set[int], expected: bool) -> None:
    ctx = make_context(tmp_path, scope_changed=True, changed={APP}, changed_lines_map={APP: lines})
    assert java_crap.touches_hunk(MEMBERS[0], ctx) is expected


def test_touches_hunk_without_gated_lines(tmp_path: Path) -> None:
    assert java_crap.touches_hunk(MEMBERS[0], make_context(tmp_path))
    scoped = make_context(tmp_path, scope_changed=True, changed={CORE})
    assert java_crap.touches_hunk(MEMBERS[0], scoped)


def test_worst_sorts_descending() -> None:
    scored = [{"crap": 5.0}, {"crap": 4.0}, {"crap": 9.0}, {"crap": 4.5}]
    assert java_crap.worst(scored, 4.0) == [{"crap": 9.0}, {"crap": 5.0}, {"crap": 4.5}]


def test_score(tmp_path: Path) -> None:
    assert java_crap.score(make_context(tmp_path), MEMBERS[0], COVERAGE) == {
        "file": APP,
        "line": 3,
        "name": "App.run",
        "cc": 5,
        "cov": 0.4,
        "crap": 5**2 * 0.6**3 + 5,
    }


def test_excluded_coverage_is_full(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(java, "coverage_excluded", lambda ctx, file: True)
    scored = java_crap.score(make_context(tmp_path), MEMBERS[0], COVERAGE)
    assert scored["cov"] == java_crap.COVERED == 1.0


def test_score_without_file_coverage(tmp_path: Path) -> None:
    member = {**MEMBERS[0], "file": "src/main/java/app/Gone.java"}
    assert java_crap.score(make_context(tmp_path), member, COVERAGE)["crap"] == 30


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [(3, 6, 0.4), (3, 4, 2 / 3), (10, 10, 1.0), (5, 6, 0.0), (7, 9, 0.0), (1, 100, 0.5)],
)
def test_window(start: int, end: int, expected: float) -> None:
    assert java_crap.window(COVERAGE["files"][APP], start, end) == pytest.approx(expected)


def test_window_empty() -> None:
    assert java_crap.window({}, 1, 10) == 0.0
    assert java_crap.lines_in({"lines": {"1": 3, "2": 0, "12": 1}}, 1, 11) == [3, 0]
    assert java_crap.branches_missed({"missing_branches": [[1, 2, 4], [5, 1, 2], [20, 3, 4]]}, 1, 5) == 3


def test_describe() -> None:
    finding = {"file": "A.java", "line": 7, "name": "A.m", "crap": 12.345, "cc": 3, "cov": 0.256}
    assert java_crap.describe(finding) == "A.java:7 A.m crap=12.3 (cc=3, coverage=26%)"
