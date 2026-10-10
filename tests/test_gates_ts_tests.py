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
    lines = [f"line {n}" for n in range(40)]
    fake = fake_run(ts_tests, [(1, "\n".join(lines))])

    result = checked(ts_tests.run_gate(make_context(tmp_path, TS)), ts_tests.GATE)

    assert (result.gate, result.ok, result.summary, result.findings) == ("ts.tests", False, "tests failed", lines[10:])
    assert fake.calls[0][:2] == ["npx", "vitest"]
    assert fake.options[0] == {"cwd": tmp_path / "web", "env": None, "timeout": 1800}


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


def test_vitest_failure_findings_ignore_the_jest_results(tmp_path: Path) -> None:
    write_jest(tmp_path, [{"name": "a.test.ts", "status": "failed", "message": "boom", "assertionResults": []}])
    ctx = make_context(tmp_path, TS)

    assert ts_tests.failure_findings(ctx, "vitest") == []
    assert ts_tests.failure_findings(ctx, "jest") == ["web/a.test.ts:1 suite failed to run: boom"]


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
    assert fake.options == [{"cwd": tmp_path, "timeout": 1800}]


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


PLAYWRIGHT = {"ts": {"root": "web", "runner": "playwright", "sources": ["app", "lib"]}}


def install_playwright(root: Path, *names: str) -> Path:
    folder = root / "web" / "node_modules" / ".bin"
    folder.mkdir(parents=True, exist_ok=True)
    for name in names:
        (folder / name).write_text("")
    return folder


def write_report(root: Path, report: dict[str, Any] | str) -> Path:
    path = root / ".marestail" / ts_tests.JEST_RESULTS
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report if isinstance(report, str) else json.dumps(report))
    return path


def playwright_call(root: Path, bins: Path) -> list[str]:
    return [
        str(bins / "c8"),
        "--reporter=json",
        "--reporter=lcov",
        f"--reports-dir={root / '.marestail' / 'ts-coverage'}",
        "--all",
        "--include=app/**",
        "--include=lib/**",
        "--exclude=**/*.spec.ts",
        "--exclude=**/*.spec.tsx",
        "--exclude=**/*.test.ts",
        "--exclude=**/*.test.tsx",
        "--exclude=**/*.spec.js",
        "--exclude=**/*.test.js",
        str(bins / "playwright"),
        "test",
        "--reporter=line",
        "--reporter=json",
    ]


def test_unknown_runner_fails_before_running(tmp_path: Path, fake_run: Any) -> None:
    fake = fake_run(ts_tests)

    result = checked(ts_tests.run_gate(make_context(tmp_path, {"ts": {"runner": "mocha"}})), ts_tests.GATE)

    assert (result.ok, result.summary, result.findings) == (False, "unknown [ts] runner 'mocha'; use vitest, jest or playwright", [])
    assert fake.calls == []


@pytest.mark.parametrize(
    ("runner", "names", "expected"),
    [
        ("vitest", (), ""),
        ("jest", (), ""),
        ("playwright", (), "playwright is not installed at node_modules/.bin/playwright; add @playwright/test"),
        ("playwright", ("c8",), "playwright is not installed at node_modules/.bin/playwright; add @playwright/test"),
        ("playwright", ("playwright",), "c8 is not installed at node_modules/.bin/c8; add c8 so playwright coverage can be collected"),
        ("playwright", ("playwright", "c8"), ""),
    ],
)
def test_runner_problem(tmp_path: Path, runner: str, names: tuple[str, ...], expected: str) -> None:
    install_playwright(tmp_path, *names)

    assert ts_tests.runner_problem(make_context(tmp_path, PLAYWRIGHT), runner) == expected


def test_playwright_without_c8_fails_before_running(tmp_path: Path, fake_run: Any) -> None:
    install_playwright(tmp_path, "playwright")
    fake = fake_run(ts_tests)

    result = checked(ts_tests.run_gate(make_context(tmp_path, PLAYWRIGHT)), ts_tests.GATE)

    assert (result.ok, result.summary, result.findings) == (
        False,
        "c8 is not installed at node_modules/.bin/c8; add c8 so playwright coverage can be collected",
        [],
    )
    assert fake.calls == []


