import subprocess
from pathlib import Path
from typing import Any

import pytest

from marestail import route
from marestail.route import Choice


class FakeProcess:
    def __init__(self, result: subprocess.CompletedProcess[str] | Exception) -> None:
        self.result = result
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(self, command: list[str], **options: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append((command, options))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.fixture
def dandelion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    binary = tmp_path / "dandelion"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    monkeypatch.setenv("MARESTAIL_DANDELION", str(binary))
    return str(binary)


@pytest.fixture
def missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> str:
    binary = str(tmp_path / "nowhere" / "dandelion")
    monkeypatch.setenv("MARESTAIL_DANDELION", binary)
    return binary


def fake_process(monkeypatch: pytest.MonkeyPatch, result: subprocess.CompletedProcess[str] | Exception) -> FakeProcess:
    fake = FakeProcess(result)
    monkeypatch.setattr(subprocess, "run", fake)
    return fake


def completed(code: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], code, stdout, stderr)


@pytest.mark.parametrize(
    ("model", "expected"), [("dandelion/route", True), ("dandelion/route-best", True), ("claude-opus-5", False), (None, False)]
)
def test_is_routed(model: str | None, expected: bool) -> None:
    assert route.is_routed(model) is expected


def test_binary_defaults_to_dandelion(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MARESTAIL_DANDELION", raising=False)
    assert route.binary() == "dandelion"


def test_installed(dandelion: str) -> None:
    assert route.binary() == dandelion
    assert route.installed() is True


def test_install_hint(missing: str) -> None:
    assert route.installed() is False
    assert route.install_hint() == (
        f"dandelion is not installed: no `{missing}` on PATH.\n"
        "marestail route and the dandelion/route models need it to pick a subscription with quota left. "
        "Install it from https://github.com/maxh213/dandelion:\n"
        "  git clone https://github.com/maxh213/dandelion.git\n"
        "  cd dandelion && npm ci && npm link\n"
        "It needs a Node.js that runs TypeScript directly (22.18 or newer).\n"
    )


def test_require_raises_the_hint(missing: str) -> None:
    with pytest.raises(SystemExit) as raised:
        route.require()
    assert str(raised.value) == route.install_hint().rstrip()


def test_require_passes_when_installed(dandelion: str) -> None:
    route.require()


@pytest.mark.parametrize("args", [["--high"], ["--help"]])
def test_command_without_dandelion(missing: str, capsys: pytest.CaptureFixture[str], args: list[str]) -> None:
    assert route.command(args) == 127
    assert capsys.readouterr().err == route.install_hint()


def test_command_forwards_arguments(dandelion: str, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = fake_process(monkeypatch, completed(3))
    assert route.command(["--high", "x"]) == 3
    assert fake.calls == [([dandelion, "route", "--high", "x"], {"check": False})]


@pytest.mark.parametrize(("model", "extra"), [("dandelion/route", []), ("dandelion/route-best", ["--high"])])
def test_choose_parses_the_last_line(dandelion: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, model: str, extra: list[str]) -> None:
    fake = fake_process(monkeypatch, completed(0, "noise\n  claude-opus-5 high claude  \n\n"))
    assert route.choose(model, tmp_path) == (Choice("claude-opus-5 high claude", "claude", "claude-opus-5", "high", {}), "")
    options = {"cwd": tmp_path, "capture_output": True, "text": True, "timeout": 300, "check": False}
    assert fake.calls == [([dandelion, "route", *extra], options)]


def test_choose_without_dandelion(missing: str, tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="dandelion is not installed"):
        route.choose("dandelion/route", tmp_path)


def test_choose_timeout(dandelion: str, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_process(monkeypatch, subprocess.TimeoutExpired("dandelion", 300))
    assert route.choose("dandelion/route", Path()) == (None, "dandelion route timed out after 300s")


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (completed(0, "x\nnone\n"), (None, "no subscription has quota left")),
        (completed(1, "none"), (None, "no subscription has quota left")),
        (completed(3, "", " boom \n"), (None, "dandelion route failed with exit 3: boom")),
        (completed(2, "gpt claude\n"), (None, "dandelion route failed with exit 2: gpt claude")),
        (completed(0, "  \n"), (None, "dandelion route failed with exit 0: no output")),
        (completed(4, "", "e" * 250), (None, "dandelion route failed with exit 4: " + "e" * 200)),
        (completed(0, "gpt-6 kimi"), (Choice("gpt-6 kimi", "kimi", "gpt-6", None, {}), "")),
    ],
)
def test_interpret(result: subprocess.CompletedProcess[str], expected: tuple[Choice | None, str]) -> None:
    assert route.interpret(result) == expected


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("m claude", Choice("m claude", "claude", "m", None, {})),
        ("m low agy", Choice("m low agy", "agy", "m", "low", {})),
        ("m grok", Choice("m grok", "grok", "m", None, {})),
        ("m cursor", Choice("m cursor", "cursor", "m", None, {})),
        ("gemini-3.8-flash high junie", Choice("gemini-3.8-flash high junie", "junie", "gemini-3.8-flash", "high", {})),
        ("gemini-3.8-flash junie", Choice("gemini-3.8-flash junie", "junie", "gemini-3.8-flash", None, {})),
    ],
)
def test_parse(line: str, expected: Choice) -> None:
    assert route.parse(line) == expected


def test_parse_claude_work(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DANDELION_CLAUDE_WORK_CONFIG_DIR", "/cfg/work")
    assert route.parse("m high claude-work") == Choice("m high claude-work", "claude", "m", "high", {"CLAUDE_CONFIG_DIR": "/cfg/work"})


@pytest.mark.parametrize(
    ("line", "message"),
    [
        ("claude", "dandelion route printed 'claude'; expected `<model> [effort] <provider>`"),
        ("a b c claude", "dandelion route printed 'a b c claude'; expected `<model> [effort] <provider>`"),
        (
            "gpt high codex",
            "dandelion route picked 'codex', which marestail has no backend for; it knows claude, claude-work, agy, kimi, grok, cursor, junie",
        ),
    ],
)
def test_parse_rejects(line: str, message: str) -> None:
    with pytest.raises(SystemExit) as raised:
        route.parse(line)
    assert str(raised.value) == message


def test_account_env_default_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("DANDELION_CLAUDE_WORK_CONFIG_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert route.account_env("claude-work") == {"CLAUDE_CONFIG_DIR": str(tmp_path / ".claude-work")}
    assert route.account_env("claude") == {}


def test_account_env_empty_setting_uses_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DANDELION_CLAUDE_WORK_CONFIG_DIR", "")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert route.account_env("claude-work") == {"CLAUDE_CONFIG_DIR": str(tmp_path / ".claude-work")}


def test_last_line() -> None:
    assert route.last_line(" a \n\n b \n  ") == "b"
    assert route.last_line("") == ""


def test_failure_detail_prefers_stderr() -> None:
    assert route.failure_detail(" err ", "line") == "err"
    assert route.failure_detail("  ", "line") == "line"
    assert route.failure_detail("", "") == "no output"
