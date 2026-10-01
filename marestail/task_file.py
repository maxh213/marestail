import tomllib
from dataclasses import dataclass
from pathlib import Path

_FENCE = "+++"
_NO_FRONT = "no front matter: line 1 must be +++"
_UNCLOSED = "front matter is not closed: no +++ line after line 1"
_NO_DEPENDS = "front matter has no depends; write depends = [] for a task with no dependencies"
_BAD_DEPENDS = "depends must be an array of strings"
_EMPTY_STACK = "stack is not allowed when depends is empty"
_REQUIRED_STACK = "stack is required when depends is not empty"
_SELF = "depends on itself"
_ALLOWED = ("depends", "stack")


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


@dataclass(frozen=True)
class _Loaded:
    file: TaskFile
    problems: list[str]


@dataclass(frozen=True)
class _Examined:
    path: Path
    resolved: Path
    file: TaskFile | None
    problems: list[str]


@dataclass(frozen=True)
class _Clean:
    path: Path
    resolved: Path
    file: TaskFile


@dataclass
class _Search:
    graph: dict[Path, list[Path]]
    ids: dict[Path, str]
    stack: list[Path]
    stacked: set[Path]
    seen: set[tuple[str, ...]]
    lines: dict[Path, list[str]]


def read(path: Path) -> TaskFile:
    loaded = _load(path, path.read_text())
    if loaded.problems:
        raise TaskFileError(loaded.problems)
    return loaded.file


def check(paths: list[Path]) -> list[str]:
    chosen, early = _collect(paths)
    examined = [_examine(path) for path in chosen]
    return _render(_merged(early, examined, _set_problems(_cleans(examined))))


def _load(path: Path, text: str) -> _Loaded:
    lines = _front(text)
    if lines is None:
        return _Loaded(_blockless(path, text), [])
    closed = _closed(lines)
    if closed is None:
        return _Loaded(_blockless(path, text), [_UNCLOSED])
    return _parsed(path, closed[0], closed[1])


def _front(text: str) -> list[str] | None:
    lines = text.splitlines(keepends=True)
    if lines and _fence(lines[0]):
        return lines
    return None


def _fence(line: str) -> bool:
    return line.removesuffix("\n") == _FENCE


def _closed(lines: list[str]) -> tuple[str, str] | None:
    index = _close_index(lines)
    if index is None:
        return None
    return "".join(lines[1:index]), "".join(lines[index + 1 :])


def _close_index(lines: list[str]) -> int | None:
    for index, line in enumerate(lines[1:], start=1):
        if _fence(line):
            return index
    return None


def _blockless(path: Path, text: str) -> TaskFile:
    return TaskFile(path.stem, path, (), None, text, False)


def _parsed(path: Path, toml_text: str, body: str) -> _Loaded:
    data, error = _toml(toml_text)
    if error:
        return _Loaded(_blockless(path, body), [error])
    found = _entries(data)
    problems = _file_problems(path.stem, data, found)
    if problems:
        return _Loaded(_blockless(path, body), problems)
    return _Loaded(_valid(path, found or [], data, body), [])


def _toml(text: str) -> tuple[dict[str, object], str]:
    try:
        loaded = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        return {}, f"front matter is not valid TOML: {error}"
    return loaded, ""


def _valid(path: Path, found: list[str], data: dict[str, object], body: str) -> TaskFile:
    depends = tuple(found)
    if depends:
        return TaskFile(path.stem, path, depends, _required_bool(data["stack"]), body, True)
    return TaskFile(path.stem, path, depends, None, body, True)


def _required_bool(value: object) -> bool:
    return value is True


def _file_problems(task_id: str, data: dict[str, object], found: list[str] | None) -> list[str]:
    return _unknown(data) + _depends_problems(task_id, data, found) + _stack(data, found)


def _entries(data: dict[str, object]) -> list[str] | None:
    if "depends" not in data:
        return None
    return _copied(data["depends"])


def _copied(value: object) -> list[str] | None:
    if not isinstance(value, list):
        return None
    found: list[str] = []
    for item in value:
        if not isinstance(item, str):
            return None
        found.append(item)
    return found


def _depends_problems(task_id: str, data: dict[str, object], found: list[str] | None) -> list[str]:
    if "depends" not in data:
        return [_NO_DEPENDS]
    if found is None:
        return [_BAD_DEPENDS]
    return _entry_problems(task_id, found)


def _entry_problems(task_id: str, entries: list[str]) -> list[str]:
    return _invalid(entries) + _repeated(entries) + _self(entries, task_id)


def _invalid(entries: list[str]) -> list[str]:
    return [_entry_line(entry) for entry in entries if _bad_id(entry)]


