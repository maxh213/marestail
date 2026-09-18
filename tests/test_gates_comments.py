import json
from pathlib import Path
from typing import Any

import pytest

from marestail import dotnet, erlang, java, ruby, rust
from marestail.gates import comments
from tests.conftest import make_context

EVERYWHERE = {"comments": {"paths": ["."]}}


def write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_clean_tree_passes(tmp_path: Path) -> None:
    write(tmp_path, "a.py", "x = 1\n")

    result = comments.run_gate(make_context(tmp_path, EVERYWHERE))

    assert (result.gate, result.ok, result.summary, result.findings) == ("comments", True, "no comments", [])


def test_python_comments_and_docstrings_are_reported(tmp_path: Path) -> None:
    write(
        tmp_path,
        "pkg/a.py",
        '#!/usr/bin/env python\n"""module"""\nx = 1  # note\n\n\nclass A:\n    """cls"""\n\n    def f(self):\n        """fn"""\n',
    )
    write(tmp_path, "b.html", "<p>\n<!-- hidden -->\n")

    result = comments.run_gate(make_context(tmp_path, EVERYWHERE))

    assert result.ok is False
    assert result.summary == "5 comments or docstrings"
    assert result.findings == [
        "pkg/a.py:3 comment: # note",
        "pkg/a.py:2 docstring",
        "pkg/a.py:7 docstring",
        "pkg/a.py:10 docstring",
        "b.html:2 comment: <!-- hidden -->",
    ]


def test_skipped_directories_and_benchmarks_are_ignored(tmp_path: Path) -> None:
    for relative in ["node_modules/a.py", ".venv/b.py", "perf/bench.py", "deep/__pycache__/c.py"]:
        write(tmp_path, relative, "# hidden\n")
    write(tmp_path, "kept.py", "# shown\n")

    assert comments.python_findings(make_context(tmp_path, EVERYWHERE)) == ["kept.py:1 comment: # shown"]


def test_skipped_reports_skip_dirs_and_benchmarks(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)

    assert [comments.skipped(tmp_path / name, ctx) for name in ["vendor/x.py", "perf/x.py", "src/x.py"]] == [True, True, False]


def test_default_paths_cover_the_root(tmp_path: Path) -> None:
    write(tmp_path, "a.py", "# hi\n")

    assert comments.python_findings(make_context(tmp_path)) == ["a.py:1 comment: # hi"]


def test_configured_paths_limit_the_scan(tmp_path: Path) -> None:
    write(tmp_path, "src/a.py", "# in\n")
    write(tmp_path, "other/b.py", "# out\n")

    assert comments.python_findings(make_context(tmp_path, {"comments": {"paths": ["src", "src"]}})) == ["src/a.py:1 comment: # in"]


def test_scoped_context_only_scans_changed_files(tmp_path: Path) -> None:
    write(tmp_path, "a.py", "# a\n")
    write(tmp_path, "b.py", "# b\n")
    ctx = make_context(tmp_path, EVERYWHERE, scope_changed=True, changed={"b.py"})

    assert comments.python_findings(ctx) == ["b.py:1 comment: # b"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("#!/bin/sh\nx = 1\n", []),
        ("x = 1\n#!/bin/sh\n", [(2, "#!/bin/sh")]),
        ("# " + "y" * 100 + "\n", [(1, "# " + "y" * 78)]),
        ("x = (\n# open\n", [(2, "# open"), (0, "could not tokenize")]),
    ],
)
def test_python_comments(text: str, expected: list[tuple[int, str]]) -> None:
    assert comments.python_comments(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("def f(:\n", []),
        ('async def f():\n    """x"""\n', [2]),
        ("x = 1\n", []),
        ('def f():\n    "not first"\n', [2]),
    ],
)
def test_docstrings(text: str, expected: list[int]) -> None:
    assert comments.docstrings(text) == expected


def test_ts_findings_needs_a_ts_root(tmp_path: Path, fake_run: Any) -> None:
    write(tmp_path, "a.ts", "// x\n")
    fake = fake_run(comments)

    assert comments.ts_findings(make_context(tmp_path, EVERYWHERE)) == []
    assert fake.calls == []


