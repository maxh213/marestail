import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from marestail import remote
from marestail.context import Context, MutationScope
from marestail.gates import ex_mutation
from marestail.report import Result
from tests.conftest import FakeRun, checked, gate_shape, make_context, untimed

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


class FakeMutation:
    def __init__(self, code: int, output: str, where: str) -> None:
        self.reply = remote.Outcome(code, output, where)
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        ctx: Context,
        gate: str,
        build: Callable[[int], list[str]],
        cwd: Path,
        workers: int,
        env: dict[str, str] | None = None,
        timeout: int | None = 7200,
        pull: tuple[str, ...] = (),
    ) -> remote.Outcome:
        command, remote_command = build(workers), build(16)
        call = {"gate": gate, "command": command, "remote_command": remote_command, "cwd": cwd, "env": env, "timeout": timeout}
        self.calls.append({"ctx": ctx, **call, "pull": pull})
        return self.reply


def muex(fake_run: Callable[..., FakeRun], monkeypatch: pytest.MonkeyPatch, code: int, output: str, where: str = "") -> FakeMutation:
    fake_run(ex_mutation, [(0, "")])
    fake = FakeMutation(code, output, where)
    monkeypatch.setattr(remote, "run_mutation", fake)
    return fake


def watched(*parts: str, limit: str = "300") -> list[str]:
    return ["bash", "-c", ex_mutation.WATCHDOG, "muex-watchdog", limit, "15", "mix", "muex", "--format", "json", "--fail-at", "0", *parts]


def test_bad_scope(tmp_path: Path) -> None:
    result = checked(ex_mutation.run_gate(project(tmp_path, {"mutation_scope": 3})), ex_mutation.GATE)
    assert shape(result) == ("ex.mutation", False, '[elixir] mutation_scope must be "changed" or "all", got 3', [])


def test_skips_without_changed_sources(tmp_path: Path) -> None:
    result = untimed(ex_mutation.run_gate(project(tmp_path, scope_changed=True, changed={"lib/gone.ex"})), ex_mutation.GATE)
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
    assert shape(checked(ex_mutation.run_gate(project(tmp_path)), ex_mutation.GATE)) == ("ex.mutation", False, summary, findings)
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
def test_bad_reports(
    tmp_path: Path, fake_run: Callable[..., FakeRun], monkeypatch: pytest.MonkeyPatch, output: str, summary: str, findings: list[str]
) -> None:
    muex(fake_run, monkeypatch, 1, output)
    assert shape(checked(ex_mutation.run_gate(project(tmp_path)), ex_mutation.GATE)) == ("ex.mutation", False, summary, findings)


def test_bad_report_names_where_it_ran(tmp_path: Path, fake_run: Callable[..., FakeRun], monkeypatch: pytest.MonkeyPatch) -> None:
    muex(fake_run, monkeypatch, 1, "lost\n", " (on box, 3s)")
    result = checked(ex_mutation.run_gate(project(tmp_path)), ex_mutation.GATE)
    assert shape(result) == ("ex.mutation", False, "muex produced no report (on box, 3s)", ["lost"])


def test_reports_survivors(tmp_path: Path, fake_run: Callable[..., FakeRun], monkeypatch: pytest.MonkeyPatch) -> None:
    fake = muex(fake_run, monkeypatch, 1, report(MUTATIONS))
    ctx = project(tmp_path)
    result = checked(ex_mutation.run_gate(ctx), ex_mutation.GATE)
    findings = ["lib/a.ex:4 Arithmetic Survived: + -> -"]
    assert shape(result) == ("ex.mutation", False, "1 of 4 mutants not killed (1 killed by timeout)", findings)
    assert len(fake.calls) == 1
    assert fake.calls[0]["ctx"] is ctx
    assert fake.calls[0] == {
        "ctx": ctx,
        "gate": "ex.mutation",
        "command": watched("--no-filter", "--concurrency", "4"),
        "remote_command": watched("--no-filter", "--concurrency", "16"),
        "cwd": tmp_path,
        "env": {"MIX_ENV": "test"},
        "timeout": 7200,
        "pull": (),
    }


def test_reports_timeouts_when_the_section_says_fail(
    tmp_path: Path, fake_run: Callable[..., FakeRun], monkeypatch: pytest.MonkeyPatch
) -> None:
    muex(fake_run, monkeypatch, 1, report(MUTATIONS))
    result = checked(ex_mutation.run_gate(project(tmp_path, {"mutation_timeouts": "FAIL"})), ex_mutation.GATE)
    findings = ["lib/a.ex:4 Arithmetic Survived: + -> -", "lib/b.ex:7 Literal timeout: 1 -> 2"]
    assert shape(result) == ("ex.mutation", False, "2 of 4 mutants not killed", findings)


