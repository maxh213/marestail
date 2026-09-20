from pathlib import Path

import pytest

from marestail import audit
from marestail.config import Config
from tests.conftest import make_context


def config(root: Path) -> Config:
    return make_context(root).config


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Feature: x\n  Scenario: Adds two numbers  \n    Given x\n  Scenario Outline: Many things\n", ["Adds two numbers", "Many things"]),
        ("Scenario:\n  Given a step\nScenario: b", ["Given a step", "b"]),
        ("Scenario:   \t", ["\t"]),
        ("Scenario:\n\n", []),
        ("Scenario: a Scenario: b\nnot Scenario: c\n\n\tScenario:c\r\n", ["a Scenario: b", "c"]),
        ("Scenario:\n  Scenario: nested\nScenario: after", ["Scenario: nested", "after"]),
        ("Scenario Outline : no\nScenarios: no\n", []),
        ("Scenario:\xa0x\xa0", ["x"]),
        ("", []),
    ],
)
def test_scenario_titles(text: str, expected: list[str]) -> None:
    assert audit.scenario_titles(text) == expected


def test_scenario_titles_stop_when_search_does_not_advance(monkeypatch: pytest.MonkeyPatch) -> None:
    class Hit:
        def end(self) -> int:
            return 9

    class Pattern:
        def search(self, *_args: object, **_kwargs: object) -> Hit:
            return Hit()

    monkeypatch.setattr(audit, "SCENARIO", Pattern())
    monkeypatch.setattr(audit, "scenario_title", lambda _text, start: (["t"], start))
    assert audit.scenario_titles("abcdefghij") == ["t"] * 11


def test_traces_empty_and_stuck_cursor_do_not_loop() -> None:
    assert audit.traces("") == []
    assert audit.traces("x") == []


def test_line_after_skips_the_newline() -> None:
    assert audit.line_after("ab\ncd", 0) == 3
    assert audit.line_after("ab\ncd", 2) == 3
    assert audit.line_after("ab", 0) == 2
    assert audit.line_after("ab", 2) == 2
    assert audit.line_after("ab\ncd\nef", 0) == 3
    assert audit.line_after("ab\ncd\nef", 3) == 6


def test_traces_stop_when_the_cursor_does_not_advance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(audit, "line_trace", lambda _text, start: ([], start))
    monkeypatch.setattr(audit, "line_end", lambda _text, end: end - 1)
    assert audit.traces("ab") == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "## Audit\n- Adds numbers -> tests/test_a.py::test_adds\n  - Other one  ->  tests/b.py::TestX::test_y  \n",
            [("Adds numbers", "tests/test_a.py", "test_adds"), ("Other one", "tests/b.py", "TestX::test_y")],
        ),
        ("- a -> b::c -> d::e", [("a", "b", "c -> d::e")]),
        ("- a->b::c", [("a", "b", "c")]),
        ("-\n  title -> f::n", [("title", "f", "n")]),
        ("- title\n  -> f::n", [("title", "f", "n")]),
        ("- title ->\n  f::n", [("title", "f", "n")]),
        ("-  -> f::n", [(" ", "f", "n")]),
        ("- a -> f::\n- b -> g::h", [("b", "g", "h")]),
        ("- a -> f::  ", [("a", "f", " ")]),
        ("- a -> f:: \n", [("a", "f", " ")]),
        ("- a -> f:::x", [("a", "f", ":x")]),
        ("- a -> ::x", []),
        ("- a -> f::x::y", [("a", "f", "x::y")]),
        ("- a -> f x::y", []),
        ("-> a::b", []),
        ("text - a -> b::c", []),
        ("* a -> b::c", []),
        ("- a -> b\n- c -> d::e", [("c", "d", "e")]),
        ("- -> -> f::n", [("->", "f", "n")]),
        ("- x\n\n-> f::n\n- y -> g::h", [("x", "f", "n"), ("y", "g", "h")]),
        ("-\n-> f::n", []),
        ("- \n-> f::n", [(" ", "f", "n")]),
        ("- a -> f::n\n\n\n- b -> g::m", [("a", "f", "n"), ("b", "g", "m")]),
        ("- a -> f::n\n  more\n", [("a", "f", "n")]),
        ("- a -> b:c", []),
        ("", []),
        ("\n\n", []),
        ("-", []),
        ("- a", []),
    ],
)
def test_traces(text: str, expected: list[tuple[str, str, str]]) -> None:
    assert audit.traces(text) == expected


@pytest.mark.parametrize(
    ("task_name", "expected"),
    [("02-login", ["login.feature"]), ("task-signup", ["signup.feature"]), ("other", ["login.feature", "signup.feature"])],
)
def test_feature_files_prefer_related(tmp_path: Path, task_name: str, expected: list[str]) -> None:
    for name in ("signup.feature", "login.feature", "notes.md"):
        write(tmp_path / "features" / name, "x")
    assert [file.name for file in audit.feature_files(config(tmp_path), task_name)] == expected


def test_feature_files_without_folder(tmp_path: Path) -> None:
    assert audit.feature_files(config(tmp_path), "t") == []


def test_listing_sorts_and_filters(tmp_path: Path) -> None:
    for name in ("b.md", "a.md", "c.txt"):
        write(tmp_path / name, "x")
    assert audit.listing(tmp_path, "*.md") == [tmp_path / "a.md", tmp_path / "b.md"]
    assert audit.listing(tmp_path / "missing", "*.md") == []


def test_is_related_accepts_stem_inside_the_task_name() -> None:
    inside = Path("log.feature")
    suffix = Path("login.feature")
    assert audit.is_related(inside, "login") is True
    assert audit.is_related(suffix, "x-login") is True
    assert audit.is_related(inside, "other") is False


