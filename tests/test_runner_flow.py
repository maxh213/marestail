import contextlib
import itertools
import os
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from marestail import config as config_module
from marestail import prompts, runner
from marestail import route as dandelion
from marestail.config import Config
from marestail.perf import db as perf_db
from marestail.perf import hygiene as perf_hygiene
from marestail.perf import review as perf_review
from marestail.perf import samples as perf_samples
from marestail.perf import settings as perf_settings
from marestail.perf import trees as perf_trees
from marestail.perf.trees import Session, Tree
from marestail.pipeline import Judge, Worker, find
from marestail.report import Result
from marestail.route import Choice
from marestail.runner import JudgeProgress, Run

CRITIC = Judge("critic", None, bounce_to="specifier")
PERF = Judge("perf", None, bounce_to="coder", writes=("perf/**",), pinned_bounce=True, optional=True)
CODER = Worker("coder", None)


def make_state(root: Path, raw: dict[str, Any] | None = None, **fields: Any) -> Run:
    base: dict[str, Any] = {"model": "m", "effort": "e", "agent": "claude", "retries": 1}
    return Run(config=Config(root=root, raw=raw or {}), task=root / "tasks" / "task.md", **{**base, **fields})


class Recorder:
    def __init__(self, *replies: Any) -> None:
        self.replies = list(replies)
        self.calls: list[tuple[Any, ...]] = []

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        if args and args[0] is None:
            raise TypeError("arg")
        self.calls.append(args + tuple(kwargs.items()))
        return self.replies.pop(0) if len(self.replies) > 1 else (self.replies[0] if self.replies else None)


def patch(monkeypatch: pytest.MonkeyPatch, target: object, name: str, *replies: Any) -> Recorder:
    recorder = Recorder(*replies)
    monkeypatch.setattr(target, name, recorder)
    return recorder


def test_pick_scope_focus_paths_enable_changed_scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "resolve_focus", lambda config, paths: paths)
    monkeypatch.setattr(runner, "hook_focus", lambda config: set())
    config = Config(root=tmp_path, raw={})
    changed, hard, focused = runner.pick_scope(config, None, ["src/a.py"])
    assert (changed, hard, focused) == (True, False, {"src/a.py"})
    none, _, empty = runner.pick_scope(config, None, None)
    assert (none, empty) == (False, set())


def test_disabled_reads_the_enabled_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[Any, ...]] = []

    def get(_self: Config, section: str, key: str, default: Any = None) -> bool:
        seen.append((section, key, default))
        return True

    monkeypatch.setattr(Config, "get", get)
    assert runner.disabled(make_state(tmp_path), PERF) is False
    assert seen == [(PERF.name, runner.ENABLED, True)]
    assert runner.ENABLED == "enabled"


def test_judge_round_passes_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = make_state(tmp_path)
    judged = patch(monkeypatch, runner, "run_judge", (runner.PASS, None, "ok"))
    assert runner.judge_round(state, CRITIC, "", 0) == (True, "ok")
    assert judged.calls == [(state, CRITIC)]


@pytest.mark.parametrize(
    ("fields", "flags", "hard_focus"),
    [
        ({}, "", None),
        ({"scope_changed": True, "focus": {"b.py", "a.py"}}, " --scope changed --focus a.py --focus b.py", None),
        ({"scope_changed": True, "focus": {"a.py"}, "hard": True}, " --scope hard --focus a.py", {"a.py"}),
    ],
)
def test_run_scope_properties(tmp_path: Path, fields: dict[str, Any], flags: str, hard_focus: set[str] | None) -> None:
    state = make_state(tmp_path, **fields)
    assert state.gate_flags == flags
    assert state.hard_focus == hard_focus


def test_run_paths_and_reports(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gates = patch(monkeypatch, runner, "run_gates", [Result(gate="g", ok=True, summary="s", seconds=0.0)])
    state = make_state(tmp_path, scope_changed=True, focus={"a.py"}, hard=True)
    assert state.task_name == "task"
    assert state.folder == tmp_path / ".marestail" / "runs" / "task"
    assert state.handoffs == tmp_path / ".marestail" / "handoffs" / "task"
    first = state.next_report("coder")
    first.write_text("x")
    assert (first.name, state.next_report("critic").name) == ("01-coder.md", "02-critic.md")
    assert state.gates("fast") == [Result(gate="g", ok=True, summary="s", seconds=0.0)]
    assert gates.calls == [("fast", True, None, {"a.py"}, True)]


def test_judge_progress_author_requested() -> None:
    progress = JudgeProgress(author_left=1)
    progress.author_requested()
    assert (progress.author_left, progress.feedback) == (0, runner.AUTHOR_AGAIN)
    progress.author_requested()
    assert (progress.author_left, progress.feedback) == (0, runner.AUTHOR_DONE)


@pytest.fixture
def pipeline_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    monkeypatch.setenv("MARESTAIL_SCOPE", "unset")
    monkeypatch.setenv("MARESTAIL_FOCUS", "unset")
    config = Config(root=tmp_path, raw={"agent": {"model": "cfg-model", "effort": "cfg-effort"}})
    seen_load: list[Path] = []

    def load(start: Path) -> Config:
        seen_load.append(start)
        return config

    monkeypatch.setattr(config_module, "load", load)
    seen: dict[str, Any] = {
        "require": patch(monkeypatch, dandelion, "require"),
        "start": patch(monkeypatch, perf_trees, "record_start"),
        "resolve": patch(monkeypatch, runner, "resolve_focus", {"src/a.py"}),
        "hook": patch(monkeypatch, runner, "hook_focus", {"src/hook.py"}),
    }

    def steps(state: Run, window: list[Any], auto: bool) -> int:
        seen["state"], seen["window"], seen["auto"] = state, window, auto
        state.perf_changes = seen.get("changes", "")
        return 7

    monkeypatch.setattr(runner, "run_steps", steps)
    seen["load"] = seen_load
    seen["config"] = config
    return seen


def test_run_pipeline_uses_config_defaults(pipeline_env: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    pipeline_env["changes"] = "perf changes"
    assert runner.run_pipeline(Path("t.md"), "coder", "cleaner", True, None, 2) == 7
    state = pipeline_env["state"]
    assert (state.model, state.effort, state.route, state.agent, state.retries) == ("cfg-model", "cfg-effort", None, None, 2)
    assert (state.scope_changed, state.hard, state.focus) == (False, False, set())
    assert state.task == Path("t.md").resolve()
    assert [step.name for step in pipeline_env["window"]] == ["coder", "cleaner"]
    assert pipeline_env["auto"] is True
    assert pipeline_env["start"].calls == [(state.config, "t")]
    assert pipeline_env["load"] == [Path.cwd()]
    assert os.environ["MARESTAIL_SCOPE"] == "unset"
    assert capsys.readouterr().out == "\nperf changes\n"


def test_run_pipeline_routes_and_scopes(pipeline_env: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    runner.run_pipeline(Path("t.md"), None, None, False, "dandelion/route", 0, scope="changed", focus=[" ", "src/a.py"])
    state = pipeline_env["state"]
    assert (state.model, state.effort, state.route) == (None, None, "dandelion/route")
    assert pipeline_env["require"].calls == [()]
    assert pipeline_env["resolve"].calls[0][0] is pipeline_env["config"]
    assert pipeline_env["resolve"].calls[0][1] == {"src/a.py"}
    assert pipeline_env["hook"].calls[0][0] is pipeline_env["config"]
    assert (state.scope_changed, state.hard, state.focus) == (True, False, {"src/a.py", "src/hook.py"})
    assert os.environ["MARESTAIL_SCOPE"] == "changed"
    assert os.environ["MARESTAIL_FOCUS"] == os.pathsep.join(["src/a.py", "src/hook.py"])
    assert capsys.readouterr().out == "\n"


def test_run_pipeline_keeps_the_agent(pipeline_env: dict[str, Any]) -> None:
    runner.run_pipeline(Path("t.md"), None, None, True, "opus", 3, agent="claude")
    assert pipeline_env["state"].agent == "claude"
    assert pipeline_env["state"].retries == 3


def test_run_pipeline_hard_scope_and_explicit_effort(pipeline_env: dict[str, Any]) -> None:
    runner.run_pipeline(Path("t.md"), None, None, False, "opus", 0, effort="low", scope="hard")
    state = pipeline_env["state"]
    assert (state.model, state.effort, state.hard, state.scope_changed) == ("opus", "low", True, True)
    assert os.environ["MARESTAIL_SCOPE"] == "hard"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        (
            {"model": "dandelion/route", "agent": "grok"},
            "--model dandelion/route picks the backend and effort before every session; drop --agent and --effort",
        ),
        (
            {"model": "dandelion/route-best", "effort": "high"},
            "--model dandelion/route-best picks the backend and effort before every session; drop --agent and --effort",
        ),
        ({"model": "x", "scope": "all", "focus": ["a.py"]}, "--focus cannot be combined with --scope all"),
        (
            {"model": "x", "scope": "hard"},
            "--scope hard needs at least one focus path: pass --focus or set [focus] paths in marestail.toml",
        ),
    ],
)
def test_run_pipeline_rejects_bad_flags(
    pipeline_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch, kwargs: dict[str, Any], message: str
) -> None:
    patch(monkeypatch, runner, "resolve_focus", set())
    patch(monkeypatch, runner, "hook_focus", set())
    arguments = {"task": Path("t.md"), "start": None, "stop": None, "auto": True, "retries": 1, **kwargs}
    with pytest.raises(SystemExit) as raised:
        runner.run_pipeline(**arguments)
    assert str(raised.value) == message


def test_run_pipeline_all_scope_without_focus(pipeline_env: dict[str, Any]) -> None:
    runner.run_pipeline(Path("t.md"), None, None, True, "x", 1, scope="all", focus=[])
    assert pipeline_env["state"].scope_changed is False


@pytest.fixture
def steps_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, Recorder]:
    return {
        "archive": patch(monkeypatch, runner, "archive_handoffs"),
        "approve": patch(monkeypatch, runner, "approve", False),
    }


