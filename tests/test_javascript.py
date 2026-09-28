from pathlib import Path
from typing import Any

from marestail import javascript
from tests.conftest import make_context

TS = {"ts": {"root": "web"}}


def test_script_names() -> None:
    assert javascript.script("comments").name == "ts_comments.mjs"
    assert javascript.script("complexity").name == "ts_complexity.mjs"
    assert javascript.script("depth").name == "ts_depth.mjs"
    assert Path(javascript.__file__).resolve().parent / "js" == javascript.SCANNERS
    assert javascript.COMMENTS.parent.name == "js"
    assert javascript.COMMENTS.is_file()


def test_scan(tmp_path: Path, fake_run: Any) -> None:
    fake = fake_run(javascript, [(0, "[]")])
    files = [tmp_path / "web" / "a.ts"]
    ctx = make_context(tmp_path, TS)
    assert javascript.scan(ctx, "complexity", files) == (0, "[]")
    assert fake.calls == [["node", str(javascript.COMPLEXITY), str(tmp_path / "web"), str(files[0])]]
    assert fake.options == [{"cwd": tmp_path / "web"}]


def test_scan_custom_cwd(tmp_path: Path, fake_run: Any) -> None:
    fake = fake_run(javascript, [(0, "")])
    javascript.scan(make_context(tmp_path, TS), "comments", ["a.ts"], cwd=tmp_path)
    assert fake.options == [{"cwd": tmp_path}]


def test_located_and_rel(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, TS)
    (tmp_path / "web").mkdir()
    assert javascript.located("src/a.ts", ctx) == "web/src/a.ts"
    assert javascript.located(str(tmp_path / "web" / "a.ts"), ctx) == "web/a.ts"
    assert javascript.located("/elsewhere/a.ts", ctx) is None
    assert javascript.rel("/elsewhere/a.ts", ctx) == "/elsewhere/a.ts"
    assert javascript.labelled("  src/a.ts \n", ctx) == "web/src/a.ts"
    assert javascript.labelled(" /elsewhere/a.ts ", ctx) == " /elsewhere/a.ts "


TOOLED = {"ts": {"root": "web", "tooling": ".marestail/tooling"}}


def tooling_dir(root: Path, *names: str) -> Path:
    folder = root / ".marestail" / "tooling"
    folder.mkdir(parents=True)
    for name in names:
        (folder / name).write_text("")
    return folder


def test_scan_resolves_typescript_from_tooling(tmp_path: Path, fake_run: Any) -> None:
    fake = fake_run(javascript, [(0, "[]")])
    javascript.scan(make_context(tmp_path, TOOLED), "depth", ["a.ts"])
    assert fake.calls == [["node", str(javascript.DEPTH), str(tmp_path / ".marestail" / "tooling"), "a.ts"]]
    assert fake.options == [{"cwd": tmp_path / "web"}]


def test_tooling_is_none_without_the_key(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, TS)
    assert javascript.tooling(ctx) is None
    assert javascript.tool(ctx, "tsc") == ["npx", "tsc"]
    assert javascript.tool(ctx, "knip", ("npx", "--yes")) == ["npx", "--yes", "knip"]
    assert javascript.tooling_file(ctx, "knip.json") is None
    assert javascript.config_flag(ctx, "knip.json", "--config") == []
    assert javascript.config_or(ctx, "knip.json", "fallback") == "fallback"


def test_tooling_points_at_the_folder_and_its_binaries(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, TOOLED)
    folder = tmp_path / ".marestail" / "tooling"
    assert javascript.tooling(ctx) == folder
    assert javascript.tool(ctx, "knip", ("npx", "--yes")) == [str(folder / "node_modules" / ".bin" / "knip")]


def test_tooling_files_count_only_when_present(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, TOOLED)
    folder = tooling_dir(tmp_path, "knip.json")
    assert javascript.tooling_file(ctx, "knip.json") == folder / "knip.json"
    assert javascript.tooling_file(ctx, "tsconfig.json") is None
    assert javascript.config_flag(ctx, "knip.json", "--config") == ["--config", str(folder / "knip.json")]
    assert javascript.config_flag(ctx, "eslint.config.mjs", "-c") == []
    assert javascript.config_or(ctx, "knip.json", "fallback") == str(folder / "knip.json")
    assert javascript.config_or(ctx, "tsconfig.json", "fallback") == "fallback"
