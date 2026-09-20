from collections.abc import Callable
from pathlib import Path

import pytest

from marestail import changes
from tests.conftest import FakeRun, commit_all, git


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (" M a.py", "a.py"),
        ("?? new dir/b.py", "new dir/b.py"),
        ("R  old.py -> new.py", "new.py"),
        ("plain.py", "plain.py"),
        ("  spaced.py  ", "spaced.py"),
        ("abc d", "abc d"),
        ("M ", "M"),
        ("MM", "MM"),
        ("M  ", "M"),
        ("XY a", "a"),
        ("XY  ", " "),
        ("  a.py", "a.py"),
    ],
)
def test_parse_line(line: str, expected: str) -> None:
    assert changes.parse_line(line) == expected


def test_porcelain_path_rejects_short_and_unstaged_lines() -> None:
    assert changes.porcelain_path("M") is None
    assert changes.porcelain_path("M  ") is None
    assert changes.porcelain_path("MMa.py") is None
    assert changes.porcelain_path("M a.py") is None
    assert changes.porcelain_path(" M a.py") == "a.py"
    assert (changes.STATUS_WIDTH, changes.PATH_START, changes.RENAME_ARROW) == (2, 3, " -> ")


@pytest.mark.parametrize(("line", "expected"), [("+++ b/a.py", "a.py"), ("+++ /dev/null", None), ("+++ a.py ", "a.py")])
def test_parse_plus(line: str, expected: str | None) -> None:
    assert changes.parse_plus(line) == expected


@pytest.mark.parametrize(
    ("header", "expected"),
    [("@@ -1,2 +3,4 @@", (3, 4)), ("@@ -1 +5 @@ def f():", (5, 1)), ("@@ -3,2 +2,0 @@", (2, 0))],
)
def test_hunk_span(header: str, expected: tuple[int, int]) -> None:
    assert changes.hunk_span(header) == expected


def test_hunks_of() -> None:
    diff = "\n".join(
        [
            "@@ -1 +1 @@",
            "diff --git a/x.py b/x.py",
            "--- a/x.py",
            "+++ b/x.py",
            "@@ -1,0 +2,2 @@",
            "+++ plus line",
            "@@ -9 +9 @@",
            "+++ /dev/null",
            "@@ -1 +0,0 @@",
            "+++ b/y.py",
            "+added",
            "@@ -4 +4 @@",
        ]
    )
    assert changes.hunks_of(diff) == {"x.py": {2, 3}, "plus line": {9}, "y.py": {4}}


def test_merge_hunks() -> None:
    lines = {"a": {1}}
    changes.merge_hunks(lines, {"a": {2}, "b": {3}})
    assert lines == {"a": {1, 2}, "b": {3}}


def test_failures_give_empty_results(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake = fake_run(changes, lambda command: (1, "?? a.py\n+++ b/a.py\n@@ -1 +1 @@"))
    assert changes.diff_names(tmp_path, ["git", "x"]) == set()
    assert changes.diff_hunks(tmp_path, ["git", "y"]) == {}
    assert changes.untracked(tmp_path) == set()
    assert changes.base_exists(tmp_path, "main") is False
    assert fake.calls[-1] == ["git", "rev-parse", "--verify", "--quiet", "main"]
    assert [option["cwd"] for option in fake.options] == [tmp_path] * len(fake.options)


def test_changed_files_commands(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake = fake_run(changes, [(0, "a.py\n\nb.py\n"), (0, " M c.py\n?? d.py\n")])
    assert changes.changed_files(tmp_path, "origin/x") == {"a.py", "b.py", "c.py", "d.py"}
    assert fake.calls == [
        ["git", "diff", "--name-only", "origin/x...HEAD"],
        ["git", "status", "--porcelain", "--untracked-files=all"],
    ]


def test_changed_lines_commands(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake = fake_run(changes, [(0, "+++ b/a.py\n@@ -1 +1,2 @@"), (0, "+++ b/a.py\n@@ -5 +5 @@"), (0, " M a.py\n?? gone.py\n")])
    assert changes.changed_lines(tmp_path, "base") == {"a.py": {1, 2, 5}}
    assert fake.calls == [
        ["git", "diff", "-U0", "base...HEAD"],
        ["git", "diff", "-U0", "HEAD"],
        ["git", "status", "--porcelain", "--untracked-files=all"],
    ]


def repo_with_changes(root: Path) -> None:
    (root / "kept.py").write_text("a\nb\nc\n")
    (root / "edited.py").write_text("1\n2\n3\n")
    commit_all(root, "base")
    git(root, "checkout", "-q", "-b", "work")
    (root / "kept.py").write_text("a\nB\nc\nd\n")
    commit_all(root, "branch")
    (root / "edited.py").write_text("1\n2\nthree\n")
    (root / "new.txt").write_text("x\ny\n")
    (root / "blob.bin").write_bytes(b"a\0b")


def test_changed_files_real_git(git_repo: Path) -> None:
    repo_with_changes(git_repo)
    assert changes.changed_files(git_repo, "main") == {"kept.py", "edited.py", "new.txt", "blob.bin"}
    assert changes.base_exists(git_repo, "main") is True
    assert changes.base_exists(git_repo, "origin/nope") is False


def test_changed_lines_real_git(git_repo: Path) -> None:
    repo_with_changes(git_repo)
    assert changes.changed_lines(git_repo, "main") == {"kept.py": {2, 4}, "edited.py": {3}, "new.txt": {1, 2}}


def test_untracked_real_git(git_repo: Path) -> None:
    repo_with_changes(git_repo)
    assert changes.untracked(git_repo) == {"new.txt", "blob.bin"}


def test_file_lines(tmp_path: Path) -> None:
    (tmp_path / "text").write_bytes(b"one\ntwo\r\nthree")
    (tmp_path / "binary").write_bytes(b"\0")
    (tmp_path / "latin").write_bytes(b"\xff\n\xfe")
    (tmp_path / "empty").write_bytes(b"")
    assert changes.file_lines(tmp_path / "text") == {1, 2, 3}
    assert changes.file_lines(tmp_path / "binary") is None
    assert changes.file_lines(tmp_path / "latin") == {1, 2}
    assert changes.file_lines(tmp_path / "empty") == set()
    assert changes.file_lines(tmp_path / "missing") is None
    assert changes.file_lines(tmp_path) is None
