import math
import random
import zlib
from dataclasses import dataclass
from typing import Any

METRICS = ("p50", "p95")
FLAGGED = ("degraded", "improved")
BASELINE = "baseline"
HEAD = "head"
COMPARED = (BASELINE, HEAD)
CONTROL = "control"
EMPTY_COMPARED = 0
MIN_ROUNDS = 1
P50 = 0
P95 = 1

Record = dict[str, Any]
TreeValues = dict[str, list[float] | None]


@dataclass(frozen=True)
class Policy:
    min_runs: int = 10
    values_per_sample: int = 200
    p95_min_values: int = 200
    threshold_percent: float = 10.0
    min_change: tuple[tuple[str, float], ...] = (("ms", 1.0),)
    bootstrap: int = 500

    def floor(self, unit: str) -> float:
        return dict(self.min_change).get(unit, 0.0)


@dataclass(frozen=True)
class Measurement:
    target: str
    metric: str
    unit: str
    better: str
    pre_marestail: float | None
    baseline: float | None
    head: float | None
    runs: int
    script: str
    values: int = 0
    interval: tuple[float, float] | None = None
    control: float | None = None

    @property
    def column(self) -> str:
        return f"{self.target} {self.metric}"


@dataclass(frozen=True)
class Classified:
    measurement: Measurement
    status: str
    change: float | None


def percentiles(values: list[float]) -> tuple[float, float]:
    return sorted_percentiles(sorted(values))


def sorted_percentiles(ordered: list[float]) -> tuple[float, float]:
    middle = len(ordered) // 2
    median = ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2
    return median, ordered[math.ceil(0.95 * len(ordered)) - 1]


def compile_records(
    records: list[Record], tree_names: list[str], benches: list[str], policy: Policy
) -> tuple[list[Measurement], list[str]]:
    problems = bench_problems(records, tree_names, benches)
    measurements: list[Measurement] = []
    for target, rows in grouped(records).items():
        found, target_problems = target_measurements(target, rows, tree_names, policy)
        measurements += found
        problems += target_problems
    return measurements, problems


def grouped(records: list[Record]) -> dict[str, list[Record]]:
    groups: dict[str, list[Record]] = {}
    for record in records:
        groups.setdefault(record["target"], []).append(record)
    return groups


def bench_problems(records: list[Record], tree_names: list[str], benches: list[str]) -> list[str]:
    problems: list[str] = []
    for bench in benches:
        problems += single_bench_problems(bench, [record for record in records if record["script"] == bench], tree_names)
    return problems


def single_bench_problems(bench: str, own: list[Record], tree_names: list[str]) -> list[str]:
    problems = [f"`{bench}` has no samples on the {tree} tree" for tree in missing_trees(own, tree_names)]
    if len({record["db"] for record in own}) > 1:
        problems.append(f"`{bench}` was run with --db on some trees and without it on others")
    return problems


def missing_trees(own: list[Record], tree_names: list[str]) -> list[str]:
    seen = {record["tree"] for record in own}
    return [tree for tree in tree_names if tree not in seen]


def target_measurements(target: str, rows: list[Record], tree_names: list[str], policy: Policy) -> tuple[list[Measurement], list[str]]:
    problems = variant_problems(target, rows)
    values, tree_problems = values_by_tree(target, rows, tree_names, policy)
    problems += tree_problems
    problems += absence_problems(target, problems, values)
    if problems:
        return [], problems
    return measurements_for(target, rows, values, policy), []


def variant_problems(target: str, rows: list[Record]) -> list[str]:
    return [f"`{target}` reports more than one {key}: {', '.join(sorted(seen))}" for key, seen in variants(rows).items() if len(seen) > 1]


def values_by_tree(target: str, rows: list[Record], tree_names: list[str], policy: Policy) -> tuple[TreeValues, list[str]]:
    values: TreeValues = {}
    problems: list[str] = []
    for tree in tree_names:
        values[tree], problem = tree_values(target, rows_on(rows, tree), tree, policy)
        problems += [problem] if problem else []
    return values, problems


def rows_on(rows: list[Record], tree: str) -> list[Record]:
    return [row for row in rows if row["tree"] == tree]


def tree_values_of(values: TreeValues, tree: str) -> list[float] | None:
    if tree not in values:
        return None
    return values[tree]


def absence_problems(target: str, problems: list[str], values: TreeValues) -> list[str]:
    if problems or tree_values_of(values, BASELINE) is not None or tree_values_of(values, HEAD) is not None:
        return []
    return [f"`{target}` is absent on both the baseline and head trees"]


