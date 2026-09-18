import time
from pathlib import Path
from typing import Any

import pytest

from marestail import audit, runner
from marestail.config import Config
from marestail.perf import trees as perf_trees
from marestail.pipeline import Worker
from marestail.report import Result, render
from marestail.runner import Run
from tests.conftest import commit_all, git

LABEL = "m e"


def make_state(root: Path, **fields: Any) -> Run:
    base: dict[str, Any] = {"model": "m", "effort": "e", "agent": "claude", "retries": 1}
    return Run(config=Config(root=root, raw={}), task=root / "tasks" / "task.md", **{**base, **fields})


def write(root: Path, path: str, text: str = "x\n") -> Path:
    file = root / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(text)
    return file


def last_message(root: Path) -> str:
    return git(root, "log", "-1", "--format=%B").strip()


def commit_count(root: Path) -> int:
    return int(git(root, "rev-list", "--count", "HEAD"))


def tracked(root: Path) -> list[str]:
    return git(root, "ls-files").split()


@pytest.fixture
def repo(git_repo: Path) -> Path:
    write(git_repo, "a.txt", "a\n")
    write(git_repo, "c.txt", "c\n")
    write(git_repo, ".gitignore", "*.log\n")
    commit_all(git_repo, "init")
    return git_repo


@pytest.mark.parametrize(
    ("line", "expected"),
    [("?? new.txt", "new.txt"), (" M a.txt", "a.txt"), ("M  a.txt", "a.txt"), ("a.txt", "a.txt"), ("ab cd", "cd"), ("  x", "  x")],
)
def test_porcelain_path(line: str, expected: str) -> None:
    assert runner.porcelain_path(line) == expected


@pytest.mark.parametrize(("path", "expected"), [("", False), ("  ", False), (".marestail/x", False), (" .marestail/x", False), ("a", True)])
def test_outside_work(path: str, expected: bool) -> None:
    assert runner.outside_work(path) is expected


def test_changed_paths_reads_porcelain(repo: Path) -> None:
    write(repo, "a.txt", "changed\n")
    write(repo, "b.txt")
    write(repo, ".marestail/runs/x.json")
    git(repo, "mv", "c.txt", "d.txt")
    assert runner.changed_paths(Config(root=repo, raw={}), runner.STATUS_COMMAND) == ["a.txt", "d.txt", "b.txt"]


def test_head_matches_git(repo: Path) -> None:
    assert runner.head(Config(root=repo, raw={})) == git(repo, "rev-parse", "HEAD").strip()


