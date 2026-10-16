import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, NamedTuple

from marestail import config as config_module
from marestail.config import Config
from marestail.perf import db as perf_db
from marestail.perf import hygiene, settings, trees

BETTER = ("lower", "higher")
OK = ""


class Target(NamedTuple):
    bench: str
    tree: trees.Tree
    database: perf_db.Database | None


class SampleError(Exception):
    pass


def run_command(script: str, tree_name: str, samples: int, db: bool) -> int:
    config = config_module.load(Path.cwd())
    target, problem = resolve(config, script, tree_name, db)
    if target is None:
        return fail(problem, 2)
    return take_samples(config, target, samples)


def resolve(config: Config, script: str, tree_name: str, db: bool) -> tuple[Target | None, str]:
    active = trees.active(config)
    if active is None:
        return None, "no perf run in progress"
    bench = bench_path(config, script)
    if bench is None:
        return None, f"{script} is not an executable perf/bench_* script in {config.root}"
    tree = active.get(tree_name)
    if tree is None:
        return None, f"unknown tree {tree_name}; choose from {', '.join(active)}"
    return with_database(config, bench, tree, db)


def with_database(config: Config, bench: str, tree: trees.Tree, db: bool) -> tuple[Target | None, str]:
    if not db:
        return Target(bench, tree, None), OK
    database, problem = perf_db.for_run(config)
    if database is None:
        return None, problem
    return Target(bench, tree, database), OK


def take_samples(config: Config, target: Target, samples: int) -> int:
    stamp = hygiene.fingerprint(config.root, target.bench)
    report_dropped(drop_stale(config, target.bench, stamp), target.bench)
    for sample in range(1, samples + 1):
        problem = take_sample(config, target.bench, target.tree, sample, (target.database, stamp))
        if problem:
            return fail(problem, 1)
    return 0


def report_dropped(dropped: int, bench: str) -> None:
    if dropped:
        sys.stderr.write(
            f"dropped {dropped} earlier measurements of {bench} taken before it or a shared file under perf/ changed; "
            "take its samples again on every tree\n"
        )


def drop_stale(config: Config, bench: str, stamp: str) -> int:
    path = trees.samples_file(config)
    if not path.exists():
        return 0
    lines = path.read_text().splitlines()
    kept = fresh_lines(lines, bench, stamp)
    if len(kept) != len(lines):
        path.write_text("".join(line + "\n" for line in kept))
    return len(lines) - len(kept)


def fresh_lines(lines: list[str], bench: str, stamp: str) -> list[str]:
    return [line for line in lines if not stale(line, bench, stamp)]


def stale(line: str, bench: str, stamp: str) -> bool:
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return False
    return isinstance(record, dict) and record.get("script") == bench and record.get("fingerprint") != stamp


def fail(message: str, code: int) -> int:
    sys.stderr.write(message + "\n")
    return code


def bench_path(config: Config, script: str) -> str | None:
    root = config.root.resolve()
    candidate = (Path.cwd() / script).resolve()
    if not candidate.is_relative_to(root):
        return None
    relative = candidate.relative_to(root)
    return relative.as_posix() if is_bench(relative) and is_executable(candidate) else None


def is_bench(relative: Path) -> bool:
    return relative.parent == Path("perf") and relative.name.startswith("bench_")


def is_executable(candidate: Path) -> bool:
    return candidate.is_file() and os.access(candidate, os.X_OK)


def take_sample(config: Config, bench: str, tree: trees.Tree, sample: int, harness: tuple[perf_db.Database | None, str]) -> str:
    database, stamp = harness
    label = f"{bench} sample {sample} on {tree.name}"
    timeout = settings.sample_timeout(config)
    try:
        reset_ms, env = sample_env(database, tree, sample, label)
        records = sample_records(execute(config, bench, tree, env, (label, timeout)), label)
    except SampleError as error:
        return str(error)
    base = {
        "tree": tree.name,
        "sha": tree.sha,
        "script": bench,
        "fingerprint": stamp,
        "sample": sample,
        "db": database is not None,
        "reset_ms": reset_ms,
    }
    with trees.samples_file(config).open("a") as handle:
        handle.writelines(json.dumps({**base, **record}) + "\n" for record in records)
    return ""


def sample_env(database: perf_db.Database | None, tree: trees.Tree, sample: int, label: str) -> tuple[int | None, dict[str, str]]:
    env = {"MARESTAIL_PERF_TREE": tree.name, "MARESTAIL_PERF_TREE_PATH": str(tree.path), "MARESTAIL_PERF_SAMPLE": str(sample)}
    if database is None:
        return None, env
    try:
        reset_ms, database_env = perf_db.reset(database, tree)
    except perf_db.DatabaseError as error:
        raise SampleError(f"{label}: {error}") from error
    return reset_ms, env | database_env


def execute(config: Config, bench: str, tree: trees.Tree, env: dict[str, str], limits: tuple[str, int]) -> subprocess.CompletedProcess[str]:
    label, timeout = limits
    try:
        return subprocess.run(
            [str(config.root / bench)],
            cwd=tree.path,
            env={**os.environ, **env},
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise SampleError(f"{label} timed out after {timeout}s") from error
    except OSError as error:
        raise SampleError(f"{label} could not start: {error}") from error


def sample_records(completed: subprocess.CompletedProcess[str], label: str) -> list[dict[str, Any]]:
    records, passthrough = parse_output(completed.stdout)
    for line in passthrough:
        print(line)
    sys.stderr.write(completed.stderr)
    if completed.returncode != 0:
        raise SampleError(f"{label} exited {completed.returncode}")
    if not records:
        raise SampleError(f"{label} printed no JSON measurement")
    return records


def parse_output(stdout: str) -> tuple[list[dict[str, Any]], list[str]]:
    records: list[dict[str, Any]] = []
    passthrough: list[str] = []
    for line in stdout.splitlines():
        record = measurement(line)
        if record is None:
            passthrough.append(line)
        else:
            records.append(record)
    return records, passthrough


def measurement(line: str) -> dict[str, Any] | None:
    data = targeted(line)
    if data is None:
        return None
    if data.get("absent") is True:
        return {"target": data["target"], "absent": True}
    return timed(data)


def targeted(line: str) -> dict[str, Any] | None:
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None
    return data if has_target(data) else None


def has_target(data: object) -> bool:
    return isinstance(data, dict) and isinstance(data.get("target"), str) and bool(data["target"])


def timed(data: dict[str, Any]) -> dict[str, Any] | None:
    timings = timing_fields(data)
    if timings is None or not isinstance(data.get("unit"), str) or data.get("better") not in BETTER:
        return None
    return {"target": data["target"], "unit": data["unit"], "better": data["better"], **timings}


def timing_fields(data: dict[str, Any]) -> dict[str, Any] | None:
    if "values" in data:
        return {"values": data["values"]} if numbers(data["values"]) else None
    return {"value": data["value"]} if is_number(data.get("value")) else None


def numbers(values: object) -> bool:
    return isinstance(values, list) and bool(values) and all(is_number(value) for value in values)


def is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)
