import itertools
import json
import time
from pathlib import Path
from typing import Any

import pytest

from marestail import java
from marestail.context import Context
from marestail.gates import java_deps
from marestail.report import Result, result_seconds
from tests.conftest import make_context, reject_none

WEB = "src/main/java/app/web/Api.java"
DOMAIN = "src/main/java/app/domain/Order.java"
REPO = "src/main/java/app/repo/Store.java"
LAYERS: list[dict[str, Any]] = [
    {"from": "app.domain", "forbid": ["app.web", "src/main/java/app/repo"], "forbid_external": ["javax.servlet.*"]},
    {"from": "src/main/java/app/web"},
]
DATA: dict[str, Any] = {
    "files": [
        {"path": WEB, "package": "app.web", "imports": [{"name": "javax.servlet.Http", "line": 2}]},
        {
            "path": DOMAIN,
            "package": "app.domain",
            "imports": [{"name": "javax.servlet.Filter", "line": 3}, {"name": "java.util.List", "line": 4}],
        },
        {"path": REPO, "package": "app.repo", "imports": []},
    ],
    "edges": [
        {"from": DOMAIN, "to": WEB, "toPackage": "app.web", "symbol": "app.web.Api", "line": 7},
        {"from": DOMAIN, "to": REPO, "toPackage": "app.repo", "symbol": "app.repo.Store", "line": 8},
        {"from": WEB, "to": DOMAIN, "toPackage": "app.domain", "symbol": "app.domain.Order", "line": 5},
    ],
}
BREAKS = [
    f"{DOMAIN}:7 app.domain must not depend on app.web (app.web.Api)",
    f"{DOMAIN}:8 app.domain must not depend on src/main/java/app/repo (app.repo.Store)",
    f"{DOMAIN}:3 app.domain must not depend on javax.servlet.Filter (matches javax.servlet.*)",
    f"{DOMAIN}:7 dependency cycle: {DOMAIN} <-> {WEB}",
]


@pytest.fixture(autouse=True)
def clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "time", itertools.count(50.0, 2.0).__next__)


def project(root: Path, layers: list[dict[str, Any]] | None = LAYERS) -> None:
    for relative in (WEB, DOMAIN, REPO):
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_text("class X {}\n")
    if layers is not None:
        (root / ".java-layers.json").write_text(json.dumps({"layers": layers}))


def fake_scan(monkeypatch: pytest.MonkeyPatch, reply: tuple[Any, str | None]) -> list[tuple[str, list[Path]]]:
    seen: list[tuple[str, list[Path]]] = []

    def scan(ctx: Context, mode: str, paths: list[Path], extra: list[str] | None = None) -> tuple[Any, str | None]:
        seen.append((mode, paths))
        return reply

    monkeypatch.setattr(java, "scan", reject_none(scan))
    return seen


def fields(result: Result) -> tuple[str, bool, str, list[str], float]:
    return result.gate, result.ok, result.summary, result.findings, result_seconds(result)


def test_needs_layer_contract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project(tmp_path, None)
    seen = fake_scan(monkeypatch, (DATA, None))
    result = java_deps.run_gate(make_context(tmp_path))
    assert fields(result) == (
        "java.deps",
        False,
        "no .java-layers.json; copy templates/java-layers.json and name the layers",
        [".java-layers.json:1 missing"],
        0.0,
    )
    assert seen == []


def test_skips_without_sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".java-layers.json").write_text(json.dumps({"layers": []}))
    seen = fake_scan(monkeypatch, (DATA, None))
    assert fields(java_deps.run_gate(make_context(tmp_path))) == ("java.deps", True, "skipped: no Java sources", [], 0.0)
    assert seen == []


def test_reports_scan_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project(tmp_path)
    seen = fake_scan(monkeypatch, (None, "javac not found"))
    assert fields(java_deps.run_gate(make_context(tmp_path))) == ("java.deps", False, "javac not found", [], 2.0)
    assert seen == [("deps", [tmp_path / DOMAIN, tmp_path / REPO, tmp_path / WEB])]


def test_reports_layer_breaks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project(tmp_path)
    fake_scan(monkeypatch, (DATA, None))
    assert fields(java_deps.run_gate(make_context(tmp_path))) == ("java.deps", False, "4 layer breaks", BREAKS, 2.0)


