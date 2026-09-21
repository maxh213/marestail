import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from marestail import rust
from marestail.gates import rs_mutation
from tests.conftest import make_context

VERSION = ["cargo", "mutants", "--version"]


@pytest.fixture(autouse=True)
def no_llvm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rust, "env", lambda ctx: {})


def mutant(file: str, line: int, function: str | None, name: str, summary: str) -> dict[str, Any]:
    body: dict[str, Any] = {"package": "demo", "file": file, "name": name, "span": {"start": {"line": line, "column": 5}}}
    body["function"] = {"function_name": function, "return_type": "-> i32"} if function else None
    return {"scenario": {"Mutant": body}, "summary": summary}


def outcomes() -> dict[str, Any]:
    return {
        "outcomes": [
            {"scenario": "Baseline", "summary": "Success"},
            mutant("src/lib.rs", 3, "add", "src/lib.rs:3:5: replace add -> i32 with 0 in add", "MissedMutant"),
            mutant("src/lib.rs", 4, "add", "src/lib.rs:4:5: replace + with - in add", "CaughtMutant"),
            mutant("src/lib.rs", 5, "add", "replace + with * in add", "Timeout"),
            mutant("src/lib.rs", 6, "add", "x", "Unviable"),
            mutant("src/main.rs", 8, None, "delete !", "Failure"),
            {"scenario": {"Mutant": {"file": "src/x.rs"}}},
            {"scenario": "Other"},
        ]
    }


def setup_crate(root: Path) -> None:
    for name in ("src/lib.rs", "src/main.rs"):
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text("fn main() {}\n")
    stale = root / ".marestail" / "mutants.out" / "outcomes.json"
    stale.parent.mkdir(parents=True)
    stale.write_text("{}")


def writes(root: Path, report: dict[str, Any] | None, output: str = "") -> Callable[[list[str]], tuple[int, str]]:
    def reply(command: list[str]) -> tuple[int, str]:
        if command == VERSION:
            return 0, "cargo-mutants 25.0.0"
        if report is not None:
            (root / ".marestail" / "mutants.out").mkdir(parents=True, exist_ok=True)
            (root / ".marestail" / "mutants.out" / "outcomes.json").write_text(json.dumps(report))
        return 2, output

    return reply


def test_skips_without_changed_sources(tmp_path: Path, fake_run: Any) -> None:
    fake = fake_run(rust)
    result = rs_mutation.run_gate(make_context(tmp_path, scope_changed=True))
    assert (result.gate, result.ok, result.summary) == ("rs.mutation", True, "skipped: no changed rust sources")
    assert fake.calls == []


@pytest.mark.parametrize(
    ("reply", "finding"),
    [
        ((127, ""), f"cargo is not installed: {rust.INSTALL['cargo']}"),
        ((101, "error: no such command: `mutants`"), f"cargo mutants is not installed: {rust.INSTALL['mutants']}"),
        ((1, "  " + "v" * 250 + "  "), "cargo mutants --version failed: " + "v" * 200),
    ],
)
def test_mutants_missing(tmp_path: Path, fake_run: Any, reply: tuple[int, str], finding: str) -> None:
    fake = fake_run(rust, [reply])
    result = rs_mutation.run_gate(make_context(tmp_path))
    assert (result.ok, result.summary, result.findings) == (False, "cargo-mutants missing", [finding])
    assert fake.options == [{"cwd": tmp_path, "env": {}, "timeout": 60}]


def test_full_run(tmp_path: Path, fake_run: Any) -> None:
    setup_crate(tmp_path)
    fake = fake_run(rust, writes(tmp_path, outcomes()))
    result = rs_mutation.run_gate(make_context(tmp_path, {"rust": {"mutation_args": ["--in-place"], "mutation_timeout": "99"}}))
    work = str(tmp_path / ".marestail")
    assert fake.calls[1] == ["cargo", "mutants", "--output", work, "--no-shuffle", "--colors", "never", "--jobs", "2", "--in-place"]
    assert fake.options[1] == {"cwd": tmp_path, "env": {}, "timeout": 99}
    assert (result.ok, result.summary) == (False, "3 of 5 mutants not killed")
    assert result.findings == [
        "src/lib.rs:3 add: replace add -> i32 with 0 survived",
        "src/main.rs:8 ?: delete ! Failure",
        "src/x.rs:0 ?:  ?",
    ]


def test_scoped_run_passes(tmp_path: Path, fake_run: Any) -> None:
    setup_crate(tmp_path)
    report = {"outcomes": [mutant("src/lib.rs", 1, "f", "n", "CaughtMutant")]}
    fake = fake_run(rust, writes(tmp_path, report))
    ctx = make_context(tmp_path, {"rust": {"mutation_jobs": 4}}, scope_changed=True, changed={"src/lib.rs"})
    result = rs_mutation.run_gate(ctx)
    assert fake.calls[1][-3:] == ["4", "--file", "src/lib.rs"]
    assert fake.options[1]["timeout"] == 7200
    assert (result.ok, result.summary, result.findings) == (True, "all 1 mutants killed", [])


def test_no_outcomes(tmp_path: Path, fake_run: Any) -> None:
    setup_crate(tmp_path)
    fake_run(rust, writes(tmp_path, None, "error: boom\n"))
    result = rs_mutation.run_gate(make_context(tmp_path))
    assert (result.ok, result.summary, result.findings) == (False, "cargo mutants produced no outcomes.json (exit 2)", ["error: boom"])


@pytest.mark.parametrize(
    ("report", "summary"),
    [
        ({"outcomes": [{"scenario": "Baseline", "summary": "Failure"}]}, "tests fail before any mutation"),
        ({"outcomes": [mutant("a.rs", 1, "f", "n", "Unviable")]}, "no viable mutants were generated"),
        ({}, "no viable mutants were generated"),
    ],
)
def test_verdict_failures(tmp_path: Path, report: dict[str, Any], summary: str) -> None:
    result = rs_mutation.verdict(make_context(tmp_path), report, "log line\n", 0.0)
    assert (result.ok, result.summary, result.findings) == (False, summary, ["log line"])


def test_is_mutant_requires_a_dict_scenario() -> None:
    assert rs_mutation.is_mutant({"scenario": ["Mutant"]}) is False
    assert rs_mutation.is_mutant({"scenario": {"Mutant": {}}}) is True


def test_after_colon_splits_once() -> None:
    assert rs_mutation.after_colon("replace x with y: z in f", "f") == "z"
    assert rs_mutation.after_colon("plain", "f") == "plain"


def test_verdict_rejects_a_missing_output(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match=r"^output$"):
        rs_mutation.verdict(make_context(tmp_path), {"outcomes": []}, None, 0.0)  # type: ignore[arg-type]


def test_require_output_keeps_text() -> None:
    rs_mutation.require_output("log")


def test_clear_outcomes_ignores_a_missing_folder(tmp_path: Path) -> None:
    rs_mutation.clear_outcomes(tmp_path / "missing")
    assert rs_mutation.IGNORE_MISSING is True


def test_verdict_caps_findings(tmp_path: Path) -> None:
    report = {"outcomes": [mutant("a.rs", n, "f", "n", "MissedMutant") for n in range(70)]}
    result = rs_mutation.verdict(make_context(tmp_path), report, "", 0.0)
    assert (result.summary, len(result.findings)) == ("70 of 70 mutants not killed", 60)