def test_ts_findings_needs_files(tmp_path: Path, fake_run: Any) -> None:
    fake = fake_run(comments)

    assert comments.ts_findings(make_context(tmp_path, {**EVERYWHERE, "ts": {"root": "web"}})) == []
    assert fake.calls == []


def test_ts_findings_runs_the_scanner(tmp_path: Path, fake_run: Any) -> None:
    source = write(tmp_path, "web/a.tsx", "// x\n")
    reply = json.dumps([{"file": str(source), "line": 4, "text": "// x"}])
    fake = fake_run(comments, [(0, reply)])

    findings = comments.ts_findings(make_context(tmp_path, {**EVERYWHERE, "ts": {"root": "web"}}))

    assert findings == ["web/a.tsx:4 comment: // x"]
    assert fake.calls == [["node", str(comments.SCRIPT), str(tmp_path / "web"), str(source)]]
    assert fake.options[0]["cwd"] == tmp_path


def test_ts_scanner_failure_keeps_the_output_tail(tmp_path: Path, fake_run: Any) -> None:
    write(tmp_path, "a.js", "// x\n")
    fake_run(comments, [(2, "  " + "a" * 50 + "b" * 200 + "  \n")])

    assert comments.ts_findings(make_context(tmp_path, {**EVERYWHERE, "ts": {"root": "."}})) == ["comment scanner failed: " + "b" * 200]


def test_elixir_findings(tmp_path: Path, fake_run: Any) -> None:
    source = write(tmp_path, "lib/a.ex", "# x\n")
    fake = fake_run(comments, [(0, json.dumps([{"file": str(source), "line": 1, "text": "# x"}])), (1, "boom")])
    ctx = make_context(tmp_path, EVERYWHERE)

    assert comments.elixir_findings(ctx) == ["lib/a.ex:1 comment: # x"]
    assert comments.elixir_findings(ctx) == ["elixir comment scanner failed: boom"]
    assert fake.calls[0] == ["elixir", str(comments.EX_SCRIPT), str(source)]


def test_elixir_findings_without_files(tmp_path: Path, fake_run: Any) -> None:
    fake = fake_run(comments)

    assert comments.elixir_findings(make_context(tmp_path, EVERYWHERE)) == []
    assert fake.calls == []


@pytest.mark.parametrize(
    ("reply", "hint", "expected"),
    [
        ((0, '[{"file": "FILE", "line": 2, "text": "% c"}]'), None, ["src/a.erl:2 comment: % c"]),
        ((1, "bad"), "install erlang", ["install erlang"]),
        ((1, "bad"), None, ["erlang comment scanner failed: bad"]),
        ((0, "[]"), "never used", []),
    ],
)
def test_erlang_findings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reply: tuple[int, str], hint: str | None, expected: list[str]
) -> None:
    source = write(tmp_path, "src/a.erl", "% c\n")
    calls: list[tuple[str, list[str]]] = []

    def escript(ctx: object, script: str, args: list[str]) -> tuple[int, str]:
        calls.append((script, args))
        return reply[0], reply[1].replace("FILE", str(source))

    monkeypatch.setattr(erlang, "escript", escript)
    monkeypatch.setattr(erlang, "hint", lambda code, output: hint)

    assert comments.erlang_findings(make_context(tmp_path, EVERYWHERE)) == expected
    assert calls == [("comments.escript", [str(source)])]


def test_erlang_findings_without_files(tmp_path: Path) -> None:
    assert comments.erlang_findings(make_context(tmp_path, EVERYWHERE)) == []


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ((0, ""), []),
        ((0, '[{"file": "FILE", "line": 3, "text": "# r"}]'), ["app/a.rb:3 comment: # r"]),
        ((1, " oops "), ["ruby comment scanner failed: oops"]),
    ],
)
def test_ruby_findings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reply: tuple[int, str], expected: list[str]) -> None:
    source = write(tmp_path, "app/a.rb", "# r\n")
    write(tmp_path, "lib/tasks/b.txt", "# r\n")
    calls: list[tuple[str, list[Path]]] = []

    def scan(ctx: object, mode: str, paths: list[Path]) -> tuple[int, str]:
        calls.append((mode, paths))
        return reply[0], reply[1].replace("FILE", str(source))

    monkeypatch.setattr(ruby, "scan", scan)

    assert comments.ruby_findings(make_context(tmp_path, {**EVERYWHERE, "ruby": {}})) == expected
    assert calls == [("comments", [source])]


