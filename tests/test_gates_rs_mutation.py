import json
import random
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from marestail import rust
from marestail.gates import rs_mutation
from tests.conftest import checked, make_context, untimed

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
    result = untimed(rs_mutation.run_gate(make_context(tmp_path, scope_changed=True)), rs_mutation.GATE)
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
    result = checked(rs_mutation.run_gate(make_context(tmp_path)), rs_mutation.GATE)
    assert (result.ok, result.summary, result.findings) == (False, "cargo-mutants missing", [finding])
    assert fake.options == [{"cwd": tmp_path, "env": {}, "timeout": 60}]


def test_full_run(tmp_path: Path, fake_run: Any) -> None:
    setup_crate(tmp_path)
    fake = fake_run(rust, writes(tmp_path, outcomes()))
    result = checked(
        rs_mutation.run_gate(make_context(tmp_path, {"rust": {"mutation_args": ["--in-place"], "mutation_timeout": "99"}})),
        rs_mutation.GATE,
    )
    work = str(tmp_path / ".marestail")
    assert fake.calls[1] == ["cargo", "mutants", "--output", work, "--no-shuffle", "--colors", "never", "--in-place"]
    assert fake.options[1] == {"cwd": tmp_path, "env": {}, "timeout": 99}
    assert (result.ok, result.summary) == (False, "3 of 5 mutants not killed")
    assert result.findings == [
        "src/lib.rs:3 add: replace add -> i32 with 0 survived",
        "src/main.rs:8 ?: delete ! Failure",
        "src/x.rs:0 ?:  ?",
    ]


def test_the_default_jobs_is_two(tmp_path: Path, fake_run: Any) -> None:
    setup_crate(tmp_path)
    fake = fake_run(rust, writes(tmp_path, outcomes()))

    checked(rs_mutation.run_gate(make_context(tmp_path)), rs_mutation.GATE)

    work = str(tmp_path / ".marestail")
    assert fake.calls[1] == ["cargo", "mutants", "--output", work, "--no-shuffle", "--colors", "never", "--jobs", "2"]


def knob_raw(key: int | None, rust_section: dict[str, Any] | None = None) -> dict[str, Any]:
    raw: dict[str, Any] = {"rust": rust_section or {}}
    if key is not None:
        raw["run"] = {"jobs": key}
    return raw


@pytest.mark.parametrize(("env", "key"), [(None, 3), ("3", None)])
def test_the_knob_sets_the_jobs(tmp_path: Path, fake_run: Any, monkeypatch: pytest.MonkeyPatch, env: str | None, key: int | None) -> None:
    if env is not None:
        monkeypatch.setenv("MARESTAIL_JOBS", env)
    setup_crate(tmp_path)
    fake = fake_run(rust, writes(tmp_path, outcomes()))

    checked(rs_mutation.run_gate(make_context(tmp_path, knob_raw(key))), rs_mutation.GATE)

    work = str(tmp_path / ".marestail")
    assert fake.calls[1] == ["cargo", "mutants", "--output", work, "--no-shuffle", "--colors", "never", "--jobs", "3"]


def test_a_language_key_beats_the_run_key(tmp_path: Path, fake_run: Any) -> None:
    setup_crate(tmp_path)
    fake = fake_run(rust, writes(tmp_path, outcomes()))
    raw = knob_raw(3, {"mutation_jobs": 4})
    ctx = make_context(tmp_path, raw, scope_changed=True, changed={"src/lib.rs"})

    checked(rs_mutation.run_gate(ctx), rs_mutation.GATE)

    assert fake.calls[1][-3:] == ["4", "--file", "src/lib.rs"]


