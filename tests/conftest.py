import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

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
