import ast
import os
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest

from marestail import nice
from marestail.config import Config

ROOT = Path(__file__).resolve().parent.parent
REAL_SCORE_PATH = nice._score_path
PARAGRAPH = (
    "`marestail run` starts at nice 19 with idle I/O and a raised OOM score, so a fleet of pipelines "
    "yields the CPU and disk to you and is first in line if the kernel needs memory. Children inherit. "
    "`[run] nice = 0` or `MARESTAIL_NICE=0` turns it off; any other integer 1\u201319 is the level "
    "(`MARESTAIL_NICE` wins over the file). `marestail watch` and `marestail gate` stay at the shell's priority."
)
ROW = (
    "| `MARESTAIL_NICE` | priority for `marestail run`: 0 turns it off, 1\u201319 is the nice level (default 19); wins over `[run] nice` |"
)
NUMERIC: list[tuple[str | None, dict[str, Any] | None, int]] = [
    (None, None, 19),
    (None, {}, 19),
    (None, {"run": {}}, 19),
    (None, {"run": {"nice": True}}, 19),
    (None, {"run": {"nice": 1}}, 1),
    (None, {"run": {"nice": 5}}, 5),
    (None, {"run": {"nice": 10}}, 10),
    (None, {"run": {"nice": "10"}}, 10),
    (None, {"run": {"nice": 19}}, 19),
    (None, {"run": {"nice": 20}}, 19),
    (None, {"run": {"nice": 99}}, 19),
    ("7", {"run": {"nice": 19}}, 7),
    ("99", {"run": {"nice": 5}}, 19),
]
OFF: list[tuple[str | None, dict[str, Any]]] = [
    (None, {"run": {"nice": False}}),
    (None, {"run": {"nice": 0}}),
    (None, {"run": {"nice": "0"}}),
    (None, {"run": {"nice": ""}}),
    (None, {"run": {"nice": None}}),
    ("0", {"run": {"nice": 19}}),
    ("", {"run": {"nice": 19}}),
]
ACCEPTED: list[tuple[object, int | None]] = [
    (True, 19),
    (False, None),
    (None, None),
    ("", None),
    (0, None),
    ("0", None),
    (10, 10),
    ("10", 10),
    (99, 99),
    ("99", 99),
]
REJECTED: list[tuple[object, str]] = [
    ("high", "nice must be an integer 0-19, got 'high'"),
    (-1, "nice must be an integer 0-19, got -1"),
    ("-4", "nice must be an integer 0-19, got -4"),
    ("1.5", "nice must be an integer 0-19, got '1.5'"),
    (1.5, "nice must be an integer 0-19, got 1.5"),
    ("true", "nice must be an integer 0-19, got 'true'"),
    ("false", "nice must be an integer 0-19, got 'false'"),
]


class Effects:
    def __init__(self) -> None:
        self.priorities: list[tuple[object, ...]] = []
        self.commands: list[tuple[list[str], bool, bool]] = []
        self.writes: list[tuple[str, str]] = []

    def priority(self, error: BaseException | None) -> Callable[..., None]:
        def setpriority(*args: object) -> None:
            self.priorities.append(args)
            if error is not None:
                raise error

        return setpriority

    def command(self, error: BaseException | None, code: int) -> Callable[..., subprocess.CompletedProcess[str]]:
        def run(cmd: Sequence[str], check: bool = False, capture_output: bool = False) -> subprocess.CompletedProcess[str]:
            listed = [str(part) for part in cmd]
            self.commands.append((listed, check, capture_output))
            refuse_checked(error, check, code, listed)
            return subprocess.CompletedProcess(listed, code)

        return run

    def writer(self, folder: Path, oom_error: bool) -> Callable[..., int]:
        original = Path.write_text

        def write_text(
            path: Path,
            data: str,
            encoding: str | None = None,
            errors: str | None = None,
            newline: str | None = None,
        ) -> int:
            if path.name != "oom_score_adj":
                return original(path, data, encoding, errors, newline)
            self.writes.append((str(path), data))
            if oom_error:
                raise OSError("oom")
            return plant(folder, data)

        return write_text


def refuse_checked(error: BaseException | None, check: bool, code: int, cmd: list[str]) -> None:
    if error is not None:
        raise error
    if check and code != 0:
        raise subprocess.CalledProcessError(code, cmd)


def plant(folder: Path, data: str) -> int:
    with (folder / "oom_score_adj").open("w", encoding="utf-8", newline="") as handle:
        return handle.write(data)


def observe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    priority_error: BaseException | None = None,
    ionice_error: BaseException | None = None,
    ionice_code: int = 0,
    oom_error: bool = False,
) -> Effects:
    seen = Effects()
    monkeypatch.setattr(os, "setpriority", seen.priority(priority_error))
    monkeypatch.setattr(subprocess, "run", seen.command(ionice_error, ionice_code))
    monkeypatch.setattr(nice, "_score_path", REAL_SCORE_PATH)
    monkeypatch.setattr(Path, "write_text", seen.writer(tmp_path, oom_error))
    return seen


def configured(tmp_path: Path, raw: dict[str, Any] | None) -> Config | None:
    if raw is None:
        return None
    return Config(root=tmp_path, raw=raw)


def use_env(monkeypatch: pytest.MonkeyPatch, env: str | None) -> None:
    if env is not None:
        monkeypatch.setenv(nice.ENV, env)


def assert_applied(seen: Effects, tmp_path: Path, level: int) -> None:
    pid = os.getpid()
    assert seen.priorities == [(os.PRIO_PROCESS, 0, level)]
    assert seen.commands == [(["ionice", "-c", "3", "-p", str(pid)], False, True)]
    assert seen.writes == [(f"/proc/{pid}/oom_score_adj", "500")]
    assert (tmp_path / "oom_score_adj").read_bytes() == b"500"


