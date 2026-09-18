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


def test_step_defaults() -> None:
    worker = Worker("w", None)
    judge = Judge("j", "sonar", bounce_to="w")
    assert (worker.audit, worker.pause_after) == (False, False)
    assert (judge.bounces, judge.pause_after, judge.writes, judge.pinned_bounce, judge.optional) == (0, False, (), False, False)
