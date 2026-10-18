import itertools
import json
import time
from pathlib import Path
from typing import Any

import pytest

from marestail import golang
from marestail.config import Config
from marestail.context import build
from marestail.gates import _crap, _hyper_crap, go_crap
from marestail.gates._crap import crap_score, describe
from tests.conftest import checked, commit_all, make_context, untimed

GATE = go_crap.GATE


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


def test_scoring_uses_the_shared_crap_helpers() -> None:
    assert go_crap.crap_score is _crap.crap_score
    assert go_crap.describe is _crap.describe
    assert go_crap.above is _crap.above
    assert go_crap.KEY is _crap.KEY
    assert go_crap.DEFAULT is _crap.DEFAULT
    assert go_crap.judged is _hyper_crap.judged
    assert go_crap.Hyper is _hyper_crap.Hyper
    assert go_crap.unit is _hyper_crap.unit
    assert go_crap.unhit_lines is _hyper_crap.unhit_lines


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
    monkeypatch.setattr(golang, "scan", lambda ctx, mode, files: (None, "go scanner failed (complexity): boom"))
    result = checked(go_crap.run_gate(make_context(tmp_path, {"go": {}})), GATE)
    assert (result.ok, result.summary, result.findings) == (False, "complexity scanner failed", ["go scanner failed (complexity): boom"])
    assert path.is_file()


def test_statement_share_and_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = source(tmp_path)
    blocks = [[10, 1, 10, 2, 2, 1], [12, 1, 12, 2, 1, 0], [30, 1, 30, 2, 9, 0]]
    coverage(tmp_path, {"a.go": {"blocks": blocks, "lines": {"10": 1, "12": 0}}})
    records = [
        {"file": str(path), "name": "Classify", "line": 10, "end": 20, "complexity": 5},
        {"file": str(path), "name": "Empty", "line": 40, "end": 44, "complexity": 1},
    ]
    monkeypatch.setattr(golang, "scan", lambda ctx, mode, files: (records, None))
    result = checked(go_crap.run_gate(make_context(tmp_path, {"go": {"crap_max": 4}})), GATE)
    share = 2 / 3
    expected = describe({"file": "a.go", "line": 10, "name": "Classify", "cc": 5, "cov": share, "crap": crap_score(5, share)})
    assert result.findings == [expected]
    assert expected == "a.go:10 Classify crap=5.9 (cc=5, coverage=67%)"
    assert result.summary == "2 functions, 1 above CRAP 4"
    assert result.ok is False
    loose = checked(go_crap.run_gate(make_context(tmp_path, {"go": {"crap_max": 6}})), GATE)
    assert loose.ok is True
    assert loose.summary == "2 functions, 0 above CRAP 6"


def test_hyper_uses_base_units(git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
    monkeypatch.setattr(golang, "scan", lambda ctx, mode, files: ([record], None))
    units = go_crap.base_units(make_context(tmp_path, {"go": {}}), tmp_path / "copy.go")
    assert units == [go_crap.go_unit("", record)]
    monkeypatch.setattr(golang, "scan", lambda ctx, mode, files: (None, "boom"))
    assert go_crap.base_units(make_context(tmp_path, {"go": {}}), tmp_path / "copy.go") is None
