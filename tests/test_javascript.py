from pathlib import Path
from typing import Any

from marestail import javascript
from tests.conftest import make_context

TS = {"ts": {"root": "web"}}


def test_script_names() -> None:
    assert javascript.script("comments").name == "ts_comments.mjs"
    assert javascript.script("complexity").name == "ts_complexity.mjs"
    assert javascript.script("depth").name == "ts_depth.mjs"
    assert javascript.COMMENTS.parent.name == "js"


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
