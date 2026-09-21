import hashlib
import json
import os
import secrets
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from marestail import config as config_module
from marestail.config import Config
from marestail.freeze import matches_any
from marestail.perf import image as perf_image
from marestail.perf import settings, trees
from marestail.shell import run, tail

__all__ = [
    "Database",
    "DatabaseError",
    "build",
    "build_database",
    "command",
    "disk_message",
    "estimate_bytes",
    "for_run",
    "golden_meta",
    "golden_name",
    "golden_state",
    "prepare",
    "prune",
    "refuses",
    "release",
    "reset",
    "start_build",
    "status_line",
]

DEFAULT_PREFIX = "marestail-perf-"
DEFAULT_VOLUME = "marestail-perf-pgdata"
DEFAULT_PORT = 55432
DEFAULT_MIN_FREE_GB = 50
DEFAULT_SKIP_TABLES = ["schema_migrations", "alembic_version", "ar_internal_metadata", "__EFMigrationsHistory"]
TREE_PORT_OFFSETS = {"head": 0, "baseline": 1, "pre-marestail": 2}
DATABASE = "bench"
BUILD_TIMEOUT = 6 * 3600
READY_TIMEOUT = 120
INIT_TIMEOUT = 600
GIB = 1024**3
EMPTY = ""
ALIVE = 0
POLL = 0.05
META_TIME = "%Y-%m-%dT%H:%M:%S%z"
NOT_CONFIGURED = "configure [perf.db] migrate in marestail.toml"
REFLINK_FAILED = "reflink copy failed: the Docker data root must be on a reflink-capable filesystem such as btrfs or XFS"
CLI = Path(__file__).resolve().parents[1] / "cli.py"
SUPERUSER = "postgres"
TREES_JSON = "trees.json"
READY = "ready"
BUILDING = "building"
FAILED = "failed"
IMAGE = "image"
MIGRATE = "migrate"
SEED_SQL = "seed.sql"
BYTES = "bytes"
STATE = "state"
STARTED = "started"
ERROR = "error"
TABLES_SQL = (
    "select tablename, format('%I.%I', schemaname, tablename) from pg_tables "
    "where schemaname not in ('pg_catalog', 'information_schema') order by 2"
)


class DatabaseError(Exception):
    pass


@dataclass(frozen=True)
class Database:
    root: Path
    migrate: str
    url_env: str
    rows: int
    rows_source: str
    migrations: list[str]
    skip_tables: list[str]
    min_free_gb: float
    image: str
    image_source: str
    port: int
    prefix: str
    volume: str
    home: Path

    @property
    def repo_key(self) -> str:
        return hashlib.sha256(str(self.root).encode()).hexdigest()[:12]

    @property
    def helper(self) -> str:
        return f"{self.prefix}files"

    def tree_container(self, tree: str) -> str:
        return f"{self.prefix}db-{self.repo_key}-{tree}"

    def golden_container(self, name: str) -> str:
        return f"{self.prefix}golden-{name}"

    def tree_port(self, tree: str) -> int:
        return self.port + TREE_PORT_OFFSETS[tree]

    def work_dir(self, tree: str) -> str:
        return f"/perf/work/{self.repo_key}-{tree}"


def build_database(config: Config, section: dict[str, Any], rows: int, rows_source: str, image: str, image_source: str) -> Database:
    return Database(
        root=config.root,
        migrate=str(section.get(MIGRATE, EMPTY)),
        url_env=str(section.get("url_env", "DATABASE_URL")),
        rows=rows,
        rows_source=rows_source,
        migrations=list(section.get("migrations", [])),
        skip_tables=list(section.get("skip_tables", DEFAULT_SKIP_TABLES)),
        min_free_gb=float(section.get("min_free_gb", DEFAULT_MIN_FREE_GB)),
        image=image,
        image_source=image_source,
        port=int(os.environ.get("MARESTAIL_PERF_DB_PORT", section.get("port", DEFAULT_PORT))),
        prefix=os.environ.get("MARESTAIL_PERF_DB_PREFIX", DEFAULT_PREFIX),
        volume=os.environ.get("MARESTAIL_PERF_DB_VOLUME", DEFAULT_VOLUME),
        home=Path(os.environ.get("MARESTAIL_PERF_DB_HOME", Path.home() / ".config" / "marestail")),
    )


MILLISECONDS = 1000
SIZE_WIDTH = 8
MKDIR_PARENTS = True