def test_run_steps_completes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, steps_env: dict[str, Recorder], capsys: Any) -> None:
    run_step = patch(monkeypatch, runner, "run_step", True)
    state = make_state(tmp_path)
    assert runner.run_steps(state, [find("specifier"), find("critic"), find("coder")], True) == 0
    assert [call[1].name for call in run_step.calls] == ["specifier", "critic", "coder"]
    assert [call[0] for call in run_step.calls] == [state, state, state]
    assert steps_env["archive"].calls == [(state,)]
    assert steps_env["approve"].calls == []
    assert capsys.readouterr().out == "pipeline complete\n"


def test_run_steps_stops_on_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, steps_env: dict[str, Recorder], capsys: Any) -> None:
    patch(monkeypatch, runner, "run_step", True, False)
    assert runner.run_steps(make_state(tmp_path), [find("specifier"), find("coder"), find("qa")], True) == 1
    assert steps_env["archive"].calls == []
    assert capsys.readouterr().out == "pipeline stopped at coder\n"


def test_run_steps_pauses_for_approval(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, steps_env: dict[str, Recorder]) -> None:
    patch(monkeypatch, runner, "run_step", True)
    state = make_state(tmp_path)
    assert runner.run_steps(state, [find("specifier"), find("critic"), find("coder")], False) == 1
    assert steps_env["approve"].calls == [(state,)]
    steps_env["approve"].replies = [True]
    assert runner.run_steps(state, [find("critic"), find("coder")], False) == 0


def test_run_step_worker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    worker = patch(monkeypatch, runner, "run_worker", False)
    state = make_state(tmp_path)
    assert runner.run_step(state, CODER) is False
    assert worker.calls == [(state, CODER, "")]


@pytest.mark.parametrize(
    ("judge", "raw", "guidance", "expected"),
    [
        (PERF, {"perf": {"enabled": False}}, False, "perf disabled in marestail.toml; skipping\n"),
        (PERF, {"perf": {}}, False, ""),
        (PERF, {"perf": {"enabled": True}}, False, ""),
        (PERF, {}, False, ""),
        (CRITIC, {"critic": {"enabled": False}}, False, ""),
        (find("practices"), {}, False, "practices: no guidance files; skipping\n"),
        (find("practices"), {}, True, ""),
    ],
)
def test_run_step_judge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any, judge: Any, raw: dict[str, Any], guidance: bool, expected: str
) -> None:
    loop = patch(monkeypatch, runner, "run_judge_loop", "looped")
    (tmp_path / "guidance").mkdir()
    (tmp_path / "guidance" / ("g.md" if guidance else "g.txt")).write_text("g")
    state = make_state(tmp_path, raw=raw)
    outcome = runner.run_step(state, judge)
    assert capsys.readouterr().out == expected
    assert outcome == (True if expected else "looped")
    assert loop.calls == ([] if expected else [(state, judge)])


