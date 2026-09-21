import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from marestail.context import Context
from marestail.gates import ex_mutation
from marestail.report import Result
from tests.conftest import FakeRun, gate_shape, make_context

MUTATIONS = [
    {"location": {"file": "lib/a.ex", "line": 4}, "mutator": "Muex.Mutator.Arithmetic", "status": "Survived", "description": "+ -> -"},
    {"location": {"file": "lib/a.ex", "line": 5}, "mutator": "Muex.Mutator.Boolean", "status": "killed", "description": "x" * 90},
    {"location": {"file": "lib/b.ex", "line": 6}, "mutator": "Comparison", "status": "invalid"},
    {"location": {"file": "lib/b.ex", "line": 7}, "mutator": "Muex.Mutator.Literal", "status": "timeout", "description": "1 -> 2"},
    {"location": {"file": "lib/b.ex", "line": 8}, "status": "Equivalent"},
]


def shape(result: Result) -> tuple[str, bool, str, list[str]]:
    return gate_shape(result)


def project(root: Path, settings: dict[str, Any] | None = None, **fields: Any) -> Context:
    (root / "lib").mkdir()
    (root / "lib" / "a.ex").write_text("")
    return make_context(root, {"elixir": {"mutation_scope": "all", **(settings or {})}}, **fields)


def report(mutations: list[dict[str, Any]]) -> str:
    return "Compiling...\n" + json.dumps({"mutations": mutations}) + "\ntrailing {"


def test_bad_scope(tmp_path: Path) -> None:
    result = ex_mutation.run_gate(project(tmp_path, {"mutation_scope": 3}))
    assert shape(result) == ("ex.mutation", False, '[elixir] mutation_scope must be "changed" or "all", got 3', [])


def test_skips_without_changed_sources(tmp_path: Path) -> None:
    result = ex_mutation.run_gate(project(tmp_path, scope_changed=True, changed={"lib/gone.ex"}))
    assert shape(result) == ("ex.mutation", True, "skipped: no changed elixir sources", [])


@pytest.mark.parametrize(
    ("code", "summary", "findings"),
    [
        (127, "mix not available", ["mix is not installed: install Elixir"]),
        (1, "muex is not installed", ['add {:muex, "~> 0.11", only: [:dev, :test], runtime: false} to mix.exs and run mix deps.get']),
    ],
)
def test_muex_missing(tmp_path: Path, fake_run: Callable[..., FakeRun], code: int, summary: str, findings: list[str]) -> None:
    fake = fake_run(ex_mutation, [(code, "")])
    assert shape(ex_mutation.run_gate(project(tmp_path))) == ("ex.mutation", False, summary, findings)
    assert fake.calls == [["mix", "help", "muex"]]
    assert fake.options == [{"cwd": tmp_path, "timeout": 120}]


@pytest.mark.parametrize(
    ("output", "summary", "findings"),
    [
        ("compiled\n\nno json", "muex produced no report", ["compiled", "no json"]),
        ("prefix {not json", "muex report unreadable", ["prefix {not json"]),
        ('{"mutations": []}', "no mutants were generated", ['{"mutations": []}']),
        ('{"other": 1}', "no mutants were generated", ['{"other": 1}']),
    ],
)
def test_bad_reports(tmp_path: Path, fake_run: Callable[..., FakeRun], output: str, summary: str, findings: list[str]) -> None:
    fake_run(ex_mutation, [(0, ""), (1, output)])
    assert shape(ex_mutation.run_gate(project(tmp_path))) == ("ex.mutation", False, summary, findings)