def for_run(config: Config) -> tuple[Database | None, str]:
    section = settings.db(config)
    if not section.get(MIGRATE):
        return None, NOT_CONFIGURED
    recorded = trees.recorded(config)
    try:
        rows, rows_source = recorded_rows(config, recorded)
    except ValueError as error:
        return None, str(error)
    image, image_source = recorded_image(config, section, recorded)
    return build_database(config, section, rows, rows_source, image, image_source), ""


def recorded_rows(config: Config, recorded: dict[str, Any]) -> tuple[int, str]:
    if recorded.get("rows") is not None:
        return int(recorded["rows"]), str(recorded.get("rows_source", TREES_JSON))
    return settings.effective_rows(config)


def recorded_image(config: Config, section: dict[str, Any], recorded: dict[str, Any]) -> tuple[str, str]:
    if recorded.get(IMAGE):
        return str(recorded[IMAGE]), str(recorded.get("image_source", TREES_JSON))
    return perf_image.resolve(config.root, section.get(IMAGE))


def prepare(config: Config, session: trees.Session) -> None:
    section = settings.db(config)
    if not section.get(MIGRATE):
        return
    rows, rows_source = rows_or_exit(config)
    image, image_source = perf_image.resolve(config.root, section.get(IMAGE))
    print(image_line(image, image_source))
    session.image, session.image_source, session.rows, session.rows_source = image, image_source, rows, rows_source
    trees.write_trees(config, session)
    if rows > 0 and seed_script(config.root) is None:
        return
    build_all(build_database(config, section, rows, rows_source, image, image_source), session)


def rows_or_exit(config: Config) -> tuple[int, str]:
    try:
        return settings.effective_rows(config)
    except ValueError as error:
        raise SystemExit(str(error)) from error


def image_line(image: str, image_source: str) -> str:
    if image_source == "default":
        return f"   no Postgres version found in the repo; using {image}"
    return f"   performance database image {image} from {image_source}"


def build_all(database: Database, session: trees.Session) -> None:
    for tree in session.trees:
        problem = build(database, tree)
        if problem:
            session.notes.append(f"golden for the {tree.name} tree failed: {problem}")


def release(config: Config, session: trees.Session) -> None:
    if session.image is None:
        return
    database, _ = for_run(config)
    if database is None:
        return
    remove_tree_containers(database, session)
    if helper_running(database):
        docker(database, "exec", database.helper, "rm", "-rf", *work_dirs(database, session))


def remove_tree_containers(database: Database, session: trees.Session) -> None:
    for tree in session.trees:
        remove_container(database, database.tree_container(tree.name))


def work_dirs(database: Database, session: trees.Session) -> list[str]:
    return [database.work_dir(tree.name) for tree in session.trees]


def remove_container(database: Database, name: str) -> None:
    docker(database, "rm", "-f", "-v", name)


def docker(database: Database, *args: str, stdin: str | None = None, timeout: int = 600) -> tuple[int, str]:
    return run(["docker", *args], cwd=database.root, stdin=stdin, timeout=timeout)


def step(result: tuple[int, str], label: str) -> str:
    code, output = result
    if code != 0:
        raise DatabaseError(f"{label} failed (exit {code}): {' | '.join(tail(output, 5))}")
    return output


def helper_running(database: Database) -> bool:
    code, output = docker(database, "inspect", "-f", "{{.State.Running}}", database.helper)
    return code == 0 and output.strip() == "true"


def ensure_helper(database: Database) -> None:
    if helper_running(database):
        return
    remove_container(database, database.helper)
    image = perf_image.helper_image(database.image)
    ensure_image(database, image)
    step(
        docker(
            database, "run", "-d", "--name", database.helper, "-v", f"{database.volume}:/perf", "--entrypoint", "sleep", image, "infinity"
        ),
        "starting the file helper",
    )


def files(database: Database, script: str, stdin: str | None = None) -> tuple[int, str]:
    ensure_helper(database)
    interactive = ["-i"] if stdin is not None else []
    return docker(database, "exec", *interactive, database.helper, "bash", "-c", script, stdin=stdin, timeout=BUILD_TIMEOUT)


def ensure_image(database: Database, image: str) -> None:
    code, _ = docker(database, "image", "inspect", image)
    if code == 0:
        return
    print(f"   pulling {image}")
    step(docker(database, "pull", image, timeout=BUILD_TIMEOUT), f"docker pull {image}")


