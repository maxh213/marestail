import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from marestail import remote
from marestail.context import Context, MutationScope
from marestail.gates import _stryker, ts_mutation
from tests.conftest import FakeRun, Reply, checked, make_context, untimed

TS = {"ts": {"root": "web"}}
BASE = [
    "npx",
    "stryker",
    "run",
    "--reporters",
    "json,progress",
    "--tempDirName",
    ".stryker-tmp",
    "--cleanTempDir",
    "always",
    "--dryRunTimeoutMinutes",
    "120",
]
PULL = ("web/reports/mutation/mutation.json", "web/reports/stryker-incremental.json")
Replies = list[Reply] | Callable[[list[str]], Reply]


class Offload(FakeRun):
    def __init__(self, replies: Replies, where: str) -> None:
        super().__init__(replies)
        self.where = where
        self.builds: list[Callable[[bool], list[str]]] = []

    def run_mutation(
        self,
        ctx: Any,
        gate: str,
        build: Callable[[bool], list[str]],
        cwd: Path,
        env: dict[str, str] | None = None,
        timeout: int | None = 0,
        pull: tuple[str, ...] = (),
    ) -> remote.Outcome:
        if not isinstance(ctx, Context):
            raise TypeError("ctx")
        self.builds.append(build)
        code, output = self(build(False), cwd, gate=gate, env=env, timeout=timeout, pull=pull)
        return remote.Outcome(code, output, self.where)


@pytest.fixture
def offload(monkeypatch: pytest.MonkeyPatch) -> Callable[..., Offload]:
    def install(replies: Replies | None = None, where: str = "") -> Offload:
        fake = Offload([] if replies is None else replies, where)
        monkeypatch.setattr(remote, "run_mutation", fake.run_mutation)
        return fake

    return install


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
    result = checked(ts_mutation.run_gate(make_context(tmp_path, {"ts": {"mutation_scope": "some"}})), ts_mutation.GATE)

    assert (result.gate, result.ok, result.findings) == ("ts.mutation", False, [])
    assert result.summary == '[ts] mutation_scope must be "changed" or "all", got \'some\''


def test_nothing_changed_is_skipped(tmp_path: Path, offload: Any) -> None:
    fake = offload()

    result = untimed(
        ts_mutation.run_gate(make_context(tmp_path, TS, scope_changed=True, changed={"web/src/a.test.ts", "perf/b.ts"})), ts_mutation.GATE
    )

    assert (result.ok, result.summary) == (True, "skipped: no changed typescript sources")
    assert fake.calls == []


def test_full_run_reports_survivors(tmp_path: Path, offload: Any) -> None:
    prepare(tmp_path)
    report = {
        "files": {
            "src/a.ts": {"mutants": [mutant("Killed", 1), mutant("Survived", 2, "y" * 80), mutant("NoCoverage", 3, None)]},
            str(tmp_path / "web" / "src" / "b.ts"): {"mutants": [mutant("Timeout", 9)]},
            "src/c.ts": {},
        }
    }
    seen: list[bool] = []
    fake = offload(stryker(tmp_path, report, seen))

    result = checked(ts_mutation.run_gate(full_context(tmp_path)), ts_mutation.GATE)

    assert (result.ok, result.summary) == (False, "2 surviving mutants (1 killed by timeout)")
    assert result.findings == [
        "web/src/a.ts:2 BooleanLiteral Survived: " + "y" * 60,
        "web/src/a.ts:3 BooleanLiteral NoCoverage: None",
    ]
    assert fake.calls == [BASE]
    assert fake.options == [{"cwd": tmp_path / "web", "gate": "ts.mutation", "env": None, "timeout": 7200, "pull": PULL}]
    assert seen == [False]
    assert not (tmp_path / "web" / ".stryker-tmp").exists()


def test_missing_report_fails_and_cleans_up(tmp_path: Path, offload: Any) -> None:
    prepare(tmp_path)
    offload(stryker(tmp_path, None, []))

    result = checked(ts_mutation.run_gate(full_context(tmp_path)), ts_mutation.GATE)

    assert (result.ok, result.summary, result.findings) == (False, "stryker produced no report (exit 1)", ["stryker log"])
    assert not (tmp_path / "web" / ".stryker-tmp").exists()


