import itertools
import json
import time
from pathlib import Path
from typing import Any

import pytest

from marestail import golang
from marestail.gates import go_lint
from tests.conftest import checked, make_context, reject_none, untimed

GATE = go_lint.GATE
BUNDLED = golang.PACKAGE / "go" / "golangci.yml"


@pytest.fixture(autouse=True)
def clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "time", itertools.count(1.0, 0.25).__next__)
    monkeypatch.setattr(golang, "tool", reject_none(lambda ctx, name: {"golangci-lint": "/bin/golangci-lint"}[name]))


def plant(root: Path, relative: str = "a.go", text: str = "package a\n") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def ready(root: Path, go: dict[str, Any] | None = None, **fields: Any) -> Any:
    plant(root, "go.mod", "module example.com/mod\n")
    ctx = make_context(root, {"go": {} if go is None else go}, **fields)
    ctx.memo[golang.MODULE_KEY] = "example.com/mod"
    ctx.memo[golang.TOOLCHAIN] = {"GOVERSION": "go1.25.0", "GOROOT": "/usr/lib/go", "GOBIN": "", "GOPATH": ""}
    return ctx


def issue(path: str, line: int, linter: str, text: str) -> dict[str, Any]:
    return {"FromLinter": linter, "Text": text, "Pos": {"Filename": path, "Line": line, "Column": 1}}


def write_report(command: list[str], payload: dict[str, Any]) -> None:
    path = Path(command[command.index("--output.json.path") + 1])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


def test_skips_without_changed_go_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(golang, "preflight", lambda ctx: (_ for _ in ()).throw(AssertionError("preflight")))
    ctx = make_context(tmp_path, {"go": {}}, scope_changed=True, changed={"README.md"})
    result = untimed(go_lint.run_gate(ctx), GATE)
    assert (result.ok, result.summary, result.findings) == (True, "skipped: no changed Go files", [])


def test_a_changed_go_mod_is_not_a_skip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(golang, "preflight", lambda ctx: "go is not installed: x")
    ctx = make_context(tmp_path, {"go": {}}, scope_changed=True, changed={"go.sum"})
    result = checked(go_lint.run_gate(ctx), GATE)
    assert (result.ok, result.summary, result.findings) == (False, "go toolchain not ready", ["go is not installed: x"])


