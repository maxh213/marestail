import itertools
import json
import time
from pathlib import Path
from typing import Any

import pytest

from marestail import golang
from marestail.context import NO_CHANGED_MUTANTS, MutationScope
from marestail.gates import go_mutation
from tests.conftest import checked, make_context, untimed

GATE = go_mutation.GATE
FLAGS = go_mutation.FLAGS


@pytest.fixture(autouse=True)
def clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "time", itertools.count(1.0, 0.25).__next__)
    monkeypatch.setattr(golang, "tool", lambda ctx, name: "/bin/gremlins")


def plant(root: Path, relative: str, text: str = "package p\n") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def ready(root: Path, go: dict[str, Any] | None = None, **fields: Any) -> Any:
    plant(root, "go.mod", "module example.com/mod\n")
    ctx = make_context(root, {"go": {} if go is None else go}, **fields)
    ctx.memo[golang.MODULE_KEY] = "example.com/mod"
    ctx.memo[golang.TOOLCHAIN] = {"GOVERSION": "go1.25.0", "GOROOT": "/usr/lib/go", "GOBIN": "", "GOPATH": ""}
    return ctx


def mutant(status: str, line: int, column: int = 1, kind: str = "CONDITIONALS_BOUNDARY", name: str = "a.go") -> dict[str, Any]:
    return {"file_name": name, "type": kind, "status": status, "line": line, "column": column}


def report(ctx: Any, mutants: list[dict[str, Any]]) -> None:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in mutants:
        grouped.setdefault(str(item["file_name"]), []).append({key: value for key, value in item.items() if key != "file_name"})
    payload = {"files": [{"file_name": name, "mutations": found} for name, found in grouped.items()]}
    path = ctx.work / go_mutation.REPORT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


def unleash(ctx: Any, mutants: list[dict[str, Any]], code: int = 0, output: str = "") -> Any:
    def reply(command: list[str]) -> tuple[int, str]:
        if mutants:
            report(ctx, mutants)
        return code, output

    return reply


