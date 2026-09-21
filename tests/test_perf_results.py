from typing import Any

import pytest

from marestail.perf import results
from marestail.perf.results import Classified, Measurement, Policy

BENCH = "perf/bench_a"
SMALL = Policy(min_runs=2, values_per_sample=2, p95_min_values=2, bootstrap=0)


def rec(tree: str, target: str = "t", **extra: Any) -> dict[str, Any]:
    return {"target": target, "tree": tree, "script": BENCH, "db": False, **extra}


def timed(tree: str, *values: float, **extra: Any) -> dict[str, Any]:
    return rec(tree, unit="ms", better="lower", values=list(values), **extra)


def measure(**fields: Any) -> Measurement:
    base: dict[str, Any] = {
        "target": "t",
        "metric": "p50",
        "unit": "ms",
        "better": "lower",
        "pre_marestail": None,
        "baseline": 100.0,
        "head": 100.0,
        "runs": 10,
        "script": BENCH,
        "values": 500,
    }
    return Measurement(**(base | fields))


def test_policy_floor_uses_the_unit_or_zero() -> None:
    assert Policy().floor("ms") == 1.0
    assert Policy().floor("s") == 0.0


def test_measurement_column_joins_target_and_metric() -> None:
    assert measure(target="get /x", metric="p95").column == "get /x p95"


@pytest.mark.parametrize(
    ("values", "expected"),
    [([3.0, 1.0, 2.0], (2.0, 3.0)), ([4.0, 1.0, 3.0, 2.0], (2.5, 4.0)), ([float(n) for n in range(20, 0, -1)], (10.5, 19.0))],
)
def test_percentiles(values: list[float], expected: tuple[float, float]) -> None:
    assert results.percentiles(values) == expected


def test_compile_records_builds_both_metrics() -> None:
    records = [timed("baseline", 10, 20), timed("baseline", 30, 40), timed("head", 1, 2), timed("head", 3, 4), timed("head", 5, 6)]
    measurements, problems = results.compile_records(records, ["baseline", "head"], [BENCH], SMALL)
    assert problems == []
    assert measurements == [
        Measurement("t", "p50", "ms", "lower", None, 25.0, 3.5, 2, BENCH, 4, None, None),
        Measurement("t", "p95", "ms", "lower", None, 40.0, 6.0, 2, BENCH, 4, None, None),
    ]


def test_compile_records_with_every_tree_and_bootstrap() -> None:
    trees = ["pre-marestail", "baseline", "head", "control"]
    records = [timed(tree, value, value) for tree, value in zip(trees, (5, 10, 20, 11), strict=True) for _ in range(2)]
    policy = Policy(min_runs=2, values_per_sample=2, bootstrap=3)
    measurements, problems = results.compile_records(records, trees, [], policy)
    assert problems == []
    assert [(item.pre_marestail, item.baseline, item.head, item.control) for item in measurements] == [(5, 10, 20, 11)] * 2
    assert [item.interval for item in measurements] == [(100.0, 100.0), (100.0, 100.0)]
    assert [item.values for item in measurements] == [4, 4]


def test_compile_records_single_values_and_new_target() -> None:
    records = [rec("head", unit="s", better="higher", value=2.0) for _ in range(2)] + [rec("baseline", absent=True)]
    measurements, problems = results.compile_records(records, ["baseline", "head"], [], SMALL)
    assert problems == []
    assert measurements[0] == Measurement("t", "p50", "s", "higher", None, None, 2.0, 2, BENCH, 2, None, None)


@pytest.mark.parametrize(
    ("records", "benches", "expected"),
    [
        ([timed("head", 1, 2)] * 2, [BENCH, "perf/bench_b"], ["`perf/bench_a` has no samples on the baseline tree"]),
        (
            [timed("baseline", 1, 2, db=True)] * 2 + [timed("head", 1, 2)] * 2,
            [BENCH],
            ["`perf/bench_a` was run with --db on some trees and without it on others"],
        ),
    ],
)
def test_bench_problems(records: list[dict[str, Any]], benches: list[str], expected: list[str]) -> None:
    problems = results.compile_records(records, ["baseline", "head"], benches, SMALL)[1]
    assert problems[: len(expected)] == expected
    assert ("`perf/bench_b` has no samples on the baseline tree" in problems) == ("perf/bench_b" in benches)


def test_bench_with_no_samples_anywhere() -> None:
    assert results.compile_records([], ["baseline", "head"], ["perf/bench_b"], SMALL) == (
        [],
        ["`perf/bench_b` has no samples on the baseline tree", "`perf/bench_b` has no samples on the head tree"],
    )


