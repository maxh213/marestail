import json
import os
import re
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from marestail import prompts, runner, timeline
from marestail.config import Config
from marestail.pipeline import Judge, Worker
from marestail.report import Result
from marestail.route import Choice
from marestail.runner import JudgeProgress, Run
from tests.conftest import commit_all, git

ROOT = Path(__file__).resolve().parent.parent
CODER = Worker("coder", None)
CRITIC = Judge("critic", None, bounce_to="specifier")


@pytest.fixture
def repo(git_repo: Path) -> Path:
    (git_repo / "tasks").mkdir(exist_ok=True)
    (git_repo / "tasks" / "t.md").write_text("# t\n")
    (git_repo / ".gitignore").write_text(".marestail/\n")
    commit_all(git_repo, "init")
    return git_repo


def make_state(root: Path, **fields: Any) -> Run:
    base: dict[str, Any] = {
        "config": Config(root=root, raw={"git": {"base": "main"}}),
        "task": root / "tasks" / "t.md",
        "model": "m",
        "effort": "high",
        "agent": "claude",
        "retries": 1,
    }
    return Run(**{**base, **fields})


def test_utc_now_is_utc_iso(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Any] = []
    real_now = datetime.now

    class DatetimeProxy:
        @staticmethod
        def now(tz: Any = None) -> datetime:
            seen.append(tz)
            return real_now(tz)

    monkeypatch.setattr(timeline, "datetime", DatetimeProxy)
    stamp = timeline.utc_now()
    assert seen == [UTC]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", stamp)
    parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == UTC.utcoffset(None)
    assert abs((datetime.now(UTC) - parsed).total_seconds()) < 2


def test_gate_entries() -> None:
    results = [Result(gate="sonar", ok=False, summary="fail", seconds=41.0)]
    assert timeline._gate_entries(results) == [{"name": "sonar", "seconds": 41.0, "ok": False}]


def test_first_paragraph() -> None:
    assert timeline._first_paragraph("one\n\ntwo") == "one"
    assert timeline._first_paragraph("  only  ") == "only"
    assert timeline._first_paragraph("a\n\nb\n\nc") == "a"
    assert timeline._first_paragraph("lead\n\n\ntrail") == "lead"


def test_load_document_overwrites_task(tmp_path: Path) -> None:
    (tmp_path / "timeline.json").write_text(json.dumps({"task": "old", "steps": [{"id": "01"}]}) + "\n")
    loaded = timeline._load_document(tmp_path, "new")
    assert loaded["task"] == "new"
    assert loaded["steps"] == [{"id": "01"}]
    assert list(loaded.keys()) == ["task", "steps"]


def test_done_line_prefers_handoff(tmp_path: Path) -> None:
    handoff = tmp_path / "h.md"
    handoff.write_text("coded the adder\n\nmore\n")
    assert timeline._done_line(handoff, [{"hash": "a", "subject": "subj"}], {"summary": "sum"}) == "coded the adder"


def test_handoff_paragraph_missing(tmp_path: Path) -> None:
    assert timeline._handoff_paragraph(tmp_path / "missing") == ""


def test_commit_or_summary() -> None:
    assert timeline._commit_or_summary([{"hash": "a", "subject": "subj"}], {"summary": "sum"}) == "subj"
    assert timeline._commit_or_summary([], {"summary": "sum"}) == "sum"
    assert timeline._commit_or_summary([], None) == ""
    assert timeline._commit_or_summary([], {}) == ""
    assert timeline._commit_or_summary([], {"other": "x"}) == ""


def test_done_line_falls_back_to_commit(tmp_path: Path) -> None:
    assert timeline._done_line(tmp_path / "missing", [{"hash": "a", "subject": "subj"}], {"summary": "sum"}) == "subj"


def test_done_line_falls_back_to_summary(tmp_path: Path) -> None:
    assert timeline._done_line(tmp_path / "missing", [], {"summary": "sum"}) == "sum"


def test_done_line_empty(tmp_path: Path) -> None:
    assert timeline._done_line(tmp_path / "missing", [], None) == ""


