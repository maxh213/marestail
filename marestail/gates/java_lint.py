import json
import os
import re
import shutil
import time
from pathlib import Path

from marestail import java
from marestail.context import Context
from marestail.report import Result
from marestail.shell import run

MAX_LINES = 60
SUPPRESSION = re.compile(r"@SuppressWarnings\b|@SuppressFBWarnings\b|//\s*NOPMD")
PMD_MAIN = "net.sourceforge.pmd.cli.PmdCli"
PMD_OK = {0, 4, 5}


def run_gate(ctx: Context) -> Result:
    started = time.time()
    if ctx.scoped and not ctx.changed_under(ctx.java_root(), (".java",)):
        return Result.skipped("java.lint", "no changed Java files")
    error = java.require_pom(ctx)
    if error:
        return Result("java.lint", False, error, [], 0.0)
    files = java.files(ctx)
    if not files:
        return Result.skipped("java.lint", "no Java sources")
    classpath, error = java.classpath(ctx)
    if error:
        return Result("java.lint", False, error, [], time.time() - started)
    classes = ctx.work / "java-lint-classes"
    shutil.rmtree(classes, ignore_errors=True)
    release = java.release(ctx)
    extra = ["--classpath", str(classpath), "--classes", str(classes), *(["--release", release] if release else [])]
    diagnostics, error = java.scan(ctx, "lint", files, extra)
    if error:
        return Result("java.lint", False, error, [], time.time() - started)
    findings = suppression_findings(ctx, files) + javac_findings(ctx, diagnostics)
    if any(d["kind"] == "ERROR" for d in diagnostics):
        return Result("java.lint", False, "does not compile", sorted(set(findings))[:MAX_LINES], time.time() - started)
    pmd, error = pmd_findings(ctx, files, classpath, classes, release)
    if error:
        return Result("java.lint", False, error, findings[:MAX_LINES], time.time() - started)
    findings = sorted(set(findings + pmd))
    summary = "javac -Xlint:all and PMD clean" if not findings else f"{len(findings)} problems"
    return Result("java.lint", not findings, summary, findings[:MAX_LINES], time.time() - started)


def suppression_findings(ctx: Context, files: list[Path]) -> list[str]:
    findings = []
    for path in java.in_scope(ctx, files):
        for number, line in enumerate(path.read_text(errors="replace").splitlines(), start=1):
            if SUPPRESSION.search(line):
                findings.append(f"{java.rel(ctx, path)}:{number} warning suppressed in source; fix the code instead")
    return findings


def javac_findings(ctx: Context, diagnostics: list[dict]) -> list[str]:
    findings = []
    for diagnostic in diagnostics:
        file = diagnostic["file"] or java.rel(ctx, java.pom(ctx))
        if diagnostic["file"] and not ctx.in_scope(file):
            continue
        code = str(diagnostic["code"] or diagnostic["kind"]).removeprefix("compiler.")
        findings.append(f"{file}:{diagnostic['line']} javac {code}: {diagnostic['message'][:200]}")
    return findings


def pmd_findings(ctx: Context, files: list[Path], classpath: Path, classes: Path, release: str | None) -> tuple[list[str], str | None]:
    tools, error = java.pmd_classpath(ctx)
    if error:
        return [], error
    report = ctx.work / "java-pmd.json"
    report.unlink(missing_ok=True)
    listing = ctx.work / "java-pmd-files.txt"
    listing.write_text("\n".join(map(str, files)) + "\n")
    code, output = run(
        pmd_command(ctx, tools, listing, report, os.pathsep.join([str(classes), classpath.read_text().strip()]), release),
        cwd=ctx.root,
        timeout=1800,
    )
    if code not in PMD_OK or not report.exists():
        return [], f"PMD failed (exit {code}): {output.strip()[-300:]}"
    data = json.loads(report.read_text())
    findings = []
    for entry in data.get("files", []):
        relative = java.rel(ctx, entry["filename"])
        if not ctx.in_scope(relative):
            continue
        for violation in entry.get("violations", []):
            findings.append(
                f"{relative}:{violation['beginline']} PMD {violation['rule']}: {' '.join(violation['description'].split())[:200]}"
            )
    for problem in data.get("processingErrors", []) + data.get("configurationErrors", []):
        where = java.rel(ctx, problem["filename"]) if problem.get("filename") else java.rel(ctx, java.pom(ctx))
        findings.append(f"{where}:1 PMD could not analyse: {' '.join(str(problem.get('message', '')).split())[:200]}")
    return findings, None


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
