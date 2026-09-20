import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

from marestail import java
from marestail.context import Context
from marestail.report import Result, elapsed
from marestail.shell import run

GATE = "java.lint"
MAX_LINES = 60
SUPPRESSION = re.compile(r"@SuppressWarnings\b|@SuppressFBWarnings\b|//\s*NOPMD")
PMD_MAIN = "net.sourceforge.pmd.cli.PmdCli"
PMD_OK = {0, 4, 5}
FILENAME = "filename"


def run_gate(ctx: Context) -> Result:
    started = time.time()
    if untouched(ctx):
        return Result.skipped(GATE, "no changed Java files")
    error = java.require_pom(ctx)
    if error:
        return Result(GATE, False, error, [])
    files = java.files(ctx)
    if not files:
        return Result.skipped(GATE, "no Java sources")
    return lint(ctx, files, started)


def untouched(ctx: Context) -> bool:
    return ctx.scoped and not ctx.changed_under(ctx.java_root(), (".java",))


def failure(summary: str, findings: list[str], started: float) -> Result:
    return Result(GATE, False, summary, findings[:MAX_LINES], elapsed(started))


def lint(ctx: Context, files: list[Path], started: float) -> Result:
    classpath, error = java.classpath(ctx)
    if classpath is None:
        return failure(str(error), [], started)
    classes = ctx.work / "java-lint-classes"
    shutil.rmtree(classes, ignore_errors=True)
    release = java.release(ctx)
    diagnostics, error = java.scan(ctx, "lint", files, scan_args(classpath, classes, release))
    if error:
        return failure(error, [], started)
    findings = suppression_findings(ctx, files) + javac_findings(ctx, diagnostics)
    if compile_failed(diagnostics):
        return failure("does not compile", sorted(set(findings)), started)
    pmd, error = pmd_findings(ctx, files, classpath, classes, release)
    return finish(findings, pmd, error, started)


def scan_args(classpath: Path, classes: Path, release: str | None) -> list[str]:
    return ["--classpath", str(classpath), "--classes", str(classes), *(["--release", release] if release else [])]


def compile_failed(diagnostics: list[dict[str, Any]]) -> bool:
    return any(d["kind"] == "ERROR" for d in diagnostics)


def finish(findings: list[str], pmd: list[str], error: str | None, started: float) -> Result:
    if error:
        return failure(error, findings, started)
    combined = sorted(set(findings + pmd))
    summary = "javac -Xlint:all and PMD clean" if not combined else f"{len(combined)} problems"
    return Result(GATE, not combined, summary, combined[:MAX_LINES], elapsed(started))


def suppression_findings(ctx: Context, files: list[Path]) -> list[str]:
    findings = []
    for path in java.in_scope(ctx, files):
        for number, line in enumerate(path.read_text(errors="replace").splitlines(), start=1):
            if SUPPRESSION.search(line):
                findings.append(f"{java.rel(ctx, path)}:{number} warning suppressed in source; fix the code instead")
    return findings


def javac_findings(ctx: Context, diagnostics: list[dict[str, Any]]) -> list[str]:
    return [finding for finding in (javac_finding(ctx, diagnostic) for diagnostic in diagnostics) if finding is not None]


def outside_scope(ctx: Context, diagnostic: dict[str, Any]) -> bool:
    return bool(diagnostic["file"]) and not ctx.in_scope(diagnostic["file"])


def javac_finding(ctx: Context, diagnostic: dict[str, Any]) -> str | None:
    if outside_scope(ctx, diagnostic):
        return None
    file = diagnostic["file"] or pom_rel(ctx)
    code = str(diagnostic["code"] or diagnostic["kind"]).removeprefix("compiler.")
    return f"{file}:{diagnostic['line']} javac {code}: {diagnostic['message'][:200]}"


def pom_rel(ctx: Context) -> str:
    return java.rel(ctx, java.pom(ctx))


def squash(text: str) -> str:
    return " ".join(text.split())[:200]


def pmd_findings(ctx: Context, files: list[Path], classpath: Path, classes: Path, release: str | None) -> tuple[list[str], str | None]:
    tools, error = java.pmd_classpath(ctx)
    if tools is None:
        return [], str(error)
    report = ctx.work / "java-pmd.json"
    report.unlink(missing_ok=True)
    listing = ctx.work / "java-pmd-files.txt"
    listing.write_text("\n".join(map(str, files)) + "\n")
    aux = os.pathsep.join([str(classes), classpath.read_text().strip()])
    code, output = run(pmd_command(ctx, tools, listing, report, aux, release), cwd=ctx.root, timeout=1800)
    if code not in PMD_OK or not report.exists():
        return [], f"PMD failed (exit {code}): {java.output_tail(output)}"
    return read_pmd(ctx, json.loads(report.read_text())), None


def read_pmd(ctx: Context, data: dict[str, Any]) -> list[str]:
    violations = [finding for entry in data.get("files", []) for finding in entry_findings(ctx, entry)]
    problems = data.get("processingErrors", []) + data.get("configurationErrors", [])
    return violations + [problem_finding(ctx, problem) for problem in problems]


def entry_findings(ctx: Context, entry: dict[str, Any]) -> list[str]:
    relative = java.rel(ctx, entry[FILENAME])
    if not ctx.in_scope(relative):
        return []
    return [f"{relative}:{v['beginline']} PMD {v['rule']}: {squash(v['description'])}" for v in entry.get("violations", [])]


def problem_finding(ctx: Context, problem: dict[str, Any]) -> str:
    where = java.rel(ctx, problem[FILENAME]) if problem.get(FILENAME) else pom_rel(ctx)
    return f"{where}:1 PMD could not analyse: {squash(str(problem.get('message', '')))}"


def pmd_command(ctx: Context, tools: str, listing: Path, report: Path, aux: str, release: str | None) -> list[str]:
    configured = ctx.java("pmd_ruleset")
    ruleset = ctx.java_root() / str(configured) if configured else java.PMD_RULESET
    command = [
        java.tool(ctx, "java"),
        "-cp",
        tools,
        PMD_MAIN,
        "check",
        "--no-cache",
        "--no-progress",
        "--no-fail-on-violation",
        "-R",
        str(ruleset),
        "-f",
        "json",
        "-r",
        str(report),
        "--aux-classpath",
        aux,
        "--file-list",
        str(listing),
    ]
    return command + (["--use-version", f"java-{release}"] if release else [])