def password(database: Database) -> str:
    path = database.home / "perf-db.json"
    if path.exists():
        return str(json.loads(path.read_text())["password"])
    secret = secrets.token_urlsafe(24)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"password": secret}))
    path.chmod(0o600)
    return secret


def connection_url(database: Database, port: int | str) -> str:
    return f"postgresql://{SUPERUSER}:{password(database)}@127.0.0.1:{port}/{DATABASE}"


def database_env(database: Database, container: str, url: str) -> dict[str, str]:
    return {
        "MARESTAIL_PERF_DATABASE_URL": url,
        "MARESTAIL_PERF_DATABASE": DATABASE,
        "MARESTAIL_PERF_DB_CONTAINER": container,
        "MARESTAIL_PERF_ROWS": str(database.rows),
        database.url_env: url,
    }


def start_postgres(database: Database, name: str, pgdata: str, port: int | None, timeout: int) -> None:
    ensure_image(database, database.image)
    publish = f"127.0.0.1:{port}:5432" if port else "127.0.0.1::5432"
    step(
        docker(
            database,
            "run",
            "-d",
            "--name",
            name,
            "-v",
            f"{database.volume}:/perf",
            "-p",
            publish,
            "-e",
            f"POSTGRES_PASSWORD={password(database)}",
            "-e",
            f"POSTGRES_DB={DATABASE}",
            "-e",
            f"PGDATA={pgdata}",
            database.image,
        ),
        f"starting {name}",
    )
    wait_ready(database, name, timeout)


def wait_ready(database: Database, name: str, timeout: int) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        code, output = docker(database, "exec", name, "pg_isready", "-q", "-h", "127.0.0.1", "-U", SUPERUSER)
        if code == 0:
            return
        if "is not running" in output:
            _, logs = docker(database, "logs", "--tail", "20", name)
            raise DatabaseError(f"{name} stopped before it was ready: {' | '.join(tail(logs, 5))}")
        time.sleep(POLL)
    raise DatabaseError(f"{name} was not ready after {timeout}s")


COLON = ":"
TAB = "\t"


def after_last_colon(text: str) -> str:
    if COLON not in text:
        return text
    return text[text.rindex(COLON) + 1 :]


def host_port(database: Database, name: str) -> str:
    output = step(docker(database, "port", name, "5432/tcp"), f"docker port {name}")
    return after_last_colon(output.strip().splitlines()[0])


def psql(database: Database, container: str, sql: str) -> tuple[int, str]:
    return docker(
        database,
        "exec",
        container,
        "psql",
        "-v",
        "ON_ERROR_STOP=1",
        "-qAt",
        "-F",
        "\t",
        "-U",
        SUPERUSER,
        "-d",
        DATABASE,
        "-c",
        sql,
        timeout=BUILD_TIMEOUT,
    )


def seed_script(root: Path) -> Path | None:
    folder = root / "perf"
    if (folder / SEED_SQL).is_file():
        return folder / SEED_SQL
    if not folder.is_dir():
        return None
    return next(iter(executable_seeds(folder)), None)


def executable_seeds(folder: Path) -> list[Path]:
    return sorted(path for path in folder.glob("seed*") if executable_seed(path))


def executable_seed(path: Path) -> bool:
    return path.is_file() and (path.name == "seed" or path.name.startswith("seed.")) and os.access(path, os.X_OK)


def golden_name(database: Database, tree: trees.Tree) -> str:
    seed = seed_script(database.root)
    seed_bytes = seed.read_bytes() if seed is not None and database.rows > 0 else b""
    return golden_hash(str(database.root), database.image, schema_identity(database, tree), seed_bytes, database.rows)


def golden_hash(root: str, image: str, identity: str, seed: bytes, rows: int) -> str:
    digest = hashlib.sha256()
    for part in (root.encode(), image.encode(), identity.encode(), seed, str(rows).encode()):
        digest.update(len(part).to_bytes(SIZE_WIDTH))
        digest.update(part)
    return "golden_" + digest.hexdigest()[:16]


def schema_identity(database: Database, tree: trees.Tree) -> str:
    if not database.migrations:
        return tree.sha
    _, listing = run(["git", "ls-tree", "-r", tree.sha], cwd=database.root)
    return "\n".join(migration_entries(listing, database.migrations))


def after_tab(line: str) -> str:
    if TAB not in line:
        return line
    return line[line.index(TAB) + 1 :]


def migration_entries(listing: str, migrations: list[str]) -> list[str]:
    return [line for line in listing.splitlines() if TAB in line and matches_any(after_tab(line), migrations)]


