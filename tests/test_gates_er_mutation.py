import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from marestail import erlang
from marestail.context import Context
from marestail.gates import er_mutation
from marestail.report import Result
from tests.conftest import FakeRun, make_context

HINT = "erlang unavailable: install Erlang/OTP 25+ (erl, erlc, escript), or docker with `docker pull erlang:27`"
Reply = tuple[int, str]


class Clock:
    def __init__(self, *moments: float) -> None:
        self.moments = list(moments)

    def time(self) -> float:
        return self.moments.pop(0) if len(self.moments) > 1 else self.moments[0]


def shape(result: Result) -> tuple[str, bool, str, list[str]]:
    return result.gate, result.ok, result.summary, result.findings


def project(
    root: Path, names: tuple[str, ...] = ("src/a.erl", "test/a_tests.erl"), settings: dict[str, Any] | None = None, **fields: Any
) -> Context:
    for name in names:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text("")
    (root / ".marestail").mkdir()
    config = {"erlc": "erlc", "escript": "escript", "mutation_scope": "all", **(settings or {})}
    return make_context(root, {"erlang": config}, **fields)


def mutant(root: Path, ident: int, line: int = 3) -> dict[str, Any]:
    return {
        "id": ident,
        "file": str(root / "src" / "a.erl"),
        "line": line,
        "operator": "comparison",
        "original": "<",
        "replacement": ">=",
        "mutant": f"/scratch/m{ident}/a.erl",
    }


def toolchain(mutants: list[dict[str, Any]] | None, replies: list[Reply]) -> Callable[[list[str]], Reply]:
    queue = list(replies)

    def reply(command: list[str]) -> Reply:
        if command[2:3] == ["mutants"] and mutants is not None:
            (Path(command[3]) / "mutants.json").write_text(json.dumps({"mutants": mutants}))
        return queue.pop(0) if queue else (0, "")

    return reply


def run(
    tmp_path: Path, fake_run: Callable[..., FakeRun], mutants: list[dict[str, Any]] | None, replies: list[Reply], **options: Any
) -> Result:
    fake_run(erlang, toolchain(mutants, replies))
    return er_mutation.run_gate(project(tmp_path, **options))


def test_skips_without_sources(tmp_path: Path) -> None:
    result = er_mutation.run_gate(project(tmp_path, ()))
    assert shape(result) == ("er.mutation", True, "skipped: no erlang sources under [erlang] sources (default src/)", [])


def test_bad_scope_setting(tmp_path: Path) -> None:
    result = er_mutation.run_gate(project(tmp_path, settings={"mutation_scope": "some"}))
    assert shape(result) == ("er.mutation", False, '[erlang] mutation_scope must be "changed" or "all", got \'some\'', [])


@pytest.mark.parametrize("changed", [{"README.md"}, {"src/gone.erl"}])
def test_skips_unchanged_sources(tmp_path: Path, changed: set[str]) -> None:
    result = er_mutation.run_gate(project(tmp_path, scope_changed=True, changed=changed))
    assert shape(result) == ("er.mutation", True, "skipped: no changed erlang sources", [])


def test_needs_tests(tmp_path: Path) -> None:
    result = er_mutation.run_gate(project(tmp_path, ("src/a.erl",)))
    finding = "marestail.toml:1 no test files under [erlang] test_dirs (default test/, tests/) or *_tests.erl next to the sources"
    assert shape(result) == ("er.mutation", False, "no eunit test files", [finding])


