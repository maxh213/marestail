import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from marestail import rust
from marestail.gates import rs_tests
from tests.conftest import make_context

CARGO_OUT = "running 3 tests\ntest result: ok. 3 passed; 0 failed\n\ntest result: ok. 2 passed; 0 failed\n"


@pytest.fixture(autouse=True)
def no_llvm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rust, "env", lambda ctx: {})


def lcov(root: Path) -> str:
    return "\n".join(
        [
            "TN:",
            "DA:1,1",
            f"SF:{root}/src/lib.rs",
            "FN:1,add",
            "DA:1,1",
            "DA:2,0",
            "DA:3,4",
            "DA:3,2",
            "end_of_record",
            "SF:src/main.rs",
            "DA:5,0",
            "end_of_record",
        ]
    )


def llvm_json(root: Path) -> dict[str, Any]:
    functions = [
        {
            "name": "add",
            "filenames": [f"{root}/src/lib.rs"],
            "regions": [[1, 1, 3, 2, 5, 0, 0, 0], [3, 9, 3, 20, 0, 0, 0, 0], [2, 5, 2, 9, 0, 0, 0, 0]],
        },
        {
            "name": "exp",
            "filenames": [f"{root}/src/lib.rs"],
            "regions": [[3, 9, 3, 20, 0, 0, 0, 0], [4, 1, 4, 2, 0, 1, 0, 0], [7, 1, 7, 2, 0, 0, 0, 2]],
        },
        {"name": "gen", "filenames": ["src/gen.rs"], "regions": [[1, 4, 1, 9, 0, 0, 0, 0]]},
    ]
    return {"type": "llvm.coverage.json.export", "data": [{"functions": functions}, {}]}


def writes_reports(root: Path, lcov_text: str, export: dict[str, Any]) -> Callable[[list[str]], tuple[int, str]]:
    def reply(command: list[str]) -> tuple[int, str]:
        if "report" not in command:
            return 0, CARGO_OUT
        target = Path(command[-1])
        target.write_text(json.dumps(export) if "--json" in command else lcov_text)
        return 0, ""

    return reply


def work(root: Path) -> Path:
    (root / ".marestail").mkdir(exist_ok=True)
    return root / ".marestail"


def test_llvm_cov_missing(tmp_path: Path, fake_run: Any) -> None:
    fake = fake_run(rust, [(101, "error: no such command: `llvm-cov`")])
    result = rs_tests.run_gate(make_context(tmp_path, {"rust": {"test_args": ["--workspace"]}}))
    assert (result.gate, result.ok, result.summary) == ("rs.tests", False, "cargo llvm-cov missing")
    assert result.findings == [f"cargo llvm-cov is not installed: {rust.INSTALL['llvm-cov']}"]
    assert fake.calls == [["cargo", "llvm-cov", "--no-report", "--workspace"]]
    assert fake.options == [{"cwd": tmp_path, "env": {}, "timeout": 3600}]


def test_tests_fail(tmp_path: Path, fake_run: Any) -> None:
    fake = fake_run(rust, [(101, "test a ... FAILED\n")])
    result = rs_tests.run_gate(make_context(tmp_path))
    assert (result.ok, result.summary, result.findings) == (False, "tests failed", ["test a ... FAILED"])
    assert fake.calls[0] == ["cargo", "llvm-cov", "--no-report"]
    assert rs_tests.extra_args(make_context(tmp_path)) == []


@pytest.mark.parametrize(("second", "flag"), [((1, "bad json\n"), "--json"), ((0, "no file"), "--json")])
def test_report_missing(tmp_path: Path, fake_run: Any, second: tuple[int, str], flag: str) -> None:
    (work(tmp_path) / rs_tests.RAW_JSON).write_text("stale")
    fake = fake_run(rust, [(0, CARGO_OUT), second])
    result = rs_tests.run_gate(make_context(tmp_path, {"rust": {"coverage_ignore_regex": "gen"}}))
    assert (result.ok, result.summary, result.findings) == (False, f"cargo llvm-cov report {flag} produced no report", tail_of(second[1]))
    raw = str(tmp_path / ".marestail" / rs_tests.RAW_JSON)
    assert fake.calls[1] == ["cargo", "llvm-cov", "report", "--json", "--ignore-filename-regex", "gen", "--output-path", raw]
    assert fake.options[1] == {"cwd": tmp_path, "env": {}, "timeout": 600}


def tail_of(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.strip()]