def test_judge_loop_passes_after_rework(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    patch(monkeypatch, runner, "run_judge", ("BOUNCE", "coder", "1. a"), ("BOUNCE", None, "1. b"), ("PASS", None, "ok"))
    worker = patch(monkeypatch, runner, "run_worker", True)
    state = make_state(tmp_path)
    assert runner.run_judge_loop(state, CRITIC) is True
    assert worker.calls == [(state, find("coder"), "1. a"), (state, find("specifier"), "1. b")]


def test_judge_loop_stops_on_repeated_findings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    patch(monkeypatch, runner, "run_judge", ("BOUNCE", None, "1.  same\nnote a"), ("BOUNCE", None, " 1. same \nnote b"))
    patch(monkeypatch, runner, "run_worker", True)
    assert runner.run_judge_loop(make_state(tmp_path), CRITIC) is False
    assert capsys.readouterr().out == ("critic repeated the same findings twice; the worker is not making progress, stopping for a human\n")


def test_judge_loop_stops_after_bounce_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    patch(monkeypatch, runner, "run_judge", ("BOUNCE", None, "1. a"), ("BOUNCE", None, "1. b"))
    worker = patch(monkeypatch, runner, "run_worker", True)
    judge = Judge("hardener", None, bounce_to="coder", bounces=1)
    assert runner.run_judge_loop(make_state(tmp_path), judge) is False
    assert len(worker.calls) == 1
    assert capsys.readouterr().out == "hardener still bouncing after 1 rounds; stopping for a human\n"


def test_judge_loop_stops_after_max_rounds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = make_state(tmp_path)
    rounds = patch(monkeypatch, runner, "judge_round", (None, "x"))
    assert runner.run_judge_loop(state, CRITIC) is False
    assert len(rounds.calls) == 1000
    assert rounds.calls[0] == (state, CRITIC, "", 0)
    assert rounds.calls[1] == (state, CRITIC, "x", 1)
    assert rounds.calls[-1] == (state, CRITIC, "x", 999)


def test_author_round_pins_config_session_and_note(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = make_state(tmp_path)
    session = Session(task="task")
    note = tmp_path / "note.md"
    note.write_text("n")
    benches = patch(monkeypatch, perf_review, "bench_scripts", ["perf/b.py"])
    section = patch(monkeypatch, perf_trees, "prompt_section", "trees")
    heads = patch(monkeypatch, runner, "head", "abc")
    invoke = patch(monkeypatch, runner, "invoke")
    discard = patch(monkeypatch, runner, "discard_edits")
    stage = patch(monkeypatch, runner, "stage_writes")
    recorded = patch(monkeypatch, runner, "record_staged")
    restore = patch(monkeypatch, runner, "restore_files")
    ignored = patch(monkeypatch, runner, "drop_ignored_since", ["x"])
    prints = patch(monkeypatch, runner, "fingerprints", {"perf/b.py": "1"}, {"perf/b.py": "2"})
    prompt = patch(monkeypatch, prompts, "perf_author_prompt", "PROMPT")
    patch(monkeypatch, runner, "agent_label", "claude")
    assert runner.author_round(state, PERF, session, note, "fb") is True
    assert benches.calls == [(state.config,), (state.config,)]
    assert section.calls == [(state.config, session)]
    assert heads.calls == [(state.config,)]
    assert prompt.calls == [(state.config, state.task, state.task_name, "trees", note, "fb")]
    assert invoke.calls == [(state, "note", "PROMPT")]
    assert discard.calls == [(state.config, ("keep", note), ("writes", PERF.writes))]
    assert stage.calls == [(state.config, PERF.writes)]
    assert recorded.calls == [(state.config, "note benches", "perf", "claude")]
    assert ignored.calls == [(state.config, "abc")]
    assert restore.calls == [(state.config, ["x"])]
    assert prints.calls == [(state.config, ["perf/b.py"]), (state.config, ["perf/b.py"])]


@pytest.mark.parametrize(("target", "worked", "calls"), [("critic", True, 0), (None, False, 1)])
def test_judge_loop_stops_when_rework_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str | None, worked: bool, calls: int
) -> None:
    judged = patch(monkeypatch, runner, "run_judge", ("BOUNCE", target, "1. a"))
    worker = patch(monkeypatch, runner, "run_worker", worked)
    assert runner.run_judge_loop(make_state(tmp_path), CRITIC) is False
    assert len(worker.calls) == calls
    assert len(judged.calls) == 1


@pytest.mark.parametrize(
    ("previous", "current", "expected"),
    [("", "", False), ("1. a", "1.   a\nprose", True), ("1. a", "1. b", False), ("text", "other", True)],
)
def test_same_findings(previous: str, current: str, expected: bool) -> None:
    assert runner.same_findings(previous, current) is expected


def test_normalise_findings() -> None:
    assert runner.normalise_findings("  1.  a \t b\nnot\n2.x\n- 3. c") == ["1. a b", "2.x"]


def test_attempts() -> None:
    assert list(runner.attempts(1)) == [1]
    assert list(runner.attempts(2)) == [1, 2]
    assert list(itertools.islice(runner.attempts(0), 4)) == [1, 2, 3, 4]
    assert list(itertools.islice(runner.attempts(-1), 2)) == [1, 2]
    assert runner.attempt_limit(0) == runner.ATTEMPT_CAP
    assert runner.attempt_limit(-1) == runner.ATTEMPT_CAP
    assert runner.attempt_limit(3) == 3
    assert list(runner.attempts(0))[-1] == runner.ATTEMPT_CAP
    assert len(list(runner.attempts(0))) == runner.ATTEMPT_CAP


def test_attempts_shown() -> None:
    assert runner.attempts_shown(0) == "unlimited"
    assert runner.attempts_shown(-1) == "unlimited"
    assert runner.attempts_shown(1) == "1"
    assert runner.attempts_shown(2) == "2"


def test_runner_constants() -> None:
    assert runner.UNLIMITED == "unlimited"
    assert runner.ENABLED == "enabled"
    assert runner.GROK == "grok"
    assert runner.SPACE == " "
    assert runner.ATTEMPT_CAP == 10000
    assert runner.RUN_TYPE == "run"
    assert runner.MISSING_OK is True
    assert runner.RENAME_MARK == " -> "
    assert runner.AUTHOR_VERDICT.pattern == r"^\s*VERDICT:\s*AUTHOR\b"
    assert runner.VERDICT_LINE.pattern == r"VERDICT:\s*(PASS|BOUNCE)(?:[ \t]+(\w+))?"


def test_need_rejects_the_wrong_type() -> None:
    with pytest.raises(TypeError, match=r"^run$"):
        runner.need(None, runner.Run)


def test_need_accepts_the_matching_type() -> None:
    runner.need("x", str)


def test_renamed_path_keeps_plain_and_splits_on_arrow() -> None:
    assert runner.renamed_path("src/a.py") == "src/a.py"
    assert runner.renamed_path("old.py -> new.py") == "new.py"
    assert runner.renamed_path("a -> b -> c") == "b -> c"
    assert runner.renamed_path(" -> dest") == "dest"


def test_after_marker_and_until_heading() -> None:
    assert runner.after_marker("pre## Config change\nbody\n## Next\n", runner.CONFIG_CHANGE) == "\nbody\n## Next\n"
    assert runner.after_marker("nope", runner.CONFIG_CHANGE) == "nope"
    assert runner.until_heading("body\n## Next\nrest") == "body"
    assert runner.until_heading("only") == "only"
    assert runner.until_heading("\n## First\nrest") == ""
    assert runner.until_heading("a\n## One\nmid\n## Two\nend") == "a"
    assert runner.HEADING_MARK == "\n## "
    assert runner.MISSING == -1


@pytest.mark.parametrize(
    ("problems", "shape"),
    [("3 errors in 1.5s", "# errors in #s"), ("  a\n\n b ", "a b"), ("v2.10.3", "v#.#")],
)
def test_problem_shape(problems: str, shape: str) -> None:
    assert runner.problem_shape(problems) == shape


@pytest.fixture
def worker_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, Recorder]:
    return {
        "head": patch(monkeypatch, runner, "head", "before"),
        "prompt": patch(monkeypatch, prompts, "worker_prompt", "PROMPT"),
        "invoke": patch(monkeypatch, runner, "invoke"),
        "drop": patch(monkeypatch, runner, "drop_ignored_since", {"a.log": b"x"}),
        "fold": patch(monkeypatch, runner, "fold_handoff"),
        "restore": patch(monkeypatch, runner, "restore_files"),
    }


