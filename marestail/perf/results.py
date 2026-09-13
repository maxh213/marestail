import math
import statistics
from dataclasses import dataclass

METRICS = ("p50", "p95")
FLAGGED = ("degraded", "improved")


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

    @property
    def column(self) -> str:
        return f"{self.target} {self.metric}"


@dataclass(frozen=True)
class Classified:
    measurement: Measurement
    status: str
    change: float | None


def percentiles(values: list[float]) -> tuple[float, float]:
    ordered = sorted(values)
    return statistics.median(ordered), ordered[math.ceil(0.95 * len(ordered)) - 1]


def compile_records(records: list[dict], tree_names: list[str], benches: list[str], min_runs: int) -> tuple[list[Measurement], list[str]]:
    problems = bench_problems(records, tree_names, benches)
    measurements: list[Measurement] = []
    for target, rows in grouped(records).items():
        found, target_problems = target_measurements(target, rows, tree_names, min_runs)
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


def target_measurements(target: str, rows: list[dict], tree_names: list[str], min_runs: int) -> tuple[list[Measurement], list[str]]:
    problems = [f"`{target}` reports more than one {key}: {', '.join(sorted(seen))}" for key, seen in variants(rows).items() if len(seen) > 1]
    values: dict[str, list[float] | None] = {}
    for tree in tree_names:
        values[tree], problem = tree_values(target, [row for row in rows if row["tree"] == tree], tree, min_runs)
        problems += [problem] if problem else []
    if not problems and values.get("baseline") is None and values.get("head") is None:
        problems.append(f"`{target}` is absent on both the baseline and head trees")
    if problems:
        return [], problems
    return measurements_for(target, rows, values), []


def variants(rows: list[dict]) -> dict[str, set[str]]:
    return {key: {str(row[key]) for row in rows if key in row} for key in ("unit", "better")}


def tree_values(target: str, rows: list[dict], tree: str, min_runs: int) -> tuple[list[float] | None, str]:
    values = [row["value"] for row in rows if "value" in row]
    absent = any(row.get("absent") for row in rows)
    if values and absent:
        return None, f"`{target}` has both values and absent on the {tree} tree"
    if not values and not absent:
        return None, f"`{target}` was not measured on the {tree} tree"
    if values and len(values) < min_runs:
        return None, f"`{target}` has {len(values)} samples on the {tree} tree; min_runs is {min_runs}"
    return values or None, ""


def measurements_for(target: str, rows: list[dict], values: dict[str, list[float] | None]) -> list[Measurement]:
    first = next(row for row in rows if "value" in row)
    runs = min(len(found) for found in values.values() if found)
    stats = {tree: percentiles(found) for tree, found in values.items() if found}
    return [
        Measurement(
            target, metric, first["unit"], first["better"],
            stat(stats, "pre-marestail", index), stat(stats, "baseline", index), stat(stats, "head", index),
            runs, first["script"],
        )
        for index, metric in enumerate(METRICS)
    ]


def stat(stats: dict[str, tuple[float, float]], tree: str, index: int) -> float | None:
    found = stats.get(tree)
    return None if found is None else found[index]


def classify(measurement: Measurement, threshold: float) -> Classified:
    if measurement.baseline is None:
        return Classified(measurement, "new", None)
    if measurement.head is None:
        return Classified(measurement, "removed", None)
    change = percent_change(measurement.baseline, measurement.head)
    if abs(change) < threshold:
        return Classified(measurement, "unchanged", change)
    worse = change > 0 if measurement.better == "lower" else change < 0
    return Classified(measurement, "degraded" if worse else "improved", change)


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
    measured = {item.measurement.column for item in classified}
    problems = [
        f"column `{column}` in PERFORMANCE.md was not re-measured; every existing bench runs on every tree every time"
        for column in existing_columns
        if column not in measured
    ]
    unflagged: dict[str, Classified] = {}
    for item in classified:
        if item.status in FLAGGED and item.measurement.target not in verdict_text:
            unflagged.setdefault(item.measurement.target, item)
    problems += [f"`{target}` is {item.status} ({item.change:+.1f}% {item.measurement.metric}) but the verdict does not name it" for target, item in unflagged.items()]
    if not classified and (benches or existing_columns or verdict != "PASS"):
        problems.append("no measurements were taken; run every perf/bench_* script on every tree through `marestail perf run`")
    return problems
