from pathlib import Path

import pytest

from marestail import hunks
from marestail.config import Config
from tests.conftest import commit_all, git

UTIL = "def untouched(value):\n    if value:\n        return 1\n    return 0\n"
NO_MOVES = "under hyper no file may be renamed, moved or deleted"


def write(root: Path, path: str, text: str) -> None:
    file = root / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(text)


@pytest.fixture
def repo(git_repo: Path) -> tuple[Config, str]:
    write(git_repo, "util.py", UTIL)
    write(git_repo, "src.py", "original\n")
    commit_all(git_repo, "init")
    return Config(root=git_repo, raw={}), git(git_repo, "rev-parse", "HEAD").strip()


def change(config: Config, files: dict[str, str]) -> None:
    for path, text in files.items():
        write(config.root, path, text)
    commit_all(config.root, "change")


def test_problems_is_empty_when_every_source_hunk_is_listed(repo: tuple[Config, str]) -> None:
    config, start = repo
    change(config, {"src.py": "def add_one(x):\n    return x + 1\n", "tests/test_src.py": "def test_a():\n    pass\n"})
    assert hunks.problems(config, start, "done\n## Hunks\n- src.py:1-2 — the fix needs it\n") == []


def test_problems_reports_a_whitespace_hunk_and_an_unlisted_hunk(repo: tuple[Config, str]) -> None:
    config, start = repo
    reindented = (
        UTIL.replace("    if", "        if").replace("        return 1", "            return 1").replace("    return 0", "        return 0")
    )
    change(config, {"util.py": reindented, "src.py": "changed\n"})
    assert hunks.problems(config, start, "no section") == [
        f"util.py:2-4: {hunks._WHITESPACE}",
        "src.py:1-1: not listed under ## Hunks",
    ]


def test_problems_treats_a_reindent_with_a_real_change_as_a_normal_hunk(repo: tuple[Config, str]) -> None:
    config, start = repo
    change(config, {"util.py": "def untouched(value):\n        if value:\n            return 2\n        return 0\n"})
    assert hunks.problems(config, start, "") == ["util.py:2-4: not listed under ## Hunks"]


def test_problems_reports_a_rename_alone(repo: tuple[Config, str]) -> None:
    config, start = repo
    git(config.root, "mv", "util.py", "helpers.py")
    commit_all(config.root, "move")
    assert hunks.problems(config, start, "") == [f"util.py -> helpers.py: renamed or moved; {NO_MOVES}"]


def test_problems_reports_a_whitespace_hunk_next_to_a_real_change(repo: tuple[Config, str]) -> None:
    config, start = repo
    change(config, {"util.py": UTIL.replace("    if", "        if").replace("return 0", "return 5")})
    assert hunks.problems(config, start, "") == [
        f"util.py:2-2: {hunks._WHITESPACE}",
        "util.py:4-4: not listed under ## Hunks",
    ]


@pytest.mark.parametrize("renames", ["true", "false"])
def test_problems_reports_a_renamed_and_edited_file_only_as_a_rename(repo: tuple[Config, str], renames: str) -> None:
    config, start = repo
    git(config.root, "config", "diff.renames", renames)
    git(config.root, "mv", "util.py", "helpers.py")
    write(config.root, "helpers.py", UTIL.replace("    if", "        if").replace("return 1", "return 2"))
    commit_all(config.root, "move")
    assert hunks.problems(config, start, "") == [f"util.py -> helpers.py: renamed or moved; {NO_MOVES}"]


def test_problems_names_renamed_paths_with_spaces(repo: tuple[Config, str]) -> None:
    config, _ = repo
    write(config.root, "my util.py", UTIL)
    commit_all(config.root, "spaced")
    start = git(config.root, "rev-parse", "HEAD").strip()
    git(config.root, "mv", "my util.py", "my helpers.py")
    commit_all(config.root, "move")
    assert hunks.problems(config, start, "") == [f"my util.py -> my helpers.py: renamed or moved; {NO_MOVES}"]


def test_problems_reports_a_deletion_alone(repo: tuple[Config, str]) -> None:
    config, start = repo
    git(config.root, "rm", "-q", "util.py")
    commit_all(config.root, "delete")
    assert hunks.problems(config, start, "") == [f"util.py: deleted; {NO_MOVES}"]


def test_problems_ignore_features_qa_and_non_source_hunks(repo: tuple[Config, str]) -> None:
    config, start = repo
    change(config, {"features/t.feature": "Feature: t\n", "qa/t.md": "1. x\n", "notes.txt": "x\n"})
    assert hunks.problems(config, start, "") == []


@pytest.mark.parametrize(
    ("header", "expected"),
    [("@@ -1 +1,2 @@", (1, 2)), ("@@ -3 +3 @@", (3, 3)), ("@@ -3,2 +2,0 @@", (2, 2)), ("@@ -0,0 +5,3 @@ def x():", (5, 7))],
)
def test_span_reads_the_new_side(header: str, expected: tuple[int, int]) -> None:
    match = hunks._HUNK_HEADER.search(header)
    assert match is not None
    assert hunks._span(match) == expected


