import ast
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from marestail import depth, dotnet, erlang, java, ruby, rust
from marestail.config import Config
from marestail.context import Context
from marestail.depth import Module
from tests.conftest import FakeRun

SCANNERS = ["python", "ts", "elixir", "erlang", "ruby", "dotnet", "rust", "java"]


def write(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)


def parse(source: str) -> ast.Module:
    return ast.parse(source)


def first_function(source: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    return depth.functions(parse(source))[0]


def test_module_properties() -> None:
    assert (Module("a", [], 5).ratio, Module("a", ["x", "y"], 5).ratio) == (5.0, 2.5)
    assert (Module("a", [], 0, lines=300).long, Module("a", [], 0, lines=301).long) == (False, True)
    assert Module("a", list("abcd"), 23).shallow is True
    assert Module("a", list("abcd"), 24).shallow is False
    assert Module("a", list("abc"), 1).shallow is False


def test_scanners_order() -> None:
    assert [section for section, _ in depth.scanners()] == SCANNERS
    assert [scanner.__name__ for _, scanner in depth.scanners()] == [f"{section}_modules" for section in SCANNERS]


def test_raw_modules_only_runs_configured_sections(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for section in SCANNERS:
        monkeypatch.setattr(depth, f"{section}_modules", lambda config, name=section: [Module(name, [], 0)])
    config = Config(root=tmp_path, raw={"java": {}, "python": {}, "rust": {}, "ts": "flat"})
    assert [module.path for module in depth.raw_modules(config)] == ["python", "rust", "java"]


@pytest.mark.parametrize(
    ("relative", "expected"),
    [
        ("pkg/mod.py", False),
        ("mod.py", False),
        ("tests/mod.py", True),
        ("pkg/node_modules/mod.py", True),
        ("a_tests.py/mod.py", True),
        ("pkg/a_tests.py", True),
        ("pkg/test_a.py", True),
        ("perf/bench.py", True),
        ("src/perf/bench.py", False),
        ("pkg/tests", False),
    ],
)
def test_skipped(tmp_path: Path, relative: str, expected: bool) -> None:
    assert depth.skipped(tmp_path / relative, tmp_path) is expected


PY_SOURCE = """import os
import _thread
from pkg._inner import thing
from other import _hidden
from . import sibling
from .._up import x


def forward(a, b):
    return target(a, b)


def _helper():
    return 1


class Thing:
    def method(self, value):
        return other(value)


async def run(x):
    return work(x)


NAME = 1
_PRIVATE = 2
first, second = 1, 2
obj.attr = 3
"""


def test_python_modules(tmp_path: Path) -> None:
    write(tmp_path, {"src/pkg/core.py": PY_SOURCE, "src/pkg/tests/t.py": "x = 1\n", "src/test_x.py": "", "outside.py": ""})
    modules = depth.python_modules(Config(root=tmp_path, raw={"python": {"root": "src"}}))
    assert [module.path for module in modules] == ["src/pkg/core.py"]
    found = modules[0]
    assert (found.public, found.statements, found.lines) == (["forward", "Thing", "run", "NAME"], 19, 0)
    assert found.pass_throughs == [
        "src/pkg/core.py:9 forward only forwards its arguments",
        "src/pkg/core.py:22 run only forwards its arguments",
        "src/pkg/core.py:18 method only forwards its arguments",
    ]
    assert found.private_imports == [
        "src/pkg/core.py:4 imports private module other._hidden from outside its package",
    ]


def test_python_modules_default_root(tmp_path: Path) -> None:
    write(tmp_path, {"b.py": "x = 1\n", "a.py": "", "perf/c.py": ""})
    assert [module.path for module in depth.python_modules(Config(root=tmp_path, raw={"python": {}}))] == ["a.py", "b.py"]


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("__all__ = ['a', 'b']\ndef c(): pass\n", ["a", "b"]),
        ("__all__ = ('a', 1, x)\n", ["a", 1]),
        ("__all__ = names\n__all__ = ['late']\n", ["late"]),
        ("__all__ = names\ndef f(): pass\n", ["f"]),
        ("x = __all__ = ['both']\n", ["both"]),
        ("__all__: list = ['typed']\nA = 1\n", ["A"]),
        ("def _a(): pass\nclass B: pass\nasync def c(): pass\nD = E = 1\n_F = 1\ng.h = 2\nimport i\n", ["B", "c", "D", "E"]),
    ],
)
def test_public_names(source: str, expected: list[Any]) -> None:
    assert depth.public_names(parse(source)) == expected