@pytest.mark.parametrize(
    ("mutants", "replies", "summary", "findings"),
    [
        (None, [(127, "escript: not found (x)")], HINT, [HINT]),
        (None, [(1, "boom\n")], "mutant generation failed", ["boom"]),
        (None, [(0, "wrote nothing")], "no mutant manifest written", ["wrote nothing"]),
        ([], [], "no mutants were generated", ["no mutable comparison, arithmetic or boolean operators found in the erlang sources"]),
        ("one", [(0, ""), (127, "erlc: not found (x)")], HINT, [HINT]),
        ("one", [(0, ""), (1, "src/a.erl:1: bad")], "sources failed to compile", ["src/a.erl:1: bad"]),
        ("one", [(0, ""), (0, ""), (1, "test/a_tests.erl:1: bad")], "tests failed to compile", ["test/a_tests.erl:1: bad"]),
        ("one", [(0, ""), (0, ""), (0, ""), (127, "escript: not found (x)")], HINT, [HINT]),
        (
            "one",
            [(0, ""), (0, ""), (0, ""), (1, "Failed: 1.")],
            "test suite fails on unmutated sources; fix the suite first",
            ["Failed: 1."],
        ),
        ("one", [(0, ""), (0, ""), (0, ""), (2, "crash")], "eunit run failed on unmutated sources", ["crash"]),
    ],
)
def test_early_failures(
    tmp_path: Path, fake_run: Callable[..., FakeRun], mutants: Any, replies: list[Reply], summary: str, findings: list[str]
) -> None:
    generated = [mutant(tmp_path, 1)] if mutants == "one" else mutants
    assert shape(run(tmp_path, fake_run, generated, replies)) == ("er.mutation", False, summary, findings)


