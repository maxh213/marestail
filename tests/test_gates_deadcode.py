import json
from pathlib import Path
from typing import Any

import pytest

from marestail import dotnet, erlang, java, ruby, rust
from marestail.gates import deadcode
from tests.conftest import make_context

VULTURE = "\n".join(
    [
        "marestail/a.py:3: unused function 'foo' (60% confidence)",
        "  marestail/b.py:9: unused variable 'x' (60% confidence)  ",
        "marestail/b.py:12: unreachable code after 'return' (100% confidence)",
        "not a vulture line",
    ]
)
PYTHON = {"python": {"sources": ["marestail"]}}


def test_nothing_configured_passes(tmp_path: Path) -> None:
    result = deadcode.run_gate(make_context(tmp_path))

    assert (result.gate, result.ok, result.summary, result.findings) == ("deadcode", True, "nothing unreachable", [])


def test_python_findings_through_the_gate(tmp_path: Path, fake_run: Any) -> None:
    fake = fake_run(deadcode, [(3, VULTURE)])

    result = deadcode.run_gate(make_context(tmp_path, PYTHON))

    assert (result.ok, result.summary) == (False, "2 dead definitions")
    assert result.findings == ["marestail/a.py:3 unused function 'foo'", "marestail/b.py:12 unreachable code after 'return'"]
    assert fake.options == [{"cwd": tmp_path, "timeout": 600}]


def test_scoped_gate_keeps_findings_in_scope(tmp_path: Path, fake_run: Any) -> None:
    fake_run(deadcode, [(3, VULTURE)])

    result = deadcode.run_gate(make_context(tmp_path, PYTHON, scope_changed=True, changed={"marestail/b.py"}))

    assert result.findings == ["marestail/b.py:12 unreachable code after 'return'"]


def test_python_kinds_can_be_configured(tmp_path: Path, fake_run: Any) -> None:
    fake_run(deadcode, [(0, VULTURE)])

    findings = deadcode.python_findings(make_context(tmp_path, {**PYTHON, "deadcode": {"python_kinds": ["unused variable"]}}))

    assert findings == ["marestail/b.py:9 unused variable 'x'"]


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ((127, "nope"), ["vulture is not installed: uv pip install --python .venv/bin/python vulture"]),
        ((2, " " + "e" * 250 + " "), ["vulture failed: " + "e" * 200]),
        ((1, VULTURE.splitlines()[0]), ["marestail/a.py:3 unused function 'foo'"]),
        ((0, ""), []),
        ((3, ""), []),
    ],
)
def test_python_findings_outcomes(tmp_path: Path, fake_run: Any, reply: tuple[int, str], expected: list[str]) -> None:
    fake_run(deadcode, [reply])

    assert deadcode.python_findings(make_context(tmp_path, PYTHON)) == expected


def test_python_root_resolves_paths(tmp_path: Path, fake_run: Any) -> None:
    fake = fake_run(deadcode, [(3, "pkg/a.py:1: unused class 'A' (60% confidence)")])

    findings = deadcode.python_findings(make_context(tmp_path, {"python": {"root": "svc"}}))

    assert findings == ["svc/pkg/a.py:1 unused class 'A'"]
    assert fake.options[0]["cwd"] == tmp_path / "svc"


def test_vulture_command_defaults(tmp_path: Path) -> None:
    command = deadcode.vulture_command(make_context(tmp_path, {"python": {}}))

    assert command == [
        str(tmp_path / ".venv" / "bin" / "vulture"),
        ".",
        "--min-confidence",
        "60",
        "--exclude",
        ",".join(deadcode.PYTHON_EXCLUDES),
        "--ignore-decorators",
        ",".join(deadcode.PYTHON_DECORATORS),
    ]


def test_vulture_command_settings(tmp_path: Path) -> None:
    raw = {
        "python": {"sources": ["a", "b"]},
        "deadcode": {"min_confidence": 80, "python_exclude": ["x/*"], "python_decorators": ["@d"], "python_ignore_names": ["run", "go"]},
    }

    command = deadcode.vulture_command(make_context(tmp_path, raw))

    assert command[1:] == ["a", "b", "--min-confidence", "80", "--exclude", "x/*", "--ignore-decorators", "@d", "--ignore-names", "run,go"]


def test_benchmarks_are_excluded_from_vulture() -> None:
    assert "perf/*" in deadcode.PYTHON_EXCLUDES


