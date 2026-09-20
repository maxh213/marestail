import socket
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from marestail.config import Config
from marestail.context import Context
from marestail.report import Result

Reply = tuple[int, str]
MUTMUT_COPIES = (
    "templates",
    "roles",
    "guidance",
    "features",
    "README.md",
    "marestail.toml",
    "sonar-project.properties",
    ".importlinter",
    "CLAUDE.md",
    "AGENTS.md",
    "tools",
    "bin",
)


class Clock:
    def __init__(self, start: float = 1000.0, step: float = 0.25) -> None:
        self.next = start
        self.step = step

    def __call__(self) -> float:
        now = self.next
        self.next += self.step
        return now


def gate_shape(result: Result) -> tuple[str, bool, str, list[str]]:
    assert isinstance(result.gate, str) and result.gate
    assert result.seconds is not None
    assert result.seconds >= 0
    return result.gate, result.ok, result.summary, result.findings


class FakeRun:
    def __init__(self, replies: list[Reply] | Callable[[list[str]], Reply]) -> None:
        self.replies = list(replies) if isinstance(replies, list) else replies
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


@pytest.fixture(autouse=True)
def isolate_erlang_host(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("marestail.erlang.host_checks", {})
    if getattr(request.module, "KEEP_ERLANG_HOST", False):
        return
    monkeypatch.setattr("marestail.erlang.host_erlang", lambda _ctx: True)


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True).stdout


def git_try(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=False)


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


def git_toplevel() -> Path | None:
    completed = git_try("rev-parse", "--show-toplevel")
    if completed.returncode != 0:
        return None
    return Path(completed.stdout.strip())


def link_from_root(root: Path, here: Path, name: str) -> None:
    source = root / name
    destination = here / name
    if destination.exists() or not source.exists():
        return
    destination.symlink_to(source)


def populate_mutmut_tree(here: Path) -> None:
    if here.name != "mutants":
        return
    root = git_toplevel()
    if root is None or root == here:
        return
    for name in MUTMUT_COPIES:
        link_from_root(root, here, name)


def pytest_configure(config: pytest.Config) -> None:
    populate_mutmut_tree(Path.cwd())


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
