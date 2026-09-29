import json
from pathlib import Path
from typing import Any

import pytest

from marestail import javascript
from marestail.gates import _coverage, ts_tests
from tests.conftest import checked, make_context

TS = {"ts": {"root": "web"}}


def coverage_entry(statements: dict[str, int], branches: dict[str, list[int]]) -> dict[str, Any]:
    return {
        "s": statements,
        "statementMap": {key: {"start": {"line": int(key) * 10}} for key in statements},
        "b": branches,
        "branchMap": {
            key: {"loc": {"start": {"line": int(key) * 10 + 1}}, "locations": [{"start": {"line": int(key) * 10 + 2}}, {}]}
            for key in branches
        },
    }


def write_coverage(root: Path, coverage: dict[str, Any]) -> None:
    folder = root / ".marestail" / _coverage.TS_COVERAGE_DIR
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "coverage-final.json").write_text(json.dumps(coverage))


def write_jest(root: Path, results: list[dict[str, Any]]) -> None:
    (root / ".marestail").mkdir(exist_ok=True)
    (root / ".marestail" / ts_tests.JEST_RESULTS).write_text(json.dumps({"testResults": results}))


def test_chosen_runner_defaults_to_vitest(tmp_path: Path) -> None:
    assert ts_tests.chosen_runner(make_context(tmp_path)) == "vitest"
    assert ts_tests.chosen_runner(make_context(tmp_path, {"ts": {"runner": "jest"}})) == "jest"


def test_vitest_command(tmp_path: Path) -> None:
    assert ts_tests.vitest_command(make_context(tmp_path)) == [
        "npx",
        "vitest",
        "run",
        "--coverage.enabled=true",
        "--coverage.all=true",
        "--coverage.reporter=json",
        "--coverage.reporter=lcov",
        f"--coverage.reportsDirectory={tmp_path / '.marestail' / 'ts-coverage'}",
    ]


def test_vitest_command_with_tooling(tmp_path: Path) -> None:
    folder = tmp_path / ".marestail" / "tooling"
    ctx = make_context(tmp_path, {"ts": {"tooling": ".marestail/tooling"}})
    vitest = str(folder / "node_modules" / ".bin" / "vitest")

    assert ts_tests.vitest_command(ctx)[:3] == [vitest, "run", "--coverage.enabled=true"]
    folder.mkdir(parents=True)
    (folder / "vitest.config.ts").write_text("")
    assert ts_tests.vitest_command(ctx)[:5] == [vitest, "run", "--config", str(folder / "vitest.config.ts"), "--coverage.enabled=true"]


def test_jest_command_ignores_tooling(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"ts": {"runner": "jest", "tooling": ".marestail/tooling"}})

    assert ts_tests.jest_command(ctx)[:2] == [str(tmp_path / "node_modules" / ".bin" / "jest"), "--ci"]


@pytest.mark.parametrize(
    ("ts", "globs"),
    [
        ({}, ["--collectCoverageFrom=src/**/*.{ts,tsx,js,jsx}"]),
        ({"source": "app"}, ["--collectCoverageFrom=app/**/*.{ts,tsx,js,jsx}"]),
        (
            {"sources": ["a", "b"], "source": "x"},
            ["--collectCoverageFrom=a/**/*.{ts,tsx,js,jsx}", "--collectCoverageFrom=b/**/*.{ts,tsx,js,jsx}"],
        ),
    ],
)
def test_jest_command(tmp_path: Path, ts: dict[str, Any], globs: list[str]) -> None:
    work = tmp_path / ".marestail"

    assert ts_tests.jest_command(make_context(tmp_path, {"ts": {"root": "web", **ts}})) == [
        str(tmp_path / "web" / "node_modules" / ".bin" / "jest"),
        "--ci",
        "--coverage",
        "--coverageProvider=babel",
        "--coverageReporters=json",
        "--coverageReporters=lcov",
        f"--coverageDirectory={work / 'ts-coverage'}",
        "--json",
        f"--outputFile={work / 'ts-tests.json'}",
        "--testLocationInResults",
        *globs,
    ]


def test_vitest_failure_shows_the_output_tail(tmp_path: Path, fake_run: Any) -> None:
    fake = fake_run(ts_tests, [(1, "\n".join(f"line {n}" for n in range(40)))])

    result = checked(ts_tests.run_gate(make_context(tmp_path, TS)), ts_tests.GATE)

    assert (result.gate, result.ok, result.summary) == ("ts.tests", False, "tests failed")
    assert result.findings == [f"line {n}" for n in range(10, 40)]
    assert fake.calls[0][:2] == ["npx", "vitest"]
    assert fake.options[0] == {"cwd": tmp_path / "web", "timeout": 1800}


