import itertools
import json
import time
from pathlib import Path
from typing import Any

import pytest

from marestail import golang
from marestail.gates import go_tests
from tests.conftest import checked, make_context

MODULE = "example.com/mod"


@pytest.fixture(autouse=True)
def clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "time", itertools.count(1.0, 0.25).__next__)


def ready(root: Path, go: dict[str, Any] | None = None, **fields: Any) -> Any:
    (root / "go.mod").write_text(f"module {MODULE}\n")
    ctx = make_context(root, {"go": go or {}}, **fields)
    ctx.memo[golang.MODULE_KEY] = MODULE
    ctx.memo[golang.TOOLCHAIN] = {"GOVERSION": "go1.25.0", "GOROOT": "/usr/lib/go", "GOBIN": "", "GOPATH": ""}
    return ctx


def cover(ctx: Any, text: str) -> None:
    ctx.work.mkdir(parents=True, exist_ok=True)
    (ctx.work / go_tests.PROFILE).write_text(text)


def test_command_tags_and_args(tmp_path: Path, fake_run: Any) -> None:
    ctx = ready(tmp_path, {"build_tags": ["a", "b"], "test_args": ["-parallel", "1"], "go": "/usr/lib/go/bin/go"})

    def reply(command: list[str]) -> tuple[int, str]:
        cover(ctx, f"mode: set\n{MODULE}/a.go:1.1,1.2 1 1\n")
        return 0, ""

    fake = fake_run(go_tests, reply)
    result = checked(go_tests.run_gate(ctx), go_tests.GATE)
    profile = str(ctx.work / go_tests.PROFILE)
    assert fake.calls == [
        [
            "/usr/lib/go/bin/go",
            "test",
            "-json",
            "-count=1",
            "-covermode=set",
            "-coverprofile",
            profile,
            "-tags",
            "a,b",
            "-parallel",
            "1",
            "./...",
        ]
    ]
    assert "-coverpkg" not in fake.calls[0]
    assert fake.options == [{"cwd": tmp_path, "timeout": 3600}]
    assert result.summary == "0 passed, statement coverage 100.0%, 0 gaps (need 0)"


def test_plain_command_and_stale_files(tmp_path: Path, fake_run: Any) -> None:
    ctx = ready(tmp_path)
    for name in (go_tests.PROFILE, go_tests.COVERAGE, go_tests.SONAR):
        cover(ctx, "stale") if name == go_tests.PROFILE else (ctx.work / name).write_text("stale")

    def reply(command: list[str]) -> tuple[int, str]:
        cover(ctx, f"mode: set\n{MODULE}/a.go:1.1,1.2 1 1\n")
        return 0, '{"Action":"pass","Package":"example.com/mod"}\n{"Action":"pass","Test":"T"}\n'

    fake = fake_run(go_tests, reply)
    result = checked(go_tests.run_gate(ctx), go_tests.GATE)
    assert fake.calls[0][:6] == ["go", "test", "-json", "-count=1", "-covermode=set", "-coverprofile"]
    assert fake.calls[0][-1] == "./..."
    assert result.summary == "1 passed, statement coverage 100.0%, 0 gaps (need 0)"
    assert "on changed files" not in result.summary


def test_deletes_reports_before_a_missing_go(tmp_path: Path, fake_run: Any) -> None:
    ctx = make_context(tmp_path, {"go": {}})
    ctx.work.mkdir()
    for name in (go_tests.PROFILE, go_tests.COVERAGE, go_tests.SONAR):
        (ctx.work / name).write_text("stale")
    fake = fake_run(go_tests, [])
    result = checked(go_tests.run_gate(ctx), go_tests.GATE)
    assert (result.ok, result.summary, result.findings) == (
        False,
        "go toolchain not ready",
        ["go.mod:1 missing; [go] root must name the folder that holds go.mod"],
    )
    assert fake.calls == []
    assert not any((ctx.work / name).exists() for name in (go_tests.PROFILE, go_tests.COVERAGE, go_tests.SONAR))


def test_test_exit_127(tmp_path: Path, fake_run: Any) -> None:
    ctx = ready(tmp_path)
    fake_run(go_tests, [(127, "missing")])
    result = checked(go_tests.run_gate(ctx), go_tests.GATE)
    assert (result.ok, result.summary, result.findings) == (False, "go toolchain not ready", [golang.go_missing()])


def test_failed_tests_keep_output_and_plain_lines(tmp_path: Path, fake_run: Any) -> None:
    ctx = ready(tmp_path)
    output = '\n{"Action":"pass","Test":"Sub"}\nnot-json\n{not json\n{"Action":"fail","Test":"Bad","Output":"nope\\n"}\n{"Action":"pass","Output":1}\n'
    fake_run(go_tests, [(1, output)])
    result = checked(go_tests.run_gate(ctx), go_tests.GATE)
    assert result.summary == "tests failed"
    assert result.findings == ["not-json", "{not json", "nope"]


def test_missing_profile_and_mode_only(tmp_path: Path, fake_run: Any) -> None:
    ctx = ready(tmp_path)
    fake_run(go_tests, [(0, "ran")])
    missing = checked(go_tests.run_gate(ctx), go_tests.GATE)
    assert (missing.ok, missing.summary, missing.findings) == (False, "go test wrote no coverage profile", ["ran"])
    ctx = ready(tmp_path)

    def reply(command: list[str]) -> tuple[int, str]:
        cover(ctx, "mode: set\nnoise\n")
        return 0, "only mode"

    fake_run(go_tests, reply)
    empty = checked(go_tests.run_gate(ctx), go_tests.GATE)
    assert (empty.ok, empty.summary, empty.findings) == (False, "coverage report lists no source files", ["only mode"])


