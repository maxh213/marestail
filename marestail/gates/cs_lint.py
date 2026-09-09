import json
import re
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

from marestail import dotnet
from marestail.context import Context
from marestail.report import Result
from marestail.shell import tail

MAX_LINES = 60
ANALYSIS_LEVEL = "8.0"
LEVELS = {"error", "warning"}
SUPPRESSION = re.compile(r"#pragma\s+warning\s+disable|\[\s*SuppressMessage")


def run_gate(ctx: Context) -> Result:
    started = time.time()
    if ctx.scope_changed and not ctx.changed_under(ctx.dotnet_root(), (".cs",)):
        return Result.skipped("cs.lint", "no changed C# files")
    product, tests, error = dotnet.projects(ctx)
    if error:
        return Result("cs.lint", False, error, [], time.time() - started)
    findings = suppression_findings(ctx)
    for project in dict.fromkeys([product, tests]):
        sarif = ctx.work / f"cs-lint-{project.stem}.sarif"
        sarif.unlink(missing_ok=True)
        code, output = dotnet.dotnet(ctx, build_args(project, sarif), timeout=900)
        if not sarif.exists():
            return Result("cs.lint", False, dotnet.hint(code, output) or f"no SARIF written for {dotnet.rel(ctx, project)}; the build did not compile", tail(output), time.time() - started)
        findings += sarif_findings(ctx, sarif, project)
    if ctx.scope_changed:
        findings = [f for f in findings if f.split(":")[0] in ctx.changed]
    findings = sorted(set(findings))
    summary = f"analyzers clean (AnalysisLevel {ANALYSIS_LEVEL}, Recommended)" if not findings else f"{len(findings)} problems"
    return Result("cs.lint", not findings, summary, findings[:MAX_LINES], time.time() - started)


def build_args(project: Path, sarif: Path) -> list[str]:
    return [
        "build", str(project), "-t:Rebuild", "-nologo", "-v:q",
        "-p:EnableNETAnalyzers=true", f"-p:AnalysisLevel={ANALYSIS_LEVEL}", "-p:AnalysisMode=Recommended",
        "-p:EnforceCodeStyleInBuild=true", "-p:TreatWarningsAsErrors=false", f"-p:ErrorLog={sarif}%2cversion=2.1",
        "-p:GenerateDocumentationFile=true", "-p:NoWarn=CS1591%3bCS1573%3bCS1587%3bCS1712",
    ]


def suppression_findings(ctx: Context) -> list[str]:
    findings = []
    for path in dotnet.files(ctx):
        for number, line in enumerate(path.read_text(errors="replace").splitlines(), start=1):
            if SUPPRESSION.search(line):
                findings.append(f"{dotnet.rel(ctx, path)}:{number} analyzer suppressed in source; fix the code instead")
    return findings


def sarif_findings(ctx: Context, sarif: Path, project: Path) -> list[str]:
    data = json.loads(sarif.read_text())
    if not str(data.get("version", "")).startswith("2.1"):
        return [f"{dotnet.rel(ctx, sarif)}:1 SARIF version {data.get('version')} is not 2.1; the ErrorLog comma must be escaped as %2c"]
    findings = []
    for result in data["runs"][0].get("results", []):
        where = location(ctx, result, project)
        if where is None or result.get("level", "warning") not in LEVELS:
            continue
        message = " ".join(result["message"]["text"].split())
        findings.append(f"{where} {result.get('ruleId', '?')}: {message[:200]}")
    return findings


def location(ctx: Context, result: dict, project: Path) -> str | None:
    locations = result.get("locations") or []
    if not locations:
        return f"{dotnet.rel(ctx, project)}:1"
    physical = locations[0]["physicalLocation"]
    uri = physical["artifactLocation"]["uri"]
    path = Path(unquote(urlparse(uri).path)) if uri.startswith("file:") else ctx.dotnet_root() / unquote(uri)
    path = path.resolve()
    if not path.is_relative_to(ctx.root.resolve()) or not path.is_relative_to(ctx.dotnet_root().resolve()) or dotnet.generated(ctx, path):
        return None
    return f"{dotnet.rel(ctx, path)}:{physical.get('region', {}).get('startLine', 1)}"