def test_all_killed_scoped(tmp_path: Path, fake_run: Callable[..., FakeRun], monkeypatch: pytest.MonkeyPatch) -> None:
    fake = muex(fake_run, monkeypatch, 0, report(MUTATIONS[1:3]))
    settings = {"mutation_timeout": 0, "muex_run_limit": 45, "muex_concurrency": 3}
    ctx = project(tmp_path, settings, scope_changed=True, changed={"lib/a.ex"})
    result = checked(ex_mutation.run_gate(ctx), ex_mutation.GATE)
    assert shape(result) == ("ex.mutation", True, "all 1 mutants killed", [])
    assert fake.calls == [
        {
            "ctx": ctx,
            "gate": "ex.mutation",
            "command": watched("--no-filter", "--concurrency", "3", "--files", "lib/a.ex", limit="45"),
            "remote_command": watched("--no-filter", "--concurrency", "16", "--files", "lib/a.ex", limit="45"),
            "cwd": tmp_path,
            "env": {"MIX_ENV": "test"},
            "timeout": None,
            "pull": (),
        }
    ]


def test_scope_note(tmp_path: Path, fake_run: Callable[..., FakeRun], monkeypatch: pytest.MonkeyPatch) -> None:
    muex(fake_run, monkeypatch, 0, report(MUTATIONS[:1]))
    result = checked(ex_mutation.run_gate(project(tmp_path, {"mutation_scope": "changed"})), ex_mutation.GATE)
    assert result.summary == "1 of 1 mutants not killed (no base origin/master; full run)"


@pytest.mark.parametrize(
    ("settings", "summary"),
    [
        ({}, "1 of 1 mutants not killed (on box, 3s)"),
        ({"mutation_scope": "changed"}, "1 of 1 mutants not killed (no base origin/master; full run) (on box, 3s)"),
    ],
)
def test_summary_names_where_it_ran(
    tmp_path: Path, fake_run: Callable[..., FakeRun], monkeypatch: pytest.MonkeyPatch, settings: dict[str, Any], summary: str
) -> None:
    muex(fake_run, monkeypatch, 0, report(MUTATIONS[:1]), " (on box, 3s)")
    result = checked(ex_mutation.run_gate(project(tmp_path, settings)), ex_mutation.GATE)
    assert shape(result) == ("ex.mutation", False, summary, ["lib/a.ex:4 Arithmetic Survived: + -> -"])


def test_located_keeps_the_scope_when_it_ran_here() -> None:
    scope = MutationScope("full", note="(n)")
    assert ex_mutation.located(scope, "") is scope
    assert ex_mutation.located(scope, " (on box, 1s)") == MutationScope("full", note="(n) (on box, 1s)")
    assert ex_mutation.located(MutationScope("full"), " (on box, 1s)") == MutationScope("full", note="(on box, 1s)")


ELIXIR_TIMEOUT = {
    "location": {"file": "lib/app.ex", "line": 4},
    "mutator": "Elixir.Muex.Mutators.Arithmetic",
    "status": "timeout",
    "description": "plus",
}


@pytest.mark.parametrize(
    ("settings", "verdict"),
    [
        ({}, (True, "all 1 mutants killed (1 killed by timeout)", [])),
        ({"mutation_timeouts": "fail"}, (False, "1 of 1 mutants not killed", ["lib/app.ex:4 Arithmetic timeout: plus"])),
    ],
)
def test_elixir_forgives_a_timeout_unless_the_section_says_fail(
    tmp_path: Path,
    fake_run: Callable[..., FakeRun],
    monkeypatch: pytest.MonkeyPatch,
    settings: dict[str, Any],
    verdict: tuple[bool, str, list[str]],
) -> None:
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "app.ex").write_text("")
    fake = muex(fake_run, monkeypatch, 0, json.dumps({"mutations": [ELIXIR_TIMEOUT]}))
    ctx = make_context(tmp_path, {"elixir": {"mutation_scope": "all", **settings}})
    result = checked(ex_mutation.run_gate(ctx), ex_mutation.GATE)
    assert shape(result) == ("ex.mutation", *verdict)
    assert fake.calls[0]["command"] == watched("--no-filter", "--concurrency", "4")