def test_lcov_report_missing(tmp_path: Path, fake_run: Any) -> None:
    work(tmp_path)

    def reply(command: list[str]) -> tuple[int, str]:
        if "--json" in command:
            Path(command[-1]).write_text("{}")
        return (0, "") if "--lcov" not in command else (2, "lcov broke")

    fake = fake_run(rust, reply)
    result = rs_tests.run_gate(make_context(tmp_path))
    assert result.summary == "cargo llvm-cov report --lcov produced no report"
    assert fake.calls[2] == ["cargo", "llvm-cov", "report", "--lcov", "--output-path", str(tmp_path / ".marestail" / rs_tests.LCOV)]


def test_no_source_files(tmp_path: Path, fake_run: Any) -> None:
    work(tmp_path)
    fake_run(rust, writes_reports(tmp_path, "TN:\n", {"data": []}))
    result = rs_tests.run_gate(make_context(tmp_path))
    assert (result.ok, result.summary) == (False, "coverage report lists no source files")
    assert result.findings == tail_of(CARGO_OUT)


def test_merged_coverage(tmp_path: Path, fake_run: Any) -> None:
    work(tmp_path)
    fake_run(rust, writes_reports(tmp_path, lcov(tmp_path), llvm_json(tmp_path)))
    result = rs_tests.run_gate(make_context(tmp_path))
    assert (result.ok, result.summary) == (False, "5 passed, line coverage 50.0%, 4 gaps (need 0)")
    assert result.findings == [
        "src/gen.rs:1 code at column 4 never runs",
        "src/lib.rs:2 not covered",
        "src/lib.rs:3 code at column 9 never runs",
        "src/main.rs:5 not covered",
    ]
    saved = json.loads((tmp_path / ".marestail" / "rs-coverage.json").read_text())
    assert saved == {
        "files": {
            "src/lib.rs": {"lines": {"1": 1, "2": 0, "3": 4}, "regions": {"1:1": 5, "3:9": 0, "2:5": 0}},
            "src/main.rs": {"lines": {"5": 0}, "regions": {}},
            "src/gen.rs": {"lines": {}, "regions": {"1:4": 0}},
        },
        "totals": {"percent_covered": 50.0},
    }


def test_scoped_summary(tmp_path: Path, fake_run: Any) -> None:
    work(tmp_path)
    fake_run(rust, writes_reports(tmp_path, "SF:src/lib.rs\nDA:1,3\n", {"data": []}))
    ctx = make_context(tmp_path, scope_changed=True, changed={"src/main.rs"})
    result = rs_tests.run_gate(ctx)
    assert (result.ok, result.summary, result.findings) == (True, "5 passed, line coverage 100.0%, 0 gaps on changed files (need 0)", [])


def test_line_percent_without_lines() -> None:
    assert rs_tests.line_percent({"a.rs": {"lines": {}, "regions": {}}}) == 100.0


def test_lcov_line_records_hits(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    files: dict[str, rs_tests.Entry] = {}
    current = rs_tests.lcov_line(ctx, files, None, "SF:src/lib.rs")
    current = rs_tests.lcov_line(ctx, files, current, "DA:10,3,extra")
    assert files["src/lib.rs"]["lines"]["10"] == 3
    assert rs_tests.da_hits("DA:10,3,extra") == ("10", "3")
    assert rs_tests.da_hits("DA:10,3,extra,more") == ("10", "3")
    assert rs_tests.da_hits("DA:4,0") == ("4", "0")


def test_coverage_findings_orders_regions(tmp_path: Path) -> None:
    coverage = {"files": {"a.rs": {"lines": {"10": 0, "2": 1}, "regions": {"10:3": 0, "9:12": 0, "9:2": 0, "1:1": 4}}}}
    assert rs_tests.coverage_findings(coverage, make_context(tmp_path)) == [
        "a.rs:10 not covered",
        "a.rs:9 code at column 2 never runs",
        "a.rs:9 code at column 12 never runs",
    ]


def test_coverage_findings_keeps_only_files_in_scope(tmp_path: Path) -> None:
    coverage = {"files": {"a.rs": {"lines": {"1": 0}, "regions": {}}, "b.rs": {"lines": {"2": 0}, "regions": {}}}}
    ctx = make_context(tmp_path, scope_changed=True, changed={"a.rs"})
    assert rs_tests.coverage_findings(coverage, ctx) == ["a.rs:1 not covered"]


def test_json_list_defaults_missing_keys() -> None:
    assert rs_tests.json_list({}, "data") == []
    assert rs_tests.json_list({"data": [1]}, "data") == [1]


def test_merge_function_skips_missing_regions(tmp_path: Path) -> None:
    files: dict[str, rs_tests.Entry] = {}
    rs_tests.merge_function(make_context(tmp_path), files, {})
    assert files == {}