def assert_idle(seen: Effects, tmp_path: Path) -> None:
    assert seen.priorities == []
    assert seen.commands == []
    assert seen.writes == []
    assert not (tmp_path / "oom_score_adj").exists()


@pytest.fixture(autouse=True)
def keep_the_process(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv(nice.ENV, raising=False)
    monkeypatch.setattr(os, "setpriority", lambda *args, **kwargs: None)
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess([], 0))
    monkeypatch.setattr(nice, "_score_path", lambda: tmp_path / "safety-oom")


@pytest.mark.parametrize(("raw", "expected"), ACCEPTED)
def test_parse_accepts(raw: object, expected: int | None) -> None:
    assert nice.parse(raw) == expected


@pytest.mark.parametrize(("raw", "message"), REJECTED)
def test_parse_rejects(raw: object, message: str) -> None:
    with pytest.raises(SystemExit) as raised:
        nice.parse(raw)
    assert str(raised.value) == message


@pytest.mark.parametrize(("env", "raw", "expected"), NUMERIC)
def test_level_caps_at_nineteen(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, env: str | None, raw: dict[str, Any] | None, expected: int
) -> None:
    use_env(monkeypatch, env)
    assert nice.level(configured(tmp_path, raw)) == expected


@pytest.mark.parametrize(("env", "raw"), OFF)
def test_level_is_off(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, env: str | None, raw: dict[str, Any]) -> None:
    use_env(monkeypatch, env)
    assert nice.level(configured(tmp_path, raw)) is None


def test_level_rejects_a_bad_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(nice.ENV, "high")
    config = Config(root=tmp_path, raw={"run": {"nice": 19}})
    with pytest.raises(SystemExit) as raised:
        nice.level(config)
    assert str(raised.value) == "nice must be an integer 0-19, got 'high'"


@pytest.mark.parametrize(("env", "raw", "expected"), NUMERIC)
def test_apply_sets_priority_io_and_oom(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    env: str | None,
    raw: dict[str, Any] | None,
    expected: int,
) -> None:
    use_env(monkeypatch, env)
    seen = observe(monkeypatch, tmp_path)
    nice.apply(configured(tmp_path, raw))
    assert_applied(seen, tmp_path, expected)
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize(("env", "raw"), OFF)
def test_apply_skips_when_off(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str], env: str | None, raw: dict[str, Any]
) -> None:
    use_env(monkeypatch, env)
    seen = observe(monkeypatch, tmp_path)
    nice.apply(configured(tmp_path, raw))
    assert_idle(seen, tmp_path)
    assert capsys.readouterr() == ("", "")


def test_apply_ignores_a_failing_ionice(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen = observe(monkeypatch, tmp_path, ionice_code=3)
    nice.apply(Config(root=tmp_path, raw={}))
    assert_applied(seen, tmp_path, 19)


def test_apply_swallows_setpriority_oserror(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen = observe(monkeypatch, tmp_path, priority_error=OSError("prio"))
    nice.apply(Config(root=tmp_path, raw={}))
    assert_applied(seen, tmp_path, 19)


def test_apply_swallows_setpriority_attribute_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen = observe(monkeypatch, tmp_path, priority_error=AttributeError("prio"))
    nice.apply(Config(root=tmp_path, raw={}))
    assert_applied(seen, tmp_path, 19)


def test_apply_swallows_a_missing_ionice(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen = observe(monkeypatch, tmp_path, ionice_error=OSError("missing"))
    nice.apply(Config(root=tmp_path, raw={}))
    assert_applied(seen, tmp_path, 19)


def test_apply_swallows_an_oom_write_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen = observe(monkeypatch, tmp_path, oom_error=True)
    nice.apply(Config(root=tmp_path, raw={}))
    pid = os.getpid()
    assert seen.priorities == [(os.PRIO_PROCESS, 0, 19)]
    assert seen.commands == [(["ionice", "-c", "3", "-p", str(pid)], False, True)]
    assert seen.writes == [(f"/proc/{pid}/oom_score_adj", "500")]
    assert not (tmp_path / "oom_score_adj").exists()


def test_oom_score_path_is_the_proc_file() -> None:
    assert REAL_SCORE_PATH() == Path(f"/proc/{os.getpid()}/oom_score_adj")
    assert (nice.DEFAULT, nice.OOM_SCORE, nice.ENV) == (19, 500, "MARESTAIL_NICE")


def test_readme_documents_run_priority() -> None:
    text = (ROOT / "README.md").read_text()
    after = text.split("## Use", 1)[1].split("```", 2)[2]
    table = text.split("## Environment variables", 1)[1].split("## ", 1)[0]
    assert after.lstrip("\n").startswith(PARAGRAPH)
    assert ROW in table
    assert "\u2013" in PARAGRAPH
    assert "\u2013" in ROW


def test_nice_is_listed_with_ran_against() -> None:
    line = next(line for line in (ROOT / ".importlinter").read_text().splitlines() if "marestail.ran_against" in line)
    assert "marestail.nice" in [part.strip() for part in line.split(":")]


def test_nice_imports_only_config() -> None:
    assert imported_marestail(ROOT / "marestail" / "nice.py") == ["marestail.config"]
    assert not (ROOT / "tools" / "test-nice.py").exists()


def imported_marestail(path: Path) -> list[str]:
    found = [module for node in ast.walk(ast.parse(path.read_text())) for module in modules_of(node)]
    return sorted(module for module in found if module.startswith("marestail"))


def modules_of(node: ast.AST) -> list[str]:
    if isinstance(node, ast.ImportFrom):
        return from_module(node)
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    return []


def from_module(node: ast.ImportFrom) -> list[str]:
    if node.module is None:
        return []
    return [node.module]