KNIP = {
    "files": ["src/orphan.ts"],
    "issues": [
        {"file": "src/a.ts", "exports": [{"name": "helper", "line": 4}], "types": ["Shape"], "files": ["ignored"]},
        {"exports": [], "enumMembers": [{"name": "X"}]},
    ],
}


def test_ts_findings(tmp_path: Path, fake_run: Any) -> None:
    fake = fake_run(deadcode, [(1, "noise " + json.dumps(KNIP) + " trailing {")])

    findings = deadcode.ts_findings(make_context(tmp_path, {"ts": {"root": "web"}}))

    assert findings == ["web/src/orphan.ts unused file", "web/src/a.ts:4 unused export 'helper'", "web/src/a.ts:0 unused type 'Shape'"]
    assert fake.calls == [["npx", "--yes", "knip", "--reporter", "json", "--no-progress"]]
    assert fake.options == [{"cwd": tmp_path / "web", "timeout": 900}]


def test_ts_kinds_can_be_configured(tmp_path: Path, fake_run: Any) -> None:
    fake_run(deadcode, [(0, json.dumps(KNIP))])

    findings = deadcode.ts_findings(make_context(tmp_path, {"ts": {}, "deadcode": {"ts_kinds": ["enumMembers", "exports"]}}))

    assert findings == ["src/a.ts:4 unused export 'helper'", ".:0 unused enumMember 'X'"]


def test_ts_findings_without_a_report(tmp_path: Path, fake_run: Any) -> None:
    fake_run(deadcode, [(2, " " + "k" * 300 + "\n")])

    assert deadcode.ts_findings(make_context(tmp_path, {"ts": {}})) == ["knip produced no report: " + "k" * 200]


@pytest.mark.parametrize(
    ("item", "expected"),
    [({"name": "a", "line": 3}, "f.ts:3 unused export 'a'"), ({}, "f.ts:0 unused export ''"), ("b", "f.ts:0 unused export 'b'")],
)
def test_describe(item: Any, expected: str) -> None:
    assert deadcode.describe(Path("f.ts"), "exports", item) == expected


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ((0, ""), []),
        ((0, "[\"app/a.rb:1 unused method 'x'\"]"), ["app/a.rb:1 unused method 'x'"]),
        ((1, " " + "r" * 210), ["ruby deadcode scanner failed: " + "r" * 200]),
    ],
)
def test_ruby_findings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reply: tuple[int, str], expected: list[str]) -> None:
    files = [tmp_path / "app" / "a.rb"]
    calls: list[tuple[str, list[Path]]] = []

    def scan(ctx: object, mode: str, found: list[Path]) -> tuple[int, str]:
        calls.append((mode, found))
        return reply

    monkeypatch.setattr(ruby, "sources", lambda ctx: files)
    monkeypatch.setattr(ruby, "scan", scan)

    assert deadcode.ruby_findings(make_context(tmp_path, {"ruby": {}})) == expected
    assert calls == [("dead", files)]


def test_ruby_findings_without_sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ruby, "sources", lambda ctx: [])

    assert deadcode.ruby_findings(make_context(tmp_path, {"ruby": {}})) == []


def test_language_sections_are_optional(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    scanners = [
        deadcode.python_findings,
        deadcode.ts_findings,
        deadcode.ruby_findings,
        deadcode.dotnet_findings,
        deadcode.rust_findings,
        deadcode.java_findings,
        deadcode.elixir_findings,
        deadcode.erlang_findings,
    ]

    assert [scanner(ctx) for scanner in scanners] == [[]] * 8


ENTRY = {"file": "A.cs", "line": 5, "kind": "method", "name": "Go"}


@pytest.mark.parametrize(
    ("module", "section", "failure"),
    [(dotnet, "dotnet", "C# deadcode scanner failed"), (java, "java", "java deadcode scanner failed")],
)
def test_structured_scanners(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, module: Any, section: str, failure: str) -> None:
    files = [tmp_path / "A"]
    replies = [([ENTRY], None), (None, "exploded")]
    calls: list[tuple[str, list[Path]]] = []

    def scan(ctx: object, mode: str, found: list[Path]) -> tuple[Any, Any]:
        calls.append((mode, found))
        return replies.pop(0)

    monkeypatch.setattr(module, "sources", lambda ctx: files)
    monkeypatch.setattr(module, "scan", scan)
    scanner = getattr(deadcode, f"{section}_findings")
    ctx = make_context(tmp_path, {section: {}})

    assert scanner(ctx) == ["A.cs:5 unused method 'Go'"]
    assert scanner(ctx) == [f"{failure}: exploded"]
    assert calls == [("dead", files), ("dead", files)]


@pytest.mark.parametrize("module", [dotnet, java, rust])
def test_structured_scanners_without_sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, module: Any) -> None:
    monkeypatch.setattr(module, "sources", lambda ctx: [])
    monkeypatch.setattr(rust, "use_files", lambda ctx: [])
    name = module.__name__.split(".")[-1]

    assert getattr(deadcode, f"{name}_findings")(make_context(tmp_path, {name: {}})) == []


