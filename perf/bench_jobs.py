#!/usr/bin/env python3
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

import harness

harness.use_tree()

from marestail import remote, report
from marestail.config import Config
from marestail.context import Context, MutationScope
from marestail.gates import cs_mutation, er_mutation, ex_mutation, java_mutation, py_mutation, rb_mutation, rs_mutation, ts_mutation

try:
    from marestail import jobs as jobs_module
except ImportError:
    jobs_module = None

ENV = "MARESTAIL_JOBS" if jobs_module is None else jobs_module.ENV
SURVIVORS = 70
PENDING = 5
CAP = "100000"
THREE = "3"
FINDING = "src/a.rs:9 run survived"
SUBJECTS = ["Admin::Boss*", "Named*", "User*"]
PATTERNS = ["marestail.jobs", "marestail.gates.py_mutation"]
ELIXIR_FILES = ["lib/app.ex"]
CS_TARGETS = ["App/A.cs", "App/B.cs", "App/C.cs", "App/D.cs"]
Row = Callable[[], Any] | None

scratch = Path(tempfile.mkdtemp(prefix="marestail-perf-jobs-"))
started = time.time()


def apply_env(value: str | None) -> None:
    if value is None:
        os.environ.pop(ENV, None)
        return
    os.environ[ENV] = value


def emit(target: str, fn: Row, env: str | None = None) -> None:
    apply_env(env)
    if not callable(fn):
        harness.absent(target)
        return
    harness.emit(target, harness.measure(fn))


def config(raw: dict[str, Any]) -> Config:
    return Config(root=scratch, raw=raw)


def context(raw: dict[str, Any]) -> Context:
    return Context(config=config(raw))


def only_jobs(fn: Row) -> Row:
    return fn if jobs_module is not None else None


def bound(module: Any, name: str) -> Row:
    found = getattr(module, name, None)
    return found if callable(found) else None


def fake_mutation(
    ctx: Context,
    gate: str,
    build: Callable[[int], list[str]],
    cwd: Path,
    workers: int,
    env: dict[str, str] | None = None,
    timeout: int | None = 7200,
    pull: tuple[str, ...] = (),
) -> remote.Outcome:
    del ctx, gate, cwd, env, timeout, pull
    build(workers)
    return remote.Outcome(1, "", "")


def reject_parse() -> None:
    try:
        jobs_module.parse("four", jobs_module.ENV)
    except SystemExit:
        return


def run_pool() -> None:
    er_mutation.run_mutants(job, [dict(mutant) for mutant in mutants])


def emit_jobs() -> None:
    emit("jobs.parse", only_jobs(lambda: jobs_module.parse(3, "[run] jobs")))
    emit("jobs.parse text", only_jobs(lambda: jobs_module.parse("03", jobs_module.ENV)))
    emit("jobs.parse reject", None if jobs_module is None else reject_parse)
    emit("jobs._as_int", only_jobs(lambda: jobs_module._as_int(3)))
    emit("jobs._digits", only_jobs(lambda: jobs_module._digits("03")))
    emit("jobs.run_jobs", only_jobs(lambda: jobs_module.run_jobs(empty)))
    emit("jobs.run_jobs file", only_jobs(lambda: jobs_module.run_jobs(file_jobs)))
    emit("jobs.run_jobs env", only_jobs(lambda: jobs_module.run_jobs(file_jobs)), CAP)
    emit("jobs.run_jobs empty", only_jobs(lambda: jobs_module.run_jobs(file_jobs)), "")
    emit("jobs.worker_args", only_jobs(lambda: jobs_module.worker_args(empty, "--concurrency")))
    emit("jobs.worker_args env", only_jobs(lambda: jobs_module.worker_args(file_jobs, "--jobs")), THREE)
    emit("jobs.pick", only_jobs(lambda: jobs_module.pick(empty, "python", "mutation_workers", 4)))
    emit("jobs.pick language", only_jobs(lambda: jobs_module.pick(py_ctx.config, "python", "mutation_workers", 4)))
    emit("jobs.pick file", only_jobs(lambda: jobs_module.pick(file_jobs, "python", "mutation_workers", 4)))
    emit("jobs.pick env", only_jobs(lambda: jobs_module.pick(py_ctx.config, "python", "mutation_workers", 4)), THREE)
    emit("jobs.pick zero", only_jobs(lambda: jobs_module.pick(zero_key, "elixir", "muex_concurrency", 4)))
    emit("jobs._from_env", only_jobs(lambda: jobs_module._from_env()))
    emit("jobs._from_env set", only_jobs(lambda: jobs_module._from_env()), CAP)
    emit("jobs._from_file", only_jobs(lambda: jobs_module._from_file(empty)))
    emit("jobs._from_file set", only_jobs(lambda: jobs_module._from_file(file_jobs)))
    emit("jobs._reject_bad_file", only_jobs(lambda: jobs_module._reject_bad_file(file_jobs)))
    emit("jobs._present", only_jobs(lambda: jobs_module._present(py_ctx.config, "python", "mutation_workers")))
    emit("jobs._present miss", only_jobs(lambda: jobs_module._present(empty, "python", "mutation_workers")))
    emit("jobs._capped", only_jobs(lambda: jobs_module._capped(100000)))


