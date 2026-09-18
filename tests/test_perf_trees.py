from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from marestail.config import Config
from marestail.perf import trees
from marestail.shell import run as real_run
from tests.conftest import FakeRun, commit_all, git

TABLE = "| Task | Commit | Date | Rows | t p50 | u p95 |\n|---|---|---|---|---|---|\n| a | b | c | d | 1 | 2 |\n"


def config_at(root: Path, **perf: Any) -> Config:
    return Config(root=root, raw={"perf": perf, "git": {"base": "main"}})


def commit_file(root: Path, name: str, text: str = "x") -> str:
    (root / name).write_text(text)
    commit_all(root, name)
    return git(root, "rev-parse", "HEAD").strip()


def test_work_files_live_under_marestail_perf(tmp_path: Path) -> None:
    config = config_at(tmp_path)
    assert trees.trees_file(config) == tmp_path / ".marestail" / "perf" / "trees.json"
    assert trees.samples_file(config) == tmp_path / ".marestail" / "perf" / "samples.jsonl"
    assert (tmp_path / ".marestail" / "perf").is_dir()
    assert trees.start_file(config, "t1") == tmp_path / ".marestail" / "runs" / "t1" / "start-commit"


def test_recorded_and_active(tmp_path: Path) -> None:
    config = config_at(tmp_path)
    assert trees.recorded(config) == {}
    assert trees.active(config) is None
    session = trees.Session("t1", [trees.Tree("head", "abc", tmp_path)], image="pg", image_source="env", rows=3, rows_source="toml")
    trees.write_trees(config, session)
    assert trees.recorded(config) == {
        "task": "t1",
        "image": "pg",
        "image_source": "env",
        "rows": 3,
        "rows_source": "toml",
        "trees": [{"tree": "head", "sha": "abc", "path": str(tmp_path)}],
    }
    assert trees.active(config) == {"head": trees.Tree("head", "abc", tmp_path)}
    assert trees.trees_file(config).read_text().endswith("}\n")


def test_record_start_keeps_the_first_commit(git_repo: Path) -> None:
    config = config_at(git_repo)
    first = commit_file(git_repo, "a")
    trees.record_start(config, "t1")
    commit_file(git_repo, "b")
    trees.record_start(config, "t1")
    assert trees.start_file(config, "t1").read_text() == first + "\n"
    assert trees.start_commit(config, "t1") == (first, "")


def test_start_commit_falls_back_to_merge_base(git_repo: Path) -> None:
    first = commit_file(git_repo, "a")
    git(git_repo, "checkout", "-q", "-b", "topic")
    commit_file(git_repo, "b")
    assert trees.start_commit(config_at(git_repo), "t1") == (first, "no recorded start commit for t1; using git merge-base main HEAD")


def test_start_commit_falls_back_to_head(git_repo: Path) -> None:
    head = commit_file(git_repo, "a")
    config = Config(root=git_repo, raw={})
    assert trees.start_commit(config, "t1") == (
        head,
        "no recorded start commit for t1 and no merge-base with origin/master; using HEAD",
    )


def test_archive_start(tmp_path: Path) -> None:
    config = config_at(tmp_path)
    trees.archive_start(config, "t1", None)
    path = trees.start_file(config, "t1")
    path.parent.mkdir(parents=True)
    path.write_text("abc\n")
    trees.archive_start(config, "t1", None)
    assert not path.exists()
    path.write_text("def\n")
    destination = tmp_path / "archive"
    destination.mkdir()
    trees.archive_start(config, "t1", destination)
    assert not path.exists()
    assert (destination / "start-commit").read_text() == "def\n"


def test_pre_marestail_commit_is_the_parent_of_the_toml_commit(git_repo: Path) -> None:
    config = config_at(git_repo)
    assert trees.pre_marestail_commit(config) == (None, trees.NO_PRE_MARESTAIL)
    commit_file(git_repo, "marestail.toml")
    assert trees.pre_marestail_commit(config) == (None, trees.NO_PRE_MARESTAIL)
    git(git_repo, "rm", "-q", "marestail.toml")
    commit_all(git_repo, "drop")
    commit_file(git_repo, "marestail.toml", "again")
    assert trees.pre_marestail_commit(config) == (None, trees.NO_PRE_MARESTAIL)


def test_pre_marestail_commit_found(git_repo: Path) -> None:
    first = commit_file(git_repo, "a")
    commit_file(git_repo, "marestail.toml")
    assert trees.pre_marestail_commit(config_at(git_repo)) == (first, "")


def test_pre_marestail_commit_skipped_once_the_table_has_rows(git_repo: Path) -> None:
    commit_file(git_repo, "a")
    commit_file(git_repo, "marestail.toml")
    (git_repo / "PERFORMANCE.md").write_text(TABLE)
    assert trees.pre_marestail_commit(config_at(git_repo)) == (None, "")


def worktrees(root: Path) -> list[str]:
    return [line for line in git(root, "worktree", "list", "--porcelain").splitlines() if line.startswith("worktree ")]