def test_rust_findings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    files, uses = [tmp_path / "lib.rs"], [tmp_path / "main.rs"]
    replies = [([{**ENTRY, "file": "/abs/lib.rs"}], None), ([], "no cargo")]
    calls: list[tuple[str, list[Path], list[Path]]] = []

    def scan(ctx: object, mode: str, found: list[Path], uses: list[Path]) -> tuple[Any, Any]:
        calls.append((mode, found, uses))
        return replies.pop(0)

    monkeypatch.setattr(rust, "sources", lambda ctx: files)
    monkeypatch.setattr(rust, "use_files", lambda ctx: uses)
    monkeypatch.setattr(rust, "rel", lambda ctx, file: f"rel:{file}")
    monkeypatch.setattr(rust, "scan", scan)
    ctx = make_context(tmp_path, {"rust": {}})

    assert deadcode.rust_findings(ctx) == ["rel:/abs/lib.rs:5 unused method 'Go'"]
    assert deadcode.rust_findings(ctx) == ["rust deadcode scanner failed: no cargo"]
    assert calls == [("dead", files, uses)] * 2


def elixir_writer(ctx_work: Path, entries: list[dict[str, Any]], code: int = 0) -> Any:
    def reply(command: list[str]) -> tuple[int, str]:
        ctx_work.mkdir(exist_ok=True)
        (ctx_work / "ex-deadcode.json").write_text(json.dumps(entries))
        return code, "mix output"

    return reply


def test_elixir_findings(tmp_path: Path, fake_run: Any) -> None:
    entries = [
        {"file": "lib/a.ex", "line": 3, "module": "A", "function": "go", "arity": 1},
        {"file": "../../../outside.ex", "line": 4, "module": "B", "function": "stop", "arity": 0},
    ]
    fake = fake_run(deadcode, elixir_writer(tmp_path / ".marestail", entries))

    findings = deadcode.elixir_findings(make_context(tmp_path, {"elixir": {"root": "app"}}))

    assert findings == ["app/lib/a.ex:3 unused function A.go/1", "../../../outside.ex:4 unused function B.stop/0"]
    assert fake.options == [{"cwd": tmp_path / "app", "timeout": 900}]


@pytest.mark.parametrize("code", [1, 0])
def test_elixir_failures(tmp_path: Path, fake_run: Any, code: int) -> None:
    stale = tmp_path / ".marestail" / "ex-deadcode.json"
    stale.parent.mkdir()
    stale.write_text("[]")
    fake_run(deadcode, [(code, " " + "m" * 220)])

    assert deadcode.elixir_findings(make_context(tmp_path, {"elixir": {}})) == ["elixir dead code analysis failed: " + "m" * 200]
    assert not stale.exists()


def test_elixir_failure_after_writing_the_report(tmp_path: Path, fake_run: Any) -> None:
    fake_run(deadcode, elixir_writer(tmp_path / ".marestail", [], code=1))

    assert deadcode.elixir_findings(make_context(tmp_path, {"elixir": {}})) == ["elixir dead code analysis failed: mix output"]


def test_elixir_command(tmp_path: Path) -> None:
    out = tmp_path / "out.json"
    script = str(Path(deadcode.__file__).resolve().parent.parent / "ex" / "deadcode.exs")
    base = ["mix", "run", "--no-start", script, "--out", str(out)]
    raw = {"elixir": {"preset": "phoenix", "deadcode_ignore_modules": ["A", "B"], "deadcode_ignore": ["x", "y"]}}

    assert deadcode.elixir_command(make_context(tmp_path, {"elixir": {}}), out) == base
    assert deadcode.elixir_command(make_context(tmp_path, raw), out) == [
        *base,
        "--preset",
        "phoenix",
        "--ignore-modules",
        "A,B",
        "--ignore",
        "x,y",
    ]