def test_run_worker_succeeds_after_feedback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, worker_env: dict[str, Recorder], capsys: Any
) -> None:
    verify = patch(monkeypatch, runner, "verify_worker", "missing handoff", "")
    state = make_state(tmp_path, retries=3, scope_changed=True, focus={"a.py"}, hard=True, labels={"x"})
    assert runner.run_worker(state, CODER, "first feedback") is True
    reports = [state.handoffs / "01-coder.md", state.handoffs / "01-coder.md"]
    assert [call[5] for call in worker_env["prompt"].calls] == ["first feedback", "missing handoff"]
    assert worker_env["prompt"].calls[0] == (
        state.config,
        CODER,
        state.task,
        "task",
        reports[0],
        "first feedback",
        "m e",
        " --scope hard --focus a.py",
        {"a.py"},
    )
    assert worker_env["invoke"].calls == [(state, "01-coder", "PROMPT"), (state, "01-coder", "PROMPT")]
    assert verify.calls[1] == (state, CODER, reports[1], "before")
    assert worker_env["drop"].calls == [(state.config, "before")]
    assert worker_env["fold"].calls == [(state.config, "coder", reports[1], "before", "m e", {"x"})]
    assert worker_env["restore"].calls == [(state.config, {"a.log": b"x"})]
    assert worker_env["head"].calls == [(state.config,)]
    assert capsys.readouterr().out == "== coder (01-coder) attempt 1\nmissing handoff\n== coder (01-coder) attempt 2\n"


def test_run_worker_gives_up_after_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, worker_env: dict[str, Recorder], capsys: Any
) -> None:
    patch(monkeypatch, runner, "verify_worker", "problem a", "problem b")
    assert runner.run_worker(make_state(tmp_path, retries=2), CODER, "") is False
    assert worker_env["fold"].calls == []
    assert capsys.readouterr().out.splitlines()[-1] == "problem b"


def test_run_worker_stops_on_repeated_problems(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, worker_env: dict[str, Recorder], capsys: Any
) -> None:
    verify = patch(monkeypatch, runner, "verify_worker", "3 errors", "4 errors", "5 errors", "6 errors")
    assert runner.run_worker(make_state(tmp_path, retries=0), CODER, "") is False
    assert len(verify.calls) == 3
    assert capsys.readouterr().out.splitlines()[-1] == (
        "coder got the same problems back 3 times in a row; the worker is not making progress, stopping for a human"
    )


@contextlib.contextmanager
def fake_measuring(session: Session, log: list[str]) -> Iterator[Session]:
    log.append("enter")
    yield session
    log.append("exit")


@pytest.fixture
def perf_session(monkeypatch: pytest.MonkeyPatch) -> tuple[Session, list[str]]:
    session = Session(task="task", trees=[Tree("head", "sha", Path("/h"))])
    log: list[str] = []
    monkeypatch.setattr(perf_trees, "measuring", lambda config, task: fake_measuring(session, log))
    monkeypatch.setattr(perf_db, "prepare", lambda config, s: log.append("prepare"))
    monkeypatch.setattr(perf_db, "release", lambda config, s: log.append("release"))
    return session, log


def test_measuring_only_for_perf(tmp_path: Path, perf_session: tuple[Session, list[str]]) -> None:
    session, log = perf_session
    state = make_state(tmp_path)
    with runner.measuring(state, CRITIC) as none:
        assert none is None
    assert log == []
    with pytest.raises(RuntimeError), runner.measuring(state, PERF) as measured:
        assert measured is session
        raise RuntimeError
    assert log == ["enter", "prepare", "release"]


def test_run_judge_retries_until_verdict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    attempt = patch(monkeypatch, runner, "judge_attempt", (None, "retry feedback"), (("PASS", None, "ok"), ""))
    state = make_state(tmp_path, retries=0)
    assert runner.run_judge(state, CRITIC) == ("PASS", None, "ok")
    assert [call[5] for call in attempt.calls] == ["", "retry feedback"]
    assert attempt.calls[0][1:5] == (CRITIC, state.handoffs / "01-critic.md", ("", True), None)
    assert capsys.readouterr().out == "== critic (01-critic) attempt 1\n== critic (01-critic) attempt 2\n"


def test_run_judge_gives_up(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    patch(monkeypatch, runner, "judge_attempt", (None, "again"))
    assert runner.run_judge(make_state(tmp_path, retries=2), CRITIC) == ("BOUNCE", None, "critic produced no verdict after 2 attempts")


def test_run_judge_unlimited_shown_when_retries_are_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "attempts", lambda _retries: [1])
    patch(monkeypatch, runner, "judge_attempt", (None, "again"))
    assert runner.run_judge(make_state(tmp_path, retries=0), CRITIC) == (
        "BOUNCE",
        None,
        "critic produced no verdict after unlimited attempts",
    )


def test_run_judge_perf_authoring_rounds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, perf_session: tuple[Session, list[str]]) -> None:
    session, _ = perf_session
    author = patch(monkeypatch, runner, "author_phase", "authored")
    fill = patch(monkeypatch, runner, "fill_samples")
    attempt = patch(monkeypatch, runner, "judge_attempt", (("AUTHOR", None, ""), "authored"), (("PASS", None, "done"), ""))
    state = make_state(tmp_path, retries=0)
    assert runner.run_judge(state, PERF) == ("PASS", None, "done")
    assert [call[3] for call in author.calls] == ["", runner.AUTHOR_AGAIN]
    assert len(fill.calls) == 2
    assert attempt.calls[0][4] is session
    assert attempt.calls[1][5] == "authored"


def test_run_judge_perf_with_failed_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, perf_session: tuple[Session, list[str]]) -> None:
    patch(monkeypatch, runner, "run_gates", [Result(gate="g", ok=False, summary="bad", seconds=0.0)])
    author = patch(monkeypatch, runner, "author_phase", "authored")
    fill = patch(monkeypatch, runner, "fill_samples")
    patch(monkeypatch, runner, "judge_attempt", (("BOUNCE", None, "x"), ""))
    judge = Judge("perf", "full", bounce_to="coder")
    assert runner.run_judge(make_state(tmp_path), judge) == ("BOUNCE", None, "x")
    assert (author.calls, fill.calls) == ([], [])


