from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from marestail import erlang
from marestail.context import Context
from marestail.gates import er_lint
from marestail.report import Result
from tests.conftest import FakeRun, gate_shape, make_context

HINT = "erlang unavailable: install Erlang/OTP 25+ (erl, erlc, escript), or docker with `docker pull erlang:27`"
WARNINGS = ["+warn_export_all", "+warn_export_vars", "+warn_shadow_vars", "+warn_obsolete_guard", "+warn_unused_import", "-Werror"]


def shape(result: Result) -> tuple[str, bool, str, list[str]]:
    return gate_shape(result)


def project(root: Path, names: tuple[str, ...] = ("src/a.erl", "test/a_tests.erl"), **fields: Any) -> Context:
    for name in names:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text("")
    (root / ".marestail").mkdir()
    return make_context(root, {"erlang": {"erlc": "erlc"}}, **fields)


def test_skips_unchanged_scope(tmp_path: Path) -> None:
    ctx = project(tmp_path, scope_changed=True, changed={"README.md"})
    assert shape(er_lint.run_gate(ctx)) == ("er.lint", True, "skipped: no changed erlang files", [])


def test_skips_without_files(tmp_path: Path) -> None:
    result = er_lint.run_gate(project(tmp_path, ()))
    assert shape(result) == ("er.lint", True, "skipped: no erlang sources under [erlang] sources (default src/)", [])


def test_clean(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake = fake_run(erlang, [(0, ""), (0, "")])
    result = er_lint.run_gate(project(tmp_path, scope_changed=True, changed={"src/a.hrl"}))
    assert shape(result) == ("er.lint", True, "erlc strong warnings clean", [])
    ebin = str(tmp_path / ".marestail" / "er-lint-ebin")
    assert fake.calls == [
        ["erlc", "+debug_info", *WARNINGS, "-o", ebin, str(tmp_path / "src/a.erl")],
        ["erlc", "-DTEST", "+debug_info", *WARNINGS, "-pa", ebin, "-o", ebin, str(tmp_path / "test/a_tests.erl")],
    ]
    assert [options["timeout"] for options in fake.options] == [900, 900]


def test_only_tests(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake = fake_run(erlang, [(0, "")])
    er_lint.run_gate(project(tmp_path, ("tests/t.erl",)))
    assert [call[1] for call in fake.calls] == ["-DTEST"]


def test_only_sources(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake = fake_run(erlang, [(0, "")])
    er_lint.run_gate(project(tmp_path, ("src/a.erl",)))
    assert [call[1] for call in fake.calls] == ["+debug_info"]


def test_hint_stops(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake = fake_run(erlang, [(1, "src/a.erl:1: bad"), (127, "erlc: not found (x)")])
    assert shape(er_lint.run_gate(project(tmp_path))) == ("er.lint", False, HINT, [HINT])
    assert len(fake.calls) == 2


def test_collects_findings(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    source = tmp_path / "src" / "a.erl"
    replies = [(1, f"{source}:3:5: Warning: variable 'X' is unused\nnoise\n"), (1, "test/a_tests.erl:9: function f/0 undefined")]
    fake_run(erlang, replies)
    result = er_lint.run_gate(project(tmp_path))
    findings = ["src/a.erl:3 Warning: variable 'X' is unused", "test/a_tests.erl:9 function f/0 undefined"]
    assert shape(result) == ("er.lint", False, "2 problems", findings)


def test_caps_findings(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake_run(erlang, [(1, "\n".join(f"src/a.erl:{n}: bad" for n in range(70))), (0, "")])
    result = er_lint.run_gate(project(tmp_path))
    assert (result.summary, len(result.findings)) == ("70 problems", 60)


@pytest.mark.parametrize(
    ("output", "changed", "expected"),
    [
        ("src/a.erl:3: bad\nsrc/b.hrl:4:1: worse\nx.txt:1: skip", {"src/b.hrl"}, ["src/b.hrl:4 worse"]),
        ("escript crashed\n\n  badarg\n", {"src/b.hrl"}, ["escript crashed", "  badarg"]),
        ("src/a.erl:3:bad", {"src/b.hrl"}, []),
    ],
)
def test_lint_findings_scoped(tmp_path: Path, output: str, changed: set[str], expected: list[str]) -> None:
    ctx = make_context(tmp_path, scope_changed=True, changed=changed)
    assert er_lint.lint_findings(output, ctx) == expected


def test_lint_findings_unscoped(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    assert er_lint.lint_findings("src/a.erl:3: bad\nsrc/b.hrl:4: worse", ctx) == ["src/a.erl:3 bad", "src/b.hrl:4 worse"]


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("a.erl:1:2:  x", ["a.erl:1 x"]),
        ("x.hrl:5: y", ["x.hrl:5 y"]),
        (".erl:1: z", []),
        ("a.erl.erl:3: w", ["a.erl.erl:3 w"]),
        ("b.erl:q.erl:4:5:6: v", ["b.erl:q.erl:4 6: v"]),
        ("c.erl:7:", ["c.erl:7 "]),
    ],
)
def test_erlc_findings_parse(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, output: str, expected: list[str]) -> None:
    monkeypatch.setattr(erlang, "rel", lambda ctx, path: path)
    assert er_lint.erlc_findings(output, make_context(tmp_path)) == expected


def test_failed_findings_ignore_output_when_code_is_zero() -> None:
    assert er_lint.failed_findings(0, ["src/a.erl:1 unused"]) == []
    assert er_lint.failed_findings(1, ["src/a.erl:1 unused"]) == ["src/a.erl:1 unused"]


def test_batch_findings_clean_code_drops_parsed_lines(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    assert er_lint.batch_findings(0, "src/a.erl:1: unused", ctx) == []


def test_batch_findings_rejects_a_missing_code(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match=r"^code$"):
        er_lint.batch_findings(None, "src/a.erl:1: unused", make_context(tmp_path))  # type: ignore[arg-type]


def test_erlang_suffixes_stay_lowercase() -> None:
    assert er_lint.ERLANG_SUFFIXES == (".erl", ".hrl")
    assert er_lint.LINT_TIMEOUT == 900
