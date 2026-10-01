import tomllib
from dataclasses import dataclass
from pathlib import Path

FENCE = "+++"
KEY_HEADING = "depends and stack"
KEYS = ("depends", "stack")
READMES = "readme.md"
NO_FRONT = "no front matter: line 1 must be +++"
UNCLOSED = "front matter is not closed: no +++ line after line 1"
NO_DEPENDS = "front matter has no depends; write depends = [] for a task with no dependencies"
BAD_DEPENDS = "depends must be an array of strings"
EMPTY_STACK = "stack is not allowed when depends is empty"
REQUIRED_STACK = "stack is required when depends is not empty"
SELF_DEPENDENCY = "depends on itself"
NOT_AN_ID = "is not a task id; use the file name without .md"


@dataclass(frozen=True)
class TaskFile:
    id: str
    path: Path
    depends: tuple[str, ...]
    stack: bool | None
    body: str
    has_front_matter: bool


class TaskFileError(ValueError):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("\n".join(problems))
        self.problems = problems


def read(path: Path) -> TaskFile:
    task, problems = _parse(path, path.read_text())
    if problems:
        raise TaskFileError(problems)
    return task


def check(paths: list[Path]) -> list[str]:
    chosen, absent = _choose(paths)
    checked = [_inspect(path) for path in chosen]
    rows = sorted([*absent, *_file_rows(checked, _set_problems(checked))], key=_row_path)
    return _render(rows)


def _parse(path: Path, text: str) -> tuple[TaskFile, list[str]]:
    lines = text.splitlines(keepends=True)
    closing = _closing_index(lines) if _opens_block(lines) else None
    if closing is None:
        return _blockless(path, text), _missing_close(lines)
    return _parse_block(path, "".join(lines[1:closing]), "".join(lines[closing + 1 :]))


def _missing_close(lines: list[str]) -> list[str]:
    if _opens_block(lines):
        return [UNCLOSED]
    return []


def _opens_block(lines: list[str]) -> bool:
    return bool(lines) and _is_fence(lines[0])


def _is_fence(line: str) -> bool:
    return line.removesuffix("\n") == FENCE


def _closing_index(lines: list[str]) -> int | None:
    for index in range(1, len(lines)):
        if _is_fence(lines[index]):
            return index
    return None


def _blockless(path: Path, text: str) -> TaskFile:
    return TaskFile(path.stem, path, (), None, text, False)


def _parse_block(path: Path, toml_text: str, body: str) -> tuple[TaskFile, list[str]]:
    table, error = _parse_toml(toml_text)
    entries, problems = _fields(path.stem, table, error)
    return _present(path, table, entries, body), problems


def _fields(task_id: str, table: dict[str, object], error: str) -> tuple[list[str] | None, list[str]]:
    if error:
        return None, [error]
    entries, depends_problems = _depends_problems(task_id, table)
    problems = [*_unknown_keys(table), *depends_problems, *_stack_problems(table, entries)]
    if problems:
        return None, problems
    return entries, []


def _parse_toml(text: str) -> tuple[dict[str, object], str]:
    try:
        return tomllib.loads(text), ""
    except tomllib.TOMLDecodeError as error:
        return {}, f"front matter is not valid TOML: {error}"


def _present(path: Path, table: dict[str, object], entries: list[str] | None, body: str) -> TaskFile:
    depends = tuple(entries or ())
    if not depends:
        return TaskFile(path.stem, path, (), None, body, True)
    return TaskFile(path.stem, path, depends, table["stack"] is True, body, True)


def _unknown_keys(table: dict[str, object]) -> list[str]:
    return [f"unknown front matter key {key!r}; allowed keys are {KEY_HEADING}" for key in sorted(table) if key not in KEYS]


def _depends_problems(task_id: str, table: dict[str, object]) -> tuple[list[str] | None, list[str]]:
    if "depends" not in table:
        return None, [NO_DEPENDS]
    entries = _string_list(table["depends"])
    if entries is None:
        return None, [BAD_DEPENDS]
    return entries, _entry_problems(task_id, entries)


