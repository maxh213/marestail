#!/usr/bin/env python3
import os
import shutil
import tempfile
from pathlib import Path

import harness

root = harness.use_tree()

from marestail import freeze, graph, install, report
from marestail.context import build
from marestail.gates import py_crap
from marestail.gates import sonar as sonar_gate

config = harness.make_config(root)
ctx = build(config, False)
results = [
    report.Result("py.tests", True, "2367 passed, coverage 100.0%", [], 1.2),
    report.Result("py.crap", True, "1693 functions, 0 above CRAP 4", [], 0.4),
    report.Result("py.lint", True, "ruff clean", [], 0.3),
    report.Result("py.deps", True, "import contracts held", [], 0.2),
    report.Result("py.runtime", True, "skipped: nothing declares the interpreter that ships", [], 0.0),
    report.Result("comments", True, "no comments", [], 0.5),
    report.Result("depth", True, "no depth findings", [], 0.4),
    report.Result("deadcode", True, "no dead code", [], 0.3),
    report.Result("docs", True, "docs match the code", [], 0.2),
]
freeze_paths = [
    "marestail/cli.py",
    "marestail.toml",
    "pyproject.toml",
    "features/000-green-the-repo.feature",
    "qa/000-green-the-repo.md",
    "README.md",
    "perf/bench_cli.py",
    "tests/test_cli.py",
    "sonar-project.properties",
    "guidance/cs.md",
]
grok_home = tempfile.mkdtemp()
os.environ["GROK_HOME"] = grok_home


def install_once() -> None:
    target = Path(tempfile.mkdtemp())
    try:
        install.install(target)
    finally:
        shutil.rmtree(target, ignore_errors=True)


try:
    harness.emit("sonar.scanner_exclusions", harness.measure(lambda: sonar_gate.scanner_exclusions(ctx)))
    harness.emit("freeze.frozen_paths", harness.measure(lambda: freeze.frozen_paths(config, "coder", freeze_paths)))
    harness.emit("graph.root_packages", harness.measure(lambda: graph.root_packages(config.root)))
    harness.emit("report.render", harness.measure(lambda: report.render(results)))
    harness.emit("report.to_json", harness.measure(lambda: report.to_json(results, "all", set())))
    harness.emit("marestail install", harness.measure(install_once))
    if hasattr(py_crap, "coverage_omits"):
        harness.emit("py_crap.coverage_omits", harness.measure(lambda: py_crap.coverage_omits(ctx)))
    else:
        harness.absent("py_crap.coverage_omits")
finally:
    shutil.rmtree(grok_home, ignore_errors=True)
