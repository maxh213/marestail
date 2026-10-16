from pathlib import Path

import pytest

from marestail.perf import table
from marestail.perf.results import Classified, Measurement

HEADER = "| Task | Commit | Date | Rows | GET /a p50 |\n|---|---|---|---|---|\n"


def measurement(metric: str = "p50", head: float | None = 12.5, pre: float | None = None, values: int = 0) -> Measurement:
    return Measurement("GET /a", metric, "ms", "lower", pre, 10.0, head, 3, "perf/bench_a.py", values)


def snapshot(classified: list[Classified], task: str = "t", pre_commit: str | None = None) -> table.Snapshot:
    return table.Snapshot(task, "abc1234", "2026-09-13", "1000", pre_commit, classified)


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        (Classified(measurement(), "removed", None), "removed"),
        (Classified(measurement(head=None), "unchanged", 1.0), "removed"),
        (Classified(measurement(), "new", 3.0), "12.5ms (new)"),
        (Classified(measurement(), "unchanged", None), "12.5ms (new)"),
        (Classified(measurement(values=2), "thin", 1.0), "12.5ms (n=2)"),
        (Classified(measurement(), "degraded", 25.0), "12.5ms (+25.0%) ⚠"),
        (Classified(measurement(), "improved", -3.04), "12.5ms (-3.0%) ✓"),
        (Classified(measurement(head=1 / 3), "unchanged", 0.0), "0.3333333333ms (+0.0%)"),
    ],
)
def test_task_cell(item: Classified, expected: str) -> None:
    assert table.task_cell(item) == expected


@pytest.mark.parametrize(("pre", "expected"), [(None, "—"), (4.0, "4ms"), (1234567.891, "1234567.891ms")])
def test_pre_cell(pre: float | None, expected: str) -> None:
    assert table.pre_cell(measurement(pre=pre)) == expected


def test_table_end_stops_at_the_first_non_row() -> None:
    lines = ["| a |\n", "|---|\n", "| 1 |\n", "after\n", "| not |\n"]
    assert table.table_end(lines, 2) == 3
    assert table.table_end(["| a |\n"], 1) == 1
    assert table.table_end(["| a |\n", "| b |\n"], 0) == 2
    assert table.table_end(["| a |\n"], 5) == 5


def test_parse_without_table() -> None:
    assert table.parse("# Title\n") == table.Table("# Title\n", [], [], "")


def test_row_map_pairs_cells_up_to_the_shorter_side() -> None:
    assert table.row_map(["a", "b"], ["1", "2"]) == {"a": "1", "b": "2"}
    assert table.row_map(["a", "b", "c"], ["1", "2"]) == {"a": "1", "b": "2"}
    assert table.row_map(["a"], ["1", "2"]) == {"a": "1"}


def test_parse_table() -> None:
    text = "intro\n" + HEADER + "| t | a\\|b | d | 5 | 1ms |\n| short |\nafter\n| not | a row |\n"
    parsed = table.parse(text)
    assert parsed.prefix == "intro\n"
    assert parsed.columns == ["GET /a p50"]
    assert parsed.rows == [
        {"Task": "t", "Commit": "a|b", "Date": "d", "Rows": "5", "GET /a p50": "1ms"},
        {"Task": "short"},
    ]
    assert parsed.suffix == "after\n| not | a row |\n"


def test_parse_table_at_end() -> None:
    parsed = table.parse(HEADER.rstrip("\n"))
    assert (parsed.prefix, parsed.rows, parsed.suffix) == ("", [], "")


def test_render_round_trip() -> None:
    parsed = table.Table("pre\n", ["x|y"], [{"Task": "t", "x|y": "1"}], "post\n")
    rendered = table.render(parsed)
    assert rendered == "pre\n| Task | Commit | Date | Rows | x\\|y |\n|---|---|---|---|---|\n| t | — | — | — | 1 |\npost\n"
    assert table.parse(rendered) == table.Table(
        "pre\n", ["x|y"], [{"Task": "t", "Commit": "—", "Date": "—", "Rows": "—", "x|y": "1"}], "post\n"
    )


def test_upsert_appends_and_adds_columns() -> None:
    start = table.Table("", ["old"], [{"Task": "a", "old": "1"}], "")
    result = table.upsert(start, snapshot([Classified(measurement(), "new", None)]))
    assert result.columns == ["old", "GET /a p50"]
    assert result.rows == [
        {"Task": "a", "old": "1"},
        {"Task": "t", "Commit": "abc1234", "Date": "2026-09-13", "Rows": "1000", "GET /a p50": "12.5ms (new)"},
    ]


def test_upsert_replaces_task_and_pre_row() -> None:
    rows = [{"Task": "a"}, {"Task": "pre-marestail", "x": "1"}, {"Task": "t", "x": "old"}, {"Task": "z"}]
    start = table.Table("p", ["GET /a p50"], rows, "s")
    result = table.upsert(start, snapshot([Classified(measurement(pre=9.0), "degraded", 25.0)], pre_commit="0000000"))
    assert result.columns == ["GET /a p50"]
    assert result.rows == [
        {"Task": "pre-marestail", "Commit": "0000000", "Date": "2026-09-13", "Rows": "1000", "GET /a p50": "9ms"},
        {"Task": "a"},
        {"Task": "t", "Commit": "abc1234", "Date": "2026-09-13", "Rows": "1000", "GET /a p50": "12.5ms (+25.0%) ⚠"},
        {"Task": "z"},
    ]
    assert (result.prefix, result.suffix) == ("p", "s")


def test_snapshot_columns() -> None:
    items = [Classified(measurement(), "new", None), Classified(measurement("p95"), "new", None)]
    assert snapshot(items).columns == ["GET /a p50", "GET /a p95"]


def test_load_missing_and_present(tmp_path: Path) -> None:
    assert table.load(tmp_path) == table.Table("", [], [], "")
    (tmp_path / "PERFORMANCE.md").write_text(HEADER + "| t | c | d | r | 1ms |\n")
    assert table.load(tmp_path).rows == [{"Task": "t", "Commit": "c", "Date": "d", "Rows": "r", "GET /a p50": "1ms"}]


def test_write_starts_from_template(tmp_path: Path) -> None:
    table.write(tmp_path, snapshot([Classified(measurement(), "new", None)]))
    template = table.TEMPLATE.read_text()
    expected = template + "| t | abc1234 | 2026-09-13 | 1000 | 12.5ms (new) |\n"
    assert (tmp_path / "PERFORMANCE.md").read_text() == expected.replace(
        "| Rows |\n|---|---|---|---|", "| Rows | GET /a p50 |\n|---|---|---|---|---|"
    )


def test_write_updates_existing(tmp_path: Path) -> None:
    (tmp_path / "PERFORMANCE.md").write_text("# Mine\n" + HEADER + "| t | c | d | r | 1ms |\ntail\n")
    table.write(tmp_path, snapshot([Classified(measurement(), "improved", -50.0)]))
    assert (tmp_path / "PERFORMANCE.md").read_text() == (
        "# Mine\n" + HEADER + "| t | abc1234 | 2026-09-13 | 1000 | 12.5ms (-50.0%) ✓ |\ntail\n"
    )
