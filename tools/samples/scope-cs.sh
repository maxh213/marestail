#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."

if command -v dotnet >/dev/null 2>&1; then
    echo "note: dotnet is installed; this script still only runs unit-level assertions (no live fixture is defined)"
else
    echo "note: dotnet MISSING on this machine; cs gates verified at unit level with a fabricated dotnet layer"
fi

python3 - <<'EOF'
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, ".")

from marestail import dotnet
from marestail.config import Config
from marestail.context import Context
from marestail.gates import cs_crap, cs_deps, cs_lint, cs_mutation, cs_tests


def ctx_for(root: Path, changed: set[str], lines: dict[str, set[int]]) -> Context:
    return Context(config=Config(root=root, raw={}), scope_changed=True, changed=changed, focus=set(), changed_lines_map=lines)


with tempfile.TemporaryDirectory() as d:
    root = Path(d)
    src = root / "src"
    src.mkdir()
    (root / ".marestail").mkdir()
    dirty = src / "Dirty.cs"
    fresh = src / "Fresh.cs"
    dirty.write_text("class Dirty {\n#pragma warning disable\n    [ExcludeFromCodeCoverage]\n    void M() {}\n}\n")
    fresh.write_text("class Fresh {\n    void M() {}\n}\n")
    ctx = ctx_for(root, {"src/Fresh.cs"}, {"src/Fresh.cs": {2}})
    unscoped = Context(config=Config(root=root, raw={}))

    coverage = {"files": {
        "src/Dirty.cs": {"lines": {"5": 0}, "missing_lines": [5], "missing_branches": [[6, 1]]},
        "src/Fresh.cs": {"lines": {"2": 0, "9": 0}, "missing_lines": [2, 9], "missing_branches": []},
    }}
    assert cs_tests.coverage_findings(coverage, ctx) == ["src/Fresh.cs:2 not covered"]
    assert len(cs_tests.coverage_findings(coverage, unscoped)) == 4

    real_sources = dotnet.sources
    dotnet.sources = lambda c: [dirty, fresh]
    try:
        assert cs_tests.attribute_findings(ctx) == []
        assert len(cs_tests.attribute_findings(unscoped)) == 1

        members = [
            {"file": "src/Fresh.cs", "line": 3, "startLine": 3, "endLine": 10, "name": "Fresh.Untouched", "complexity": 6, "hasBody": True},
            {"file": "src/Fresh.cs", "line": 2, "startLine": 2, "endLine": 4, "name": "Fresh.Touched", "complexity": 5, "hasBody": True},
        ]
        dotnet.load_coverage = lambda c: {"files": {}}
        seen = {}
        def fake_scan(c, mode, paths):
            seen["paths"] = paths
            return members, None
        dotnet.scan = fake_scan
        result = cs_crap.run_gate(ctx)
        assert seen["paths"] == [fresh], seen
        assert not result.ok
        assert len(result.findings) == 1 and "Fresh.Touched" in result.findings[0], result.findings
        result_all = cs_crap.run_gate(unscoped)
        assert len(result_all.findings) == 2, result_all.findings
    finally:
        dotnet.sources = real_sources

    (root / ".dotnet-layers.json").write_text(json.dumps({"layers": [{"from": "src", "forbid": ["vendor"]}]}))
    real_scan = dotnet.scan
    data = {"edges": [{"from": "src/Dirty.cs", "to": "vendor/Lib.cs", "line": 3, "symbol": "Lib"}], "files": []}
    dotnet.scan = lambda c, mode, paths: (data, None)
    try:
        scoped_result = cs_deps.run_gate(ctx)
        assert scoped_result.ok, scoped_result.findings
        all_result = cs_deps.run_gate(unscoped)
        assert not all_result.ok and "src/Dirty.cs:3" in all_result.findings[0], all_result.findings
    finally:
        dotnet.scan = real_scan

    app = root / "App" / "App.csproj"
    tests = root / "App.Tests" / "App.Tests.csproj"
    app.parent.mkdir()
    tests.parent.mkdir()
    app.write_text("<Project/>")
    tests.write_text("<Project/>")
    sarif_body = {
        "version": "2.1.0",
        "runs": [{"results": [
            {"ruleId": "CA9999", "level": "warning", "message": {"text": "dirty finding"},
             "locations": [{"physicalLocation": {"artifactLocation": {"uri": "src/Dirty.cs"}, "region": {"startLine": 4}}}]},
            {"ruleId": "CA1111", "level": "warning", "message": {"text": "fresh finding"},
             "locations": [{"physicalLocation": {"artifactLocation": {"uri": "src/Fresh.cs"}, "region": {"startLine": 7}}}]},
            {"ruleId": "CS0000", "level": "error", "message": {"text": "project level"}},
        ]}],
    }
    real_projects, real_dotnet, real_files = dotnet.projects, dotnet.dotnet, dotnet.files
    dotnet.projects = lambda c: (app, tests, None)
    dotnet.files = lambda c: [dirty, fresh]
    def fake_dotnet(c, args, **kw):
        target = next((a.split("=", 1)[1].split("%")[0] for a in args if a.startswith("-p:ErrorLog=")), None)
        if target:
            Path(target).write_text(json.dumps(sarif_body))
        return 0, ""
    dotnet.dotnet = fake_dotnet
    try:
        lint_scoped = cs_lint.run_gate(ctx)
        assert not lint_scoped.ok
        texts = lint_scoped.findings
        assert any("src/Fresh.cs:7 CA1111" in t for t in texts), texts
        assert any("CS0000" in t for t in texts), texts
        assert not any("Dirty" in t for t in texts), texts
        lint_all = cs_lint.run_gate(unscoped)
        assert any("Dirty" in t for t in lint_all.findings), lint_all.findings
        empty = ctx_for(root, set(), {})
        skipped = cs_lint.run_gate(empty)
        assert skipped.ok and skipped.summary.startswith("skipped"), skipped.summary
    finally:
        dotnet.projects, dotnet.dotnet, dotnet.files = real_projects, real_dotnet, real_files

    args = cs_mutation.command(ctx, app, tests, root / "out", ["src/Fresh.cs"])
    assert "**/src/Fresh.cs" in args, args
    assert not any("Dirty" in a for a in args), args
    real_projects, real_dotnet = dotnet.projects, dotnet.dotnet
    dotnet.projects = lambda c: (app, tests, None)
    captured = {}
    def fake_tool(c, args, **kw):
        captured.setdefault("calls", []).append(args)
        if args and args[0] == "stryker":
            out = Path(args[args.index("-O") + 1])
            out.mkdir(parents=True, exist_ok=True)
            (out / "reports").mkdir(exist_ok=True)
            (out / "reports" / "mutation-report.json").write_text(json.dumps({"files": {"src/Fresh.cs": {"mutants": [{"status": "Killed", "location": {"start": {"line": 2}}}]}}}))
        return 0, ""
    dotnet.dotnet = fake_tool
    try:
        killed = cs_mutation.run_gate(ctx)
        assert killed.ok, killed.findings
        stryker_args = next(a for a in captured["calls"] if a and a[0] == "stryker")
        assert "**/src/Fresh.cs" in stryker_args, stryker_args
        empty = ctx_for(root, set(), {})
        skipped = cs_mutation.run_gate(empty)
        assert skipped.ok and skipped.summary.startswith("skipped"), skipped.summary
    finally:
        dotnet.projects, dotnet.dotnet = real_projects, real_dotnet

print("cs unit assertions OK")
EOF
