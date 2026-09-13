import re
from dataclasses import dataclass
from pathlib import Path

FILENAME = "PERFORMANCE.md"
FIXED = ["Task", "Commit", "Date", "Rows"]
HEADER_START = "| Task |"
CELL_BORDER = re.compile(r"(?<!\\)\|")


@dataclass(frozen=True)
class Table:
    prefix: str
    columns: list[str]
    rows: list[dict[str, str]]
    suffix: str


def load(root: Path) -> Table:
    path = root / FILENAME
    return parse(path.read_text() if path.exists() else "")


def parse(text: str) -> Table:
    lines = text.splitlines(keepends=True)
    start = next((index for index, line in enumerate(lines) if line.startswith(HEADER_START)), None)
    if start is None:
        return Table(text, [], [], "")
    header = split_row(lines[start])
    end = start + 2
    while end < len(lines) and lines[end].startswith("|"):
        end += 1
    rows = [dict(zip(header, split_row(line))) for line in lines[start + 2 : end]]
    columns = [cell for cell in header if cell not in FIXED]
    return Table("".join(lines[:start]), columns, rows, "".join(lines[end:]))


def split_row(line: str) -> list[str]:
    cells = CELL_BORDER.split(line.strip())[1:-1]
    return [cell.strip().replace("\\|", "|") for cell in cells]
