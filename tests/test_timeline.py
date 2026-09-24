import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from marestail import runner, timeline
from marestail.config import Config
from marestail.report import Result
from marestail.runner import Run
from tests.conftest import commit_all, git

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def repo(git_repo: Path) -> Path:
    (git_repo / "tasks").mkdir(exist_ok=True)
    (git_repo / "tasks" / "t.md").write_text("# t\n")
    (git_repo / ".gitignore").write_text(".marestail/\n")
    commit_all(git_repo, "init")
    return git_repo


def test_utc_now_ends_with_z() -> None:
    stamp = timeline.utc_now()
    assert stamp.endswith("Z")
    assert "T" in stamp


def test_gate_entries() -> None:
    results = [Result(gate="sonar", ok=False, summary="fail", seconds=41.0)]
    assert timeline.gate_entries(results) == [{"name": "sonar", "seconds": 41.0, "ok": False}]


def test_first_paragraph() -> None:
    assert timeline.first_paragraph("one\n\ntwo") == "one"
    assert timeline.first_paragraph("  only  ") == "only"


def test_done_line_prefers_handoff(tmp_path: Path) -> None:
    handoff = tmp_path / "h.md"
    handoff.write_text("coded the adder\n\nmore\n")
    assert timeline.done_line(handoff, [{"hash": "a", "subject": "subj"}], {"summary": "sum"}) == "coded the adder"


def test_handoff_paragraph_missing(tmp_path: Path) -> None:
    assert timeline.handoff_paragraph(tmp_path / "missing") == ""


def test_commit_or_summary() -> None:
    assert timeline.commit_or_summary([{"hash": "a", "subject": "subj"}], {"summary": "sum"}) == "subj"
    assert timeline.commit_or_summary([], {"summary": "sum"}) == "sum"
    assert timeline.commit_or_summary([], None) == ""


def test_done_line_falls_back_to_commit(tmp_path: Path) -> None:
    assert timeline.done_line(tmp_path / "missing", [{"hash": "a", "subject": "subj"}], {"summary": "sum"}) == "subj"


def test_done_line_falls_back_to_summary(tmp_path: Path) -> None:
    assert timeline.done_line(tmp_path / "missing", [], {"summary": "sum"}) == "sum"


def test_done_line_empty(tmp_path: Path) -> None:
    assert timeline.done_line(tmp_path / "missing", [], None) == ""


def test_verdict_text() -> None:
    assert timeline.verdict_text("PASS", None) == "PASS"
    assert timeline.verdict_text("BOUNCE", "specifier") == "BOUNCE specifier"
    assert timeline.verdict_text("AUTHOR", None) == "AUTHOR"


def test_ordered_step_and_build() -> None:
    step = timeline.build_step(
        "01-coder",
        "coder",
        1,
        "a",
        "b",
        [],
        [],
        {"backend": "claude", "model": "m", "effort": None, "account": None, "minutes": 0.0, "summary": "ok"},
        None,
        [],
        [],
        "done",
    )
    assert list(step.keys()) == ["id", "role", "attempt", "started_at", "ended_at", "gate", "waits", "agent", "commits", "files", "done"]
    judged = timeline.build_step("01-hardener", "hardener", 1, "a", "b", [], [], None, "BOUNCE", [], [], "x")
    assert list(judged.keys()) == [
        "id",
        "role",
        "attempt",
        "started_at",
        "ended_at",
        "gate",
        "waits",
        "verdict",
        "commits",
        "files",
        "done",
    ]


def test_write_and_load(tmp_path: Path) -> None:
    step = timeline.build_step("01-coder", "coder", 1, "a", "b", [], [], None, None, [], [], "done")
    timeline.append_step(tmp_path, "t", step)
    loaded = timeline.load_document(tmp_path, "t")
    assert loaded == {"task": "t", "steps": [step]}
    assert "01-coder" in (tmp_path / "timeline.md").read_text()
    empty = timeline.load_document(tmp_path / "missing", "u")
    assert empty == {"task": "u", "steps": []}


