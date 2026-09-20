import socket
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from coverage.files import GlobMatcher, prep_patterns

from marestail.config import Config
from marestail.context import Context

Reply = tuple[int, str]


class FakeRun:
    def __init__(self, replies: list[Reply] | Callable[[list[str]], Reply]) -> None:
        self.replies = replies
        self.calls: list[list[str]] = []
        self.options: list[dict[str, Any]] = []

    def __call__(self, command: list[str], cwd: Path, **options: Any) -> Reply:
        self.calls.append(list(command))
        self.options.append({"cwd": cwd, **options})
        if callable(self.replies):
            return self.replies(list(command))
        return self.replies.pop(0) if self.replies else (0, "")


@pytest.fixture
def fake_run(monkeypatch: pytest.MonkeyPatch) -> Callable[..., FakeRun]:
    def install(module: object, replies: list[Reply] | Callable[[list[str]], Reply] | None = None) -> FakeRun:
        fake = FakeRun([] if replies is None else replies)
        monkeypatch.setattr(module, "run", fake)
        return fake

    return install


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True).stdout


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "commit.gpgsign", "false")
    return root


def commit_all(root: Path, message: str = "commit") -> None:
    git(root, "add", "-A")
    git(root, "commit", "-q", "--allow-empty", "-m", message)


def make_context(root: Path, raw: dict[str, Any] | None = None, **fields: Any) -> Context:
    return Context(config=Config(root=root, raw=raw or {}), **fields)


FORBIDDEN_BINARIES = frozenset(
    {"docker", "claude", "grok", "kilo", "kimi", "cursor-agent", "agy", "dandelion", "sonar-scanner", "curl", "wget"}
)
TUI_OMIT = "tui"


def cover_tui_sources(config: pytest.Config) -> None:
    plugin = config.pluginmanager.getplugin("_cov")
    controller = getattr(plugin, "cov_controller", None)
    cov = getattr(controller, "cov", None)
    inorout = getattr(cov, "_inorout", None)
    if cov is None or inorout is None:
        return
    omit = [pattern for pattern in cov.config.run_omit if TUI_OMIT not in pattern.replace("\\", "/")]
    if omit == list(cov.config.run_omit):
        return
    cov.config.run_omit = omit
    inorout.omit = prep_patterns(omit)
    inorout.omit_match = GlobMatcher(inorout.omit, "omit", "Omit", inorout._debug) if inorout.omit else None


def pytest_configure(config: pytest.Config) -> None:
    cover_tui_sources(config)


class ForbiddenCallError(RuntimeError):
    pass


class GuardedPopen(subprocess.Popen[Any]):
    def __init__(self, args: Any, *rest: Any, **options: Any) -> None:
        program = args if isinstance(args, str) else args[0]
        if Path(str(program).split()[0]).name in FORBIDDEN_BINARIES:
            raise ForbiddenCallError(f"tests must fake {program}")
        super().__init__(args, *rest, **options)


def refuse_network(*args: Any, **options: Any) -> Any:
    raise ForbiddenCallError("tests must not use the network")


@pytest.fixture(autouse=True)
def hermetic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "Popen", GuardedPopen)
    monkeypatch.setattr(socket.socket, "connect", refuse_network)
    monkeypatch.setattr(socket, "create_connection", refuse_network)