@pytest.mark.parametrize(
    ("section", "passing"),
    [({}, {"killed", "invalid", "equivalent", "timeout"}), ({"mutation_timeouts": "Fail"}, {"killed", "invalid", "equivalent"})],
)
def test_passing_statuses(tmp_path: Path, section: dict[str, Any], passing: set[str]) -> None:
    assert ex_mutation.passing_statuses(make_context(tmp_path, {"elixir": section})) == passing


def test_timed_out_counts_only_forgiven_timeouts() -> None:
    mutations = [{"status": "Timeout"}, {"status": "timeout"}, {"status": "killed"}]
    assert ex_mutation.timed_out(mutations, {"timeout"}) == 2
    assert ex_mutation.timed_out(mutations, {"killed"}) == 0


def test_judged_counts_timeouts_beside_survivors(tmp_path: Path) -> None:
    result = ex_mutation.judged(make_context(tmp_path), "(n)", MUTATIONS, 0.0)
    assert shape(result) == (
        "ex.mutation",
        False,
        "1 of 4 mutants not killed (1 killed by timeout) (n)",
        ["lib/a.ex:4 Arithmetic Survived: + -> -"],
    )


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
        "muex_max_mutations": 50,
        "muex_mirror": ["lib", "test"],
    }
    command = ex_mutation.command(make_context(tmp_path, {"elixir": settings}), ["lib/a.ex", "lib/b.ex"], 0)
    expected = ["--no-optimize", "--preset", "strict", "--max-mutations", "50", "--mirror", "lib,test", "--files", "lib/a.ex,lib/b.ex"]
    assert command == ["mix", "muex", "--format", "json", "--fail-at", "0", *expected]


@pytest.mark.parametrize(("mirror", "expected"), [(None, []), ([], []), ("lib", ["--mirror", "lib"]), ([1, "b"], ["--mirror", "1,b"])])
def test_mirror_option(mirror: Any, expected: list[str]) -> None:
    assert ex_mutation.mirror_option(mirror) == expected


def test_mutator_name_keeps_the_last_segment() -> None:
    assert ex_mutation.mutator_name("Muex.Mutator.Arithmetic") == "Arithmetic"
    assert ex_mutation.mutator_name("Comparison") == "Comparison"


def test_switches_default_to_no_filter(tmp_path: Path) -> None:
    assert ex_mutation.switches(make_context(tmp_path)) == ["--no-filter"]


def test_flag_off_only_is_false() -> None:
    assert ex_mutation.flag_off(False) is True
    assert ex_mutation.flag_off(True) is False


def test_mutation_list_missing_key_is_empty() -> None:
    assert ex_mutation.mutation_list({}) == []
    assert ex_mutation.mutation_list({"mutations": [{"status": "killed"}]}) == [{"status": "killed"}]


def test_status_reads_lowercase() -> None:
    assert ex_mutation.status({"status": "KiLLed"}) == "killed"
    assert ex_mutation.status({}) == ""


def test_hyper_counts_only_mutants_on_changed_lines(
    tmp_path: Path, fake_run: Callable[..., FakeRun], monkeypatch: pytest.MonkeyPatch
) -> None:
    muex(fake_run, monkeypatch, 1, report([*MUTATIONS[:2], {"location": {"file": "lib/a.ex"}, "status": "Survived"}]))
    ctx = project(tmp_path, scope_changed=True, hyper=True, changed={"lib/a.ex"}, changed_lines_map={"lib/a.ex": {4}})
    result = checked(ex_mutation.run_gate(ctx), ex_mutation.GATE)
    assert shape(result) == ("ex.mutation", False, "1 of 1 mutants not killed", ["lib/a.ex:4 Arithmetic Survived: + -> -"])
    assert ctx.file_level == 1


def test_hyper_passes_when_no_mutant_starts_on_a_changed_line(
    tmp_path: Path, fake_run: Callable[..., FakeRun], monkeypatch: pytest.MonkeyPatch
) -> None:
    muex(fake_run, monkeypatch, 1, report(MUTATIONS[:1]))
    ctx = project(tmp_path, scope_changed=True, hyper=True, changed={"lib/a.ex"}, changed_lines_map={"lib/a.ex": {9}})
    assert shape(checked(ex_mutation.run_gate(ctx), ex_mutation.GATE)) == ("ex.mutation", True, "no mutants on changed lines", [])
