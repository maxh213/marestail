#!/usr/bin/env python3
import json
import shutil
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import harness

harness.use_tree()

from marestail import report
from marestail.config import Config
from marestail.context import Context, MutationScope
from marestail.gates import cs_mutation, ex_mutation, java_mutation, py_mutation, ts_tests
from marestail.tui import app, collect
from marestail.tui.model import RepoState

MUTANTS = 2000
SPECS = 300
BEDS = 6
FANOUT = 5
REPOS = 100
LOG_RUNS = 40
OUTPUT_LINES = 400

scratch = Path(tempfile.mkdtemp())


def status_of(index: int, killed: str, timeout: str, survived: str) -> str:
    if index % 7 == 0:
        return timeout
    if index % 97 == 0:
        return survived
    return killed


def make_context(raw: dict[str, Any]) -> Context:
    project = scratch / "project"
    project.mkdir(exist_ok=True)
    return Context(config=Config(root=project, raw=raw))


def strict_raw(section: str) -> dict[str, Any]:
    return {section: {"mutation_timeouts": "fail"}}


def py_statuses() -> list[tuple[str, str]]:
    return [(f"marestail.mod.x_f{index}__mutmut_1", status_of(index, "killed", "timeout", "survived")) for index in range(MUTANTS)]


def ex_mutations() -> list[dict[str, Any]]:
    return [
        {
            "status": status_of(index, "killed", "timeout", "survived"),
            "location": {"file": f"lib/m{index % 20}.ex", "line": index % 300 + 1},
            "mutator": "Elixir.Muex.Mutator.Arithmetic",
            "description": "plus to minus",
        }
        for index in range(MUTANTS)
    ]


def cs_mutants() -> list[tuple[str, dict[str, Any]]]:
    return [
        (
            f"App/M{index % 20}.cs",
            {
                "status": status_of(index, "Killed", "Timeout", "Survived"),
                "mutatorName": "Equality",
                "replacement": "a != b",
                "location": {"start": {"line": index % 300 + 1, "column": 4}},
            },
        )
        for index in range(MUTANTS)
    ]


def java_mutants() -> list[ET.Element]:
    found = []
    for index in range(MUTANTS):
        mutation = ET.Element("mutation", {"status": status_of(index, "KILLED", "TIMED_OUT", "SURVIVED"), "detected": "true"})
        for tag, text in (
            ("sourceFile", f"M{index % 20}.java"),
            ("mutatedClass", f"app.M{index % 20}"),
            ("mutatedMethod", "run"),
            ("lineNumber", str(index % 300 + 1)),
            ("description", "replaced int return with 0"),
        ):
            ET.SubElement(mutation, tag).text = text
        found.append(mutation)
    return found


