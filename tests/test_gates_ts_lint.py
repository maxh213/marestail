import json
from pathlib import Path
from typing import Any

import pytest

from marestail.gates import ts_lint
from tests.conftest import make_context

TS = {"ts": {"root": "web"}}
ESLINT = ["npx", "eslint", ".", "--max-warnings", "0", "--format", "json"]


def with_tsconfig(root: Path, name: str = "tsconfig.app.json") -> Path:
    (root / "web").mkdir(exist_ok=True)
    (root / "web" / name).write_text("{}")
    return root


def eslint_report(root: Path) -> str:
    return json.dumps(
        [
            {"filePath": str(root / "web" / "src" / "a.ts"), "messages": [{"line": 3, "ruleId": "no-var", "message": "Use let.\nMore."}]},
            {"filePath": "src/b.ts", "messages": [{"message": ""}, {"line": 0, "ruleId": None}]},
            {"messages": []},
        ]
    )


def test_scoped_run_without_typescript_changes_is_skipped(tmp_path: Path, fake_run: Any) -> None:
    fake = fake_run(ts_lint)

    result = ts_lint.run_gate(make_context(tmp_path, TS, scope_changed=True, changed={"web/readme.md"}))

    assert (result.gate, result.ok, result.summary) == ("ts.lint", True, "skipped: no changed typescript files")
    assert fake.calls == []


def test_clean_run(tmp_path: Path, fake_run: Any) -> None:
    fake = fake_run(ts_lint, [(0, "whatever"), (0, "[]")])

    result = ts_lint.run_gate(make_context(with_tsconfig(tmp_path), TS))

    assert (result.ok, result.summary, result.findings) == (True, "tsc and eslint clean", [])
    assert fake.calls == [["npx", "tsc", "--noEmit", "-p", "tsconfig.app.json"], ESLINT]
    assert fake.options == [{"cwd": tmp_path / "web", "timeout": 900}] * 2


def test_missing_tsconfig_is_reported(tmp_path: Path, fake_run: Any) -> None:
    fake = fake_run(ts_lint, [(0, "")])

    result = ts_lint.run_gate(make_context(tmp_path, {"ts": {"root": "web", "tsconfig": "tsconfig.json"}}))

    assert (result.ok, result.summary) == (False, "1 problems")
    assert result.findings == ["marestail.toml:1 [ts] tsconfig = 'tsconfig.json' does not exist under web"]
    assert fake.calls == [ESLINT]


def test_findings_are_capped(tmp_path: Path, fake_run: Any) -> None:
    tsc = "\n".join(f"src/a.ts({n},1): error TS1: bad" for n in range(1, 71))
    fake_run(ts_lint, [(2, tsc), (1, "[]")])

    result = ts_lint.run_gate(make_context(with_tsconfig(tmp_path), TS))

    assert result.summary == "71 problems"
    assert result.findings == [f"web/src/a.ts:{n} error TS1: bad" for n in range(1, 61)]


def test_tsc_errors_are_parsed(tmp_path: Path, fake_run: Any) -> None:
    output = "npm notice hi\n  src/a.ts(4,2):   error TS2322: " + "x" * 400 + "\n/outside/b.ts(9,1): error TS1\nFound 2 errors.\n"
    fake_run(ts_lint, [(2, output)])

    findings = ts_lint.tsc_findings(make_context(with_tsconfig(tmp_path), TS))

    assert findings == ["web/src/a.ts:4 " + ("error TS2322: " + "x" * 400)[:300], "/outside/b.ts:9 error TS1"]


def test_tsc_errors_out_of_scope_are_dropped(tmp_path: Path, fake_run: Any) -> None:
    fake_run(ts_lint, [(2, "src/a.ts(4,2): error A\nsrc/b.ts(1,1): error B\n")])
    ctx = make_context(with_tsconfig(tmp_path), TS, scope_changed=True, changed={"web/src/b.ts"})

    assert ts_lint.tsc_findings(ctx) == ["web/src/b.ts:1 error B"]


