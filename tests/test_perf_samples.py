import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from marestail import config as config_module
from marestail.perf import db as perf_db
from marestail.perf import hygiene, samples, trees

BENCH = "perf/bench_a"
GOOD = '{"target": "t", "unit": "ms", "better": "lower", "value": 3}'


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "marestail.toml").write_text("[perf]\nsample_timeout = 7\n")
    (tmp_path / "wt").mkdir()
    monkeypatch.chdir(tmp_path)
    return tmp_path


def start_session(root: Path) -> None:
    config = config_module.load(root)
    session = trees.Session("task", [trees.Tree("head", "sha1", root / "wt"), trees.Tree("baseline", "sha0", root / "wt")])
    trees.write_trees(config, session)


def bench(root: Path, body: str, name: str = "bench_a", mode: int = 0o755) -> Path:
    path = root / "perf" / name
    path.parent.mkdir(exist_ok=True)
    path.write_text("#!/bin/sh\n" + body + "\n")
    path.chmod(mode)
    return path


def saved(root: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (root / ".marestail" / "perf" / "samples.jsonl").read_text().splitlines()]


def test_run_command_needs_an_active_run(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert samples.run_command(BENCH, "head", 1, False) == 2
    assert capsys.readouterr().err == "no perf run in progress\n"


@pytest.mark.parametrize(
    ("script", "name", "mode"),
    [
        ("../elsewhere/bench_a", "bench_a", 0o755),
        ("perf/helper", "helper", 0o755),
        ("bench_a", "bench_a", 0o755),
        (BENCH, "bench_a", 0o644),
        ("perf/bench_missing", "bench_a", 0o755),
    ],
)
def test_run_command_rejects_non_bench_scripts(root: Path, capsys: pytest.CaptureFixture[str], script: str, name: str, mode: int) -> None:
    start_session(root)
    bench(root, "true", name, mode)
    (root / "bench_a").write_text("")
    assert samples.run_command(script, "head", 1, False) == 2
    assert capsys.readouterr().err == f"{script} is not an executable perf/bench_* script in {root}\n"


def test_run_command_rejects_a_bench_directory(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    start_session(root)
    (root / "perf" / "bench_dir").mkdir(parents=True)
    assert samples.run_command("perf/bench_dir", "head", 1, False) == 2
    assert "perf/bench_dir is not an executable" in capsys.readouterr().err


def test_with_database_without_db(root: Path) -> None:
    start_session(root)
    config = config_module.load(root)
    active = trees.active(config)
    assert active is not None
    target, problem = samples.with_database(config, BENCH, active["head"], False)
    assert problem == samples.OK
    assert target is not None
    assert target.database is None


def test_run_command_rejects_unknown_tree(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    start_session(root)
    bench(root, "true")
    assert samples.run_command(BENCH, "control", 1, False) == 2
    assert capsys.readouterr().err == "unknown tree control; choose from head, baseline\n"


def test_run_command_reports_a_missing_database(root: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    start_session(root)
    bench(root, "true")
    monkeypatch.setattr(perf_db, "for_run", lambda config: (None, "no database configured"))
    assert samples.run_command(BENCH, "head", 1, True) == 2
    assert capsys.readouterr().err == "no database configured\n"


def test_run_command_records_every_sample(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    start_session(root)
    bench(root, f"echo hello $MARESTAIL_PERF_SAMPLE\necho '{GOOD}'\necho oops >&2\npwd")
    assert samples.run_command(BENCH, "head", 2, False) == 0
    out, err = capsys.readouterr()
    assert out == f"hello 1\n{root / 'wt'}\nhello 2\n{root / 'wt'}\n"
    assert err == "oops\noops\n"
    records = saved(root)
    assert [record["sample"] for record in records] == [1, 2]
    stamp = records[0]["fingerprint"]
    assert records[0] == {
        "tree": "head",
        "sha": "sha1",
        "script": BENCH,
        "fingerprint": stamp,
        "sample": 1,
        "db": False,
        "reset_ms": None,
        "target": "t",
        "unit": "ms",
        "better": "lower",
        "value": 3,
    }


def test_run_command_resets_the_database_each_sample(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    start_session(root)
    bench(root, 'echo "{\\"target\\": \\"$DB_URL/$MARESTAIL_PERF_TREE\\", \\"absent\\": true}"')
    database = object()
    resets: list[Any] = []
    monkeypatch.setattr(perf_db, "for_run", lambda config: (database, ""))

    def reset(db: Any, tree: Any) -> tuple[int, dict[str, str]]:
        resets.append((db, tree.name))
        return 42, {"DB_URL": "pg://x"}

    monkeypatch.setattr(perf_db, "reset", reset)
    assert samples.run_command(BENCH, "baseline", 2, True) == 0
    assert resets == [(database, "baseline")] * 2
    assert [(record["target"], record["db"], record["reset_ms"], record["absent"]) for record in saved(root)] == [
        ("pg://x/baseline", True, 42, True)
    ] * 2


def test_run_command_stops_at_a_failed_reset(root: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    start_session(root)
    bench(root, "true")

    def broken(db: object, tree: trees.Tree) -> tuple[int, dict[str, str]]:
        raise perf_db.DatabaseError("golden not ready")

    monkeypatch.setattr(perf_db, "for_run", lambda config: (object(), ""))
    monkeypatch.setattr(perf_db, "reset", broken)
    assert samples.run_command(BENCH, "head", 3, True) == 1
    assert capsys.readouterr().err == "perf/bench_a sample 1 on head: golden not ready\n"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (f"echo '{GOOD}'\nexit 3", "perf/bench_a sample 1 on head exited 3"),
        ("echo plain", "perf/bench_a sample 1 on head printed no JSON measurement"),
    ],
)
def test_run_command_stops_at_a_failed_sample(root: Path, capsys: pytest.CaptureFixture[str], body: str, message: str) -> None:
    start_session(root)
    bench(root, body)
    assert samples.run_command(BENCH, "head", 2, False) == 1
    assert capsys.readouterr().err.endswith(message + "\n")
    assert not (root / ".marestail" / "perf" / "samples.jsonl").exists()


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (subprocess.TimeoutExpired("x", 7), "perf/bench_a sample 2 on head timed out after 7s"),
        (PermissionError("denied"), "perf/bench_a sample 2 on head could not start: denied"),
    ],
)
def test_take_sample_reports_start_failures(root: Path, monkeypatch: pytest.MonkeyPatch, error: Exception, message: str) -> None:
    config = config_module.load(root)
    seen: list[dict[str, Any]] = []

    def explode(command: list[str], **options: Any) -> None:
        seen.append({"command": command, **options})
        raise error

    monkeypatch.setattr(subprocess, "run", explode)
    tree = trees.Tree("head", "sha1", root / "wt")
    assert samples.take_sample(config, BENCH, tree, 2, (None, "stamp")) == message
    assert seen[0]["command"] == [str(root / BENCH)]
    assert (seen[0]["cwd"], seen[0]["timeout"], seen[0]["check"]) == (root / "wt", 7, False)
    assert seen[0]["env"]["MARESTAIL_PERF_TREE_PATH"] == str(root / "wt")


def line(script: str, fingerprint: str) -> str:
    return json.dumps({"script": script, "fingerprint": fingerprint})


def test_drop_stale_keeps_fresh_foreign_and_unreadable_lines(root: Path) -> None:
    config = config_module.load(root)
    assert samples.drop_stale(config, BENCH, "new") == 0
    lines = [line(BENCH, "old"), "not json", "[1]", line(BENCH, "new"), line("perf/bench_b", "old"), line(BENCH, "old")]
    trees.samples_file(config).write_text("\n".join(lines) + "\n")
    assert samples.drop_stale(config, BENCH, "new") == 2
    assert trees.samples_file(config).read_text() == "\n".join(lines[1:5]) + "\n"
    assert samples.drop_stale(config, BENCH, "new") == 0


def test_run_command_reports_dropped_samples(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    start_session(root)
    bench(root, f"echo '{GOOD}'")
    config = config_module.load(root)
    trees.samples_file(config).write_text(line(BENCH, "old") + "\n")
    assert samples.run_command(BENCH, "head", 1, False) == 0
    assert capsys.readouterr().err == (
        "dropped 1 earlier measurements of perf/bench_a taken before it or a shared file under perf/ changed; "
        "take its samples again on every tree\n"
    )
    assert len(saved(root)) == 1


def test_run_command_keeps_samples_taken_at_the_current_fingerprint(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    start_session(root)
    bench(root, f"echo '{GOOD}'")
    config = config_module.load(root)
    stamp = hygiene.fingerprint(root, BENCH)
    trees.samples_file(config).write_text(line(BENCH, "old") + "\n" + line(BENCH, stamp) + "\n")
    assert samples.run_command(BENCH, "head", 1, False) == 0
    assert "dropped 1 earlier measurements" in capsys.readouterr().err
    assert [record["fingerprint"] for record in saved(root)] == [stamp, stamp]


def test_with_database_asks_the_configuration_for_the_database(root: Path) -> None:
    start_session(root)
    config = config_module.load(root)
    active = trees.active(config)
    assert active is not None
    assert samples.with_database(config, BENCH, active["head"], True) == (None, perf_db.NOT_CONFIGURED)


def test_resolve_passes_the_configuration_down_to_the_database(root: Path) -> None:
    start_session(root)
    bench(root, "true")
    config = config_module.load(root)
    assert samples.resolve(config, BENCH, "head", True) == (None, perf_db.NOT_CONFIGURED)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("not json", None),
        ("[1, 2]", None),
        ('{"target": 5, "value": 1}', None),
        ('{"target": "", "absent": true}', None),
        ('{"target": "t", "absent": true, "value": 1}', {"target": "t", "absent": True}),
        (
            '{"target": "t", "absent": 1, "unit": "ms", "better": "lower", "value": 1}',
            {"target": "t", "unit": "ms", "better": "lower", "value": 1},
        ),
        (
            '{"target": "t", "unit": "ms", "better": "higher", "value": 1.5, "x": 1}',
            {"target": "t", "unit": "ms", "better": "higher", "value": 1.5},
        ),
        (
            '{"target": "t", "unit": "ms", "better": "lower", "values": [1, 2.5]}',
            {"target": "t", "unit": "ms", "better": "lower", "values": [1, 2.5]},
        ),
        ('{"target": "t", "unit": "ms", "better": "lower", "values": []}', None),
        ('{"target": "t", "unit": "ms", "better": "lower", "values": [1, "2"]}', None),
        ('{"target": "t", "unit": "ms", "better": "lower", "values": 3}', None),
        ('{"target": "t", "unit": "ms", "better": "lower", "value": true}', None),
        ('{"target": "t", "unit": "ms", "better": "lower", "value": "3"}', None),
        ('{"target": "t", "unit": "ms", "better": "lower"}', None),
        ('{"target": "t", "unit": 3, "better": "lower", "value": 1}', None),
        ('{"target": "t", "unit": "ms", "better": "faster", "value": 1}', None),
    ],
)
def test_measurement(text: str, expected: dict[str, Any] | None) -> None:
    assert samples.measurement(text) == expected


def test_parse_output_splits_records_from_passthrough() -> None:
    assert samples.parse_output(f"a\n{GOOD}\nb") == (
        [{"target": "t", "unit": "ms", "better": "lower", "value": 3}],
        ["a", "b"],
    )
