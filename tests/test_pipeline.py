import pytest

from marestail import pipeline
from marestail.pipeline import Judge, Worker


def test_names_follow_the_pipeline_order() -> None:
    assert pipeline.names() == ["specifier", "critic", "coder", "cleaner", "architect", "practices", "perf", "hardener", "qa"]


def test_find_returns_the_named_step() -> None:
    assert pipeline.find("coder") == Worker("coder", "fast", audit=True)
    assert pipeline.find("perf") == Judge("perf", None, bounce_to="coder", writes=("perf/**",), pinned_bounce=True, optional=True)


def test_find_rejects_an_unknown_role() -> None:
    with pytest.raises(SystemExit, match=r"^unknown role nobody; choose from specifier, critic, coder, cleaner, .*, qa$"):
        pipeline.find("nobody")


@pytest.mark.parametrize(
    ("start", "stop", "expected"),
    [
        (None, None, pipeline.names()),
        ("coder", None, ["coder", "cleaner", "architect", "practices", "perf", "hardener", "qa"]),
        (None, "critic", ["specifier", "critic"]),
        ("cleaner", "architect", ["cleaner", "architect"]),
        ("qa", "qa", ["qa"]),
    ],
)
def test_window_slices_inclusively(start: str | None, stop: str | None, expected: list[str]) -> None:
    assert [step.name for step in pipeline.window(start, stop)] == expected


def test_window_helpers() -> None:
    coder = pipeline.find("coder")
    assert pipeline.started(False, coder, "coder") is True
    assert pipeline.started(False, coder, "qa") is False
    assert pipeline.started(True, coder, "qa") is True
    assert pipeline.taken(True, coder) == [coder]
    assert pipeline.taken(False, coder) == []
    assert pipeline.stop_here(True, coder, "coder") is True
    assert pipeline.stop_here(True, coder, None) is False
    assert pipeline.stop_here(False, coder, "coder") is False


def test_step_defaults() -> None:
    worker = Worker("w", None)
    judge = Judge("j", "sonar", bounce_to="w")
    assert (worker.audit, worker.pause_after) == (False, False)
    assert (judge.bounces, judge.pause_after, judge.writes, judge.pinned_bounce, judge.optional) == (0, False, (), False, False)


HYPER_ROLES = ["specifier", "critic", "coder", "architect", "hardener", "qa"]


def test_steps_default_to_the_whole_pipeline() -> None:
    assert pipeline.steps() is pipeline.PIPELINE
    assert pipeline.steps("hard") is pipeline.PIPELINE


def test_hyper_steps_drop_cleaner_practices_and_perf_and_gate_workers_full() -> None:
    assert pipeline.names("hyper") == HYPER_ROLES
    assert [step.tier for step in pipeline.steps("hyper")] == [None, None, "full", "full", "full", "qa"]
    assert pipeline.find("coder", "hyper") == Worker("coder", "full", audit=True)
    assert pipeline.find("hardener", "hyper") is pipeline.find("hardener")


def test_hyper_step_only_changes_coder_and_architect() -> None:
    assert pipeline.hyper_step(pipeline.find("architect")) == Worker("architect", "full")
    assert pipeline.hyper_step(pipeline.find("qa")) is pipeline.find("qa")


def test_find_under_hyper_rejects_a_dropped_role() -> None:
    with pytest.raises(SystemExit, match=r"^unknown role cleaner; choose from specifier, critic, coder, architect, hardener, qa$"):
        pipeline.find("cleaner", "hyper")


@pytest.mark.parametrize(
    ("start", "stop", "expected"),
    [
        (None, None, HYPER_ROLES),
        ("coder", "architect", ["coder", "architect"]),
        ("hardener", "qa", ["hardener", "qa"]),
    ],
)
def test_window_under_hyper(start: str | None, stop: str | None, expected: list[str]) -> None:
    assert [step.name for step in pipeline.window(start, stop, "hyper")] == expected


@pytest.mark.parametrize(
    ("start", "stop", "mode", "role", "choices"),
    [
        ("cleaner", None, "hyper", "cleaner", ", ".join(HYPER_ROLES)),
        (None, "cleaner", "hyper", "cleaner", ", ".join(HYPER_ROLES)),
        ("bogus", None, None, "bogus", "specifier, critic, coder, cleaner, architect, practices, perf, hardener, qa"),
        (None, "bogus", "changed", "bogus", "specifier, critic, coder, cleaner, architect, practices, perf, hardener, qa"),
    ],
)
def test_window_rejects_an_unknown_role(start: str | None, stop: str | None, mode: str | None, role: str, choices: str) -> None:
    with pytest.raises(SystemExit) as raised:
        pipeline.window(start, stop, mode)
    assert str(raised.value) == f"unknown role {role}; choose from {choices}"


def test_check_role() -> None:
    assert pipeline.check_role(None, "hyper") is None
    assert pipeline.check_role("qa", "hyper") is None
    with pytest.raises(SystemExit):
        pipeline.check_role("perf", "hyper")