def test_bad_scope_stops_before_preflight(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(golang, "preflight", lambda ctx: (_ for _ in ()).throw(AssertionError("preflight")))
    ctx = make_context(tmp_path, {"go": {"mutation_scope": "some"}})
    result = checked(go_mutation.run_gate(ctx), GATE)
    assert result.ok is False
    assert result.summary == '[go] mutation_scope must be "changed" or "all", got \'some\''
    assert result.findings == []


def test_skips_when_nothing_is_targeted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(golang, "preflight", lambda ctx: (_ for _ in ()).throw(AssertionError("preflight")))
    scoped = make_context(tmp_path, {"go": {}}, scope_changed=True, changed={"README.md"})
    assert untimed(go_mutation.run_gate(scoped), GATE).summary == "skipped: no changed Go sources"
    full = make_context(tmp_path, {"go": {"mutation_scope": "all"}})
    assert untimed(go_mutation.run_gate(full), GATE).summary == "skipped: no Go sources"


def test_exclude_falls_back_until_it_is_set(tmp_path: Path) -> None:
    plant(tmp_path, "a.go")
    plant(tmp_path, "b.go")
    fallback = ready(tmp_path, {"coverage_exclude": ["a.go"], "mutation_scope": "all"})
    assert [path.name for path in go_mutation.mutation_targets(fallback, MutationScope("full"))] == ["b.go"]
    explicit = ready(tmp_path, {"coverage_exclude": ["a.go"], "mutation_exclude": [], "mutation_scope": "all"})
    assert [path.name for path in go_mutation.mutation_targets(explicit, MutationScope("full"))] == ["a.go", "b.go"]
    chosen = ready(tmp_path, {"mutation_exclude": ["b.go"], "mutation_scope": "all"})
    assert [path.name for path in go_mutation.mutation_targets(chosen, MutationScope("scoped", files=["a.go", "b.go"]))] == ["a.go"]
    narrow = ready(tmp_path, {"mutation_scope": "all"})
    assert [path.name for path in go_mutation.mutation_targets(narrow, MutationScope("scoped", files=["b.go"]))] == ["b.go"]


def test_command_flags_workers_tags_and_exclusions(tmp_path: Path, fake_run: Any) -> None:
    plant(tmp_path, "a.go")
    plant(tmp_path, "a+b.go")
    plant(tmp_path, "b.go")
    plant(tmp_path, "b_test.go")
    ctx = ready(
        tmp_path, {"mutation_scope": "all", "build_tags": ["a", "b"], "mutation_timeout": 9, "mutation_exclude": ["a.go", "a+b.go"]}
    )

    def reply(command: list[str]) -> tuple[int, str]:
        report(ctx, [mutant("KILLED", 1)])
        return 0, ""

    fake = fake_run(go_mutation, reply)
    result = checked(go_mutation.run_gate(ctx), GATE)
    report_path = str(ctx.work / go_mutation.REPORT)
    assert fake.calls[0][:4] == ["/bin/gremlins", "unleash", "--config", "/dev/null"]
    assert fake.calls[0][4:6] == ["--output", report_path]
    assert fake.calls[0][6 : 6 + len(FLAGS)] == FLAGS
    assert fake.calls[0][6 + len(FLAGS) :] == ["--tags", "a,b", "-E", r"^a\+b\.go$", "-E", r"^a\.go$", "."]
    assert "--workers" not in fake.calls[0]
    assert "--integration" not in fake.calls[0]
    assert "--coverpkg" not in fake.calls[0]
    assert "--diff" not in fake.calls[0]
    assert fake.options == [{"cwd": tmp_path, "timeout": 9}]
    assert result.summary == "all 1 mutants killed"


def test_workers_follow_the_knob(tmp_path: Path, fake_run: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    plant(tmp_path, "a.go")
    seen: list[list[str]] = []

    def reply(command: list[str]) -> tuple[int, str]:
        seen.append(command)
        report(ctx, [mutant("KILLED", 1)])
        return 0, ""

    ctx = ready(tmp_path, {"mutation_scope": "all", "mutation_workers": 0})
    fake_run(go_mutation, reply)
    checked(go_mutation.run_gate(ctx), GATE)
    assert "--workers" in seen[0]
    assert seen[0][seen[0].index("--workers") + 1] == "0"
    capped = ready(
        tmp_path,
        {"mutation_scope": "all"},
    )
    capped.config.raw["run"] = {"jobs": 100}
    checked(go_mutation.run_gate(capped), GATE)
    assert seen[1][seen[1].index("--workers") + 1] == "8"
    monkeypatch.setenv("MARESTAIL_JOBS", "2")
    language = ready(tmp_path, {"mutation_scope": "all", "mutation_workers": 3})
    checked(go_mutation.run_gate(language), GATE)
    assert seen[2][seen[2].index("--workers") + 1] == "2"


def test_jobs_zero_does_not_start_gremlins(tmp_path: Path, fake_run: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from marestail import gates

    plant(tmp_path, "a.go")
    monkeypatch.setenv("MARESTAIL_JOBS", "0")
    ctx = ready(tmp_path, {"mutation_scope": "all"})
    fake = fake_run(go_mutation, [])
    with pytest.raises(SystemExit, match="MARESTAIL_JOBS must be a whole number 1 or more, got '0'"):
        go_mutation.run_gate(ctx)
    assert fake.calls == []
    gate = next(item for item in gates.registry() if item.name == GATE)
    crashed = gates.run_one(gate, ctx)
    assert crashed.summary == "go.mutation crashed: SystemExit MARESTAIL_JOBS must be a whole number 1 or more, got '0'"
    assert fake.calls == []


def test_missing_tool(tmp_path: Path, fake_run: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    plant(tmp_path, "a.go")
    monkeypatch.setattr(golang, "tool", lambda ctx, name: f"{name} is not installed: {golang.INSTALL[name]}")
    fake = fake_run(go_mutation, [])
    result = checked(go_mutation.run_gate(ready(tmp_path, {"mutation_scope": "all"})), GATE)
    assert result.summary == "gremlins missing"
    assert result.findings == [f"gremlins is not installed: {golang.INSTALL['gremlins']}"]
    assert fake.calls == []


def test_preflight_problem(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plant(tmp_path, "a.go")
    monkeypatch.setattr(golang, "preflight", lambda ctx: "go is not installed: x")
    result = checked(go_mutation.run_gate(ready(tmp_path, {"mutation_scope": "all"})), GATE)
    assert (result.ok, result.summary, result.findings) == (False, "go toolchain not ready", ["go is not installed: x"])


def test_missing_report_and_no_viable_mutants(tmp_path: Path, fake_run: Any) -> None:
    plant(tmp_path, "a.go")
    ctx = ready(tmp_path, {"mutation_scope": "all"})
    stale = ctx.work / go_mutation.REPORT
    stale.parent.mkdir()
    stale.write_text('{"files": []}')
    fake_run(go_mutation, [(3, "No results to report.\n")])
    missing = checked(go_mutation.run_gate(ctx), GATE)
    assert missing.summary == go_mutation.missing_report(3)
    assert missing.findings == ["No results to report."]
    assert not stale.exists()
    ctx = ready(tmp_path, {"mutation_scope": "all"})
    fake_run(go_mutation, unleash(ctx, [mutant("NOT VIABLE", 1), mutant("SKIPPED", 2)]))
    empty = checked(go_mutation.run_gate(ctx), GATE)
    assert (empty.ok, empty.summary, empty.findings) == (False, "no mutants were generated", [])


def test_statuses_wording_and_scope_note(tmp_path: Path, fake_run: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    plant(tmp_path, "a.go")
    monkeypatch.setattr("marestail.changes.base_exists", lambda root, base: False)
    ctx = ready(tmp_path)
    ctx.config.raw["git"] = {"base": "HEAD"}
    mutants = [
        mutant("LIVED", 3, 4),
        mutant("TIMED OUT", 5),
        mutant("KILLED", 6),
        mutant("NOT COVERED", 7, 2, "INVERT_LOGICAL"),
        mutant("NOT VIABLE", 8),
        mutant("SKIPPED", 9),
        mutant("RUNNABLE", 10, 3, "ARITHMETIC_BASE"),
        {"file_name": "", "status": "LIVED", "line": 1, "column": 1},
    ]
    fake_run(go_mutation, unleash(ctx, mutants))
    result = checked(go_mutation.run_gate(ctx), GATE)
    assert result.findings == [
        ".:1 None at column 1 survived",
        "a.go:3 CONDITIONALS_BOUNDARY at column 4 survived",
        "a.go:7 INVERT_LOGICAL at column 2 not covered by its package's tests",
        "a.go:10 ARITHMETIC_BASE at column 3 survived",
    ]
    assert result.summary == "4 of 6 mutants not killed (no base HEAD; full run)"


def test_hyper_with_and_without_survivors(tmp_path: Path, fake_run: Any) -> None:
    plant(tmp_path, "a.go")
    ctx = ready(tmp_path, scope_changed=True, hyper=True, changed={"a.go"}, changed_lines_map={"a.go": {3}})
    fake_run(go_mutation, unleash(ctx, [mutant("LIVED", 3), mutant("LIVED", 9), mutant("KILLED", 3)]))
    kept = checked(go_mutation.run_gate(ctx), GATE)
    assert kept.findings == ["a.go:3 CONDITIONALS_BOUNDARY at column 1 survived"]
    assert kept.summary == "1 of 2 mutants not killed"
    ctx = ready(tmp_path, scope_changed=True, hyper=True, changed={"a.go"}, changed_lines_map={"a.go": {4}})
    fake_run(go_mutation, unleash(ctx, [mutant("KILLED", 4), mutant("LIVED", 9)]))
    clear = checked(go_mutation.run_gate(ctx), GATE)
    assert (clear.ok, clear.summary, clear.findings) == (True, NO_CHANGED_MUTANTS, [])


def test_findings_are_capped(tmp_path: Path, fake_run: Any) -> None:
    plant(tmp_path, "a.go")
    ctx = ready(tmp_path, {"mutation_scope": "all"})
    fake_run(go_mutation, unleash(ctx, [mutant("LIVED", number) for number in range(1, 62)]))
    result = checked(go_mutation.run_gate(ctx), GATE)
    assert (result.summary, len(result.findings)) == ("61 of 61 mutants not killed", 60)


def test_mutant_helpers() -> None:
    assert go_mutation.worker_args(None) == []
    assert go_mutation.worker_args(0) == ["--workers", "0"]
    assert go_mutation.mutants_of({}) == []
    assert go_mutation.mutants_of({"files": None}) == []
    assert go_mutation.tagged({"mutations": None}) == []
    assert go_mutation.status({}) == ""