def test_clean_contract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project(tmp_path, [{"from": "app.repo", "forbid": ["app.web"]}])
    fake_scan(monkeypatch, ({"files": DATA["files"], "edges": DATA["edges"][:2]}, None))
    assert fields(java_deps.run_gate(make_context(tmp_path))) == ("java.deps", True, "layer contracts kept", [], 2.0)


def test_scoped_run_keeps_changed_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project(tmp_path)
    fake_scan(monkeypatch, (DATA, None))
    ctx = make_context(tmp_path, scope_changed=True, changed={WEB})
    assert fields(java_deps.run_gate(ctx)) == ("java.deps", True, "layer contracts kept", [], 2.0)


def test_findings_are_capped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project(tmp_path, [{"from": "app.domain", "forbid": ["app.web"]}])
    edges = [{"from": DOMAIN, "to": WEB, "toPackage": "app.web", "symbol": "S", "line": n} for n in range(1, 62)]
    fake_scan(monkeypatch, ({"files": DATA["files"], "edges": edges}, None))
    result = java_deps.run_gate(make_context(tmp_path))
    assert (result.summary, len(result.findings), result.findings[-1]) == (
        "61 layer breaks",
        60,
        f"{DOMAIN}:60 app.domain must not depend on app.web (S)",
    )


def test_verdict_clean() -> None:
    assert fields(java_deps.verdict([], 48.0)) == ("java.deps", True, "layer contracts kept", [], 2.0)


def test_scoped_findings(tmp_path: Path) -> None:
    findings = ["a/A.java:1 x", "b/B.java:2 y", "c:3 z"]
    assert java_deps.scoped_findings(make_context(tmp_path), findings) is findings
    ctx = make_context(tmp_path, scope_changed=True, changed={"b/B.java"}, focus={"c"})
    assert java_deps.scoped_findings(ctx, findings) == ["b/B.java:2 y", "c:3 z"]


def test_layer_findings_under_nested_root(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"java": {"root": "svc"}})
    data = {
        "files": [{"path": "svc/a/A.java", "package": "a", "imports": [{"name": "org.x.Y", "line": 1}]}],
        "edges": [{"from": "svc/a/A.java", "to": "svc/b/B.java", "toPackage": "", "symbol": "B", "line": 4}],
    }
    layers = [{"from": "a", "forbid": ["b"], "forbid_external": ["org.*"]}, {"from": "a/", "forbid": ["b/"]}]
    assert java_deps.layer_findings(ctx, layers, data) == [
        "svc/a/A.java:4 a/ must not depend on b/ (B)",
        "svc/a/A.java:1 a must not depend on org.x.Y (matches org.*)",
    ]


def test_layer_findings_unknown_edge_package(tmp_path: Path) -> None:
    data = {"files": [], "edges": [{"from": "x/X.java", "to": "y/Y.java", "toPackage": "app.web", "symbol": "Y", "line": 2}]}
    assert java_deps.layer_findings(make_context(tmp_path), [{"from": "app", "forbid": ["app.web"]}], data) == []
    assert java_deps.layer_findings(make_context(tmp_path), [{"from": "XXXX", "forbid": ["app.web"]}], data) == []
    assert java_deps.layer_findings(make_context(tmp_path), [{"from": "x/", "forbid": ["app.web"]}], data) == [
        "x/X.java:2 x/ must not depend on app.web (Y)"
    ]


@pytest.mark.parametrize(
    ("path", "package", "layer", "prefix", "expected"),
    [
        ("src/a/A.java", "app.domain", "app.domain", "", True),
        ("src/a/A.java", "app.domain.model", "app.domain", "", True),
        ("src/a/A.java", "app.domainx", "app.domain", "", False),
        ("src/a/A.java", "app", "app.domain", "", False),
        ("svc/src/a/A.java", "", "src/a", "svc/", True),
        ("svc/src/ab/A.java", "", "src/a", "svc/", False),
        ("src/a/A.java", "", "src/a", "svc/", False),
    ],
)
def test_inside(path: str, package: str, layer: str, prefix: str, expected: bool) -> None:
    assert java_deps.inside(path, package, layer, prefix) is expected
