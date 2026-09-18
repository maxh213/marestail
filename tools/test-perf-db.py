#!/usr/bin/env python3
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from marestail import config as config_module
from marestail.perf import db as perf_db
from marestail.perf import samples, settings
from marestail.perf import trees as perf_trees

PREFIX = "marestail-perf-test-"
VOLUME = "marestail-perf-test-pgdata"
PORT = "55532"
MIGRATE = (
    'docker exec -i "$MARESTAIL_PERF_DB_CONTAINER" psql -v ON_ERROR_STOP=1 -q -U postgres -d bench '
    '-c "create table a (id bigint primary key, note text); create table b (id bigint primary key)"'
)
SEED = "insert into a select g, md5(g::text) from generate_series(1, :rows) g;\ninsert into b select g from generate_series(1, :rows) g;\n"
SHORT_SEED = (
    "insert into a select g, md5(g::text) from generate_series(1, :rows) g;\ninsert into b select g from generate_series(1, 10) g;\n"
)
COUNT_BENCH = """#!/usr/bin/env python3
import json, os, subprocess
container = os.environ["MARESTAIL_PERF_DB_CONTAINER"]
def query(sql):
    return subprocess.run(["docker", "exec", container, "psql", "-qAt", "-U", "postgres", "-d", "bench", "-c", sql], check=True, capture_output=True, text=True).stdout.strip()
count = int(query("select count(*) from a"))
query("insert into a values (-1, 'written by the bench')")
print(json.dumps({"target": "rows in a", "unit": "rows", "better": "lower", "value": count}))
"""


def expect(name: str, got: object, wanted: object) -> None:
    if got != wanted:
        raise SystemExit(f"{name}: {got!r} != {wanted!r}")


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def docker(*args: str) -> str:
    return subprocess.run(["docker", *args], capture_output=True, text=True).stdout.strip()


@contextlib.contextmanager
def quiet() -> Iterator[None]:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        yield


def make_repo(tmp: str) -> Path:
    root = Path(tmp) / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "test@marestail")
    git(root, "config", "user.name", "test")
    (root / "marestail.toml").write_text(f"[git]\nbase = \"main\"\n\n[perf.db]\nrows = 1000\nmigrate = '''{MIGRATE}'''\n")
    (root / ".gitignore").write_text(".marestail/\n")
    (root / "docker-compose.yml").write_text("services:\n  db:\n    image: postgres:16\n")
    (root / "perf").mkdir()
    (root / "perf" / "seed.sql").write_text(SEED)
    bench = root / "perf" / "bench_count.py"
    bench.write_text(COUNT_BENCH)
    bench.chmod(0o755)
    git(root, "add", "-A")
    git(root, "commit", "-qm", "init")
    return root


def head_tree(session: perf_trees.Session) -> perf_trees.Tree:
    return next(tree for tree in session.trees if tree.name == "head")


def goldens_listing(database: perf_db.Database) -> list[str]:
    return docker("exec", database.helper, "ls", "/perf/goldens").split()


def sample_values(config: config_module.Config) -> list[dict[str, Any]]:
    return [json.loads(line) for line in perf_trees.samples_file(config).read_text().splitlines()]


def seeded_golden(config: config_module.Config, session: perf_trees.Session, root: Path) -> tuple[perf_db.Database, str]:
    with quiet():
        perf_db.prepare(config, session)
    expect("image-resolved", (session.image, session.image_source), ("postgres:16", "docker-compose.yml"))
    found, problem = perf_db.for_run(config)
    database = cast(perf_db.Database, found)
    expect("database-configured", problem, "")
    head = head_tree(session)
    name = perf_db.golden_name(database, head)
    expect("golden-ready", perf_db.golden_state(database, name), "ready")
    expect("golden-status-line", perf_db.status_line(database, head).split()[:4], ["head", "postgres:16", name, "ready"])
    expect("golden-meta-rows", perf_db.golden_meta(database, name).get("rows"), 1000)
    with contextlib.chdir(root), quiet():
        expect("db-samples-exit", samples.run_command("perf/bench_count.py", "head", 2, True), 0)
    records = sample_values(config)
    expect("db-count-both-samples", [record["value"] for record in records], [1000, 1000])
    expect("db-reset-logged", [isinstance(record["reset_ms"], int) and record["db"] for record in records], [True, True])
    version = docker("exec", database.tree_container("head"), "psql", "-qAt", "-U", "postgres", "-c", "show server_version")
    expect("server-version", version.split(".")[0], "16")
    return database, name


