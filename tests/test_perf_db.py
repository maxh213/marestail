import json
import os
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from marestail.config import Config
from marestail.perf import db, trees
from tests.conftest import FakeRun, Reply

RUNNING = "{{.State.Running}}"


def make_db(tmp_path: Path, **changes: Any) -> db.Database:
    root = tmp_path / "repo"
    root.mkdir(exist_ok=True)
    base: dict[str, Any] = {
        "root": root,
        "migrate": "make migrate",
        "url_env": "DATABASE_URL",
        "rows": 1000,
        "rows_source": "test",
        "migrations": [],
        "skip_tables": ["schema_migrations"],
        "min_free_gb": 50.0,
        "image": "postgres:16-alpine",
        "image_source": "test",
        "port": 55432,
        "prefix": "mp-",
        "volume": "vol",
        "home": tmp_path / "home",
    }
    return db.Database(**(base | changes))


def rules(table: dict[str, Reply]) -> Callable[[list[str]], Reply]:
    merged = {RUNNING: (0, "true\n")} | table

    def reply(command: list[str]) -> Reply:
        text = " ".join(command)
        return next((answer for key, answer in merged.items() if key in text), (0, ""))

    return reply


def joined(fake: FakeRun) -> list[str]:
    return [" ".join(command) for command in fake.calls if RUNNING not in command]


def tree(tmp_path: Path, name: str = "head") -> trees.Tree:
    return trees.Tree(name, "abc123", tmp_path / name)


@pytest.fixture
def frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db.time, "time", lambda: 1000.0)
    monkeypatch.setattr(db.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(db.secrets, "token_urlsafe", lambda size: f"pw{size}")


def test_database_names(tmp_path: Path) -> None:
    database = make_db(tmp_path)
    key = database.repo_key
    assert len(key) == 12
    assert database.helper == "mp-files"
    assert database.tree_container("head") == f"mp-db-{key}-head"
    assert database.golden_container("g") == "mp-golden-g"
    assert [database.tree_port(name) for name in ("head", "baseline", "pre-marestail")] == [55432, 55433, 55434]
    assert database.work_dir("head") == f"/perf/work/{key}-head"


def test_build_database_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("PORT", "PREFIX", "VOLUME", "HOME"):
        monkeypatch.delenv(f"MARESTAIL_PERF_DB_{name}", raising=False)
    monkeypatch.setattr(db.Path, "home", lambda: tmp_path)
    database = db.build_database(Config(root=tmp_path, raw={}), {"migrate": "m"}, 5, "rs", "img", "is")
    assert database == db.Database(
        tmp_path,
        "m",
        "DATABASE_URL",
        5,
        "rs",
        [],
        db.DEFAULT_SKIP_TABLES,
        50.0,
        "img",
        "is",
        55432,
        "marestail-perf-",
        "marestail-perf-pgdata",
        tmp_path / ".config" / "marestail",
    )


def test_build_database_configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARESTAIL_PERF_DB_PREFIX", "p-")
    monkeypatch.setenv("MARESTAIL_PERF_DB_VOLUME", "v")
    monkeypatch.setenv("MARESTAIL_PERF_DB_HOME", str(tmp_path / "h"))
    monkeypatch.delenv("MARESTAIL_PERF_DB_PORT", raising=False)
    section = {"migrate": 7, "url_env": "PG", "migrations": ("db/**",), "skip_tables": ["t"], "min_free_gb": "2", "port": "6000"}
    database = db.build_database(Config(root=tmp_path, raw={}), section, 5, "rs", "img", "is")
    assert (database.migrate, database.url_env, database.migrations, database.skip_tables) == ("7", "PG", ["db/**"], ["t"])
    assert (database.min_free_gb, database.port, database.prefix, database.volume, database.home) == (2.0, 6000, "p-", "v", tmp_path / "h")
    monkeypatch.setenv("MARESTAIL_PERF_DB_PORT", "7000")
    assert db.build_database(Config(root=tmp_path, raw={}), section, 5, "rs", "img", "is").port == 7000


def perf_config(tmp_path: Path, section: dict[str, Any] | None = None) -> Config:
    return Config(root=tmp_path, raw={"perf": {"db": {"migrate": "m"} if section is None else section}})


def test_for_run_not_configured(tmp_path: Path) -> None:
    assert db.for_run(perf_config(tmp_path, {})) == (None, "configure [perf.db] migrate in marestail.toml")


def test_for_run_bad_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARESTAIL_PERF_DB_ROWS", "lots")
    assert db.for_run(perf_config(tmp_path)) == (None, "[perf.db] rows must be a whole number ≥ 0, got lots")


def test_for_run_uses_recorded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(trees, "recorded", lambda config: {"rows": "12", "image": "postgres:9"})
    database, problem = db.for_run(perf_config(tmp_path))
    assert problem == ""
    assert database is not None
    assert (database.rows, database.rows_source, database.image, database.image_source) == (12, "trees.json", "postgres:9", "trees.json")


def test_for_run_recorded_sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    recorded = {"rows": 0, "rows_source": "env", "image": "postgres:9", "image_source": "compose.yml"}
    monkeypatch.setattr(trees, "recorded", lambda config: recorded)
    database, _ = db.for_run(perf_config(tmp_path))
    assert database is not None
    assert (database.rows, database.rows_source, database.image, database.image_source) == (0, "env", "postgres:9", "compose.yml")


def test_for_run_falls_back_to_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MARESTAIL_PERF_DB_ROWS", raising=False)
    monkeypatch.setattr(trees, "recorded", lambda config: {"rows": None, "image": ""})
    database, _ = db.for_run(perf_config(tmp_path, {"migrate": "m", "rows": 3, "image": "postgres:11"}))
    assert database is not None
    assert (database.rows, database.rows_source, database.image, database.image_source) == (
        3,
        "marestail.toml",
        "postgres:11",
        "marestail.toml",
    )


@pytest.fixture
def session_calls(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    calls: list[Any] = []
    monkeypatch.setattr(trees, "write_trees", lambda config, session: calls.append(("write", session.image, session.rows)))
    monkeypatch.setattr(
        db, "build", lambda database, tree: calls.append(("build", tree.name, database.rows)) or ("bad" if tree.name == "baseline" else "")
    )
    return calls


def test_prepare_not_configured(tmp_path: Path, session_calls: list[Any]) -> None:
    db.prepare(perf_config(tmp_path, {}), trees.Session("t"))
    assert session_calls == []


def test_prepare_bad_rows_exits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, session_calls: list[Any]) -> None:
    monkeypatch.setenv("MARESTAIL_PERF_DB_ROWS", "x")
    config = perf_config(tmp_path)
    session = trees.Session("t")
    with pytest.raises(SystemExit, match=r"^\[perf.db\] rows must be a whole number ≥ 0, got x$"):
        db.prepare(config, session)
    assert session_calls == []


def test_prepare_without_seed_stops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, session_calls: list[Any], capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("MARESTAIL_PERF_DB_ROWS", "10")
    session = trees.Session("t", [tree(tmp_path)])
    db.prepare(perf_config(tmp_path), session)
    assert capsys.readouterr().out == "   no Postgres version found in the repo; using postgres:18\n"
    assert (session.image, session.image_source, session.rows, session.rows_source) == (
        "postgres:18",
        "default",
        10,
        "MARESTAIL_PERF_DB_ROWS",
    )
    assert session_calls == [("write", "postgres:18", 10)]


def test_prepare_builds_each_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, session_calls: list[Any], capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("MARESTAIL_PERF_DB_ROWS", "0")
    session = trees.Session("t", [tree(tmp_path), tree(tmp_path, "baseline")])
    db.prepare(perf_config(tmp_path, {"migrate": "m", "image": "postgres:15"}), session)
    assert capsys.readouterr().out == "   performance database image postgres:15 from marestail.toml\n"
    assert session_calls == [("write", "postgres:15", 0), ("build", "head", 0), ("build", "baseline", 0)]
    assert session.notes == ["golden for the baseline tree failed: bad"]


def test_prepare_with_seed_builds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, session_calls: list[Any]) -> None:
    monkeypatch.setenv("MARESTAIL_PERF_DB_ROWS", "4")
    (tmp_path / "perf").mkdir()
    (tmp_path / "perf" / "seed.sql").write_text("")
    db.prepare(perf_config(tmp_path), trees.Session("t", [tree(tmp_path)]))
    assert session_calls[1:] == [("build", "head", 4)]


