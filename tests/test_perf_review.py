import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from marestail.perf import hygiene, review, table, trees
from marestail.perf.results import Classified, Measurement
from tests.conftest import FakeRun, make_context

BENCH = "perf/bench_a"
POLICY = {"perf": {"min_runs": 2, "values_per_sample": 2, "bootstrap": 0}}


def config_at(root: Path, raw: dict[str, Any] | None = None) -> Any:
    return make_context(root, raw).config


def item(target: str, status: str, change: float | None = 12.5, metric: str = "p50") -> Classified:
    return Classified(Measurement(target, metric, "ms", "lower", None, 10.0, 11.25, 2, BENCH, 4), status, change)


def session_with(*names: str, image: str | None = None) -> trees.Session:
    return trees.Session("task-1", [trees.Tree(name, f"sha-{name}", Path(f"/w/{name}")) for name in names], image=image)


def write_samples(config: Any, records: list[dict[str, Any]]) -> None:
    trees.samples_file(config).write_text("".join(json.dumps(record) + "\n" for record in records) + "\n")


def make_bench(root: Path, name: str = "bench_a") -> None:
    (root / "perf").mkdir(exist_ok=True)
    (root / "perf" / name).write_text("#!/bin/sh\n")


def sample(tree: str, stamp: str, values: list[float], db: bool = False) -> dict[str, Any]:
    return {"target": "t", "tree": tree, "script": BENCH, "db": db, "fingerprint": stamp, "unit": "ms", "better": "lower", "values": values}


def test_review_classifies_and_writes_results(tmp_path: Path) -> None:
    config = config_at(tmp_path, POLICY)
    make_bench(tmp_path)
    stamp = hygiene.fingerprint(tmp_path, BENCH)
    write_samples(config, [sample("baseline", stamp, [10, 10], True)] * 2 + [sample("head", stamp, [20, 20], True)] * 2)
    report = tmp_path / "verdict.md"
    report.write_text("t got slower")
    outcome = review.review(config, session_with("baseline", "head"), report, "FAIL")
    assert outcome.problems == []
    assert outcome.used_db is True
    assert [(entry.status, entry.change, entry.measurement.metric) for entry in outcome.classified] == [
        ("degraded", 100.0, "p50"),
        ("thin", 100.0, "p95"),
    ]
    data = json.loads((tmp_path / "verdict.results.json").read_text())
    assert data["measurements"][0] | {"interval": None} == {
        "target": "t",
        "metric": "p50",
        "unit": "ms",
        "better": "lower",
        "pre_marestail": None,
        "baseline": 10.0,
        "head": 20.0,
        "runs": 2,
        "script": BENCH,
        "values": 4,
        "interval": None,
        "control": None,
        "status": "degraded",
        "change": 100.0,
    }


def test_review_collects_every_kind_of_problem(tmp_path: Path) -> None:
    config = config_at(tmp_path, POLICY)
    make_bench(tmp_path)
    (tmp_path / "app.csproj").write_text("")
    (tmp_path / "perf" / "x.cs").write_text("")
    write_samples(config, [sample("baseline", "old", [10, 10])] * 2 + [sample("head", "old", [20, 20])] * 2)
    report = tmp_path / "verdict.md"
    report.write_text("")
    outcome = review.review(config, session_with("baseline", "head"), report, "PASS")
    assert outcome.used_db is False
    assert outcome.problems == [
        "`t` is degraded (+100.0% p50) but the verdict does not name it",
        "`perf/x.cs` would compile into app.csproj, because an SDK project at the repo root includes every .cs file below it; "
        "write this bench in another language",
        "`perf/bench_a` has samples taken before it or a shared file under perf/ last changed (2 on baseline, 2 on head); "
        "take its samples again on every tree",
    ]


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        ([], []),
        (["b.csproj"], []),
        (["perf/x.txt"], []),
        (
            ["b.csproj", "a.csproj", "perf/z.cs", "perf/sub/y.cs"],
            [
                "`perf/sub/y.cs` would compile into a.csproj, b.csproj, because an SDK project at the repo root includes every .cs "
                "file below it; write this bench in another language",
                "`perf/z.cs` would compile into a.csproj, b.csproj, because an SDK project at the repo root includes every .cs "
                "file below it; write this bench in another language",
            ],
        ),
    ],
)
def test_csharp_problems(tmp_path: Path, files: list[str], expected: list[str]) -> None:
    for name in files:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("")
    assert review.csharp_problems(config_at(tmp_path)) == expected