def test_ruby_findings_need_a_section_and_files(tmp_path: Path) -> None:
    write(tmp_path, "a.rb", "# r\n")

    assert comments.ruby_findings(make_context(tmp_path, EVERYWHERE)) == []
    assert comments.ruby_findings(make_context(tmp_path / "missing", {"ruby": {}})) == []


def test_dotnet_findings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = [tmp_path / "A.cs"]
    monkeypatch.setattr(dotnet, "files", lambda ctx: paths)
    monkeypatch.setattr(dotnet, "in_scope", lambda ctx, found: found)
    monkeypatch.setattr(dotnet, "scan", lambda ctx, mode, found: ([{"file": "A.cs", "line": 7, "text": "// c"}], None))

    assert comments.dotnet_findings(make_context(tmp_path, {"dotnet": {}})) == ["A.cs:7 comment: // c"]


@pytest.mark.parametrize(("paths", "expected"), [([], []), ([Path("A.cs")], ["C# comment scanner failed: broken"])])
def test_dotnet_findings_edges(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, paths: list[Path], expected: list[str]) -> None:
    monkeypatch.setattr(dotnet, "files", lambda ctx: paths)
    monkeypatch.setattr(dotnet, "in_scope", lambda ctx, found: found)
    monkeypatch.setattr(dotnet, "scan", lambda ctx, mode, found: (None, "broken"))

    assert comments.dotnet_findings(make_context(tmp_path, {"dotnet": {}})) == expected


def test_language_sections_are_optional(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)

    assert [comments.dotnet_findings(ctx), comments.rust_findings(ctx), comments.java_findings(ctx)] == [[], [], []]


def test_rust_findings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = write(tmp_path, "src/lib.rs", "// c\n")
    calls: list[tuple[str, list[Path]]] = []

    def scan(ctx: object, mode: str, paths: list[Path]) -> tuple[list[dict[str, Any]], None]:
        calls.append((mode, paths))
        return [{"file": "/abs/lib.rs", "line": 2, "text": "// c"}], None

    monkeypatch.setattr(rust, "scan", scan)
    monkeypatch.setattr(rust, "rel", lambda ctx, file: f"rel:{file}")

    assert comments.rust_findings(make_context(tmp_path, {**EVERYWHERE, "rust": {}})) == ["rel:/abs/lib.rs:2 comment: // c"]
    assert calls == [("comments", [source])]


def test_rust_findings_edges(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rust, "scan", lambda ctx, mode, paths: ([], "no cargo"))
    ctx = make_context(tmp_path, {**EVERYWHERE, "rust": {}})

    assert comments.rust_findings(ctx) == []
    write(tmp_path, "a.rs", "\n")
    assert comments.rust_findings(ctx) == ["rust comment scanner failed: no cargo"]


def test_java_findings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write(tmp_path, "A.java", "// c\n")
    replies = [([{"file": "A.java", "line": 1, "text": "// c"}], None), (None, "no jdk")]
    monkeypatch.setattr(java, "scan", lambda ctx, mode, paths: replies.pop(0))
    ctx = make_context(tmp_path, {**EVERYWHERE, "java": {}})

    assert comments.java_findings(ctx) == ["A.java:1 comment: // c"]
    assert comments.java_findings(ctx) == ["java comment scanner failed: no jdk"]


def test_markup_findings(tmp_path: Path) -> None:
    write(tmp_path, "a.html", "<p>/* not css */</p>\n   {# note #}   \n")
    write(tmp_path, "b.css", "p {}\n/* c */\n")
    write(tmp_path, "c.j2", "<!-- " + "z" * 100 + "\n")
    write(tmp_path, "d.txt", "<!-- ignored -->\n")

    assert comments.markup_findings(make_context(tmp_path, EVERYWHERE)) == [
        "a.html:2 comment: {# note #}",
        "b.css:2 comment: /* c */",
        "c.j2:1 comment: <!-- " + "z" * 75,
    ]