def test_parse_hunks_takes_each_file_path_from_its_header() -> None:
    diff = (
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x\n+y\n@@ -4 +4,2 @@\n+z\n"
        "diff --git a/b.py b/b.py\ndeleted file mode 100644\n--- a/b.py\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-++ b/c.py\n"
    )
    assert hunks._parse_hunks(diff) == [("a.py", 1, 1), ("a.py", 4, 5)]


def test_parse_hunks_of_an_empty_diff() -> None:
    assert hunks._parse_hunks("") == []


@pytest.mark.parametrize(
    ("one", "other", "expected"),
    [
        (("a", 2, 4), ("a", 4, 6), True),
        (("a", 2, 4), ("a", 3, 3), True),
        (("a", 2, 4), ("a", 5, 6), False),
        (("a", 5, 6), ("a", 2, 4), False),
        (("a", 2, 4), ("b", 2, 4), False),
    ],
)
def test_overlaps(one: hunks._Hunk, other: hunks._Hunk, expected: bool) -> None:
    assert hunks._overlaps(one, other) is expected


def test_covered() -> None:
    assert hunks._covered(("a", 1, 1), [("b", 1, 1), ("a", 1, 3)]) is True
    assert hunks._covered(("a", 1, 1), []) is False


@pytest.mark.parametrize(("path", "expected"), [("src.py", True), ("tests/test_src.py", False), ("notes.txt", False)])
def test_needs_listing(path: str, expected: bool) -> None:
    assert hunks._needs_listing(path) is expected


def test_findings() -> None:
    assert hunks._findings([("a.py", 1, 2)], "bad") == ["a.py:1-2: bad"]


def test_moved_file() -> None:
    assert hunks._moved_file(["D", "a.py"]) == (f"a.py: deleted; {NO_MOVES}", "a.py")
    assert hunks._moved_file(["R100", "a.py", "b.py"]) == (f"a.py -> b.py: renamed or moved; {NO_MOVES}", "b.py")


def test_listed_hunks_accept_bullets_bare_lines_and_single_lines() -> None:
    handoff = (
        "intro\n- x.py:9-9 — outside\n## Hunks\n- src.py:1-2 — the fix needs it\nutil.py:3-3 — why\n"
        "  * lib/a.py:7 - why\nnot a line\n\n## Audit\n- b.py:1-1 — after\n"
    )
    assert hunks._listed_hunks(handoff) == [("src.py", 1, 2), ("util.py", 3, 3), ("lib/a.py", 7, 7)]


def test_listed_hunks_without_a_section() -> None:
    assert hunks._listed_hunks("- src.py:1-2 — why\n") == []


def test_hunks_section_runs_to_the_end_without_a_next_heading() -> None:
    assert hunks._hunks_section("## Hunks  \na\n\nb") == ["a", "", "b"]


def test_review_holds_the_diff_since_start_and_the_latest_hunks(repo: tuple[Config, str], tmp_path: Path) -> None:
    config, start = repo
    change(config, {"util.py": UTIL.replace("return 1", "return 2"), "features/t.feature": "Feature: t\n"})
    handoffs = tmp_path / "handoffs"
    write(handoffs, "01-coder.md", "coded\n## Hunks\n- old.py:1 — why\n")
    write(handoffs, "03-coder.md", "coded\n## Hunks\n- util.py:3-3 — why\n\n## Audit\n- x\n")
    write(handoffs, "04-architect.md", "architect done\n")
    review = hunks.review(config, start, handoffs)
    assert "util.py | 2 +-" in review["Diff stat"]
    assert "features" not in review["Diff stat"]
    assert "+        return 2" in review["Diff"]
    assert review["Hunks"] == "## 03-coder\n- util.py:3-3 — why"


def test_review_lists_the_coder_then_the_architect(repo: tuple[Config, str], tmp_path: Path) -> None:
    config, start = repo
    write(tmp_path, "01-coder.md", "## Hunks\n- util.py:3-3 — why\n")
    write(tmp_path, "02-architect.md", "## Hunks\n- util.py:1-4 — boy scout\n")
    assert hunks.review(config, start, tmp_path)["Hunks"] == "## 01-coder\n- util.py:3-3 — why\n## 02-architect\n- util.py:1-4 — boy scout"


def test_review_is_empty_without_changes_or_handoffs(repo: tuple[Config, str], tmp_path: Path) -> None:
    config, start = repo
    assert hunks.review(config, start, tmp_path / "missing") == {"Diff stat": "", "Diff": "", "Hunks": ""}


def test_hunks_listing_of_a_handoff_without_hunks(tmp_path: Path) -> None:
    report = tmp_path / "02-architect.md"
    report.write_text("## Hunks\n\n")
    assert hunks._hunks_listing(report) == ""
