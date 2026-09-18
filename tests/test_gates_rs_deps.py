import json
from pathlib import Path
from typing import Any

from marestail import rust
from marestail.gates import rs_deps
from tests.conftest import make_context

LAYERS = [{"from": "src/domain", "forbid": ["src/web", "src/db/"]}]


def setup_crate(root: Path) -> None:
    for name in ("src/lib.rs", "src/domain/user.rs"):
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text("mod x;\n")
    binary = root / ".marestail" / rust.SCAN_BIN
    binary.parent.mkdir(parents=True)
    binary.write_text("")
    (root / ".marestail" / "rs-scan" / "stamp").write_text(rust.scanner_digest())


def edge(source: str, target: str, line: int = 1, symbol: str = "x") -> dict[str, Any]:
    return {"from": source, "to": target, "line": line, "symbol": symbol}


def scanned_edges(root: Path) -> list[dict[str, Any]]:
    return [
        edge(str(root / "src/domain/user.rs"), str(root / "src/web.rs"), 3, "crate::web::Page"),
        edge("src/domain/user.rs", "src/db/pool.rs", 4, "crate::db::pool"),
        edge("src/domain.rs", "src/web/hooks.rs", 5, "crate::webhooks"),
        edge("src/a.rs", "src/b.rs", 7),
        edge("src/b.rs", "src/c.rs", 8),
        edge("src/c.rs", "src/a.rs", 9),
        edge("src/c.rs", "src/c.rs", 10),
    ]


def test_no_sources(tmp_path: Path) -> None:
    result = rs_deps.run_gate(make_context(tmp_path))
    assert (result.gate, result.ok, result.summary) == ("rs.deps", True, "skipped: no rust sources")


def test_scanner_failure(tmp_path: Path, fake_run: Any) -> None:
    setup_crate(tmp_path)
    fake_run(rust, [(3, "crash")])
    result = rs_deps.run_gate(make_context(tmp_path))
    assert (result.ok, result.summary, result.findings) == (False, "dependency scanner failed", ["rust scanner failed (deps): crash"])


def test_reports_layers_and_cycles(tmp_path: Path, fake_run: Any) -> None:
    setup_crate(tmp_path)
    fake = fake_run(rust, [(0, json.dumps(scanned_edges(tmp_path)))])
    result = rs_deps.run_gate(make_context(tmp_path, {"rust": {"layers": LAYERS}}))
    scanner = str(tmp_path / ".marestail" / rust.SCAN_BIN)
    sources = [str(tmp_path / "src/domain/user.rs"), str(tmp_path / "src/lib.rs")]
    assert fake.calls == [[scanner, "deps", "--root", str(tmp_path), *sources]]
    assert (result.ok, result.summary) == (False, "4 dependency breaks")
    assert result.findings == [
        "src/domain/user.rs:3 src/domain/user.rs must not depend on src/web.rs (crate::web::Page)",
        "src/domain/user.rs:4 src/domain/user.rs must not depend on src/db/pool.rs (crate::db::pool)",
        "src/domain.rs:5 src/domain.rs must not depend on src/web/hooks.rs (crate::webhooks)",
        "src/a.rs:7 module cycle: src/a.rs -> src/b.rs -> src/c.rs -> src/a.rs",
    ]


def test_clean(tmp_path: Path, fake_run: Any) -> None:
    setup_crate(tmp_path)
    fake_run(rust, [(0, json.dumps([edge("src/lib.rs", "src/domain/user.rs")]))])
    result = rs_deps.run_gate(make_context(tmp_path))
    assert (result.ok, result.summary, result.findings) == (True, "layer contracts kept, no module cycles", [])


def test_scoped_findings(tmp_path: Path) -> None:
    edges = rs_deps.relative_edges(make_context(tmp_path), scanned_edges(tmp_path))
    ctx = make_context(tmp_path, {"rust": {"layers": LAYERS}}, scope_changed=True, changed={"src/domain.rs", "src/b.rs"})
    assert rs_deps.layer_findings(ctx, edges) == ["src/domain.rs:5 src/domain.rs must not depend on src/web/hooks.rs (crate::webhooks)"]
    assert rs_deps.cycle_findings(ctx, edges) == ["src/a.rs:7 module cycle: src/a.rs -> src/b.rs -> src/c.rs -> src/a.rs"]
    outside = make_context(tmp_path, scope_changed=True, changed={"src/lib.rs"})
    assert rs_deps.cycle_findings(outside, edges) == []


def test_relative_edges_without_scan(tmp_path: Path) -> None:
    assert rs_deps.relative_edges(make_context(tmp_path), None) == []


def test_under() -> None:
    assert rs_deps.under("src/db", "src/db/")
    assert rs_deps.under("src/db/x.rs", "src/db")
    assert rs_deps.under("src/db.rs", "src/db")
    assert not rs_deps.under("src/dbx.rs", "src/db")


def test_load_layers(tmp_path: Path) -> None:
    assert rs_deps.load_layers(make_context(tmp_path)) == []
    (tmp_path / ".rust-layers.json").write_text(json.dumps(LAYERS))
    assert rs_deps.load_layers(make_context(tmp_path)) == LAYERS
    (tmp_path / "l.json").write_text("[]")
    assert rs_deps.load_layers(make_context(tmp_path, {"rust": {"layers_file": "l.json"}})) == []
    assert rs_deps.load_layers(make_context(tmp_path, {"rust": {"layers": []}})) == LAYERS


def test_components_finds_every_cycle() -> None:
    edges = [edge("a", "b"), edge("b", "a"), edge("b", "c"), edge("c", "d"), edge("d", "c"), edge("d", "e"), edge("x", "a")]
    graph = rs_deps.edge_graph(edges)
    assert rs_deps.components(graph) == [["d", "c"], ["b", "a"]]


def test_components_cross_edge_to_finished_node() -> None:
    edges = [edge("a", "b"), edge("b", "a"), edge("c", "a"), edge("c", "d"), edge("d", "c")]
    assert rs_deps.components(rs_deps.edge_graph(edges)) == [["b", "a"], ["d", "c"]]