def test_verdict_text() -> None:
    assert timeline.verdict_text("PASS", None) == "PASS"
    assert timeline.verdict_text("BOUNCE", "specifier") == "BOUNCE specifier"
    assert timeline.verdict_text("AUTHOR", None) == "AUTHOR"
    assert timeline.verdict_text("BOUNCE", None) == "BOUNCE"


def test_ordered_step_and_build() -> None:
    agent = {"backend": "claude", "model": "m", "effort": None, "account": None, "minutes": 0.0, "summary": "ok"}
    step = timeline._build_step("01-coder", "coder", 1, "a", "b", [], [], agent, None, [], [], "done")
    assert list(step.keys()) == ["id", "role", "attempt", "started_at", "ended_at", "gate", "waits", "agent", "commits", "files", "done"]
    assert step["agent"] == agent
    assert "verdict" not in step
    judged = timeline._build_step("01-hardener", "hardener", 1, "a", "b", [], [], None, "BOUNCE", [], [], "x")
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
    assert judged["verdict"] == "BOUNCE"
    assert "agent" not in judged
    both = timeline._build_step("01-p", "perf", 1, "a", "b", [], [], agent, "AUTHOR", [], [], "x")
    assert list(both.keys()).index("agent") < list(both.keys()).index("verdict")
    assert both["agent"] == agent
    assert both["verdict"] == "AUTHOR"


def test_write_and_load(tmp_path: Path) -> None:
    timeline.record(tmp_path, "t", "01-coder", "coder", 1, "a", [], [], None, None, [], [], tmp_path / "missing")
    loaded = timeline._load_document(tmp_path, "t")
    assert loaded["task"] == "t"
    assert len(loaded["steps"]) == 1
    assert loaded["steps"][0]["id"] == "01-coder"
    assert "01-coder" in (tmp_path / "timeline.md").read_text()
    empty = timeline._load_document(tmp_path / "missing", "u")
    assert empty == {"task": "u", "steps": []}


def test_record_passes_every_field(tmp_path: Path) -> None:
    agent = {"backend": "claude", "model": "m", "effort": "high", "account": None, "minutes": 0.1, "summary": "ok"}
    waits = [{"reason": "rate-limit", "seconds": 0}]
    gates = [Result(gate="sonar", ok=False, summary="fail", seconds=41.0)]
    commits = [{"hash": "abc", "subject": "subj"}]
    files = ["src.py"]
    started = "2026-01-02T03:04:05.678Z"
    timeline.record(tmp_path, "t", "01-coder", "coder", 2, started, gates, waits, agent, "PASS", commits, files, tmp_path / "missing")
    step = json.loads((tmp_path / "timeline.json").read_text())["steps"][0]
    assert step["id"] == "01-coder"
    assert step["role"] == "coder"
    assert step["attempt"] == 2
    assert step["started_at"] == started
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", step["ended_at"])
    assert step["gate"] == [{"name": "sonar", "seconds": 41.0, "ok": False}]
    assert step["waits"] == waits
    assert step["agent"] == agent
    assert step["verdict"] == "PASS"
    assert step["commits"] == commits
    assert step["files"] == files
    assert step["done"] == "subj"


def test_record_done_from_agent_when_no_handoff_or_commits(tmp_path: Path) -> None:
    agent = {"summary": "from-agent"}
    timeline.record(tmp_path, "t", "01-c", "coder", 1, "a", [], [], agent, None, [], [], tmp_path / "missing")
    assert json.loads((tmp_path / "timeline.json").read_text())["steps"][0]["done"] == "from-agent"


def test_format_value() -> None:
    assert timeline._format_value([1]) == "[1]"
    assert timeline._format_value({"a": 1}) == '{"a": 1}'
    assert timeline._format_value("x\ny") == "x y"
    assert timeline._format_value({"a": "café"}) == '{"a": "caf\\u00e9"}'


def test_render_markdown() -> None:
    step = timeline._build_step("01-coder", "coder", 1, "a", "b", [], [], None, None, [], [], "done")
    text = timeline._render_markdown([step])
    assert text.startswith("## 01-coder (attempt 1)\n\n- role: coder\n")
    assert "- id:" not in text
    assert "- attempt:" not in text
    assert text.endswith("- done: done\n")
    assert "XXXX" not in text
    second = timeline._build_step("02-coder", "coder", 2, "c", "d", [], [], None, None, [], [], "two")
    joined = timeline._render_markdown([step, second])
    assert "## 01-coder (attempt 1)\n\n" in joined
    assert "\n## 02-coder (attempt 2)\n" in joined
    assert "XX\nXX" not in joined


