from pathlib import Path
from typing import Any

from marestail import elixir
from tests.conftest import make_context


def test_script_names() -> None:
    assert elixir.script("comments").name == "comments.exs"
    assert elixir.script("complexity").name == "complexity.exs"
    assert elixir.script("coverage").name == "coverage.exs"
    assert Path(elixir.__file__).resolve().parent / "ex" == elixir.SCANNERS
    assert elixir.DEADCODE.parent.name == "ex"
    assert elixir.DEADCODE.is_file()


def test_scan(tmp_path: Path, fake_run: Any) -> None:
    fake = fake_run(elixir, [(0, "[]")])
    ctx = make_context(tmp_path, {"elixir": {"root": "app"}})
    assert elixir.scan(ctx, "complexity", ["lib/a.ex"], timeout=600) == (0, "[]")
    assert fake.calls == [["elixir", str(elixir.COMPLEXITY), "lib/a.ex"]]
    assert fake.options == [{"cwd": tmp_path / "app", "timeout": 600}]


def test_scan_custom_cwd(tmp_path: Path, fake_run: Any) -> None:
    fake = fake_run(elixir, [(1, "nope")])
    assert elixir.scan(make_context(tmp_path), "comments", ["a.ex"], cwd=tmp_path) == (1, "nope")
    assert fake.options == [{"cwd": tmp_path, "timeout": 3600}]


def test_deadcode_flags(tmp_path: Path) -> None:
    out = tmp_path / "out.json"
    empty = elixir.deadcode_command(make_context(tmp_path), out)
    assert empty == ["mix", "run", "--no-start", str(elixir.DEADCODE), "--out", str(out)]
    raw = {"elixir": {"preset": "phoenix", "deadcode_ignore_modules": ["A"], "deadcode_ignore": ["x"]}}
    assert elixir.deadcode_command(make_context(tmp_path, raw), out)[-6:] == [
        "--preset",
        "phoenix",
        "--ignore-modules",
        "A",
        "--ignore",
        "x",
    ]


def test_project_files(tmp_path: Path) -> None:
    (tmp_path / "app" / "lib").mkdir(parents=True)
    (tmp_path / "app" / "lib" / "a.ex").write_text("")
    (tmp_path / "app" / "lib" / "b.ex").write_text("")
    ctx = make_context(tmp_path, {"elixir": {"root": "app"}})
    assert elixir.project_files(ctx, tmp_path / "app", ["app/lib/b.ex", "app/lib/a.ex", "app/lib/zz.ex"]) == ["lib/a.ex", "lib/b.ex"]
    assert elixir.project_files(ctx, tmp_path, ["app/lib/a.ex", "lib/a.ex"]) == ["app/lib/a.ex"]
    assert elixir.strip_prefix("lib/a.ex", Path(".")) == Path("lib/a.ex")