def test_explicit_all_absent() -> None:
    assert depth.explicit_all(parse("x = 1\n")) is None
    assert depth.explicit_all(parse("__all__ = []\n")) == []


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("def f(a, b):\n    return g(a, b)\n", True),
        ("def f(self, a):\n    return g(a)\n", True),
        ("def f(cls, a):\n    return g(a)\n", True),
        ("async def f(a):\n    return g(a)\n", True),
        ("def f():\n    return g()\n", False),
        ("def f(a, b):\n    return g(b, a)\n", False),
        ("def f(a):\n    return g(a, 1)\n", False),
        ("def f(a):\n    return g(a.b)\n", False),
        ("def f(a):\n    return g(a, k=1)\n", False),
        ("def f(a):\n    return a\n", False),
        ("def f(a):\n    return\n", False),
        ("def f(a):\n    g(a)\n", False),
        ("def f(a):\n    x = 1\n    return g(a)\n", False),
        ("@d\ndef f(a):\n    return g(a)\n", False),
        ("def f(a, b):\n    return g(a)\n", False),
    ],
)
def test_is_pass_through(source: str, expected: bool) -> None:
    assert depth.is_pass_through(first_function(source)) is expected


@pytest.mark.parametrize(
    ("source", "path", "expected"),
    [
        ("import _x\n", "a.py", []),
        ("import _x\n", "pkg/a.py", []),
        ("import os._x\n", "pkg/a.py", ["os._x"]),
        ("import pkg._x.y\n", "other/a.py", ["pkg._x.y"]),
        ("from pkg import _x, y\n", "pkg/a.py", []),
        ("from pkg import _x, y\n", "a.py", ["pkg._x"]),
        ("from pkg._in import a\n", "a.py", ["pkg._in", "pkg._in.a"]),
        ("from . import _x\n", "a.py", []),
        ("import os\nfrom os import path\n", "a.py", []),
    ],
)
def test_private_imports(tmp_path: Path, source: str, path: str, expected: list[str]) -> None:
    found = depth.private_imports(parse(source), tmp_path / path, tmp_path, "L")
    assert found == [f"L:1 imports private module {target} from outside its package" for target in expected]


@pytest.mark.parametrize(
    ("target", "package", "expected"),
    [
        ("a._b", ("a",), False),
        ("a._b", ("c",), True),
        ("_a", (), False),
        ("_a", ("p",), False),
        ("p._a", ("q",), True),
        ("a.b._c", ("a",), True),
    ],
)
def test_outside_private(target: str, package: tuple[str, ...], expected: bool) -> None:
    assert depth.outside_private(target, package) is expected


def test_forwarders() -> None:
    found = [{"line": 3, "name": "f", "target": "g"}]
    assert depth.forwarders("x.ts", found) == ["x.ts:3 f only forwards its arguments"]
    assert depth.targeted_forwarders("x.rs", found) == ["x.rs:3 f only forwards its arguments to g"]


def ts_config(root: Path) -> Config:
    return Config(root=root, raw={"ts": {"root": "web"}})