BOTH = [timed("baseline", 1, 2)] * 2 + [timed("head", 1, 2)] * 2


@pytest.mark.parametrize(
    ("records", "expected"),
    [
        (
            [*BOTH, rec("head", unit="s", better="higher", value=1.0)],
            ["`t` reports more than one unit: ms, s", "`t` reports more than one better: higher, lower"],
        ),
        ([*BOTH, rec("head", absent=True)], ["`t` has both values and absent on the head tree"]),
        (BOTH[:2], ["`t` was not measured on the head tree"]),
        (BOTH[:3], ["`t` has 1 samples on the head tree; min_runs is 2"]),
        (
            [*BOTH[:2], timed("head", 1), timed("head", 1, 2)],
            ["`t` has 1 samples on the head tree with fewer than 2 values each; time at least 2 requests per sample"],
        ),
        ([rec("baseline", absent=True), rec("head", absent=True)], ["`t` is absent on both the baseline and head trees"]),
    ],
)
def test_target_problems(records: list[dict[str, Any]], expected: list[str]) -> None:
    assert results.compile_records(records, ["baseline", "head"], [], SMALL) == ([], expected)


def test_problems_on_one_target_do_not_hide_others() -> None:
    records = [*BOTH, timed("baseline", 1, 2, target="u")]
    measurements, problems = results.compile_records(records, ["baseline", "head"], [], SMALL)
    assert [item.target for item in measurements] == ["t", "t"]
    assert problems == ["`u` has 1 samples on the baseline tree; min_runs is 2", "`u` was not measured on the head tree"]


def test_bootstrap_needs_both_sides_and_rounds() -> None:
    assert results.bootstrap(None, [1.0], 5, 1) is None
    assert results.bootstrap([1.0], [], 5, 1) is None
    assert results.bootstrap([1.0], [1.0], 0, 1) is None
    assert results.bootstrap([10.0, 10.0], [15.0, 15.0], 1, 1) == ((50.0, 50.0), (50.0, 50.0))
    assert results.bootstrap([10.0, 10.0], [15.0, 15.0], 4, 1) == ((50.0, 50.0), (50.0, 50.0))


def test_bootstrap_is_seeded() -> None:
    first = results.bootstrap([1.0, 5.0, 9.0], [2.0, 6.0, 12.0], 50, 7)
    assert first == results.bootstrap([1.0, 5.0, 9.0], [2.0, 6.0, 12.0], 50, 7)
    assert first != results.bootstrap([1.0, 5.0, 9.0], [2.0, 6.0, 12.0], 50, 8)
    assert first is not None
    assert first[0] != first[1]


def test_absence_problems_when_baseline_has_values() -> None:
    assert results.absence_problems("t", [], {results.BASELINE: [1.0]}) == []
    assert results.absence_problems("t", [], {}) == ["`t` is absent on both the baseline and head trees"]


BOOTSTRAP = Policy(min_runs=1, values_per_sample=1, p95_min_values=1, bootstrap=50)


def test_measurements_for_counts_no_compared_values() -> None:
    found = results.measurements_for("t", [timed("baseline", 1.0)], {"control": [1.0], results.BASELINE: []}, BOOTSTRAP)
    assert [item.values for item in found] == [0, 0]
    assert [item.interval for item in found] == [None, None]


SPREAD: dict[str, list[float] | None] = {
    results.BASELINE: [1.0, 2.0, 3.5, 5.0, 6.5, 8.0, 9.5, 11.0, 12.5, 14.0],
    results.HEAD: [2.0, 3.0, 4.5, 7.0, 8.5, 11.0, 13.5, 15.0, 17.5, 21.0],
}


def intervals_for(target: str) -> list[tuple[float, float] | None]:
    return [item.interval for item in results.measurements_for(target, [timed("baseline", 1.0)], SPREAD, BOOTSTRAP)]


def test_measurements_for_seeds_the_bootstrap_from_the_target_name() -> None:
    assert intervals_for("t") == [(-40.909090909, 170.0), (7.142857143, 84.210526316)]
    assert intervals_for("t") == intervals_for("t")
    assert intervals_for("u") == [(-47.368421053, 178.571428571), (7.142857143, 90.909090909)]


def test_interval_takes_the_middle_ninety_five_percent() -> None:
    assert results.interval([float(n) for n in range(99, -1, -1)]) == (2.0, 97.0)