def _entry_line(entry: str) -> str:
    return f"depends entry '{entry}' is not a task id; use the file name without .md"


def _bad_id(entry: str) -> bool:
    if not entry:
        return True
    if "/" in entry or "\\" in entry:
        return True
    return entry.endswith(".md")


def _repeated(entries: list[str]) -> list[str]:
    counts: dict[str, int] = {}
    order: list[str] = []
    for entry in entries:
        _tally(entry, counts, order)
    return [f"depends lists {entry} twice" for entry in order if counts[entry] > 1]


def _tally(entry: str, counts: dict[str, int], order: list[str]) -> None:
    if entry not in counts:
        order.append(entry)
    counts[entry] = counts.get(entry, 0) + 1


def _self(entries: list[str], task_id: str) -> list[str]:
    if task_id in entries:
        return [_SELF]
    return []


def _unknown(data: dict[str, object]) -> list[str]:
    return [_unknown_line(key) for key in sorted(data) if key not in _ALLOWED]


def _unknown_line(key: str) -> str:
    return f"unknown front matter key {key!r}; allowed keys are depends and stack"


def _stack(data: dict[str, object], found: list[str] | None) -> list[str]:
    if "stack" not in data:
        return _missing_stack(found)
    return _typed(data["stack"]) + _stack_rules(data["stack"], found)


def _missing_stack(found: list[str] | None) -> list[str]:
    if found:
        return [_REQUIRED_STACK]
    return []


def _typed(value: object) -> list[str]:
    if isinstance(value, bool):
        return []
    return [f"stack must be true or false, got {value!r}"]


def _stack_rules(value: object, found: list[str] | None) -> list[str]:
    if found is None:
        return []
    return _ruled(value, found)


def _ruled(value: object, found: list[str]) -> list[str]:
    if found:
        return _true_count(value, len(found))
    return [_EMPTY_STACK, *_true_count(value, 0)]


def _true_count(value: object, count: int) -> list[str]:
    if value is True and count != 1:
        return [f"stack = true needs exactly one dependency, got {count}"]
    return []


def _collect(paths: list[Path]) -> tuple[list[Path], list[tuple[str, list[str]]]]:
    chosen: dict[Path, Path] = {}
    early: list[tuple[str, list[str]]] = []
    for path in paths:
        _take(path, chosen, early)
    return list(chosen.values()), early


def _take(path: Path, chosen: dict[Path, Path], early: list[tuple[str, list[str]]]) -> None:
    if not path.exists():
        early.append((str(path), ["no such file or folder"]))
        return
    if path.is_dir():
        _take_dir(path, chosen)
        return
    _take_file(path, chosen, early)


def _take_dir(path: Path, chosen: dict[Path, Path]) -> None:
    for child in sorted(_markdown(path)):
        _remember(path / child.name, chosen)


def _markdown(path: Path) -> list[Path]:
    return [child for child in path.glob("*.md") if child.name.lower() != "readme.md"]


def _take_file(path: Path, chosen: dict[Path, Path], early: list[tuple[str, list[str]]]) -> None:
    if path.suffix == ".md":
        _remember(path, chosen)
        return
    early.append((str(path), ["not a .md file"]))


def _remember(path: Path, chosen: dict[Path, Path]) -> None:
    chosen.setdefault(path.resolve(), path)


def _examine(path: Path) -> _Examined:
    loaded = _load(path, path.read_text())
    return _Examined(path, path.resolve(), _clean_file(loaded), _gap(loaded))


def _gap(loaded: _Loaded) -> list[str]:
    if loaded.problems:
        return loaded.problems
    if loaded.file.has_front_matter:
        return []
    return [_NO_FRONT]


def _clean_file(loaded: _Loaded) -> TaskFile | None:
    if _gap(loaded):
        return None
    return loaded.file


def _merged(early: list[tuple[str, list[str]]], examined: list[_Examined], extra: dict[Path, list[str]]) -> list[tuple[str, list[str]]]:
    rows = list(early)
    for item in examined:
        _add_row(rows, item, extra)
    return sorted(rows, key=_row_path)


def _add_row(rows: list[tuple[str, list[str]]], item: _Examined, extra: dict[Path, list[str]]) -> None:
    problems = item.problems + extra.get(item.path, [])
    if problems:
        rows.append((str(item.path), problems))


def _row_path(row: tuple[str, list[str]]) -> str:
    return row[0]


def _render(rows: list[tuple[str, list[str]]]) -> list[str]:
    lines: list[str] = []
    for path, problems in rows:
        lines.extend(_prefixed(path, problems))
    return lines


def _prefixed(path: str, problems: list[str]) -> list[str]:
    return [f"{path}: {problem}" for problem in problems]


