#!/usr/bin/env python3
import inspect
import os
import shutil
import subprocess
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

import harness

root = harness.use_tree()

from marestail import install
from marestail.config import Config
from marestail.context import Context
from marestail.gates import sonar as sonar_gate
from marestail.gates import ts_lint, ts_mutation, ts_tests

TOOLING = ".marestail/tooling"
TOOLING_CONFIGS = ["eslint.config.mjs", "tsconfig.json", "vitest.config.ts", "stryker.config.json", "knip.json"]
MUTATE = ["src/a.ts", "src/b.ts"]
scratch = Path(tempfile.mkdtemp())
os.environ["GROK_HOME"] = str(scratch / "grok")
fake_bin = scratch / "bin"
fake_bin.mkdir()
(fake_bin / "npm").write_text("#!/bin/sh\necho npm out\nexit 0\n")
(fake_bin / "npm").chmod(0o755)
os.environ["PATH"] = f"{fake_bin}{os.pathsep}{os.environ['PATH']}"
supports_hyper = "hyper" in inspect.signature(install.install).parameters
mutation_takes_ctx = len(inspect.signature(ts_mutation.mutation_command).parameters) == 2


def git_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    (path / ".gitignore").write_text("node_modules/\n")
    (path / "CLAUDE.md").write_text("# ours\n")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(path), "-c", "user.name=b", "-c", "user.email=b@b", "commit", "-qm", "init"],
        check=True,
    )
    return path


def fresh_repos(count: int) -> list[Path]:
    return [git_repo(scratch / f"repo-{index}") for index in range(count)]


def install_hyper_fresh(targets: list[Path]) -> None:
    install.install(targets.pop(), hyper=True)


def tooling_ctx(with_tooling: bool) -> Context:
    fixture = scratch / ("with-tooling" if with_tooling else "without-tooling")
    folder = fixture / TOOLING
    folder.mkdir(parents=True)
    ts: dict[str, object] = {"root": ".", "source": "src"}
    if with_tooling:
        ts["tooling"] = TOOLING
        for name in TOOLING_CONFIGS:
            (folder / name).write_text("{}\n")
        (folder / "sonar-project.properties").write_text("sonar.exclusions=perf/**,dist/**\n")
    (fixture / "sonar-project.properties").write_text("sonar.exclusions=perf/**\n")
    return Context(config=Config(root=fixture, raw={"ts": ts}))


def mutation(ctx: Context) -> list[str]:
    return ts_mutation.mutation_command(ctx, MUTATE) if mutation_takes_ctx else ts_mutation.mutation_command(MUTATE)


def command_targets(label: str, ctx: Context) -> None:
    harness.emit(f"ts_lint.eslint_command{label}", harness.measure(lambda: ts_lint.eslint_command(ctx)))
    harness.emit(f"ts_mutation.mutation_command{label}", harness.measure(lambda: mutation(ctx)))
    harness.emit(f"ts_tests.vitest_command{label}", harness.measure(lambda: ts_tests.vitest_command(ctx)))
    harness.emit(f"sonar.scanner_properties{label}", harness.measure(lambda: sonar_gate.scanner_properties(ctx, "k")))
    harness.emit(f"sonar.project_properties{label}", harness.measure(lambda: sonar_gate.project_properties(ctx)))


try:
    if supports_hyper:
        fresh = fresh_repos(harness.VALUES + harness.WARMUP)
        harness.emit("marestail install hyper", harness.measure(lambda: install_hyper_fresh(fresh)))
        again = git_repo(scratch / "repo-again")
        with redirect_stdout(harness.Discard()):
            install.install(again, hyper=True)
        harness.emit("marestail install hyper again", harness.measure(lambda: install.install(again, hyper=True)))
    else:
        harness.absent("marestail install hyper")
        harness.absent("marestail install hyper again")
    command_targets("", tooling_ctx(False))
    command_targets(" tooling", tooling_ctx(True))
finally:
    shutil.rmtree(scratch, ignore_errors=True)
