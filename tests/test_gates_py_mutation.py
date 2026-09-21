import json
from pathlib import Path
from typing import Any

import pytest

from marestail.context import MutationScope
from marestail.gates import py_mutation
from tests.conftest import Clock, checked, gate_shape, make_context, untimed

FULL = {"python": {"mutation_scope": "all"}}


def write_meta(root: Path, name: str, codes: dict[str, Any]) -> None:
    path = root / "mutants" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"exit_code_by_key": codes}))


def test_bad_setting(tmp_path: Path, fake_run: Any) -> None:
    fake = fake_run(py_mutation)
    result = checked(py_mutation.run_gate(make_context(tmp_path, {"python": {"mutation_scope": "some"}})), py_mutation.GATE)
    assert gate_shape(result)[:3] == (
        "py.mutation",
        False,
        '[python] mutation_scope must be "changed" or "all", got \'some\'',
    )
    assert fake.calls == []


def test_bad_setting_reports_no_findings(tmp_path: Path, fake_run: Any) -> None:
    fake_run(py_mutation)
    result = checked(py_mutation.run_gate(make_context(tmp_path, {"python": {"mutation_scope": "some"}})), py_mutation.GATE)
    assert result.findings == []


def test_scoped_note_follows_the_summary(tmp_path: Path, fake_run: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "pkg").mkdir()
    monkeypatch.setattr(
        "marestail.context.Context.mutation_files",
        lambda self, *args: MutationScope("scoped", ["pkg/a.py"], note="(scoped to 1 file)"),
    )

    def mutmut(command: list[str]) -> tuple[int, str]:
        write_meta(tmp_path, "m.meta", {"pkg.a.f__mutmut_1": 3})
        return 0, ""

    fake_run(py_mutation, mutmut)
    result = checked(py_mutation.run_gate(make_context(tmp_path)), py_mutation.GATE)
    assert result.summary == "all 1 mutants killed (scoped to 1 file)"


