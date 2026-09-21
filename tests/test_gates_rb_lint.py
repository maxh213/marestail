import json
from pathlib import Path
from typing import Any

import pytest

from marestail.gates import rb_lint
from tests.conftest import make_context


def rubocop_report(root: Path) -> str:
    files = [
        {
            "path": "app/models/user.rb",
            "offenses": [
                {
                    "severity": "convention",
                    "message": "Line is too long.",
                    "cop_name": "Layout/LineLength",
                    "location": {"line": 3, "column": 1},
                },
                {"message": "Odd.", "cop_name": None, "location": None},
            ],
        },
        {"path": str(root / "lib" / "tool.rb"), "offenses": [{"cop_name": "Style/X", "location": {"line": 9}}]},
        {"path": "clean.rb", "offenses": []},
        {"offenses": [{"message": "m"}]},
    ]
    return "warning: something\n" + json.dumps({"metadata": {}, "files": files, "summary": {"offense_count": 4}})


def test_skips_when_no_ruby_changed(tmp_path: Path, fake_run: Any) -> None:
    fake = fake_run(rb_lint)
    result = rb_lint.run_gate(make_context(tmp_path, scope_changed=True, changed={"a.py"}))
    assert (result.gate, result.ok, result.summary) == ("rb.lint", True, "skipped: no changed ruby files")
    assert fake.calls == []


def test_rubocop_missing(tmp_path: Path, fake_run: Any) -> None:
    fake = fake_run(rb_lint, [(127, "")])
    result = rb_lint.run_gate(make_context(tmp_path, scope_changed=True, changed={"views/a.jbuilder"}))
    assert (result.ok, result.summary, result.findings) == (
        False,
        "rubocop missing",
        ["rubocop is not installed: add gem 'rubocop' and bundle install"],
    )
    assert fake.calls == [["bundle", "exec", "rubocop", "--format", "json", "--force-exclusion"]]
    assert fake.options == [{"cwd": tmp_path, "timeout": 900}]


def test_reports_offenses(tmp_path: Path, fake_run: Any) -> None:
    fake_run(rb_lint, [(1, rubocop_report(tmp_path))])
    result = rb_lint.run_gate(make_context(tmp_path))
    assert (result.ok, result.summary) == (False, "4 problems")
    assert result.findings == [
        "app/models/user.rb:3 Layout/LineLength: Line is too long.",
        "app/models/user.rb:0 rubocop: Odd.",
        "lib/tool.rb:9 Style/X: ",
        ".:0 rubocop: m",
    ]


@pytest.mark.parametrize(
    ("reply", "ok", "summary", "findings"),
    [
        ((0, "  \n"), True, "rubocop clean", []),
        ((2, ""), False, "1 problems", ["rubocop failed: "]),
        ((0, json.dumps({"files": []})), True, "rubocop clean", []),
    ],
)
def test_empty_outputs(tmp_path: Path, fake_run: Any, reply: tuple[int, str], ok: bool, summary: str, findings: list[str]) -> None:
    fake_run(rb_lint, [reply])
    result = rb_lint.run_gate(make_context(tmp_path))
    assert (result.ok, result.summary, result.findings) == (ok, summary, findings)


def test_findings_are_capped(tmp_path: Path, fake_run: Any) -> None:
    fake_run(rb_lint, [(1, "\n".join(f"oops {n}" for n in range(70)))])
    result = rb_lint.run_gate(make_context(tmp_path))
    assert result.summary == "60 problems"
    assert result.findings == [f"oops {n}" for n in range(60)]


@pytest.mark.parametrize(("output", "expected"), [("bad\n\n  \nworse", ["bad", "worse"]), ("oops {not json", ["oops {not json"])])
def test_parse_non_json(tmp_path: Path, output: str, expected: list[str]) -> None:
    assert rb_lint.parse(output, make_context(tmp_path)) == expected


def test_parse_scoped(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"ruby": {"root": "."}}, scope_changed=True, changed={"lib/tool.rb"})
    assert rb_lint.parse(rubocop_report(tmp_path), ctx) == ["lib/tool.rb:9 Style/X: "]


def test_failed_tail_keeps_the_last_characters() -> None:
    assert rb_lint.failed_tail("x" * 250) == "x" * 200
    assert rb_lint.failed_tail("  short  ") == "short"


def test_list_field_defaults_and_rejects_a_non_list() -> None:
    assert rb_lint.list_field({}, "files") == []
    assert rb_lint.list_field({"files": [{"path": "a.rb"}]}, "files") == [{"path": "a.rb"}]


def test_list_field_rejects_a_non_list() -> None:
    with pytest.raises(TypeError, match=r"^list$"):
        rb_lint.list_field({"files": {}}, "files")


def test_parse_under_ruby_root(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"ruby": {"root": "web"}})
    output = json.dumps({"files": [{"path": "app/a.rb", "offenses": [{"cop_name": "C", "message": "m", "location": {"line": 2}}]}]})
    assert rb_lint.parse(output, ctx) == ["web/app/a.rb:2 C: m"]