def test_discard_edits_without_strays_does_nothing(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    keep = write(repo, "report.md")
    runner.discard_edits(Config(root=repo, raw={}), keep)
    assert keep.exists()
    assert capsys.readouterr().out == ""


def test_discard_edits_resets_whole_tree(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    keep = write(repo, "report.md")
    write(repo, "a.txt", "changed\n")
    write(repo, "junk/j.txt")
    scratch = write(repo, ".marestail/runs/x.json")
    runner.discard_edits(Config(root=repo, raw={}), keep)
    assert (repo / "a.txt").read_text() == "a\n"
    assert not (repo / "junk").exists()
    assert keep.exists()
    assert scratch.exists()
    assert capsys.readouterr().out == "   discarding edits a judge made: a.txt, junk/j.txt\n"


def test_discard_edits_with_writes_restores_only_strays(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    keep = write(repo, "report.md")
    write(repo, "a.txt", "changed\n")
    write(repo, "staged.txt")
    git(repo, "add", "staged.txt")
    write(repo, "z.txt")
    bench = write(repo, "perf/b.py")
    runner.discard_edits(Config(root=repo, raw={}), keep, writes=("perf/**",))
    assert (repo / "a.txt").read_text() == "a\n"
    assert not (repo / "staged.txt").exists()
    assert not (repo / "z.txt").exists()
    assert bench.exists()
    assert "staged.txt" not in tracked(repo)
    assert capsys.readouterr().out == "   discarding edits a judge made: a.txt, staged.txt, z.txt\n"


def test_restore_paths_handles_only_tracked_or_only_untracked(repo: Path) -> None:
    config = Config(root=repo, raw={})
    write(repo, "a.txt", "changed\n")
    runner.restore_paths(config, ["a.txt"])
    assert (repo / "a.txt").read_text() == "a\n"
    write(repo, "new.txt")
    runner.restore_paths(config, ["new.txt"])
    assert not (repo / "new.txt").exists()


def test_stage_writes_without_patterns_runs_nothing(repo: Path, fake_run: Any) -> None:
    fake = fake_run(runner)
    runner.stage_writes(Config(root=repo, raw={}), ())
    assert fake.calls == []


def test_stage_writes_stages_matching_paths(repo: Path) -> None:
    config = Config(root=repo, raw={})
    write(repo, "other.txt")
    runner.stage_writes(config, ("perf/**",))
    assert git(repo, "diff", "--cached", "--name-only") == ""
    write(repo, "perf/x.py")
    runner.stage_writes(config, ("perf/**",))
    assert git(repo, "diff", "--cached", "--name-only").split() == ["perf/x.py"]


def test_drop_ignored_since_amends_commit(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = Config(root=repo, raw={})
    before = runner.head(config)
    write(repo, "a.log", "log data\n")
    write(repo, "b.txt")
    git(repo, "add", "-f", "a.log", "b.txt")
    git(repo, "commit", "-q", "-m", "work")
    saved = runner.drop_ignored_since(config, before)
    assert saved == {"a.log": b"log data\n"}
    assert "a.log" not in git(repo, "ls-tree", "-r", "--name-only", "HEAD").split()
    assert commit_count(repo) == 2
    assert last_message(repo) == "work"
    assert capsys.readouterr().out == "   dropping gitignored files: a.log\n"


def test_drop_ignored_since_without_commit_only_unstages(repo: Path) -> None:
    config = Config(root=repo, raw={})
    before = runner.head(config)
    write(repo, "a.log")
    git(repo, "add", "-f", "a.log")
    (repo / "a.log").unlink()
    assert runner.drop_ignored_since(config, before) == {}
    assert "a.log" not in tracked(repo)
    assert runner.head(config) == before


def test_drop_ignored_since_with_nothing_ignored(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = Config(root=repo, raw={})
    before = runner.head(config)
    write(repo, "b.txt")
    commit_all(repo, "b")
    assert runner.drop_ignored_since(config, before) == {}
    assert capsys.readouterr().out == ""


def test_added_paths() -> None:
    assert runner.added_paths("a\nb\n", "a\n\nc\nb\nd") == ["c", "d"]


def test_restore_files_creates_parents(tmp_path: Path) -> None:
    runner.restore_files(Config(root=tmp_path, raw={}), {"deep/dir/f.bin": b"\x00data", "top.txt": b"t"})
    assert (tmp_path / "deep/dir/f.bin").read_bytes() == b"\x00data"
    assert (tmp_path / "top.txt").read_bytes() == b"t"


@pytest.mark.parametrize(
    ("message", "label", "used", "expected"),
    [
        ("msg", "", None, "msg"),
        ("msg", "L", None, "[L] msg"),
        ("[L] msg", "L", None, "[L] msg"),
        ("[O] msg", "L", {"O"}, "[O] msg"),
        ("[O] msg", "L", None, "[L] [O] msg"),
    ],
)
def test_stamped(message: str, label: str, used: set[str] | None, expected: str) -> None:
    assert runner.stamped(message, label, used) == expected


@pytest.mark.parametrize(
    ("message", "expected"),
    [("a\n\nBy coder.\n", "a"), ("a\nBy other.", "a\nBy other."), ("", ""), ("By coder.", "")],
)
def test_strip_byline(message: str, expected: str) -> None:
    assert runner.strip_byline(message, "coder") == expected


def test_record_commit(repo: Path) -> None:
    runner.record_commit(Config(root=repo, raw={}), "critic verdict: PASS", "  details \n", "critic", LABEL)
    assert last_message(repo) == "[m e] critic verdict: PASS\n\ndetails\n\nBy critic."


def test_record_staged_commits_only_when_staged(repo: Path) -> None:
    config = Config(root=repo, raw={})
    runner.record_staged(config, "perf-author-1 benches", "perf", LABEL)
    assert commit_count(repo) == 1
    write(repo, "perf/b.py")
    git(repo, "add", "perf/b.py")
    runner.record_staged(config, "perf-author-1 benches", "perf", LABEL)
    assert last_message(repo) == "[m e] perf-author-1 benches\n\nBy perf."


def test_fold_handoff_without_commits_records_one(repo: Path) -> None:
    config = Config(root=repo, raw={})
    report = write(repo, ".marestail/h/01-coder.md", "\nbody\n")
    runner.fold_handoff(config, "coder", report, runner.head(config), LABEL)
    assert last_message(repo) == "[m e] coder handoff\n\nbody\n\nBy coder."


def test_fold_handoff_restamps_and_amends(repo: Path) -> None:
    config = Config(root=repo, raw={})
    before = runner.head(config)
    write(repo, "b.txt")
    git(repo, "add", "b.txt")
    git(repo, "-c", "user.name=Worker", "commit", "-q", "-m", "first\n\nBy coder.")
    write(repo, "c.txt", "changed\n")
    git(repo, "commit", "-qam", "second\n\nBy coder.")
    report = write(repo, ".marestail/h/01-coder.md", "body")
    runner.fold_handoff(config, "coder", report, before, LABEL, {"other"})
    assert last_message(repo) == "[m e] second\n\nbody\n\nBy coder."
    assert git(repo, "log", "-1", "--skip=1", "--format=%an|%B").strip() == "Worker|[m e] first\n\nBy coder."
    assert commit_count(repo) == 3


def test_fold_handoff_without_label_keeps_history(repo: Path) -> None:
    config = Config(root=repo, raw={})
    before = runner.head(config)
    write(repo, "b.txt")
    commit_all(repo, "work\n\nBy coder.")
    report = write(repo, ".marestail/h/01-coder.md", "body")
    runner.fold_handoff(config, "coder", report, before, "")
    assert last_message(repo) == "work\n\nbody\n\nBy coder."


def test_stamp_history_skips_merges(repo: Path) -> None:
    config = Config(root=repo, raw={})
    before = runner.head(config)
    git(repo, "checkout", "-q", "-b", "side")
    write(repo, "side.txt")
    commit_all(repo, "side")
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "-q", "--no-ff", "-m", "merge", "side")
    head = runner.head(config)
    runner.stamp_history(config, before, LABEL)
    assert runner.head(config) == head


def test_stamp_history_skips_without_commits(repo: Path) -> None:
    config = Config(root=repo, raw={})
    before = runner.head(config)
    runner.stamp_history(config, before, LABEL)
    assert runner.head(config) == before


def test_file_diff(repo: Path) -> None:
    config = Config(root=repo, raw={})
    before = runner.head(config)
    write(repo, "a.txt", "b\n")
    assert "-a\n+b" in runner.file_diff(config, before, "a.txt")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (None, None),
        ("no section", None),
        ("intro\n## Config change\n  because \n## Next\nmore", "because"),
        ("## Config change\nall of it\n", "all of it"),
    ],
)
def test_config_change_section(tmp_path: Path, text: str | None, expected: str | None) -> None:
    report = tmp_path / "r.md"
    if text is not None:
        report.write_text(text)
    assert runner.config_change_section(report) == expected


def frozen_setup(repo: Path) -> tuple[Run, str]:
    write(repo, "marestail.toml", "[a]\n")
    commit_all(repo, "toml")
    state = make_state(repo)
    before = runner.head(state.config)
    write(repo, "marestail.toml", "[b]\n")
    commit_all(repo, "change toml")
    return state, before


def test_reject_config_change_without_reason_reverts(repo: Path) -> None:
    state, before = frozen_setup(repo)
    report = state.next_report("coder")
    report.write_text("no reason")
    problems = runner.reject_config_change(state, Worker("coder", None), report, before, ["marestail.toml"])
    assert problems == ["marestail.toml is frozen for coder; reverted. Work within the current configuration."]
    assert (repo / "marestail.toml").read_text() == "[a]\n"
    assert last_message(repo) == "[m e] Revert change to frozen files by 01-coder\n\nBy runner."


def test_reject_config_change_records_proposal(repo: Path) -> None:
    state, before = frozen_setup(repo)
    report = state.next_report("coder")
    report.write_text("## Config change\nneeded\n")
    diff = git(repo, "diff", f"{before}..HEAD", "--", "marestail.toml").strip()
    problems = runner.reject_config_change(state, Worker("coder", None), report, before, ["marestail.toml"])
    body = f"Proposed by 01-coder: marestail.toml\n\nneeded\n\n```diff\n{diff}\n```\n"
    assert problems == [
        "marestail.toml: frozen, reverted. Your reason was recorded as .marestail/handoffs/task/02-proposal.md "
        "for a human to consider after the run. Find a way within the current configuration."
    ]
    assert (state.handoffs / "02-proposal.md").read_text() == body
    assert last_message(repo) == f"[m e] Revert change to frozen files by 01-coder, recorded as a proposal\n\n{body}\nBy runner."
    assert (repo / "marestail.toml").read_text() == "[a]\n"


def test_proposals_summary(tmp_path: Path) -> None:
    state = make_state(tmp_path)
    assert runner.proposals_summary(state) == ""
    state.handoffs.mkdir(parents=True)
    (state.handoffs / "01-coder.md").write_text("c")
    assert runner.proposals_summary(state) == ""
    (state.handoffs / "02-proposal.md").write_text(" first \n")
    (state.handoffs / "04-proposal.md").write_text("second")
    assert runner.proposals_summary(state) == (
        "\n## Config changes the agents asked for and were refused\nDecide whether to make any of these yourself.\n\n"
        "### 02-proposal\nfirst\n\n### 04-proposal\nsecond"
    )


def fail_gates(monkeypatch: pytest.MonkeyPatch, ok: bool) -> list[tuple[Any, ...]]:
    calls: list[tuple[Any, ...]] = []

    def gates(*args: Any) -> list[Result]:
        calls.append(args)
        return [Result(gate="lint", ok=ok, summary="summary")]

    monkeypatch.setattr(runner, "run_gates", gates)
    return calls


def test_verify_worker_clean(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fail_gates(monkeypatch, True)
    state = make_state(repo)
    report = state.next_report("coder")
    report.write_text("done")
    assert runner.verify_worker(state, Worker("coder", "fast"), report, runner.head(state.config)) == ""
    assert calls == [("fast", False, None, set(), False)]


def test_verify_worker_collects_problems(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fail_gates(monkeypatch, False)
    monkeypatch.setattr(audit, "problems", lambda config, task, text, role: [f"audit {task} {text} {role}"])
    write(repo, "marestail.toml", "[a]\n")
    commit_all(repo, "toml")
    state = make_state(repo)
    before = runner.head(state.config)
    write(repo, "marestail.toml", "[b]\n")
    missing = state.handoffs / "01-coder.md"
    problems = runner.verify_worker(state, Worker("coder", "fast", audit=True), missing, before)
    gate = render([Result(gate="lint", ok=False, summary="summary")])
    assert problems == "\n\n".join(
        [
            "missing handoff .marestail/handoffs/task/01-coder.md",
            "uncommitted changes:\nmarestail.toml",
            "marestail.toml is frozen for coder",
            gate,
        ]
    )


def test_verify_worker_audits_existing_report(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(audit, "problems", lambda config, task, text, role: [f"audit {task} {text} {role}"])
    state = make_state(repo)
    report = state.next_report("coder")
    report.write_text("handoff")
    assert runner.verify_worker(state, Worker("coder", None, audit=True), report, runner.head(state.config)) == "audit task handoff coder"


def test_verify_worker_lists_first_twenty_dirty_paths(repo: Path) -> None:
    state = make_state(repo)
    report = state.next_report("coder")
    report.write_text("x")
    for number in range(25):
        write(repo, f"f{number:02d}.txt")
    problems = runner.verify_worker(state, Worker("coder", None), report, runner.head(state.config))
    assert problems == "uncommitted changes:\n" + "\n".join(f"f{number:02d}.txt" for number in range(20))


def test_verify_worker_reverts_committed_frozen_change(repo: Path) -> None:
    state, before = frozen_setup(repo)
    report = state.next_report("coder")
    report.write_text("done")
    problems = runner.verify_worker(state, Worker("coder", None), report, before)
    assert problems == "marestail.toml is frozen for coder; reverted. Work within the current configuration."


def test_verify_worker_tolerates_csproj_package_additions(repo: Path) -> None:
    write(repo, "app/App.csproj", "<Project>\n</Project>\n")
    commit_all(repo, "csproj")
    state = make_state(repo)
    before = runner.head(state.config)
    write(repo, "app/App.csproj", '<Project>\n<PackageReference Include="X" Version="1" />\n</Project>\n')
    commit_all(repo, "add package")
    report = state.next_report("coder")
    report.write_text("done")
    assert runner.verify_worker(state, Worker("coder", None), report, before) == ""


def test_gate_for(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fail_gates(monkeypatch, False)
    state = make_state(tmp_path)
    assert runner.gate_for(state, None) == ("", True)
    assert runner.gate_for(state, "full") == (render([Result(gate="lint", ok=False, summary="summary")]), False)
    fail_gates(monkeypatch, True)
    assert runner.gate_for(state, "full")[1] is True


def test_archive_handoffs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archived: list[tuple[Any, ...]] = []
    monkeypatch.setattr(perf_trees, "archive_start", lambda *args: archived.append(args))
    monkeypatch.setattr(time, "strftime", lambda fmt: f"stamp{fmt}")
    state = make_state(tmp_path)
    runner.archive_handoffs(state)
    state.next_report("coder").write_text("h")
    runner.archive_handoffs(state)
    destination = state.folder / "handoffs-stamp%Y%m%dT%H%M%S"
    assert archived == [(state.config, "task", None), (state.config, "task", destination)]
    assert (destination / "01-coder.md").read_text() == "h"
    assert not state.handoffs.exists()