def variants(rows: list[Record]) -> dict[str, set[str]]:
    return {key: {str(row[key]) for row in rows if key in row} for key in ("unit", "better")}


def measured(row: Record) -> bool:
    return "value" in row or "values" in row


def row_values(row: Record) -> list[float]:
    return list(row["values"]) if "values" in row else [row["value"]]


def tree_values(target: str, rows: list[Record], tree: str, policy: Policy) -> tuple[list[float] | None, str]:
    samples = measured_rows(rows)
    problem = presence_problem(target, bool(samples), any(row.get("absent") for row in rows), tree) or count_problem(
        target, samples, tree, policy
    )
    if problem:
        return None, problem
    return pooled(samples), ""


def measured_rows(rows: list[Record]) -> list[Record]:
    return [row for row in rows if measured(row)]


def pooled(samples: list[Record]) -> list[float] | None:
    return [value for row in samples for value in row_values(row)] or None


def presence_problem(target: str, has_samples: bool, absent: bool, tree: str) -> str:
    if has_samples != absent:
        return ""
    if absent:
        return f"`{target}` has both values and absent on the {tree} tree"
    return f"`{target}` was not measured on the {tree} tree"


def count_problem(target: str, samples: list[Record], tree: str, policy: Policy) -> str:
    if samples and len(samples) < policy.min_runs:
        return f"`{target}` has {len(samples)} samples on the {tree} tree; min_runs is {policy.min_runs}"
    return short_problem(target, short_rows(samples, policy), tree, policy)


def short_rows(samples: list[Record], policy: Policy) -> list[Record]:
    return [row for row in samples if "values" in row and len(row["values"]) < policy.values_per_sample]


def short_problem(target: str, short: list[Record], tree: str, policy: Policy) -> str:
    if not short:
        return ""
    return (
        f"`{target}` has {len(short)} samples on the {tree} tree with fewer than {policy.values_per_sample} values each; "
        f"time at least {policy.values_per_sample} requests per sample"
    )


def measurements_for(target: str, rows: list[Record], values: TreeValues, policy: Policy) -> list[Measurement]:
    first = first_measured(rows)
    stats = tree_stats(values)
    runs = run_count(rows, values)
    compared = min(compared_sizes(values), default=EMPTY_COMPARED)
    intervals = bootstrap(tree_values_of(values, BASELINE), tree_values_of(values, HEAD), policy.bootstrap, zlib.crc32(target.encode()))
    return [
        Measurement(
            target,
            metric,
            first["unit"],
            first["better"],
            stat(stats, "pre-marestail", index),
            stat(stats, BASELINE, index),
            stat(stats, HEAD, index),
            runs,
            first["script"],
            compared,
            intervals[index] if intervals else None,
            stat(stats, CONTROL, index),
        )
        for index, metric in enumerate(METRICS)
    ]


def first_measured(rows: list[Record]) -> Record:
    return next(row for row in rows if measured(row))


def tree_stats(values: TreeValues) -> dict[str, tuple[float, float]]:
    return {tree: percentiles(found) for tree, found in values.items() if found}


def run_count(rows: list[Record], values: TreeValues) -> int:
    return min(tree_runs(rows, tree) for tree, found in values.items() if found)


def tree_runs(rows: list[Record], tree: str) -> int:
    return sum(1 for row in rows if row["tree"] == tree and measured(row))


def compared_sizes(values: TreeValues) -> list[int]:
    return [len(found) for tree, found in values.items() if tree in COMPARED and found]


def stat(stats: dict[str, tuple[float, float]], tree: str, index: int) -> float | None:
    found = stats.get(tree)
    return None if found is None else found[index]


def bootstrap(
    baseline: list[float] | None, head: list[float] | None, rounds: int, seed: int
) -> tuple[tuple[float, float], tuple[float, float]] | None:
    if not baseline or not head or rounds < MIN_ROUNDS:
        return None
    return resampled_intervals(baseline, head, rounds, random.Random(seed))


def resampled_intervals(
    baseline: list[float], head: list[float], rounds: int, rng: random.Random
) -> tuple[tuple[float, float], tuple[float, float]]:
    changes: tuple[list[float], list[float]] = ([], [])
    for _ in range(rounds):
        before = resample(rng, baseline)
        after = resample(rng, head)
        for index, collected in enumerate(changes):
            collected.append(percent_change(before[index], after[index]))
    return interval(changes[P50]), interval(changes[P95])


