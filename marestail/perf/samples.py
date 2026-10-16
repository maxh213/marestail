import json
import os
import socket
import subprocess
import sys
from pathlib import Path

from marestail import config as config_module
from marestail import remote
from marestail.config import Config
from marestail.perf import db as perf_db
from marestail.perf import hygiene, settings, trees

BETTER = ("lower", "higher")


def run_command(script: str, tree_name: str, samples: int, db: bool) -> int:
    config = config_module.load(Path.cwd())
    active = trees.active(config)
    if active is None:
        return fail("no perf run in progress", 2)
    bench = bench_path(config, script)
    if bench is None:
        return fail(f"{script} is not an executable perf/bench_* script in {config.root}", 2)
    tree = active.get(tree_name)
    if tree is None:
        return fail(f"unknown tree {tree_name}; choose from {', '.join(active)}", 2)
    database = None
    if db:
        database, problem = perf_db.for_run(config)
        if database is None:
            return fail(problem, 2)
    stamp = hygiene.fingerprint(config.root, bench)
    dropped = drop_stale(config, bench, stamp, remote.perf_target(config))
    if dropped:
        sys.stderr.write(
            f"dropped {dropped} earlier measurements of {bench} taken before it or a shared file under perf/ changed, "
            "or taken on another machine; take its samples again on every tree\n"
        )
    for sample in range(1, samples + 1):
        problem = take_sample(config, bench, tree, sample, (database, stamp))
        if problem:
            return fail(problem, 1)
    return 0


def drop_stale(config: Config, bench: str, stamp: str, where: str | None = None) -> int:
    path = trees.samples_file(config)
    if not path.exists():
        return 0
    lines = path.read_text().splitlines()
    kept = [line for line in lines if not stale(line, bench, stamp, where)]
    if len(kept) != len(lines):
        path.write_text("".join(line + "\n" for line in kept))
    return len(lines) - len(kept)


def stale(line: str, bench: str, stamp: str, where: str | None = None) -> bool:
    """A sample is stale when the bench changed, or when it was taken on a different machine than samples are taken on now."""
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return False
    if not isinstance(record, dict) or record.get("script") != bench:
        return False
    if record.get("fingerprint") != stamp:
        return True
    if where is None or record.get("db"):
        return False
    return record.get("where", socket.gethostname()) != where


def fail(message: str, code: int) -> int:
    sys.stderr.write(message + "\n")
    return code


def bench_path(config: Config, script: str) -> str | None:
    root = config.root.resolve()
    candidate = (Path.cwd() / script).resolve()
    if not candidate.is_relative_to(root):
        return None
    relative = candidate.relative_to(root)
    if relative.parent != Path("perf") or not relative.name.startswith("bench_"):
        return None
    if not candidate.is_file() or not os.access(candidate, os.X_OK):
        return None
    return relative.as_posix()


def take_sample(config: Config, bench: str, tree: trees.Tree, sample: int, harness: tuple[perf_db.Database | None, str]) -> str:
    database, stamp = harness
    label = f"{bench} sample {sample} on {tree.name}"
    timeout = settings.sample_timeout(config)
    env = {"MARESTAIL_PERF_TREE": tree.name, "MARESTAIL_PERF_TREE_PATH": str(tree.path), "MARESTAIL_PERF_SAMPLE": str(sample)}
    reset_ms = None
    if database is not None:
        try:
            reset_ms, database_env = perf_db.reset(database, tree)
        except perf_db.DatabaseError as error:
            return f"{label}: {error}"
        env |= database_env
    where = socket.gethostname()
    outcome = remote.run_sample(config, bench, tree.path, env, timeout) if database is None else None
    if outcome is not None:
        code, stdout, stderr = outcome.code, outcome.output, ""
        where = remote.perf_target(config)
        label += outcome.where
    else:
        try:
            completed = subprocess.run(
                [str(config.root / bench)], cwd=tree.path, env={**os.environ, **env}, capture_output=True, text=True, timeout=timeout, check=False
            )
        except subprocess.TimeoutExpired:
            return f"{label} timed out after {timeout}s"
        except OSError as error:
            return f"{label} could not start: {error}"
        code, stdout, stderr = completed.returncode, completed.stdout, completed.stderr
    records, passthrough = parse_output(stdout)
    for line in passthrough:
        print(line)
    sys.stderr.write(stderr)
    if code != 0:
        return f"{label} exited {code}"
    if not records:
        return f"{label} printed no JSON measurement"
    base = {
        "tree": tree.name, "sha": tree.sha, "script": bench, "fingerprint": stamp, "sample": sample, "db": database is not None,
        "reset_ms": reset_ms, "where": where,
    }
    with trees.samples_file(config).open("a") as handle:
        handle.writelines(json.dumps({**base, **record}) + "\n" for record in records)
    return ""


def parse_output(stdout: str) -> tuple[list[dict], list[str]]:
    records: list[dict] = []
    passthrough: list[str] = []
    for line in stdout.splitlines():
        record = measurement(line)
        if record is None:
            passthrough.append(line)
        else:
            records.append(record)
    return records, passthrough


def measurement(line: str) -> dict | None:
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("target"), str) or not data["target"]:
        return None
    if data.get("absent") is True:
        return {"target": data["target"], "absent": True}
    timings = timing_fields(data)
    if timings is None or not isinstance(data.get("unit"), str) or data.get("better") not in BETTER:
        return None
    return {"target": data["target"], "unit": data["unit"], "better": data["better"], **timings}


def timing_fields(data: dict) -> dict | None:
    if "values" in data:
        values = data["values"]
        valid = isinstance(values, list) and bool(values) and all(is_number(value) for value in values)
        return {"values": values} if valid else None
    return {"value": data["value"]} if is_number(data.get("value")) else None


def is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)