def test_profile_merge_reports_and_scope(tmp_path: Path, fake_run: Any) -> None:
    plant = tmp_path / "gen.go"
    plant.write_text("// Code generated by tool DO NOT EDIT.\npackage p\n")
    ctx = ready(
        tmp_path,
        {"coverage_exclude": ["skip.go"]},
        scope_changed=True,
        changed={"a.go", "b.go"},
        changed_lines_map={"a.go": {3, 4, 5}, "b.go": set()},
    )
    profile = "\n".join(
        [
            "mode: set",
            f"{MODULE}/a.go:1.1,1.5 1 5",
            f"{MODULE}/a.go:1.6,1.8 1 0",
            f"{MODULE}/a.go:1.1,1.5 1 1",
            f"{MODULE}/a.go:3.1,3.2 1 0",
            f"{MODULE}/a.go:3.4,3.8 1 0",
            f"{MODULE}/a.go:4.1,6.2 1 0",
            f"{MODULE}/a.go:8.1,8.2 1 0",
            f"{MODULE}/b.go:2.1,2.3 2 1",
            f"{MODULE}/b.go:2.1,2.3 2 4",
            f"{MODULE}/b.go:2.1,2.3 2 2",
            f"{MODULE}/zero.go:9.1,9.2 0 0",
            f"{MODULE}/gen.go:1.1,1.2 1 0",
            f"{MODULE}/skip.go:1.1,1.2 1 0",
            "other.com/x/a.go:1.1,1.2 1 0",
            f"{MODULE}/out.go:1.1,1.2 1 0",
        ]
    )

    def reply(command: list[str]) -> tuple[int, str]:
        cover(ctx, profile + "\n")
        return 0, '{"Action":"pass","Test":"One"}\n{"Action":"pass","Test":"One/sub"}\n'

    fake_run(go_tests, reply)
    result = checked(go_tests.run_gate(ctx), go_tests.GATE)
    assert result.findings == ["a.go:3 not covered", "a.go:4 not covered"]
    assert result.summary == "2 passed, statement coverage 33.3%, 2 gaps on changed files (need 0)"
    stored = json.loads((ctx.work / go_tests.COVERAGE).read_text())
    assert stored == {
        "files": {
            "a.go": {
                "blocks": [
                    [1, 1, 1, 5, 1, 5],
                    [1, 6, 1, 8, 1, 0],
                    [3, 1, 3, 2, 1, 0],
                    [3, 4, 3, 8, 1, 0],
                    [4, 1, 6, 2, 1, 0],
                    [8, 1, 8, 2, 1, 0],
                ],
                "lines": {"1": 0, "3": 0, "4": 0, "8": 0},
            },
            "b.go": {"blocks": [[2, 1, 2, 3, 2, 4]], "lines": {"2": 4}},
            "out.go": {"blocks": [[1, 1, 1, 2, 1, 0]], "lines": {"1": 0}},
            "zero.go": {"blocks": [[9, 1, 9, 2, 0, 0]], "lines": {"9": 0}},
        },
        "totals": {"percent_covered": 33.3},
    }
    assert (ctx.work / go_tests.SONAR).read_text() == (
        "mode: set\n"
        "a.go:1.1,1.5 1 5\n"
        "a.go:1.6,1.8 1 0\n"
        "a.go:3.1,3.2 1 0\n"
        "a.go:3.4,3.8 1 0\n"
        "a.go:4.1,6.2 1 0\n"
        "a.go:8.1,8.2 1 0\n"
        "b.go:2.1,2.3 2 4\n"
        "out.go:1.1,1.2 1 0\n"
        "zero.go:9.1,9.2 0 0\n"
    )


def test_zero_statements_are_zero_percent(tmp_path: Path) -> None:
    files = {"a.go": {"blocks": [[1, 1, 1, 2, 0, 0]], "lines": {"1": 0}}}
    assert go_tests.percent(files) == 0.0
    assert go_tests.line_map([(1, 0, 1, 1, 1, 2), (1, 2, 1, 3, 1, 5)]) == {"1": 2}


def test_unscoped_gap_omits_the_changed_phrase(tmp_path: Path, fake_run: Any) -> None:
    ctx = ready(tmp_path)

    def reply(command: list[str]) -> tuple[int, str]:
        cover(ctx, f"mode: set\n{MODULE}/a.go:4.1,4.2 1 0\n")
        return 0, ""

    fake_run(go_tests, reply)
    result = checked(go_tests.run_gate(ctx), go_tests.GATE)
    assert result.findings == ["a.go:4 not covered"]
    assert result.summary == "0 passed, statement coverage 0.0%, 1 gaps (need 0)"


def test_mod_edit_exit_127_is_the_install_line(tmp_path: Path, fake_run: Any) -> None:
    (tmp_path / "go.mod").write_text("module m\n")
    ctx = make_context(tmp_path, {"go": {}})
    ctx.work.mkdir()
    for name in (go_tests.PROFILE, go_tests.COVERAGE, go_tests.SONAR):
        (ctx.work / name).write_text("stale")
    fake = fake_run(golang, [(127, "missing")])
    result = checked(go_tests.run_gate(ctx), go_tests.GATE)
    assert (result.ok, result.summary, result.findings) == (False, "go toolchain not ready", [golang.go_missing()])
    assert fake.calls == [["go", "mod", "edit", "-json"]]
    assert not any((ctx.work / name).exists() for name in (go_tests.PROFILE, go_tests.COVERAGE, go_tests.SONAR))
