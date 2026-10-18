import re
from pathlib import Path

import pytest

from marestail import config as config_module
from marestail import gates
from marestail.context import Context
from marestail.gates import Gate
from marestail.report import Result, render_one
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
    ("go", "go"),
]
FAST_KINDS = ["tests", "crap", "lint", "deps"]


def expected_registry() -> list[tuple[str, str, str | None]]:
    fast = [(f"{prefix}.{kind}", "fast", section) for prefix, section in LANGUAGES for kind in FAST_KINDS]
    fast.insert(4, ("py.runtime", "fast", "python"))
    shared = [("comments", "fast", None), ("depth", "fast", None), ("deadcode", "fast", None), ("docs", "fast", "docs")]
    mutation = [(f"{prefix}.mutation", "full", section) for prefix, section in LANGUAGES]
    return [*fast, *shared, *mutation, ("sonar", "sonar", "sonar"), ("qa", "qa", "qa"), ("visual", "qa", "visual")]


def test_registry_order_and_tiers() -> None:
    assert [(gate.name, gate.tier, gate.section) for gate in gates.registry()] == expected_registry()


def test_registry_runners_are_gate_modules() -> None:
    runners = {gate.name: gate.run for gate in gates.registry()}
    expected = {name: f"marestail.gates.{module}" for name, _, _, module in gates.GATE_SPECS}
    assert {name: runner.__module__ for name, runner in runners.items()} == expected
    assert all(runner.__name__ == gates.RUN_GATE for runner in runners.values())


def test_gate_from_and_load_runner() -> None:
    gate = gates.gate_from(("docs", "fast", "docs", "docs"))
    assert (gate.name, gate.tier, gate.section, gate.run.__module__) == ("docs", "fast", "docs", "marestail.gates.docs")
    assert gates.load_runner("qa").__name__ == "run_gate"


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
    assert [gate.name for gate in gates.select("qa", None) if gate.tier != "fast"] == ["qa", "visual"]
    assert len(gates.select("fast", set())) == 41
    assert len(gates.select("all", None)) == 53


def test_select_only() -> None:
    assert [gate.name for gate in gates.select("full", {"qa", "sonar", "py.mutation", "py.tests"})] == ["py.tests", "py.mutation", "sonar"]


def noop(ctx: object) -> Result:
    return Result("x", True, "", [], 0.0)


@pytest.mark.parametrize(
    ("tier", "only", "expected"),
    [("fast", None, True), ("fast", set(), True), ("fast", {"a"}, True), ("fast", {"b"}, False), ("qa", None, False)],
)
def test_wanted_gate(tier: str, only: set[str] | None, expected: bool) -> None:
    assert gates.wanted_gate(Gate("a", tier, None, noop), {"fast"}, only) is expected


def test_gate_runner_signature(tmp_path: Path) -> None:
    assert Gate("a", "fast", None, noop).run(make_context(tmp_path)) == Result("x", True, "", [], 0.0)


def test_gate_focus_soft_skips_hooks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gates, "resolve_focus", lambda config, focus: set(focus))
    hooks: list[int] = []

    def hook(_config: object) -> set[str]:
        hooks.append(1)
        return {"hook"}

    monkeypatch.setattr(gates, "hook_focus", hook)
    config = make_context(tmp_path).config
    assert gates.gate_focus(config, {"a"}, False) == {"a"}
    assert hooks == []
    assert gates.gate_focus(config, {"a"}, True) == {"a", "hook"}
    assert hooks == [1]


def test_run_gates_with_context_runs_gates_with_ctx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "marestail.toml").write_text("[python]\n")
    monkeypatch.chdir(tmp_path)
    seen: list[object] = []

    def run_one(_gate: object, ctx: object) -> Result:
        seen.append(ctx)
        return Result("g", True, "ok", [], 0.0)

    monkeypatch.setattr(gates, "run_one", run_one)
    monkeypatch.setattr(gates, "configured_gates", lambda *args: [Gate("g", "fast", None, noop)])
    _, ctx = gates.run_gates_with_context("fast", False, None)
    assert seen == [ctx]


def test_run_gates_with_context_skips_listed_gates_without_running_them(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "marestail.toml").write_text('[python]\n[gates]\nskip = ["py.mutation"]\n')
    monkeypatch.chdir(tmp_path)
    ran: list[str] = []

    def run_one(gate: Gate, _ctx: object) -> Result:
        ran.append(gate.name)
        return Result(gate.name, True, "ok", [], 0.0)

    monkeypatch.setattr(gates, "run_one", run_one)
    monkeypatch.setattr(
        gates, "configured_gates", lambda *args: [Gate("py.tests", "fast", None, noop), Gate("py.mutation", "full", None, noop)]
    )
    results, _ = gates.run_gates_with_context("full", False, None)
    assert ran == ["py.tests"]
    assert results == [
        Result("py.tests", True, "ok", [], 0.0),
        Result("py.mutation", True, "skipped: listed in [gates] skip in marestail.toml"),
    ]


def config_with(raw: dict[str, object], tmp_path: Path) -> config_module.Config:
    return config_module.Config(root=tmp_path, raw=raw)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({}, set()),
        ({"gates": {}}, set()),
        ({"gates": {"skip": []}}, set()),
        ({"gates": {"skip": ["ts.mutation"]}}, {"ts.mutation"}),
        ({"gates": {"skip": ["ts.mutation", "sonar", "ts.mutation"]}}, {"ts.mutation", "sonar"}),
    ],
)
def test_skip_list(raw: dict[str, object], expected: set[str], tmp_path: Path) -> None:
    assert gates.skip_list(config_with(raw, tmp_path)) == expected