def test_scoped_run_mutates_changed_sources(tmp_path: Path, offload: Any) -> None:
    for name in ["a.ts", "b.ts"]:
        (tmp_path / "web" / "src").mkdir(parents=True, exist_ok=True)
        (tmp_path / "web" / "src" / name).write_text("")
    report = {"files": {"src/a.ts": {"mutants": [mutant("Survived", 4)]}, "src/other.ts": {"mutants": [mutant("Survived", 1)]}}}
    fake = offload(stryker(tmp_path, report, []))
    ctx = make_context(tmp_path, TS, scope_changed=True, changed={"web/src/a.ts", "web/src/b.ts"})

    result = checked(ts_mutation.run_gate(ctx), ts_mutation.GATE)

    assert result.findings == ["web/src/a.ts:4 BooleanLiteral Survived: x"]
    assert fake.calls == [[*BASE, "--mutate", "src/a.ts,src/b.ts"]]


def test_all_killed_with_a_note(tmp_path: Path, offload: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "web").mkdir()
    ctx = make_context(tmp_path, TS)
    monkeypatch.setattr(ctx, "mutation_files", lambda *args: MutationScope("full", note="(no base main; full run)"))
    offload(stryker(tmp_path, {"files": {"src/a.ts": {"mutants": [mutant("Killed", 1)]}}}, []))

    result = checked(ts_mutation.run_gate(ctx), ts_mutation.GATE)

    assert (result.ok, result.summary, result.findings) == (True, "all mutants killed (no base main; full run)", [])


def test_survivor_summary_with_a_note(tmp_path: Path) -> None:
    result = ts_mutation.survivor_result(["a"], "(note)", 0.0)

    assert (result.ok, result.summary) == (False, "1 surviving mutants (note)")


def test_mutation_command(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, TS)

    assert ts_mutation.mutation_command(ctx, []) == BASE
    assert ts_mutation.mutation_command(ctx, ["a.ts", "b.ts"]) == [*BASE, "--mutate", "a.ts,b.ts"]


def test_mutation_command_uses_the_tooling_config(tmp_path: Path) -> None:
    tooling = tmp_path / ".marestail" / "tooling"
    ctx = make_context(tmp_path, {"ts": {"root": ".", "tooling": ".marestail/tooling"}})
    stryker = str(tooling / "node_modules" / ".bin" / "stryker")

    assert ts_mutation.mutation_command(ctx, []) == [stryker, *BASE[2:]]
    tooling.mkdir(parents=True)
    (tooling / "stryker.config.json").write_text("{}")
    assert ts_mutation.mutation_command(ctx, []) == [stryker, "run", str(tooling / "stryker.config.json"), *BASE[3:]]


def test_changed_sources_drop_tests_specs_and_benchmarks(tmp_path: Path) -> None:
    files = ["web/src/a.ts", "web/src/a.test.ts", "web/src/a.spec.tsx", "web/perf/x.ts", "perf/y.ts"]

    assert _stryker.changed_sources(make_context(tmp_path, TS), files) == ["src/a.ts", "perf/x.ts"]


def test_surviving_skips_out_of_scope_files(tmp_path: Path) -> None:
    report = {
        "files": {
            "src/a.ts": {"mutants": [mutant("RuntimeError", 1), mutant("CompileError", 2), mutant("Timeout", 5)]},
            "src/b.ts": {"mutants": [mutant("Survived", 3)]},
        }
    }
    ctx = make_context(tmp_path, TS, scope_changed=True, changed={"web/src/a.ts"})

    assert ts_mutation.survivors_of(ts_mutation.placed(report, ctx)) == ["web/src/a.ts:1 BooleanLiteral RuntimeError: x"]
    assert ts_mutation.placed({}, ctx) == []


def test_replacement_text_defaults_and_clips() -> None:
    assert ts_mutation.replacement_text({}) == ""
    assert ts_mutation.replacement_text({"replacement": "x" * 80}) == "x" * 60


def test_json_map_and_list_defaults() -> None:
    assert _stryker.json_map({}, "files") == {}
    assert _stryker.json_list({}, "mutants") == []