def test_measuring_builds_and_removes_every_tree(git_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    first = commit_file(git_repo, "a", "one")
    git(git_repo, "checkout", "-q", "-b", "topic")
    head = commit_file(git_repo, "marestail.toml")
    config = config_at(git_repo, control=True)
    with trees.measuring(config, "t1") as session:
        assert [(tree.name, tree.sha) for tree in session.trees] == [
            ("baseline", first),
            ("head", head),
            ("control", first),
            ("pre-marestail", first),
        ]
        assert session.trees[1].path == git_repo
        assert all((tree.path / "a").read_text() == "one" for tree in session.trees)
        assert [entry["tree"] for entry in trees.recorded(config)["trees"]] == ["baseline", "head", "control", "pre-marestail"]
        assert len(worktrees(git_repo)) == 4
        paths = [tree.path for tree in session.trees]
    assert session.notes == ["no recorded start commit for t1; using git merge-base main HEAD"]
    assert capsys.readouterr().out == "   no recorded start commit for t1; using git merge-base main HEAD\n"
    assert len(worktrees(git_repo)) == 1
    assert [path.exists() for path in paths] == [False, True, False, False]
    assert not trees.trees_file(config).exists()


def test_measuring_notes_a_failed_setup(git_repo: Path, fake_run: Callable[..., FakeRun], capsys: pytest.CaptureFixture[str]) -> None:
    start = commit_file(git_repo, "a")
    trees.record_start(config_at(git_repo), "t1")
    commit_file(git_repo, "marestail.toml")
    fake = fake_run(trees, lambda command: (1, "one\ntwo\n") if command[0] == "bash" else real_run(command, git_repo))
    with trees.measuring(config_at(git_repo, setup="make seed"), "t1") as session:
        assert [tree.name for tree in session.trees] == ["baseline", "head", "pre-marestail"]
    assert session.notes == [
        "[perf] setup failed in the baseline tree (exit 1): one | two",
        "[perf] setup failed in the pre-marestail tree (exit 1): one | two",
    ]
    assert fake.calls[2] == ["git", "worktree", "add", "--detach", str(session.trees[0].path), start]
    assert fake.calls[3] == ["bash", "-lc", "make seed"]
    assert fake.options[3] == {"cwd": session.trees[0].path, "timeout": 3600}
    assert capsys.readouterr().out == ""


def test_measuring_with_a_passing_setup(git_repo: Path, fake_run: Callable[..., FakeRun]) -> None:
    commit_file(git_repo, "a")
    (git_repo / "PERFORMANCE.md").write_text(TABLE)
    fake_run(trees, lambda command: (0, "") if command[0] == "bash" else real_run(command, git_repo))
    with trees.measuring(config_at(git_repo, setup="make seed"), "t1") as session:
        assert [tree.name for tree in session.trees] == ["baseline", "head"]
    assert session.notes == ["no recorded start commit for t1; using git merge-base main HEAD"]


def test_measuring_cleans_up_after_a_failed_worktree(git_repo: Path) -> None:
    commit_file(git_repo, "a")
    config = config_at(git_repo)
    trees.start_file(config, "t1").parent.mkdir(parents=True)
    trees.start_file(config, "t1").write_text("0000000000000000000000000000000000000000\n")
    with (
        pytest.raises(RuntimeError, match=r"^git worktree add for the baseline tree at 0{40} failed: \S") as raised,
        trees.measuring(config, "t1"),
    ):
        pass
    assert "\n" not in str(raised.value)
    assert len(worktrees(git_repo)) == 1
    assert not trees.trees_file(config).exists()


def test_prompt_section_lists_trees_policy_and_notes(tmp_path: Path) -> None:
    (tmp_path / "PERFORMANCE.md").write_text(TABLE)
    config = config_at(tmp_path, threshold_percent=7.5, min_runs=3, min_change={"ms": 2, "rps": 0.5})
    session = trees.Session(
        "t1",
        [trees.Tree("baseline", "aaa", Path("/w/b")), trees.Tree("control", "aaa", Path("/w/c"))],
        notes=["first", "second"],
        image="postgres:16",
        image_source="env",
        rows=10,
        rows_source="default",
    )
    assert trees.prompt_section(config, session).splitlines() == [
        "- baseline: aaa at /w/b",
        "- control: aaa at /w/c",
        "- threshold_percent: 7.5",
        "- min_runs: 3",
        "- values_per_sample: 200 (p95 is classified only with 200 pooled values per tree)",
        "- min_change: 2 ms, 0.5 rps",
        "- existing columns every run must re-measure: `t p50`, `u p95`",
        "- control: a second copy of the baseline commit; its difference from baseline is the noise a change must exceed",
        "- performance database: postgres:16 (from env), rows 10 (default); connect each tree's app with `marestail perf db url --tree <tree>`",
        "- note: first",
        "- note: second",
    ]


def test_prompt_section_minimal(tmp_path: Path) -> None:
    config = config_at(tmp_path, min_change={})
    session = trees.Session("t1", [trees.Tree("head", "bbb", tmp_path)])
    assert trees.prompt_section(config, session) == "\n".join(
        [
            f"- head: bbb at {tmp_path}",
            "- threshold_percent: 10",
            "- min_runs: 10",
            "- values_per_sample: 200 (p95 is classified only with 200 pooled values per tree)",
            "- min_change: none",
            "- existing columns every run must re-measure: none",
        ]
    )