@pytest.mark.parametrize("skip", ["ts.mutation", [1], ["ts.mutation", None], {"ts.mutation": True}])
def test_skip_list_refuses_anything_but_a_list_of_names(skip: object, tmp_path: Path) -> None:
    config = config_with({"gates": {"skip": skip}}, tmp_path)
    with pytest.raises(SystemExit) as raised:
        gates.skip_list(config)
    assert str(raised.value) == '[gates] skip must be a list of gate names, for example skip = ["ts.mutation"]'


def test_skip_list_refuses_names_that_are_not_gates(tmp_path: Path) -> None:
    config = config_with({"gates": {"skip": ["ts.mutations", "sonar", "a.b"]}}, tmp_path)
    with pytest.raises(SystemExit) as raised:
        gates.skip_list(config)
    message = str(raised.value)
    assert message.startswith("[gates] skip names gates that do not exist: a.b, ts.mutations; known gates: comments, cs.crap, ")
    assert message.endswith(", ts.tests, visual")
    assert ", sonar, " in message


def test_known_names_are_every_registered_gate() -> None:
    assert gates.known_names() == {gate.name for gate in gates.registry()}


def test_run_or_skip_runs_a_gate_that_is_not_listed(tmp_path: Path) -> None:
    gate = Gate("x", "fast", None, lambda _ctx: Result("x", False, "bad", ["f"], 0.5))
    assert gates.run_or_skip(gate, make_context(tmp_path), {"y"}).summary.startswith("bad")
    assert gates.run_or_skip(gate, make_context(tmp_path), {"x"}) == Result("x", True, "skipped: listed in [gates] skip in marestail.toml")


def test_run_one_passes_the_context(tmp_path: Path) -> None:
    seen: list[object] = []

    def run(ctx: object) -> Result:
        seen.append(ctx)
        return Result("x", True, "ok", [], 0.25)

    ctx = make_context(tmp_path)
    result = gates.run_one(Gate("x", "fast", None, run), ctx)
    assert seen == [ctx]
    assert (result.gate, result.ok, result.summary) == ("x", True, "ok")


def test_run_one_appends_the_file_level_count(tmp_path: Path) -> None:
    def run(ctx: Context) -> Result:
        ctx.file_level += 2
        return Result("x", False, "1 problems", ["a.py:6 x"], 0.0)

    ctx = make_context(tmp_path, file_level=5)
    result = gates.run_one(Gate("x", "fast", None, run), ctx)
    assert (result.summary, ctx.file_level) == ("1 problems; 2 file-level findings not gated under hyper", 0)


def test_a_mutation_gate_crash_does_not_stop_the_next_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "marestail.toml").write_text("[python]\n")
    monkeypatch.chdir(tmp_path)

    def boom(_ctx: Context) -> Result:
        raise SystemExit("MARESTAIL_JOBS must be a whole number 1 or more, got '0'")

    def skipped(_ctx: Context) -> Result:
        return Result.skipped("ts.mutation", "no changed typescript sources")

    planned = [Gate("py.mutation", "full", "python", boom), Gate("ts.mutation", "full", "ts", skipped)]
    monkeypatch.setattr(gates, "configured_gates", lambda *args: planned)
    results, _ = gates.run_gates_with_context("full", False, None)
    first, second = results

    summary = "py.mutation crashed: SystemExit MARESTAIL_JOBS must be a whole number 1 or more, got '0'"
    assert (first.gate, first.ok, first.summary) == ("py.mutation", False, summary)
    head = render_one(first).splitlines()[0]
    assert re.fullmatch(rf"\[FAIL\] py\.mutation    {re.escape(summary)}  \(\d+\.\ds\)", head)
    assert (second.ok, second.summary) == (True, "skipped: no changed typescript sources")
    assert render_one(second) == "[ok  ] ts.mutation    skipped: no changed typescript sources  (0.0s)"


def capture_run_gates(monkeypatch: pytest.MonkeyPatch) -> list[tuple[object, ...]]:
    seen: list[tuple[object, ...]] = []

    def fake(
        tier: str,
        scope_changed: bool,
        only: set[str] | None,
        focus: set[str] | None = None,
        hard: bool = False,
        hyper: bool = False,
    ) -> tuple[list[Result], str]:
        seen.append((tier, scope_changed, only, focus, hard, hyper))
        return [Result("g", True, "ok", [], 0.0)], "ctx"

    monkeypatch.setattr(gates, "run_gates_with_context", fake)
    return seen


def test_run_gates_forwards_every_argument(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = capture_run_gates(monkeypatch)
    assert gates.run_gates("full", True, {"docs"}, {"src"}, True, True) == [Result("g", True, "ok", [], 0.0)]
    assert seen == [("full", True, {"docs"}, {"src"}, True, True)]


def test_run_gates_defaults_are_unfocused_and_soft(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = capture_run_gates(monkeypatch)
    assert gates.run_gates("fast", False, None) == [Result("g", True, "ok", [], 0.0)]
    assert seen == [("fast", False, None, None, False, False)]