def test_drop_tree_skips_a_missing_path(tmp_path: Path) -> None:
    missing = tmp_path / "gone"
    _stryker.drop_tree(missing)
    assert not missing.exists()
    present = tmp_path / "tmp"
    present.mkdir()
    (present / "x").write_text("x")
    _stryker.drop_tree(present)
    assert not present.exists()


def hyper(root: Path, lines: dict[str, set[int]]) -> Any:
    (root / "web").mkdir(exist_ok=True)
    return make_context(root, TS, scope_changed=True, hyper=True, changed=set(lines), changed_lines_map=lines)


def test_hyper_mutates_and_reports_only_changed_lines(tmp_path: Path, offload: Any) -> None:
    report = {"files": {"src/a.ts": {"mutants": [mutant("Survived", 9), mutant("Survived", 10, ""), mutant("Killed", 11)]}}}
    fake = offload(stryker(tmp_path, report, []))
    ctx = hyper(tmp_path, {"web/src/a.ts": {10, 11, 14}, "web/src/a.test.ts": {1}})

    result = checked(ts_mutation.run_gate(ctx), ts_mutation.GATE)

    assert (result.ok, result.summary, result.findings) == (False, "1 surviving mutants", ["web/src/a.ts:10 BooleanLiteral Survived: "])
    assert fake.calls == [[*BASE, "--mutate", "src/a.ts:10-11,src/a.ts:14-14"]]


def test_hyper_passes_when_no_mutant_starts_on_a_changed_line(tmp_path: Path, offload: Any) -> None:
    offload(stryker(tmp_path, {"files": {"src/a.ts": {"mutants": [mutant("Survived", 9)]}}}, []))

    result = checked(ts_mutation.run_gate(hyper(tmp_path, {"web/src/a.ts": {10}})), ts_mutation.GATE)

    assert (result.ok, result.summary, result.findings) == (True, "no mutants on changed lines", [])


def test_hyper_missing_report_still_fails(tmp_path: Path, offload: Any) -> None:
    offload(stryker(tmp_path, None, []))

    result = checked(ts_mutation.run_gate(hyper(tmp_path, {"web/src/a.ts": {10}})), ts_mutation.GATE)

    assert (result.ok, result.summary) == (False, "stryker produced no report (exit 1)")


def test_spans_group_consecutive_lines() -> None:
    assert _stryker.spans([1, 2, 3, 7, 9, 10]) == [[1, 3], [7, 7], [9, 10]]
    assert _stryker.spans([]) == []


def test_hyper_targets_skip_a_source_with_no_changed_lines(tmp_path: Path) -> None:
    ctx = hyper(tmp_path, {"web/src/a.ts": {3}})
    assert _stryker.targets(ctx, ["src/a.ts", "src/b.ts"]) == ["src/a.ts:3-3"]


TOOLING = ".marestail/tooling"
COMMAND = {"ts": {"root": "web", "tooling": TOOLING}, "hyper": {"test_cmd": "node t.js"}}


def command_context(root: Path, lines: dict[str, set[int]]) -> Any:
    (root / "web").mkdir(exist_ok=True)
    return make_context(root, COMMAND, scope_changed=True, hyper=True, changed=set(lines), changed_lines_map=lines)


def command_stryker(root: Path, report: dict[str, Any] | None, code: int = 0) -> Any:
    def reply(command: list[str]) -> tuple[int, str]:
        (root / "web" / _stryker.COMMAND_TEMP).mkdir(parents=True)
        if report is not None:
            (root / ".marestail" / _stryker.COMMAND_REPORT).write_text(json.dumps(report))
        return code, "stryker log"

    return reply