def resample(rng: random.Random, values: list[float]) -> tuple[float, float]:
    return percentiles(rng.choices(values, k=len(values)))


def interval(changes: list[float]) -> tuple[float, float]:
    ordered = sorted(changes)
    return ordered[int(0.025 * len(ordered))], ordered[math.ceil(0.975 * len(ordered)) - 1]


def classify(measurement: Measurement, policy: Policy) -> Classified:
    if measurement.baseline is None:
        return Classified(measurement, "new", None)
    if measurement.head is None:
        return Classified(measurement, "removed", None)
    change = percent_change(measurement.baseline, measurement.head)
    return Classified(measurement, compared_status(measurement, abs(measurement.head - measurement.baseline), change, policy), change)


def compared_status(measurement: Measurement, difference: float, change: float, policy: Policy) -> str:
    if measurement.metric == "p95" and measurement.values < policy.p95_min_values:
        return "thin"
    if difference < policy.floor(measurement.unit):
        return "unchanged"
    return shift_status(measurement, change, max(policy.threshold_percent, noise(measurement)))


def shift_status(measurement: Measurement, change: float, bar: float) -> str:
    low, high = measurement.interval or (change, change)
    return direction_status(low >= bar, high <= -bar, measurement.better)


def direction_status(rose: bool, fell: bool, better: str) -> str:
    if not (rose or fell):
        return "unchanged"
    return "degraded" if worse(rose, fell, better) else "improved"


def worse(rose: bool, fell: bool, better: str) -> bool:
    return rose if better == "lower" else fell


def noise(measurement: Measurement) -> float:
    if measurement.control is None or measurement.baseline is None:
        return 0.0
    return abs(percent_change(measurement.baseline, measurement.control))


def percent_change(baseline: float, head: float) -> float:
    if baseline == 0:
        return 0.0 if head == 0 else math.copysign(100.0, head)
    return round((head - baseline) * 100 / baseline, 9)


def stale_problems(records: list[Record], fingerprints: dict[str, str]) -> list[str]:
    return [stale_message(script, per_tree) for script, per_tree in sorted(stale_counts(records, fingerprints).items())]


def stale_counts(records: list[Record], fingerprints: dict[str, str]) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for record in records:
        if is_stale(record, fingerprints):
            per_tree = counts.setdefault(record["script"], {})
            per_tree[record["tree"]] = per_tree.get(record["tree"], 0) + 1
    return counts


def is_stale(record: Record, fingerprints: dict[str, str]) -> bool:
    script = record["script"]
    return script in fingerprints and record.get("fingerprint") != fingerprints[script]


def stale_message(script: str, per_tree: dict[str, int]) -> str:
    return (
        f"`{script}` has samples taken before it or a shared file under perf/ last changed "
        f"({', '.join(f'{count} on {tree}' for tree, count in sorted(per_tree.items()))}); take its samples again on every tree"
    )


def audit(classified: list[Classified], existing_columns: list[str], verdict_text: str, verdict: str, benches: list[str]) -> list[str]:
    problems = unmeasured_problems(classified, existing_columns)
    problems += unflagged_problems(classified, verdict_text)
    problems += empty_problems(classified, existing_columns, verdict, benches)
    return problems


def unmeasured_problems(classified: list[Classified], existing_columns: list[str]) -> list[str]:
    measured_columns = {item.measurement.column for item in classified}
    return [
        f"column `{column}` in PERFORMANCE.md was not re-measured; every existing bench runs on every tree every time"
        for column in existing_columns
        if column not in measured_columns
    ]


def unflagged_problems(classified: list[Classified], verdict_text: str) -> list[str]:
    return [
        f"`{target}` is {item.status} ({item.change:+.1f}% {item.measurement.metric}) but the verdict does not name it"
        for target, item in first_unflagged(classified, verdict_text).items()
    ]


def first_unflagged(classified: list[Classified], verdict_text: str) -> dict[str, Classified]:
    unflagged: dict[str, Classified] = {}
    for item in classified:
        if item.status in FLAGGED and item.measurement.target not in verdict_text:
            unflagged.setdefault(item.measurement.target, item)
    return unflagged


def empty_problems(classified: list[Classified], existing_columns: list[str], verdict: str, benches: list[str]) -> list[str]:
    if classified or not expects_measurements(existing_columns, verdict, benches):
        return []
    return ["no measurements were taken; run every perf/bench_* script on every tree through `marestail perf run`"]


def expects_measurements(existing_columns: list[str], verdict: str, benches: list[str]) -> bool:
    return bool(benches or existing_columns or verdict != "PASS")
