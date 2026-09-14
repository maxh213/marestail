#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."

echo "note: unit-level assertions with a fabricated scanner and maven; tools/samples/java is the live fixture"

python3 - <<'EOF'
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, ".")

from marestail import java
from marestail.config import Config
from marestail.context import Context
from marestail.gates import java_crap, java_deps, java_lint, java_mutation, java_tests

RAW = {"java": {"root": "."}}


def ctx_for(root: Path, changed: set[str], lines: dict[str, set[int]]) -> Context:
    return Context(config=Config(root=root, raw=RAW), scope_changed=True, changed=changed, focus=set(), changed_lines_map=lines)


with tempfile.TemporaryDirectory() as d:
    root = Path(d)
    src = root / "src" / "main" / "java" / "app"
    src.mkdir(parents=True)
    (root / ".marestail").mkdir()
    dirty, fresh = src / "Dirty.java", src / "Fresh.java"
    dirty.write_text("package app;\n@SuppressWarnings(\"all\")\nclass Dirty {}\n")
    fresh.write_text("package app;\nclass Fresh {\n    void m() {}\n}\n")
    dirty_rel, fresh_rel = "src/main/java/app/Dirty.java", "src/main/java/app/Fresh.java"
    ctx = ctx_for(root, {fresh_rel}, {fresh_rel: {3}})
    empty = ctx_for(root, set(), {})
    unscoped = Context(config=Config(root=root, raw=RAW))

    coverage = {"files": {
        dirty_rel: {"lines": {"3": 0}, "missing_lines": [3], "missing_branches": [[3, 1, 2]]},
        fresh_rel: {"lines": {"3": 0, "9": 0}, "missing_lines": [3, 9], "missing_branches": []},
    }}
    assert java_tests.coverage_findings(coverage, ctx) == [f"{fresh_rel}:3 not covered"]
    assert len(java_tests.coverage_findings(coverage, unscoped)) == 4

    assert java_lint.suppression_findings(ctx, [dirty, fresh]) == []
    assert len(java_lint.suppression_findings(unscoped, [dirty, fresh])) == 1
    skipped = java_lint.run_gate(empty)
    assert skipped.ok and skipped.summary.startswith("skipped"), skipped.summary

    members = [
        {"file": fresh_rel, "line": 5, "startLine": 5, "endLine": 12, "name": "Fresh.untouched", "complexity": 6},
        {"file": fresh_rel, "line": 3, "startLine": 3, "endLine": 4, "name": "Fresh.touched", "complexity": 5},
    ]
    layers = {"files": [{"path": dirty_rel, "package": "app", "types": ["app.Dirty"], "imports": []}],
              "edges": [{"from": dirty_rel, "to": "src/main/java/vendor/Lib.java", "toPackage": "vendor", "symbol": "vendor.Lib", "line": 3}]}
    (root / ".java-layers.json").write_text(json.dumps({"layers": [{"from": "app", "forbid": ["vendor"]}]}))
    seen = {}

    def fake_scan(c, mode, paths, extra=None):
        seen[mode] = paths
        return (members if mode == "complexity" else layers), None

    real_load, real_scan = java.load_coverage, java.scan
    java.load_coverage, java.scan = (lambda c: {"files": {}}), fake_scan
    try:
        crap = java_crap.run_gate(ctx)
        assert seen["complexity"] == [fresh], seen
        assert len(crap.findings) == 1 and "Fresh.touched" in crap.findings[0], crap.findings
        assert len(java_crap.run_gate(unscoped).findings) == 2
        assert java_deps.run_gate(ctx).ok
        broken = java_deps.run_gate(unscoped)
        assert not broken.ok and broken.findings[0].startswith(f"{dirty_rel}:3 app must not depend on vendor"), broken.findings
    finally:
        java.load_coverage, java.scan = real_load, real_scan

    (root / "pom.xml").write_text("<project><build><plugins><plugin><artifactId>pitest-maven</artifactId></plugin></plugins></build></project>")
    calls = []

    def fake_mvn(c, args, **kw):
        calls.append(args)
        out = Path(next(a for a in args if a.startswith("-DreportsDirectory=")).split("=", 1)[1])
        out.mkdir(parents=True, exist_ok=True)
        (out / "mutations.xml").write_text(
            "<mutations><mutation status='KILLED'><sourceFile>Fresh.java</sourceFile><mutatedClass>app.Fresh</mutatedClass>"
            "<mutatedMethod>m</mutatedMethod><lineNumber>3</lineNumber><description>removed call</description></mutation></mutations>"
        )
        return 0, ""

    real_mvn = java.mvn
    java.mvn = fake_mvn
    try:
        killed = java_mutation.run_gate(ctx)
        assert killed.ok, killed
        assert "-DtargetClasses=app.Fresh,app.Fresh$*" in calls[0], calls[0]
        none = java_mutation.run_gate(empty)
        assert none.ok and none.summary.startswith("skipped"), none.summary
    finally:
        java.mvn = real_mvn

print("java unit assertions OK")
EOF
