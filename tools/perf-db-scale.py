#!/usr/bin/env python3
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from marestail import config as config_module
from marestail.perf import db as perf_db
from marestail.perf import settings
from marestail.perf import trees as perf_trees

PREFIX = "marestail-perf-test-"
VOLUME = "marestail-perf-test-pgdata"
PORT = "55532"
IMAGE = "postgres:16"
ROWS = 50000000
RESETS = 10
MIGRATE = (
    'docker exec -i "$MARESTAIL_PERF_DB_CONTAINER" psql -v ON_ERROR_STOP=1 -q -U postgres -d bench '
    '-c "create table t (id bigint primary key, amount numeric, created_at timestamptz, note text)"'
)
SEED = "insert into t select g, (g % 10000) / 100.0, now() - make_interval(secs => g), md5(g::text) from generate_series(1, :rows) g;\n"


def git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


def docker(*args):
    return subprocess.run(["docker", *args], capture_output=True, text=True).stdout.strip()


def make_repo(tmp: str) -> Path:
    root = Path(tmp) / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "scale@marestail")
    git(root, "config", "user.name", "scale")
    (root / "marestail.toml").write_text(
        f"[git]\nbase = \"main\"\n\n[perf.db]\nrows = {ROWS}\nimage = \"{IMAGE}\"\nmigrate = '''{MIGRATE}'''\n"
    )
    (root / ".gitignore").write_text(".marestail/\n")
    (root / "perf").mkdir()
    (root / "perf" / "seed.sql").write_text(SEED)
    git(root, "add", "-A")
    git(root, "commit", "-qm", "init")
    return root


def remove_test_docker():
    for name in docker("ps", "-a", "--format", "{{.Names}}").split():
        if name.startswith(PREFIX):
            docker("rm", "-f", "-v", name)
    docker("volume", "rm", VOLUME)


def measure(config) -> str:
    with perf_trees.measuring(config, "scale") as session:
        try:
            started = time.monotonic()
            perf_db.prepare(config, session)
            seed_s = round(time.monotonic() - started)
            failures = [note for note in session.notes if note.startswith("golden for the")]
            if failures:
                raise SystemExit("; ".join(failures))
            database, problem = perf_db.for_run(config)
            if database is None:
                raise SystemExit(problem)
            head = next(tree for tree in session.trees if tree.name == "head")
            golden_bytes = perf_db.golden_meta(database, perf_db.golden_name(database, head))["bytes"]
            resets = [perf_db.reset(database, head)[0] for _ in range(RESETS)]
            print(f"resets_ms={resets}")
        finally:
            perf_db.release(config, session)
    return (
        f"scale: image={IMAGE} rows={ROWS} seed_s={seed_s} golden_bytes={golden_bytes} "
        f"reset_ms_median={round(statistics.median(resets))} reset_ms_max={max(resets)}"
    )


def main():
    home = tempfile.TemporaryDirectory()
    os.environ.update(
        {
            "MARESTAIL_PERF_DB_PREFIX": PREFIX,
            "MARESTAIL_PERF_DB_VOLUME": VOLUME,
            "MARESTAIL_PERF_DB_PORT": PORT,
            "MARESTAIL_PERF_DB_HOME": home.name,
        }
    )
    os.environ.pop(settings.ROWS_ENV, None)
    remove_test_docker()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            line = measure(config_module.load(make_repo(tmp)))
    finally:
        remove_test_docker()
        home.cleanup()
    print(line)


if __name__ == "__main__":
    main()
