import importlib
import os
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from marestail import config as config_module
from marestail import context as context_module
from marestail.context import Context, hook_focus, resolve_focus
from marestail.report import Result, elapsed

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


GATE_SPECS: tuple[tuple[str, str, str | None, str], ...] = (
    ("py.tests", FAST, PYTHON, "py_tests"),
    ("py.crap", FAST, PYTHON, "py_crap"),
    ("py.lint", FAST, PYTHON, "py_lint"),
    ("py.deps", FAST, PYTHON, "py_deps"),
    ("py.runtime", FAST, PYTHON, "py_runtime"),
    ("ts.tests", FAST, TS, "ts_tests"),
    ("ts.crap", FAST, TS, "ts_crap"),
    ("ts.lint", FAST, TS, "ts_lint"),
    ("ts.deps", FAST, TS, "ts_deps"),
    ("ex.tests", FAST, ELIXIR, "ex_tests"),
    ("ex.crap", FAST, ELIXIR, "ex_crap"),
    ("ex.lint", FAST, ELIXIR, "ex_lint"),
    ("ex.deps", FAST, ELIXIR, "ex_deps"),
    ("rb.tests", FAST, RUBY, "rb_tests"),
    ("rb.crap", FAST, RUBY, "rb_crap"),
    ("rb.lint", FAST, RUBY, "rb_lint"),
    ("rb.deps", FAST, RUBY, "rb_deps"),
    ("cs.tests", FAST, DOTNET, "cs_tests"),
    ("cs.crap", FAST, DOTNET, "cs_crap"),
    ("cs.lint", FAST, DOTNET, "cs_lint"),
    ("cs.deps", FAST, DOTNET, "cs_deps"),
    ("er.tests", FAST, ERLANG, "er_tests"),
    ("er.crap", FAST, ERLANG, "er_crap"),
    ("er.lint", FAST, ERLANG, "er_lint"),
    ("er.deps", FAST, ERLANG, "er_deps"),
    ("rs.tests", FAST, RUST, "rs_tests"),
    ("rs.crap", FAST, RUST, "rs_crap"),
    ("rs.lint", FAST, RUST, "rs_lint"),
    ("rs.deps", FAST, RUST, "rs_deps"),
    ("java.tests", FAST, JAVA, "java_tests"),
    ("java.crap", FAST, JAVA, "java_crap"),
    ("java.lint", FAST, JAVA, "java_lint"),
    ("java.deps", FAST, JAVA, "java_deps"),
    ("comments", FAST, None, "comments"),
    ("depth", FAST, None, "depth"),
    ("deadcode", FAST, None, "deadcode"),
    ("docs", FAST, "docs", "docs"),
    ("py.mutation", FULL, PYTHON, "py_mutation"),
    ("ts.mutation", FULL, TS, "ts_mutation"),
    ("ex.mutation", FULL, ELIXIR, "ex_mutation"),
    ("rb.mutation", FULL, RUBY, "rb_mutation"),
    ("cs.mutation", FULL, DOTNET, "cs_mutation"),
    ("er.mutation", FULL, ERLANG, "er_mutation"),
    ("rs.mutation", FULL, RUST, "rs_mutation"),
    ("java.mutation", FULL, JAVA, "java_mutation"),
    ("sonar", SONAR, SONAR, "sonar"),
    ("qa", QA, QA, "qa"),
)
RUN_GATE = "run_gate"


def registry() -> list[Gate]:
    return [gate_from(spec) for spec in GATE_SPECS]


def gate_from(spec: tuple[str, str, str | None, str]) -> Gate:
    name, tier, section, module = spec
    return Gate(name, tier, section, load_runner(module))


def load_runner(module: str) -> Runner:
    loaded = importlib.import_module(f"{__package__}.{module}")
    found: Runner = getattr(loaded, RUN_GATE)
    return found


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
        elapsed(started),
    )
