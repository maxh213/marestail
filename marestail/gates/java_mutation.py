import shutil
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from marestail import java
from marestail.context import Context
from marestail.report import Result
from marestail.shell import tail

PITEST = "org.pitest:pitest-maven"
KILLED = {"KILLED", "TIMED_OUT"}
IGNORED = {"NON_VIABLE"}
INSTALL = "declare org.pitest:pitest-maven with the org.pitest:pitest-junit5-plugin dependency in pom.xml; copy templates/java-pitest.xml"


def run_gate(ctx: Context) -> Result:
    started = time.time()
    error = java.require_pom(ctx)
    if error:
        return Result("java.mutation", False, error, [], 0.0)
    if PITEST.split(":")[1] not in java.pom(ctx).read_text(errors="replace"):
        return Result("java.mutation", False, "PIT is not in the pom", [f"{java.rel(ctx, java.pom(ctx))}:1 {INSTALL}"], 0.0)
    scope = ctx.mutation_files("java", ctx.java_root(), (".java",))
    if scope.mode == "error":
        return Result("java.mutation", False, scope.note, [], time.time() - started)
    wanted = None if scope.files is None else set(scope.files)
    targets = [path for path in java.sources(ctx) if (wanted is None or java.rel(ctx, path) in wanted) and not java.mutation_excluded(ctx, java.rel(ctx, path))]
    if not targets:
        return Result.skipped("java.mutation", "no changed Java sources" if wanted is not None else "no Java sources")
    out = ctx.work / "pit"
    shutil.rmtree(out, ignore_errors=True)
    code, output = java.mvn(ctx, command(ctx, targets, out), timeout=int(ctx.java("mutation_timeout", 7200)))
    report = out / "mutations.xml"
    if not report.exists():
        return Result("java.mutation", False, java.maven_hint(code, output) or missing(output, code), tail(output), time.time() - started)
    mutants = [m for m in ET.parse(report).getroot().findall("mutation") if m.get("status") not in IGNORED]
    if not mutants:
        return Result("java.mutation", False, "no mutants were generated", tail(output), time.time() - started)
    findings = [describe(ctx, mutant) for mutant in mutants if mutant.get("status") not in KILLED]
    summary = f"{len(findings)} of {len(mutants)} mutants not killed" if findings else f"all {len(mutants)} mutants killed"
    summary += f" {scope.note}" if scope.note else ""
    return Result("java.mutation", not findings, summary, findings, time.time() - started)


def command(ctx: Context, targets: list[Path], out: Path) -> list[str]:
    classes = [name for path in targets for name in class_globs(java.class_name(ctx, path))]
    packages = sorted({test_glob(java.class_name(ctx, path)) for path in java.tests(ctx)})
    args = [
        "test-compile", f"{PITEST}:mutationCoverage",
        f"-DtargetClasses={','.join(classes)}", "-DoutputFormats=XML", "-DtimestampedReports=false",
        f"-DreportsDirectory={out}", f"-Dthreads={ctx.java('mutation_threads', 2)}",
    ]
    return args + ([f"-DtargetTests={','.join(packages)}"] if packages else [])


def class_globs(name: str | None) -> list[str]:
    return [name, name + "$*"] if name else []


def test_glob(name: str | None) -> str:
    package = (name or "").rpartition(".")[0]
    return f"{package}.*" if package else "*"


def missing(output: str, code: int) -> str:
    lowered = output.lower()
    if "pitest plugin" in lowered or "could not run any tests" in lowered:
        return INSTALL
    if "no mutations found" in lowered:
        return "no mutants were generated"
    return f"PIT produced no report (exit {code})"


def describe(ctx: Context, mutant: ET.Element) -> str:
    owner = (mutant.findtext("mutatedClass") or "").split("$", 1)[0]
    path = java.locate(ctx, "/".join(owner.split(".")[:-1]), mutant.findtext("sourceFile") or "", java.source_roots(ctx))
    where = java.rel(ctx, path) if path is not None else owner
    status = (mutant.get("status") or "?").lower().replace("_", " ")
    return f"{where}:{mutant.findtext('lineNumber') or 0} {mutant.findtext('mutatedMethod')}: {mutant.findtext('description')} {status}"