def test_command_proof_runs_stryker_from_tooling_with_the_written_config(tmp_path: Path, offload: Any) -> None:
    report = {"files": {"src/a.js": {"mutants": [mutant("Killed", 7), mutant("Survived", 2)]}}}
    fake = offload(command_stryker(tmp_path, report))
    ctx = command_context(tmp_path, {"web/src/a.js": {7}, "web/src/a.test.js": {1}, "web/src/b.py": {1}})

    result = checked(ts_mutation.run_gate(ctx), ts_mutation.GATE)

    config = tmp_path / ".marestail" / "stryker" / "command.config.json"
    assert (result.ok, result.summary, result.findings) == (True, "all mutants killed (proof: mutation via [hyper] test_cmd)", [])
    command = [str(tmp_path / TOOLING / "node_modules" / ".bin" / "stryker"), "run", str(config)]
    assert fake.calls == [command]
    assert fake.builds[0](True) == command
    assert fake.options == [{"cwd": tmp_path / "web", "gate": "", "env": None, "timeout": 7200, "pull": ()}]
    assert json.loads(config.read_text()) == {
        "testRunner": "command",
        "commandRunner": {"command": "node t.js"},
        "coverageAnalysis": "off",
        "reporters": ["json", "progress"],
        "jsonReporter": {"fileName": str(tmp_path / ".marestail" / "stryker" / "mutation.json")},
        "tempDirName": ".marestail/stryker-tmp",
        "cleanTempDir": "always",
        "ignorePatterns": [".marestail"],
        "mutate": ["src/a.js:7-7"],
    }
    assert not (tmp_path / "web" / _stryker.COMMAND_TEMP).exists()


@pytest.mark.parametrize("existing", [False, True])
def test_command_config_writes_the_pinned_text(tmp_path: Path, existing: bool) -> None:
    if existing:
        (tmp_path / ".marestail" / "stryker").mkdir(parents=True)
    ctx = command_context(tmp_path, {"web/src/a.js": {7}})

    path = _stryker.command_config(ctx, ["src/a.js:7-7"])

    report = json.dumps(str(tmp_path / ".marestail" / "stryker" / "mutation.json"))
    assert path == tmp_path / ".marestail" / "stryker" / "command.config.json"
    assert path.read_text() == (
        "{\n"
        '  "testRunner": "command",\n'
        '  "commandRunner": {\n'
        '    "command": "node t.js"\n'
        "  },\n"
        '  "coverageAnalysis": "off",\n'
        '  "reporters": [\n'
        '    "json",\n'
        '    "progress"\n'
        "  ],\n"
        '  "jsonReporter": {\n'
        f'    "fileName": {report}\n'
        "  },\n"
        '  "tempDirName": ".marestail/stryker-tmp",\n'
        '  "cleanTempDir": "always",\n'
        '  "ignorePatterns": [\n'
        '    ".marestail"\n'
        "  ],\n"
        '  "mutate": [\n'
        '    "src/a.js:7-7"\n'
        "  ]\n"
        "}\n"
    )


def test_command_proof_hints_when_nothing_is_killed(tmp_path: Path, offload: Any) -> None:
    report = {"files": {"src/a.js": {"mutants": [mutant("Survived", 7), mutant("Survived", 7, "y"), mutant("Survived", 3)]}}}
    offload(command_stryker(tmp_path, report))

    result = checked(ts_mutation.run_gate(command_context(tmp_path, {"web/src/a.js": {7}})), ts_mutation.GATE)

    assert (result.ok, result.summary) == (False, "2 surviving mutants (proof: mutation via [hyper] test_cmd)")
    assert result.findings == [
        "hint: no mutant was killed; a test that loads code with vm must pass process into the sandbox, "
        "or no assertion depends on the changed lines",
        "web/src/a.js:7 BooleanLiteral Survived: x",
        "web/src/a.js:7 BooleanLiteral Survived: y",
    ]


def test_command_proof_gives_no_hint_when_a_mutant_is_killed(tmp_path: Path, offload: Any) -> None:
    report = {"files": {"src/a.js": {"mutants": [mutant("Killed", 7), mutant("Survived", 7)]}}}
    offload(command_stryker(tmp_path, report))

    result = checked(ts_mutation.run_gate(command_context(tmp_path, {"web/src/a.js": {7}})), ts_mutation.GATE)

    assert result.findings == ["web/src/a.js:7 BooleanLiteral Survived: x"]


@pytest.mark.parametrize("report", [None, {"files": {"src/a.js": {"mutants": [mutant("Survived", 2)]}}}])
def test_command_proof_with_no_mutants_on_changed_lines_passes(tmp_path: Path, offload: Any, report: dict[str, Any] | None) -> None:
    offload(command_stryker(tmp_path, report))

    result = checked(ts_mutation.run_gate(command_context(tmp_path, {"web/src/a.js": {7}})), ts_mutation.GATE)

    assert (result.ok, result.summary, result.findings) == (True, "no mutants on changed lines", [])