def test_jest_failure_reads_the_results(tmp_path: Path, fake_run: Any) -> None:
    write_jest(
        tmp_path,
        [
            {
                "name": str(tmp_path / "web" / "a.test.ts"),
                "assertionResults": [{"status": "failed", "fullName": "adds", "failureMessages": ["\n boom \nmore"]}],
            },
            {"name": "b.test.ts", "status": "failed", "message": "Cannot find module"},
        ],
    )
    fake = fake_run(ts_tests, [(1, "raw")])

    result = checked(ts_tests.run_gate(make_context(tmp_path, {"ts": {"root": "web", "runner": "jest"}})), ts_tests.GATE)

    assert result.findings == ["web/a.test.ts:1 adds failed: boom", "web/b.test.ts:1 suite failed to run: Cannot find module"]
    assert fake.calls[0][0].endswith("jest")


def test_jest_failure_without_results_shows_output(tmp_path: Path, fake_run: Any) -> None:
    fake_run(ts_tests, [(1, "raw failure")])

    result = checked(ts_tests.run_gate(make_context(tmp_path, {"ts": {"runner": "jest"}})), ts_tests.GATE)

    assert result.findings == ["raw failure"]


def test_suite_failures(tmp_path: Path) -> None:
    suite = {
        "name": " web/a.test.ts ",
        "status": "failed",
        "message": "ignored because cases failed",
        "assertionResults": [
            {"status": "passed", "fullName": "ok"},
            {"status": "failed", "fullName": "x", "location": {"line": 7}, "failureMessages": ["a", "b"]},
            {"status": "failed", "fullName": "y", "location": None},
        ],
    }

    assert ts_tests.suite_failures(suite, make_context(tmp_path)) == [
        "web/a.test.ts:7 x failed: a b",
        "web/a.test.ts:1 y failed: no message",
    ]


def test_passing_suite_has_no_failures(tmp_path: Path) -> None:
    assert ts_tests.suite_failures({"name": "a.ts", "status": "passed"}, make_context(tmp_path)) == []


@pytest.mark.parametrize(("text", "expected"), [("", "no message"), ("\n  \n  hi  \nthere", "hi"), ("z" * 300, "z" * 200)])
def test_first_line(text: str, expected: str) -> None:
    assert ts_tests.first_line(text) == expected


def test_uninstrumented_files_fail(tmp_path: Path, fake_run: Any) -> None:
    fake_run(ts_tests, [(0, "Failed to collect coverage from src/a.ts\nFailed to collect coverage from src/b.ts\n")])

    result = checked(ts_tests.run_gate(make_context(tmp_path, TS)), ts_tests.GATE)

    assert (result.ok, result.summary) == (False, "2 files could not be instrumented")
    assert result.findings == ["web/src/a.ts:1 not instrumented", "web/src/b.ts:1 not instrumented"]


def test_uninstrumented_files_out_of_scope_are_ignored(tmp_path: Path, fake_run: Any) -> None:
    fake_run(ts_tests, [(0, "Failed to collect coverage from src/a.ts\n")])

    result = checked(ts_tests.run_gate(make_context(tmp_path, TS, scope_changed=True)), ts_tests.GATE)

    assert (result.ok, result.summary) == (False, "no coverage report; check [ts] runner and sources")


@pytest.mark.parametrize("coverage", [None, {}])
def test_missing_coverage_fails(tmp_path: Path, fake_run: Any, coverage: dict[str, Any] | None) -> None:
    if coverage is not None:
        write_coverage(tmp_path, coverage)
    fake_run(ts_tests, [(0, "Tests 3 passed")])

    result = checked(ts_tests.run_gate(make_context(tmp_path, TS)), ts_tests.GATE)

    assert (result.ok, result.summary, result.findings) == (False, "no coverage report; check [ts] runner and sources", ["Tests 3 passed"])


def test_full_coverage_passes(tmp_path: Path, fake_run: Any) -> None:
    write_coverage(tmp_path, {str(tmp_path / "web" / "a.ts"): coverage_entry({"1": 2}, {"1": [1, 1]})})
    fake_run(ts_tests, [(0, "noise\n      Tests  12 passed (12)\n")])

    result = checked(ts_tests.run_gate(make_context(tmp_path, TS)), ts_tests.GATE)

    assert (result.ok, result.summary, result.findings) == (True, "12 passed, 0 uncovered lines/branches (need 0)", [])


