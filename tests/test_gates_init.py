from pathlib import Path

import pytest

from marestail import gates
from marestail.gates import Gate
from marestail.report import Result
from tests.conftest import make_context

LANGUAGES = [
    ("py", "python"),
    ("ts", "ts"),
    ("ex", "elixir"),
    ("rb", "ruby"),
    ("cs", "dotnet"),
    ("er", "erlang"),
    ("rs", "rust"),
    ("java", "java"),
]
FAST_KINDS = ["tests", "crap", "lint", "deps"]


def expected_registry() -> list[tuple[str, str, str | None]]:
    fast = [(f"{prefix}.{kind}", "fast", section) for prefix, section in LANGUAGES for kind in FAST_KINDS]
    fast.insert(4, ("py.runtime", "fast", "python"))
    shared = [("comments", "fast", None), ("depth", "fast", None), ("deadcode", "fast", None), ("docs", "fast", "docs")]
    mutation = [(f"{prefix}.mutation", "full", section) for prefix, section in LANGUAGES]
    return [*fast, *shared, *mutation, ("sonar", "sonar", "sonar"), ("qa", "qa", "qa")]


def test_registry_order_and_tiers() -> None:
    assert [(gate.name, gate.tier, gate.section) for gate in gates.registry()] == expected_registry()


def test_registry_runners_are_gate_modules() -> None:
    runners = {gate.name: gate.run for gate in gates.registry()}
    assert (runners["qa"].__module__, runners["depth"].__module__) == ("marestail.gates.qa", "marestail.gates.depth")
    assert all(runner.__name__ == "run_gate" for runner in runners.values())


@pytest.mark.parametrize(
    ("tier", "expected"),
    [
        ("fast", {"fast"}),
        ("sonar", {"fast", "sonar"}),
        ("full", {"fast", "sonar", "full"}),
        ("qa", {"fast", "qa"}),
        ("all", {"fast", "sonar", "full", "qa"}),
    ],
)
def test_tiers_for(tier: str, expected: set[str]) -> None:
    assert gates.tiers_for(tier) == expected


def test_tiers_for_unknown() -> None:
    with pytest.raises(KeyError):
        gates.tiers_for("nope")


def test_select_by_tier() -> None:
    assert [gate.name for gate in gates.select("qa", None) if gate.tier != "fast"] == ["qa"]
    assert len(gates.select("fast", set())) == 37
    assert len(gates.select("all", None)) == 47


def test_select_only() -> None:
    assert [gate.name for gate in gates.select("full", {"qa", "sonar", "py.mutation", "py.tests"})] == ["py.tests", "py.mutation", "sonar"]


def noop(ctx: object) -> Result:
    return Result("x", True, "")


@pytest.mark.parametrize(
    ("tier", "only", "expected"),
    [("fast", None, True), ("fast", set(), True), ("fast", {"a"}, True), ("fast", {"b"}, False), ("qa", None, False)],
)
def test_wanted_gate(tier: str, only: set[str] | None, expected: bool) -> None:
    assert gates.wanted_gate(Gate("a", tier, None, noop), {"fast"}, only) is expected


def test_gate_runner_signature(tmp_path: Path) -> None:
    assert Gate("a", "fast", None, noop).run(make_context(tmp_path)) == Result("x", True, "")