def test_command_proof_without_a_report_after_a_failure_fails(tmp_path: Path, offload: Any) -> None:
    offload([(127, "stryker: not found")])

    result = checked(ts_mutation.run_gate(command_context(tmp_path, {"web/src/a.js": {7}})), ts_mutation.GATE)

    assert (result.ok, result.summary, result.findings) == (False, "stryker produced no report (exit 127)", ["stryker: not found"])


def test_command_proof_skips_when_only_tests_changed(tmp_path: Path, offload: Any) -> None:
    fake = offload()

    result = untimed(ts_mutation.run_gate(command_context(tmp_path, {"web/src/a.test.js": {1}})), ts_mutation.GATE)

    assert result.summary == "skipped: no changed typescript sources"
    assert fake.calls == []


def test_command_proof_runs_once_per_context(tmp_path: Path, offload: Any) -> None:
    fake = offload(command_stryker(tmp_path, None))
    ctx = command_context(tmp_path, {"web/src/a.js": {7}})

    first = _stryker.command_proof(ctx, ["src/a.js:7-7"])

    assert _stryker.command_proof(ctx, ["src/a.js:7-7"]) is first
    assert (first.code, first.report, len(fake.calls)) == (0, None, 1)


def test_command_targets_include_javascript_but_not_tests(tmp_path: Path) -> None:
    ctx = command_context(tmp_path, {"web/src/a.js": {1, 2}, "web/src/b.cjs": {4}, "web/src/a.test.js": {1}, "web/x.md": {1}})
    assert _stryker.command_targets(ctx) == ["src/a.js:1-2", "src/b.cjs:4-4"]


def test_suffixes_widen_only_under_test_cmd(tmp_path: Path) -> None:
    assert ts_mutation.suffixes(command_context(tmp_path, {})) == (".js", ".mjs", ".cjs", ".ts", ".tsx")
    assert ts_mutation.suffixes(hyper(tmp_path, {})) == (".ts", ".tsx")


def test_line_statuses_group_every_mutant_by_start_line(tmp_path: Path) -> None:
    report = {"files": {"src/a.js": {"mutants": [mutant("Killed", 7), mutant("Survived", 7), mutant("Killed", 3)]}, "src/b.js": {}}}
    statuses = _stryker.Proof(0, "", report).line_statuses(command_context(tmp_path, {}))
    assert {name: dict(lines) for name, lines in statuses.items()} == {"web/src/a.js": {7: {"Killed", "Survived"}, 3: {"Killed"}}}


def timeout_report(root: Path) -> Callable[[list[str]], Reply]:
    report = {"files": {"src/a.ts": {"mutants": [mutant("CompileError", 2), mutant("Timeout", 4)]}}}

    def npx(command: list[str]) -> Reply:
        path = root / ts_mutation.REPORT
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report))
        return 0, ""

    return npx


def test_a_compile_error_is_killed_and_a_timeout_is_counted_on_its_own(tmp_path: Path, fake_run: Any) -> None:
    fake = fake_run(remote, timeout_report(tmp_path))

    result = checked(ts_mutation.run_gate(make_context(tmp_path, {"ts": {"mutation_scope": "all"}})), ts_mutation.GATE)

    assert (result.ok, result.summary, result.findings) == (True, "all mutants killed (1 killed by timeout)", [])
    assert fake.calls == [BASE]


def test_a_timeout_survives_when_mutation_timeouts_is_fail(tmp_path: Path, fake_run: Any) -> None:
    fake_run(remote, timeout_report(tmp_path))
    ctx = make_context(tmp_path, {"ts": {"mutation_scope": "all", "mutation_timeouts": "fail"}})

    result = checked(ts_mutation.run_gate(ctx), ts_mutation.GATE)

    assert (result.ok, result.summary, result.findings) == (False, "1 surviving mutants", ["src/a.ts:4 BooleanLiteral Timeout: x"])