def test_the_variable_beats_a_language_key(tmp_path: Path, fake_run: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARESTAIL_JOBS", "3")
    setup_crate(tmp_path)
    fake = fake_run(rust, writes(tmp_path, outcomes()))
    ctx = make_context(tmp_path, knob_raw(None, {"mutation_jobs": 4}), scope_changed=True, changed={"src/lib.rs"})

    checked(rs_mutation.run_gate(ctx), rs_mutation.GATE)

    assert fake.calls[1][-3:] == ["3", "--file", "src/lib.rs"]


@pytest.mark.parametrize(("env", "key"), [(None, None), ("3", None), (None, 3)])
def test_in_place_passes_no_jobs(tmp_path: Path, fake_run: Any, monkeypatch: pytest.MonkeyPatch, env: str | None, key: int | None) -> None:
    if env is not None:
        monkeypatch.setenv("MARESTAIL_JOBS", env)
    setup_crate(tmp_path)
    fake = fake_run(rust, writes(tmp_path, outcomes()))
    ctx = make_context(tmp_path, knob_raw(key, {"mutation_args": ["--in-place"]}))

    checked(rs_mutation.run_gate(ctx), rs_mutation.GATE)

    work = str(tmp_path / ".marestail")
    assert fake.calls[1] == ["cargo", "mutants", "--output", work, "--no-shuffle", "--colors", "never", "--in-place"]


def test_scoped_run_passes(tmp_path: Path, fake_run: Any) -> None:
    setup_crate(tmp_path)
    report = {"outcomes": [mutant("src/lib.rs", 1, "f", "n", "CaughtMutant")]}
    fake = fake_run(rust, writes(tmp_path, report))
    ctx = make_context(tmp_path, {"rust": {"mutation_jobs": 4}}, scope_changed=True, changed={"src/lib.rs"})
    result = checked(rs_mutation.run_gate(ctx), rs_mutation.GATE)
    assert fake.calls[1][-3:] == ["4", "--file", "src/lib.rs"]
    assert fake.options[1]["timeout"] == 7200
    assert (result.ok, result.summary, result.findings) == (True, "all 1 mutants killed", [])


def test_failing_baseline_tails_the_cargo_output(tmp_path: Path, fake_run: Any) -> None:
    setup_crate(tmp_path)
    report = {"outcomes": [{"scenario": "Baseline", "summary": "Failure"}]}
    fake_run(rust, writes(tmp_path, report, "baseline log\n"))
    result = checked(rs_mutation.run_gate(make_context(tmp_path)), rs_mutation.GATE)
    assert (result.ok, result.summary, result.findings) == (False, "tests fail before any mutation", ["baseline log"])


def test_only_unviable_mutants_tails_the_cargo_output(tmp_path: Path, fake_run: Any) -> None:
    setup_crate(tmp_path)
    report = {"outcomes": [mutant("src/lib.rs", 1, "f", "n", "Unviable")]}
    fake_run(rust, writes(tmp_path, report, "unviable log\n"))
    result = checked(rs_mutation.run_gate(make_context(tmp_path)), rs_mutation.GATE)
    assert (result.ok, result.summary, result.findings) == (False, "no viable mutants were generated", ["unviable log"])


def test_no_outcomes(tmp_path: Path, fake_run: Any) -> None:
    setup_crate(tmp_path)
    fake_run(rust, writes(tmp_path, None, "error: boom\n"))
    result = checked(rs_mutation.run_gate(make_context(tmp_path)), rs_mutation.GATE)
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
    assert rs_mutation.after_colon("replace a: b with c: d in f", "f") == "b with c: d"


def test_clear_outcomes_ignores_a_missing_folder(tmp_path: Path) -> None:
    rs_mutation.clear_outcomes(tmp_path / "missing")


def test_verdict_caps_findings(tmp_path: Path) -> None:
    report = {"outcomes": [mutant("a.rs", n, "f", "n", "MissedMutant") for n in range(70)]}
    result = rs_mutation.verdict(make_context(tmp_path), report, "", 0.0)
    assert (result.summary, len(result.findings)) == ("70 of 70 mutants not killed", 60)


def test_findings_are_sorted_before_the_cut(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"rust": {}})
    survivors = [mutant("src/a.rs", n, "add", "swap", "MissedMutant") for n in range(1, 41)]
    survivors += [mutant("src/b.rs", n, "add", "swap", "MissedMutant") for n in range(1, 31)]
    baseline = {"scenario": "Baseline", "summary": "Success"}

    def judge(order: list[dict[str, Any]]) -> Any:
        return rs_mutation.verdict(ctx, {"outcomes": [baseline, *order]}, "", 0.0)

    shuffled = survivors.copy()
    random.Random(0).shuffle(shuffled)
    first, second = judge(survivors), judge(shuffled)
    assert len(first.findings) == 60
    assert first.findings == second.findings
    assert first.findings[0] == "src/a.rs:1 add: swap survived"
    assert first.findings.index("src/a.rs:9 add: swap survived") < first.findings.index("src/a.rs:10 add: swap survived")
    assert first.findings[59] == "src/b.rs:20 add: swap survived"
    assert "src/b.rs:21 add: swap survived" not in first.findings
    assert first.summary == "70 of 70 mutants not killed"


def test_verdict_hyper_counts_only_mutants_on_changed_lines(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, scope_changed=True, hyper=True, changed={"a.rs"}, changed_lines_map={"a.rs": {3}})
    report = {
        "outcomes": [
            mutant("a.rs", 3, "f", "n", "MissedMutant"),
            mutant("a.rs", 3, "f", "n", "CaughtMutant"),
            mutant("a.rs", 9, "f", "n", "MissedMutant"),
        ]
    }
    result = rs_mutation.verdict(ctx, report, "", 0.0)
    assert (result.ok, result.summary, result.findings) == (False, "1 of 2 mutants not killed", ["a.rs:3 f: n survived"])
    report = {"outcomes": [mutant("a.rs", 9, "f", "n", "MissedMutant")]}
    passed = checked(rs_mutation.verdict(ctx, report, "", 0.0), rs_mutation.GATE)
    assert (passed.ok, passed.summary, passed.findings) == (True, "no mutants on changed lines", [])


@pytest.mark.parametrize("section", [{}, {"mutation_timeouts": "fail"}])
def test_rust_timeout_is_killed_without_a_phrase(tmp_path: Path, fake_run: Any, section: dict[str, Any]) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "lib.rs").write_text("")
    timed_out = {
        "scenario": {
            "Mutant": {
                "file": "src/lib.rs",
                "name": "src/lib.rs:5:5: replace + with * in add",
                "span": {"start": {"line": 5, "column": 5}},
                "function": {"function_name": "add"},
            }
        },
        "summary": "Timeout",
    }
    fake_run(rust, writes(tmp_path, {"outcomes": [{"scenario": "Baseline", "summary": "Success"}, timed_out]}))
    result = checked(rs_mutation.run_gate(make_context(tmp_path, {"rust": section})), rs_mutation.GATE)
    assert (result.gate, result.ok, result.summary, result.findings) == ("rs.mutation", True, "all 1 mutants killed", [])