def test_playwright_command(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, PLAYWRIGHT)

    assert ts_tests.playwright_command(ctx) == playwright_call(tmp_path, tmp_path / "web" / "node_modules" / ".bin")
    assert (tmp_path / ".marestail" / "ts-coverage").is_dir()
    assert ts_tests.playwright_command(make_context(tmp_path, {"ts": {"source": "app"}}))[5:7] == [
        "--include=app/**",
        "--exclude=**/*.spec.ts",
    ]
    assert ts_tests.playwright_command(make_context(tmp_path, {"ts": {}}))[5:7] == ["--include=src/**", "--exclude=**/*.spec.ts"]


@pytest.mark.parametrize(
    ("runner", "first"),
    [
        ("vitest", ["npx", "vitest"]),
        ("jest", ["{root}/web/node_modules/.bin/jest", "--ci"]),
        ("playwright", ["{root}/web/node_modules/.bin/c8", "--reporter=json"]),
    ],
)
def test_command_for(tmp_path: Path, runner: str, first: list[str]) -> None:
    command = ts_tests.command_for(make_context(tmp_path, TS), runner)

    assert command[:2] == [part.format(root=tmp_path) for part in first]


def test_playwright_run_passes_with_c8_coverage(tmp_path: Path, fake_run: Any) -> None:
    bins = install_playwright(tmp_path, "playwright", "c8")
    report = write_report(tmp_path, {"stats": {"expected": 5, "unexpected": 0}})
    write_coverage(tmp_path, {str(tmp_path / "web" / "app" / "a.ts"): coverage_entry({"1": 1}, {"1": [1, 2]})})
    fake = fake_run(ts_tests, [(0, "  6 passed (1.2s)\n")])

    result = checked(ts_tests.run_gate(make_context(tmp_path, PLAYWRIGHT)), ts_tests.GATE)

    assert (result.ok, result.summary, result.findings) == (True, "5 passed, 0 uncovered lines/branches (need 0)", [])
    assert fake.calls == [playwright_call(tmp_path, bins)]
    assert fake.options == [{"cwd": tmp_path / "web", "env": {"PLAYWRIGHT_JSON_OUTPUT_NAME": str(report)}, "timeout": 1800}]


def test_playwright_run_reports_c8_gaps(tmp_path: Path, fake_run: Any) -> None:
    install_playwright(tmp_path, "playwright", "c8")
    write_coverage(tmp_path, {str(tmp_path / "web" / "app" / "a.ts"): coverage_entry({"1": 0}, {"1": [1, 0]})})
    fake_run(ts_tests, [(0, "  4 passed (1.2s)\n")])

    result = checked(ts_tests.run_gate(make_context(tmp_path, PLAYWRIGHT)), ts_tests.GATE)

    assert (result.ok, result.summary) == (False, "4 passed, 2 uncovered lines/branches (need 0)")
    assert result.findings == ["web/app/a.ts:10 not covered", "web/app/a.ts:11 branch arm 1 not taken"]


def test_playwright_run_failure_reads_the_report(tmp_path: Path, fake_run: Any) -> None:
    install_playwright(tmp_path, "playwright", "c8")
    write_report(tmp_path, {"errors": ["global setup broke\nstack"]})
    fake_run(ts_tests, [(1, "raw")])

    result = checked(ts_tests.run_gate(make_context(tmp_path, PLAYWRIGHT)), ts_tests.GATE)

    assert (result.ok, result.summary, result.findings) == (False, "tests failed", ["playwright:1 global setup broke"])


def test_playwright_run_failure_without_a_report_shows_output(tmp_path: Path, fake_run: Any) -> None:
    install_playwright(tmp_path, "playwright", "c8")
    fake_run(ts_tests, [(1, "raw failure")])

    result = checked(ts_tests.run_gate(make_context(tmp_path, PLAYWRIGHT)), ts_tests.GATE)

    assert (result.ok, result.summary, result.findings) == (False, "tests failed", ["raw failure"])


