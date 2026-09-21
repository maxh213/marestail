import json
from pathlib import Path
from typing import Any

from marestail import ruby
from marestail.gates import rb_deps
from tests.conftest import make_context


def write_sources(root: Path) -> list[Path]:
    paths = [root / "app" / "models" / "user.rb", root / "lib" / "tool.rb"]
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("class X; end\n")
    return paths


def edges(root: Path) -> list[dict[str, Any]]:
    return [
        {
            "from": str(root / "app/models/user.rb"),
            "to": str(root / "app/controllers/users_controller.rb"),
            "line": 4,
            "constant": "UsersController",
        },
        {"from": str(root / "app/models/user.rb"), "to": str(root / "app/models/post.rb"), "line": 5, "constant": "Post"},
        {"from": str(root / "lib/tool.rb"), "to": str(root / "app/controllers"), "constant": "Api"},
        {"from": str(root / "lib/tool.rb"), "to": "/gems/rails.rb"},
    ]


def test_no_sources(tmp_path: Path, fake_run: Any) -> None:
    fake = fake_run(ruby)
    result = rb_deps.run_gate(make_context(tmp_path))
    assert (result.gate, result.ok, result.summary) == ("rb.deps", True, "skipped: no ruby sources")
    assert fake.calls == []


def test_scanner_failure(tmp_path: Path, fake_run: Any) -> None:
    write_sources(tmp_path)
    fake_run(ruby, [(1, "\n".join(str(n) for n in range(12)))])
    result = rb_deps.run_gate(make_context(tmp_path, {"ruby": {"ruby": "rb"}}))
    assert (result.ok, result.summary, result.findings) == (False, "dependency scanner failed", [str(n) for n in range(2, 12)])


def test_default_layers(tmp_path: Path, fake_run: Any) -> None:
    paths = write_sources(tmp_path)
    fake = fake_run(ruby, [(0, json.dumps(edges(tmp_path)))])
    result = rb_deps.run_gate(make_context(tmp_path, {"ruby": {"ruby": "rb"}}))
    assert fake.calls == [["rb", str(ruby.SCRIPT), "deps", str(tmp_path), *map(str, paths)]]
    assert (result.ok, result.summary) == (False, "2 layer breaks")
    assert result.findings == [
        "app/models/user.rb:4 app/models/user.rb must not depend on app/controllers/users_controller.rb (UsersController)",
        "lib/tool.rb:1 lib/tool.rb must not depend on app/controllers (Api)",
    ]


def test_clean_run(tmp_path: Path, fake_run: Any) -> None:
    write_sources(tmp_path)
    fake_run(ruby, [(0, "")])
    result = rb_deps.run_gate(make_context(tmp_path, {"ruby": {"ruby": "rb"}}))
    assert (result.ok, result.summary, result.findings) == (True, "layer contracts kept", [])


def test_scoped_violations(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, scope_changed=True, changed={"lib/tool.rb"})
    assert rb_deps.violations(edges(tmp_path), rb_deps.DEFAULT_LAYERS, ctx) == [
        "lib/tool.rb:1 lib/tool.rb must not depend on app/controllers (Api)"
    ]


def test_layer_prefix_matching(tmp_path: Path) -> None:
    layers = [{"from": "app/models", "forbid": ["app/views/"]}]
    found = [
        {"from": "app/modelsx/a.rb", "to": "app/views/a.rb"},
        {"from": "app/models/a.rb", "to": "app/viewsx/a.rb"},
        {"from": "app/models/a.rb", "to": "app/views/"},
    ]
    assert rb_deps.violations(found, layers, make_context(tmp_path)) == [
        "app/modelsx/a.rb:1 app/modelsx/a.rb must not depend on app/views/a.rb ()",
        "app/models/a.rb:1 app/models/a.rb must not depend on app/views/ ()",
    ]
    assert rb_deps.violations([{}], [{"from": "", "forbid": [""]}], make_context(tmp_path)) == [":1  must not depend on  ()"]


def test_load_layers(tmp_path: Path) -> None:
    configured = [{"from": "a", "forbid": ["b"]}]
    assert rb_deps.load_layers(make_context(tmp_path, {"ruby": {"layers": configured}})) == configured
    assert rb_deps.load_layers(make_context(tmp_path)) == rb_deps.DEFAULT_LAYERS
    (tmp_path / ".ruby-layers.json").write_text(json.dumps(configured))
    assert rb_deps.load_layers(make_context(tmp_path)) == configured
    (tmp_path / "layers.json").write_text("[]")
    assert rb_deps.load_layers(make_context(tmp_path, {"ruby": {"layers_file": "layers.json"}})) == []


def test_relative(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    assert rb_deps.relative(str(tmp_path / "a" / "b.rb"), ctx) == "a/b.rb"
    assert rb_deps.relative("/other/b.rb", ctx) == "/other/b.rb"


def test_in_layer_and_forbidden_trim_slashes() -> None:
    assert rb_deps.in_layer("srcX/a.rb", "srcX") is True
    assert rb_deps.in_layer("srcX/a.rb", "srcX/") is True
    assert rb_deps.forbidden("srcX/a.rb", ["srcX"]) is True
    assert rb_deps.forbidden("srcX/a.rb", ["srcX/"]) is True
    assert rb_deps.forbidden("other.rb", ["srcX"]) is False
