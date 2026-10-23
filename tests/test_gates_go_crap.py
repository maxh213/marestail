import itertools
import json
import time
from pathlib import Path
from typing import Any

import pytest

from marestail import golang
from marestail.config import Config
from marestail.context import build
from marestail.gates import go_crap
from marestail.gates._crap import crap_score, describe
from tests.conftest import checked, commit_all, make_context, reject_none, untimed

GATE = go_crap.GATE
BASE_A = "package a\n\nfunc F(n int) int {\n\tif n > 0 {\n\t\treturn 1\n\t}\n\treturn 0\n}\n\nfunc G(a, b bool) bool { return a && b }\n"
BASE_B = "package a\n\nfunc H() int {\n\treturn 1\n}\n"


@pytest.fixture(autouse=True)
def clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "time", itertools.count(1.0, 0.25).__next__)


def coverage(root: Path, files: dict[str, Any]) -> None:
    work = root / ".marestail"
    work.mkdir(exist_ok=True)
    (work / "go-coverage.json").write_text(json.dumps({"files": files}))


def source(root: Path) -> Path:
    path = root / "a.go"
    path.write_text("package a\n")
    return path


def test_needs_coverage_before_preflight(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(golang, "preflight", lambda ctx: (_ for _ in ()).throw(AssertionError("preflight")))
    result = untimed(go_crap.run_gate(make_context(tmp_path, {"go": {}})), GATE)
    assert (result.ok, result.summary, result.findings) == (False, "no coverage data; go.tests must run first", [])


def test_skips_when_nothing_is_in_scope(tmp_path: Path) -> None:
    coverage(tmp_path, {})
    result = untimed(go_crap.run_gate(make_context(tmp_path, {"go": {}})), GATE)
    assert (result.ok, result.summary) == (True, "skipped: no files in scope")
    source(tmp_path)
    excluded = make_context(tmp_path, {"go": {"coverage_exclude": ["a.go"]}})
    assert untimed(go_crap.run_gate(excluded), GATE).summary == "skipped: no files in scope"
    scoped = make_context(tmp_path, {"go": {}}, scope_changed=True, changed={"other.go"})
    assert untimed(go_crap.run_gate(scoped), GATE).summary == "skipped: no files in scope"


def test_scanner_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    coverage(tmp_path, {})
    path = source(tmp_path)
    calls: list[tuple[str, list[Path]]] = []

    def scan(ctx: object, mode: str, files: list[Path]) -> tuple[None, str]:
        calls.append((mode, files))
        return None, "go scanner failed (complexity): boom"

    monkeypatch.setattr(golang, "scan", reject_none(scan))
    result = checked(go_crap.run_gate(make_context(tmp_path, {"go": {}})), GATE)
    assert (result.ok, result.summary, result.findings) == (False, "complexity scanner failed", ["go scanner failed (complexity): boom"])
    assert calls == [("complexity", [path])]


def test_statement_share_and_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = source(tmp_path)
    blocks = [[10, 1, 10, 2, 2, 1], [12, 1, 12, 2, 1, 0], [30, 1, 30, 2, 9, 0], [50, 20, 50, 30, 1, 1]]
    coverage(tmp_path, {"a.go": {"blocks": blocks, "lines": {"10": 1, "12": 0, "30": 0, "50": 1}}})
    records = [
        {"file": str(path), "name": "Classify", "line": 10, "end": 20, "complexity": 5},
        {"file": str(path), "name": "Empty", "line": 40, "end": 44, "complexity": 1},
        {"file": str(path), "name": "Both", "line": 50, "end": 50, "complexity": 2},
    ]
    calls: list[tuple[str, list[Path]]] = []

    def scan(ctx: object, mode: str, files: list[Path]) -> tuple[list[dict[str, Any]], None]:
        calls.append((mode, files))
        return records, None

    monkeypatch.setattr(golang, "scan", reject_none(scan))
    result = checked(go_crap.run_gate(make_context(tmp_path, {"go": {"crap_max": 4}})), GATE)
    share = 2 / 3
    expected = describe({"file": "a.go", "line": 10, "name": "Classify", "cc": 5, "cov": share, "crap": crap_score(5, share)})
    assert result.findings == [expected]
    assert expected == "a.go:10 Classify crap=5.9 (cc=5, coverage=67%)"
    assert result.summary == "3 functions, 1 above CRAP 4"
    assert result.ok is False
    assert calls == [("complexity", [path])]
    loose = checked(go_crap.run_gate(make_context(tmp_path, {"go": {"crap_max": 6}})), GATE)
    assert loose.ok is True
    assert loose.summary == "3 functions, 0 above CRAP 6"


def test_hyper_judges_changed_functions_against_the_base_copy(git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first = git_repo / "a.go"
    second = git_repo / "b.go"
    first.write_text(BASE_A)
    second.write_text(BASE_B)
    commit_all(git_repo)
    first.write_text(BASE_A.replace("n > 0", "n > 1").replace("a && b", "a || b"))
    second.write_text(BASE_B.replace("return 1", "return 2"))
    blocks = [[3, 19, 4, 11, 1, 1], [4, 11, 6, 3, 1, 0], [7, 2, 7, 10, 1, 1], [10, 24, 10, 40, 1, 1]]
    coverage(git_repo, {"a.go": {"blocks": blocks, "lines": {"3": 1, "4": 0, "7": 1, "10": 1}}})
    named = {
        BASE_A: [{"name": "F", "line": 3, "end": 8, "complexity": 4}, {"name": "G", "line": 10, "end": 10, "complexity": 2}],
        BASE_B: [{"name": "H", "line": 3, "end": 5, "complexity": 4}],
    }
    calls: list[tuple[str, list[Path]]] = []

    def scan(ctx: object, mode: str, files: list[Path]) -> tuple[list[dict[str, Any]], None]:
        calls.append((mode, files))
        if files == [first, second]:
            return [{**fn, "file": str(path)} for path, text in ((first, BASE_A), (second, BASE_B)) for fn in named[text]], None
        return [{**fn, "file": str(files[0])} for fn in named[files[0].read_text()]], None

    monkeypatch.setattr(golang, "scan", reject_none(scan))
    ctx = build(Config(root=git_repo, raw={"go": {}, "git": {"base": "HEAD"}}), False, hyper=True)
    result = checked(go_crap.run_gate(ctx), GATE)
    assert result.findings == ["a.go:3 F crap=4.6 (cc=4, coverage=67%); changed lines not covered: 4"]
    assert result.summary == "3 innermost changed functions, 2 above CRAP 4, 1 of them no worse than base"
    assert result.ok is False
    assert calls[0] == ("complexity", [first, second])
    assert [(mode, [path.name for path in files]) for mode, files in calls[1:]] == [("complexity", ["a.go"]), ("complexity", ["b.go"])]
    assert all(files[0].is_relative_to(git_repo / ".marestail") for _, files in calls[1:])


def test_hyper_without_a_base_complexity(git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = git_repo / "a.go"
    path.write_text("package a\nfunc F() int {\n\treturn 1\n}\n")
    commit_all(git_repo)
    path.write_text("package a\nfunc F() int {\n\tif true {\n\t\treturn 2\n\t}\n\treturn 1\n}\n")
    coverage(git_repo, {"a.go": {"blocks": [[2, 1, 6, 2, 1, 0]], "lines": {"2": 0}}})
    seen: list[list[Path]] = []

    def scan(ctx: Any, mode: str, files: list[Path]) -> tuple[list[dict[str, Any]] | None, str | None]:
        seen.append(files)
        if files != [path]:
            return None, "base failed"
        return [{"file": str(path), "name": "F", "line": 2, "end": 6, "complexity": 2}], None

    monkeypatch.setattr(golang, "scan", scan)
    ctx = build(Config(root=git_repo, raw={"go": {}, "git": {"base": "HEAD"}}), False, hyper=True)
    result = checked(go_crap.run_gate(ctx), GATE)
    assert seen[0] == [path]
    assert [copy.name for copy in seen[1]] == ["a.go"]
    assert seen[1] != [path]
    assert "no base complexity for a.go" in result.summary
    assert result.findings == ["a.go:2 F crap=6.0 (cc=2, coverage=0%)"]


def test_score_without_a_profile_entry(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"go": {}})
    record = {"file": "missing.go", "name": "F", "line": 1, "end": 2, "complexity": 1}
    scored = go_crap.scored_functions(ctx, {"files": {}}, [record])
    assert scored == [go_crap.score(ctx, record, {})]
    assert scored[0]["cov"] == 0.0
    covered = go_crap.covered_unit(go_crap.go_unit("missing.go", record), {})
    assert (covered["cov"], covered["missing"]) == (0.0, set())


def test_base_units_return_scanned_functions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    record = {"file": "copy.go", "name": "F", "line": 2, "end": 4, "complexity": 1}
    copy = tmp_path / "copy.go"
    calls: list[tuple[str, list[Path]]] = []

    def scan(ctx: object, mode: str, files: list[Path]) -> tuple[list[dict[str, Any]], None]:
        calls.append((mode, files))
        return [record], None

    monkeypatch.setattr(golang, "scan", reject_none(scan))
    units = go_crap.base_units(make_context(tmp_path, {"go": {}}), copy)
    assert units == [go_crap.go_unit("", record)]
    assert calls == [("complexity", [copy])]
    monkeypatch.setattr(golang, "scan", lambda ctx, mode, files: (None, "boom"))
    assert go_crap.base_units(make_context(tmp_path, {"go": {}}), copy) is None