def test_uncovered_code_fails(tmp_path: Path, fake_run: Any) -> None:
    write_coverage(
        tmp_path,
        {
            "b.ts": coverage_entry({"1": 0, "2": 3, "3": 0}, {"1": [0, 4, 0]}),
            str(tmp_path / "web" / "a.ts"): coverage_entry({"1": 0}, {}),
        },
    )
    fake_run(ts_tests, [(0, "")])

    result = checked(ts_tests.run_gate(make_context(tmp_path, TS)), ts_tests.GATE)

    assert (result.ok, result.summary) == (False, "? passed, 5 uncovered lines/branches (need 0)")
    assert result.findings == [
        "web/a.ts:10 not covered",
        "web/b.ts:10 not covered",
        "web/b.ts:30 not covered",
        "web/b.ts:11 branch arm 0 not taken",
        "web/b.ts:11 branch arm 2 not taken",
    ]


def test_scoped_coverage_only_reports_changed_lines(tmp_path: Path) -> None:
    coverage = {"a.ts": coverage_entry({"1": 0, "2": 0}, {"1": [0, 0], "2": [0]}), "c.ts": coverage_entry({"1": 0}, {})}
    ctx = make_context(tmp_path, TS, scope_changed=True, changed={"web/a.ts"}, changed_lines_map={"web/a.ts": {20, 12}})

    assert ts_tests.coverage_findings(coverage, ctx) == ["web/a.ts:20 not covered", "web/a.ts:11 branch arm 0 not taken"]


@pytest.mark.parametrize(
    ("branch", "index", "gated", "expected"),
    [
        ({"loc": {"start": {"line": 1}}}, 0, None, True),
        ({"loc": {"start": {"line": 1}}}, 0, {1}, True),
        ({"loc": {"start": {"line": 1}}}, 0, {2}, False),
        ({"loc": {"start": {"line": 1}}, "locations": [{"start": {"line": 2}}]}, 0, {2}, True),
        ({"loc": {"start": {"line": 1}}, "locations": [{"start": {"line": 2}}]}, 1, {2}, False),
        ({"loc": {"start": {"line": 1}}, "locations": [{"start": None}]}, 0, {2}, False),
        ({"loc": {"start": {"line": 1}}, "locations": None}, 0, {2}, False),
    ],
)
def test_arm_gated(branch: dict[str, Any], index: int, gated: set[int] | None, expected: bool) -> None:
    assert ts_tests.arm_gated(branch, index, gated) is expected


@pytest.mark.parametrize(
    ("output", "expected"),
    [("", "?"), ("Tests: 4 passed, 4 total", "4"), ("passed 3\nTests  9 passed", "9"), ("Tests failed", "?")],
)
def test_count_tests(output: str, expected: str) -> None:
    assert ts_tests.count_tests(output) == expected


@pytest.mark.parametrize(
    ("path", "expected"),
    [("src/a.ts", "web/src/a.ts"), ("/elsewhere/a.ts", "/elsewhere/a.ts"), ("../web/b.ts", "web/b.ts")],
)
def test_relative(tmp_path: Path, path: str, expected: str) -> None:
    assert javascript.rel(path, make_context(tmp_path, TS)) == expected


def test_relative_path_strips_and_keeps_the_original_when_outside(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, TS)

    assert javascript.labelled("  src/a.ts \n", ctx) == "web/src/a.ts"
    assert javascript.labelled(" /elsewhere/a.ts ", ctx) == " /elsewhere/a.ts "
    assert javascript.labelled(str(tmp_path / "x.ts"), ctx) == "x.ts"


def test_in_scope_findings(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, scope_changed=True, changed={"a.ts"})

    assert _coverage.in_scope_findings(["a.ts:1 x: y", "b.ts:2 a.ts:1"], ctx) == ["a.ts:1 x: y"]


def test_finding_file_splits_on_the_first_colon() -> None:
    assert _coverage.finding_file("a.ts:1 x: y") == "a.ts"
    assert _coverage.finding_file("no-colon") == "no-colon"
    assert _coverage.finding_file(":leading") == ""


def test_failures_without_jest_ignores_results(tmp_path: Path) -> None:
    write_jest(tmp_path, [{"name": "a.test.ts", "status": "failed", "message": "boom", "assertionResults": []}])
    assert ts_tests.failures(make_context(tmp_path, TS), False) == []


def test_jest_failures_default_missing_results(tmp_path: Path) -> None:
    path = tmp_path / ".marestail" / ts_tests.JEST_RESULTS
    path.parent.mkdir(parents=True)
    path.write_text("{}")
    assert ts_tests.jest_failures(make_context(tmp_path)) == []


def test_suite_broken_without_a_message() -> None:
    assert ts_tests.suite_broken("a.ts", {"status": "failed"}, []) == ["a.ts:1 suite failed to run: no message"]


def test_passed_count_uses_the_last_word() -> None:
    assert ts_tests.passed_count("Tests extra 9 passed") == "9"


