from pathlib import Path

import pytest

from marestail.gates import docs
from tests.conftest import make_context

LEDGER = "| Route | Status |\n|---|---|\n| `/a` | Live |\n| `/old` | retired |\n| `/ghost` | live |\n| `/dead` | gone |\n"
APP = 'app.route("/a")\n\n@app.route("/old")\nrouter.get("/new")\napp.route("/a")\n'


def write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_gate_is_skipped_without_a_docs_section(tmp_path: Path) -> None:
    result = docs.run_gate(make_context(tmp_path))

    assert (result.gate, result.ok, result.summary) == ("docs", True, "skipped: no [docs] section")


def test_clean_docs_pass(tmp_path: Path) -> None:
    write(tmp_path, "README.md", "Set API_KEY.\n")
    write(tmp_path, "src/app.py", 'os.getenv("API_KEY")\n')

    result = docs.run_gate(make_context(tmp_path, {"docs": {}}))

    assert (result.gate, result.ok, result.summary, result.findings) == ("docs", True, "docs match the code", [])


def test_scoped_run_notes_the_global_gate(tmp_path: Path) -> None:
    write(tmp_path, "src/app.py", 'os.getenv("SECRET")\n')

    result = docs.run_gate(make_context(tmp_path, {"docs": {}}, scope_changed=True))

    assert result.ok is False
    assert result.summary == "1 drift findings (global gate — scope: changed)"
    assert result.findings == ["src/app.py:1 environment variable SECRET is not documented"]


def test_route_drift(tmp_path: Path) -> None:
    write(tmp_path, "ROUTES.md", LEDGER)
    write(tmp_path, "src/app.py", APP)

    findings = docs.route_findings(make_context(tmp_path, {"docs": {"routes_file": "ROUTES.md"}}))

    assert findings == [
        "src/app.py:4 route /new is not in ROUTES.md",
        "src/app.py:3 route /old is marked retired in ROUTES.md but still exists",
        "ROUTES.md lists /ghost as live but no code serves it",
    ]


def test_route_drift_needs_a_ledger(tmp_path: Path) -> None:
    write(tmp_path, "src/app.py", APP)

    assert docs.route_findings(make_context(tmp_path, {"docs": {}})) == []
    assert docs.route_findings(make_context(tmp_path, {"docs": {"routes_file": "R.md"}})) == [
        "R.md is missing; it must list every route with a status"
    ]


def test_custom_route_patterns(tmp_path: Path) -> None:
    write(tmp_path, "R.md", "")
    write(tmp_path, "src/app.rb", "x\nhandle '/x'\n")

    findings = docs.route_findings(make_context(tmp_path, {"docs": {"routes_file": "R.md", "route_patterns": [r"handle '(/\w+)'"]}}))

    assert findings == ["src/app.rb:2 route /x is not in R.md"]


def test_env_findings(tmp_path: Path) -> None:
    write(tmp_path, "src/app.py", 'os.getenv("PORT")\nos.environ["TOKEN"]\nos.environ.get("KNOWN")\nENV.fetch("SKIP_ME")\n')
    write(tmp_path, "src/app.ts", "process.env.TOKEN\nprocess.env.MISSING\n")
    ctx = make_context(tmp_path, {"docs": {"ignore_env": ["SKIP_ME"]}})

    assert docs.env_findings(ctx, "KNOWN is set") == [
        "src/app.py:2 environment variable TOKEN is not documented",
        "src/app.ts:2 environment variable MISSING is not documented",
    ]


def test_custom_env_patterns(tmp_path: Path) -> None:
    write(tmp_path, "src/a.py", "getenv('home')\nsetting('FOO')\n")

    findings = docs.env_findings(make_context(tmp_path, {"docs": {"env_patterns": [r"setting\('(\w+)'\)"]}}), "")

    assert findings == ["src/a.py:2 environment variable FOO is not documented"]


def test_source_files_skip_tests_benchmarks_and_other_suffixes(tmp_path: Path) -> None:
    for relative in ["src/a.py", "src/b.rs", "src/tests/c.py", "perf/d.py", "src/e.txt", "node_modules/f.js", "lib/g.java"]:
        write(tmp_path, relative, "")
    ctx = make_context(tmp_path, {"docs": {"sources": ["src", "perf"]}})

    assert [path.relative_to(tmp_path).as_posix() for path in docs.source_files(ctx)] == ["src/a.py", "src/b.rs"]


def test_doc_files_follow_patterns(tmp_path: Path) -> None:
    write(tmp_path, "README.md", "one")
    write(tmp_path, "docs/a.md", "two")
    (tmp_path / "docs" / "dir.md").mkdir()
    ctx = make_context(tmp_path, {"docs": {"files": ["docs/*.md", "README.md", "README.md"]}})

    assert docs.doc_files(ctx) == [tmp_path / "README.md", tmp_path / "docs" / "a.md"]
    assert docs.doc_text(ctx) == "one\ntwo"


def test_default_doc_file_is_the_readme(tmp_path: Path) -> None:
    write(tmp_path, "README.md", "hello")
    write(tmp_path, "OTHER.md", "no")

    assert docs.doc_text(make_context(tmp_path, {"docs": {}})) == "hello"


def test_path_findings(tmp_path: Path) -> None:
    write(tmp_path, "src/real.py", "")
    write(tmp_path, "README.md", "Use `src/real.py` and `src/gone.py`.\n\nSee `src/*.py`, `other/x.py` and `src/gone.py`.\n")

    findings = docs.path_findings(make_context(tmp_path, {"docs": {"path_prefixes": ["src/", "lib/"]}}))

    assert findings == [
        "README.md:1 mentions src/gone.py, which does not exist",
        "README.md:3 mentions src/gone.py, which does not exist",
    ]


def test_path_findings_need_prefixes(tmp_path: Path) -> None:
    write(tmp_path, "README.md", "`src/gone.py`\n")

    assert docs.path_findings(make_context(tmp_path, {"docs": {}})) == []


@pytest.mark.parametrize(
    ("token", "prefixes", "expected"),
    [
        ("src/gone.py", ("src/",), True),
        ("src/gone.py", (), False),
        ("lib/gone.py", ("src/",), False),
        ("src/*.py", ("src/",), False),
        ("src/here.py", ("src/",), False),
    ],
)
def test_missing_path(tmp_path: Path, token: str, prefixes: tuple[str, ...], expected: bool) -> None:
    write(tmp_path, "src/here.py", "")

    assert docs.missing_path(make_context(tmp_path), token, prefixes) is expected


def test_full_gate_orders_routes_env_then_paths(tmp_path: Path) -> None:
    write(tmp_path, "README.md", "| `/a` | live |\n\n`src/none.py`\n")
    write(tmp_path, "src/app.py", 'app.route("/b")\nos.getenv("NEW_VAR")\n')

    result = docs.run_gate(make_context(tmp_path, {"docs": {"routes_file": "README.md", "path_prefixes": ["src/"]}}))

    assert result.summary == "4 drift findings"
    assert result.findings == [
        "src/app.py:1 route /b is not in README.md",
        "README.md lists /a as live but no code serves it",
        "src/app.py:2 environment variable NEW_VAR is not documented",
        "README.md:3 mentions src/none.py, which does not exist",
    ]


def test_line_at_counts_newlines_in_the_prefix() -> None:
    assert docs.line_at("a\nb\nc", 0) == 1
    assert docs.line_at("a\nb\nc", 2) == 2
    assert docs.line_at("a\nb\nc", 4) == 3
    assert docs.NEWLINE == "\n"