def test_missing_tool_stops_before_gofmt(tmp_path: Path, fake_run: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(golang, "preflight", lambda ctx: None)
    monkeypatch.setattr(golang, "tool", lambda ctx, name: f"{name} is not installed: {golang.INSTALL[name]}")
    fake = fake_run(go_lint, [])
    result = checked(go_lint.run_gate(ready(tmp_path)), GATE)
    assert (result.ok, result.summary) == (False, "golangci-lint missing")
    assert result.findings == [f"golangci-lint is not installed: {golang.INSTALL['golangci-lint']}"]
    assert fake.calls == []


def test_bundled_config_is_the_pinned_file() -> None:
    text = BUNDLED.read_text()
    assert text.startswith('version: "2"\n')
    assert "default: standard\n" in text
    assert "min-complexity: 10\n" in text
    assert "max-func-lines: 0\n" in text
    assert "- fieldalignment\n" in text
    assert "max-issues-per-linter: 0\n" in text
    assert "revive" not in text
    assert "ST1000" not in text


def test_command_cache_tags_and_custom_config(tmp_path: Path, fake_run: Any) -> None:
    plant(tmp_path, "a.go")
    plant(tmp_path, "a_test.go")
    ctx = ready(tmp_path, {"build_tags": ["a", "b"], "golangci_config": "mine.yml"})
    report = ctx.work / go_lint.REPORT
    report.parent.mkdir()
    report.write_text("stale")

    def reply(command: list[str]) -> tuple[int, str]:
        if command[1] == "run":
            assert not report.exists()
            write_report(command, {"Issues": []})
            return 0, ""
        return 0, ""

    fake = fake_run(go_lint, reply)
    result = checked(go_lint.run_gate(ctx), GATE)
    assert (result.ok, result.summary) == (True, "golangci-lint and gofmt clean")
    assert fake.calls[0] == [
        "/bin/golangci-lint",
        "run",
        "--config",
        str(tmp_path / "mine.yml"),
        "--allow-parallel-runners",
        "--path-mode",
        "abs",
        "--show-stats=false",
        "--max-issues-per-linter=0",
        "--max-same-issues=0",
        "--output.json.path",
        str(report),
        "--build-tags",
        "a,b",
        "./...",
    ]
    assert fake.options[0] == {"cwd": tmp_path, "env": {"GOLANGCI_LINT_CACHE": str(ctx.work / "golangci-cache")}, "timeout": 1800}
    assert fake.calls[1][0] == "/usr/lib/go/bin/gofmt"
    assert fake.calls[1][1:] == ["-l", str(tmp_path / "a.go"), str(tmp_path / "a_test.go")]
    assert fake.options[1] == {"cwd": tmp_path, "timeout": 300}


def test_bundled_config_when_unset(tmp_path: Path) -> None:
    assert go_lint.config_path(ready(tmp_path)) == str(BUNDLED)
    assert go_lint.config_path(ready(tmp_path, {"golangci_config": ""})) == str(BUNDLED)


def expect_lint_failure(tmp_path: Path, fake_run: Any, code: int, present: bool) -> None:
    ctx = ready(tmp_path)

    def reply(command: list[str]) -> tuple[int, str]:
        if present:
            write_report(command, {"Issues": []})
        return code, "x" * 400

    fake_run(go_lint, reply)
    result = checked(go_lint.run_gate(ctx), GATE)
    assert result.ok is False
    assert result.summary == f"golangci-lint failed (exit {code})"
    assert result.findings == ["x" * 300]


@pytest.mark.parametrize(("code", "present"), [(0, False), (1, False), (3, True), (3, False)])
def test_lint_exit_matrix(tmp_path: Path, fake_run: Any, code: int, present: bool) -> None:
    expect_lint_failure(tmp_path, fake_run, code, present)


def test_exit_one_with_a_report_is_accepted(tmp_path: Path, fake_run: Any) -> None:
    source = plant(tmp_path, "internal/store/store.go")
    wasted = "assigned to size, but reassigned without using the value\nmore"
    payload = {
        "Issues": [
            issue(str(source), 14, "wastedassign", wasted),
            issue(str(source), 14, "wastedassign", wasted),
            issue(str(source), 2, "typecheck", "cannot find"),
            issue("/elsewhere/z.go", 1, "unused", "z"),
            {"FromLinter": "govet", "Text": None, "Pos": {}},
            issue(str(tmp_path / "other.go"), 3, "errcheck", "err"),
        ]
    }

    def reply(command: list[str]) -> tuple[int, str]:
        if command[1] == "run":
            write_report(command, payload)
            return 1, ""
        return 0, f"{source}\n\n  {tmp_path / 'other.go'}\n"

    fake_run(go_lint, reply)
    scoped = ready(tmp_path, scope_changed=True, changed={"internal/store/store.go"})
    result = checked(go_lint.run_gate(scoped), GATE)
    assert result.findings == [
        "internal/store/store.go:14 wastedassign: assigned to size, but reassigned without using the value",
        "internal/store/store.go:2 typecheck: cannot find",
        "internal/store/store.go:1 not gofmt formatted; run gofmt -w internal/store/store.go",
    ]
    assert result.summary == "3 problems"


def test_null_issues_and_no_go_files(tmp_path: Path, fake_run: Any) -> None:
    ctx = ready(tmp_path)

    def reply(command: list[str]) -> tuple[int, str]:
        write_report(command, {"Issues": None})
        return 0, ""

    fake = fake_run(go_lint, reply)
    result = checked(go_lint.run_gate(ctx), GATE)
    assert (result.ok, result.summary) == (True, "golangci-lint and gofmt clean")
    assert len(fake.calls) == 1


def test_gofmt_failure(tmp_path: Path, fake_run: Any) -> None:
    plant(tmp_path, "a.go")
    ctx = ready(tmp_path)

    def reply(command: list[str]) -> tuple[int, str]:
        if command[1] == "run":
            write_report(command, {})
            return 0, ""
        return 2, "y" * 400

    fake_run(go_lint, reply)
    result = checked(go_lint.run_gate(ctx), GATE)
    assert (result.ok, result.summary, result.findings) == (False, "gofmt failed: " + "y" * 300, [])


def test_hyper_drops_file_level_gofmt(tmp_path: Path, fake_run: Any) -> None:
    source = plant(tmp_path, "internal/store/store.go")
    ctx = ready(
        tmp_path, scope_changed=True, hyper=True, changed={"internal/store/store.go"}, changed_lines_map={"internal/store/store.go": {15}}
    )

    def reply(command: list[str]) -> tuple[int, str]:
        if command[1] == "run":
            write_report(
                command, {"Issues": [issue(str(source), 14, "wastedassign", "assigned to size, but reassigned without using the value")]}
            )
            return 1, ""
        return 0, str(source) + "\n"

    fake_run(go_lint, reply)
    from marestail import gates

    gate = next(item for item in gates.registry() if item.name == GATE)
    result = checked(gates.run_one(gate, ctx), GATE)
    assert result.ok is True
    assert result.summary == "golangci-lint and gofmt clean; 1 file-level findings not gated under hyper"
    assert result.findings == []


def test_hyper_keeps_a_changed_line(tmp_path: Path, fake_run: Any) -> None:
    source = plant(tmp_path, "a.go")
    ctx = ready(tmp_path, scope_changed=True, hyper=True, changed={"a.go"}, changed_lines_map={"a.go": {15}})

    def reply(command: list[str]) -> tuple[int, str]:
        if command[1] == "run":
            write_report(command, {"Issues": [issue(str(source), 15, "staticcheck", "x")]})
            return 1, ""
        return 0, str(source) + "\n"

    fake_run(go_lint, reply)
    result = checked(go_lint.run_gate(ctx), GATE)
    assert result.findings == ["a.go:15 staticcheck: x"]
    assert ctx.file_level == 1


def test_findings_are_capped(tmp_path: Path, fake_run: Any) -> None:
    ctx = ready(tmp_path)
    issues = [issue(str(tmp_path / "a.go"), number, "staticcheck", "x") for number in range(1, 62)]

    def reply(command: list[str]) -> tuple[int, str]:
        write_report(command, {"Issues": issues})
        return 1, ""

    fake_run(go_lint, reply)
    result = checked(go_lint.run_gate(ctx), GATE)
    assert (result.summary, len(result.findings)) == ("61 problems", 60)


def test_helpers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = ready(tmp_path)
    assert go_lint.first_line(None) == ""
    assert go_lint.first_line("a\nb") == "a"
    found: list[str] = []
    go_lint.keep_issue(found, None)
    go_lint.keep_issue(found, "a.go:1 staticcheck: x")
    go_lint.keep_issue(found, "a.go:1 staticcheck: x")
    assert found == ["a.go:1 staticcheck: x"]
    assert go_lint.issue_line({}, ctx) is None
    assert go_lint.issue_line({"Text": "x", "Pos": {"Line": 1}}, ctx) is None
    monkeypatch.setattr(golang, "toolchain", lambda _ctx: "broken")
    assert go_lint.goroot(ctx) == ""
    monkeypatch.setattr(golang, "toolchain", lambda _ctx: {})
    assert go_lint.goroot(ctx) == ""