def short_seed_fails(root: Path, database: perf_db.Database, head: perf_trees.Tree, seeded_name: str) -> str:
    (root / "perf" / "seed.sql").write_text(SHORT_SEED)
    short_name = perf_db.golden_name(database, head)
    expect("seed-bytes-change-name", short_name != seeded_name, True)
    with quiet():
        problem = perf_db.build(database, head)
    expect("short-seed-names-b", "b (10)" in problem, True)
    expect("short-seed-no-tmp", [entry for entry in goldens_listing(database) if entry.endswith(".tmp")], [])
    expect(
        "short-seed-no-build-container",
        [name for name in docker("ps", "-a", "--format", "{{.Names}}").split() if name.startswith(PREFIX + "golden-")],
        [],
    )
    return short_name


def prune_unneeded(database: perf_db.Database, seeded_name: str, short_name: str) -> None:
    removed = perf_db.prune(database, {short_name})
    expect("prune-removed", removed, [seeded_name])
    expect("prune-gone", seeded_name in goldens_listing(database), False)


def empty_golden_in_background(config: config_module.Config, session: perf_trees.Session, root: Path) -> None:
    (root / "perf" / "seed.sql").unlink()
    os.environ[settings.ROWS_ENV] = "0"
    session.rows, session.rows_source = 0, settings.ROWS_ENV
    perf_trees.write_trees(config, session)
    found, _ = perf_db.for_run(config)
    database = cast(perf_db.Database, found)
    head = head_tree(session)
    expect("background-started", perf_db.start_build(database, head).startswith("building golden_"), True)
    name = perf_db.golden_name(database, head)
    deadline = time.monotonic() + 300
    while perf_db.golden_state(database, name) == "building" and time.monotonic() < deadline:
        time.sleep(1)
    expect("empty-golden-ready", perf_db.golden_state(database, name), "ready")
    expect("empty-golden-meta-rows", perf_db.golden_meta(database, name).get("rows"), 0)
    perf_trees.samples_file(config).write_text("")
    with contextlib.chdir(root), quiet():
        expect("empty-samples-exit", samples.run_command("perf/bench_count.py", "head", 1, True), 0)
    expect("empty-count", [record["value"] for record in sample_values(config)], [0])
    os.environ.pop(settings.ROWS_ENV)


def missing_seed_fails(config: config_module.Config, session: perf_trees.Session) -> None:
    database = perf_db.build_database(config, settings.db(config), 1000, "test", "postgres:16", "test")
    with quiet():
        problem = perf_db.build(database, head_tree(session))
    expect("missing-seed", "[perf.db] rows = 1000 needs a perf/seed script" in problem, True)


def checks(config: config_module.Config, session: perf_trees.Session, root: Path) -> None:
    database, seeded_name = seeded_golden(config, session, root)
    short_name = short_seed_fails(root, database, head_tree(session), seeded_name)
    prune_unneeded(database, seeded_name, short_name)
    empty_golden_in_background(config, session, root)
    missing_seed_fails(config, session)


def remove_test_docker() -> None:
    for name in docker("ps", "-a", "--format", "{{.Names}}").split():
        if name.startswith(PREFIX):
            docker("rm", "-f", "-v", name)
    docker("volume", "rm", VOLUME)


def main() -> None:
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
            root = make_repo(tmp)
            config = config_module.load(root)
            with perf_trees.measuring(config, "t") as session:
                try:
                    checks(config, session, root)
                finally:
                    perf_db.release(config, session)
    finally:
        remove_test_docker()
        home.cleanup()
    leftovers = [name for name in docker("ps", "-a", "--format", "{{.Names}}").split() if name.startswith(PREFIX)]
    expect("no-test-containers", leftovers, [])
    expect("no-test-volume", VOLUME in docker("volume", "ls", "--format", "{{.Name}}").split(), False)
    print("perf db ok")


if __name__ == "__main__":
    main()