def _cleans(examined: list[_Examined]) -> list[_Clean]:
    found: list[_Clean] = []
    for item in examined:
        _keep(found, item)
    return found


def _keep(found: list[_Clean], item: _Examined) -> None:
    if item.file is None:
        return
    found.append(_Clean(item.path, item.resolved, item.file))


def _set_problems(clean: list[_Clean]) -> dict[Path, list[str]]:
    problems: dict[Path, list[str]] = {item.path: [] for item in clean}
    _add_duplicates(clean, problems)
    _add_missing(clean, problems)
    _add_cycles(clean, problems)
    return {path: lines for path, lines in problems.items() if lines}


def _add_duplicates(clean: list[_Clean], problems: dict[Path, list[str]]) -> None:
    first: dict[str, Path] = {}
    for item in sorted(clean, key=_clean_path):
        _note_id(item, first, problems)


def _clean_path(item: _Clean) -> str:
    return str(item.path)


def _note_id(item: _Clean, first: dict[str, Path], problems: dict[Path, list[str]]) -> None:
    if item.file.id not in first:
        first[item.file.id] = item.path
        return
    problems[item.path].append(f"duplicate task id {item.file.id}: also {first[item.file.id]}")


def _add_missing(clean: list[_Clean], problems: dict[Path, list[str]]) -> None:
    for item in clean:
        problems[item.path].extend(_missing_lines(item))


def _missing_lines(item: _Clean) -> list[str]:
    return [_missing_line(item.path, dep) for dep in item.file.depends if _missing(item.path, dep)]


def _missing(path: Path, dep: str) -> bool:
    return not (path.parent / f"{dep}.md").exists()


def _missing_line(path: Path, dep: str) -> str:
    return f"depends on {dep}, but {path.parent / f'{dep}.md'} does not exist"


def _add_cycles(clean: list[_Clean], problems: dict[Path, list[str]]) -> None:
    graph, ids = _graph(clean)
    for path, lines in _cycles(graph, ids).items():
        problems[path].extend(lines)


def _graph(clean: list[_Clean]) -> tuple[dict[Path, list[Path]], dict[Path, str]]:
    ordered = sorted(clean, key=_clean_path)
    return _adjacency(ordered), _ids(ordered)


def _adjacency(ordered: list[_Clean]) -> dict[Path, list[Path]]:
    by_resolved = {item.resolved: item for item in ordered}
    return {item.path: _edges(item, by_resolved) for item in ordered}


def _ids(ordered: list[_Clean]) -> dict[Path, str]:
    return {item.path: item.file.id for item in ordered}


def _edges(item: _Clean, by_resolved: dict[Path, _Clean]) -> list[Path]:
    found: list[Path] = []
    for dep in item.file.depends:
        _add_edge(item, dep, by_resolved, found)
    return found


def _add_edge(item: _Clean, dep: str, by_resolved: dict[Path, _Clean], found: list[Path]) -> None:
    linked = by_resolved.get(_resolved_dep(item.path, dep))
    if linked is None:
        return
    found.append(linked.path)


def _resolved_dep(path: Path, dep: str) -> Path:
    return (path.parent / f"{dep}.md").resolve()


def _cycles(graph: dict[Path, list[Path]], ids: dict[Path, str]) -> dict[Path, list[str]]:
    state = _Search(graph, ids, [], set(), set(), {})
    for start in graph:
        _walk(start, state)
    return state.lines


def _walk(node: Path, state: _Search) -> None:
    state.stack.append(node)
    state.stacked.add(node)
    _follow(node, state)
    state.stack.pop()
    state.stacked.remove(node)


def _follow(node: Path, state: _Search) -> None:
    for nxt in state.graph[node]:
        _step(nxt, state)


def _step(nxt: Path, state: _Search) -> None:
    if nxt in state.stacked:
        _record(state, nxt)
        return
    _walk(nxt, state)


def _record(state: _Search, back: Path) -> None:
    cycle = state.stack[state.stack.index(back) :]
    key = tuple(_turned(cycle, state.ids))
    if key in state.seen:
        return
    state.seen.add(key)
    state.lines.setdefault(_holder(cycle, state.ids), []).append(f"dependency cycle: {' -> '.join(key)}")


def _turned(cycle: list[Path], ids: dict[Path, str]) -> list[str]:
    names = [ids[path] for path in cycle]
    index = names.index(min(names))
    ordered = names[index:] + names[:index]
    return [*ordered, ordered[0]]


def _holder(cycle: list[Path], ids: dict[Path, str]) -> Path:
    names = [ids[path] for path in cycle]
    return cycle[names.index(min(names))]