def estimate_bytes(goldens: list[dict[str, Any]], rows: int, min_free_gb: float) -> int:
    seeded = [golden for golden in goldens if int(golden.get("rows", 0)) > 0]
    if not seeded:
        return int(min_free_gb * GIB)
    largest = max(seeded, key=lambda golden: int(golden[BYTES]))
    return int(largest[BYTES]) * rows * 6 // (int(largest["rows"]) * 5)


def refuses(free: int, goldens: list[dict[str, Any]], rows: int, min_free_gb: float) -> bool:
    return rows > 0 and free < estimate_bytes(goldens, rows, min_free_gb)


def status_path(database: Database, name: str) -> Path:
    return database.home / "perf-db" / f"{name}.json"


def log_path(database: Database, name: str) -> Path:
    return database.home / "perf-db" / f"{name}.log"


def read_status(database: Database, name: str) -> dict[str, Any]:
    path = status_path(database, name)
    return json.loads(path.read_text()) if path.exists() else {}


def write_status(database: Database, name: str, data: dict[str, Any]) -> None:
    path = status_path(database, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data) + "\n")


def mark_building(database: Database, name: str, pid: int) -> None:
    write_status(database, name, {STATE: BUILDING, STARTED: time.time(), "pid": pid})


def finish_status(database: Database, name: str, state: str, error: str) -> None:
    started = read_status(database, name).get(STARTED, time.time())
    write_status(database, name, {STATE: state, STARTED: started, "finished": time.time(), ERROR: error})


def golden_ready(database: Database, name: str) -> bool:
    code, _ = files(database, f"test -f /perf/goldens/{name}/READY")
    return code == 0


def golden_state(database: Database, name: str) -> str:
    if golden_ready(database, name):
        return READY
    return recorded_state(read_status(database, name))


def recorded_state(status: dict[str, Any]) -> str:
    if status.get(STATE) == BUILDING and process_alive(status.get("pid")):
        return BUILDING
    return FAILED if status.get(STATE) == FAILED else "missing"


def process_alive(pid: object) -> bool:
    if not isinstance(pid, int):
        return False
    try:
        os.kill(pid, ALIVE)
    except OSError:
        return False
    return True


def status_line(database: Database, tree: trees.Tree) -> str:
    name = golden_name(database, tree)
    state = golden_state(database, name)
    status = read_status(database, name)
    line = f"{tree.name} {database.image} {name} {state} {elapsed(status, state)}"
    return line + (f" {status[ERROR]}" if state == FAILED and status.get(ERROR) else "")


def elapsed(status: dict[str, Any], state: str) -> str:
    started = status.get(STARTED)
    if started is None or state == "missing":
        return "-"
    finished = time.time() if state == BUILDING else status.get("finished", time.time())
    return f"{int(finished - started)}s"


def build(database: Database, tree: trees.Tree) -> str:
    name = golden_name(database, tree)
    try:
        if golden_ready(database, name):
            return ""
        mark_building(database, name, os.getpid())
        print(f"   building {name} for the {tree.name} tree ({database.image}, rows = {database.rows})")
        build_golden(database, tree, name)
    except DatabaseError as error:
        discard_build(database, name)
        finish_status(database, name, FAILED, str(error))
        return str(error)
    finish_status(database, name, READY, "")
    print(f"   {name} ready")
    return ""


def build_golden(database: Database, tree: trees.Tree, name: str) -> None:
    seed = required_seed(database, name)
    container = database.golden_container(name)
    temporary = f"/perf/goldens/{name}.tmp"
    step(files(database, f"rm -rf {temporary} && mkdir -p /perf/goldens"), "preparing the golden directory")
    start_postgres(database, container, temporary, None, INIT_TIMEOUT)
    env = database_env(database, container, connection_url(database, host_port(database, container)))
    step(run(["bash", "-lc", database.migrate], cwd=tree.path, env=env, timeout=BUILD_TIMEOUT), "[perf.db] migrate")
    if seed is not None:
        seed_golden(database, tree, container, seed, env)
    step(psql(database, container, "VACUUM (ANALYZE)"), "VACUUM (ANALYZE)")
    step(psql(database, container, "CHECKPOINT"), "CHECKPOINT")
    step(docker(database, "stop", "-t", "600", container, timeout=900), f"stopping {container}")
    remove_container(database, container)
    finalise_golden(database, name)


