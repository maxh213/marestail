import json
from pathlib import Path
from typing import Any

import pytest

from marestail import dotnet
from marestail.gates import cs_crap
from marestail.report import Result
from tests.conftest import gate_shape, make_context

COVERAGE = {
    "files": {
        "App/A.cs": {"lines": {"3": 1, "4": 0, "5": 1, "20": 0}, "missing_branches": [[4, 1], [30, 0]]},
    }
}


def member(name: str, start: int, end: int, complexity: int, has_body: bool = True, file: str = "App/A.cs") -> dict[str, Any]:
    return {"file": file, "line": start, "name": name, "startLine": start, "endLine": end, "complexity": complexity, "hasBody": has_body}


MEMBERS = [member("Low", 3, 5, 2), member("High", 3, 5, 6), member("Dead", 20, 20, 5), member("Abstract", 40, 40, 3, False)]


def view(result: Result) -> tuple[str, bool, str, list[str]]:
    return gate_shape(result)


def project(root: Path, coverage: dict[str, Any] | None = COVERAGE, **fields: Any) -> Any:
    for name in ("App/App.csproj", "AppTests/AppTests.csproj", "App/A.cs", "App/B.cs", "AppTests/T.cs"):
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text("")
    ctx = make_context(root, {"dotnet": {"crap_max": 4}}, **fields)
    ctx.work.mkdir()
    if coverage is not None:
        (ctx.work / dotnet.COVERAGE_JSON).write_text(json.dumps(coverage))
    return ctx


@pytest.fixture(autouse=True)
def fresh_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dotnet, "PROJECTS", {})


class FakeScan:
    def __init__(self, reply: tuple[Any, str | None]) -> None:
        self.reply = reply
        self.calls: list[tuple[str, list[Path]]] = []
        self.contexts: list[Any] = []

    def __call__(self, ctx: Any, mode: str, paths: list[Path]) -> tuple[Any, str | None]:
        self.contexts.append(ctx)
        self.calls.append((mode, paths))
        return self.reply


def install(monkeypatch: pytest.MonkeyPatch, reply: tuple[Any, str | None]) -> FakeScan:
    fake = FakeScan(reply)
    monkeypatch.setattr(dotnet, "scan", fake)
    return fake


def test_needs_coverage(tmp_path: Path) -> None:
    result = cs_crap.run_gate(project(tmp_path, None))
    assert view(result) == ("cs.crap", False, "no coverage data; cs.tests must run first", [])
    assert result.seconds == 0.0


def test_skips_without_files_in_scope(tmp_path: Path) -> None:
    result = cs_crap.run_gate(project(tmp_path, scope_changed=True, changed={"README.md"}))
    assert view(result) == ("cs.crap", True, "skipped: no C# files in scope", [])


def test_reports_scan_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = install(monkeypatch, (None, "C# scanner failed (complexity): boom"))
    ctx = project(tmp_path)
    result = cs_crap.run_gate(ctx)
    assert view(result) == ("cs.crap", False, "C# scanner failed (complexity): boom", [])
    assert result.seconds < 1_000_000
    assert fake.calls == [("complexity", [tmp_path / "App" / "A.cs", tmp_path / "App" / "B.cs"])]
    assert fake.contexts == [ctx]


def test_scores_all_members(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, (MEMBERS, None))
    assert view(cs_crap.run_gate(project(tmp_path))) == (
        "cs.crap",
        False,
        "4 members, 2 above CRAP 4",
        [
            "App/A.cs:20 Dead crap=30.0 (cc=5, coverage=0%)",
            "App/A.cs:3 High crap=10.5 (cc=6, coverage=50%)",
        ],
    )


def test_passes_under_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, ([member("Low", 3, 5, 2)], None))
    assert view(cs_crap.run_gate(project(tmp_path))) == ("cs.crap", True, "1 members, 0 above CRAP 4", [])


def test_scoped_keeps_members_touching_hunks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = install(monkeypatch, (MEMBERS, None))
    ctx = project(tmp_path, scope_changed=True, changed={"App/A.cs"}, changed_lines_map={"App/A.cs": {20}})
    assert view(cs_crap.run_gate(ctx)) == (
        "cs.crap",
        False,
        "1 members, 1 above CRAP 4",
        ["App/A.cs:20 Dead crap=30.0 (cc=5, coverage=0%)"],
    )
    assert fake.calls == [("complexity", [tmp_path / "App" / "A.cs"])]


def test_touches_hunk(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, scope_changed=True, changed={"A.cs"}, changed_lines_map={"A.cs": {5}})
    assert cs_crap.touches_hunk(member("M", 3, 5, 1, file="A.cs"), ctx) is True
    assert cs_crap.touches_hunk(member("M", 6, 9, 1, file="A.cs"), ctx) is False
    assert cs_crap.touches_hunk(member("M", 1, 2, 1, file="A.cs"), make_context(tmp_path)) is True


def test_scoped_members_unscoped_returns_all(tmp_path: Path) -> None:
    assert cs_crap.scoped_members(make_context(tmp_path), MEMBERS) is MEMBERS


def test_score_excluded_counts_as_covered(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"dotnet": {"coverage_exclude": ["App"]}})
    assert cs_crap.score(ctx, member("Dead", 20, 20, 5), COVERAGE) == {
        "file": "App/A.cs",
        "line": 20,
        "name": "Dead",
        "cc": 5,
        "cov": 1.0,
        "crap": 5.0,
    }


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [(3, 5, 0.5), (3, 3, 1.0), (20, 20, 0.0), (6, 19, 0.0), (1, 40, 1 / 3)],
)
def test_window(start: int, end: int, expected: float) -> None:
    assert cs_crap.window(COVERAGE["files"]["App/A.cs"], start, end) == expected


def test_window_without_data() -> None:
    assert cs_crap.window({}, 1, 10) == 0.0
    assert cs_crap.window({"lines": {"2": 3}}, 1, 10) == 1.0


def test_offenders_sorted_by_crap() -> None:
    scored = [{"crap": 5.0}, {"crap": 4.0}, {"crap": 9.0}]
    assert cs_crap.offenders(scored, 4.0) == [{"crap": 9.0}, {"crap": 5.0}]


def test_describe() -> None:
    finding = {"file": "A.cs", "line": 7, "name": "Run", "crap": 12.345, "cc": 3, "cov": 0.256}
    assert cs_crap.describe(finding) == "A.cs:7 Run crap=12.3 (cc=3, coverage=26%)"