def test_line_end_keeps_a_newline_at_the_start() -> None:
    assert audit.line_end("\nabc", 0) == 0
    assert audit.line_end("abc", 0) == 3
    assert audit.MISSING == -1


def test_line_trace_on_spaces_without_a_bullet() -> None:
    assert audit.line_trace("   ", 0) == ([], 0)
    assert audit.traces("   ") == []


def test_arrows_on_a_single_line_do_not_append_past_the_end() -> None:
    text = "- a -> b::c"
    found = audit.arrows(text, 0)
    assert found == [text.index("->")]
    assert len(text) not in found


def test_arrow_target_without_a_separator_is_missing() -> None:
    assert audit.arrow_target("- a -> b", 5) is None
    assert audit.arrow_target("not-an-arrow", 0) is None


def test_named_target_end_index_is_past_the_name() -> None:
    text = "- a -> b::cdef"
    split = text.index("::")
    found = audit.named_target(text, text.index("b"), split)
    assert found is not None
    (file, name), end = found
    assert (file, name) == ("b", "cdef")
    assert end == split + len(audit.SEPARATOR) + len(name)


def test_feature_paths_use_the_module_constants(tmp_path: Path) -> None:
    write(tmp_path / audit.FEATURES / "t.feature", "x")
    assert audit.FEATURE_GLOB == "*.feature"
    assert [file.name for file in audit.feature_files(config(tmp_path), "t")] == ["t.feature"]


def test_scenarios_across_files(tmp_path: Path) -> None:
    first = write(tmp_path / "a.feature", "Scenario: one\nScenario: two\n")
    second = write(tmp_path / "b.feature", "Scenario Outline: three\n")
    assert audit.scenarios([first, second]) == ["one", "two", "three"]


def test_normalise() -> None:
    assert audit.normalise("  Adds, TWO  numbers!! ") == "adds two numbers"


def test_test_exists(tmp_path: Path) -> None:
    write(tmp_path / "tests" / "test_a.py", "def test_adds():\n    pass\n")
    assert audit.test_exists(config(tmp_path), "tests/test_a.py", "test_adds") is True
    assert audit.test_exists(config(tmp_path), "tests/test_a.py", "TestX::test_adds") is True
    assert audit.test_exists(config(tmp_path), "tests/test_a.py", "test_other") is False
    assert audit.test_exists(config(tmp_path), "tests", "test_adds") is False
    assert audit.test_exists(config(tmp_path), "tests/missing.py", "test_adds") is False


def test_problems_without_features(tmp_path: Path) -> None:
    assert audit.problems(config(tmp_path), "t", "- a -> b::c") == ["audit: no feature file found for this task"]


def test_problems_without_scenarios(tmp_path: Path) -> None:
    write(tmp_path / "features" / "t.feature", "Feature: nothing\n")
    assert audit.problems(config(tmp_path), "t", "") == ["audit: no feature file found for this task"]


def test_problems_report_every_kind(tmp_path: Path) -> None:
    write(
        tmp_path / "features" / "t.feature", "Scenario: Adds numbers\nScenario: Subtracts\nScenario: Works end to end\nScenario: Missing\n"
    )
    write(tmp_path / "tests" / "test_a.py", "def test_adds(): pass\n")
    handoff = (
        "- adds NUMBERS -> tests/test_a.py::test_adds\n"
        "- Subtracts -> tests/test_a.py::test_subtracts\n"
        "- Works end to end -> qa/test_e2e.py::test_flow\n"
    )
    assert audit.problems(config(tmp_path), "t", handoff) == [
        "audit: no test traced for scenario 'Missing'",
        "audit: qa/test_e2e.py::test_flow is under a path the coder cannot edit, so it cannot prove this scenario; "
        "end-to-end tests under qa/ are written by the QA role. Trace the scenario to a test you can write.",
        "audit: tests/test_a.py::test_subtracts not found",
    ]


def test_problems_respect_the_role(tmp_path: Path) -> None:
    write(tmp_path / "features" / "t.feature", "Scenario: Flow\n")
    write(tmp_path / "qa" / "test_e2e.py", "def test_flow(): pass\n")
    assert audit.problems(config(tmp_path), "t", "- Flow -> qa/test_e2e.py::test_flow", role="specifier") == []


def test_problems_all_traced(tmp_path: Path) -> None:
    write(tmp_path / "features" / "t.feature", "Scenario: Adds\n")
    write(tmp_path / "tests" / "test_a.py", "def test_adds(): pass\n")
    assert audit.problems(config(tmp_path), "t", "- Adds -> tests/test_a.py::test_adds") == []


def test_instructions_name_the_feature_files(tmp_path: Path) -> None:
    write(tmp_path / "features" / "t.feature", "x")
    write(tmp_path / "features" / "u.feature", "x")
    text = audit.instructions(config(tmp_path), "zzz")
    assert text.startswith("Audit before you hand off. Re-read the task and features/t.feature, features/u.feature. For every Scenario")
    assert text.endswith("the QA role writes the end-to-end test from the QA procedure after the hardener.")


def test_instructions_without_feature_files(tmp_path: Path) -> None:
    text = audit.instructions(config(tmp_path), "t")
    assert text == (
        "Audit before you hand off. Re-read the task and features/*.feature. For every Scenario, find the test that proves it "
        "and would fail if that behaviour broke. If one has none, write it. Then in the handoff, under `## Audit`, "
        "write one line per scenario: `- <scenario title> -> <test file path>::<test function name>`. "
        "The runner checks every scenario is traced and every test exists. Trace only to tests you can write: never "
        "to files under `qa/` or `features/`, which are frozen for you; the QA role writes the end-to-end test from "
        "the QA procedure after the hardener."
    )