def test_release_skips_without_image(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake = fake_run(db)
    db.release(perf_config(tmp_path), trees.Session("t"))
    db.release(perf_config(tmp_path, {}), trees.Session("t", image="postgres:16"))
    assert fake.calls == []


@pytest.mark.parametrize(("running", "extra"), [("true", True), ("false", False)])
def test_release_removes_containers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_run: Callable[..., FakeRun], running: str, extra: bool
) -> None:
    monkeypatch.setenv("MARESTAIL_PERF_DB_PREFIX", "mp-")
    monkeypatch.setenv("MARESTAIL_PERF_DB_ROWS", "0")
    fake = fake_run(db, rules({RUNNING: (0, running)}))
    session = trees.Session("t", [tree(tmp_path), tree(tmp_path, "baseline")], image="postgres:16")
    db.release(perf_config(tmp_path), session)
    key = make_db(tmp_path, root=tmp_path).repo_key
    expected = [f"docker rm -f -v mp-db-{key}-head", f"docker rm -f -v mp-db-{key}-baseline"]
    tail = [f"docker exec mp-files rm -rf /perf/work/{key}-head /perf/work/{key}-baseline"] if extra else []
    assert joined(fake) == expected + tail


def test_docker_passes_options(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake = fake_run(db, [(3, "out")])
    database = make_db(tmp_path)
    assert db.docker(database, "ps", stdin="in", timeout=5) == (3, "out")
    assert fake.calls == [["docker", "ps"]]
    assert fake.options == [{"cwd": database.root, "stdin": "in", "timeout": 5}]
    db.docker(database, "ps")
    assert fake.options[1] == {"cwd": database.root, "stdin": None, "timeout": 600}


def test_step() -> None:
    assert db.step((0, "fine"), "x") == "fine"
    output = "\n".join(str(number) for number in range(8))
    with pytest.raises(db.DatabaseError, match=r"^label failed \(exit 2\): 3 \| 4 \| 5 \| 6 \| 7$"):
        db.step((2, output), "label")


@pytest.mark.parametrize(("reply", "expected"), [((0, " true\n"), True), ((0, "false"), False), ((1, "true"), False)])
def test_helper_running(tmp_path: Path, fake_run: Callable[..., FakeRun], reply: Reply, expected: bool) -> None:
    fake = fake_run(db, [reply])
    assert db.helper_running(make_db(tmp_path)) is expected
    assert fake.calls == [["docker", "inspect", "-f", RUNNING, "mp-files"]]


def test_ensure_helper_running(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake = fake_run(db, rules({}))
    db.ensure_helper(make_db(tmp_path))
    assert len(fake.calls) == 1


def test_ensure_helper_starts(tmp_path: Path, fake_run: Callable[..., FakeRun], capsys: pytest.CaptureFixture[str]) -> None:
    fake = fake_run(db, rules({RUNNING: (1, ""), "image inspect": (1, "")}))
    db.ensure_helper(make_db(tmp_path))
    assert fake.calls[0] == ["docker", "inspect", "-f", RUNNING, "mp-files"]
    assert joined(fake) == [
        "docker rm -f -v mp-files",
        "docker image inspect postgres:16",
        "docker pull postgres:16",
        "docker run -d --name mp-files -v vol:/perf --entrypoint sleep postgres:16 infinity",
    ]
    assert fake.options[3]["timeout"] == db.BUILD_TIMEOUT
    assert capsys.readouterr().out == "   pulling postgres:16\n"


def test_ensure_helper_failure(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake_run(db, rules({RUNNING: (1, ""), "run -d": (125, "boom")}))
    database = make_db(tmp_path)
    with pytest.raises(db.DatabaseError, match=r"^starting the file helper failed \(exit 125\): boom$"):
        db.ensure_helper(database)


def test_ensure_image_pull_fails(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake_run(db, [(1, ""), (1, "denied")])
    database = make_db(tmp_path)
    with pytest.raises(db.DatabaseError, match=r"^docker pull img failed \(exit 1\): denied$"):
        db.ensure_image(database, "img")


def test_ensure_image_present(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake = fake_run(db, [(0, "")])
    db.ensure_image(make_db(tmp_path), "img")
    assert fake.calls == [["docker", "image", "inspect", "img"]]


def test_files(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake = fake_run(db, rules({"bash -c": (0, "done")}))
    database = make_db(tmp_path)
    assert db.files(database, "ls") == (0, "done")
    assert db.files(database, "cat", stdin="x") == (0, "done")
    assert joined(fake) == ["docker exec mp-files bash -c ls", "docker exec -i mp-files bash -c cat"]
    assert [fake.options[1]["stdin"], fake.options[3]["stdin"], fake.options[3]["timeout"]] == [None, "x", db.BUILD_TIMEOUT]


def test_password_created_once(tmp_path: Path, frozen: None) -> None:
    database = make_db(tmp_path)
    assert db.password(database) == "pw24"
    path = tmp_path / "home" / "perf-db.json"
    assert json.loads(path.read_text()) == {"password": "pw24"}
    assert path.stat().st_mode & 0o777 == 0o600
    path.write_text(json.dumps({"password": "kept"}))
    assert db.password(database) == "kept"


def test_connection_url_and_env(tmp_path: Path, frozen: None) -> None:
    database = make_db(tmp_path, url_env="PG_URL", rows=7)
    url = db.connection_url(database, 5)
    assert url == "postgresql://postgres:pw24@127.0.0.1:5/bench"
    assert db.database_env(database, "c", url) == {
        "MARESTAIL_PERF_DATABASE_URL": url,
        "MARESTAIL_PERF_DATABASE": "bench",
        "MARESTAIL_PERF_DB_CONTAINER": "c",
        "MARESTAIL_PERF_ROWS": "7",
        "PG_URL": url,
    }


@pytest.mark.parametrize(("port", "publish"), [(5433, "127.0.0.1:5433:5432"), (None, "127.0.0.1::5432")])
def test_start_postgres(tmp_path: Path, fake_run: Callable[..., FakeRun], frozen: None, port: int | None, publish: str) -> None:
    fake = fake_run(db, rules({}))
    db.start_postgres(make_db(tmp_path), "box", "/perf/x", port, 9)
    assert joined(fake) == [
        "docker image inspect postgres:16-alpine",
        f"docker run -d --name box -v vol:/perf -p {publish} -e POSTGRES_PASSWORD=pw24 -e POSTGRES_DB=bench -e PGDATA=/perf/x postgres:16-alpine",
        "docker exec box pg_isready -q -h 127.0.0.1 -U postgres",
    ]


def test_start_postgres_failure(tmp_path: Path, fake_run: Callable[..., FakeRun], frozen: None) -> None:
    fake_run(db, rules({"run -d": (1, "port taken")}))
    database = make_db(tmp_path)
    with pytest.raises(db.DatabaseError, match=r"^starting box failed \(exit 1\): port taken$"):
        db.start_postgres(database, "box", "/perf/x", None, 9)


def clock(monkeypatch: pytest.MonkeyPatch, ticks: list[float]) -> None:
    values: Iterator[float] = iter(ticks)
    monkeypatch.setattr(db.time, "monotonic", lambda: next(values))
    monkeypatch.setattr(db.time, "sleep", lambda seconds: None)


def test_wait_ready_retries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_run: Callable[..., FakeRun]) -> None:
    clock(monkeypatch, [0.0, 1.0, 2.0])
    fake = fake_run(db, [(2, "no response"), (0, "")])
    db.wait_ready(make_db(tmp_path), "box", 10)
    assert len(fake.calls) == 2


def test_wait_ready_stopped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_run: Callable[..., FakeRun]) -> None:
    clock(monkeypatch, [0.0, 1.0])
    fake = fake_run(db, [(1, "container box is not running"), (0, "a\nb\n")])
    database = make_db(tmp_path)
    with pytest.raises(db.DatabaseError, match=r"^box stopped before it was ready: a \| b$"):
        db.wait_ready(database, "box", 10)
    assert fake.calls[1] == ["docker", "logs", "--tail", "20", "box"]


def test_wait_ready_times_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_run: Callable[..., FakeRun]) -> None:
    clock(monkeypatch, [0.0, 5.0, 10.0])
    fake = fake_run(db, [(2, "")])
    database = make_db(tmp_path)
    with pytest.raises(db.DatabaseError, match=r"^box was not ready after 10s$"):
        db.wait_ready(database, "box", 10)
    assert len(fake.calls) == 1


def test_host_port(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake = fake_run(db, [(0, "0.0.0.0:32768\n[::]:32768\n")])
    assert db.host_port(make_db(tmp_path), "box") == "32768"
    assert fake.calls == [["docker", "port", "box", "5432/tcp"]]


def test_psql(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake = fake_run(db, [(0, "1")])
    assert db.psql(make_db(tmp_path), "box", "select 1") == (0, "1")
    assert fake.calls == [
        ["docker", "exec", "box", "psql", "-v", "ON_ERROR_STOP=1", "-qAt", "-F", "\t", "-U", "postgres", "-d", "bench", "-c", "select 1"]
    ]
    assert fake.options[0]["timeout"] == db.BUILD_TIMEOUT


def executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return path


def test_seed_script_none(tmp_path: Path) -> None:
    assert db.seed_script(tmp_path) is None
    (tmp_path / "perf").mkdir()
    (tmp_path / "perf" / "seed.py").write_text("")
    (tmp_path / "perf" / "seeds").mkdir()
    executable(tmp_path / "perf" / "seedling")
    assert db.seed_script(tmp_path) is None


def test_seed_script_prefers_sql(tmp_path: Path) -> None:
    executable(tmp_path / "perf" / "seed")
    assert db.seed_script(tmp_path) == tmp_path / "perf" / "seed"
    executable(tmp_path / "perf" / "seed.a.sh")
    assert db.seed_script(tmp_path) == tmp_path / "perf" / "seed"
    (tmp_path / "perf" / "seed.sql").write_text("")
    assert db.seed_script(tmp_path) == tmp_path / "perf" / "seed.sql"


def test_seed_script_sorted(tmp_path: Path) -> None:
    executable(tmp_path / "perf" / "seed.sh")
    executable(tmp_path / "perf" / "seed.py")
    assert db.seed_script(tmp_path) == tmp_path / "perf" / "seed.py"


def test_golden_hash() -> None:
    assert db.golden_hash("r", "i", "x", b"s", 1) == "golden_550b299af98b782e"
    assert db.golden_hash("ri", "", "x", b"s", 1) != db.golden_hash("r", "i", "x", b"s", 1)


def test_golden_name_depends_on_seed(tmp_path: Path) -> None:
    database = make_db(tmp_path)
    head = tree(tmp_path)
    plain = db.golden_name(database, head)
    assert plain == db.golden_hash(str(database.root), "postgres:16-alpine", "abc123", b"", 1000)
    (database.root / "perf").mkdir()
    (database.root / "perf" / "seed.sql").write_text("insert")
    assert db.golden_name(database, head) == db.golden_hash(str(database.root), "postgres:16-alpine", "abc123", b"insert", 1000)
    empty = make_db(tmp_path, rows=0)
    assert db.golden_name(empty, head) == db.golden_hash(str(database.root), "postgres:16-alpine", "abc123", b"", 0)


def test_schema_identity_uses_migrations(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    listing = "100644 blob a1\tdb/migrate/1.sql\n100644 blob b2\tsrc/app.py\nno tab here\n100644 blob c3\tdb/migrate/2.sql\n"
    fake = fake_run(db, [(0, listing)])
    database = make_db(tmp_path, migrations=["db/migrate/*.sql"])
    assert db.schema_identity(database, tree(tmp_path)) == "100644 blob a1\tdb/migrate/1.sql\n100644 blob c3\tdb/migrate/2.sql"
    assert fake.calls == [["git", "ls-tree", "-r", "abc123"]]
    assert fake.options[0]["cwd"] == database.root


def test_schema_identity_without_migrations(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake = fake_run(db)
    assert db.schema_identity(make_db(tmp_path), tree(tmp_path)) == "abc123"
    assert fake.calls == []


def test_estimate_and_refuses() -> None:
    prior = [{"rows": 1000000, "bytes": 250000000}, {"rows": 0, "bytes": 900000000}, {"bytes": 1}, {"rows": 10, "bytes": 10}]
    assert db.estimate_bytes(prior, 10000000, 50) == 3000000000
    assert db.estimate_bytes([], 10, 1.5) == int(1.5 * 1024**3)
    assert db.estimate_bytes([{"rows": 0, "bytes": 5}], 10, 2) == 2 * 1024**3
    assert db.refuses(2999999999, prior, 10000000, 50) is True
    assert db.refuses(3000000000, prior, 10000000, 50) is False
    assert db.refuses(0, prior, 0, 50) is False


def test_disk_message() -> None:
    assert db.disk_message("g", 3 * 1024**3, 1024**3) == (
        "not enough disk for g: need ~3.0 GB, have 1.0 GB free on the Docker data root; run marestail perf db prune or lower [perf.db] rows"
    )


def test_status_files(tmp_path: Path, frozen: None) -> None:
    database = make_db(tmp_path)
    assert db.status_path(database, "g") == tmp_path / "home" / "perf-db" / "g.json"
    assert db.log_path(database, "g") == tmp_path / "home" / "perf-db" / "g.log"
    assert db.read_status(database, "g") == {}
    db.write_status(database, "g", {"state": "building", "started": 10})
    assert db.status_path(database, "g").read_text() == '{"state": "building", "started": 10}\n'
    db.finish_status(database, "g", "failed", "why")
    assert db.read_status(database, "g") == {"state": "failed", "started": 10, "finished": 1000.0, "error": "why"}
    db.finish_status(database, "h", "ready", "")
    assert db.read_status(database, "h") == {"state": "ready", "started": 1000.0, "finished": 1000.0, "error": ""}
    db.mark_building(database, "i", 42)
    assert db.read_status(database, "i") == {"state": "building", "started": 1000.0, "pid": 42}


@pytest.mark.parametrize(("code", "expected"), [(0, True), (1, False)])
def test_golden_ready(tmp_path: Path, fake_run: Callable[..., FakeRun], code: int, expected: bool) -> None:
    fake = fake_run(db, rules({"test -f": (code, "")}))
    assert db.golden_ready(make_db(tmp_path), "g") is expected
    assert joined(fake) == ["docker exec mp-files bash -c test -f /perf/goldens/g/READY"]


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ({}, "missing"),
        ({"state": "building", "pid": os.getpid()}, "building"),
        ({"state": "building", "pid": "nope"}, "missing"),
        ({"state": "building"}, "missing"),
        ({"state": "failed", "pid": os.getpid()}, "failed"),
        ({"state": "ready"}, "missing"),
    ],
)
def test_recorded_state(status: dict[str, Any], expected: str) -> None:
    assert db.recorded_state(status) == expected


def test_process_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    assert db.process_alive(os.getpid()) is True
    assert db.process_alive(None) is False

    def refuse(pid: int, signal: int) -> None:
        raise ProcessLookupError(pid)

    monkeypatch.setattr(db.os, "kill", refuse)
    assert db.process_alive(1) is False


def test_golden_state(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    database = make_db(tmp_path)
    fake_run(db, rules({"test -f": (0, "")}))
    assert db.golden_state(database, "g") == "ready"
    fake_run(db, rules({"test -f": (1, "")}))
    db.write_status(database, "g", {"state": "failed"})
    assert db.golden_state(database, "g") == "failed"


@pytest.mark.parametrize(
    ("status", "state", "expected"),
    [
        ({}, "building", "-"),
        ({"started": 10}, "missing", "-"),
        ({"started": 10}, "building", "990s"),
        ({"started": 10, "finished": 15.9}, "ready", "5s"),
        ({"started": 10}, "failed", "990s"),
    ],
)
def test_elapsed(frozen: None, status: dict[str, Any], state: str, expected: str) -> None:
    assert db.elapsed(status, state) == expected


@pytest.mark.parametrize(
    ("status", "ready", "suffix"),
    [
        ({"state": "failed", "started": 10, "finished": 20, "error": "boom"}, 1, "failed 10s boom"),
        ({"state": "failed", "started": 10, "finished": 20, "error": ""}, 1, "failed 10s"),
        ({"state": "failed", "started": 10, "finished": 20}, 1, "failed 10s"),
        ({"state": "ready", "started": 10, "finished": 20, "error": "old"}, 0, "ready 10s"),
    ],
)
def test_status_line(tmp_path: Path, fake_run: Callable[..., FakeRun], status: dict[str, Any], ready: int, suffix: str) -> None:
    fake_run(db, rules({"test -f": (ready, "")}))
    database = make_db(tmp_path)
    head = tree(tmp_path)
    name = db.golden_name(database, head)
    db.write_status(database, name, status)
    assert db.status_line(database, head) == f"head postgres:16-alpine {name} {suffix}"


def test_build_already_ready(tmp_path: Path, fake_run: Callable[..., FakeRun], capsys: pytest.CaptureFixture[str]) -> None:
    fake = fake_run(db, rules({"test -f": (0, "")}))
    assert db.build(make_db(tmp_path), tree(tmp_path)) == ""
    assert len(joined(fake)) == 1
    assert capsys.readouterr().out == ""


def test_build_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_run: Callable[..., FakeRun], frozen: None, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_run(db, rules({"test -f": (1, "")}))
    database = make_db(tmp_path)
    seen: list[Any] = []
    monkeypatch.setattr(db, "build_golden", lambda database, tree, name: seen.append((tree.name, name, db.read_status(database, name))))
    assert db.build(database, tree(tmp_path)) == ""
    name = db.golden_name(database, tree(tmp_path))
    assert seen == [("head", name, {"state": "building", "started": 1000.0, "pid": os.getpid()})]
    assert db.read_status(database, name) == {"state": "ready", "started": 1000.0, "finished": 1000.0, "error": ""}
    assert capsys.readouterr().out == f"   building {name} for the head tree (postgres:16-alpine, rows = 1000)\n   {name} ready\n"


def test_build_failure_discards(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_run: Callable[..., FakeRun], frozen: None) -> None:
    fake = fake_run(db, rules({"test -f": (1, ""), ".tmp": (1, "busy")}))
    database = make_db(tmp_path)

    def explode(database: db.Database, tree: trees.Tree, name: str) -> None:
        raise db.DatabaseError("no space")

    monkeypatch.setattr(db, "build_golden", explode)
    assert db.build(database, tree(tmp_path)) == "no space"
    name = db.golden_name(database, tree(tmp_path))
    assert db.read_status(database, name) == {"state": "failed", "started": 1000.0, "finished": 1000.0, "error": "no space"}
    assert joined(fake)[-2:] == [f"docker rm -f -v mp-golden-{name}", f"docker exec mp-files bash -c rm -rf /perf/goldens/{name}.tmp"]


def test_discard_build_ignores_helper_failure(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake = fake_run(db, rules({RUNNING: (1, ""), "run -d": (1, "")}))
    db.discard_build(make_db(tmp_path), "g")
    assert joined(fake)[0] == "docker rm -f -v mp-golden-g"


def golden_rules(extra: dict[str, Reply] | None = None) -> Callable[[list[str]], Reply]:
    table = {
        "df -B1": (0, "Filesystem\n999999999999\n"),
        "cat /perf/goldens/*": (0, ""),
        "port mp-golden": (0, "0.0.0.0:40000\n"),
        "from pg_tables": (0, "a\tpublic.a\nschema_migrations\tpublic.schema_migrations\nodd\n"),
        "count(*)": (0, "1000\n"),
        "du -sb": (0, "4096\n"),
    }
    return rules(table | (extra or {}))


def test_build_golden_seeded(tmp_path: Path, fake_run: Callable[..., FakeRun], frozen: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db.time, "strftime", lambda pattern: "2026-09-18T00:00:00+0000")
    fake = fake_run(db, golden_rules())
    database = make_db(tmp_path)
    (database.root / "perf").mkdir()
    (database.root / "perf" / "seed.sql").write_text("insert")
    db.build_golden(database, tree(tmp_path), "g")
    commands = joined(fake)
    assert commands[:3] == [
        "docker exec mp-files bash -c mkdir -p /perf && df -B1 --output=avail /perf | tail -1",
        "docker exec mp-files bash -c cat /perf/goldens/*/META.json 2>/dev/null; true",
        "docker exec mp-files bash -c rm -rf /perf/goldens/g.tmp && mkdir -p /perf/goldens",
    ]
    assert commands[3:5] == [
        "docker image inspect postgres:16-alpine",
        "docker run -d --name mp-golden-g -v vol:/perf -p 127.0.0.1::5432 -e POSTGRES_PASSWORD=pw24 -e POSTGRES_DB=bench "
        "-e PGDATA=/perf/goldens/g.tmp postgres:16-alpine",
    ]
    assert commands[6:] == [
        "docker port mp-golden-g 5432/tcp",
        "bash -lc make migrate",
        "docker exec -i mp-golden-g psql -v ON_ERROR_STOP=1 -q -v rows=1000 -U postgres -d bench",
        "docker exec mp-golden-g psql -v ON_ERROR_STOP=1 -qAt -F \t -U postgres -d bench -c " + db.TABLES_SQL,
        "docker exec mp-golden-g psql -v ON_ERROR_STOP=1 -qAt -F \t -U postgres -d bench -c select count(*) from public.a",
        "docker exec mp-golden-g psql -v ON_ERROR_STOP=1 -qAt -F \t -U postgres -d bench -c VACUUM (ANALYZE)",
        "docker exec mp-golden-g psql -v ON_ERROR_STOP=1 -qAt -F \t -U postgres -d bench -c CHECKPOINT",
        "docker stop -t 600 mp-golden-g",
        "docker rm -f -v mp-golden-g",
        "docker exec mp-files bash -c rm -rf /perf/goldens/g && mv /perf/goldens/g.tmp /perf/goldens/g && du -sb /perf/goldens/g | cut -f1",
        "docker exec -i mp-files bash -c cat > /perf/goldens/g/META.json && touch /perf/goldens/g/READY",
    ]
    meta = {
        "name": "g",
        "root": str(database.root),
        "rows": 1000,
        "bytes": 4096,
        "image": "postgres:16-alpine",
        "built_at": "2026-09-18T00:00:00+0000",
    }
    assert fake.options[-1]["stdin"] == json.dumps(meta) + "\n"
    migrate = fake.options[[" ".join(call) for call in fake.calls].index("bash -lc make migrate")]
    assert migrate["cwd"] == tmp_path / "head"
    assert migrate["env"]["MARESTAIL_PERF_DATABASE_URL"] == "postgresql://postgres:pw24@127.0.0.1:40000/bench"
    assert migrate["timeout"] == db.BUILD_TIMEOUT


def test_build_golden_empty(tmp_path: Path, fake_run: Callable[..., FakeRun], frozen: None) -> None:
    fake = fake_run(db, golden_rules())
    db.build_golden(make_db(tmp_path, rows=0), tree(tmp_path), "g")
    commands = joined(fake)
    assert commands[0] == "docker exec mp-files bash -c rm -rf /perf/goldens/g.tmp && mkdir -p /perf/goldens"
    assert not any("rows=" in command or "df -B1" in command for command in commands)


def test_build_golden_needs_seed(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake = fake_run(db, golden_rules())
    database = make_db(tmp_path)
    head = tree(tmp_path)
    with pytest.raises(db.DatabaseError, match=r"^\[perf.db\] rows = 1000 needs a perf/seed script$"):
        db.build_golden(database, head, "g")
    assert fake.calls == []


def test_build_golden_short_tables(tmp_path: Path, fake_run: Callable[..., FakeRun], frozen: None) -> None:
    fake_run(db, golden_rules({"count(*)": (0, "10\n")}))
    database = make_db(tmp_path)
    executable(database.root / "perf" / "seed")
    head = tree(tmp_path)
    with pytest.raises(db.DatabaseError, match=r"^tables below 1000 rows after the seed: a \(10\)$"):
        db.build_golden(database, head, "g")


def test_build_golden_migrate_fails(tmp_path: Path, fake_run: Callable[..., FakeRun], frozen: None) -> None:
    fake_run(db, golden_rules({"make migrate": (2, "syntax")}))
    database = make_db(tmp_path, rows=0)
    head = tree(tmp_path)
    with pytest.raises(db.DatabaseError, match=r"^\[perf.db\] migrate failed \(exit 2\): syntax$"):
        db.build_golden(database, head, "g")


def test_run_seed_executable(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake = fake_run(db, [(0, "")])
    database = make_db(tmp_path)
    seed = executable(database.root / "perf" / "seed")
    db.run_seed(database, tree(tmp_path), "box", seed, {"A": "1"})
    assert fake.calls == [[str(seed)]]
    assert fake.options == [{"cwd": tmp_path / "head", "env": {"A": "1"}, "timeout": db.BUILD_TIMEOUT}]


def test_run_seed_failure_label(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake = fake_run(db, [(3, "bad sql")])
    database = make_db(tmp_path, rows=5)
    seed = database.root / "perf" / "seed.sql"
    seed.parent.mkdir()
    seed.write_text("insert :rows")
    head = tree(tmp_path)
    with pytest.raises(db.DatabaseError, match=r"^perf/seed.sql failed \(exit 3\): bad sql$"):
        db.run_seed(database, head, "box", seed, {})
    assert fake.calls[0][-6:] == ["-v", "rows=5", "-U", "postgres", "-d", "bench"]
    assert fake.options[0]["stdin"] == "insert :rows"


def test_seedable_tables() -> None:
    cells = db.table_cells("a\tpublic.a\nskip\tpublic.skip\nodd\nb\tpublic.b\textra\n")
    assert db.seedable_tables(cells, ["skip"]) == [("a", "public.a")]


def test_check_disk_refuses(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    database = make_db(tmp_path, rows=10000000)
    meta = {"name": "old", "root": str(database.root), "rows": 1000000, "bytes": 250000000}
    other = {"name": "x", "root": "/elsewhere", "rows": 1, "bytes": 10**15}
    listing = json.dumps(meta) + "\n" + json.dumps(other) + "\nnoise\n"
    fake_run(db, rules({"df -B1": (0, "Avail\n2999999999\n"), "META.json": (0, listing)}))
    with pytest.raises(db.DatabaseError, match=r"^not enough disk for g: need ~2.8 GB, have 2.8 GB free"):
        db.check_disk(database, "g")
    fake_run(db, rules({"df -B1": (0, "3000000000"), "META.json": (0, listing)}))
    db.check_disk(database, "g")


def test_repo_goldens(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    database = make_db(tmp_path)
    listing = json.dumps({"name": "a", "root": str(database.root)}) + '\n{"name": "b"}\ncat: no such file\n'
    fake_run(db, rules({"META.json": (1, listing)}))
    assert db.repo_goldens(database) == [{"name": "a", "root": str(database.root)}]


@pytest.mark.parametrize(("reply", "expected"), [((0, '{"rows": 3}\n'), {"rows": 3}), ((1, "missing"), {})])
def test_golden_meta(tmp_path: Path, fake_run: Callable[..., FakeRun], reply: Reply, expected: dict[str, Any]) -> None:
    fake = fake_run(db, rules({"META.json": reply}))
    assert db.golden_meta(make_db(tmp_path), "g") == expected
    assert joined(fake) == ["docker exec mp-files bash -c cat /perf/goldens/g/META.json"]


class FakeChild:
    pid = 4242


def test_start_build_background(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_run: Callable[..., FakeRun], frozen: None) -> None:
    fake_run(db, rules({"test -f": (1, "")}))
    launched: list[Any] = []

    def popen(command: list[str], **options: Any) -> FakeChild:
        launched.append((command, options["cwd"], options["stderr"], options["start_new_session"], options["stdout"].name))
        return FakeChild()

    monkeypatch.setattr(db.subprocess, "Popen", popen)
    database = make_db(tmp_path)
    name = db.golden_name(database, tree(tmp_path))
    log = tmp_path / "home" / "perf-db" / f"{name}.log"
    assert db.start_build(database, tree(tmp_path)) == (
        f"building {name} for the head tree in the background; poll marestail perf db status; log: {log}"
    )
    command = [db.sys.executable, str(db.CLI), "perf", "db", "golden", "--tree", "head", "--wait"]
    assert launched == [(command, database.root, db.subprocess.STDOUT, True, str(log))]
    assert db.read_status(database, name) == {"state": "building", "started": 1000.0, "pid": 4242}


@pytest.mark.parametrize(("ready", "state"), [(0, "ready"), (1, "building")])
def test_start_build_skips(tmp_path: Path, fake_run: Callable[..., FakeRun], ready: int, state: str) -> None:
    fake_run(db, rules({"test -f": (ready, "")}))
    database = make_db(tmp_path)
    name = db.golden_name(database, tree(tmp_path))
    db.write_status(database, name, {"state": "building", "pid": os.getpid()})
    assert db.start_build(database, tree(tmp_path)) == f"head {name} already {state}"


def test_reset_not_ready(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake_run(db, rules({"test -f": (1, "")}))
    database = make_db(tmp_path)
    name = db.golden_name(database, tree(tmp_path))
    head = tree(tmp_path)
    with pytest.raises(db.DatabaseError, match=f"^golden not ready: head postgres:16-alpine {name} missing -$"):
        db.reset(database, head)


@pytest.mark.parametrize(
    ("output", "message"),
    [
        ("cp: failed to clone: Operation not supported", db.REFLINK_FAILED),
        ("cp: --reflink unavailable", db.REFLINK_FAILED),
        ("a\nb\nc\nd", "copying the golden failed: b | c | d"),
    ],
)
def test_reset_copy_fails(tmp_path: Path, fake_run: Callable[..., FakeRun], output: str, message: str) -> None:
    fake_run(db, rules({"cp -a": (1, output)}))
    database = make_db(tmp_path)
    head = tree(tmp_path)
    with pytest.raises(db.DatabaseError) as raised:
        db.reset(database, head)
    assert str(raised.value) == message


def test_reset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_run: Callable[..., FakeRun], frozen: None) -> None:
    clock(monkeypatch, [10.0, 10.0, 10.0, 10.25])
    fake = fake_run(db, rules({}))
    database = make_db(tmp_path)
    name = db.golden_name(database, tree(tmp_path))
    work = database.work_dir("head")
    container = database.tree_container("head")
    reset_ms, env = db.reset(database, tree(tmp_path))
    assert reset_ms == 250
    assert env == db.database_env(database, container, "postgresql://postgres:pw24@127.0.0.1:55432/bench")
    assert joined(fake)[1:4] == [
        f"docker rm -f -v {container}",
        f"docker exec mp-files bash -c rm -rf {work} && mkdir -p /perf/work && cp -a --reflink=always /perf/goldens/{name} {work} "
        f"&& rm -f {work}/READY {work}/META.json",
        "docker image inspect postgres:16-alpine",
    ]
    assert "-p 127.0.0.1:55432:5432" in joined(fake)[4]


def test_prune(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    database = make_db(tmp_path)
    listing = "\n".join(json.dumps({"name": name, "root": str(database.root)}) for name in ("keep", "drop"))
    fake = fake_run(db, rules({"META.json": (0, listing)}))
    db.write_status(database, "drop", {})
    db.log_path(database, "drop").write_text("log")
    db.write_status(database, "keep", {})
    assert db.prune(database, {"keep"}) == ["drop"]
    assert joined(fake)[-1] == "docker exec mp-files bash -c rm -rf /perf/goldens/drop"
    assert not db.status_path(database, "drop").exists()
    assert not db.log_path(database, "drop").exists()
    assert db.status_path(database, "keep").exists()


def test_prune_failure(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    database = make_db(tmp_path)
    fake_run(db, rules({"META.json": (0, json.dumps({"name": "x", "root": str(database.root)})), "rm -rf": (1, "busy")}))
    with pytest.raises(db.DatabaseError, match=r"^removing x failed \(exit 1\): busy$"):
        db.prune(database, set())


def test_down(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake = fake_run(db, [(0, "mp-files\nother\nmp-db-1\n")])
    assert db.down(make_db(tmp_path)) == ["mp-files", "mp-db-1"]
    assert joined(fake) == ["docker ps -a --format {{.Names}}", "docker rm -f -v mp-files", "docker rm -f -v mp-db-1"]


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, frozen: None) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "marestail.toml").write_text('[perf.db]\nmigrate = "true"\nrows = 0\n')
    monkeypatch.chdir(root)
    monkeypatch.delenv("MARESTAIL_PERF_DB_ROWS", raising=False)
    monkeypatch.setenv("MARESTAIL_PERF_DB_PREFIX", "mp-")
    monkeypatch.setenv("MARESTAIL_PERF_DB_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("MARESTAIL_PERF_DB_PORT", raising=False)
    return root


def activate(monkeypatch: pytest.MonkeyPatch, active: dict[str, trees.Tree] | None) -> None:
    monkeypatch.setattr(trees, "active", lambda config: active)


def test_command_not_configured(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (project / "marestail.toml").write_text("[perf]\n")
    assert db.command("status", None, False) == 2
    assert capsys.readouterr().err == "configure [perf.db] migrate in marestail.toml\n"


def test_status_without_run(project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    activate(monkeypatch, None)
    assert db.command("status", None, False) == 2
    assert capsys.readouterr().err == "no perf run in progress\n"


def test_status_lists_trees(
    project: Path, monkeypatch: pytest.MonkeyPatch, fake_run: Callable[..., FakeRun], capsys: pytest.CaptureFixture[str]
) -> None:
    activate(monkeypatch, {"head": tree(project), "baseline": tree(project, "baseline")})
    fake_run(db, rules({"test -f": (0, "")}))
    assert db.command("status", None, False) == 0
    lines = capsys.readouterr().out.splitlines()
    assert [line.split()[0] for line in lines] == ["head", "baseline"]
    assert all(line.endswith(" ready -") for line in lines)


@pytest.mark.parametrize(("tree_name", "port"), [("head", 55432), ("pre-marestail", 55434)])
def test_url(project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tree_name: str, port: int) -> None:
    activate(monkeypatch, {})
    assert db.command("url", tree_name, False) == 0
    assert capsys.readouterr().out == f"postgresql://postgres:pw24@127.0.0.1:{port}/bench\n"


@pytest.mark.parametrize("tree_name", ["nope", None])
def test_url_unknown(project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tree_name: str | None) -> None:
    activate(monkeypatch, {})
    assert db.command("url", tree_name, False) == 2
    assert capsys.readouterr().err == f"unknown tree {tree_name}; choose from head, baseline, pre-marestail\n"


@pytest.mark.parametrize("tree_name", ["baseline", None])
def test_golden_unknown_tree(
    project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tree_name: str | None
) -> None:
    activate(monkeypatch, {"head": tree(project)})
    assert db.command("golden", tree_name, True) == 2
    assert capsys.readouterr().err == f"no tree {tree_name} in a perf run in progress\n"


@pytest.mark.parametrize(("problem", "code", "err"), [("", 0, ""), ("broken", 1, "broken\n")])
def test_golden_wait(
    project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], problem: str, code: int, err: str
) -> None:
    activate(monkeypatch, {"head": tree(project)})
    monkeypatch.setattr(db, "build", lambda database, tree: problem)
    assert db.command("golden", "head", True) == code
    assert capsys.readouterr().err == err


def test_golden_background(project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    activate(monkeypatch, {"head": tree(project)})
    monkeypatch.setattr(db, "start_build", lambda database, tree: f"started {tree.name}")
    assert db.command("golden", "head", False) == 0
    assert capsys.readouterr().out == "started head\n"


def test_prune_action(
    project: Path, monkeypatch: pytest.MonkeyPatch, fake_run: Callable[..., FakeRun], capsys: pytest.CaptureFixture[str]
) -> None:
    activate(monkeypatch, {"head": tree(project)})
    kept: list[set[str]] = []
    monkeypatch.setattr(db, "prune", lambda database, needed: kept.append(needed) or ["a", "b"])
    assert db.command("prune", None, False) == 0
    assert capsys.readouterr().out == "pruned a\npruned b\n"
    assert [len(names) for names in kept] == [1]
    monkeypatch.setattr(db, "prune", lambda database, needed: [])
    assert db.command("prune", None, False) == 0
    assert capsys.readouterr().out == "nothing to prune\n"


def test_down_action(
    project: Path, monkeypatch: pytest.MonkeyPatch, fake_run: Callable[..., FakeRun], capsys: pytest.CaptureFixture[str]
) -> None:
    activate(monkeypatch, None)
    fake_run(db, [(0, "mp-a\n")])
    assert db.command("down", None, False) == 0
    assert capsys.readouterr().out == "removed mp-a\n"
    fake_run(db, [(0, "")])
    assert db.command("down", None, False) == 0
    assert capsys.readouterr().out == "no performance database containers\n"


def test_command_reports_database_error(
    project: Path, monkeypatch: pytest.MonkeyPatch, fake_run: Callable[..., FakeRun], capsys: pytest.CaptureFixture[str]
) -> None:
    activate(monkeypatch, {"head": tree(project)})
    fake_run(db, rules({"META.json": (0, "")}))

    def broken(database: db.Database, needed: set[str]) -> list[str]:
        raise db.DatabaseError("docker gone")

    monkeypatch.setattr(db, "prune", broken)
    assert db.command("prune", None, False) == 1
    assert capsys.readouterr().err == "docker gone\n"
