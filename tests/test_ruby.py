from pathlib import Path
from typing import Any

import pytest

from marestail import ruby
from tests.conftest import make_context


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, []), (["a", 2], ["a", "2"]), ("docker exec", ["docker exec"]), (3, ["3"])],
)
def test_listify(value: Any, expected: list[str]) -> None:
    assert ruby.listify(value) == expected


def test_source_defaults() -> None:
    assert ruby.SOURCES_KEY == "sources"
    assert ruby.DEFAULT_FOLDERS == ["app", "lib"]


def test_bundle_default_and_configured(tmp_path: Path) -> None:
    assert ruby.bundle(make_context(tmp_path), "rspec") == ["bundle", "exec", "rspec"]
    ctx = make_context(tmp_path, {"ruby": {"exec": ["bin/exec"]}})
    assert ruby.bundle(ctx, "rubocop", "-a") == ["bin/exec", "rubocop", "-a"]


def test_scan_without_files_skips_ruby(tmp_path: Path, fake_run: Any) -> None:
    fake = fake_run(ruby)
    assert ruby.scan(make_context(tmp_path), "deps", []) == (0, "[]")
    assert fake.calls == []


def test_scan_uses_configured_ruby(tmp_path: Path, fake_run: Any) -> None:
    fake = fake_run(ruby, [(0, '[{"name": "a"}]')])
    ctx = make_context(tmp_path, {"ruby": {"ruby": "/opt/ruby"}})
    files = [tmp_path / "a.rb", tmp_path / "b.rb"]
    assert ruby.scan(ctx, "deps", files, extra=["/root"]) == (0, '[{"name": "a"}]')
    assert fake.calls == [["/opt/ruby", str(ruby.SCRIPT), "deps", "/root", str(files[0]), str(files[1])]]
    assert fake.options == [{"cwd": tmp_path, "timeout": 600}]


def test_scan_prefers_local_ruby(tmp_path: Path, fake_run: Any) -> None:
    fake = fake_run(ruby, [(0, ""), (0, "[]")])
    assert ruby.scan(make_context(tmp_path), "complexity", [tmp_path / "a.rb"]) == (0, "[]")
    assert fake.calls == [["ruby", "-e", ""], ["ruby", str(ruby.SCRIPT), "complexity", str(tmp_path / "a.rb")]]
    assert fake.options[0] == {"cwd": tmp_path, "timeout": 30}


@pytest.mark.parametrize(("raw", "image"), [({}, "ruby:3.2-slim"), ({"ruby": {"image": "ruby:3.3"}}, "ruby:3.3")])
def test_ruby_bin_falls_back_to_docker(tmp_path: Path, fake_run: Any, raw: dict[str, Any], image: str) -> None:
    fake_run(ruby, [(127, "not found")])
    root = ruby.MARESTAIL_ROOT
    assert ruby.ruby_bin(make_context(tmp_path, raw)) == [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{tmp_path}:{tmp_path}",
        "-v",
        f"{root}:{root}",
        "-w",
        str(tmp_path),
        image,
        "ruby",
    ]


def test_script_paths() -> None:
    assert ruby.SCRIPT.name == "scan.rb"
    assert ruby.SCRIPT.parent.name == "rb"
    assert Path(ruby.__file__).resolve().parent / "rb" / "scan.rb" == ruby.SCRIPT
    assert ruby.SCRIPT.is_file()
    assert (ruby.MARESTAIL_ROOT / "marestail" / "ruby.py").exists()


@pytest.mark.parametrize(("output", "expected"), [("", []), ('[{"a": 1}]', [{"a": 1}])])
def test_scanned(output: str, expected: list[dict[str, int]]) -> None:
    assert ruby.scanned(output) == expected


def test_relative(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"ruby": {"root": "app"}})
    assert ruby.relative("models/user.rb", ctx) == "app/models/user.rb"
    assert ruby.relative(str(tmp_path / "lib" / "x.rb"), ctx) == "lib/x.rb"
    assert ruby.relative("/elsewhere/x.rb", ctx) == "/elsewhere/x.rb"


def test_skip_dirs() -> None:
    assert sorted(ruby.SKIP_DIRS) == [".git", "coverage", "log", "node_modules", "spec", "test", "tmp", "vendor"]


def test_sources_uses_app_and_lib(tmp_path: Path) -> None:
    for name in ("app/a.rb", "lib/b.rb", "spec/c.rb", "other/d.rb"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
    found = ruby.sources(make_context(tmp_path))
    assert found == [tmp_path / "app" / "a.rb", tmp_path / "lib" / "b.rb"]
