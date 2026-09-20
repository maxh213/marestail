import re
from pathlib import Path

from marestail import freeze
from marestail.config import Config

SCENARIO = re.compile(r"^[^\S\n]*Scenario(?: Outline)?:", re.MULTILINE)
SPACE = re.compile(r"\s")
BULLET = "-"
ARROW = "->"
SEPARATOR = "::"

Trace = tuple[str, str, str]
Found = tuple[Trace, int]


def listing(folder: Path, pattern: str) -> list[Path]:
    return sorted(folder.glob(pattern)) if folder.exists() else []


def related_files(folder: Path, pattern: str, task_name: str) -> list[Path]:
    files = listing(folder, pattern)
    return [file for file in files if is_related(file, task_name)] or files


def is_related(file: Path, task_name: str) -> bool:
    return file.stem in task_name or task_name.endswith(file.stem)


def feature_files(config: Config, task_name: str) -> list[Path]:
    return related_files(config.root / "features", "*.feature", task_name)


def scenarios(files: list[Path]) -> list[str]:
    return [title for file in files for title in scenario_titles(file.read_text())]


def scenario_titles(text: str) -> list[str]:
    titles: list[str] = []
    found = SCENARIO.search(text)
    for _ in range(len(text) + 1):
        if not found:
            break
        title, end = scenario_title(text, found.end())
        titles += title
        found = SCENARIO.search(text, end)
    return titles


def scenario_title(text: str, start: int) -> tuple[list[str], int]:
    begin = skip_spaces(text, start)
    if begin == len(text):
        return last_visible(text[start:]), begin
    title = text[begin : line_end(text, begin)].rstrip()
    return [title], begin + len(title)


def last_visible(spaces: str) -> list[str]:
    last = spaces.replace("\n", "")[-1:]
    return [last] if last else []


def skip_spaces(text: str, start: int) -> int:
    return len(text) - len(text[start:].lstrip())


def word_end(text: str, start: int) -> int:
    found = SPACE.search(text, start)
    return found.start() if found else len(text)


def line_end(text: str, start: int) -> int:
    end = text.find("\n", start)
    return len(text) if end < 0 else end


def traces(text: str) -> list[Trace]:
    found: list[Trace] = []
    start = 0
    for _ in range(len(text) + 1):
        if start >= len(text):
            break
        trace, end = line_trace(text, start)
        found += trace
        start = line_end(text, end) + 1
    return found


def line_trace(text: str, start: int) -> tuple[list[Trace], int]:
    dash = skip_spaces(text, start)
    if dash >= line_end(text, start) or text[dash] != BULLET:
        return [], start
    found = bullet_trace(text, dash + 1)
    return ([found[0]], found[1]) if found else ([], start)


def bullet_trace(text: str, start: int) -> Found | None:
    begin = skip_spaces(text, start)
    return titled_trace(text, begin) or spaced_trace(text, start, begin)


def titled_trace(text: str, begin: int) -> Found | None:
    for arrow in arrows(text, begin):
        target = arrow_target(text, arrow)
        if target:
            return (text[begin:arrow].rstrip(), *target[0]), target[1]
    return None


def spaced_trace(text: str, start: int, begin: int) -> Found | None:
    gap = text[start:begin].replace("\n", "")
    target = arrow_target(text, begin) if gap else None
    return ((gap[-1], *target[0]), target[1]) if target else None


def arrows(text: str, begin: int) -> list[int]:
    end = line_end(text, begin)
    found = [index for index in range(begin + 1, end) if text.startswith(ARROW, index)]
    return [*found, skip_spaces(text, end)] if end < len(text) else found


def arrow_target(text: str, arrow: int) -> tuple[tuple[str, str], int] | None:
    if not text.startswith(ARROW, arrow):
        return None
    begin = skip_spaces(text, arrow + len(ARROW))
    split = text.find(SEPARATOR, begin + 1, word_end(text, begin))
    return named_target(text, begin, split) if split >= 0 else None


def named_target(text: str, begin: int, split: int) -> tuple[tuple[str, str], int] | None:
    start = split + len(SEPARATOR)
    rest = text[start : line_end(text, start)]
    name = rest.rstrip() or rest[:1]
    return ((text[begin:split], name), start + len(name)) if name else None


def problems(config: Config, task_name: str, handoff: str, role: str = "coder") -> list[str]:
    titles = scenarios(feature_files(config, task_name))
    if not titles:
        return ["audit: no feature file found for this task"]
    found = traces(handoff)
    frozen = frozen_traces(config, role, found)
    return untraced(titles, found) + unwritable(frozen, role) + broken(config, found, frozen)


def untraced(titles: list[str], found: list[Trace]) -> list[str]:
    covered = {normalise(title) for title, _, _ in found}
    return [f"audit: no test traced for scenario '{title}'" for title in titles if normalise(title) not in covered]


def frozen_traces(config: Config, role: str, found: list[Trace]) -> list[tuple[str, str]]:
    return [(file, name) for _, file, name in found if freeze.frozen_paths(config, role, [file])]


def unwritable(frozen: list[tuple[str, str]], role: str) -> list[str]:
    return [
        f"audit: {file}::{name} is under a path the {role} cannot edit, so it cannot prove this scenario; "
        "end-to-end tests under qa/ are written by the QA role. Trace the scenario to a test you can write."
        for file, name in frozen
    ]


def broken(config: Config, found: list[Trace], frozen: list[tuple[str, str]]) -> list[str]:
    return [
        f"audit: {file}::{name} not found" for _, file, name in found if (file, name) not in frozen and not test_exists(config, file, name)
    ]


def normalise(title: str) -> str:
    return re.sub(r"\W+", " ", title).strip().casefold()


def test_exists(config: Config, file: str, name: str) -> bool:
    path = config.root / file
    return path.is_file() and name.split(SEPARATOR)[-1] in path.read_text()


def instructions(config: Config, task_name: str) -> str:
    files = ", ".join(str(file.relative_to(config.root)) for file in feature_files(config, task_name)) or "features/*.feature"
    return (
        f"Audit before you hand off. Re-read the task and {files}. For every Scenario, find the test that proves it "
        "and would fail if that behaviour broke. If one has none, write it. Then in the handoff, under `## Audit`, "
        "write one line per scenario: `- <scenario title> -> <test file path>::<test function name>`. "
        "The runner checks every scenario is traced and every test exists. Trace only to tests you can write: never "
        "to files under `qa/` or `features/`, which are frozen for you; the QA role writes the end-to-end test from "
        "the QA procedure after the hardener."
    )
