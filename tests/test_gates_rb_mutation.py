import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from marestail.gates import rb_mutation
from tests.conftest import gate_shape, make_context

USER = "class User\n  module Named\n  end\nend\nclass Admin::Boss < User\n"
INFO = ["bundle", "info", "mutant"]
SURVIVOR = {"mutation_result": {"mutation_type": "evil"}, "criteria_result": {"test_result": False, "timeout": False}}
KILLED = {"mutation_result": {"mutation_type": "evil"}, "criteria_result": {"test_result": True}}
NEUTRAL = {"mutation_result": {"mutation_type": "neutral"}, "criteria_result": {}}


def scoped(root: Path, raw: dict[str, Any] | None = None) -> Any:
    path = root / "app" / "models" / "user.rb"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(USER)
    return make_context(root, raw or {"ruby": {"exec": ["bundle", "exec"]}}, scope_changed=True, changed={"app/models/user.rb"})


def session(root: Path) -> dict[str, Any]:
    return {
        "subject_results": [
            {
                "identification": "User#name:app/models/user.rb:3",
                "source_path": str(root / "app" / "models" / "user.rb"),
                "coverage_results": [SURVIVOR, KILLED, SURVIVOR, NEUTRAL, {}],
            },
            {"identification": "weird", "source_path": "lib/x.rb", "coverage_results": [{"mutation_result": {"mutation_type": "noop"}}]},
            {},
        ]
    }


def writes_session(root: Path, report: str, output: str = "") -> Callable[[list[str]], tuple[int, str]]:
    def reply(command: list[str]) -> tuple[int, str]:
        if command[-2:] == ["info", "mutant"]:
            return 0, "mutant (0.12)"
        folder = root / ".mutant" / "results"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "old.json").write_text("{}")
        os.utime(folder / "old.json", (1, 1))
        (folder / "new.json").write_text(report)
        return 1, output

    return reply


def test_mutation_default_is_enabled() -> None:
    assert rb_mutation.MUTATION_DEFAULT is True
    assert rb_mutation.mutation_off(True) is False
    assert rb_mutation.mutation_off(False) is True


def test_mutation_off_rejects_none() -> None:
    with pytest.raises(TypeError, match=r"^flag$"):
        rb_mutation.mutation_off(None)


def test_disabled(tmp_path: Path) -> None:
    result = rb_mutation.run_gate(make_context(tmp_path, {"ruby": {"mutation": False}}))
    assert gate_shape(result) == ("rb.mutation", True, "skipped: disabled: [ruby] mutation = false", [])


def test_bad_scope_setting(tmp_path: Path) -> None:
    result = rb_mutation.run_gate(make_context(tmp_path, {"ruby": {"mutation_scope": "some"}}))
    assert (result.ok, result.summary, result.findings) == (False, '[ruby] mutation_scope must be "changed" or "all", got \'some\'', [])


def test_nothing_changed(tmp_path: Path, fake_run: Any) -> None:
    fake = fake_run(rb_mutation)
    result = rb_mutation.run_gate(make_context(tmp_path, scope_changed=True, changed={"spec/user_spec.rb"}))
    assert gate_shape(result)[:3] == ("rb.mutation", True, "skipped: no changed ruby sources")
    assert fake.calls == []


@pytest.mark.parametrize(
    ("code", "summary", "finding"),
    [
        (127, "bundle not available", "bundle is not installed: install ruby and bundler"),
        (7, "mutant is not in the bundle", f"mutant is not installed: {rb_mutation.INSTALL}"),
    ],
)
def test_bundle_problems(tmp_path: Path, fake_run: Any, code: int, summary: str, finding: str) -> None:
    fake = fake_run(rb_mutation, [(code, "")])
    result = rb_mutation.run_gate(scoped(tmp_path, {"ruby": {"exec": ["bin/bundle", "exec"]}}))
    assert gate_shape(result) == ("rb.mutation", False, summary, [finding])
    assert fake.calls == [["bin/bundle", "info", "mutant"]]
    assert fake.options == [{"cwd": tmp_path, "timeout": 120}]


def test_session_report(tmp_path: Path, fake_run: Any) -> None:
    fake = fake_run(rb_mutation, writes_session(tmp_path, json.dumps(session(tmp_path))))
    result = rb_mutation.run_gate(scoped(tmp_path))
    assert fake.calls == [INFO, ["bundle", "exec", "mutant", "run", "Admin::Boss*", "Named*", "User*"]]
    assert fake.options[1] == {"cwd": tmp_path, "timeout": 7200}
    assert (result.ok, result.summary) == (False, "5 of 6 mutants not killed")
    assert result.findings == [
        "app/models/user.rb:3 User#name: 3 mutants survived",
        "app/models/user.rb:3 User#name: 1 neutral mutant failed, tests do not pass unmutated",
        "lib/x.rb:0 weird: 1 noop mutant failed",
    ]