def test_coverage_findings_skips_out_of_scope_files(tmp_path: Path) -> None:
    coverage = {"a.ts": coverage_entry({"1": 0}, {}), "b.ts": coverage_entry({"1": 0}, {})}
    ctx = make_context(tmp_path, TS, scope_changed=True, changed={"web/b.ts"})
    assert ts_tests.coverage_findings(coverage, ctx) == ["web/b.ts:10 not covered"]


COMMAND = {"ts": {"root": "web"}, "hyper": {"test_cmd": "node t.js"}}


def test_hyper_test_cmd_passes_on_exit_zero_and_reads_no_coverage(tmp_path: Path, fake_run: Any) -> None:
    fake = fake_run(ts_tests, [(0, "ok - a\n")])

    result = checked(ts_tests.run_gate(make_context(tmp_path, COMMAND, hyper=True)), ts_tests.GATE)

    assert (result.ok, result.summary, result.findings) == (
        True,
        "test_cmd exited 0; coverage advisory: not measured with [hyper] test_cmd",
        [],
    )
    assert fake.calls == [["bash", "-lc", "node t.js"]]
    assert fake.options[0]["cwd"] == tmp_path


def test_hyper_test_cmd_failure_shows_the_last_lines(tmp_path: Path, fake_run: Any) -> None:
    fake_run(ts_tests, [(1, "\n".join(f"line {n}" for n in range(40)) + "\nAssertionError: boom\n")])

    result = checked(ts_tests.run_gate(make_context(tmp_path, COMMAND, hyper=True)), ts_tests.GATE)

    assert (result.ok, result.summary) == (False, "test_cmd exited 1")
    assert len(result.findings) == 30
    assert result.findings[-1] == "AssertionError: boom"


def test_hyper_without_test_cmd_or_known_runner_fails_before_running(tmp_path: Path, fake_run: Any) -> None:
    fake = fake_run(ts_tests)

    result = checked(ts_tests.run_gate(make_context(tmp_path, TS, hyper=True)), ts_tests.GATE)

    assert (result.ok, result.summary, result.findings) == (False, ts_tests.NO_RUNNER, [])
    assert ts_tests.NO_RUNNER == "hyper: set [hyper] test_cmd to the command that runs this repository's tests"
    assert fake.calls == []


def test_test_cmd_outside_hyper_is_ignored(tmp_path: Path, fake_run: Any) -> None:
    fake = fake_run(ts_tests, [(1, "raw")])

    result = checked(ts_tests.run_gate(make_context(tmp_path, COMMAND)), ts_tests.GATE)

    assert (result.ok, result.summary) == (False, "tests failed")
    assert fake.calls[0][:2] == ["npx", "vitest"]


@pytest.mark.parametrize(
    ("manifest", "expected"),
    [
        (None, False),
        ({}, False),
        ({"dependencies": {"left-pad": "1"}, "devDependencies": None}, False),
        ({"devDependencies": {"vitest": "3"}}, True),
        ({"dependencies": {"jest": "29"}}, True),
    ],
)
def test_known_runner_reads_the_targets_own_package_json(tmp_path: Path, manifest: dict[str, Any] | None, expected: bool) -> None:
    (tmp_path / "web").mkdir()
    if manifest is not None:
        (tmp_path / "web" / "package.json").write_text(json.dumps(manifest))
    assert ts_tests.known_runner(make_context(tmp_path, TS)) is expected


def test_hyper_with_a_known_runner_runs_it_as_today(tmp_path: Path, fake_run: Any) -> None:
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "package.json").write_text(json.dumps({"devDependencies": {"vitest": "3"}}))
    fake = fake_run(ts_tests, [(1, "raw")])

    result = checked(ts_tests.run_gate(make_context(tmp_path, TS, hyper=True)), ts_tests.GATE)

    assert result.summary == "tests failed"
    assert fake.calls[0][:3] == ["npx", "vitest", "run"]


def test_hyper_does_not_count_a_runner_installed_only_in_tooling(tmp_path: Path, fake_run: Any) -> None:
    tooling = tmp_path / ".marestail" / "tooling"
    (tooling / "node_modules" / ".bin").mkdir(parents=True)
    (tooling / "node_modules" / ".bin" / "vitest").write_text("")
    (tooling / "vitest.config.ts").write_text("")
    raw = {"ts": {"root": ".", "tooling": ".marestail/tooling"}}
    fake = fake_run(ts_tests, [(1, "raw")])

    hyper = checked(ts_tests.run_gate(make_context(tmp_path, raw, hyper=True)), ts_tests.GATE)
    diff = checked(ts_tests.run_gate(make_context(tmp_path, raw, scope_changed=True)), ts_tests.GATE)

    assert (hyper.ok, hyper.summary) == (False, ts_tests.NO_RUNNER)
    assert diff.summary == "tests failed"
    assert fake.calls[0][0] == str(tooling / "node_modules" / ".bin" / "vitest")