def test_a_report_names_where_stryker_ran(tmp_path: Path, offload: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "web").mkdir()
    ctx = make_context(tmp_path, TS)
    monkeypatch.setattr(ctx, "mutation_files", lambda *args: MutationScope("full", note="(no base main; full run)"))
    offload(stryker(tmp_path, {"files": {"src/a.ts": {"mutants": [mutant("Killed", 1)]}}}, []), " (on box, 3s)")

    result = checked(ts_mutation.run_gate(ctx), ts_mutation.GATE)

    assert result.summary == "all mutants killed (no base main; full run) (on box, 3s)"


def test_a_bare_report_names_where_stryker_ran(tmp_path: Path, offload: Any) -> None:
    (tmp_path / "web").mkdir()
    offload(stryker(tmp_path, {"files": {"src/a.ts": {"mutants": [mutant("Killed", 1)]}}}, []), " (on box, 3s)")

    result = checked(ts_mutation.run_gate(full_context(tmp_path)), ts_mutation.GATE)

    assert (result.ok, result.summary, result.findings) == (True, "all mutants killed (on box, 3s)", [])


def test_a_missing_report_names_where_stryker_ran(tmp_path: Path, offload: Any) -> None:
    (tmp_path / "web").mkdir()
    offload(stryker(tmp_path, None, []), " (on box, 3s)")

    result = checked(ts_mutation.run_gate(full_context(tmp_path)), ts_mutation.GATE)

    assert (result.ok, result.summary, result.findings) == (False, "stryker produced no report (exit 1) (on box, 3s)", ["stryker log"])


def test_a_report_with_no_mutants_outside_hyper_is_all_killed(tmp_path: Path, offload: Any) -> None:
    (tmp_path / "web").mkdir()
    offload(stryker(tmp_path, {"files": {}}, []))

    result = checked(ts_mutation.run_gate(full_context(tmp_path)), ts_mutation.GATE)

    assert (result.ok, result.summary, result.findings) == (True, "all mutants killed", [])


def test_the_remote_command_asks_for_remote_concurrency(tmp_path: Path, offload: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[tuple[Any, str, int, bool]] = []

    def workers(ctx: Any, gate: str, local: int, on_remote: bool) -> int:
        asked.append((ctx, gate, local, on_remote))
        return local + 10 * on_remote

    monkeypatch.setattr(remote, "workers", workers)
    (tmp_path / "web").mkdir()
    fake = offload(stryker(tmp_path, None, []))

    ctx = full_context(tmp_path)

    ts_mutation.run_gate(ctx)

    assert fake.calls == [BASE]
    assert fake.builds[0](True) == [*BASE, "--concurrency", "10"]
    assert ts_mutation.mutation_command(ctx, []) == BASE
    assert asked == [(ctx, "ts.mutation", 0, False), (ctx, "ts.mutation", 0, True), (ctx, "ts.mutation", 0, False)]


def test_mutation_command_sets_concurrency_and_incremental(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"ts": {"root": "web", "mutation_concurrency": "3", "mutation_incremental": True}})

    assert ts_mutation.mutation_command(ctx, ["a.ts"]) == [
        *BASE,
        "--concurrency",
        "3",
        "--incremental",
        "--incrementalFile",
        "reports/stryker-incremental.json",
        "--mutate",
        "a.ts",
    ]


def test_concurrency_defaults_to_zero(tmp_path: Path) -> None:
    assert ts_mutation.concurrency(make_context(tmp_path, TS), True) == 0
    assert ts_mutation.concurrency(make_context(tmp_path, {"ts": {"mutation_concurrency": 5}}), False) == 5


@pytest.mark.parametrize(
    ("status", "strict", "expected"),
    [
        ("Survived", False, True),
        ("NoCoverage", True, True),
        ("RuntimeError", False, True),
        ("CompileError", True, False),
        ("Killed", True, False),
        ("Timeout", False, False),
        ("Timeout", True, True),
    ],
)
def test_failed_mutants(status: str, strict: bool, expected: bool) -> None:
    assert ts_mutation.failed(mutant(status, 1), strict) is expected


