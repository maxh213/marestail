import itertools
import json
import time
from pathlib import Path
from typing import Any

import pytest

from marestail import golang
from marestail.gates import go_deps
from tests.conftest import checked, make_context, untimed

GATE = go_deps.GATE
MODULE = "example.com/marestail/sample"
CONTRACT = {
    "layers": [{"from": "internal/domain", "forbid": ["internal/store"], "forbid_external": ["net/http", "github.com/jackc/pgx/*"]}]
}


@pytest.fixture(autouse=True)
def clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "time", itertools.count(1.0, 0.25).__next__)


def plant(root: Path, relative: str, text: str = "package p\n") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def layers(root: Path, payload: dict[str, Any] | None = None) -> None:
    (root / go_deps.LAYERS_FILE).write_text(json.dumps(CONTRACT if payload is None else payload))


def ready(root: Path, **fields: Any) -> Any:
    plant(root, "go.mod", f"module {MODULE}\n")
    ctx = make_context(root, {"go": {}}, **fields)
    ctx.memo[golang.MODULE_KEY] = MODULE
    ctx.memo[golang.TOOLCHAIN] = {"GOVERSION": "go1.25.0", "GOROOT": "/usr/lib/go", "GOBIN": "", "GOPATH": ""}
    return ctx


def record(path: Path, line: int, imported: str) -> dict[str, Any]:
    return {"file": str(path), "line": line, "path": imported}


def test_missing_contract_wins_over_an_empty_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(golang, "sources", lambda ctx: (_ for _ in ()).throw(AssertionError("sources")))
    result = untimed(go_deps.run_gate(make_context(tmp_path, {"go": {}})), GATE)
    assert result.ok is False
    assert result.summary == "no .go-layers.json; copy templates/go-layers.json and name the layers"
    assert result.findings == [".go-layers.json:1 missing"]


def test_skips_without_sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layers(tmp_path)
    monkeypatch.setattr(golang, "preflight", lambda ctx: (_ for _ in ()).throw(AssertionError("preflight")))
    result = untimed(go_deps.run_gate(make_context(tmp_path, {"go": {}})), GATE)
    assert (result.ok, result.summary) == (True, "skipped: no Go sources")


def test_preflight_stops_before_the_scan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layers(tmp_path)
    plant(tmp_path, "a.go")
    monkeypatch.setattr(golang, "preflight", lambda ctx: "go is not installed: x")
    monkeypatch.setattr(golang, "scan", lambda ctx, mode, files: (_ for _ in ()).throw(AssertionError("scan")))
    result = checked(go_deps.run_gate(make_context(tmp_path, {"go": {}})), GATE)
    assert (result.ok, result.summary, result.findings) == (False, "go toolchain not ready", ["go is not installed: x"])


def test_scanner_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layers(tmp_path)
    plant(tmp_path, "a.go")
    monkeypatch.setattr(golang, "scan", lambda ctx, mode, files: (None, "go scanner failed (imports): boom"))
    result = checked(go_deps.run_gate(ready(tmp_path)), GATE)
    assert (result.ok, result.summary, result.findings) == (False, "import scanner failed", ["go scanner failed (imports): boom"])


def test_internal_external_and_subfolders(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layers(tmp_path)
    domain = plant(tmp_path, "internal/domain/domain.go")
    nested = plant(tmp_path, "internal/domain/web/web.go")
    store = plant(tmp_path, "internal/store/store.go")
    records = [
        record(domain, 6, f"{MODULE}/internal/store"),
        record(nested, 4, f"{MODULE}/internal/store/db"),
        record(domain, 8, "github.com/jackc/pgx/v5"),
        record(store, 3, "net/http"),
        record(store, 9, "fmt"),
        record(domain, 2, "internal/storage"),
    ]
    monkeypatch.setattr(golang, "scan", lambda ctx, mode, files: (records, None))
    result = checked(go_deps.run_gate(ready(tmp_path)), GATE)
    assert result.findings == [
        f"internal/domain/domain.go:6 internal/domain must not depend on internal/store ({MODULE}/internal/store)",
        f"internal/domain/web/web.go:4 internal/domain must not depend on internal/store ({MODULE}/internal/store/db)",
        "internal/domain/domain.go:8 internal/domain must not depend on github.com/jackc/pgx/* (github.com/jackc/pgx/v5)",
    ]
    assert result.summary == "3 layer breaks"


def test_clean_summary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layers(tmp_path, {"layers": [{"from": "internal/domain", "forbid": ["internal/web"]}]})
    domain = plant(tmp_path, "internal/domain/domain.go")
    monkeypatch.setattr(golang, "scan", lambda ctx, mode, files: ([record(domain, 2, "fmt")], None))
    result = checked(go_deps.run_gate(ready(tmp_path)), GATE)
    assert result.ok is True
    assert result.summary == go_deps.KEPT
    assert result.findings == []


def test_scope_and_hyper_filter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layers(tmp_path)
    domain = plant(tmp_path, "internal/domain/domain.go")
    other = plant(tmp_path, "internal/domain/other.go")
    records = [record(domain, 6, f"{MODULE}/internal/store"), record(other, 3, "net/http")]
    monkeypatch.setattr(golang, "scan", lambda ctx, mode, files: (records, None))
    scoped = ready(tmp_path, scope_changed=True, changed={"internal/domain/domain.go"})
    result = checked(go_deps.run_gate(scoped), GATE)
    assert result.findings == [f"internal/domain/domain.go:6 internal/domain must not depend on internal/store ({MODULE}/internal/store)"]
    hyper = ready(
        tmp_path,
        scope_changed=True,
        hyper=True,
        changed={"internal/domain/domain.go"},
        changed_lines_map={"internal/domain/domain.go": {9}},
    )
    dropped = checked(go_deps.run_gate(hyper), GATE)
    assert dropped.ok is True
    assert dropped.summary == go_deps.KEPT


def test_findings_are_capped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layers(tmp_path, {"layers": [{"from": "internal/domain", "forbid_external": ["net/http"]}]})
    domain = plant(tmp_path, "internal/domain/domain.go")
    records = [record(domain, number, "net/http") for number in range(1, 62)]
    monkeypatch.setattr(golang, "scan", lambda ctx, mode, files: (records, None))
    result = checked(go_deps.run_gate(ready(tmp_path)), GATE)
    assert (result.summary, len(result.findings)) == ("61 layer breaks", 60)


def test_template_contract() -> None:
    payload = json.loads(Path("templates/go-layers.json").read_text())
    assert payload["layers"][0]["forbid_external"] == ["net/http", "database/sql", "github.com/jackc/pgx/*"]
    assert payload["layers"][1]["forbid"] == ["internal/web"]