def test_unreadable_session(tmp_path: Path, fake_run: Any) -> None:
    fake_run(rb_mutation, writes_session(tmp_path, "{broken", "boom\n"))
    result = rb_mutation.run_gate(scoped(tmp_path))
    assert (result.ok, result.summary, result.findings) == (False, "mutant session report unreadable", ["boom"])


def test_session_with_no_mutants(tmp_path: Path, fake_run: Any) -> None:
    fake_run(rb_mutation, writes_session(tmp_path, json.dumps({"subject_results": []}), "nothing"))
    result = rb_mutation.run_gate(scoped(tmp_path))
    assert (result.ok, result.summary, result.findings) == (False, "no mutants were generated", ["nothing"])


def stdout_report(root: Path) -> str:
    user = root / "app" / "models" / "user.rb"
    return f"Mutant environment\nevil:User#name:{user}:3:ab12c\nevil:User#name:{user}:3:ffff1\nneutral::lib/x.rb:9:0000\nResults: 10\n"


def test_stdout_report_full_run(tmp_path: Path, fake_run: Any) -> None:
    fake = fake_run(rb_mutation, [(0, ""), (1, stdout_report(tmp_path))])
    ctx = make_context(tmp_path, {"ruby": {"mutation_scope": "all"}})
    result = rb_mutation.run_gate(ctx)
    assert fake.calls == [["bundle", "info", "mutant"], ["bundle", "exec", "mutant", "run"]]
    assert (result.ok, result.summary) == (False, "3 of 10 mutants not killed")
    assert result.findings == [
        "app/models/user.rb:3 User#name: 2 mutants survived",
        "lib/x.rb:9 ?: 1 neutral mutant failed, tests do not pass unmutated",
    ]


def test_stdout_all_killed_with_note(tmp_path: Path, fake_run: Any) -> None:
    fake_run(rb_mutation, [(0, ""), (0, "Results: 4\n")])
    result = rb_mutation.run_gate(make_context(tmp_path, {"git": {"base": "origin/none"}}))
    assert (result.ok, result.summary, result.findings) == (True, "all 4 mutants killed (no base origin/none; full run)", [])


def test_no_report(tmp_path: Path, fake_run: Any) -> None:
    fake_run(rb_mutation, [(0, ""), (2, "crashed\n")])
    result = rb_mutation.run_gate(make_context(tmp_path, {"ruby": {"mutation_scope": "all"}}))
    assert (result.ok, result.summary, result.findings) == (False, "mutant produced no report (exit 2)", ["crashed"])


def test_findings_capped() -> None:
    failures = [(f"f{n}.rb", n, "X", "evil") for n in range(70)]
    result = rb_mutation.verdict(100, failures, "", 0.0, "")
    assert (result.summary, len(result.findings)) == ("70 of 100 mutants not killed", 60)
    assert result.findings[0] == "f0.rb:0 X: 1 mutant survived"


@pytest.mark.parametrize(("value", "expected"), [(["bundle", "exec"], ["bundle"]), (["exec"], ["bundle"]), ("bin/x", ["bundle"])])
def test_bundler(tmp_path: Path, value: Any, expected: list[str]) -> None:
    assert rb_mutation.bundler(make_context(tmp_path, {"ruby": {"exec": value}})) == expected


def test_changed_subjects(tmp_path: Path) -> None:
    for name in ("app/a.rb", "spec/a_spec.rb", "perf/b.rb"):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("class Alpha\nend\nmodule Beta::Gamma\n")
    files = ["app/a.rb", "spec/a_spec.rb", "perf/b.rb", "app/missing.rb"]
    assert rb_mutation.changed_subjects(make_context(tmp_path), files) == ["Alpha*", "Beta::Gamma*"]


@pytest.mark.parametrize(
    ("identification", "expected"),
    [("A#b:app/a.rb:12", ("A#b", 12)), ("A#b:app/a.rb:x", ("A#b:app/a.rb:x", 0)), ("", ("?", 0)), ("a:b", ("a:b", 0))],
)
def test_label(identification: str, expected: tuple[str, int]) -> None:
    assert rb_mutation.label(identification) == expected


def test_sessions_without_folder(tmp_path: Path) -> None:
    assert rb_mutation.sessions(tmp_path) == set()