class FakeErlang:
    def __init__(self, tmp_path: Path, compiled: tuple[int, str], xref: tuple[int, str], hint: str | None = None) -> None:
        self.sources = [tmp_path / "src" / "a.erl", tmp_path / "src" / "b.erl"]
        self.ebin = tmp_path / "ebin"
        self.compiled, self.xref, self.hint_text = compiled, xref, hint
        self.calls: list[Any] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(erlang, "source_files", lambda ctx: self.sources)
        monkeypatch.setattr(erlang, "fresh_dir", self.fresh_dir)
        monkeypatch.setattr(erlang, "erlc", self.erlc)
        monkeypatch.setattr(erlang, "escript", self.escript)
        monkeypatch.setattr(erlang, "hint", lambda code, output: self.hint_text if code else None)
        monkeypatch.setattr(erlang, "rel", lambda ctx, path: f"rel/{path.name}")

    def fresh_dir(self, path: Path) -> Path:
        self.calls.append(("fresh", path.name))
        self.ebin.mkdir()
        for name in ["b.beam", "a.beam", "notes.txt"]:
            (self.ebin / name).write_text("")
        return self.ebin

    def erlc(self, ctx: Any, args: list[str], timeout: int) -> tuple[int, str]:
        self.calls.append(("erlc", args, timeout))
        return self.compiled

    def escript(self, ctx: Any, script: str, args: list[str], timeout: int) -> tuple[int, str]:
        self.calls.append(("escript", script, args, timeout))
        return self.xref


def test_erlang_findings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    entries = [{"module": "a", "line": 2, "function": "go", "arity": 0}, {"module": "zz", "line": 5, "function": "f", "arity": 2}]
    fake = FakeErlang(tmp_path, (0, ""), (0, json.dumps(entries)))
    fake.install(monkeypatch)

    findings = deadcode.erlang_findings(make_context(tmp_path, {"erlang": {"deadcode_ignore": ["^test_", "x"]}}))

    assert findings == ["rel/a.erl:2 unused function a.go/0", "zz:5 unused function zz.f/2"]
    beams = [str(fake.ebin / "a.beam"), str(fake.ebin / "b.beam")]
    assert fake.calls == [
        ("fresh", "er-deadcode-ebin"),
        ("erlc", ["+debug_info", "-o", str(fake.ebin), *map(str, fake.sources)], 900),
        ("escript", "deadcode.escript", ["--ignore", "^test_,x", *beams], 600),
    ]


@pytest.mark.parametrize(
    ("compiled", "xref", "hint", "expected"),
    [
        ((1, "x"), (0, "[]"), "install erlang", ["install erlang"]),
        ((1, " " + "c" * 210), (0, "[]"), None, ["erlang dead code analysis failed: " + "c" * 200]),
        ((0, ""), (2, "boom"), "no escript", ["no escript"]),
        ((0, ""), (2, "boom"), None, ["erlang dead code analysis failed: boom"]),
        ((0, ""), (0, "[]"), "unused", []),
    ],
)
def test_erlang_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, compiled: tuple[int, str], xref: tuple[int, str], hint: str | None, expected: list[str]
) -> None:
    FakeErlang(tmp_path, compiled, xref, hint).install(monkeypatch)

    assert deadcode.erlang_findings(make_context(tmp_path, {"erlang": {}})) == expected


def test_erlang_without_sources_or_ignores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeErlang(tmp_path, (0, ""), (0, "[]"))
    fake.install(monkeypatch)
    ctx = make_context(tmp_path, {"erlang": {}})

    assert deadcode.erlang_findings(ctx) == []
    assert fake.calls[-1] == ("escript", "deadcode.escript", [str(fake.ebin / "a.beam"), str(fake.ebin / "b.beam")], 600)
    fake.sources = []
    assert deadcode.erlang_findings(ctx) == []


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("a:b.py:3: unused function 'f' (60% confidence)", [("a:b.py", "3", "unused function", "'f'")]),
        ("m.py:1: x:2: unused import 'y' (90% confidence)", [("m.py:1: x", "2", "unused import", "'y'")]),
        ("m.py:1: unused variable 'v (x)' (60% confidence)", [("m.py", "1", "unused variable", "'v (x)'")]),
        ("m.py:1: unused function 'f' (60% confidence) ", [("m.py", "1", "unused function", "'f'")]),
        (":1: unused function 'f' (60% confidence)", []),
        ("m.py:1: unused function (60% confidence)", []),
        ("m.py:1: unreachable code after 'return' (x% confidence)", []),
    ],
)
def test_vulture_entries(output: str, expected: list[tuple[str, str, str, str]]) -> None:
    assert deadcode.vulture_entries(output) == expected