def test_ts_modules_without_files(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake = fake_run(depth)
    write(tmp_path, {"web/src/a.test.ts": "", "web/src/a.js": ""})
    assert depth.ts_modules(ts_config(tmp_path)) == []
    assert fake.calls == []


def test_ts_modules(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    write(tmp_path, {"web/src/b.tsx": "", "web/src/a.ts": "", "web/src/tests/c.ts": "", "web/src/x.test.ts": "", "web/other/d.ts": ""})
    entry = {"file": str(tmp_path / "web/src/a.ts"), "exports": ["a"], "statements": 4, "passThroughs": [{"line": 2, "name": "f"}]}
    fake = fake_run(depth, [(0, json.dumps([entry]))])
    modules = depth.ts_modules(ts_config(tmp_path))
    web = tmp_path / "web"
    assert fake.calls == [["node", str(depth.TS_SCRIPT), str(web), str(web / "src/a.ts"), str(web / "src/b.tsx")]]
    assert fake.options[0]["cwd"] == web
    assert modules == [Module("web/src/a.ts", ["a"], 4, ["web/src/a.ts:2 f only forwards its arguments"])]


def test_ts_modules_custom_source(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    write(tmp_path, {"lib/a.ts": ""})
    fake = fake_run(depth, [(0, "[]")])
    assert depth.ts_modules(Config(root=tmp_path, raw={"ts": {"source": "lib"}})) == []
    assert fake.calls[0][3:] == [str(tmp_path / "lib/a.ts")]


def test_ts_modules_failure(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    write(tmp_path, {"web/src/a.ts": ""})
    fake_run(depth, [(1, "x" * 10 + "y" * 300)])
    config = ts_config(tmp_path)
    with pytest.raises(SystemExit) as raised:
        depth.ts_modules(config)
    assert str(raised.value) == "ts depth analysis failed: " + "y" * 300


def test_elixir_modules(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    write(tmp_path, {"app/lib/b.ex": "", "app/lib/a.ex": "", "app/test/t.ex": "", "app/lib/c.exs": ""})
    items = [
        {"file": str(tmp_path / "app/lib/a.ex"), "public": ["f"], "statements": 3, "pass_throughs": ["raw"]},
        {"file": str(tmp_path / "app/lib/b.ex")},
    ]
    fake = fake_run(depth, [(0, json.dumps(items))])
    config = Config(root=tmp_path, raw={"elixir": {"root": "app"}})
    assert depth.elixir_modules(config) == [Module("app/lib/a.ex", ["f"], 3, ["raw"]), Module("app/lib/b.ex", [], 0, [])]
    app = tmp_path / "app"
    assert fake.calls == [["elixir", str(depth.EX_SCRIPT), str(app / "lib/a.ex"), str(app / "lib/b.ex")]]
    assert fake.options[0]["cwd"] == app


def test_elixir_modules_empty_and_failing(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake = fake_run(depth, [(1, "boom")])
    config = Config(root=tmp_path, raw={"elixir": {}})
    assert depth.elixir_modules(config) == []
    assert fake.calls == []
    write(tmp_path, {"a.ex": ""})
    assert depth.elixir_modules(config) == []
    assert len(fake.calls) == 1


def test_source_files(tmp_path: Path) -> None:
    write(tmp_path, {"b.rb": "", "a.rb": "", "a_spec.rb": "", "spec/c.rb": "", "d.py": ""})
    assert depth.source_files(tmp_path, "*.rb", "_spec.rb") == [tmp_path / "a.rb", tmp_path / "b.rb"]


def fake_erlang(monkeypatch: pytest.MonkeyPatch, files: list[Path], reply: tuple[int, str]) -> list[Any]:
    calls: list[Any] = []
    monkeypatch.setattr(erlang, "source_files", lambda ctx: calls.append(ctx) or files)

    def escript(ctx: Context, script: str, args: list[str]) -> tuple[int, str]:
        calls.append((script, args))
        return reply

    monkeypatch.setattr(erlang, "escript", escript)
    return calls


def test_erlang_modules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    items = [{"file": str(tmp_path / "src/a.erl"), "public": ["f/1"], "statements": 2, "pass_throughs": [{"line": 5, "name": "f/1"}]}]
    calls = fake_erlang(monkeypatch, [tmp_path / "src/a.erl"], (0, json.dumps([*items, {"file": str(tmp_path / "src/b.erl")}])))
    config = Config(root=tmp_path, raw={"erlang": {}})
    assert depth.erlang_modules(config) == [
        Module("src/a.erl", ["f/1"], 2, ["src/a.erl:5 f/1 only forwards its arguments"]),
        Module("src/b.erl", [], 0, []),
    ]
    assert calls == [Context(config=config), ("depth.escript", [str(tmp_path / "src/a.erl")])]


def test_erlang_modules_empty_and_failing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = Config(root=tmp_path, raw={"erlang": {}})
    fake_erlang(monkeypatch, [], (0, "[]"))
    assert depth.erlang_modules(config) == []
    calls = fake_erlang(monkeypatch, [tmp_path / "a.erl"], (1, "boom"))
    assert depth.erlang_modules(config) == []
    assert len(calls) == 2


def fake_ruby(monkeypatch: pytest.MonkeyPatch, reply: tuple[int, str]) -> list[Any]:
    calls: list[Any] = []

    def scan(ctx: Context, mode: str, files: list[Path]) -> tuple[int, str]:
        calls.append((ctx, mode, files))
        return reply

    monkeypatch.setattr(ruby, "scan", scan)
    return calls


def test_ruby_modules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write(tmp_path, {"app/lib/a.rb": "", "app/lib/a_spec.rb": "", "app/spec/b.rb": ""})
    items = [{"file": str(tmp_path / "app/lib/a.rb"), "public": ["A#f"], "statements": 7, "pass_throughs": ["p"]}]
    calls = fake_ruby(monkeypatch, (0, json.dumps(items)))
    config = Config(root=tmp_path, raw={"ruby": {"root": "app"}})
    assert depth.ruby_modules(config) == [Module("app/lib/a.rb", ["A#f"], 7, ["p"])]
    assert calls == [(Context(config=config), "depth", [tmp_path / "app/lib/a.rb"])]


@pytest.mark.parametrize(("reply", "files"), [((0, ""), {"a.rb": ""}), ((1, "[{}]"), {"a.rb": ""}), ((0, "[{}]"), {})])
def test_ruby_modules_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reply: tuple[int, str], files: dict[str, str]) -> None:
    write(tmp_path, files)
    calls = fake_ruby(monkeypatch, reply)
    assert depth.ruby_modules(Config(root=tmp_path, raw={"ruby": {}})) == []
    assert len(calls) == len(files)


ITEM = {"file": "src/A.x", "public": ["A.f"], "statements": 9, "pass_throughs": [{"line": 4, "name": "f", "target": "g"}]}
TARGETED = Module("src/A.x", ["A.f"], 9, ["src/A.x:4 f only forwards its arguments to g"])


def fake_scanner(monkeypatch: pytest.MonkeyPatch, module: Any, files: list[Path], reply: tuple[Any, str | None]) -> list[Any]:
    calls: list[Any] = []
    monkeypatch.setattr(module, "sources", lambda ctx: calls.append(ctx) or files)

    def scan(ctx: Context, mode: str, paths: list[Path]) -> tuple[Any, str | None]:
        calls.append((mode, paths))
        return reply

    monkeypatch.setattr(module, "scan", scan)
    return calls


def test_dotnet_modules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = Config(root=tmp_path, raw={"dotnet": {}})
    calls = fake_scanner(monkeypatch, dotnet, [Path("A.cs")], ([ITEM], None))
    assert depth.dotnet_modules(config) == [TARGETED]
    assert calls == [Context(config=config), ("depth", [Path("A.cs")])]


def test_dotnet_modules_empty_and_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = Config(root=tmp_path, raw={"dotnet": {}})
    fake_scanner(monkeypatch, dotnet, [], ([ITEM], None))
    assert depth.dotnet_modules(config) == []
    fake_scanner(monkeypatch, dotnet, [Path("A.cs")], ([ITEM], "bad"))
    assert depth.dotnet_modules(config) == []


def test_rust_modules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = Config(root=tmp_path, raw={"rust": {}})
    calls = fake_scanner(monkeypatch, rust, [Path("lib.rs")], ([ITEM], None))
    monkeypatch.setattr(rust, "rel", lambda ctx, path: calls.append(ctx) or f"crate/{path}")
    expected = Module("crate/src/A.x", ["A.f"], 9, ["crate/src/A.x:4 f only forwards its arguments to g"])
    assert depth.rust_modules(config) == [expected]
    assert calls == [Context(config=config), ("depth", [Path("lib.rs")]), Context(config=config)]


def test_rust_modules_empty_and_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = Config(root=tmp_path, raw={"rust": {}})
    fake_scanner(monkeypatch, rust, [], ([ITEM], None))
    assert depth.rust_modules(config) == []
    fake_scanner(monkeypatch, rust, [Path("lib.rs")], (None, "bad"))
    assert depth.rust_modules(config) == []


def test_java_modules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = Config(root=tmp_path, raw={"java": {}})
    calls = fake_scanner(monkeypatch, java, [Path("A.java")], ([ITEM, ITEM], ""))
    assert depth.java_modules(config) == [TARGETED, TARGETED]
    assert calls == [Context(config=config), ("depth", [Path("A.java")])]
    fake_scanner(monkeypatch, java, [], ([ITEM], None))
    assert depth.java_modules(config) == []


def test_java_modules_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_scanner(monkeypatch, java, [Path("A.java")], (None, "no jdk"))
    config = Config(root=tmp_path, raw={"java": {}})
    with pytest.raises(SystemExit) as raised:
        depth.java_modules(config)
    assert str(raised.value) == "java depth analysis failed: no jdk"


def test_rule_breaks() -> None:
    modules = [Module("a", [], 0, ["p1"], ["i1"]), Module("b", [], 0), Module("c", [], 0, [], ["i2"])]
    assert depth.rule_breaks(modules) == ["p1", "i1", "i2"]


def test_report_line() -> None:
    assert depth.report_line(Module("a.py", ["x"], 12, lines=7)) == f"{'a.py':<50}      1     12   12.0      7"
    assert depth.report_line(Module("b.py", list("abcd"), 4, lines=301)).endswith("  4      4    1.0    301  shallow, long")
    assert depth.report_line(Module("c.py", [], 3, lines=400)).endswith("  long")


def test_report() -> None:
    modules = [Module("deep.py", ["a"], 30, ["deep.py:1 f only forwards its arguments"]), Module("thin.py", ["a", "b"], 2)]
    assert depth.report(modules).splitlines() == [
        depth.REPORT_HEADER,
        depth.report_line(modules[1]),
        depth.report_line(modules[0]),
        "",
        "hard rules:",
        "deep.py:1 f only forwards its arguments",
    ]
    assert depth.report([]) == "module                                             public  stmts  ratio  lines"


def test_analyse_counts_lines(tmp_path: Path) -> None:
    write(tmp_path, {"a.py": "x = 1\ny = 2\n\n"})
    config = Config(root=tmp_path, raw={"python": {}})
    modules = depth.analyse(config)
    assert [(module.path, module.lines) for module in modules] == [("a.py", 3)]


def test_analyse_missing_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(depth, "raw_modules", lambda config: [Module("gone.py", [], 0, lines=9)])
    assert depth.analyse(Config(root=tmp_path, raw={}))[0].lines == 0
