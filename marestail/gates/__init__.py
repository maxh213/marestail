from collections.abc import Callable
from dataclasses import dataclass

from marestail.context import Context
from marestail.report import Result

FAST = "fast"
SONAR = "sonar"
FULL = "full"
QA = "qa"

Runner = Callable[[Context], Result]


@dataclass(frozen=True)
class Gate:
    name: str
    tier: str
    section: str | None
    run: Runner


def registry() -> list[Gate]:
    from marestail.gates import comments, depth, py_crap, py_deps, py_lint, py_mutation, py_tests, qa, sonar
    from marestail.gates import ts_crap, ts_deps, ts_lint, ts_mutation, ts_tests

    return [
        Gate("py.tests", FAST, "python", py_tests.run_gate),
        Gate("py.crap", FAST, "python", py_crap.run_gate),
        Gate("py.lint", FAST, "python", py_lint.run_gate),
        Gate("py.deps", FAST, "python", py_deps.run_gate),
        Gate("ts.tests", FAST, "ts", ts_tests.run_gate),
        Gate("ts.crap", FAST, "ts", ts_crap.run_gate),
        Gate("ts.lint", FAST, "ts", ts_lint.run_gate),
        Gate("ts.deps", FAST, "ts", ts_deps.run_gate),
        Gate("comments", FAST, None, comments.run_gate),
        Gate("depth", FAST, None, depth.run_gate),
        Gate("py.mutation", FULL, "python", py_mutation.run_gate),
        Gate("ts.mutation", FULL, "ts", ts_mutation.run_gate),
        Gate("sonar", SONAR, "sonar", sonar.run_gate),
        Gate("qa", QA, "qa", qa.run_gate),
    ]


def select(tier: str, only: set[str] | None) -> list[Gate]:
    wanted = tiers_for(tier)
    return [gate for gate in registry() if gate.tier in wanted and (not only or gate.name in only)]


def tiers_for(tier: str) -> set[str]:
    return {FAST: {FAST}, SONAR: {FAST, SONAR}, FULL: {FAST, SONAR, FULL}, QA: {FAST, QA}, "all": {FAST, SONAR, FULL, QA}}[tier]