def test_format_value() -> None:
    assert timeline.format_value([1]) == "[1]"
    assert timeline.format_value({"a": 1}) == '{"a": 1}'
    assert timeline.format_value("x\ny") == "x y"


def test_render_markdown() -> None:
    step = timeline.build_step("01-coder", "coder", 1, "a", "b", [], [], None, None, [], [], "done")
    text = timeline.render_markdown([step])
    assert "## 01-coder (attempt 1)" in text
    assert "- role: coder" in text


def test_outcome_verdict() -> None:
    assert runner.outcome_verdict(None) is None
    assert runner.outcome_verdict(("PASS", None, "")) == "PASS"
    assert runner.outcome_verdict(("BOUNCE", "coder", "")) == "BOUNCE coder"


def test_commit_entry() -> None:
    assert runner.commit_entry("abc\0subject") == {"hash": "abc", "subject": "subject"}


def test_reset_and_note_wait(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = Run(config=Config(root=tmp_path, raw={}), task=tmp_path / "t.md", model=None, retries=1)
    state.attempt_agent = {"backend": "claude"}
    state.attempt_waits = [{"reason": "rate-limit", "seconds": 0}]
    runner.reset_attempt(state)
    assert state.attempt_agent is None
    assert state.attempt_waits == []
    monkeypatch.setattr(runner, "LIMIT_WAIT_SECONDS", 0)
    runner.note_wait(state, "rate-limit")
    assert state.attempt_waits == [{"reason": "rate-limit", "seconds": 0}]


def test_remember_agent(tmp_path: Path) -> None:
    state = Run(config=Config(root=tmp_path, raw={}), task=tmp_path / "t.md", model="m", retries=1, agent="claude", account="claude")
    runner.remember_agent(state, "claude", 0.0, "summary")
    assert state.attempt_agent is not None
    assert state.attempt_agent["backend"] == "claude"
    assert state.attempt_agent["model"] == "m"
    assert state.attempt_agent["account"] == "claude"
    assert state.attempt_agent["summary"] == "summary"
    assert state.attempt_agent["minutes"] >= 0


def test_attempt_commits_and_files(repo: Path) -> None:
    before = runner.head(Config(root=repo, raw={"git": {"base": "main"}}))
    (repo / "extra.py").write_text("x\n")
    git(repo, "add", "extra.py")
    git(repo, "commit", "-qm", "extra")
    config = Config(root=repo, raw={"git": {"base": "main"}})
    commits = runner.attempt_commits(config, before)
    assert len(commits) == 1
    assert commits[0]["subject"] == "extra"
    assert "extra.py" in runner.attempt_files(config, before)


def test_record_attempt_writes_timeline(repo: Path) -> None:
    state = Run(
        config=Config(root=repo, raw={"git": {"base": "main"}}), task=repo / "tasks" / "t.md", model=None, retries=1, agent="claude"
    )
    report = state.next_report("coder")
    report.write_text("coded\n")
    before = runner.head(state.config)
    runner.reset_attempt(state)
    runner.remember_agent(state, "claude", 0.0, "ok")
    runner.record_attempt(state, report, "coder", 1, timeline.utc_now(), before, [], None)
    data = json.loads((state.folder / "timeline.json").read_text())
    assert data["task"] == "t"
    assert data["steps"][0]["role"] == "coder"
    assert data["steps"][0]["done"] == "coded"


def test_watch_diagnostic_script_passes() -> None:
    env = {key: value for key, value in os.environ.items() if key not in {"MARESTAIL_AGENT", "AGENT", "MODEL", "EFFORT"}}
    completed = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "test-watch.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert completed.returncode == 0
    assert "ok" in completed.stdout.strip().splitlines()[-1]


def test_timeline_diagnostic_passes_entrypoint() -> None:
    text = (ROOT / "tools" / "test-timeline.py").read_text()
    assert "def timeline_diagnostic_passes()" in text
    main = text.split('if __name__ == "__main__":', 1)[1]
    assert "timeline_diagnostic_passes()" in main
    assert 'print("timeline ok")' in main