@pytest.mark.parametrize(
    ("timeouts", "ok", "summary", "findings"),
    [
        ({}, True, "all mutants killed (2 killed by timeout) (n)", []),
        (
            {"mutation_timeouts": "fail"},
            False,
            "2 surviving mutants (n)",
            ["a.ts:4 BooleanLiteral Timeout: x", "a.ts:5 BooleanLiteral Timeout: x"],
        ),
    ],
)
def test_judged_counts_timeouts_unless_they_fail(
    tmp_path: Path, timeouts: dict[str, str], ok: bool, summary: str, findings: list[str]
) -> None:
    mutants = [("a.ts", mutant("Killed", 1)), ("a.ts", mutant("Timeout", 4)), ("a.ts", mutant("Timeout", 5))]

    result = checked(ts_mutation.judged(make_context(tmp_path, {"ts": timeouts}), mutants, "(n)", 0.0), ts_mutation.GATE)

    assert (result.ok, result.summary, result.findings) == (ok, summary, findings)


@pytest.mark.parametrize(
    ("survivors", "timed_out", "note", "summary"),
    [
        ([], 1, "", "all mutants killed (1 killed by timeout)"),
        (["a"], 0, "", "1 surviving mutants"),
        (["a", "b"], 3, "(n)", "2 surviving mutants (3 killed by timeout) (n)"),
    ],
)
def test_survivor_result_counts_timeouts(survivors: list[str], timed_out: int, note: str, summary: str) -> None:
    assert ts_mutation.survivor_result(survivors, note, 0.0, timed_out).summary == summary


@pytest.mark.parametrize(
    ("statuses", "survivors", "expected"),
    [
        (["Survived"], ["x"], [ts_mutation.HINT]),
        (["Survived", "Killed"], ["x"], []),
        (["Timeout"], [], []),
    ],
)
def test_hint_only_when_survivors_and_nothing_killed(statuses: list[str], survivors: list[str], expected: list[str]) -> None:
    assert ts_mutation.hint([("a.js", mutant(status, 1)) for status in statuses], survivors) == expected


def test_command_proof_forgives_a_timeout(tmp_path: Path, offload: Any) -> None:
    report = {"files": {"src/a.js": {"mutants": [mutant("Timeout", 7)]}}}
    offload(command_stryker(tmp_path, report))

    result = checked(ts_mutation.run_gate(command_context(tmp_path, {"web/src/a.js": {7}})), ts_mutation.GATE)

    assert (result.ok, result.summary, result.findings) == (
        True,
        "all mutants killed (1 killed by timeout) (proof: mutation via [hyper] test_cmd)",
        [],
    )


def test_execute_runs_the_built_command_and_keeps_where(tmp_path: Path, offload: Any) -> None:
    (tmp_path / "temp").mkdir()
    report = tmp_path / "report.json"
    report.write_text("{}")
    built: list[bool] = []

    def build(on_remote: bool) -> list[str]:
        built.append(on_remote)
        return ["stryker", "run"]

    def reply(command: list[str]) -> Reply:
        (tmp_path / "temp").mkdir()
        report.write_text('{"files": {}}')
        return 2, "log"

    fake = offload(reply, " (on box, 3s)")

    proof = _stryker.execute(make_context(tmp_path), build, tmp_path / "temp", report, "ts.mutation", ("report.json",))

    assert proof == _stryker.Proof(2, "log", {"files": {}}, " (on box, 3s)")
    assert built == [False]
    assert fake.calls == [["stryker", "run"]]
    assert fake.options == [{"cwd": tmp_path, "gate": "ts.mutation", "env": None, "timeout": 7200, "pull": ("report.json",)}]
    assert not (tmp_path / "temp").exists()


def test_execute_defaults_to_no_gate_and_no_pull(tmp_path: Path, offload: Any) -> None:
    fake = offload([(0, "")])

    proof = _stryker.execute(make_context(tmp_path), lambda on_remote: ["s"], tmp_path / "temp", tmp_path / "r.json")

    assert proof == _stryker.Proof(0, "", None, "")
    assert fake.options == [{"cwd": tmp_path, "gate": "", "env": None, "timeout": 7200, "pull": ()}]


def test_proof_failure_names_where_it_ran() -> None:
    result = _stryker.Proof(3, "a\nb", None, " (on box, 3s)").failure("ts.mutation", 0.0)

    assert (result.gate, result.ok, result.summary, result.findings) == (
        "ts.mutation",
        False,
        "stryker produced no report (exit 3) (on box, 3s)",
        ["a", "b"],
    )
    assert _stryker.Proof(3, "", None).where == ""
