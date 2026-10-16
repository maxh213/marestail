from collections.abc import Callable
from pathlib import Path

from marestail import worktree
from marestail.config import Config
from tests.conftest import FakeRun, commit_all, git


def test_start_commit_prefers_the_recorded_file(git_repo: Path) -> None:
    config = Config(git_repo, {"git": {"base": "main"}})
    (git_repo / "a").write_text("a")
    commit_all(git_repo)
    first = worktree.head(config)
    assert worktree.start_commit(config, "t") == (first, "no recorded start commit for t; using git merge-base main HEAD")
    worktree.start_file(config, "t").parent.mkdir(parents=True)
    worktree.start_file(config, "t").write_text("abc\n")
    assert worktree.start_commit(config, "t") == ("abc", "")


def test_start_commit_falls_back_to_head(git_repo: Path) -> None:
    config = Config(git_repo, {})
    commit_all(git_repo)
    assert worktree.start_commit(config, "t") == (
        git(git_repo, "rev-parse", "HEAD").strip(),
        "no recorded start commit for t and no merge-base with origin/master; using HEAD",
    )


def test_add_and_remove(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake = fake_run(worktree, [(0, "ok"), (0, "")])
    assert worktree.add(tmp_path, tmp_path / "wt", "abc") == (0, "ok")
    worktree.remove(tmp_path, tmp_path / "wt")
    assert fake.calls == [
        ["git", "worktree", "add", "--detach", str(tmp_path / "wt"), "abc"],
        ["git", "worktree", "remove", "--force", str(tmp_path / "wt")],
    ]
    assert [option["cwd"] for option in fake.options] == [tmp_path, tmp_path]