@pytest.mark.parametrize(
    ("fields", "status", "change"),
    [
        ({"baseline": None}, "new", None),
        ({"head": None}, "removed", None),
        ({"metric": "p95", "values": 199, "head": 200.0}, "thin", 100.0),
        ({"metric": "p95", "values": 200, "head": 200.0}, "degraded", 100.0),
        ({"baseline": 1.0, "head": 1.9}, "unchanged", 90.0),
        ({"baseline": 1.0, "head": 2.0}, "degraded", 100.0),
        ({"head": 110.0}, "degraded", 10.0),
        ({"head": 109.0}, "unchanged", 9.0),
        ({"head": 90.0}, "improved", -10.0),
        ({"head": 91.0}, "unchanged", -9.0),
        ({"head": 120.0, "better": "higher"}, "improved", 20.0),
        ({"head": 80.0, "better": "higher"}, "degraded", -20.0),
        ({"head": 120.0, "control": 130.0}, "unchanged", 20.0),
        ({"head": 120.0, "control": 85.0}, "degraded", 20.0),
        ({"head": 120.0, "interval": (5.0, 30.0)}, "unchanged", 20.0),
        ({"head": 120.0, "interval": (10.0, 30.0)}, "degraded", 20.0),
        ({"head": 80.0, "interval": (-30.0, -10.0)}, "improved", -20.0),
    ],
)
def test_classify(fields: dict[str, Any], status: str, change: float | None) -> None:
    item = measure(**fields)
    assert results.classify(item, Policy()) == Classified(item, status, change)


def test_classify_unit_without_floor() -> None:
    item = measure(unit="rps", baseline=1.0, head=1.5)
    assert results.classify(item, Policy()).status == "degraded"


@pytest.mark.parametrize(
    ("fields", "expected"),
    [({}, 0.0), ({"control": 90.0}, 10.0), ({"control": 90.0, "baseline": None}, 0.0)],
)
def test_noise(fields: dict[str, Any], expected: float) -> None:
    assert results.noise(measure(**fields)) == expected


@pytest.mark.parametrize(
    ("baseline", "head", "expected"),
    [(0.0, 0.0, 0.0), (0.0, 5.0, 100.0), (0.0, -5.0, -100.0), (10.0, 11.0, 10.0), (3.0, 4.0, 33.333333333)],
)
def test_percent_change(baseline: float, head: float, expected: float) -> None:
    assert results.percent_change(baseline, head) == expected


def test_stale_problems_counts_per_script_and_tree() -> None:
    records = [
        rec("head", script="perf/bench_b", fingerprint="old"),
        rec("baseline", script="perf/bench_b"),
        rec("head", script="perf/bench_b", fingerprint="old"),
        rec("head", script="perf/bench_b", fingerprint="new"),
        rec("head", script="perf/bench_a", fingerprint="x"),
        rec("head", script="perf/bench_c", fingerprint="old"),
    ]
    assert results.stale_problems(records, {"perf/bench_b": "new", "perf/bench_a": "y"}) == [
        "`perf/bench_a` has samples taken before it or a shared file under perf/ last changed (1 on head); "
        "take its samples again on every tree",
        "`perf/bench_b` has samples taken before it or a shared file under perf/ last changed (1 on baseline, 2 on head); "
        "take its samples again on every tree",
    ]


def classified(target: str, status: str, change: float | None = 12.34, metric: str = "p50") -> Classified:
    return Classified(measure(target=target, metric=metric), status, change)


def test_audit_reports_unmeasured_columns_and_unflagged_changes() -> None:
    items = [
        classified("a", "degraded"),
        classified("a", "improved", -3.0, "p95"),
        classified("b", "improved", -3.0),
        classified("c", "unchanged"),
        classified("d", "degraded"),
    ]
    assert results.audit(items, ["a p50", "z p95"], "only d", "FAIL", []) == [
        "column `z p95` in PERFORMANCE.md was not re-measured; every existing bench runs on every tree every time",
        "`a` is degraded (+12.3% p50) but the verdict does not name it",
        "`b` is improved (-3.0% p50) but the verdict does not name it",
    ]


EMPTY = "no measurements were taken; run every perf/bench_* script on every tree through `marestail perf run`"


@pytest.mark.parametrize(
    ("existing", "verdict", "benches", "expected"),
    [
        ([], "PASS", [], []),
        ([], "FAIL", [], [EMPTY]),
        ([], "PASS", [BENCH], [EMPTY]),
        (["x p50"], "PASS", [], [results.unmeasured_problems([], ["x p50"])[0], EMPTY]),
    ],
)
def test_audit_without_measurements(existing: list[str], verdict: str, benches: list[str], expected: list[str]) -> None:
    assert results.audit([], existing, "", verdict, benches) == expected


def test_audit_with_measurements_never_reports_empty() -> None:
    assert results.audit([classified("a", "unchanged")], [], "", "FAIL", [BENCH]) == []
