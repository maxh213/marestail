import json
from pathlib import Path
from typing import Any

import pytest

from marestail import rust
from marestail.gates import rs_lint
from tests.conftest import make_context

CLIPPY = ["cargo", "clippy", "--all-targets", "--message-format=json", "--", "-D", "warnings", "-D", "clippy::pedantic"]
FMT = ["cargo", "fmt", "--check", "--message-format", "short"]


@pytest.fixture(autouse=True)
def no_llvm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rust, "env", lambda ctx: {})


def message(file: str, line: int, text: str, level: str = "warning", code: str | None = "clippy::needless_return") -> str:
    spans = [
        {"file_name": "src/other.rs", "line_start": 99, "is_primary": False},
        {"file_name": file, "line_start": line, "is_primary": True},
    ]
    body = {"message": text, "level": level, "code": {"code": code} if code else None, "spans": spans}
    return json.dumps({"reason": "compiler-message", "package_id": "demo", "message": body})


def clippy_output(root: Path) -> str:
    return "\n".join(
        [
            json.dumps({"reason": "compiler-artifact", "target": {}}),
            message("src/lib.rs", 3, "unneeded `return` statement"),
            message("src/lib.rs", 3, "unneeded `return` statement"),
            message(str(root / "src" / "main.rs"), 7, "mismatched types", "error", None),
            message("src/lib.rs", 1, "note only", "note"),
            json.dumps({"reason": "compiler-message", "message": {"level": "warning", "message": "no span", "spans": []}}),
            json.dumps({"reason": "compiler-message", "message": None}),
            "{not json",
            "   Compiling demo v0.1.0",
            json.dumps({"reason": "build-finished", "success": False}),
        ]
    )


def test_skips_without_rust_changes(tmp_path: Path, fake_run: Any) -> None:
    fake = fake_run(rust)
    result = rs_lint.run_gate(make_context(tmp_path, scope_changed=True, changed={"README.md"}))
    assert (result.gate, result.ok, result.summary) == ("rs.lint", True, "skipped: no changed rust files")
    assert fake.calls == []


def test_clippy_missing(tmp_path: Path, fake_run: Any) -> None:
    fake = fake_run(rust, [(101, "error: no such command: `clippy`")])
    result = rs_lint.run_gate(make_context(tmp_path, scope_changed=True, changed={"Cargo.toml"}))
    assert (result.ok, result.summary, result.findings) == (
        False,
        "clippy missing",
        [f"cargo clippy is not installed: {rust.INSTALL['clippy']}"],
    )
    assert fake.calls == [CLIPPY]
    assert fake.options == [{"cwd": tmp_path, "env": {}, "timeout": 1800}]


def test_reports_clippy_and_fmt(tmp_path: Path, fake_run: Any) -> None:
    fake = fake_run(rust, [(101, clippy_output(tmp_path)), (1, f"{tmp_path}/src/lib.rs\nsrc/main.rs\nDiff in x\n")])
    result = rs_lint.run_gate(make_context(tmp_path, {"rust": {"clippy_args": "-Dwarnings"}}))
    assert fake.calls == [[*CLIPPY[:5], "-Dwarnings"], FMT]
    assert fake.options[1] == {"cwd": tmp_path, "env": {}, "timeout": 300}
    assert (result.ok, result.summary) == (False, "4 problems")
    assert result.findings == [
        "src/lib.rs:3 clippy::needless_return: unneeded `return` statement",
        "src/main.rs:7 error: mismatched types",
        "src/lib.rs:1 not rustfmt formatted; run cargo fmt",
        "src/main.rs:1 not rustfmt formatted; run cargo fmt",
    ]


def test_clean(tmp_path: Path, fake_run: Any) -> None:
    fake_run(rust, [(0, ""), (0, "")])
    result = rs_lint.run_gate(make_context(tmp_path))
    assert (result.ok, result.summary, result.findings) == (True, "clippy and rustfmt clean", [])


def test_clippy_failure_without_messages(tmp_path: Path, fake_run: Any) -> None:
    fake_run(rust, [(101, "error: could not compile\n"), (127, "")])
    result = rs_lint.run_gate(make_context(tmp_path))
    assert result.findings == ["cargo clippy failed: error: could not compile", f"cargo is not installed: {rust.INSTALL['cargo']}"]


def test_fmt_failure_without_paths(tmp_path: Path, fake_run: Any) -> None:
    fake_run(rust, [(1, "Error: rustfmt crashed\n")])
    assert rs_lint.format_findings(make_context(tmp_path)) == ["cargo fmt --check failed: Error: rustfmt crashed"]


def test_scoped_findings(tmp_path: Path, fake_run: Any) -> None:
    ctx = make_context(tmp_path, scope_changed=True, changed={"src/main.rs"})
    assert rs_lint.clippy_findings(clippy_output(tmp_path), ctx) == ["src/main.rs:7 error: mismatched types"]
    fake_run(rust, [(1, "src/lib.rs\nsrc/main.rs\n")])
    assert rs_lint.format_findings(ctx) == ["src/main.rs:1 not rustfmt formatted; run cargo fmt"]


def test_capped_at_max_lines(tmp_path: Path, fake_run: Any) -> None:
    output = "\n".join(message("src/lib.rs", n, "w") for n in range(1, 71))
    fake_run(rust, [(0, output), (0, "")])
    result = rs_lint.run_gate(make_context(tmp_path))
    assert (result.summary, len(result.findings)) == ("70 problems", 60)
