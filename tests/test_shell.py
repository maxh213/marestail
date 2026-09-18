import subprocess
from pathlib import Path
from typing import Any

import pytest

from marestail import shell


def test_run_merges_output_and_strips_ansi_and_carriage_returns(tmp_path: Path) -> None:
    command = ["sh", "-c", "printf 'a\\033[31mb\\033[0m\\r\\n'; printf 'err\\n' >&2; exit 3"]
    assert shell.run(command, cwd=tmp_path) == (3, "ab\nerr\n")


def test_run_uses_cwd(tmp_path: Path) -> None:
    (tmp_path / "marker").write_text("")
    assert shell.run(["sh", "-c", "test -f marker"], cwd=tmp_path) == (0, "")


def test_run_passes_stdin(tmp_path: Path) -> None:
    assert shell.run(["sh", "-c", "read line; printf '%s!' \"$line\""], cwd=tmp_path, stdin="hi\n") == (0, "hi!")


def test_run_merges_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KEEP", "kept")
    monkeypatch.setenv("SWAP", "old")
    command = ["sh", "-c", 'printf \'%s %s %s\' "$KEEP" "$SWAP" "$ADD"']
    assert shell.run(command, cwd=tmp_path, env={"SWAP": "new", "ADD": "added"}) == (0, "kept new added")


def test_run_reports_missing_binary(tmp_path: Path) -> None:
    code, output = shell.run(["no-such-binary-xyz", "arg"], cwd=tmp_path)
    assert (code, output) == (127, "no-such-binary-xyz: not found ([Errno 2] No such file or directory: 'no-such-binary-xyz')")


def test_run_reports_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def expire(command: list[str], **options: Any) -> None:
        seen.update(options)
        raise subprocess.TimeoutExpired(command, options["timeout"])

    monkeypatch.setattr(shell.subprocess, "run", expire)
    assert shell.run(["sh", "-c", "x"], cwd=tmp_path, timeout=7) == (124, "sh -c x: timed out after 7s")
    assert (seen["timeout"], seen["cwd"], seen["input"], seen["check"]) == (7, tmp_path, None, False)


def test_run_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def record(command: list[str], **options: Any) -> subprocess.CompletedProcess[str]:
        seen.update(options)
        return subprocess.CompletedProcess(command, 0, "out", "err")

    monkeypatch.setattr(shell.subprocess, "run", record)
    monkeypatch.setenv("FROM_OS", "1")
    assert shell.run(["x"], cwd=tmp_path) == (0, "outerr")
    assert (seen["timeout"], seen["capture_output"], seen["text"], seen["env"]["FROM_OS"]) == (3600, True, True, "1")


def test_run_real_timeout(tmp_path: Path) -> None:
    code, output = shell.run(["sh", "-c", "while :; do :; done"], cwd=tmp_path, timeout=0)
    assert (code, output) == (124, "sh -c while :; do :; done: timed out after 0s")


@pytest.mark.parametrize(
    ("text", "expected"),
    [("\x1b[1;32mok\x1b[0m\r\n", "ok\n"), ("\x1b[?25lhidden\x1b[?25h", "hidden"), ("plain", "plain")],
)
def test_clean(text: str, expected: str) -> None:
    assert shell.clean(text) == expected


def test_tail_drops_blank_lines_and_keeps_last() -> None:
    text = "\n".join(str(number) for number in range(40)) + "\n  \n"
    assert shell.tail(text) == [str(number) for number in range(10, 40)]
    assert shell.tail("a\n\nb\n c", 2) == ["b", " c"]
