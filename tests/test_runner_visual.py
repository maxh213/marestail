import itertools
import time
from pathlib import Path
from typing import Any

import pytest

from marestail import prompts, reported, runner
from marestail import route as dandelion
from marestail.config import Config
from marestail.gates import visual as visual_gate
from marestail.gates.visual import Report, Reproduction
from marestail.pipeline import Judge, Worker
from marestail.route import Choice
from marestail.runner import Run

VISUAL = Judge("visual", "visual", bounce_to="coder", optional=True, targets=("coder", "specifier"))
CRITIC = Judge("critic", None, bounce_to="specifier")
PERF = Judge("perf", None, bounce_to="coder", pinned_bounce=True)
DONE = '{"total_cost_usd": 0.5, "num_turns": 2, "result": "ok"}'
LIMIT = '{"is_error": true, "result": "rate limit exceeded"}'


class Recorder:
    def __init__(self, *replies: Any) -> None:
        self.replies = list(replies)
        self.calls: list[tuple[Any, ...]] = []

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append(args + tuple(kwargs.items()))
        return self.replies.pop(0) if len(self.replies) > 1 else (self.replies[0] if self.replies else None)


def patch(monkeypatch: pytest.MonkeyPatch, target: object, name: str, *replies: Any) -> Recorder:
    recorder = Recorder(*replies)
    monkeypatch.setattr(target, name, recorder)
    return recorder


def make_state(root: Path, visual: dict[str, Any] | None = None, **fields: Any) -> Run:
    base: dict[str, Any] = {"model": "gpt-9", "effort": "high", "agent": "cursor", "retries": 1}
    return Run(config=Config(root=root, raw={"visual": visual or {}}), task=root / "tasks" / "t.md", **{**base, **fields})


