import json
from pathlib import Path
from typing import Any

import pytest

from marestail.context import MutationScope
from marestail.gates import ts_mutation
from tests.conftest import make_context

TS = {"ts": {"root": "web"}}
BASE = ["npx", "stryker", "run", "--reporters", "json,progress", "--tempDirName", ".stryker-tmp", "--cleanTempDir", "always"]


def mutant(status: str, line: int, replacement: Any = "x") -> dict[str, Any]:
    return {"status": status, "mutatorName": "BooleanLiteral", "location": {"start": {"line": line}}, "replacement": replacement}


def stryker(root: Path, report: dict[str, Any] | None, seen: list[bool]) -> Any:
    def reply(command: list[str]) -> tuple[int, str]:
        seen.append((root / "web" / ".stryker-tmp").exists())
        (root / "web" / ".stryker-tmp").mkdir()
        if report is not None:
            path = root / "web" / ts_mutation.REPORT
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(report))
        return 1, "stryker log"

    return reply


def prepare(root: Path) -> None:
    (root / "web" / ".stryker-tmp").mkdir(parents=True)
    stale = root / "web" / ts_mutation.REPORT
    stale.parent.mkdir(parents=True)
    stale.write_text("{}")


def full_context(root: Path, **fields: Any) -> Any:
    return make_context(root, {"ts": {"root": "web", "mutation_scope": "all"}}, **fields)


def test_bad_setting_is_an_error(tmp_path: Path) -> None:
    result = ts_mutation.run_gate(make_context(tmp_path, {"ts": {"mutation_scope": "some"}}))

    assert (result.gate, result.ok, result.findings) == ("ts.mutation", False, [])
    assert result.summary == '[ts] mutation_scope must be "changed" or "all", got \'some\''


def test_nothing_changed_is_skipped(tmp_path: Path, fake_run) -> None:
    fake = fake_run(ts_mutation)

    result = ts_mutation.run_gate(make_context(tmp_path, TS, scope_changed=True, changed={"web/src/a.test.ts", "perf/b.ts"}))

    assert (result.ok, result.summary) == (True, "skipped: no changed typescript sources")
    assert fake.calls == []


def test_full_run_reports_survivors(tmp_path: Path, fake_run) -> None:
    prepare(tmp_path)
    report = {
        "files": {
            "src/a.ts": {"mutants": [mutant("Killed", 1), mutant("Survived", 2, "y" * 80), mutant("NoCoverage", 3, None)]},
            str(tmp_path / "web" / "src" / "b.ts"): {"mutants": [mutant("Timeout", 9)]},
            "src/c.ts": {},
        }
    }
    seen: list[bool] = []
    fake = fake_run(ts_mutation, stryker(tmp_path, report, seen))

    result = ts_mutation.run_gate(full_context(tmp_path))

    assert (result.ok, result.summary) == (False, "3 surviving mutants")
    assert result.findings == [
        "web/src/a.ts:2 BooleanLiteral Survived: " + "y" * 60,
        "web/src/a.ts:3 BooleanLiteral NoCoverage: None",
        "web/src/b.ts:9 BooleanLiteral Timeout: x",
    ]
    assert fake.calls == [BASE]
    assert fake.options == [{"cwd": tmp_path / "web", "timeout": 7200}]
    assert seen == [False]
    assert not (tmp_path / "web" / ".stryker-tmp").exists()


def test_missing_report_fails_and_cleans_up(tmp_path: Path, fake_run) -> None:
    prepare(tmp_path)
    fake_run(ts_mutation, stryker(tmp_path, None, []))

    result = ts_mutation.run_gate(full_context(tmp_path))

    assert (result.ok, result.summary, result.findings) == (False, "stryker produced no report (exit 1)", ["stryker log"])
    assert not (tmp_path / "web" / ".stryker-tmp").exists()


def test_scoped_run_mutates_changed_sources(tmp_path: Path, fake_run) -> None:
    for name in ["a.ts", "b.ts"]:
        (tmp_path / "web" / "src").mkdir(parents=True, exist_ok=True)
        (tmp_path / "web" / "src" / name).write_text("")
    report = {"files": {"src/a.ts": {"mutants": [mutant("Survived", 4)]}, "src/other.ts": {"mutants": [mutant("Survived", 1)]}}}
    fake = fake_run(ts_mutation, stryker(tmp_path, report, []))
    ctx = make_context(tmp_path, TS, scope_changed=True, changed={"web/src/a.ts", "web/src/b.ts"})

    result = ts_mutation.run_gate(ctx)

    assert result.findings == ["web/src/a.ts:4 BooleanLiteral Survived: x"]
    assert fake.calls == [[*BASE, "--mutate", "src/a.ts,src/b.ts"]]


def test_all_killed_with_a_note(tmp_path: Path, fake_run, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "web").mkdir()
    ctx = make_context(tmp_path, TS)
    monkeypatch.setattr(ctx, "mutation_files", lambda *args: MutationScope("full", note="(no base main; full run)"))
    fake_run(ts_mutation, stryker(tmp_path, {"files": {"src/a.ts": {"mutants": [mutant("Killed", 1)]}}}, []))

    result = ts_mutation.run_gate(ctx)

    assert (result.ok, result.summary, result.findings) == (True, "all mutants killed (no base main; full run)", [])


def test_survivor_summary_with_a_note(tmp_path: Path) -> None:
    result = ts_mutation.survivor_result(["a"], "(note)", 0.0)

    assert (result.ok, result.summary) == (False, "1 surviving mutants (note)")


def test_mutation_command() -> None:
    assert ts_mutation.mutation_command([]) == BASE
    assert ts_mutation.mutation_command(["a.ts", "b.ts"]) == [*BASE, "--mutate", "a.ts,b.ts"]


def test_changed_sources_drop_tests_specs_and_benchmarks(tmp_path: Path) -> None:
    files = ["web/src/a.ts", "web/src/a.test.ts", "web/src/a.spec.tsx", "web/perf/x.ts", "perf/y.ts"]

    assert ts_mutation.changed_sources(make_context(tmp_path, TS), files) == ["src/a.ts", "perf/x.ts"]


def test_surviving_skips_out_of_scope_files(tmp_path: Path) -> None:
    report = {
        "files": {
            "src/a.ts": {"mutants": [mutant("RuntimeError", 1), mutant("CompileError", 2)]},
            "src/b.ts": {"mutants": [mutant("Survived", 3)]},
        }
    }
    ctx = make_context(tmp_path, TS, scope_changed=True, changed={"web/src/a.ts"})

    assert ts_mutation.surviving(report, ctx) == [
        "web/src/a.ts:1 BooleanLiteral RuntimeError: x",
        "web/src/a.ts:2 BooleanLiteral CompileError: x",
    ]
    assert ts_mutation.surviving({}, ctx) == []