def emit_commands() -> None:
    emit("py_mutation.mutate", lambda: py_mutation.mutate(plain, PATTERNS, "", started))
    emit("py_mutation.mutate jobs", lambda: py_mutation.mutate(py_ctx, PATTERNS, "", started), THREE)
    emit("ex_mutation.with_muex", lambda: ex_mutation.with_muex(plain, scratch, scope, ELIXIR_FILES, started))
    emit("ex_mutation.with_muex jobs", lambda: ex_mutation.with_muex(ex_ctx, scratch, scope, ELIXIR_FILES, started), THREE)
    emit("java_mutation.mutate", lambda: java_mutation.mutate(plain, java_targets, "", started))
    emit("java_mutation.mutate jobs", lambda: java_mutation.mutate(java_ctx, java_targets, "", started), THREE)
    emit("rb_mutation.mutate", lambda: rb_mutation.mutate(plain, SUBJECTS, "", started))
    emit("rb_mutation.mutate jobs", lambda: rb_mutation.mutate(plain, SUBJECTS, "", started), THREE)
    emit("rb_mutation.verdict", lambda: rb_mutation.verdict(SURVIVORS, failures, "", started))
    emit("rb_mutation.sorted_findings", None if sort_rb is None else lambda: sort_rb(failures))
    emit("cs_mutation.command", lambda: cs_mutation.command(plain, product, tests, out, CS_TARGETS))
    emit("cs_mutation.command jobs", lambda: cs_mutation.command(plain, product, tests, out, CS_TARGETS), THREE)
    emit("ts_mutation.local_concurrency", lambda: ts_mutation.local_concurrency(plain))
    emit("ts_mutation.local_concurrency key", lambda: ts_mutation.local_concurrency(ts_key))
    emit("ts_mutation.local_concurrency file", lambda: ts_mutation.local_concurrency(ts_file))
    emit("ts_mutation.local_concurrency env", lambda: ts_mutation.local_concurrency(ts_both), THREE)
    emit("rs_mutation.command", lambda: rs_mutation.command(plain, []))
    emit("rs_mutation.command jobs", lambda: rs_mutation.command(plain, []), THREE)
    emit("rs_mutation.command in place", lambda: rs_mutation.command(in_place, []))
    emit("rs_mutation.worker_flag", None if flag is None else lambda: flag(plain, []))
    emit("rs_mutation.worker_flag in place", None if flag is None else lambda: flag(in_place, ["--in-place"]))
    emit("rs_mutation.judged", lambda: rs_mutation.judged(plain, outcomes, started))
    emit("rs_mutation.sorted_findings", None if sort_rs is None else lambda: sort_rs(plain, outcomes))
    emit("report.finding_order", None if order is None else lambda: order(FINDING))


def emit_erlang() -> None:
    emit("er_mutation.run_mutants", run_pool)
    emit("er_mutation.run_mutants jobs", run_pool, THREE)
    emit("er_mutation.mutant_timeout", None if timeout_of is None else lambda: timeout_of(job))
    emit("er_mutation.pool_size", None if pool is None else lambda: pool(job))
    emit("er_mutation.pool_size jobs", None if pool is None else lambda: pool(job), THREE)
    emit("er_mutation.unskipped", None if skip is None else lambda: skip(mutants))


empty = config({})
file_jobs = config({"run": {"jobs": 3}})
zero_key = config({"elixir": {"muex_concurrency": 0}, "run": {"jobs": 3}})
plain = context({})
py_ctx = context({"python": {"mutation_workers": 8}, "run": {"jobs": 3}})
ex_ctx = context({"elixir": {"muex_concurrency": 8}, "run": {"jobs": 3}})
java_ctx = context({"java": {"mutation_threads": 8}, "run": {"jobs": 3}})
ts_key = context({"ts": {"mutation_concurrency": 4}})
ts_file = context({"run": {"jobs": 3}})
ts_both = context({"ts": {"mutation_concurrency": 4}, "run": {"jobs": 3}})
in_place = context({"rust": {"mutation_args": ["--in-place"]}})
scope = MutationScope("full")
job = er_mutation.Job(plain, started, scope, [], [], baseline_seconds=1.5)
mutants = [{"id": index, "status": "pending"} for index in range(PENDING)]
mutants.append({"id": "skipped", "status": "skipped"})
failures = [(f"src/{'a' if index % 2 == 0 else 'b'}.rb", (index % 40) + 1, "User", "evil") for index in range(SURVIVORS)]
outcomes = [
    {
        "summary": "MissedMutant",
        "scenario": {
            "Mutant": {
                "file": "src/a.rs" if index % 2 == 0 else "src/b.rs",
                "span": {"start": {"line": (index % 40) + 1}},
                "function": {"function_name": "run"},
                "name": "run: replace + with -",
            }
        },
    }
    for index in range(SURVIVORS)
]
product = scratch / "App" / "Product.csproj"
tests = scratch / "App.Tests" / "App.Tests.csproj"
out = scratch / "stryker"
java_targets = [scratch / "src" / "main" / "java" / "App.java"]
sort_rb = bound(rb_mutation, "sorted_findings")
sort_rs = bound(rs_mutation, "sorted_findings")
flag = bound(rs_mutation, "worker_flag")
order = bound(report, "finding_order")
timeout_of = bound(er_mutation, "mutant_timeout")
pool = bound(er_mutation, "pool_size")
skip = bound(er_mutation, "unskipped")
remote.run_mutation = fake_mutation
er_mutation.check_mutant = lambda er_job, mutant, per_mutant_timeout: None
ex_mutation.muex_missing = lambda folder: None
rb_mutation.run = lambda *args, **kwargs: (1, "")

try:
    emit_jobs()
    emit_commands()
    emit_erlang()
finally:
    apply_env(None)
    shutil.rmtree(scratch, ignore_errors=True)