def test_load_records(tmp_path: Path) -> None:
    config = config_at(tmp_path)
    assert review.load_records(config) == []
    trees.samples_file(config).write_text('{"a": 1}\n  \n{"b": 2}\n')
    assert review.load_records(config) == [{"a": 1}, {"b": 2}]


def test_bench_scripts(tmp_path: Path) -> None:
    config = config_at(tmp_path)
    assert review.bench_scripts(config) == []
    make_bench(tmp_path, "bench_z")
    make_bench(tmp_path, "bench_b")
    (tmp_path / "perf" / "helper.py").write_text("")
    (tmp_path / "perf" / "bench_dir").mkdir()
    assert review.bench_scripts(config) == ["perf/bench_b", "perf/bench_z"]


def test_record_table_skips_without_measurements(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake = fake_run(review)
    review.record_table(config_at(tmp_path), session_with("head"), review.Review([], [], False))
    assert fake.calls == []
    assert not (tmp_path / table.FILENAME).exists()


@pytest.mark.parametrize(("ignored", "staged"), [(1, True), (0, False)])
def test_record_table_writes_and_stages(
    tmp_path: Path, fake_run: Callable[..., FakeRun], monkeypatch: pytest.MonkeyPatch, ignored: int, staged: bool
) -> None:
    monkeypatch.setattr(time, "strftime", lambda _: "2026-01-02")
    fake = fake_run(review, [(0, "abc1234\n"), (0, "pre5678\n"), (ignored, "")])
    outcome = review.Review([], [item("t", "degraded")], False)
    review.record_table(config_at(tmp_path), session_with("head", table.PRE_MARESTAIL), outcome)
    expected = [
        ["git", "rev-parse", "--short", "sha-head"],
        ["git", "rev-parse", "--short", "sha-pre-marestail"],
        ["git", "check-ignore", "-q", table.FILENAME],
    ]
    assert fake.calls == expected + ([["git", "add", "--", table.FILENAME]] if staged else [])
    loaded = table.load(tmp_path)
    assert loaded.columns == ["t p50"]
    assert [row["Commit"] for row in loaded.rows] == ["pre5678", "abc1234"]
    assert loaded.rows[-1]["Date"] == "2026-01-02"


def test_snapshot_without_head_or_pre(tmp_path: Path, fake_run: Callable[..., FakeRun], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "strftime", lambda _: "2026-01-02")
    fake = fake_run(review, [(0, " HEADSHA \n")])
    outcome = review.Review([], [item("t", "new")], False)
    snapshot = review.snapshot_for(config_at(tmp_path), session_with("baseline"), outcome)
    assert fake.calls == [["git", "rev-parse", "--short", "HEAD"]]
    assert snapshot == table.Snapshot("task-1", "HEADSHA", "2026-01-02", table.EMPTY, None, outcome.classified)


def test_rows_cell(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MARESTAIL_PERF_DB_ROWS", raising=False)
    config = config_at(tmp_path, {"perf": {"db": {"rows": 123}}})
    assert review.rows_cell(config, True) == "123"
    assert review.rows_cell(config, False) == table.EMPTY


def test_tree_named() -> None:
    session = session_with("baseline", "head")
    assert review.tree_named(session, "head") == session.trees[1]
    assert review.tree_named(session, "control") is None


def test_changes_summary_lists_changes_database_and_setup() -> None:
    session = session_with("head", image="postgres:16")
    session.image_source, session.rows, session.rows_source = "marestail.toml", 5, "default"
    outcome = review.Review(
        [], [item("a", "degraded"), item("b", "unchanged"), item("c", "removed", None), item("d", "improved", -3)], True
    )
    text = "intro\n## Setup needed\n\n run make seed \n## Next\nmore"
    assert review.changes_summary(outcome, text, session) == "\n".join(
        [
            "## Performance changes",
            "- Postgres image: postgres:16 (marestail.toml)",
            "- rows: 5 (default)",
            "- degraded +12.5% `a p50`",
            "- removed `c p50`",
            "- improved -3.0% `d p50`",
            "",
            "### Setup needed",
            "run make seed",
        ]
    )


def test_changes_summary_with_nothing_changed() -> None:
    outcome = review.Review([], [item("b", "unchanged")], False)
    assert review.changes_summary(outcome, "## Setup needed\n\n", session_with("head")) == "## Performance changes\n- none"


def test_setup_needed_runs_to_the_end_without_a_next_heading() -> None:
    assert review.setup_needed("## Setup needed\ninstall x\n### sub\nkeep") == "install x\n### sub\nkeep"
    assert review.setup_needed("nothing here") == ""