@pytest.fixture
def sessions(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    monkeypatch.setattr(runner, "LIMIT_WAIT_SECONDS", 0)
    ticks = itertools.count(0, 30)
    monkeypatch.setattr(time, "time", lambda: next(ticks))
    patch(monkeypatch, time, "sleep")
    seen = Recorder((0, DONE))

    def backend(state: Run, name: str, prompt: str, prompt_file: Path) -> tuple[int, str]:
        reply: tuple[int, str] = seen(name, state.model, state.effort, state.route, prompt)
        return reply

    monkeypatch.setattr(runner, "run_backend", backend)
    return seen


def built(state: Run) -> Any:
    return lambda: f"prompt for {runner.resolve_agent(state)}"


def test_gate_tier_runs_the_visual_gate_alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert runner.gate_tier("visual") == ("qa", {"visual"})
    assert runner.gate_tier("full") == ("full", None)
    gates = patch(monkeypatch, runner, "run_gates", [])
    make_state(tmp_path).gates("visual")
    assert gates.calls == [("qa", False, {"visual"}, set(), False, False)]


def test_visual_skips_without_a_block_and_only_for_itself(tmp_path: Path) -> None:
    state = make_state(tmp_path)
    assert runner.skip_reason(state, VISUAL) == "visual: no qa/t.md; skipping"
    assert runner.skip_reason(state, CRITIC) == ""
    (tmp_path / "qa").mkdir()
    (tmp_path / "qa" / "t.md").write_text("```visual\nroute: /\nselector: #w\n```\n")
    assert runner.skip_reason(state, VISUAL) == ""


def test_visual_disabled_uses_the_optional_judge_line(tmp_path: Path) -> None:
    assert runner.skip_reason(make_state(tmp_path, {"enabled": False}), VISUAL) == "visual disabled in marestail.toml; skipping"


@pytest.mark.parametrize(
    ("judge", "target", "expected"),
    [
        (VISUAL, "coder", "coder"),
        (VISUAL, "specifier", "specifier"),
        (VISUAL, "hardener", None),
        (VISUAL, None, None),
        (CRITIC, "hardener", "hardener"),
        (PERF, "specifier", None),
    ],
)
def test_bounce_target_keeps_only_the_allowed_roles(judge: Judge, target: str | None, expected: str | None) -> None:
    assert runner.bounce_target(judge, target) == expected


def test_gated_verdict_sends_a_hardener_bounce_from_visual_to_the_coder(tmp_path: Path) -> None:
    report = tmp_path / "r.md"
    report.write_text("VERDICT: BOUNCE hardener")
    assert runner.gated_verdict(VISUAL, report, ("", True, []), ("BOUNCE", "hardener")) == ("BOUNCE", None, "VERDICT: BOUNCE hardener")


def test_review_for_visual_shows_pictures_to_a_backend_that_can_read_them(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    section = patch(monkeypatch, visual_gate, "judge_section", "SECTION")
    assert runner.review_for(make_state(tmp_path), VISUAL) == {"Visual": "SECTION"}
    assert runner.review_for(make_state(tmp_path, agent="kilo"), VISUAL) == {"Visual": "SECTION"}
    assert [call[1:] for call in section.calls] == [("t", ""), ("t", "kilo")]
    assert all(isinstance(call[0], Config) for call in section.calls)
    assert runner.review_for(make_state(tmp_path), CRITIC) is None


def test_judge_session_builds_the_visual_prompt_for_the_judge_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sessions: Recorder
) -> None:
    patch(monkeypatch, visual_gate, "judge_section", "SECTION")
    prompt = patch(monkeypatch, prompts, "judge_prompt", "PROMPT")
    patch(monkeypatch, runner, "head", "abc")
    patch(monkeypatch, runner, "discard_edits")
    state = make_state(tmp_path)
    assert runner.judge_session(state, VISUAL, tmp_path / "02-visual.md", "gate", None, "") == "abc"
    assert prompt.calls[0][-2] == {"Visual": "SECTION"}
    assert sessions.calls == [("claude", "claude-fable-5-1", None, None, "PROMPT")]


def test_ask_sends_other_judges_through_invoke(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    invoke = patch(monkeypatch, runner, "invoke")
    state = make_state(tmp_path)
    runner.ask(state, CRITIC, "01-critic", lambda: "P")
    assert invoke.calls == [(state, "01-critic", "P")]


def test_the_judge_model_looks_first_and_the_run_keeps_its_backend(tmp_path: Path, sessions: Recorder, capsys: Any) -> None:
    state = make_state(tmp_path, account_env={"K": "v"}, account="acct")
    runner.ask(state, VISUAL, "02-visual", built(state))
    assert sessions.calls == [("claude", "claude-fable-5-1", None, None, "prompt for claude")]
    assert (state.agent, state.model, state.effort, state.account_env, state.account) == ("cursor", "gpt-9", "high", {"K": "v"}, "acct")
    assert (state.judged_by, state.blind, state.unseen) == ("claude-fable-5-1", False, False)
    assert state.attempt_agent is not None
    assert state.attempt_agent["model"] == "claude-fable-5-1"
    assert (state.folder / "02-visual.prompt.md").read_text() == "prompt for claude"
    assert (state.folder / "02-visual.json").read_text() == DONE
    out = capsys.readouterr().out
    assert "   02-visual finished in " in out
    assert "out of usage" not in out


def test_a_configured_judge_model_is_used(tmp_path: Path, sessions: Recorder) -> None:
    state = make_state(tmp_path, {"judge_model": "claude-opus-5-5"})
    runner.ask(state, VISUAL, "02-visual", built(state))
    assert sessions.calls[0][1] == "claude-opus-5-5"
    assert state.judged_by == "claude-opus-5-5"


def test_an_out_of_usage_judge_model_falls_back_at_once(tmp_path: Path, sessions: Recorder, capsys: Any) -> None:
    sessions.replies = [(1, LIMIT), (0, DONE)]
    state = make_state(tmp_path)
    runner.ask(state, VISUAL, "02-visual", built(state))
    assert [call[:2] for call in sessions.calls] == [("claude", "claude-fable-5-1"), ("cursor", "gpt-9")]
    assert sessions.calls[1][-1] == "prompt for cursor"
    assert capsys.readouterr().out.splitlines()[0] == "visual: claude-fable-5-1 is out of usage; judging with cursor gpt-9"
    assert (state.attempt_waits, state.judged_by, state.blind, state.unseen) == ([], "gpt-9 high", False, False)


def test_a_fallback_that_cannot_read_images_is_blind(tmp_path: Path, sessions: Recorder, capsys: Any) -> None:
    sessions.replies = [(1, LIMIT), (0, DONE)]
    state = make_state(tmp_path, agent="kilo", model=None, effort=None)
    runner.ask(state, VISUAL, "02-visual", built(state))
    assert capsys.readouterr().out.splitlines()[0] == "visual: claude-fable-5-1 is out of usage; judging with kilo"
    assert sessions.calls[1][-1] == "prompt for kilo"
    assert (state.blind, state.unseen) == (True, True)


def test_a_routed_fallback_rebuilds_the_prompt_for_the_picked_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sessions: Recorder, capsys: Any
) -> None:
    choose = patch(monkeypatch, dandelion, "choose", (Choice(line="kilo/x kilo", backend="kilo", model="kilo/x", effort=None, env={}), ""))
    sessions.replies = [(1, LIMIT), (0, DONE)]
    state = make_state(tmp_path, agent=None, model=None, effort=None, route="dandelion/route")
    runner.ask(state, VISUAL, "02-visual", built(state))
    assert len(choose.calls) == 1
    assert sessions.calls[0][3] is None
    assert sessions.calls[1][0] == "kilo"
    assert sessions.calls[1][-1] == "prompt for kilo"
    assert (state.folder / "02-visual.prompt.md").read_text() == "prompt for kilo"
    out = capsys.readouterr().out.splitlines()
    assert out[:2] == ["visual: claude-fable-5-1 is out of usage; judging with dandelion/route", "   dandelion/route: kilo/x kilo"]
    assert (state.judged_by, state.unseen) == ("kilo/x", True)


def test_a_limited_fallback_waits_like_any_role(tmp_path: Path, sessions: Recorder, capsys: Any) -> None:
    sessions.replies = [(1, LIMIT), (1, LIMIT), (0, DONE)]
    state = make_state(tmp_path)
    runner.ask(state, VISUAL, "02-visual", built(state))
    assert [wait["reason"] for wait in state.attempt_waits] == ["rate-limit"]
    assert "   rate limited; waiting 0 min before retrying 02-visual" in capsys.readouterr().out


def test_invoke_once_keeps_the_prompt_without_a_rebuild(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sessions: Recorder) -> None:
    patch(monkeypatch, dandelion, "choose", (Choice(line="gpt-9 cursor", backend="cursor", model="gpt-9", effort=None, env={}), ""))
    state = make_state(tmp_path, agent=None, model=None, effort=None, route="dandelion/route")
    prompt_file = runner.saved_prompt(state, "x", "P")
    assert runner.invoke_once(state, "x", "P", prompt_file) == ("P", True)


def test_fallback_name_ends_at_the_backend_without_a_model(tmp_path: Path) -> None:
    assert runner.fallback_name(make_state(tmp_path)) == "cursor gpt-9"
    assert runner.fallback_name(make_state(tmp_path, model=None)) == "cursor"
    assert runner.fallback_name(make_state(tmp_path, route="dandelion/route")) == "dandelion/route"


def test_commit_verdict_stamps_the_judge_and_marks_a_blind_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    patch(monkeypatch, runner, "stage_writes")
    patch(monkeypatch, runner, "drop_ignored_since", {})
    patch(monkeypatch, runner, "restore_files")
    commits = patch(monkeypatch, runner, "record_commit")
    state = make_state(tmp_path, judged_by="kilo/x", blind=True)
    runner.commit_verdict(state, VISUAL, "abc", (runner.PASS, None, "ok"))
    assert commits.calls[0][1:] == ("visual verdict: PASS (geometry only, pictures not seen)", "ok", "visual", "kilo/x")
    assert capsys.readouterr().out == "   verdict PASS (geometry only, pictures not seen)\n"
    runner.commit_verdict(make_state(tmp_path), CRITIC, "abc", (runner.BOUNCE, "specifier", "no"))
    assert commits.calls[1][1:] == ("critic verdict: BOUNCE to specifier", "no", "critic", "gpt-9 high")


def test_reset_attempt_forgets_who_judged(tmp_path: Path) -> None:
    state = make_state(tmp_path, judged_by="x", blind=True, unseen=True)
    runner.reset_attempt(state)
    assert (state.judged_by, state.blind, state.unseen) == ("", False, True)


REPORT = Report("t", "/donate.html", "square", "#widget")
CAPTURE = Reproduction(REPORT, "abc", ["desktop"], {})


def test_bug_for_hands_the_run_to_the_reported_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sections = patch(monkeypatch, reported, "prompt_sections", {"Reported": "BODY"})
    state = make_state(tmp_path, agent="kilo", reproduction=Reproduction(REPORT, "abc", ["desktop"], {}))
    assert runner.bug_for(state, "critic") == {"Reported": "BODY"}
    assert sections.calls == [(state.config, state.reproduction, "critic", state.handoffs, "kilo")]


def test_verify_worker_asks_the_reported_module_about_the_handoff(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    problems = patch(monkeypatch, reported, "handoff_problems", ["missing ## Observed section in h.md"])
    for name in ("missing_handoff", "changed_paths", "frozen_problems", "hunk_problems", "audit_problems", "gate_problems"):
        patch(monkeypatch, runner, name, [])
    state = make_state(tmp_path, reproduction=CAPTURE)
    report = tmp_path / "01-specifier.md"
    assert runner.verify_worker(state, Worker("specifier", None), report, "before") == "missing ## Observed section in h.md"
    assert problems.calls == [(state.config, CAPTURE, "specifier", report)]


def test_verify_worker_rejects_a_reproduced_specifier_without_observed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("changed_paths", "frozen_problems", "hunk_problems", "audit_problems", "gate_problems"):
        patch(monkeypatch, runner, name, [])
    state = make_state(tmp_path, reproduction=CAPTURE)
    report = state.handoffs / "01-specifier.md"
    report.parent.mkdir(parents=True)
    report.write_text("spec written\n")
    problems = runner.verify_worker(state, Worker("specifier", None), report, "before")
    assert problems == "missing ## Observed section in .marestail/handoffs/t/01-specifier.md"
    assert runner.verify_worker(make_state(tmp_path), Worker("specifier", None), report, "before") == ""


@pytest.mark.parametrize(
    ("role", "sections"),
    [("specifier", {"Reported": f"BODY\n{reported.ASK_OBSERVED}"}), ("coder", {})],
)
def test_worker_prompt_carries_the_reported_bug_for_the_specifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, role: str, sections: dict[str, str]
) -> None:
    patch(monkeypatch, visual_gate, "reported_section", "BODY")
    prompt = patch(monkeypatch, prompts, "worker_prompt", "PROMPT")
    for name in ("head", "invoke", "record_attempt", "drop_ignored_since", "fold_handoff", "restore_files"):
        patch(monkeypatch, runner, name)
    patch(monkeypatch, runner, "verify_worker", "")
    runner.worker_attempt(make_state(tmp_path, reproduction=CAPTURE), Worker(role, None), "", 1, "before")
    assert prompt.calls[0][-1] == sections


def test_critic_prompt_carries_the_reported_bug_and_the_observed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    patch(monkeypatch, visual_gate, "reported_section", "BODY")
    prompt = patch(monkeypatch, prompts, "judge_prompt", "PROMPT")
    state = make_state(tmp_path, reproduction=CAPTURE)
    state.handoffs.mkdir(parents=True)
    (state.handoffs / "01-specifier.md").write_text("## Observed\nthe frame is 440px\n")
    runner.built_judge_prompt(state, CRITIC, state.handoffs / "02-critic.md", ("", "", ""))
    runner.built_judge_prompt(state, PERF, state.handoffs / "03-perf.md", ("", "", ""))
    assert [call[-1] for call in prompt.calls] == [{"Reported": "BODY", "Observed": "the frame is 440px"}, {}]