def test_no_mutants_in_changed_sources(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    result = run(tmp_path, fake_run, [], [], scope_changed=True, changed={"src/a.erl"})
    assert result.findings == ["no mutable comparison, arithmetic or boolean operators found in the changed erlang sources"]


def test_runs_every_mutant(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    mutants = [mutant(tmp_path, ident, ident) for ident in range(1, 6)]
    outcomes = [(1, "bad"), (0, ""), (0, ""), (0, ""), (1, ""), (0, ""), (124, ""), (0, ""), (3, "")]
    fake = fake_run(erlang, toolchain(mutants, [(0, ""), (0, ""), (0, ""), (0, ""), *outcomes]))
    result = er_mutation.run_gate(project(tmp_path))
    finding = "src/a.erl:2 comparison mutant survived: < -> >="
    assert shape(result) == ("er.mutation", False, "1 of 4 mutants not killed (1 failed to compile)", [finding])
    scratch = tmp_path / ".marestail" / "er-mutation"
    base, test = str(scratch / "ebin-base"), str(scratch / "ebin-test")
    script = str(erlang.SCRIPT_DIR / "mutation.escript")
    assert fake.calls[:5] == [
        ["escript", script, "mutants", str(scratch), str(tmp_path / "src/a.erl")],
        ["erlc", "+debug_info", "-o", base, str(tmp_path / "src/a.erl")],
        ["erlc", "-DTEST", "+debug_info", "-pa", base, "-o", test, str(tmp_path / "test/a_tests.erl")],
        ["escript", script, "run", base, base, test],
        ["erlc", "+debug_info", "-I", str(tmp_path / "src"), "-o", str(scratch / "ebin-1"), "/scratch/m1/a.erl"],
    ]
    assert fake.calls[6] == ["escript", script, "run", str(scratch / "ebin-2"), base, test]
    assert [options["timeout"] for options in fake.options[:7]] == [900, 900, 900, 1800, 300, 300, 60]
    assert not (scratch / "ebin-1").exists()
    assert not (scratch / "ebin-2").exists()
    report = json.loads((tmp_path / ".marestail" / "er-mutation.json").read_text())
    assert [entry["status"] for entry in report["mutants"]] == ["invalid", "survived", "killed", "timeout", "error"]
    assert report["mutants"][0] == {
        "file": "src/a.erl",
        "line": 1,
        "operator": "comparison",
        "original": "<",
        "replacement": ">=",
        "status": "invalid",
    }


def test_all_killed_with_scope_note(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    result = run(tmp_path, fake_run, [mutant(tmp_path, 1)], [(0, "")] * 5 + [(1, "")], settings={"mutation_scope": "changed"})
    assert shape(result) == ("er.mutation", True, "all 1 mutants killed (no base origin/master; full run)", [])


def test_nothing_runnable(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    result = run(tmp_path, fake_run, [mutant(tmp_path, 1), mutant(tmp_path, 2)], [(0, "")] * 4 + [(1, ""), (1, "")])
    assert shape(result) == ("er.mutation", False, "no runnable mutants: all 2 failed to compile or were capped", [])


def test_cap_skips_mutants(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    mutants = [mutant(tmp_path, ident) for ident in range(1, 5)]
    fake = fake_run(erlang, toolchain(mutants, [(0, "")] * 4 + [(0, ""), (1, ""), (0, ""), (1, "")]))
    result = er_mutation.run_gate(project(tmp_path, settings={"mutation_max": 2}))
    assert shape(result) == ("er.mutation", True, "all 2 mutants killed (2 skipped by mutation_max)", [])
    assert [call[5] for call in fake.calls[4::2]] == [
        str(tmp_path / ".marestail/er-mutation/ebin-1"),
        str(tmp_path / ".marestail/er-mutation/ebin-3"),
    ]


def test_time_budget(tmp_path: Path, fake_run: Callable[..., FakeRun], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(er_mutation, "time", Clock(0, 10, 110, 6900, 7090))
    mutants = [mutant(tmp_path, 1, 4), mutant(tmp_path, 2, 8)]
    fake = fake_run(erlang, toolchain(mutants, [(0, "")] * 6))
    result = er_mutation.run_gate(project(tmp_path))
    findings = ["src/a.erl:4 comparison mutant survived: < -> >=", "src/a.erl:8 comparison mutant not checked (time budget): < -> >="]
    assert shape(result) == ("er.mutation", False, "1 of 2 mutants not killed (1 unchecked (time budget))", findings)
    assert fake.options[-1]["timeout"] == 300
    assert result.seconds == 7090


@pytest.mark.parametrize(("baseline", "expected"), [(1, 60), (100, 1000), (500, 1800)])
def test_per_mutant_timeout(
    tmp_path: Path, fake_run: Callable[..., FakeRun], monkeypatch: pytest.MonkeyPatch, baseline: float, expected: int
) -> None:
    monkeypatch.setattr(er_mutation, "time", Clock(0, 0, baseline, baseline))
    fake = fake_run(erlang, toolchain([mutant(tmp_path, 1)], [(0, "")] * 5 + [(1, "")]))
    er_mutation.run_gate(project(tmp_path))
    assert fake.options[-1]["timeout"] == expected


@pytest.mark.parametrize(("cap", "count", "skipped"), [(0, 3, 0), (None, 3, 0), (3, 3, 0), (5, 3, 0), (-1, 3, 0), (1, 3, 2), (2, 5, 3)])
def test_apply_cap(tmp_path: Path, cap: Any, count: int, skipped: int) -> None:
    mutants: list[dict[str, Any]] = [{"id": ident} for ident in range(count)]
    er_mutation.apply_cap(mutants, make_context(tmp_path, {"erlang": {"mutation_max": cap}}))
    assert sum(1 for entry in mutants if entry.get("status") == "skipped") == skipped


def test_apply_cap_spreads_kept_mutants(tmp_path: Path) -> None:
    mutants = [{"id": ident} for ident in range(7)]
    er_mutation.apply_cap(mutants, make_context(tmp_path, {"erlang": {"mutation_max": "3"}}))
    assert [entry["id"] for entry in mutants if "status" not in entry] == [0, 2, 4]


def test_mutate_files(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    sources = [tmp_path / "src/a.erl", tmp_path / "src/b.erl"]
    assert er_mutation.mutate_files(ctx, sources, None) == sources
    assert er_mutation.mutate_files(ctx, sources, ["src/b.erl", "src/c.erl"]) == [tmp_path / "src/b.erl"]
    assert er_mutation.mutate_files(ctx, sources, []) == []