def required_seed(database: Database, name: str) -> Path | None:
    if database.rows <= 0:
        return None
    seed = seed_script(database.root)
    if seed is None:
        raise DatabaseError(f"[perf.db] rows = {database.rows} needs a perf/seed script")
    check_disk(database, name)
    return seed


def seed_golden(database: Database, tree: trees.Tree, container: str, seed: Path, env: dict[str, str]) -> None:
    run_seed(database, tree, container, seed, env)
    short = short_tables(database, container)
    if short:
        raise DatabaseError(f"tables below {database.rows} rows after the seed: {', '.join(short)}")


def run_seed(database: Database, tree: trees.Tree, container: str, seed: Path, env: dict[str, str]) -> None:
    label = seed.relative_to(database.root).as_posix()
    if seed.name == SEED_SQL:
        command = [
            "exec",
            "-i",
            container,
            "psql",
            "-v",
            "ON_ERROR_STOP=1",
            "-q",
            "-v",
            f"rows={database.rows}",
            "-U",
            SUPERUSER,
            "-d",
            DATABASE,
        ]
        step(docker(database, *command, stdin=seed.read_text(), timeout=BUILD_TIMEOUT), label)
        return
    step(run([str(seed)], cwd=tree.path, env=env, timeout=BUILD_TIMEOUT), label)


def short_tables(database: Database, container: str) -> list[str]:
    listing = step(psql(database, container, TABLES_SQL), "listing tables")
    tables = seedable_tables(table_cells(listing), database.skip_tables)
    counts = [(name, count_rows(database, container, qualified)) for name, qualified in tables]
    return [f"{name} ({count})" for name, count in counts if count < database.rows]


def table_cells(listing: str) -> list[list[str]]:
    return [line.split("\t") for line in listing.splitlines()]


def seedable_tables(rows: list[list[str]], skip_tables: list[str]) -> list[tuple[str, str]]:
    return [(cells[0], cells[1]) for cells in rows if len(cells) == 2 and cells[0] not in skip_tables]


def count_rows(database: Database, container: str, qualified: str) -> int:
    output = step(psql(database, container, f"select count(*) from {qualified}"), f"counting {qualified}")
    return int(output.strip().splitlines()[-1])


def check_disk(database: Database, name: str) -> None:
    output = step(files(database, "mkdir -p /perf && df -B1 --output=avail /perf | tail -1"), "df")
    free = int(output.strip().splitlines()[-1])
    goldens = repo_goldens(database)
    if refuses(free, goldens, database.rows, database.min_free_gb):
        raise DatabaseError(disk_message(name, estimate_bytes(goldens, database.rows, database.min_free_gb), free))


def disk_message(name: str, needed: int, free: int) -> str:
    return (
        f"not enough disk for {name}: need ~{needed / GIB:.1f} GB, have {free / GIB:.1f} GB free on the Docker data root; "
        "run marestail perf db prune or lower [perf.db] rows"
    )


def repo_goldens(database: Database) -> list[dict[str, Any]]:
    _, output = files(database, "cat /perf/goldens/*/META.json 2>/dev/null; true")
    return [meta for meta in parse_metas(output) if meta.get("root") == str(database.root)]


def parse_metas(output: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in output.splitlines() if line.startswith("{")]


def finalise_golden(database: Database, name: str) -> None:
    final = f"/perf/goldens/{name}"
    output = step(files(database, f"rm -rf {final} && mv {final}.tmp {final} && du -sb {final} | cut -f1"), "moving the golden into place")
    meta = {
        "name": name,
        "root": str(database.root),
        "rows": database.rows,
        BYTES: int(output.strip().splitlines()[-1]),
        IMAGE: database.image,
        "built_at": time.strftime(META_TIME),
    }
    step(files(database, f"cat > {final}/META.json && touch {final}/READY", stdin=json.dumps(meta) + "\n"), "writing META.json")


def discard_build(database: Database, name: str) -> None:
    remove_container(database, database.golden_container(name))
    try:
        files(database, f"rm -rf /perf/goldens/{name}.tmp")
    except DatabaseError:
        return


def golden_meta(database: Database, name: str) -> dict[str, Any]:
    code, output = files(database, f"cat /perf/goldens/{name}/META.json")
    return json.loads(output) if code == 0 else {}


