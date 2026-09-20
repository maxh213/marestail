import json
import time

import pytest

from marestail import report
from marestail.report import Result
from tests.conftest import Clock


def test_elapsed_subtracts_the_start(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "time", Clock(1000.0, 0.25))
    started = time.time()
    assert report.elapsed(started) == 0.25


def test_skipped() -> None:
    assert Result.skipped("py.tests", "no python") == Result("py.tests", True, "skipped: no python", [], 0.0)


def test_result_field_names() -> None:
    assert (report.GATE_FIELD, report.SECONDS_FIELD) == ("gate", "seconds")


def test_result_rejects_a_missing_gate() -> None:
    with pytest.raises(TypeError, match=r"^gate$"):
        Result(None, True, "ok")


def test_result_rejects_missing_seconds() -> None:
    with pytest.raises(TypeError, match=r"^seconds$"):
        Result("docs", True, "ok", [], None)


def test_render_one_passing() -> None:
    result = Result("py.lint", True, "clean", ["a", "b"], 1.26)
    assert report.render_one(result) == "[ok  ] py.lint        clean  (1.3s)\n       a\n       b"


def test_render_one_truncates_findings() -> None:
    result = Result("docs", False, "bad", [f"f{n}" for n in range(42)])
    lines = report.render_one(result).splitlines()
    assert lines[0] == "[FAIL] docs           bad  (0.0s)"
    assert (len(lines), lines[40], lines[41]) == (42, "       f39", "       ... 2 more")


def test_render_one_exactly_max_findings() -> None:
    result = Result("docs", False, "bad", [f"f{n}" for n in range(40)])
    assert report.render_one(result).splitlines()[-1] == "       f39"


def test_render_one_hides_a_single_extra_finding() -> None:
    result = Result("docs", False, "bad", [f"f{n}" for n in range(41)])
    assert report.render_one(result).splitlines()[-1] == "       ... 1 more"


def test_render_passed_without_scope() -> None:
    assert report.render([Result("a", True, "fine")]) == "[ok  ] a              fine  (0.0s)\n\nGATE PASSED"


def test_render_failed_with_scope() -> None:
    results = [Result("a", False, "x"), Result("b", True, "y"), Result("c", False, "z")]
    text = report.render(results, "changed")
    assert text.splitlines()[:2] == ["scope: changed", ""]
    assert text.splitlines()[-2:] == ["", "GATE FAILED: a, c"]


def test_render_empty() -> None:
    assert report.render([], "") == "\nGATE PASSED"


def test_verdict() -> None:
    assert report.verdict([Result("a", True, "")]) == "GATE PASSED"
    assert report.verdict([Result("a", False, ""), Result("b", False, "")]) == "GATE FAILED: a, b"


def test_to_json() -> None:
    text = report.to_json([Result("a", False, "s", ["f"], 2.5)], "changed", {"z", "b"})
    assert json.loads(text) == {
        "scope": "changed",
        "focus": ["b", "z"],
        "results": [{"gate": "a", "ok": False, "summary": "s", "findings": ["f"], "seconds": 2.5}],
    }
    assert text.startswith('{\n  "scope": "changed",\n  "focus": [\n    "b",')


def test_to_json_defaults() -> None:
    assert report.to_json([]) == '{\n  "scope": "all",\n  "focus": [],\n  "results": []\n}'
