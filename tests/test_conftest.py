from pathlib import Path
from typing import cast

import pytest

from tests import conftest


def test_git_toplevel_is_this_repo() -> None:
    root = conftest.git_toplevel()
    assert root is not None
    assert (root / "marestail.toml").is_file()


def test_git_toplevel_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(conftest.subprocess, "run", lambda *args, **kwargs: type("R", (), {"returncode": 1, "stdout": ""})())
    assert conftest.git_toplevel() is None


def test_link_from_root_skips_missing_and_existing(tmp_path: Path) -> None:
    root = tmp_path / "root"
    here = tmp_path / "here"
    root.mkdir()
    here.mkdir()
    (root / "README.md").write_text("ok")
    (here / "keep").write_text("old")
    conftest.link_from_root(root, here, "missing")
    conftest.link_from_root(root, here, "keep")
    conftest.link_from_root(root, here, "README.md")
    assert not (here / "missing").exists()
    assert (here / "keep").read_text() == "old"
    assert (here / "README.md").resolve() == (root / "README.md").resolve()


def test_populate_mutmut_tree_ignores_other_directories(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(conftest, "git_toplevel", lambda: tmp_path)
    conftest.populate_mutmut_tree(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_populate_mutmut_tree_skips_when_cwd_is_the_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = tmp_path / "mutants"
    folder.mkdir()
    monkeypatch.setattr(conftest, "git_toplevel", lambda: folder)
    conftest.populate_mutmut_tree(folder)
    assert list(folder.iterdir()) == []


def test_populate_mutmut_tree_skips_without_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = tmp_path / "mutants"
    folder.mkdir()
    monkeypatch.setattr(conftest, "git_toplevel", lambda: None)
    conftest.populate_mutmut_tree(folder)
    assert list(folder.iterdir()) == []


def test_populate_mutmut_tree_links_copies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "repo"
    here = tmp_path / "mutants"
    root.mkdir()
    here.mkdir()
    (root / "templates").mkdir()
    (root / "README.md").write_text("docs")
    monkeypatch.setattr(conftest, "git_toplevel", lambda: root)
    monkeypatch.setattr(conftest, "MUTMUT_COPIES", ("templates", "README.md", "missing"))
    conftest.populate_mutmut_tree(here)
    assert (here / "templates").is_symlink()
    assert (here / "README.md").read_text() == "docs"
    assert not (here / "missing").exists()


def test_pytest_configure_uses_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Path] = []
    monkeypatch.setattr(conftest, "populate_mutmut_tree", seen.append)
    monkeypatch.chdir(tmp_path)
    conftest.pytest_configure(cast(pytest.Config, object()))
    assert seen == [tmp_path]