def start_build(database: Database, tree: trees.Tree) -> str:
    name = golden_name(database, tree)
    state = golden_state(database, name)
    if state in (READY, BUILDING):
        return f"{tree.name} {name} already {state}"
    log = log_path(database, name)
    log.parent.mkdir(parents=MKDIR_PARENTS, exist_ok=True)
    with log.open("a") as handle:
        child = subprocess.Popen(
            [sys.executable, str(CLI), "perf", "db", "golden", "--tree", tree.name, "--wait"],
            cwd=database.root,
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    mark_building(database, name, child.pid)
    return f"building {name} for the {tree.name} tree in the background; poll marestail perf db status; log: {log}"


def reset(database: Database, tree: trees.Tree) -> tuple[int, dict[str, str]]:
    name = golden_name(database, tree)
    if not golden_ready(database, name):
        raise DatabaseError(f"golden not ready: {status_line(database, tree)}")
    started = time.monotonic()
    container = database.tree_container(tree.name)
    remove_container(database, container)
    work = database.work_dir(tree.name)
    copy_golden(database, name, work)
    port = database.tree_port(tree.name)
    start_postgres(database, container, work, port, READY_TIMEOUT)
    reset_ms = round((time.monotonic() - started) * MILLISECONDS)
    return reset_ms, database_env(database, container, connection_url(database, port))


def copy_golden(database: Database, name: str, work: str) -> None:
    code, output = files(
        database,
        f"rm -rf {work} && mkdir -p /perf/work && cp -a --reflink=always /perf/goldens/{name} {work} && rm -f {work}/READY {work}/META.json",
    )
    if code != 0:
        raise DatabaseError(copy_failure(output))


def copy_failure(output: str) -> str:
    lowered = output.lower()
    if "reflink" in lowered or "not supported" in lowered:
        return REFLINK_FAILED
    return f"copying the golden failed: {' | '.join(tail(output, 3))}"


def prune(database: Database, needed: set[str]) -> list[str]:
    removed = []
    for meta in repo_goldens(database):
        name = str(meta["name"])
        if name in needed:
            continue
        step(files(database, f"rm -rf /perf/goldens/{name}"), f"removing {name}")
        status_path(database, name).unlink(missing_ok=True)
        log_path(database, name).unlink(missing_ok=True)
        removed.append(name)
    return removed


def down(database: Database) -> list[str]:
    _, output = docker(database, "ps", "-a", "--format", "{{.Names}}")
    names = [name for name in output.split() if name.startswith(database.prefix)]
    for name in names:
        remove_container(database, name)
    return names


@dataclass(frozen=True)
class Request:
    database: Database
    active: dict[str, trees.Tree]
    tree_name: str | None
    wait: bool


def command(action: str, tree_name: str | None, wait: bool) -> int:
    config = config_module.load(Path.cwd())
    database, problem = for_run(config)
    if database is None:
        return fail(problem, 2)
    request = Request(database, trees.active(config) or {}, tree_name, wait)
    try:
        return ACTIONS[action](request)
    except DatabaseError as error:
        return fail(str(error), 1)


def fail(message: str, code: int) -> int:
    sys.stderr.write(message + "\n")
    return code


def golden_action(request: Request) -> int:
    tree = request.active.get(request.tree_name or "")
    if tree is None:
        return fail(f"no tree {request.tree_name} in a perf run in progress", 2)
    return (build_now if request.wait else build_in_background)(request.database, tree)


def build_now(database: Database, tree: trees.Tree) -> int:
    problem = build(database, tree)
    return fail(problem, 1) if problem else 0


def build_in_background(database: Database, tree: trees.Tree) -> int:
    print(start_build(database, tree))
    return 0


def status_action(request: Request) -> int:
    if not request.active:
        return fail("no perf run in progress", 2)
    for tree in request.active.values():
        print(status_line(request.database, tree))
    return 0


def url_action(request: Request) -> int:
    if request.tree_name not in TREE_PORT_OFFSETS:
        return fail(f"unknown tree {request.tree_name}; choose from {', '.join(TREE_PORT_OFFSETS)}", 2)
    print(connection_url(request.database, request.database.tree_port(request.tree_name)))
    return 0


def prune_action(request: Request) -> int:
    removed = prune(request.database, {golden_name(request.database, tree) for tree in request.active.values()})
    print("\n".join(f"pruned {name}" for name in removed) or "nothing to prune")
    return 0


def down_action(request: Request) -> int:
    removed = down(request.database)
    print("\n".join(f"removed {name}" for name in removed) or "no performance database containers")
    return 0


ACTIONS = {"golden": golden_action, "status": status_action, "url": url_action, "prune": prune_action, "down": down_action}
