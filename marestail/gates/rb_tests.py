import json
import time
from pathlib import Path

from marestail.context import Context
from marestail.report import Result
from marestail.ruby import bundle
from marestail.shell import run, tail

COVERAGE_JSON = "rb-coverage.json"
RESULTSET = Path("coverage/.resultset.json")


def run_gate(ctx: Context) -> Result:
    started = time.time()
    root = ctx.ruby_root()
    code, output = run(bundle(ctx, "rspec"), cwd=root, timeout=1800)
    if code != 0:
        return Result("rb.tests", False, "tests failed", tail(output), time.time() - started)
    resultset = root / RESULTSET
    if not resultset.exists():
        return Result("rb.tests", False, "no coverage/.resultset.json; SimpleCov must run with rspec", tail(output), time.time() - started)
    coverage = load_resultset(resultset, ctx)
    (ctx.work / COVERAGE_JSON).write_text(json.dumps(coverage))
    findings = coverage_findings(coverage, ctx)
    percent = coverage["totals"]["percent_covered"]
    scope = " on changed files" if ctx.scope_changed else ""
    summary = f"{count_examples(output)} passed, coverage {percent:.1f}%, {len(findings)} gaps{scope} (need 0)"
    return Result("rb.tests", not findings, summary, findings, time.time() - started)


def load_resultset(path: Path, ctx: Context) -> dict:
    raw = json.loads(path.read_text())
    files: dict = {}
    for suite in raw.values():
        for file, data in (suite.get("coverage") or {}).items():
            relative = relative_path(file, ctx)
            lines = data.get("lines") if isinstance(data, dict) else data
            missing = [i for i, hits in enumerate(lines or [], start=1) if hits == 0]
            branches = []
            if isinstance(data, dict):
                for arms in (data.get("branches") or {}).values():
                    if isinstance(arms, dict):
                        branches.extend(name for name, hits in arms.items() if hits == 0)
            files[relative] = {"missing_lines": missing, "missing_branches": branches, "lines": lines or []}
    total = sum(1 for f in files.values() for hits in f["lines"] if hits is not None)
    covered = sum(1 for f in files.values() for hits in f["lines"] if isinstance(hits, int) and hits > 0)
    percent = (covered / total * 100.0) if total else 100.0
    return {"files": files, "totals": {"percent_covered": percent}}


def coverage_findings(coverage: dict, ctx: Context) -> list[str]:
    findings = []
    for file, data in sorted(coverage["files"].items()):
        if ctx.scope_changed and file not in ctx.changed:
            continue
        findings.extend(f"{file}:{line} not covered" for line in data.get("missing_lines", []))
        findings.extend(f"{file} branch {arm} not taken" for arm in data.get("missing_branches", []))
    return findings


def relative_path(file: str, ctx: Context) -> str:
    path = Path(file)
    try:
        return str(path.resolve().relative_to(ctx.root.resolve()))
    except ValueError:
        return file


def count_examples(output: str) -> str:
    for line in reversed(output.splitlines()):
        if "example" in line and "failure" in line:
            return line.strip().split(" example")[0].split()[-1]
    return "?"