def test_reports_survivors(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake = fake_run(ex_mutation, [(0, ""), (1, report(MUTATIONS))])
    result = ex_mutation.run_gate(project(tmp_path))
    findings = ["lib/a.ex:4 Arithmetic Survived: + -> -", "lib/b.ex:7 Literal timeout: 1 -> 2"]
    assert shape(result) == ("ex.mutation", False, "2 of 4 mutants not killed", findings)
    assert fake.calls[1] == ["mix", "muex", "--format", "json", "--fail-at", "0", "--no-filter", "--concurrency", "4"]
    assert fake.options[1] == {"cwd": tmp_path, "env": {"MIX_ENV": "test"}, "timeout": 7200}


def test_all_killed_scoped(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake = fake_run(ex_mutation, [(0, ""), (0, report(MUTATIONS[1:3]))])
    result = ex_mutation.run_gate(project(tmp_path, scope_changed=True, changed={"lib/a.ex"}))
    assert shape(result) == ("ex.mutation", True, "all 1 mutants killed", [])
    assert fake.calls[1][-2:] == ["--files", "lib/a.ex"]


def test_scope_note(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake_run(ex_mutation, [(0, ""), (0, report(MUTATIONS[:1]))])
    result = ex_mutation.run_gate(project(tmp_path, {"mutation_scope": "changed"}))
    assert result.summary == "1 of 1 mutants not killed (no base origin/master; full run)"


def test_describe_outside_root(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"elixir": {"root": "app"}})
    mutation = {"location": {"file": "../../elsewhere/x.ex"}, "status": "survived"}
    assert ex_mutation.describe(ctx, mutation) == f"{(tmp_path / '..' / 'elsewhere' / 'x.ex').resolve()}:0  survived: "
    assert ex_mutation.describe(ctx, {}) == "app/?:0  None: "


def test_describe_truncates(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    assert ex_mutation.describe(ctx, MUTATIONS[1]) == "lib/a.ex:5 Boolean killed: " + "x" * 80


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, None), (0, None), ("0", None), ("None", None), ("false", None), ("OFF", None), ("", None), ("90", 90), (15, 15)],
)
def test_mutation_timeout(tmp_path: Path, value: Any, expected: int | None) -> None:
    assert ex_mutation.mutation_timeout(make_context(tmp_path, {"elixir": {"mutation_timeout": value}})) == expected


def test_mutation_timeout_default(tmp_path: Path) -> None:
    assert ex_mutation.mutation_timeout(make_context(tmp_path)) == 7200


def test_command_with_every_option(tmp_path: Path) -> None:
    settings = {
        "muex_filter": True,
        "muex_optimize": False,
        "muex_preset": "strict",
        "muex_concurrency": 0,
        "muex_max_mutations": 50,
        "muex_mirror": ["lib", "test"],
    }
    command = ex_mutation.command(make_context(tmp_path, {"elixir": settings}), ["lib/a.ex", "lib/b.ex"])
    expected = ["--no-optimize", "--preset", "strict", "--max-mutations", "50", "--mirror", "lib,test", "--files", "lib/a.ex,lib/b.ex"]
    assert command == ["mix", "muex", "--format", "json", "--fail-at", "0", *expected]


@pytest.mark.parametrize(("mirror", "expected"), [(None, []), ([], []), ("lib", ["--mirror", "lib"]), ([1, "b"], ["--mirror", "1,b"])])
def test_mirror_option(mirror: Any, expected: list[str]) -> None:
    assert ex_mutation.mirror_option(mirror) == expected


def test_mutator_name_keeps_the_last_segment() -> None:
    assert ex_mutation.mutator_name("Muex.Mutator.Arithmetic") == "Arithmetic"
    assert ex_mutation.mutator_name("Comparison") == "Comparison"
    assert ex_mutation.ELIXIR_SUFFIXES == (".ex", ".exs")


def test_switches_default_to_no_filter(tmp_path: Path) -> None:
    assert ex_mutation.switches(make_context(tmp_path)) == ["--no-filter"]


def test_flag_off_only_is_false() -> None:
    assert ex_mutation.flag_off(False) is True
    assert ex_mutation.flag_off(True) is False


def test_flag_off_rejects_none() -> None:
    with pytest.raises(TypeError, match=r"^flag$"):
        ex_mutation.flag_off(None)


def test_mutation_list_missing_key_is_empty() -> None:
    assert ex_mutation.mutation_list({}) == []
    assert ex_mutation.mutation_list({"mutations": [{"status": "killed"}]}) == [{"status": "killed"}]


def test_status_reads_lowercase() -> None:
    assert ex_mutation.status({"status": "KiLLed"}) == "killed"
    assert ex_mutation.status({}) == ""