def test_playwright_failures_without_a_report(tmp_path: Path) -> None:
    assert ts_tests.playwright_failures(make_context(tmp_path, PLAYWRIGHT)) == []


def test_playwright_failures_with_an_invalid_report(tmp_path: Path) -> None:
    write_report(tmp_path, "{nope")

    assert ts_tests.playwright_failures(make_context(tmp_path, PLAYWRIGHT)) == ["ts-tests.json:1 playwright JSON report is not valid JSON"]


def test_playwright_failures_reads_specs_then_errors(tmp_path: Path) -> None:
    spec_file = str(tmp_path / "web" / "e2e" / "a.spec.ts")
    report = {
        "suites": [
            {
                "specs": [
                    {"title": "passes", "ok": True, "file": "p.spec.ts", "line": 2},
                    {"title": "unmarked", "file": "u.spec.ts", "line": 3},
                    {
                        "title": "breaks",
                        "ok": False,
                        "file": "a.spec.ts",
                        "line": 4,
                        "tests": [
                            {
                                "results": [
                                    {"status": "passed", "error": {"message": "ignored"}},
                                    {
                                        "status": "failed",
                                        "error": {"message": "\nExpected 1\nReceived 2", "location": {"file": spec_file, "line": 9}},
                                    },
                                    {"status": "interrupted", "error": {"location": {"file": "b.spec.ts"}}},
                                ]
                            },
                            {"results": None},
                            {"results": [{"status": "skipped"}, {"status": "timedOut", "error": None}]},
                        ],
                    },
                ],
                "suites": [{"specs": [{"ok": False, "tests": None}], "suites": None}],
            },
            {"specs": None},
        ],
        "errors": [{"message": "config broke", "location": {"file": "playwright.config.ts", "line": 7}}, {}, "plain\nrest"],
    }
    write_report(tmp_path, report)

    assert ts_tests.playwright_failures(make_context(tmp_path, PLAYWRIGHT)) == [
        "web/e2e/a.spec.ts:9 breaks failed: Expected 1",
        "web/b.spec.ts:4 breaks failed: interrupted",
        "web/a.spec.ts:4 breaks failed: timedOut",
        "web/playwright:1 test failed: failed",
        "web/playwright.config.ts:7 config broke",
        "web/playwright:1 playwright error",
        "playwright:1 plain",
    ]


def test_playwright_failures_with_an_empty_report(tmp_path: Path) -> None:
    write_report(tmp_path, {"suites": None, "errors": None})

    assert ts_tests.playwright_failures(make_context(tmp_path, PLAYWRIGHT)) == []


def test_read_report(tmp_path: Path) -> None:
    path = tmp_path / "r.json"
    path.write_text('{"a": 1}')
    assert ts_tests.read_report(path) == {"a": 1}
    path.write_text("{")
    assert ts_tests.read_report(path) is None


@pytest.mark.parametrize(("data", "expected"), [({}, []), ({"k": None}, []), ({"k": []}, []), ({"k": [1, 2]}, [1, 2])])
def test_listed(data: dict[str, Any], expected: list[Any]) -> None:
    assert ts_tests.listed(data, "k") == expected


def test_playwright_specs_walks_nested_suites_in_order() -> None:
    suites = [
        {"specs": [{"title": "a"}], "suites": [{"specs": [{"title": "b"}], "suites": [{"specs": [{"title": "c"}]}]}]},
        {"suites": [{"specs": [{"title": "d"}]}], "specs": [{"title": "e"}]},
    ]

    assert [spec["title"] for spec in ts_tests.playwright_specs(suites)] == ["a", "b", "c", "e", "d"]


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ({"title": "t"}, []),
        ({"title": "t", "ok": True}, []),
        ({"title": "t", "ok": False, "file": "f.ts", "line": 3}, ["web/f.ts:3 t failed: failed"]),
        ({"ok": False, "tests": [{"results": [{"status": "timedOut"}]}]}, ["web/playwright:1 test failed: timedOut"]),
    ],
)
def test_playwright_spec_findings(tmp_path: Path, spec: dict[str, Any], expected: list[str]) -> None:
    assert ts_tests.playwright_spec_findings(spec, make_context(tmp_path, PLAYWRIGHT)) == expected