def _string_list(value: object) -> list[str] | None:
    if not isinstance(value, list):
        return None
    if not all(isinstance(item, str) for item in value):
        return None
    return value


def _entry_problems(task_id: str, entries: list[str]) -> list[str]:
    return [*_invalid_entries(entries), *_repeated_entries(entries), *_self_entry(task_id, entries)]


def _invalid_entries(entries: list[str]) -> list[str]:
    return [f"depends entry '{entry}' {NOT_AN_ID}" for entry in entries if _not_an_id(entry)]


def _not_an_id(entry: str) -> bool:
    return not entry or "/" in entry or "\\" in entry or entry.endswith(".md")


def _repeated_entries(entries: list[str]) -> list[str]:
    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry] = counts.get(entry, 0) + 1
    return [f"depends lists {entry} twice" for entry in dict.fromkeys(entries) if counts[entry] > 1]


def _self_entry(task_id: str, entries: list[str]) -> list[str]:
    return [SELF_DEPENDENCY] if task_id in entries else []


def _stack_problems(table: dict[str, object], entries: list[str] | None) -> list[str]:
    if "stack" not in table:
        return [REQUIRED_STACK] if entries else []
    value = table["stack"]
    return [*_stack_type_problems(value), *_stack_rules(value, entries)]


def _stack_type_problems(value: object) -> list[str]:
    if isinstance(value, bool):
        return []
    return [f"stack must be true or false, got {value!r}"]


def _stack_rules(value: object, entries: list[str] | None) -> list[str]:
    if entries is None:
        return []
    if not entries:
        return [EMPTY_STACK, *_stack_count_problems(value, 0)]
    return _stack_count_problems(value, len(entries))


def _stack_count_problems(value: object, count: int) -> list[str]:
    if value is True and count != 1:
        return [f"stack = true needs exactly one dependency, got {count}"]
    return []


@dataclass(frozen=True)
class _Checked:
    path: Path
    resolved: Path
    task: TaskFile
    problems: list[str]

    @property
    def clean(self) -> bool:
        return not self.problems


def _choose(paths: list[Path]) -> tuple[list[Path], list[tuple[Path, list[str]]]]:
    chosen: dict[Path, Path] = {}
    absent: list[tuple[Path, list[str]]] = []
    for path in paths:
        _choose_one(path, chosen, absent)
    return list(chosen.values()), absent


def _choose_one(path: Path, chosen: dict[Path, Path], absent: list[tuple[Path, list[str]]]) -> None:
    if not path.exists():
        absent.append((path, ["no such file or folder"]))
    elif path.is_dir():
        _choose_folder(path, chosen)
    elif path.suffix == ".md":
        chosen.setdefault(path.resolve(), path)
    else:
        absent.append((path, ["not a .md file"]))


def _choose_folder(folder: Path, chosen: dict[Path, Path]) -> None:
    for child in sorted(folder.glob("*.md")):
        if child.name.lower() != READMES:
            chosen.setdefault(child.resolve(), folder / child.name)


def _inspect(path: Path) -> _Checked:
    task, problems = _parse(path, path.read_text())
    if not problems:
        problems = _front_problem(task)
    return _Checked(path, path.resolve(), task, problems)


def _front_problem(task: TaskFile) -> list[str]:
    return [] if task.has_front_matter else [NO_FRONT]


def _file_rows(checked: list[_Checked], extra: dict[Path, list[str]]) -> list[tuple[Path, list[str]]]:
    rows: list[tuple[Path, list[str]]] = []
    for item in checked:
        problems = item.problems + extra.get(item.path, [])
        if problems:
            rows.append((item.path, problems))
    return rows


def _render(rows: list[tuple[Path, list[str]]]) -> list[str]:
    return [f"{path}: {problem}" for path, problems in rows for problem in problems]


def _row_path(row: tuple[Path, list[str]]) -> str:
    return str(row[0])


