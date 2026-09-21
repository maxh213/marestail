import json
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from marestail import dotnet
from marestail.context import Context
from marestail.report import Result, elapsed
from marestail.shell import tail

GATE = "cs.lint"
MAX_LINES = 60
ANALYSIS_LEVEL = "8.0"
LEVELS = {"error", "warning"}
COLON = ":"
SARIF_VERSION = "2.1"
SUPPRESSION = re.compile(r"#pragma\s+warning\s+disable|\[\s*SuppressMessage")


def run_gate(ctx: Context) -> Result:
    started = time.time()
    if ctx.scoped and not ctx.changed_under(ctx.dotnet_root(), (".cs",)):
        return Result.skipped(GATE, "no changed C# files")
    found = dotnet.project_pair(ctx)
    if isinstance(found, str):
        return Result(GATE, False, found, [], elapsed(started))
    return analyse(ctx, found, started)


def analyse(ctx: Context, pair: tuple[Path, Path], started: float) -> Result:
    findings = suppression_findings(ctx)
    for project in dict.fromkeys(pair):
        found = project_findings(ctx, project, findings, started)
        if isinstance(found, Result):
            return found
        findings = found
    return verdict(sorted(set(findings)), started)


def project_findings(ctx: Context, project: Path, findings: list[str], started: float) -> list[str] | Result:
    sarif = ctx.work / f"cs-lint-{project.stem}.sarif"
    sarif.unlink(missing_ok=True)
    code, output = dotnet.dotnet(ctx, build_args(project, sarif), timeout=900)
    if not sarif.exists():
        return no_sarif(ctx, project, (code, output), started)
    return findings + sarif_findings(ctx, sarif, project)


def no_sarif(ctx: Context, project: Path, outcome: tuple[int, str], started: float) -> Result:
    code, output = outcome
    message = dotnet.hint(code, output) or f"no SARIF written for {dotnet.rel(ctx, project)}; the build did not compile"
    return Result(GATE, False, message, tail(output), elapsed(started))


def verdict(findings: list[str], started: float) -> Result:
    summary = f"analyzers clean (AnalysisLevel {ANALYSIS_LEVEL}, Recommended)" if not findings else f"{len(findings)} problems"
    return Result(GATE, not findings, summary, findings[:MAX_LINES], elapsed(started))


def build_args(project: Path, sarif: Path) -> list[str]:
    return [
        "build",
        str(project),
        "-t:Rebuild",
        "-nologo",
        "-v:q",
        "-p:EnableNETAnalyzers=true",
        f"-p:AnalysisLevel={ANALYSIS_LEVEL}",
        "-p:AnalysisMode=Recommended",
        "-p:EnforceCodeStyleInBuild=true",
        "-p:TreatWarningsAsErrors=false",
        f"-p:ErrorLog={sarif}%2cversion=2.1",
        "-p:GenerateDocumentationFile=true",
        "-p:NoWarn=CS1591%3bCS1573%3bCS1587%3bCS1712",
    ]


def suppression_findings(ctx: Context) -> list[str]:
    return [
        f"{dotnet.rel(ctx, path)}:{number} analyzer suppressed in source; fix the code instead"
        for path in dotnet.in_scope(ctx, dotnet.files(ctx))
        for number in dotnet.matching_lines(path, SUPPRESSION.search)
    ]


def mapping_text(data: dict[str, Any], key: str) -> str:
    if key not in data:
        return ""
    return str(data[key])


def sarif_findings(ctx: Context, sarif: Path, project: Path) -> list[str]:
    data = json.loads(sarif.read_text())
    version = mapping_text(data, "version")
    if not version.startswith(SARIF_VERSION):
        return [
            f"{dotnet.rel(ctx, sarif)}:1 SARIF version {data.get('version')} is not {SARIF_VERSION}; the ErrorLog comma must be escaped as %2c"
        ]
    return [found for result in data["runs"][0].get("results", []) for found in result_finding(ctx, result, project)]


def result_finding(ctx: Context, result: dict[str, Any], project: Path) -> list[str]:
    where = location(ctx, result, project)
    if where is None or not reportable(result, where, ctx):
        return []
    message = " ".join(result["message"]["text"].split())
    return [f"{where} {result.get('ruleId', '?')}: {message[:200]}"]


def reportable(result: dict[str, Any], where: str, ctx: Context) -> bool:
    return result.get("level", "warning") in LEVELS and file_in_scope(where, ctx)


def path_of_finding(where: str) -> str:
    index = where.rfind(COLON)
    if index < 0:
        return where
    return where[:index]


def file_in_scope(where: str, ctx: Context) -> bool:
    path = path_of_finding(where)
    return not path.endswith(".cs") or ctx.in_scope(path)


def location(ctx: Context, result: dict[str, Any], project: Path) -> str | None:
    locations = result.get("locations") or []
    if not locations:
        return f"{dotnet.rel(ctx, project)}:1"
    physical = locations[0]["physicalLocation"]
    path = resolve_uri(ctx, physical["artifactLocation"]["uri"])
    if not reportable_path(ctx, path):
        return None
    return f"{dotnet.rel(ctx, path)}:{physical.get('region', {}).get('startLine', 1)}"


def resolve_uri(ctx: Context, uri: str) -> Path:
    path = Path(unquote(urlparse(uri).path)) if uri.startswith("file:") else ctx.dotnet_root() / unquote(uri)
    return path.resolve()


def reportable_path(ctx: Context, path: Path) -> bool:
    return path.is_relative_to(ctx.root.resolve()) and path.is_relative_to(ctx.dotnet_root().resolve()) and not dotnet.generated(ctx, path)