def test_bad_setting_measures_elapsed(tmp_path: Path, fake_run: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_run(py_mutation)
    monkeypatch.setattr("marestail.gates.py_mutation.time.time", Clock())
    result = checked(py_mutation.run_gate(make_context(tmp_path, {"python": {"mutation_scope": "some"}})), py_mutation.GATE)
    assert result.seconds == 0.25


def test_nothing_changed_skips(tmp_path: Path, fake_run: Any) -> None:
    fake = fake_run(py_mutation)
    result = untimed(
        py_mutation.run_gate(make_context(tmp_path, scope_changed=True, changed={"tests/test_a.py", "perf/b.py"})), py_mutation.GATE
    )
    assert gate_shape(result)[:3] == ("py.mutation", True, "skipped: no changed python sources")
    assert fake.calls == []


def test_mutmut_failure(tmp_path: Path, fake_run: Any) -> None:
    (tmp_path / "mutants" / "stale").mkdir(parents=True)
    (tmp_path / "keep").mkdir()
    (tmp_path / "MUTANTS").mkdir()
    fake = fake_run(py_mutation, [(1, "boom\ncrashed")])
    result = checked(
        py_mutation.run_gate(make_context(tmp_path, {"python": {"mutation_scope": "all", "mutation_workers": 8}})), py_mutation.GATE
    )
    assert (result.ok, result.summary, result.findings) == (False, "mutmut failed", ["boom", "crashed"])
    assert fake.calls == [[f"{tmp_path}/.venv/bin/mutmut", "run", "--max-children", "8"]]
    assert fake.options == [{"cwd": tmp_path, "timeout": 7200}]
    assert not (tmp_path / "mutants").exists()
    assert (tmp_path / "keep").is_dir()
    assert (tmp_path / "MUTANTS").is_dir()


def test_no_mutants_generated(tmp_path: Path, fake_run: Any) -> None:
    fake_run(py_mutation, [(1, "0 Mutants done")])
    result = checked(py_mutation.run_gate(make_context(tmp_path, FULL)), py_mutation.GATE)
    assert (result.ok, result.summary, result.findings) == (False, "no mutants were generated", ["0 Mutants done"])


def test_full_run_reports_survivors(tmp_path: Path, fake_run: Any) -> None:
    def mutmut(command: list[str]) -> tuple[int, str]:
        write_meta(tmp_path, "b.py.meta", {"b.x__mutmut_1": 0, "b.x__mutmut_2": 1, "b.x__mutmut_3": None})
        write_meta(tmp_path, "a.py.meta", {"a.y__mutmut_1": 99, "a.y__mutmut_2": 37, "a.y__mutmut_3": 34})
        return 0, ""

    fake = fake_run(py_mutation, mutmut)
    result = checked(py_mutation.run_gate(make_context(tmp_path, FULL)), py_mutation.GATE)
    assert result.findings == ["a.y__mutmut_1: suspicious", "b.x__mutmut_1: survived", "b.x__mutmut_3: not checked"]
    assert (result.ok, result.summary) == (False, "3 of 6 mutants not killed")
    assert fake.calls[0] == [f"{tmp_path}/.venv/bin/mutmut", "run", "--max-children", "4"]


def test_scoped_run_filters_by_pattern(tmp_path: Path, fake_run: Any) -> None:
    (tmp_path / "pkg").mkdir()

    def mutmut(command: list[str]) -> tuple[int, str]:
        write_meta(tmp_path, "m.meta", {"pkg.a.f__mutmut_1": 3, "pkg.b.g__mutmut_1": 0})
        return 0, ""

    fake = fake_run(py_mutation, mutmut)
    result = checked(py_mutation.run_gate(make_context(tmp_path, scope_changed=True, changed={"pkg/a.py"})), py_mutation.GATE)
    assert (result.ok, result.summary, result.findings) == (True, "all 1 mutants killed", [])
    assert fake.calls[0][:3] == [f"{tmp_path}/.venv/bin/mutmut", "run", "pkg.a.*"]


@pytest.mark.parametrize(
    ("total", "survivors", "note", "expected"),
    [
        (4, [], "", "all 4 mutants killed"),
        (4, [], "(no base x; full run)", "all 4 mutants killed (no base x; full run)"),
        (5, ["a", "b"], "", "2 of 5 mutants not killed"),
        (5, ["a"], "(n)", "1 of 5 mutants not killed (n)"),
    ],
)
def test_mutation_summary(total: int, survivors: list[str], note: str, expected: str) -> None:
    assert py_mutation.mutation_summary(total, survivors, note) == expected


def test_mutant_patterns(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"python": {"root": "src"}})
    files = ["src/pkg/mod.py", "src/tests/test_x.py", "perf/bench.py", "src/top.py"]
    assert py_mutation.mutant_patterns(ctx, files) == ["pkg.mod.*", "top.*"]


@pytest.mark.parametrize(
    ("scope", "patterns", "expected"),
    [
        (MutationScope("full"), [], False),
        (MutationScope("scoped", ["a.py"]), ["a.*"], False),
        (MutationScope("skip", []), [], True),
    ],
)
def test_nothing_to_mutate(scope: MutationScope, patterns: list[str], expected: bool) -> None:
    assert py_mutation.nothing_to_mutate(scope, patterns) is expected


def test_mutant_prefix_strips_a_trailing_star() -> None:
    assert py_mutation.mutant_prefix("marestail.report.*") == "marestail.report."
    assert py_mutation.mutant_prefix("plain") == "plain"


def test_exit_codes_without_the_key(tmp_path: Path) -> None:
    meta = tmp_path / "x.py.meta"
    meta.write_text("{}")
    assert py_mutation.exit_codes(meta) == {}
    meta.write_text('{"exit_code_by_key": {"a": 1}}')
    assert py_mutation.exit_codes(meta) == {"a": 1}
    meta.write_text('{"exit_code_by_key": []}')
    assert py_mutation.exit_codes(meta) == {}
    assert py_mutation.codes_field({}) == {}
    assert py_mutation.codes_field({"exit_code_by_key": {"a": 1}}) == {"a": 1}