def _set_problems(checked: list[_Checked]) -> dict[Path, list[str]]:
    clean = [item for item in checked if item.clean]
    problems: dict[Path, list[str]] = {}
    _duplicate_problems(clean, problems)
    _missing_problems(clean, problems)
    _cycle_problems(clean, problems)
    return problems


def _duplicate_problems(clean: list[_Checked], problems: dict[Path, list[str]]) -> None:
    first: dict[str, Path] = {}
    for item in sorted(clean, key=_path_string):
        known = first.setdefault(item.task.id, item.path)
        if known is not item.path:
            _add_problem(item.path, f"duplicate task id {item.task.id}: also {known}", problems)


def _missing_problems(clean: list[_Checked], problems: dict[Path, list[str]]) -> None:
    for item in clean:
        for dep in item.task.depends:
            _missing_problem(item, dep, problems)


def _missing_problem(item: _Checked, dep: str, problems: dict[Path, list[str]]) -> None:
    target = _dependency_path(item.path, dep)
    if not target.exists():
        _add_problem(item.path, f"depends on {dep}, but {target} does not exist", problems)


def _cycle_problems(clean: list[_Checked], problems: dict[Path, list[str]]) -> None:
    ordered = sorted(clean, key=_path_string)
    for path, lines in _cycles(_edges(ordered), _task_ids(ordered)).items():
        for line in lines:
            _add_problem(path, line, problems)


def _add_problem(path: Path, line: str, problems: dict[Path, list[str]]) -> None:
    problems.setdefault(path, []).append(line)


def _dependency_path(task: Path, dep: str) -> Path:
    return task.parent / f"{dep}.md"


def _path_string(item: _Checked) -> str:
    return str(item.path)


@dataclass
class _Trail:
    edges: dict[Path, list[Path]]
    ids: dict[Path, str]
    route: list[Path]
    on_route: set[Path]
    seen: set[tuple[str, ...]]
    lines: dict[Path, list[str]]


def _edges(clean: list[_Checked]) -> dict[Path, list[Path]]:
    by_target = {item.resolved: item.path for item in clean}
    return {item.path: _destinations(item, by_target) for item in clean}


def _destinations(item: _Checked, by_target: dict[Path, Path]) -> list[Path]:
    found: list[Path] = []
    for dep in item.task.depends:
        target = by_target.get(_dependency_path(item.path, dep).resolve())
        if target is not None:
            found.append(target)
    return found


def _task_ids(clean: list[_Checked]) -> dict[Path, str]:
    return {item.path: item.task.id for item in clean}


def _cycles(edges: dict[Path, list[Path]], ids: dict[Path, str]) -> dict[Path, list[str]]:
    trail = _Trail(edges, ids, [], set(), set(), {})
    for start in edges:
        _visit(start, trail)
    return trail.lines


def _visit(node: Path, trail: _Trail) -> None:
    trail.route.append(node)
    trail.on_route.add(node)
    _descend(node, trail)
    trail.route.pop()
    trail.on_route.discard(node)


def _descend(node: Path, trail: _Trail) -> None:
    for following in trail.edges[node]:
        _step(following, trail)


def _step(following: Path, trail: _Trail) -> None:
    if following in trail.on_route:
        _record_cycle(following, trail)
        return
    _visit(following, trail)


def _record_cycle(back: Path, trail: _Trail) -> None:
    cycle_route = trail.route[trail.route.index(back) :]
    holder, cycle = _named_cycle(cycle_route, trail.ids)
    if tuple(cycle) in trail.seen:
        return
    trail.seen.add(tuple(cycle))
    _add_problem(holder, f"dependency cycle: {' -> '.join(cycle)}", trail.lines)


def _named_cycle(route: list[Path], ids: dict[Path, str]) -> tuple[Path, list[str]]:
    names = [ids[path] for path in route]
    start = names.index(min(names))
    return route[start], [*names[start:], *names[:start], names[start]]