def playwright_report() -> dict[str, Any]:
    def spec(index: int) -> dict[str, Any]:
        ok = index % 10 != 0
        result = {"status": "passed" if ok else "failed", "error": {} if ok else {"message": f"expected {index}\nmore", "location": {"file": "e2e/a.spec.ts", "line": index}}}
        return {"title": f"case {index}", "ok": ok, "file": "e2e/a.spec.ts", "line": index, "tests": [{"results": [result]}]}

    inner = [{"title": f"group {group}", "specs": [spec(group * 30 + index) for index in range(30)], "suites": []} for group in range(SPECS // 30)]
    return {"suites": [{"title": "root", "specs": [], "suites": inner}], "errors": ["boom", {"message": "global setup failed"}], "stats": {"expected": SPECS}}


def vitest_output() -> str:
    lines = [f" ✓ src/m{index}.test.ts (12 tests) 40ms" for index in range(OUTPUT_LINES)]
    return "\n".join([*lines, "", " Test Files  400 passed (400)", "      Tests  4800 passed (4800)"])


def make_beds() -> list[Path]:
    root = scratch / "workspace"
    for bed in range(BEDS):
        (root / f"bed{bed}" / ".marestail" / "runs").mkdir(parents=True)
        (root / f"bed{bed}" / "node_modules" / "pkg").mkdir(parents=True)
        for child in range(FANOUT):
            (root / f"bed{bed}" / f"src{child}" / "lib" / "deep").mkdir(parents=True)
    (root / "group" / "nested" / ".marestail").mkdir(parents=True)
    return [root]


def make_runs() -> Path:
    runs = scratch / "runs"
    for index in range(LOG_RUNS):
        (runs / f"task-{index}").mkdir(parents=True)
        (runs / f"task-{index}" / collect.PIPELINE_LOG).write_text("log\n")
        (runs / f"overnight-{index}.log").write_text("log\n")
    return runs


def repos() -> list[RepoState]:
    found = []
    for index in range(REPOS):
        activity = f"{collect.REMOTE_MARK} stryker on marestail-mutation" if index % 3 == 0 else ("py.tests" if index % 3 == 1 else None)
        found.append(RepoState(name=f"r{index}", root=scratch / f"r{index}", branch="b", head="h", task=None, log_path=None, gate_activity=activity))
    return found


SSH_JOB = "ssh -tt -q -i /k -o HostKeyAlias=marestail-mutation -o BatchMode=yes max@1.2.3.4 echo $$ > /var/lib/marestail/busy.d/abc; flock /var/lib/marestail/job.lock sg docker -c docker exec -w /r mt mutmut run".split()
RSYNC = "rsync -a --delete -e ssh -o HostKeyAlias=marestail-mutation /r/ max@1.2.3.4:/r/".split()
GCLOUD_START = "gcloud compute instances start marestail-mutation --project=p --zone=z --quiet".split()
LOCAL = "/repo/.venv/bin/python /repo/.venv/bin/mutmut run --max-children 4".split()


def label_targets() -> None:
    for name, tokens, names in (
        ("ssh job", SSH_JOB, ["ssh"]),
        ("rsync", RSYNC, ["rsync"]),
        ("gcloud start", GCLOUD_START, ["gcloud"]),
        ("local", LOCAL, ["python", "mutmut"]),
    ):
        harness.emit(f"collect.gate_label {name}", harness.measure(lambda tokens=tokens, names=names: collect.gate_label(tokens, names)))


def mutation_targets() -> None:
    started = time.time()
    plain = make_context({})
    summary = getattr(report, "mutation_summary", None)
    if summary is None:
        harness.absent("report.mutation_summary")
    else:
        harness.emit("report.mutation_summary", harness.measure(lambda: summary(3, MUTANTS, "(scoped)", 12)))
    statuses = py_statuses()
    harness.emit("py_mutation.judged timeouts", harness.measure(lambda: py_mutation.judged(plain, statuses, "", started)))
    strict_py = make_context(strict_raw("python"))
    harness.emit("py_mutation.judged timeouts fail", harness.measure(lambda: py_mutation.judged(strict_py, statuses, "", started)))
    mutations = ex_mutations()
    scope = MutationScope(mode="all")
    harness.emit("ex_mutation.report_result timeouts", harness.measure(lambda: ex_mutation.report_result(plain, scope, mutations, "", started)))
    cs = cs_mutants()
    harness.emit("cs_mutation.verdict timeouts", harness.measure(lambda: cs_mutation.verdict(plain, cs, "", ("", started))))
    java = java_mutants()
    harness.emit("java_mutation.judged", harness.measure(lambda: java_mutation.judged(plain, java, "", started)))


def ts_targets() -> None:
    ctx = make_context({"ts": {"root": ".", "runner": "playwright"}})
    ctx.work.mkdir(parents=True, exist_ok=True)
    (ctx.work / ts_tests.JEST_RESULTS).write_text(json.dumps(playwright_report()))
    output = vitest_output()
    harness.emit("ts_tests.playwright_failures", harness.measure(lambda: ts_tests.playwright_failures(ctx)))
    harness.emit("ts_tests.count_tests vitest", harness.measure(lambda: ts_tests.count_tests(output)))
    harness.emit("ts_tests.count_tests playwright", harness.measure(lambda: ts_tests.count_tests(output, ctx, "playwright")))


def tui_targets() -> None:
    roots = make_beds()
    beds = collect.discover(roots)
    runs = make_runs()
    states = repos()
    harness.emit("collect.discover", harness.measure(lambda: collect.discover(roots)))
    harness.emit("collect.display_name", harness.measure(lambda: [collect.display_name(bed, roots) for bed in beds]))
    harness.emit("collect.run_logs", harness.measure(lambda: collect.run_logs(runs)))
    label_targets()
    harness.emit("app.on_vm", harness.measure(lambda: app.on_vm(states)))


try:
    mutation_targets()
    ts_targets()
    tui_targets()
finally:
    shutil.rmtree(scratch, ignore_errors=True)
