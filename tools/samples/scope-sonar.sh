#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."

python3 - <<'EOF'
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, ".")

from marestail.config import Config
from marestail.context import Context
from marestail.gates import docs, py_runtime, qa, sonar


class FakeClient:
    def __init__(self, responses: dict) -> None:
        self.responses = responses
        self.posts: list[dict] = []

    def get(self, path: str, **params) -> dict:
        return self.responses[(path, frozenset(params.items()))]

    def post(self, path: str, **params) -> dict:
        self.posts.append(params)
        return {}


def fake_client() -> FakeClient:
    key = "proj"
    responses = {
        ("api/qualitygates/project_status", frozenset({"projectKey": key}.items())): {"projectStatus": {"status": "ERROR"}},
        ("api/issues/search", frozenset({"componentKeys": key, "issueStatuses": "ACCEPTED,FALSE_POSITIVE", "ps": 500}.items())): {
            "issues": [
                {"key": "A1", "component": "proj:src/changed.py", "line": 7, "rule": "py:S3", "issueStatus": "ACCEPTED"},
                {"key": "A2", "component": "proj:src/other.py", "line": 3, "rule": "py:S4", "issueStatus": "FALSE_POSITIVE"},
            ]
        },
        ("api/issues/search", frozenset({"componentKeys": key, "resolved": "false", "ps": 500}.items())): {
            "issues": [
                {"component": "proj:src/changed.py", "line": 5, "severity": "MAJOR", "rule": "py:S1", "message": "in scope"},
                {"component": "proj:src/other.py", "line": 9, "severity": "MINOR", "rule": "py:S2", "message": "out of scope"},
            ]
        },
        ("api/hotspots/search", frozenset({"project": key, "status": "TO_REVIEW", "ps": 500}.items())): {
            "hotspots": [
                {"component": "proj:src/changed.py", "line": 2, "message": "review this"},
                {"component": "proj:src/other.py", "line": 4, "message": "ignore this"},
            ]
        },
        ("api/measures/component", frozenset({"component": key, "metricKeys": "coverage,duplicated_lines_density"}.items())): {
            "component": {"measures": [{"metric": "coverage", "value": "80.0"}, {"metric": "duplicated_lines_density", "value": "3.5"}]}
        },
        ("api/measures/component_tree", frozenset({"component": key, "metricKeys": "duplicated_lines_density", "qualifiers": "FIL", "ps": 500}.items())): {
            "components": [
                {"path": "src/changed.py", "measures": [{"metric": "duplicated_lines_density", "value": "6.0"}]},
                {"path": "src/other.py", "measures": [{"metric": "duplicated_lines_density", "value": "9.0"}]},
                {"path": "src/clean.py", "measures": [{"metric": "duplicated_lines_density", "value": "0.0"}]},
            ]
        },
    }
    return FakeClient(responses)


def unscoped(root: Path, raw: dict) -> Context:
    return Context(config=Config(root=root, raw=raw))


def scoped(root: Path, raw: dict) -> Context:
    return Context(
        config=Config(root=root, raw=raw),
        scope_changed=True,
        changed={"src/changed.py"},
        changed_lines_map={"src/changed.py": {1, 2}},
    )


client = fake_client()
findings, status = sonar.collect(unscoped(Path("/tmp"), {}), client, "proj")
assert status == "ERROR"
assert findings == [
    "src/changed.py:7 py:S3 was marked ACCEPTED in Sonar instead of fixed; reopened. Fix the code, or a human adds an ignore rule to sonar-project.properties",
    "src/other.py:3 py:S4 was marked FALSE_POSITIVE in Sonar instead of fixed; reopened. Fix the code, or a human adds an ignore rule to sonar-project.properties",
    "quality gate ERROR",
    "src/changed.py:5 MAJOR py:S1: in scope",
    "src/other.py:9 MINOR py:S2: out of scope",
    "src/changed.py:2 hotspot: review this",
    "src/other.py:4 hotspot: ignore this",
    "sonar coverage 80.0% (need 100)",
    "sonar duplication 3.5% (need 0)",
], findings
assert len(client.posts) == 2, client.posts
assert sonar.summarize(unscoped(Path("/tmp"), {}), findings, status) == f"{len(findings)} sonar findings"
assert sonar.summarize(unscoped(Path("/tmp"), {}), [], "OK") == "sonar clean"

client = fake_client()
ctx = scoped(Path("/tmp"), {})
findings, status = sonar.collect(ctx, client, "proj")
assert status == "ERROR"
assert findings == [
    "src/changed.py:7 py:S3 was marked ACCEPTED in Sonar instead of fixed; reopened. Fix the code, or a human adds an ignore rule to sonar-project.properties",
    "src/changed.py:5 MAJOR py:S1: in scope",
    "src/changed.py:2 hotspot: review this",
    "src/changed.py:1 sonar duplication 6.0% (need 0)",
], findings
assert all("other.py" not in finding for finding in findings), findings
assert not any(finding.startswith("quality gate") for finding in findings), findings
assert not any("coverage" in finding for finding in findings), findings
assert client.posts == [{"issue": "A1", "transition": "reopen"}], client.posts
summary = sonar.summarize(ctx, findings, status)
assert summary == f"{len(findings)} sonar findings in scope (global quality gate ERROR; scope: {ctx.scope_summary()})", summary
assert sonar.summarize(ctx, [], "OK") == f"sonar clean in scope (global quality gate OK; scope: {ctx.scope_summary()})"
print("sonar scoping OK")

with tempfile.TemporaryDirectory() as d:
    root = Path(d)
    (root / "README.md").write_text("# fixture\n")
    raw = {"qa": {"cmd": "true"}, "docs": {"files": ["README.md"], "sources": ["."]}, "python": {"runtime": f"{sys.version_info[0]}.{sys.version_info[1]}"}}
    for gate, module in (("qa", qa), ("docs", docs), ("py.runtime", py_runtime)):
        plain = module.run_gate(unscoped(root, raw))
        assert "global gate" not in plain.summary, plain.summary
        noted = module.run_gate(scoped(root, raw))
        assert noted.ok, noted.summary
        assert noted.summary.endswith("(global gate — scope: changed)"), noted.summary
        assert noted.summary.startswith(plain.summary), (plain.summary, noted.summary)
print("global gate scope notes OK")
EOF
