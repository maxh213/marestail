import json
import os
import subprocess
import sys
from pathlib import Path

from marestail import config as config_module
from marestail.config import Config
from marestail.perf import settings, trees

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
    if db:
        return fail("configure [perf.db] migrate in marestail.toml", 2)
    for sample in range(1, samples + 1):
        problem = take_sample(config, bench, tree, sample, db)
        if problem:
            return fail(problem, 1)
    return 0


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


def take_sample(config: Config, bench: str, tree: trees.Tree, sample: int, db: bool) -> str:
    label = f"{bench} sample {sample} on {tree.name}"
    timeout = settings.sample_timeout(config)
    env = {"MARESTAIL_PERF_TREE": tree.name, "MARESTAIL_PERF_TREE_PATH": str(tree.path), "MARESTAIL_PERF_SAMPLE": str(sample)}
    try:
        completed = subprocess.run(
            [str(config.root / bench)], cwd=tree.path, env={**os.environ, **env}, capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired:
        return f"{label} timed out after {timeout}s"
    except OSError as error:
        return f"{label} could not start: {error}"
    records, passthrough = parse_output(completed.stdout)
    for line in passthrough:
        print(line)
    sys.stderr.write(completed.stderr)
    if completed.returncode != 0:
        return f"{label} exited {completed.returncode}"
    if not records:
        return f"{label} printed no JSON measurement"
    base = {"tree": tree.name, "sha": tree.sha, "script": bench, "sample": sample, "db": db, "reset_ms": None}
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
    value = data.get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not isinstance(data.get("unit"), str) or data.get("better") not in BETTER:
        return None
    return {"target": data["target"], "unit": data["unit"], "better": data["better"], "value": value}
