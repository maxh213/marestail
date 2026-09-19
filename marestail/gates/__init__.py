import os
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from marestail import config as config_module
from marestail import context as context_module
from marestail.context import Context, hook_focus, resolve_focus
from marestail.report import Result

FAST = "fast"
SONAR = "sonar"
FULL = "full"
QA = "qa"
PYTHON = "python"
TS = "ts"
ELIXIR = "elixir"
RUBY = "ruby"
DOTNET = "dotnet"
ERLANG = "erlang"
RUST = "rust"
JAVA = "java"

Runner = Callable[[Context], Result]


@dataclass(frozen=True)
class Gate:
    name: str
    tier: str
    section: str | None
    run: Runner


def registry() -> list[Gate]:
    from marestail.gates import (
        comments,
        cs_crap,
        cs_deps,
        cs_lint,
        cs_mutation,
        cs_tests,
        deadcode,
        depth,
        docs,
        er_crap,
        er_deps,
        er_lint,
        er_mutation,
        er_tests,
        ex_crap,
        ex_deps,
        ex_lint,
        ex_mutation,
        ex_tests,
        java_crap,
        java_deps,
        java_lint,
        java_mutation,
        java_tests,
        py_crap,
        py_deps,
        py_lint,
        py_mutation,
        py_runtime,
        py_tests,
        qa,
        rb_crap,
        rb_deps,
        rb_lint,
        rb_mutation,
        rb_tests,
        rs_crap,
        rs_deps,
        rs_lint,
        rs_mutation,
        rs_tests,
        sonar,
        ts_crap,
        ts_deps,
        ts_lint,
        ts_mutation,
        ts_tests,
    )

    return [
        Gate("py.tests", FAST, PYTHON, py_tests.run_gate),
        Gate("py.crap", FAST, PYTHON, py_crap.run_gate),
        Gate("py.lint", FAST, PYTHON, py_lint.run_gate),
        Gate("py.deps", FAST, PYTHON, py_deps.run_gate),
        Gate("py.runtime", FAST, PYTHON, py_runtime.run_gate),
        Gate("ts.tests", FAST, TS, ts_tests.run_gate),
        Gate("ts.crap", FAST, TS, ts_crap.run_gate),
        Gate("ts.lint", FAST, TS, ts_lint.run_gate),
        Gate("ts.deps", FAST, TS, ts_deps.run_gate),
        Gate("ex.tests", FAST, ELIXIR, ex_tests.run_gate),
        Gate("ex.crap", FAST, ELIXIR, ex_crap.run_gate),
        Gate("ex.lint", FAST, ELIXIR, ex_lint.run_gate),
        Gate("ex.deps", FAST, ELIXIR, ex_deps.run_gate),
        Gate("rb.tests", FAST, RUBY, rb_tests.run_gate),
        Gate("rb.crap", FAST, RUBY, rb_crap.run_gate),
        Gate("rb.lint", FAST, RUBY, rb_lint.run_gate),
        Gate("rb.deps", FAST, RUBY, rb_deps.run_gate),
        Gate("cs.tests", FAST, DOTNET, cs_tests.run_gate),
        Gate("cs.crap", FAST, DOTNET, cs_crap.run_gate),
        Gate("cs.lint", FAST, DOTNET, cs_lint.run_gate),
        Gate("cs.deps", FAST, DOTNET, cs_deps.run_gate),
        Gate("er.tests", FAST, ERLANG, er_tests.run_gate),
        Gate("er.crap", FAST, ERLANG, er_crap.run_gate),
        Gate("er.lint", FAST, ERLANG, er_lint.run_gate),
        Gate("er.deps", FAST, ERLANG, er_deps.run_gate),
        Gate("rs.tests", FAST, RUST, rs_tests.run_gate),
        Gate("rs.crap", FAST, RUST, rs_crap.run_gate),
        Gate("rs.lint", FAST, RUST, rs_lint.run_gate),
        Gate("rs.deps", FAST, RUST, rs_deps.run_gate),
        Gate("java.tests", FAST, JAVA, java_tests.run_gate),
        Gate("java.crap", FAST, JAVA, java_crap.run_gate),
        Gate("java.lint", FAST, JAVA, java_lint.run_gate),
        Gate("java.deps", FAST, JAVA, java_deps.run_gate),
        Gate("comments", FAST, None, comments.run_gate),
        Gate("depth", FAST, None, depth.run_gate),
        Gate("deadcode", FAST, None, deadcode.run_gate),
        Gate("docs", FAST, "docs", docs.run_gate),
        Gate("py.mutation", FULL, PYTHON, py_mutation.run_gate),
        Gate("ts.mutation", FULL, TS, ts_mutation.run_gate),
        Gate("ex.mutation", FULL, ELIXIR, ex_mutation.run_gate),
        Gate("rb.mutation", FULL, RUBY, rb_mutation.run_gate),
        Gate("cs.mutation", FULL, DOTNET, cs_mutation.run_gate),
        Gate("er.mutation", FULL, ERLANG, er_mutation.run_gate),
        Gate("rs.mutation", FULL, RUST, rs_mutation.run_gate),
        Gate("java.mutation", FULL, JAVA, java_mutation.run_gate),
        Gate("sonar", SONAR, SONAR, sonar.run_gate),
        Gate("qa", QA, QA, qa.run_gate),
    ]


def select(tier: str, only: set[str] | None) -> list[Gate]:
    wanted = tiers_for(tier)
    return [gate for gate in registry() if wanted_gate(gate, wanted, only)]


def wanted_gate(gate: Gate, wanted: set[str], only: set[str] | None) -> bool:
    return gate.tier in wanted and (not only or gate.name in only)


def tiers_for(tier: str) -> set[str]:
    return {FAST: {FAST}, SONAR: {FAST, SONAR}, FULL: {FAST, SONAR, FULL}, QA: {FAST, QA}, "all": {FAST, SONAR, FULL, QA}}[tier]


def run_gates(tier: str, scope_changed: bool, only: set[str] | None, focus: set[str] | None = None, hard: bool = False) -> list[Result]:
    results, _ = run_gates_with_context(tier, scope_changed, only, focus, hard)
    return results


def run_gates_with_context(
    tier: str, scope_changed: bool, only: set[str] | None, focus: set[str] | None = None, hard: bool = False
) -> tuple[list[Result], Context]:
    os.environ["MARESTAIL_GATE_ACTIVE"] = "true"
    config = config_module.load(Path.cwd())
    ctx = context_module.build(config, scope_changed, gate_focus(config, focus or set(), hard), hard)
    return [run_one(gate, ctx) for gate in configured_gates(config, tier, only)], ctx


def gate_focus(config: config_module.Config, focus: set[str], hard: bool) -> set[str]:
    focused = resolve_focus(config, focus)
    return focused | hook_focus(config) if hard else focused


def configured_gates(config: config_module.Config, tier: str, only: set[str] | None) -> list[Gate]:
    return [gate for gate in select(tier, only) if not gate.section or config.section(gate.section) is not None]


def run_one(gate: Gate, ctx: Context) -> Result:
    started = time.time()
    try:
        return gate.run(ctx)
    except BaseException as error:
        if not isinstance(error, Exception | SystemExit):
            raise
        return crashed(gate, error, started)


def crashed(gate: Gate, error: BaseException, started: float) -> Result:
    detail = " ".join(str(error).split())[:200]
    return Result(
        gate.name,
        False,
        f"{gate.name} crashed: {type(error).__name__} {detail}",
        traceback.format_exc().strip().splitlines()[-6:],
        time.time() - started,
    )