def test_unparsed_tsc_output_is_passed_through(tmp_path: Path, fake_run: Any) -> None:
    fake_run(ts_lint, [(1, "npm warn old\nnpm WARN x\n\nerror TS5058: missing\n")])

    assert ts_lint.tsc_findings(
        make_context(with_tsconfig(tmp_path, "custom.json"), {"ts": {"root": "web", "tsconfig": "custom.json"}})
    ) == ["tsc: error TS5058: missing"]


def test_eslint_messages(tmp_path: Path, fake_run: Any) -> None:
    fake_run(ts_lint, [(1, "prefix " + eslint_report(tmp_path) + " suffix")])

    assert ts_lint.eslint_findings(make_context(tmp_path, TS)) == [
        "web/src/a.ts:3 no-var: Use let.",
        "web/src/b.ts:1 error: ",
        "web/src/b.ts:1 error: ",
    ]


def test_scoped_eslint_messages(tmp_path: Path, fake_run: Any) -> None:
    fake_run(ts_lint, [(1, eslint_report(tmp_path))])

    findings = ts_lint.eslint_findings(make_context(tmp_path, TS, scope_changed=True, changed={"web/src/a.ts"}))

    assert findings == ["web/src/a.ts:3 no-var: Use let."]


def test_scoped_eslint_without_matches_is_empty(tmp_path: Path, fake_run: Any) -> None:
    fake_run(ts_lint, [(1, "[]\nOops")])

    assert ts_lint.eslint_findings(make_context(tmp_path, TS, scope_changed=True)) == []


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("Oops: crashed\nnpm notice x\n", ["eslint: Oops: crashed"]),
        ("[not json\nboom", ["eslint: [not json", "eslint: boom"]),
        ('{"a": [1}', ['eslint: {"a": [1}']),
        ("[]\nwarning text", ["eslint: []", "eslint: warning text"]),
        ("npm warn []", ["marestail.toml:1 eslint exited 2 without a message"]),
    ],
)
def test_eslint_failures_without_messages(tmp_path: Path, fake_run: Any, output: str, expected: list[str]) -> None:
    fake_run(ts_lint, [(2, output)])

    assert ts_lint.eslint_findings(make_context(tmp_path, TS)) == expected


def test_eslint_success_has_no_findings(tmp_path: Path, fake_run: Any) -> None:
    fake_run(ts_lint, [(0, "garbage")])

    assert ts_lint.eslint_findings(make_context(tmp_path, TS)) == []


def test_eslint_ignores_benchmarks_only_at_the_repo_root(tmp_path: Path) -> None:
    assert ts_lint.eslint_command(make_context(tmp_path, {"ts": {"root": "."}}))[3:5] == ["--ignore-pattern", "perf/"]
    assert ts_lint.eslint_command(make_context(tmp_path, TS)) == ESLINT


@pytest.mark.parametrize(("output", "expected"), [("x", None), ("[1] [2]", [1]), ('["a"', None), ("[", None), ('[{"b": 2}]', [{"b": 2}])])
def test_parse(output: str, expected: Any) -> None:
    assert ts_lint.parse(output) == expected


def test_as_report_only_keeps_a_list() -> None:
    assert ts_lint.as_report([1]) == [1]
    assert ts_lint.as_report({"a": 1}) is None


def test_str_field_defaults_missing_keys() -> None:
    assert ts_lint.str_field({}, "message") == ""
    assert ts_lint.str_field({"message": "x"}, "message") == "x"
    assert ts_lint.str_field({"message": None}, "message") == ""
    assert ts_lint.TS_SUFFIXES == (".ts", ".tsx", ".js", ".jsx")


def test_list_field_defaults_and_rejects_a_non_list() -> None:
    assert ts_lint.list_field({}, "messages") == []
    assert ts_lint.list_field({"messages": [1]}, "messages") == [1]


def test_list_field_rejects_a_non_list() -> None:
    with pytest.raises(TypeError, match=r"^list$"):
        ts_lint.list_field({"messages": {}}, "messages")


def test_meaningful_drops_npm_noise_and_blank_lines() -> None:
    assert ts_lint.meaningful("npm notice a\n  \nnpm warn b\nnpm WARN c\n real\n") == [" real"]
