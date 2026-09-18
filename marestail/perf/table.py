import re
from dataclasses import dataclass
from pathlib import Path

from marestail.perf.results import Classified, Measurement

FILENAME = "PERFORMANCE.md"
TEMPLATE = Path(__file__).resolve().parents[2] / "templates" / FILENAME
TASK = "Task"
FIXED = [TASK, "Commit", "Date", "Rows"]
PRE_MARESTAIL = "pre-marestail"
EMPTY = "—"
HEADER_START = "| Task |"
CELL_BORDER = re.compile(r"(?<!\\)\|")
MARKS = {"degraded": " ⚠", "improved": " ✓", "unchanged": ""}


@dataclass(frozen=True)
class Table:
    prefix: str
    columns: list[str]
    rows: list[dict[str, str]]
    suffix: str


@dataclass(frozen=True)
class Snapshot:
    task: str
    commit: str
    date: str
    rows: str
    pre_commit: str | None
    classified: list[Classified]

    @property
    def columns(self) -> list[str]:
        return [item.measurement.column for item in self.classified]


def load(root: Path) -> Table:
    path = root / FILENAME
    return parse(path.read_text() if path.exists() else "")


def parse(text: str) -> Table:
    lines = text.splitlines(keepends=True)
    start = header_index(lines)
    if start is None:
        return Table(text, [], [], "")
    header = split_row(lines[start])
    end = table_end(lines, start + 2)
    rows = [dict(zip(header, split_row(line), strict=False)) for line in lines[start + 2 : end]]
    return Table("".join(lines[:start]), extra_columns(header), rows, "".join(lines[end:]))


def header_index(lines: list[str]) -> int | None:
    return next((index for index, line in enumerate(lines) if line.startswith(HEADER_START)), None)


def table_end(lines: list[str], end: int) -> int:
    while end < len(lines) and lines[end].startswith("|"):
        end += 1
    return end


def extra_columns(header: list[str]) -> list[str]:
    return [cell for cell in header if cell not in FIXED]


def split_row(line: str) -> list[str]:
    cells = CELL_BORDER.split(line.strip())[1:-1]
    return [cell.strip().replace("\\|", "|") for cell in cells]


def write(root: Path, snapshot: Snapshot) -> None:
    path = root / FILENAME
    current = parse(path.read_text() if path.exists() else TEMPLATE.read_text())
    path.write_text(render(upsert(current, snapshot)))


def upsert(table: Table, snapshot: Snapshot) -> Table:
    columns = table.columns + [column for column in snapshot.columns if column not in table.columns]
    rows = with_pre_row(list(table.rows), snapshot, snapshot.pre_commit)
    return Table(table.prefix, columns, placed(rows, task_row(snapshot), snapshot.task), table.suffix)


def with_pre_row(rows: list[dict[str, str]], snapshot: Snapshot, pre_commit: str | None) -> list[dict[str, str]]:
    if pre_commit is None:
        return rows
    return [pre_row(snapshot, pre_commit), *(row for row in rows if row.get(TASK) != PRE_MARESTAIL)]


def pre_row(snapshot: Snapshot, pre_commit: str) -> dict[str, str]:
    cells = {item.measurement.column: pre_cell(item.measurement) for item in snapshot.classified}
    return fixed_cells(snapshot, PRE_MARESTAIL, pre_commit) | cells


def task_row(snapshot: Snapshot) -> dict[str, str]:
    cells = {item.measurement.column: task_cell(item) for item in snapshot.classified}
    return fixed_cells(snapshot, snapshot.task, snapshot.commit) | cells


def placed(rows: list[dict[str, str]], row: dict[str, str], task: str) -> list[dict[str, str]]:
    index = next((position for position, existing in enumerate(rows) if existing.get(TASK) == task), None)
    if index is None:
        return [*rows, row]
    return [*rows[:index], row, *rows[index + 1 :]]


def fixed_cells(snapshot: Snapshot, task: str, commit: str) -> dict[str, str]:
    return {TASK: task, "Commit": commit, "Date": snapshot.date, "Rows": snapshot.rows}


def task_cell(item: Classified) -> str:
    head = item.measurement.head
    if item.status == "removed" or head is None:
        return "removed"
    return measured_cell(item, number(head) + item.measurement.unit)


def measured_cell(item: Classified, value: str) -> str:
    if item.status == "new" or item.change is None:
        return f"{value} (new)"
    if item.status == "thin":
        return f"{value} (n={item.measurement.values})"
    return f"{value} ({item.change:+.1f}%){MARKS[item.status]}"


def pre_cell(measurement: Measurement) -> str:
    return EMPTY if measurement.pre_marestail is None else number(measurement.pre_marestail) + measurement.unit


def number(value: float) -> str:
    return f"{value:.10g}"


def render(table: Table) -> str:
    header = FIXED + table.columns
    lines = [row_line(header), "|" + "|".join("---" for _ in header) + "|"]
    lines += [row_line([row.get(name, EMPTY) for name in header]) for row in table.rows]
    return table.prefix + "\n".join(lines) + "\n" + table.suffix


def row_line(cells: list[str]) -> str:
    return "| " + " | ".join(cell.replace("|", "\\|") for cell in cells) + " |"