def test_prepare_perf_skips_authoring_without_rounds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    author = patch(monkeypatch, runner, "author_phase", "authored")
    fill = patch(monkeypatch, runner, "fill_samples")
    progress = JudgeProgress(feedback="kept", author_left=0)
    session = Session(task="task")
    state = make_state(tmp_path)
    runner.prepare_perf(state, PERF, True, session, progress)
    runner.prepare_perf(state, PERF, True, None, progress)
    assert author.calls == []
    assert fill.calls == [(state, session)]
    assert progress.feedback == "kept"


def test_prepare_perf_authors_when_one_round_is_left(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    author = patch(monkeypatch, runner, "author_phase", "authored")
    fill = patch(monkeypatch, runner, "fill_samples")
    progress = JudgeProgress(feedback="old", author_left=1)
    session = Session(task="task")
    state = make_state(tmp_path)
    runner.prepare_perf(state, PERF, True, session, progress)
    assert author.calls == [(state, PERF, session, "old")]
    assert fill.calls == [(state, session)]
    assert progress.feedback == "authored"


def test_has_author_round_is_true_for_one_left() -> None:
    assert runner.has_author_round(1) is True
    assert runner.has_author_round(0) is False


def test_next_streak_starts_and_grows() -> None:
    first = runner.next_streak([], "3 errors")
    assert first == ["3 errors"]
    assert runner.next_streak(first, "4 errors") == ["3 errors", "4 errors"]
    assert runner.next_streak(first, "other") == ["other"]


def test_judged_after_last_authoring_round(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    patch(monkeypatch, runner, "judge_attempt", (("AUTHOR", None, ""), "fb"))
    progress = JudgeProgress(author_left=0)
    assert runner.judged(make_state(tmp_path), CRITIC, ("", True), progress, 1) is None
    assert progress.feedback == runner.AUTHOR_DONE


@pytest.fixture
def judge_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    env: dict[str, Any] = {
        "head": patch(monkeypatch, runner, "head", "before"),
        "prompt": patch(monkeypatch, prompts, "judge_prompt", "PROMPT"),
        "trees": patch(monkeypatch, perf_trees, "prompt_section", "TREES"),
        "discard": patch(monkeypatch, runner, "discard_edits"),
        "stage": patch(monkeypatch, runner, "stage_writes"),
        "drop": patch(monkeypatch, runner, "drop_ignored_since", {"f": b"1"}),
        "commit": patch(monkeypatch, runner, "record_commit"),
        "restore": patch(monkeypatch, runner, "restore_files"),
        "files": {},
    }

    def invoke(state: Run, label: str, prompt: str) -> None:
        state.folder.mkdir(parents=True, exist_ok=True)
        for name, text in env["files"].items():
            (state.folder / name if name.endswith(".json") else state.handoffs / name).write_text(text)

    monkeypatch.setattr(runner, "invoke", invoke)
    return env


def attempt_judge(tmp_path: Path, judge: Judge, gate: tuple[str, bool] = ("", True), session: Session | None = None) -> Any:
    state = make_state(tmp_path, hard=True, focus={"a.py"})
    report = state.next_report(judge.name)
    return state, report, runner.judge_attempt(state, judge, report, gate, session, "old feedback")


def test_judge_attempt_records_bounce(tmp_path: Path, judge_env: dict[str, Any], capsys: Any) -> None:
    judge_env["files"] = {"01-hardener.md": "VERDICT: bounce coder\n1. fix"}
    judge = Judge("hardener", None, bounce_to="coder", writes=("docs/**",))
    state, report, outcome = attempt_judge(tmp_path, judge)
    assert outcome == (("BOUNCE", "coder", "VERDICT: bounce coder\n1. fix"), "")
    assert judge_env["prompt"].calls == [
        (state.config, judge, state.task, "task", report, "", "", "old feedback", {"a.py"}),
    ]
    assert judge_env["discard"].calls == [(state.config, ("keep", report), ("writes", ("docs/**",)))]
    assert judge_env["stage"].calls == [(state.config, ("docs/**",))]
    assert judge_env["commit"].calls == [
        (state.config, "hardener verdict: BOUNCE to coder", "VERDICT: bounce coder\n1. fix", "hardener", "m e"),
    ]
    assert judge_env["restore"].calls == [(state.config, {"f": b"1"})]
    assert capsys.readouterr().out == "   verdict BOUNCE to coder\n"


def test_judge_attempt_writes_verdict_from_output(tmp_path: Path, judge_env: dict[str, Any], capsys: Any) -> None:
    judge_env["files"] = {"01-critic.json": '{"result": "VERDICT: PASS"}'}
    _, report, outcome = attempt_judge(tmp_path, CRITIC)
    assert outcome == (("PASS", None, "VERDICT: PASS\n"), "")
    assert report.read_text() == "VERDICT: PASS\n"
    assert judge_env["commit"].calls[0][1] == "critic verdict: PASS"
    assert capsys.readouterr().out == "   verdict PASS\n"


def test_judge_attempt_writes_bounce_target_from_output(tmp_path: Path, judge_env: dict[str, Any]) -> None:
    judge_env["files"] = {"01-critic.json": "VERDICT: BOUNCE specifier"}
    _, report, _ = attempt_judge(tmp_path, CRITIC)
    assert report.read_text() == "VERDICT: BOUNCE specifier\n"


def test_judge_attempt_without_verdict(tmp_path: Path, judge_env: dict[str, Any], capsys: Any) -> None:
    _, report, outcome = attempt_judge(tmp_path, CRITIC)
    assert outcome == (None, runner.no_verdict_feedback(report))
    assert judge_env["commit"].calls == []
    assert capsys.readouterr().out == "critic wrote no verdict; retrying\n"


def test_no_verdict_feedback() -> None:
    assert runner.no_verdict_feedback(Path("/r.md")) == (
        "Your session produced no verdict I could find: no file at /r.md and no line starting `VERDICT:` in your output. "
        "Write the verdict file yourself, first line `VERDICT: PASS` or `VERDICT: BOUNCE`, "
        "or print the `VERDICT:` line in your final output."
    )


@pytest.mark.parametrize(("judge", "author"), [(PERF, True), (CRITIC, False)])
def test_judge_attempt_author_request(tmp_path: Path, judge_env: dict[str, Any], judge: Judge, author: bool) -> None:
    judge_env["files"] = {f"01-{judge.name}.json": "  verdict: author\nVERDICT: PASS"}
    _, _, outcome = attempt_judge(tmp_path, judge)
    assert (outcome == (("AUTHOR", None, ""), "old feedback")) is author


def test_judge_attempt_pinned_bounce_and_failed_gate(tmp_path: Path, judge_env: dict[str, Any]) -> None:
    judge_env["files"] = {"01-practices.md": "VERDICT: PASS"}
    _, _, outcome = attempt_judge(tmp_path, cast(Judge, find("practices")), ("GATE FAILED", False))
    assert outcome == (("BOUNCE", None, "GATE FAILED\n\nVERDICT: PASS"), "")
    judge_env["files"] = {"02-practices.md": "VERDICT: BOUNCE critic"}
    _, _, pinned = attempt_judge(tmp_path, cast(Judge, find("practices")))
    assert pinned == (("BOUNCE", None, "VERDICT: BOUNCE critic"), "")


def test_judge_attempt_perf_rejected(tmp_path: Path, judge_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    review = patch(monkeypatch, runner, "review_measurements", "- missing samples")
    judge_env["files"] = {"01-perf.md": "VERDICT: PASS"}
    session = Session(task="task")
    state, report, outcome = attempt_judge(tmp_path, PERF, session=session)
    assert outcome == (None, "- missing samples")
    assert review.calls == [(state, session, report, "PASS", "VERDICT: PASS")]
    assert judge_env["prompt"].calls[0][6] == "TREES"
    assert judge_env["discard"].calls[0][2] == ("writes", ())
    assert capsys.readouterr().out == "   perf verdict rejected; retrying\n"


def test_judge_attempt_perf_accepted(tmp_path: Path, judge_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    patch(monkeypatch, runner, "review_measurements", "")
    scratch = patch(monkeypatch, perf_hygiene, "discard_scratch", ["perf/tmp.txt"])
    judge_env["files"] = {"01-perf.md": "VERDICT: PASS"}
    state, _, outcome = attempt_judge(tmp_path, PERF, session=Session(task="task"))
    assert outcome == (("PASS", None, "VERDICT: PASS"), "")
    assert judge_env["stage"].calls[0][1] == ()
    assert scratch.calls == [(state.config.root,)]
    assert capsys.readouterr().out == "   removed perf scratch perf/tmp.txt\n   verdict PASS\n"


@pytest.fixture
def author_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    env: dict[str, Any] = {
        "version": 0,
        "changes": 0,
        "head": patch(monkeypatch, runner, "head", "before"),
        "prompt": patch(monkeypatch, prompts, "perf_author_prompt", "AUTHOR PROMPT"),
        "trees": patch(monkeypatch, perf_trees, "prompt_section", "TREES"),
        "benches": patch(monkeypatch, perf_review, "bench_scripts", ["perf/a.py", "perf/b.py"]),
        "discard": patch(monkeypatch, runner, "discard_edits"),
        "stage": patch(monkeypatch, runner, "stage_writes"),
        "staged": patch(monkeypatch, runner, "record_staged"),
        "drop": patch(monkeypatch, runner, "drop_ignored_since", {"k": b"v"}),
        "restore": patch(monkeypatch, runner, "restore_files"),
        "invoke": patch(monkeypatch, runner, "invoke"),
    }

    def invoke(state: Run, label: str, prompt: str) -> None:
        env["invoke"](state, label, prompt)
        env["version"] += 1 if env["changes"] > 0 else 0
        env["changes"] -= 1

    monkeypatch.setattr(runner, "invoke", invoke)
    monkeypatch.setattr(perf_hygiene, "fingerprint", lambda root, bench: f"{bench}@{env['version']}")
    return env


def test_author_phase_stops_when_benches_settle(tmp_path: Path, author_env: dict[str, Any], capsys: Any) -> None:
    author_env["changes"] = 1
    state = make_state(tmp_path)
    session = Session(task="task")
    assert runner.author_phase(state, PERF, session, "fb") == f"fb\n\n{runner.BENCHES_CHANGED}"
    note = state.folder / "perf-author-1.md"
    assert author_env["trees"].calls[0] == (state.config, session)
    assert author_env["prompt"].calls[0] == (state.config, state.task, "task", "TREES", note, "fb")
    assert author_env["invoke"].calls[0] == (state, "perf-author-1", "AUTHOR PROMPT")
    assert author_env["discard"].calls[0] == (state.config, ("keep", note), ("writes", ("perf/**",)))
    assert author_env["stage"].calls[0] == (state.config, ("perf/**",))
    assert author_env["staged"].calls[0] == (state.config, "perf-author-1 benches", "perf", "m e")
    assert author_env["restore"].calls[0] == (state.config, {"k": b"v"})
    assert capsys.readouterr().out == (
        "   perf-author-1: benches changed; stale samples dropped, re-authoring\n   perf-author-2: benches unchanged\n"
    )


def test_author_phase_runs_three_rounds_at_most(tmp_path: Path, author_env: dict[str, Any], capsys: Any) -> None:
    author_env["changes"] = 5
    feedback = runner.author_phase(make_state(tmp_path), PERF, Session(task="task"), "")
    assert feedback == "\n\n".join([runner.BENCHES_CHANGED] * 3)
    assert len(author_env["invoke"].calls) == 3
    assert capsys.readouterr().out.count("benches changed") == 3


@pytest.fixture
def samples_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    return {
        "benches": patch(monkeypatch, perf_review, "bench_scripts", ["b.py"]),
        "policy": patch(monkeypatch, perf_settings, "policy", SimpleNamespace(min_runs=3)),
        "stale": patch(monkeypatch, perf_samples, "drop_stale", 2),
        "records": patch(monkeypatch, perf_review, "load_records", []),
        "db": patch(monkeypatch, perf_db, "for_run", (None, "no database")),
        "take": patch(monkeypatch, perf_samples, "take_sample", ""),
        "fingerprint": patch(monkeypatch, perf_hygiene, "fingerprint", "fp"),
    }


def two_trees() -> Session:
    return Session(task="task", trees=[Tree("t1", "s1", Path("/1")), Tree("t2", "s2", Path("/2"))])


def test_fill_samples_without_benches(tmp_path: Path, samples_env: dict[str, Any]) -> None:
    samples_env["benches"].replies = [[]]
    state = make_state(tmp_path)
    runner.fill_samples(state, two_trees())
    assert samples_env["benches"].calls == [(state.config,)]
    assert samples_env["policy"].calls == []


def test_fill_samples_tops_up_each_tree(tmp_path: Path, samples_env: dict[str, Any], capsys: Any) -> None:
    samples_env["records"].replies = [
        [
            {"script": "b.py", "tree": "t1", "sample": 1, "fingerprint": "fp"},
            {"script": "b.py", "tree": "t1", "sample": 2, "fingerprint": "fp"},
            {"script": "b.py", "tree": "t1", "sample": 3, "fingerprint": "old"},
            {"script": "c.py", "tree": "t2", "sample": 1, "fingerprint": "fp", "db": True},
            {"script": "b.py", "tree": "t2", "sample": 1, "fingerprint": "fp", "db": False},
        ]
    ]
    session = two_trees()
    state = make_state(tmp_path)
    runner.fill_samples(state, session)
    assert samples_env["stale"].calls == [(state.config, "b.py", "fp")]
    assert [call[3:] for call in samples_env["take"].calls] == [(3, (None, "fp")), (2, (None, "fp")), (3, (None, "fp"))]
    assert [call[2] for call in samples_env["take"].calls] == [session.trees[0], session.trees[1], session.trees[1]]
    assert capsys.readouterr().out == (
        "   dropped 2 stale b.py samples\n   filling b.py on t1: 1 sample(s)\n   filling b.py on t2: 2 sample(s)\n"
    )


def test_fill_samples_skips_full_trees_and_stops_on_problems(tmp_path: Path, samples_env: dict[str, Any], capsys: Any) -> None:
    samples_env["stale"].replies = [0]
    samples_env["records"].replies = [[{"script": "b.py", "tree": "t1", "sample": n, "fingerprint": "fp"} for n in (1, 2, 3)]]
    samples_env["take"].replies = ["disk full", ""]
    runner.fill_samples(make_state(tmp_path), two_trees())
    assert [call[3] for call in samples_env["take"].calls] == [1]
    assert capsys.readouterr().out == "   filling b.py on t2: 3 sample(s)\n   fill_samples: disk full\n"


def test_fill_samples_needs_database(tmp_path: Path, samples_env: dict[str, Any], capsys: Any) -> None:
    samples_env["stale"].replies = [0]
    samples_env["records"].replies = [[{"script": "b.py", "tree": "t9", "sample": 1, "db": True}]]
    runner.fill_samples(make_state(tmp_path), Session(task="task", trees=[Tree("t1", "s", Path("/1"))]))
    assert samples_env["take"].calls == []
    assert capsys.readouterr().out == "   fill_samples: no database\n"
    database = object()
    samples_env["db"].replies = [(database, "")]
    runner.fill_samples(make_state(tmp_path), Session(task="task", trees=[Tree("t1", "s", Path("/1"))]))
    assert samples_env["take"].calls[0][4] == (database, "fp")


def test_review_measurements(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    outcome = SimpleNamespace(problems=[])
    review = patch(monkeypatch, perf_review, "review", SimpleNamespace(problems=["a", "b"]), outcome)
    summary = patch(monkeypatch, perf_review, "changes_summary", "CHANGES")
    table = patch(monkeypatch, perf_review, "record_table")
    state = make_state(tmp_path)
    session = Session(task="task")
    report = tmp_path / "r.md"
    assert runner.review_measurements(state, session, report, "PASS", "text") == "- a\n- b"
    assert state.perf_changes == ""
    assert runner.review_measurements(state, session, report, "PASS", "text") == ""
    assert state.perf_changes == "CHANGES"
    assert runner.review_measurements(state, session, report, "BOUNCE", "text") == ""
    assert review.calls[0] == (state.config, session, report, "PASS")
    assert summary.calls[0] == (outcome, "text", session)
    assert table.calls == [(state.config, session, outcome)]


@pytest.mark.parametrize(
    ("text", "extra", "expected"),
    [
        (None, "log\nverdict: pass Coder", ("PASS", "coder")),
        ("VERDICT: BOUNCE nobody", "", ("BOUNCE", None)),
        ("VERDICT:  bounce", "", ("BOUNCE", None)),
        ("VERDICT: PASS\nVERDICT: BOUNCE coder", "", ("PASS", None)),
        ("nothing", "here", None),
    ],
)
def test_parse_verdict(tmp_path: Path, text: str | None, extra: str, expected: Any) -> None:
    report = tmp_path / "r.md"
    if text is not None:
        report.write_text(text)
    assert runner.parse_verdict(report, extra) == expected


def test_judge_session_passes_config_and_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = make_state(tmp_path)
    report = tmp_path / "01-critic.md"
    report.write_text("x")
    session = Session(task="t")
    section = patch(monkeypatch, perf_trees, "prompt_section", "TREES")
    prompt = patch(monkeypatch, prompts, "judge_prompt", "PROMPT")
    patch(monkeypatch, runner, "head", "abc")
    invoke = patch(monkeypatch, runner, "invoke")
    discard = patch(monkeypatch, runner, "discard_edits")
    assert runner.judge_session(state, CRITIC, report, "gate", session, "fb") == "abc"
    assert section.calls == [(state.config, session)]
    assert prompt.calls[0][6] == "TREES"
    assert invoke.calls == [(state, "01-critic", "PROMPT")]
    assert discard.calls[0][0] is state.config


def test_judge_session_without_trees_uses_empty_section(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = make_state(tmp_path)
    report = tmp_path / "01-critic.md"
    report.write_text("x")
    section = patch(monkeypatch, perf_trees, "prompt_section", "TREES")
    prompt = patch(monkeypatch, prompts, "judge_prompt", "PROMPT")
    patch(monkeypatch, runner, "head", "abc")
    patch(monkeypatch, runner, "invoke")
    patch(monkeypatch, runner, "discard_edits")
    runner.judge_session(state, CRITIC, report, "gate", None, "fb")
    assert section.calls == []
    assert prompt.calls[0][6] == runner.EMPTY


def test_parse_verdict_without_extra_reads_the_report(tmp_path: Path) -> None:
    report = tmp_path / "r.md"
    report.write_text("VERDICT: PASS")
    assert runner.parse_verdict(report) == ("PASS", None)
    assert runner.EMPTY == ""
    assert runner.NEWLINE == "\n"
    assert runner.GIT == "git"
    assert runner.DIFF == "diff"
    assert runner.HEAD_REF == "HEAD"


def test_read_or_empty_missing_is_blank(tmp_path: Path) -> None:
    assert runner.read_or_empty(tmp_path / "missing.md") == ""
    path = tmp_path / "a.md"
    path.write_text("hi")
    assert runner.read_or_empty(path) == "hi"


@pytest.fixture
def invoke_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setattr(runner, "LIMIT_WAIT_SECONDS", 120)
    ticks = itertools.count(0, 30)
    monkeypatch.setattr(time, "time", lambda: next(ticks))
    return {
        "sleep": patch(monkeypatch, time, "sleep"),
        "backend": patch(monkeypatch, runner, "run_backend", (0, '{"total_cost_usd": 0.5, "num_turns": 2, "result": "ok"}')),
    }


def test_invoke_reuses_an_existing_folder(tmp_path: Path, invoke_env: dict[str, Any], capsys: Any) -> None:
    state = make_state(tmp_path)
    state.folder.mkdir(parents=True, exist_ok=True)
    runner.invoke(state, "01-critic", "the prompt")
    assert (state.folder / "01-critic.prompt.md").read_text() == "the prompt"


def test_invoke_success(tmp_path: Path, invoke_env: dict[str, Any], capsys: Any) -> None:
    state = make_state(tmp_path)
    runner.invoke(state, "01-critic", "the prompt")
    prompt_file = state.folder / "01-critic.prompt.md"
    assert prompt_file.read_text() == "the prompt"
    assert invoke_env["backend"].calls == [(state, "claude", "the prompt", prompt_file)]
    assert (state.folder / "01-critic.json").read_text() == '{"total_cost_usd": 0.5, "num_turns": 2, "result": "ok"}'
    assert capsys.readouterr().out == "   01-critic finished in 0.5 min: turns=2 api-equivalent=$0.50 'ok'\n"
    assert invoke_env["sleep"].calls == []


def test_invoke_waits_on_rate_limit(tmp_path: Path, invoke_env: dict[str, Any], capsys: Any) -> None:
    invoke_env["backend"].replies = [(1, "rate limit"), (0, "done")]
    runner.invoke(make_state(tmp_path), "x", "p")
    assert invoke_env["sleep"].calls == [(120,)]
    assert capsys.readouterr().out == "   rate limited; waiting 2 min before retrying x\n   x finished in 0.5 min: done\n"


def test_invoke_gives_up_after_waits(tmp_path: Path, invoke_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    monkeypatch.setattr(runner, "LIMIT_WAITS", 2)
    invoke_env["backend"].replies = [(1, "rate limit")]
    runner.invoke(make_state(tmp_path), "x", "p")
    assert len(invoke_env["sleep"].calls) == 2
    assert capsys.readouterr().out.splitlines()[-1] == "   x: still rate limited after 2 waits"


def test_invoke_stops_when_grok_is_locked(tmp_path: Path, invoke_env: dict[str, Any], capsys: Any) -> None:
    invoke_env["backend"].replies = [(1, "always-approve is disabled by policy")]
    runner.invoke(make_state(tmp_path, agent="grok"), "x", "p")
    assert invoke_env["sleep"].calls == []
    assert capsys.readouterr().out == "   x: grok always-approve is locked; cannot run unattended\n"


def test_invoke_grok_unlocked_uses_grok_readers(tmp_path: Path, invoke_env: dict[str, Any], capsys: Any) -> None:
    invoke_env["backend"].replies = [(0, '{"text": "t", "num_turns": 1}')]
    runner.invoke(make_state(tmp_path, agent="grok"), "x", "p")
    assert capsys.readouterr().out == "   x finished in 0.5 min: turns=1 't'\n"


def test_invoke_routes_each_session(tmp_path: Path, invoke_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    choice = Choice(line="picked claude opus", backend="claude", model="opus", effort="high", env={"K": "v"})
    choose = patch(monkeypatch, dandelion, "choose", (None, "no quota left"), (choice, ""))
    state = make_state(tmp_path, agent=None, model=None, effort=None, route="dandelion/route")
    runner.invoke(state, "x", "Commit as [dandelion/route] here")
    prompt_file = state.folder / "x.prompt.md"
    assert choose.calls == [("dandelion/route", tmp_path), ("dandelion/route", tmp_path)]
    assert invoke_env["backend"].calls == [(state, "claude", "Commit as [opus high] here", prompt_file)]
    assert prompt_file.read_text() == "Commit as [opus high] here"
    assert (state.agent, state.model, state.effort, state.account_env, state.labels) == (
        "claude",
        "opus",
        "high",
        {"K": "v"},
        {"opus high"},
    )
    assert invoke_env["sleep"].calls == [(120,)]
    assert capsys.readouterr().out.splitlines()[:2] == [
        "   dandelion/route: no quota left; waiting 2 min before retrying x",
        "   dandelion/route: picked claude opus",
    ]


class FakeStdin:
    def __init__(self, tty: bool) -> None:
        self.tty = tty

    def isatty(self) -> bool:
        return self.tty


def test_approve_non_interactive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    monkeypatch.setattr(sys, "stdin", FakeStdin(False))
    state = make_state(tmp_path)
    assert runner.approve(state) is True
    state.next_report("specifier").write_text("first")
    state.next_report("coder").write_text("middle")
    state.next_report("critic").write_text("last")
    assert runner.approve(state) is True
    note = "non-interactive: continuing without approval (use --to critic to stop here)\n"
    assert capsys.readouterr().out == f"{note}\nlast\n{note}"


@pytest.mark.parametrize(("answer", "expected"), [(" Y ", True), ("n", False), ("", False)])
def test_approve_interactive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answer: str, expected: bool) -> None:
    monkeypatch.setattr(sys, "stdin", FakeStdin(True))
    asked = Recorder(answer)
    monkeypatch.setattr("builtins.input", asked)
    assert runner.approve(make_state(tmp_path)) is expected
    assert asked.calls == [("continue to coder? [y/N] ",)]


def test_commit_verdict_passes_before(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def drop_ignored_since(config: Config, before: str) -> dict[str, bytes]:
        if before is None:
            raise TypeError("before")
        seen.append(before)
        return {}

    monkeypatch.setattr(runner, "drop_ignored_since", drop_ignored_since)
    monkeypatch.setattr(runner, "stage_writes", lambda *args: None)
    monkeypatch.setattr(runner, "record_commit", lambda *args: None)
    monkeypatch.setattr(runner, "restore_files", lambda *args: None)
    monkeypatch.setattr(runner, "agent_label", lambda state: "L")
    runner.commit_verdict(make_state(tmp_path), CRITIC, "abc", (runner.PASS, None, "ok"))
    assert seen == ["abc"]


def test_fingerprints_pass_each_bench(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def fingerprint(root: Path, bench: str) -> str:
        if bench is None:
            raise TypeError("bench")
        seen.append(bench)
        return "fp"

    monkeypatch.setattr(perf_hygiene, "fingerprint", fingerprint)
    assert runner.fingerprints(Config(root=tmp_path, raw={}), ["perf/a.py"]) == {"perf/a.py": "fp"}
    assert seen == ["perf/a.py"]


def test_take_samples_pass_the_bench(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def take_sample(config: Config, bench: str, tree: Tree, number: int, harness: object) -> str:
        if bench is None:
            raise TypeError("bench")
        seen.append(bench)
        return ""

    monkeypatch.setattr(perf_samples, "take_sample", take_sample)
    tree = Tree("head", "deadbeef", tmp_path)
    runner.take_samples(Config(root=tmp_path, raw={}), "perf/a.py", tree, range(1, 2), (None, ""))
    assert seen == ["perf/a.py"]


def test_fold_handoff_forwards_used(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[set[str] | None] = []

    def stamp_history(config: Config, before: str, label: str, used: set[str] | None = None) -> None:
        if used is None:
            raise TypeError("used")
        seen.append(used)

    monkeypatch.setattr(runner, "stamp_history", stamp_history)
    monkeypatch.setattr(runner, "head", lambda config: "other")
    monkeypatch.setattr(runner, "run", lambda *args, **kwargs: (0, "msg"))
    report = tmp_path / "handoff.md"
    report.write_text("body")
    runner.fold_handoff(Config(root=tmp_path, raw={}), "coder", report, "before", "L", {"O"})
    assert seen == [{"O"}]