@pytest.mark.parametrize(
    ("status", "failed"),
    [("failed", True), ("timedOut", True), ("interrupted", True), ("passed", False), ("skipped", False), (None, False)],
)
def test_failed_results_keeps_only_failed_statuses(status: str | None, failed: bool) -> None:
    result = {"status": status}

    assert ts_tests.failed_results({"tests": [{"results": [result]}]}) == [result] * failed


@pytest.mark.parametrize(
    ("loc", "fallback", "file", "line"),
    [
        ({"file": "a.ts", "line": 2}, {"file": "b.ts", "line": 3}, "a.ts", "2"),
        ({"file": "", "line": 0}, {"file": "b.ts", "line": 3}, "b.ts", "3"),
        ({}, {}, "playwright", "1"),
    ],
)
def test_place_file_and_line(loc: dict[str, Any], fallback: dict[str, Any], file: str, line: str) -> None:
    assert (ts_tests.place_file(loc, fallback), ts_tests.place_line(loc, fallback)) == (file, line)


@pytest.mark.parametrize(
    ("error", "expected"),
    [({"location": {"file": "x/a.ts", "line": 5}}, "web/x/a.ts:5"), ({"location": None}, "web/s.ts:6"), ({}, "web/s.ts:6")],
)
def test_place(tmp_path: Path, error: dict[str, Any], expected: str) -> None:
    assert ts_tests.place(error, {"file": "s.ts", "line": 6}, make_context(tmp_path, PLAYWRIGHT)) == expected


@pytest.mark.parametrize(
    ("report", "expected"),
    [
        ({}, None),
        ({"stats": None}, None),
        ({"stats": {"unexpected": 1}}, None),
        ({"stats": {"expected": 0}}, "0"),
        ({"stats": {"expected": 12}}, "12"),
    ],
)
def test_stats_count(report: dict[str, Any], expected: str | None) -> None:
    assert ts_tests.stats_count(report) == expected


def test_report_count(tmp_path: Path) -> None:
    path = tmp_path / "r.json"
    assert ts_tests.report_count(path) is None
    path.write_text("{")
    assert ts_tests.report_count(path) is None
    path.write_text('{"stats": {"expected": 3}}')
    assert ts_tests.report_count(path) == "3"


def test_playwright_count_only_for_playwright_with_a_context(tmp_path: Path) -> None:
    write_report(tmp_path, {"stats": {"expected": 7}})
    ctx = make_context(tmp_path, PLAYWRIGHT)

    assert ts_tests.playwright_count(ctx, "playwright") == "7"
    assert ts_tests.playwright_count(ctx, "vitest") is None
    assert ts_tests.playwright_count(None, "playwright") is None


def test_count_tests_prefers_the_playwright_report(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, PLAYWRIGHT)

    assert ts_tests.count_tests("  2 passed (1s)", ctx, "playwright") == "2"
    write_report(tmp_path, {"stats": {"expected": 0}})
    assert ts_tests.count_tests("  2 passed (1s)", ctx, "playwright") == "0"
    assert ts_tests.count_tests("  2 passed (1s)", ctx, "vitest") == "2"


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("Tests  9 passed (9)", "9"),
        ("Tests: x passed", "x"),
        ("  3 passed (666ms)", "3"),
        ("  some passed", None),
        ("done 3 passed (1s)", "3"),
        ("3 passed, 4 passed", "3"),
        ("3 passedx", "3"),
        ("3passed", None),
        ("Tests failed", None),
        ("nothing", None),
    ],
)
def test_line_count(line: str, expected: str | None) -> None:
    assert ts_tests.line_count(line) == expected


def test_the_removed_failure_helpers_stay_removed() -> None:
    assert (hasattr(ts_tests, "failures"), hasattr(ts_tests, "empty_failures")) == (False, False)
