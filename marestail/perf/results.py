import math
import random
import zlib
from dataclasses import dataclass

METRICS = ("p50", "p95")
FLAGGED = ("degraded", "improved")
COMPARED = ("baseline", "head")
CONTROL = "control"


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


def compile_records(records: list[dict], tree_names: list[str], benches: list[str], policy: Policy) -> tuple[list[Measurement], list[str]]:
    problems = bench_problems(records, tree_names, benches)
    measurements: list[Measurement] = []
    for target, rows in grouped(records).items():
        found, target_problems = target_measurements(target, rows, tree_names, policy)
        measurements += found
        problems += target_problems
    return measurements, problems


def grouped(records: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for record in records:
        groups.setdefault(record["target"], []).append(record)
    return groups


def bench_problems(records: list[dict], tree_names: list[str], benches: list[str]) -> list[str]:
    problems = []
    for bench in benches:
        own = [record for record in records if record["script"] == bench]
        problems += [f"`{bench}` has no samples on the {tree} tree" for tree in tree_names if not any(record["tree"] == tree for record in own)]
        if len({record["db"] for record in own}) > 1:
            problems.append(f"`{bench}` was run with --db on some trees and without it on others")
    return problems


def target_measurements(target: str, rows: list[dict], tree_names: list[str], policy: Policy) -> tuple[list[Measurement], list[str]]:
    problems = [f"`{target}` reports more than one {key}: {', '.join(sorted(seen))}" for key, seen in variants(rows).items() if len(seen) > 1]
    values: dict[str, list[float] | None] = {}
    for tree in tree_names:
        values[tree], problem = tree_values(target, [row for row in rows if row["tree"] == tree], tree, policy)
        problems += [problem] if problem else []
    if not problems and values.get("baseline") is None and values.get("head") is None:
        problems.append(f"`{target}` is absent on both the baseline and head trees")
    if problems:
        return [], problems
    return measurements_for(target, rows, values, policy), []


def variants(rows: list[dict]) -> dict[str, set[str]]:
    return {key: {str(row[key]) for row in rows if key in row} for key in ("unit", "better")}


def measured(row: dict) -> bool:
    return "value" in row or "values" in row


def row_values(row: dict) -> list[float]:
    return list(row["values"]) if "values" in row else [row["value"]]


def tree_values(target: str, rows: list[dict], tree: str, policy: Policy) -> tuple[list[float] | None, str]:
    samples = [row for row in rows if measured(row)]
    absent = any(row.get("absent") for row in rows)
    if samples and absent:
        return None, f"`{target}` has both values and absent on the {tree} tree"
    if not samples and not absent:
        return None, f"`{target}` was not measured on the {tree} tree"
    if samples and len(samples) < policy.min_runs:
        return None, f"`{target}` has {len(samples)} samples on the {tree} tree; min_runs is {policy.min_runs}"
    short = [row for row in samples if "values" in row and len(row["values"]) < policy.values_per_sample]
    if short:
        return None, (
            f"`{target}` has {len(short)} samples on the {tree} tree with fewer than {policy.values_per_sample} values each; "
            f"time at least {policy.values_per_sample} requests per sample"
        )
    return [value for row in samples for value in row_values(row)] or None, ""


def measurements_for(target: str, rows: list[dict], values: dict[str, list[float] | None], policy: Policy) -> list[Measurement]:
    first = next(row for row in rows if measured(row))
    runs = min(sum(1 for row in rows if row["tree"] == tree and measured(row)) for tree, found in values.items() if found)
    stats = {tree: percentiles(found) for tree, found in values.items() if found}
    compared = [len(found) for tree, found in values.items() if tree in COMPARED and found]
    intervals = bootstrap(values.get("baseline"), values.get("head"), policy.bootstrap, zlib.crc32(target.encode()))
    return [
        Measurement(
            target, metric, first["unit"], first["better"],
            stat(stats, "pre-marestail", index), stat(stats, "baseline", index), stat(stats, "head", index),
            runs, first["script"], min(compared) if compared else 0,
            intervals[index] if intervals else None, stat(stats, CONTROL, index),
        )
        for index, metric in enumerate(METRICS)
    ]


def stat(stats: dict[str, tuple[float, float]], tree: str, index: int) -> float | None:
    found = stats.get(tree)
    return None if found is None else found[index]


def bootstrap(baseline: list[float] | None, head: list[float] | None, rounds: int, seed: int) -> tuple[tuple[float, float], tuple[float, float]] | None:
    if not baseline or not head or rounds <= 0:
        return None
    rng = random.Random(seed)
    changes: tuple[list[float], list[float]] = ([], [])
    for _ in range(rounds):
        before = sorted_percentiles(sorted(rng.choices(baseline, k=len(baseline))))
        after = sorted_percentiles(sorted(rng.choices(head, k=len(head))))
        for index, collected in enumerate(changes):
            collected.append(percent_change(before[index], after[index]))
    return interval(changes[0]), interval(changes[1])


def interval(changes: list[float]) -> tuple[float, float]:
    ordered = sorted(changes)
    return ordered[int(0.025 * len(ordered))], ordered[math.ceil(0.975 * len(ordered)) - 1]


def classify(measurement: Measurement, policy: Policy) -> Classified:
    if measurement.baseline is None:
        return Classified(measurement, "new", None)
    if measurement.head is None:
        return Classified(measurement, "removed", None)
    change = percent_change(measurement.baseline, measurement.head)
    if measurement.metric == "p95" and measurement.values < policy.p95_min_values:
        return Classified(measurement, "thin", change)
    if abs(measurement.head - measurement.baseline) < policy.floor(measurement.unit):
        return Classified(measurement, "unchanged", change)
    bar = max(policy.threshold_percent, noise(measurement))
    low, high = measurement.interval or (change, change)
    rose, fell = low >= bar, high <= -bar
    if rose or fell:
        worse = rose if measurement.better == "lower" else fell
        return Classified(measurement, "degraded" if worse else "improved", change)
    return Classified(measurement, "unchanged", change)


def noise(measurement: Measurement) -> float:
    if measurement.control is None or measurement.baseline is None:
        return 0.0
    return abs(percent_change(measurement.baseline, measurement.control))


def percent_change(baseline: float, head: float) -> float:
    if baseline == 0:
        return 0.0 if head == 0 else math.copysign(100.0, head)
    return round((head - baseline) * 100 / baseline, 9)


def stale_problems(records: list[dict], fingerprints: dict[str, str]) -> list[str]:
    counts: dict[str, dict[str, int]] = {}
    for record in records:
        script = record["script"]
        if script in fingerprints and record.get("fingerprint") != fingerprints[script]:
            per_tree = counts.setdefault(script, {})
            per_tree[record["tree"]] = per_tree.get(record["tree"], 0) + 1
    return [
        f"`{script}` has samples taken before it or a shared file under perf/ last changed "
        f"({', '.join(f'{count} on {tree}' for tree, count in sorted(per_tree.items()))}); take its samples again on every tree"
        for script, per_tree in sorted(counts.items())
    ]


def audit(classified: list[Classified], existing_columns: list[str], verdict_text: str, verdict: str, benches: list[str]) -> list[str]:
    measured_columns = {item.measurement.column for item in classified}
    problems = [
        f"column `{column}` in PERFORMANCE.md was not re-measured; every existing bench runs on every tree every time"
        for column in existing_columns
        if column not in measured_columns
    ]
    unflagged: dict[str, Classified] = {}
    for item in classified:
        if item.status in FLAGGED and item.measurement.target not in verdict_text:
            unflagged.setdefault(item.measurement.target, item)
    problems += [f"`{target}` is {item.status} ({item.change:+.1f}% {item.measurement.metric}) but the verdict does not name it" for target, item in unflagged.items()]
    if not classified and (benches or existing_columns or verdict != "PASS"):
        problems.append("no measurements were taken; run every perf/bench_* script on every tree through `marestail perf run`")
    return problems