def test_exec_prefix_rejects_none() -> None:
    with pytest.raises(TypeError, match=r"^exec$"):
        rb_mutation.exec_prefix(None)


def test_with_note_rejects_none() -> None:
    with pytest.raises(TypeError, match=r"^note$"):
        rb_mutation.with_note("all killed", None)  # type: ignore[arg-type]


def test_colons_from_right_include_a_trailing_colon() -> None:
    assert rb_mutation.colons_from_right("a:b:") == [3, 1]


def test_right_colon_splits_from_the_end() -> None:
    assert rb_mutation.right_colon("a:b:c") == ("a:b", ":", "c")
    assert rb_mutation.right_colon("x:y") == ("x", ":", "y")
    assert rb_mutation.right_colon(":y") == ("", ":", "y")
    assert rb_mutation.right_colon("a:") == ("a", ":", "")
    assert rb_mutation.right_colon("abc") == ("", "", "abc")
    assert rb_mutation.COLON == ":"
    assert rb_mutation.EMPTY == ""


def test_split_label_keeps_an_empty_path() -> None:
    assert rb_mutation.split_label(":file.rb:12") == [":file.rb", "", "12"]
    assert rb_mutation.split_label("plain") == ["plain"]


def test_session_failures_defaults_missing_keys(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    assert rb_mutation.session_failures({}, ctx) == (0, [])
    assert rb_mutation.list_field({}, rb_mutation.SUBJECT_RESULTS) == []


def test_session_failures_rejects_a_non_list(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    with pytest.raises(TypeError, match=r"^list$"):
        rb_mutation.session_failures({rb_mutation.SUBJECT_RESULTS: {}}, ctx)


def test_subject_failures_defaults_missing_keys(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    assert rb_mutation.subject_failures({rb_mutation.COVERAGE_RESULTS: [SURVIVOR]}, ctx) == [(".", 0, "?", "evil")]
    assert rb_mutation.subject_failures({}, ctx) == []


def test_text_field_rejects_a_non_str() -> None:
    with pytest.raises(TypeError, match=r"^text$"):
        rb_mutation.text_field({rb_mutation.IDENTIFICATION: 1}, rb_mutation.IDENTIFICATION)


def test_session_result_rejects_a_missing_note(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    with pytest.raises(TypeError, match=r"^note$"):
        rb_mutation.session_result(ctx, set(), "out", 0.0, None)  # type: ignore[arg-type]


def test_stdout_result_rejects_a_missing_note(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    with pytest.raises(TypeError, match=r"^note$"):
        rb_mutation.stdout_result(ctx, 0, "Results: 1\n", 0.0, None)  # type: ignore[arg-type]


def test_verdict_rejects_a_missing_output() -> None:
    with pytest.raises(TypeError, match=r"^output$"):
        rb_mutation.verdict(1, [], None, 0.0, "")  # type: ignore[arg-type]


def test_verdict_rejects_a_missing_note_value() -> None:
    with pytest.raises(TypeError, match=r"^note$"):
        rb_mutation.verdict(1, [], "out", 0.0, None)  # type: ignore[arg-type]


def test_verdict_requires_a_note() -> None:
    with pytest.raises(TypeError):
        rb_mutation.verdict(1, [], "out", 0.0)  # type: ignore[call-arg]


def test_verdict_keeps_a_nonempty_note() -> None:
    result = rb_mutation.verdict(1, [], "out", 0.0, "scoped")
    assert result.summary.endswith("scoped")


def test_mutable_skips_vendor() -> None:
    assert rb_mutation.mutable(Path("vendor/a.rb")) is False
    assert rb_mutation.mutable(Path("app/a.rb")) is True


def test_constants_replace_invalid_bytes(tmp_path: Path) -> None:
    path = tmp_path / "a.rb"
    path.write_bytes(b"class A\n\xff\n")
    assert "A" in rb_mutation.constants(path)
    assert rb_mutation.REPLACE == "replace"


def test_label_keeps_colons_in_the_owner() -> None:
    assert rb_mutation.label("A#b:app/foo:bar.rb:12") == ("A#b:app/foo", 12)


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("evil:a:b:1:c:2:x", [("evil", "a:b:1:c", "2")]),
        ("noop:x:1:y", [("noop", "x", "1")]),
        ("neutral::1:y", []),
        ("evilx:a:1:b", []),
        ("evil:a:1:b c", []),
        ("evil:a:1:\nnoop:s:2:z", [("noop", "s", "2")]),
        ("evil:a::2:q", [("evil", "a:", "2")]),
    ],
)
def test_identifications(output: str, expected: list[tuple[str, str, str]]) -> None:
    assert rb_mutation.identifications(output) == expected