def test_write_files_indent(tmp_path: Path) -> None:
    timeline._write_files(tmp_path, "t", [{"id": "01-coder", "attempt": 1}])
    raw = (tmp_path / "timeline.json").read_text()
    assert raw.startswith('{\n  "task": "t",\n  "steps": [\n    {\n      "id": "01-coder",\n      "attempt": 1\n    }\n  ]\n}\n')


def test_outcome_verdict() -> None:
    assert runner.outcome_verdict(None) is None
    assert runner.outcome_verdict(("PASS", None, "")) == "PASS"
    assert runner.outcome_verdict(("BOUNCE", "coder", "")) == "BOUNCE coder"
    assert runner.outcome_verdict(("AUTHOR", None, "")) == "AUTHOR"
    assert runner.outcome_verdict(("BOUNCE", None, "")) == "BOUNCE"


def test_commit_entry() -> None:
    assert runner.commit_entry("abc\0subject") == {"hash": "abc", "subject": "subject"}
    assert runner.commit_entry("abc\0subj\0extra") == {"hash": "abc", "subject": "subj\0extra"}


def test_reset_and_note_wait(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = make_state(tmp_path)
    state.attempt_agent = {"backend": "claude"}
    state.attempt_waits = [{"reason": "rate-limit", "seconds": 0}]
    runner.reset_attempt(state)
    assert state.attempt_agent is None
    assert state.attempt_waits == []
    monkeypatch.setattr(runner, "LIMIT_WAIT_SECONDS", 0)
    runner.note_wait(state, "rate-limit")
    assert state.attempt_waits == [{"reason": "rate-limit", "seconds": 0}]
    runner.note_wait(state, "dandelion-unrouted")
    assert state.attempt_waits[-1] == {"reason": "dandelion-unrouted", "seconds": 0}


def test_remember_agent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = make_state(tmp_path, account="claude")
    monkeypatch.setattr(time, "time", lambda: 90.0)
    runner.remember_agent(state, "claude", 30.0, "summary")
    assert state.attempt_agent == {
        "backend": "claude",
        "model": "m",
        "effort": "high",
        "account": "claude",
        "minutes": 1.0,
        "summary": "summary",
    }
    empty = make_state(tmp_path, effort=None, model=None, agent="claude", account="")
    runner.remember_agent(empty, "claude", 90.0, "")
    assert empty.attempt_agent is not None
    assert empty.attempt_agent["effort"] is None
    assert empty.attempt_agent["account"] is None
    assert empty.attempt_agent["minutes"] == 0.0


def test_attempt_commits_and_files(repo: Path) -> None:
    before = runner.head(Config(root=repo, raw={"git": {"base": "main"}}))
    (repo / "extra.py").write_text("x\n")
    git(repo, "add", "extra.py")
    git(repo, "commit", "-qm", "extra")
    config = Config(root=repo, raw={"git": {"base": "main"}})
    commits = runner.attempt_commits(config, before)
    assert len(commits) == 1
    assert commits[0]["subject"] == "extra"
    assert len(commits[0]["hash"]) == 40
    assert re.fullmatch(r"[0-9a-f]{40}", commits[0]["hash"])
    assert "extra.py" in runner.attempt_files(config, before)


def test_record_attempt_writes_timeline(repo: Path) -> None:
    state = make_state(repo)
    report = state.next_report("coder")
    report.write_text("coded\n")
    before = runner.head(state.config)
    (repo / "touched.py").write_text("y\n")
    git(repo, "add", "touched.py")
    git(repo, "commit", "-qm", "touch")
    runner.reset_attempt(state)
    runner.remember_agent(state, "claude", time.time(), "ok")
    started = timeline.utc_now()
    waits = [{"reason": "rate-limit", "seconds": 0}]
    state.attempt_waits = list(waits)
    runner.record_attempt(state, report, "coder", 1, started, before, [], None)
    data = json.loads((state.folder / "timeline.json").read_text())
    step = data["steps"][0]
    assert data["task"] == "t"
    assert step["id"] == report.stem
    assert step["role"] == "coder"
    assert step["attempt"] == 1
    assert step["started_at"] == started
    assert step["ended_at"].endswith("Z")
    assert step["waits"] == waits
    assert step["agent"] is not None
    assert step["agent"]["backend"] == "claude"
    assert step["agent"]["effort"] == "high"
    assert step["commits"]
    assert step["commits"][0]["subject"] == "touch"
    assert len(step["commits"][0]["hash"]) == 40
    assert "touched.py" in step["files"]
    assert step["done"] == "coded"
    assert "verdict" not in step


def test_record_attempt_writes_judge_verdict(repo: Path) -> None:
    state = make_state(repo)
    report = state.next_report("hardener")
    report.write_text("VERDICT: BOUNCE\n")
    before = runner.head(state.config)
    gates = [Result(gate="sonar", ok=False, summary="fail", seconds=41.0)]
    started = timeline.utc_now()
    runner.record_attempt(state, report, "hardener", 2, started, before, gates, "BOUNCE")
    step = json.loads((state.folder / "timeline.json").read_text())["steps"][0]
    assert step["id"] == report.stem
    assert step["role"] == "hardener"
    assert step["attempt"] == 2
    assert step["started_at"] == started
    assert step["gate"] == [{"name": "sonar", "seconds": 41.0, "ok": False}]
    assert step["verdict"] == "BOUNCE"


def test_worker_attempt_records_timeline(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = make_state(repo)
    monkeypatch.setattr(prompts, "worker_prompt", lambda *args, **kwargs: "PROMPT")

    def invoke_write(run: Run, label: str, prompt: str) -> None:
        (repo / "src.py").write_text("x\n")
        git(repo, "add", "src.py")
        git(repo, "commit", "-qm", "add src")
        (run.handoffs / f"{label}.md").write_text("coded the adder\n\nmore\n")
        run.attempt_agent = {
            "backend": "claude",
            "model": "m",
            "effort": "high",
            "account": None,
            "minutes": 0.1,
            "summary": "ok",
        }

    monkeypatch.setattr(runner, "invoke", invoke_write)
    monkeypatch.setattr(runner, "verify_worker", lambda *args: "")
    monkeypatch.setattr(runner, "drop_ignored_since", lambda *args: {})
    monkeypatch.setattr(runner, "fold_handoff", lambda *args: None)
    monkeypatch.setattr(runner, "restore_files", lambda *args: None)
    before = runner.head(state.config)
    assert runner.worker_attempt(state, CODER, "", 1, before) == ""
    step = json.loads((state.folder / "timeline.json").read_text())["steps"][0]
    assert step["id"].endswith("-coder")
    assert step["role"] == "coder"
    assert step["attempt"] == 1
    assert step["started_at"]
    assert step["started_at"].endswith("Z")
    assert step["commits"]
    assert "src.py" in step["files"]
    assert step["done"] == "coded the adder"
    assert step["agent"]["backend"] == "claude"


def test_judged_records_timeline(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = make_state(repo)
    gates = [Result(gate="sonar", ok=False, summary="fail", seconds=41.0)]
    monkeypatch.setattr(runner, "prepare_perf", lambda *args: None)

    def attempt(*args: Any) -> Any:
        (repo / "judge.py").write_text("j\n")
        git(repo, "add", "judge.py")
        git(repo, "commit", "-qm", "judge edit")
        return (("BOUNCE", "coder", "no"), "")

    monkeypatch.setattr(runner, "judge_attempt", attempt)
    progress = JudgeProgress()
    outcome = runner.judged(state, CRITIC, ("gate", False, gates), progress, 3)
    assert outcome == ("BOUNCE", "coder", "no")
    step = json.loads((state.folder / "timeline.json").read_text())["steps"][0]
    assert step["id"].endswith("-critic")
    assert step["role"] == "critic"
    assert step["attempt"] == 3
    assert step["started_at"]
    assert step["started_at"].endswith("Z")
    assert step["gate"] == [{"name": "sonar", "seconds": 41.0, "ok": False}]
    assert step["verdict"] == "BOUNCE coder"
    assert step["commits"]
    assert step["commits"][0]["subject"] == "judge edit"
    assert len(step["commits"][0]["hash"]) == 40
    assert "judge.py" in step["files"]


def test_judged_author_still_records(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = make_state(repo)
    monkeypatch.setattr(runner, "prepare_perf", lambda *args: None)
    monkeypatch.setattr(runner, "judge_attempt", lambda *args: (("AUTHOR", None, ""), "fb"))
    progress = JudgeProgress(author_left=0)
    assert runner.judged(state, CRITIC, ("", True, []), progress, 1) is None
    step = json.loads((state.folder / "timeline.json").read_text())["steps"][0]
    assert step["verdict"] == "AUTHOR"
    assert step["attempt"] == 1
    assert step["role"] == "critic"


def test_run_session_records_agent_and_rate_limit(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "LIMIT_WAIT_SECONDS", 0)
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    state = make_state(repo)
    state.folder.mkdir(parents=True, exist_ok=True)
    prompt = state.folder / "x.prompt.md"
    prompt.write_text("p")
    monkeypatch.setattr(runner, "run_backend", lambda *args: (0, '{"result": "ok", "num_turns": 1, "total_cost_usd": 0}'))
    assert runner.run_session(state, "x", "p", prompt) is True
    assert state.attempt_agent is not None
    assert state.attempt_agent["backend"] == "claude"
    assert state.attempt_agent["summary"]
    assert state.attempt_agent["effort"] == "high"
    assert state.attempt_agent["minutes"] >= 0
    assert state.attempt_agent["minutes"] < 10
    runner.reset_attempt(state)
    monkeypatch.setattr(runner, "run_backend", lambda *args: (1, "rate limit exceeded"))
    assert runner.run_session(state, "x", "p", prompt) is False
    assert state.attempt_waits == [{"reason": "rate-limit", "seconds": 0}]


def test_run_session_grok_locked_remembers_empty_summary(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = make_state(repo, agent="grok")
    state.folder.mkdir(parents=True, exist_ok=True)
    prompt = state.folder / "x.prompt.md"
    prompt.write_text("p")
    monkeypatch.setattr(runner, "run_backend", lambda *args: (1, "always-approve is disabled by policy"))
    assert runner.run_session(state, "x", "p", prompt) is True
    assert state.attempt_agent is not None
    assert state.attempt_agent["backend"] == "grok"
    assert state.attempt_agent["summary"] == ""
    assert state.attempt_agent["minutes"] >= 0


def test_invoke_once_notes_dandelion_wait(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from marestail import route as dandelion

    monkeypatch.setattr(runner, "LIMIT_WAIT_SECONDS", 0)
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    state = make_state(repo, agent=None, model=None, effort=None, route="dandelion/route")
    state.folder.mkdir(parents=True, exist_ok=True)
    prompt = state.folder / "x.prompt.md"
    monkeypatch.setattr(dandelion, "choose", lambda *args: (None, "no quota left"))
    prompt_text, finished = runner.invoke_once(state, "x", "hi", prompt)
    assert finished is False
    assert prompt_text == "hi"
    assert state.attempt_waits == [{"reason": "dandelion-unrouted", "seconds": 0}]


def test_routed_sets_account_from_line(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from marestail import route as dandelion

    choice = Choice(line="opus high claude", backend="claude", model="opus", effort="high", env={"K": "v"})
    monkeypatch.setattr(dandelion, "choose", lambda *args: (choice, ""))
    state = make_state(repo, agent=None, model=None, effort=None, route="dandelion/route")
    prompt, unrouted = runner.routed(state, "Commit as [dandelion/route] here")
    assert unrouted == ""
    assert state.account == "claude"
    assert "opus high" in prompt
    bare = Choice(line="opus high claude", backend="claude", model="opus", effort="high", env={})
    monkeypatch.setattr(dandelion, "choose", lambda *args: (bare, ""))
    state2 = make_state(repo, agent=None, model=None, effort=None, route="dandelion/route")
    runner.routed(state2, "Commit as [dandelion/route] here")
    assert state2.account == ""


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
