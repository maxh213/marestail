from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from marestail.report import Result
from tests import conftest


def test_clock_advances_from_the_start_by_the_step() -> None:
    clock = conftest.Clock(10.0, 0.5)
    assert (clock(), clock(), clock()) == (10.0, 10.5, 11.0)


def test_gate_shape_keeps_gate_ok_summary_and_findings() -> None:
    result = Result("docs", True, "ok", ["a"], 0.25)
    assert conftest.gate_shape(result) == ("docs", True, "ok", ["a"])


def test_gate_shape_rejects_a_blank_gate() -> None:
    blank = Result("", True, "ok", [], 0.0)
    with pytest.raises(AssertionError):
        conftest.gate_shape(blank)


def test_gate_shape_rejects_missing_seconds() -> None:
    broken = Result("g", True, "ok", [], 0.0)
    broken.seconds = cast(float, None)
    with pytest.raises(AssertionError):
        conftest.gate_shape(broken)


def test_erlang_tests_keep_the_real_host_probe() -> None:
    from tests import test_erlang

    assert test_erlang.KEEP_ERLANG_HOST is True


def test_fake_run_copies_reply_lists(tmp_path: Path) -> None:
    replies = [(0, "a")]
    fake = conftest.FakeRun(replies)
    assert fake(["cmd"], tmp_path) == (0, "a")
    assert replies == [(0, "a")]
    assert fake(["cmd"], tmp_path) == (0, "")


def test_fake_run_rejects_a_missing_cwd(tmp_path: Path) -> None:
    fake = conftest.FakeRun([(0, "")])
    with pytest.raises(TypeError, match=r"^cwd$"):
        fake(["cmd"], None)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match=r"^timeout$"):
        fake(["cmd"], tmp_path, timeout=None)
    with pytest.raises(TypeError, match=r"^command$"):
        fake(["cmd", None], tmp_path)  # type: ignore[list-item]
    conftest.check_fake_run(["cmd"], tmp_path, {})
    assert conftest.reject_none(lambda ctx: ctx)("x") == "x"
    with pytest.raises(TypeError, match=r"^ctx$"):
        conftest.reject_none(lambda ctx: ctx)(None)


def test_git_try_show_toplevel() -> None:
    completed = conftest.git_try("rev-parse", "--show-toplevel")
    assert completed.returncode == 0
    assert Path(completed.stdout.strip()).is_dir()


def test_git_toplevel_is_this_repo() -> None:
    root = conftest.git_toplevel()
    assert root is not None
    assert (root / "marestail.toml").is_file()


def test_git_toplevel_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(conftest, "git_try", lambda *_args: SimpleNamespace(returncode=1, stdout=""))
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
