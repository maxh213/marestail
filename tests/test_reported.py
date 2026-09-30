from pathlib import Path
from typing import Any

import pytest

from marestail import reported
from marestail.config import Config
from marestail.gates import visual
from marestail.gates.visual import Report, Reproduction

REPORT = Report("t", "/donate.html", "square", "#widget")
CAPTURE = Reproduction(REPORT, "abc", ["desktop"], {})
OBSERVED = "spec written\n## Observed\nthe frame is 440px\n- radius does not clip: does not hold\n## Left\nnothing\n"
EVERY_STEP = ["specifier", "critic", "coder"]


class Recorder:
    def __init__(self, reply: Any) -> None:
        self.reply = reply
        self.calls: list[tuple[Any, ...]] = []

    def __call__(self, *args: Any) -> Any:
        self.calls.append(args)
        return self.reply


def patch(monkeypatch: pytest.MonkeyPatch, name: str, reply: Any) -> Recorder:
    recorder = Recorder(reply)
    monkeypatch.setattr(visual, name, recorder)
    return recorder


def handoff(folder: Path, name: str, text: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(text)
    return path


def test_reproduce_first_skips_windows_without_a_spec_step(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    report = patch(monkeypatch, "bug_report", REPORT)
    reproduce = patch(monkeypatch, "reproduce", None)
    assert reported.reproduce_first(Config(tmp_path, {}), tmp_path / "t.md", ["coder"]) == (None, True)
    assert (report.calls, reproduce.calls) == ([], [])


def test_reproduce_first_skips_tasks_without_a_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    report = patch(monkeypatch, "bug_report", None)
    reproduce = patch(monkeypatch, "reproduce", None)
    config = Config(tmp_path, {})
    assert reported.reproduce_first(config, tmp_path / "t.md", ["critic"]) == (None, True)
    assert report.calls == [(config, tmp_path / "t.md")]
    assert reproduce.calls == []


def test_reproduce_first_keeps_the_capture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    patch(monkeypatch, "bug_report", REPORT)
    reproduce = patch(monkeypatch, "reproduce", CAPTURE)
    config = Config(tmp_path, {})
    assert reported.reproduce_first(config, tmp_path / "t.md", EVERY_STEP) == (CAPTURE, True)
    assert reproduce.calls == [(config, REPORT)]
    assert capsys.readouterr().out == ""


def test_reproduce_first_stops_before_the_first_spec_step(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    patch(monkeypatch, "bug_report", REPORT)
    patch(monkeypatch, "reproduce", None)
    assert reported.reproduce_first(Config(tmp_path, {}), tmp_path / "t.md", ["coder", "critic", "specifier"]) == (None, False)
    assert capsys.readouterr().out == "pipeline stopped before critic: the bug could not be reproduced\n"


def test_prompt_sections_only_with_a_capture_and_only_for_spec_roles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    section = patch(monkeypatch, "reported_section", "BODY")
    config = Config(tmp_path, {})
    assert reported.prompt_sections(config, None, "specifier", tmp_path, "") == {}
    assert reported.prompt_sections(config, CAPTURE, "coder", tmp_path, "") == {}
    assert section.calls == []


def test_prompt_sections_ask_the_specifier_for_observed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    section = patch(monkeypatch, "reported_section", "BODY")
    config = Config(tmp_path, {})
    assert reported.prompt_sections(config, CAPTURE, "specifier", tmp_path, "kilo") == {"Reported": f"BODY\n{reported.ASK_OBSERVED}"}
    assert section.calls == [(config, CAPTURE, "kilo")]


def test_prompt_sections_give_the_critic_the_newest_observed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    patch(monkeypatch, "reported_section", "BODY")
    config = Config(tmp_path, {})
    handoffs = tmp_path / "handoffs"
    assert reported.prompt_sections(config, CAPTURE, "critic", handoffs, "") == {"Reported": "BODY", "Observed": ""}
    handoff(handoffs, "01-specifier.md", "old\n## Observed\nfirst\n")
    handoff(handoffs, "02-critic.md", "## Observed\nnot mine\n")
    handoff(handoffs, "03-specifier.md", OBSERVED)
    assert reported.prompt_sections(config, CAPTURE, "critic", handoffs, "") == {
        "Reported": "BODY",
        "Observed": "the frame is 440px\n- radius does not clip: does not hold",
    }


def test_observed_needs_the_heading_on_its_own_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    patch(monkeypatch, "reported_section", "BODY")
    config = Config(tmp_path, {})
    written = handoff(tmp_path, "01-specifier.md", "## Observed\nall of it")
    assert reported.prompt_sections(config, CAPTURE, "critic", tmp_path, "")["Observed"] == "all of it"
    written.write_text("no section\n### Observed\nx")
    assert reported.prompt_sections(config, CAPTURE, "critic", tmp_path, "")["Observed"] == ""
    written.write_text("x\n## Observed  \n")
    assert reported.handoff_problems(config, CAPTURE, "specifier", written) == []
    written.write_text("x ## Observed")
    assert reported.handoff_problems(config, CAPTURE, "specifier", written) == ["missing ## Observed section in 01-specifier.md"]


def test_handoff_problems_only_for_a_reproduced_specifier(tmp_path: Path) -> None:
    config = Config(tmp_path, {})
    report = handoff(tmp_path / ".marestail" / "handoffs" / "t", "01-specifier.md", "spec written\n")
    assert reported.handoff_problems(config, None, "specifier", report) == []
    assert reported.handoff_problems(config, CAPTURE, "coder", report) == []
    assert reported.handoff_problems(config, CAPTURE, "specifier", report) == [
        "missing ## Observed section in .marestail/handoffs/t/01-specifier.md"
    ]
    report.write_text(OBSERVED)
    assert reported.handoff_problems(config, CAPTURE, "specifier", report) == []


def test_handoff_problems_for_a_handoff_never_written(tmp_path: Path) -> None:
    missing = tmp_path / "01-specifier.md"
    assert reported.handoff_problems(Config(tmp_path, {}), CAPTURE, "specifier", missing) == [
        "missing ## Observed section in 01-specifier.md"
    ]


def test_observed_is_the_first_section_up_to_the_next_heading(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    patch(monkeypatch, "reported_section", "BODY")
    text = "## Observed\nfirst\n## Left\nx\n## Observed\nsecond\n## Config change\nnone\n"
    handoff(tmp_path, "01-specifier.md", text)
    assert reported.prompt_sections(Config(tmp_path, {}), CAPTURE, "critic", tmp_path, "")["Observed"] == "first"
