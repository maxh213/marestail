import shutil
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from marestail import java
from marestail.context import Context, MutationScope
from marestail.report import Result, elapsed
from marestail.shell import tail

GATE = "java.mutation"
PITEST = "org.pitest:pitest-maven"
KILLED = {"KILLED", "TIMED_OUT"}
IGNORED = {"NON_VIABLE"}
INSTALL = "declare org.pitest:pitest-maven with the org.pitest:pitest-junit5-plugin dependency in pom.xml; copy templates/java-pitest.xml"
STATUS = "status"
NO_MUTANTS = "no mutants were generated"
EMPTY = ""


def run_gate(ctx: Context) -> Result:
    started = time.time()
    blocked = pom_problem(ctx)
    if blocked is not None:
        return blocked
    scope = ctx.mutation_files("java", ctx.java_root(), (".java",))
    if scope.mode == "error":
        return Result(GATE, False, scope.note, [], elapsed(started))
    return run_scope(ctx, scope, started)


def pom_problem(ctx: Context) -> Result | None:
    error = java.require_pom(ctx)
    if error:
        return Result(GATE, False, error, [], 0.0)
    if PITEST.split(":")[1] not in java.read_replaced(java.pom(ctx)):
        return Result(GATE, False, "PIT is not in the pom", [f"{java.rel(ctx, java.pom(ctx))}:1 {INSTALL}"], 0.0)
    return None


def run_scope(ctx: Context, scope: MutationScope, started: float) -> Result:
    wanted = None if scope.files is None else set(scope.files)
    targets = mutation_targets(ctx, wanted)
    if not targets:
        return Result.skipped(GATE, "no changed Java sources" if wanted is not None else "no Java sources")
    return mutate(ctx, targets, scope.note, started)


def mutation_targets(ctx: Context, wanted: set[str] | None) -> list[Path]:
    return [path for path in java.sources(ctx) if targeted(ctx, java.rel(ctx, path), wanted)]


def targeted(ctx: Context, relative: str, wanted: set[str] | None) -> bool:
    return (wanted is None or relative in wanted) and not java.mutation_excluded(ctx, relative)


def mutate(ctx: Context, targets: list[Path], note: str, started: float) -> Result:
    out = ctx.work / "pit"
    shutil.rmtree(out, ignore_errors=True)
    code, output = java.mvn(ctx, command(ctx, targets, out), timeout=int(ctx.java("mutation_timeout", 7200)))
    report = out / "mutations.xml"
    if not report.exists():
        return Result(GATE, False, java.maven_hint(code, output) or missing(output, code), tail(output), elapsed(started))
    mutants = viable(report)
    if not mutants:
        return Result(GATE, False, NO_MUTANTS, tail(output), elapsed(started))
    findings = survivors(ctx, mutants)
    return Result(GATE, not findings, summarise(len(findings), len(mutants), note), findings, elapsed(started))


def viable(report: Path) -> list[ET.Element]:
    return [m for m in ET.parse(report).getroot().findall("mutation") if m.get(STATUS) not in IGNORED]


def survivors(ctx: Context, mutants: list[ET.Element]) -> list[str]:
    return [describe(ctx, mutant) for mutant in mutants if mutant.get(STATUS) not in KILLED]


def summarise(survived: int, total: int, note: str) -> str:
    summary = f"{survived} of {total} mutants not killed" if survived else f"all {total} mutants killed"
    return summary + (f" {note}" if note else "")


def command(ctx: Context, targets: list[Path], out: Path) -> list[str]:
    classes = target_classes(ctx, targets)
    packages = sorted({test_glob(java.class_name(ctx, path)) for path in java.tests(ctx)})
    args = [
        "test-compile",
        f"{PITEST}:mutationCoverage",
        f"-DtargetClasses={','.join(classes)}",
        "-DoutputFormats=XML",
        "-DtimestampedReports=false",
        f"-DreportsDirectory={out}",
        f"-Dthreads={ctx.java('mutation_threads', 2)}",
    ]
    return args + ([f"-DtargetTests={','.join(packages)}"] if packages else [])


def target_classes(ctx: Context, targets: list[Path]) -> list[str]:
    return [name for path in targets for name in class_globs(java.class_name(ctx, path))]


def class_globs(name: str | None) -> list[str]:
    return [name, name + "$*"] if name else []


def test_glob(name: str | None) -> str:
    package = xml_text(name).rpartition(".")[0]
    return f"{package}.*" if package else "*"


def missing(output: str, code: int) -> str:
    lowered = output.lower()
    if "pitest plugin" in lowered or "could not run any tests" in lowered:
        return INSTALL
    if "no mutations found" in lowered:
        return NO_MUTANTS
    return f"PIT produced no report (exit {code})"


def describe(ctx: Context, mutant: ET.Element) -> str:
    where = f"{mutant_location(ctx, mutant)}:{mutant.findtext('lineNumber') or 0}"
    status = (mutant.get(STATUS) or "?").lower().replace("_", " ")
    return f"{where} {mutant.findtext('mutatedMethod')}: {mutant.findtext('description')} {status}"


def mutant_location(ctx: Context, mutant: ET.Element) -> str:
    owner = before_dollar(xml_text(mutant.findtext("mutatedClass")))
    path = java.locate(ctx, java.package_dir(owner), xml_text(mutant.findtext("sourceFile")), java.source_roots(ctx))
    return java.rel(ctx, path) if path is not None else owner


def xml_text(value: str | None) -> str:
    return value if value is not None else EMPTY


def before_dollar(text: str) -> str:
    return text.split("$", 1)[0]
