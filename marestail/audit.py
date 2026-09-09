import re
from pathlib import Path

from marestail.config import Config

SCENARIO = re.compile(r"^\s*Scenario(?: Outline)?:\s*(.+?)\s*$", re.MULTILINE)
TRACE = re.compile(r"^\s*-\s*(.+?)\s*->\s*(\S+?)::(.+?)\s*$", re.MULTILINE)


def feature_files(config: Config, task_name: str) -> list[Path]:
    folder = config.root / "features"
    files = sorted(folder.glob("*.feature")) if folder.exists() else []
    related = [f for f in files if f.stem in task_name or task_name.endswith(f.stem)]
    return related or files


def scenarios(files: list[Path]) -> list[str]:
    return [title for file in files for title in SCENARIO.findall(file.read_text())]


def problems(config: Config, task_name: str, handoff: str) -> list[str]:
    titles = scenarios(feature_files(config, task_name))
    traces = TRACE.findall(handoff)
    covered = {normalise(title) for title, _, _ in traces}
    missing = [f"audit: no test traced for scenario '{t}'" for t in titles if normalise(t) not in covered]
    broken = [f"audit: {file}::{name} not found" for _, file, name in traces if not test_exists(config, file, name)]
    if not titles:
        return ["audit: no feature file found for this task"]
    return missing + broken


def normalise(title: str) -> str:
    return re.sub(r"\W+", " ", title).strip().casefold()


def test_exists(config: Config, file: str, name: str) -> bool:
    path = config.root / file
    return path.is_file() and name.split("::")[-1] in path.read_text()


def instructions(config: Config, task_name: str) -> str:
    files = ", ".join(str(f.relative_to(config.root)) for f in feature_files(config, task_name)) or "features/*.feature"
    return (
        f"Audit before you hand off. Re-read the task and {files}. For every Scenario, find the test that proves it "
        "and would fail if that behaviour broke. If one has none, write it. Then in the handoff, under `## Audit`, "
        "write one line per scenario: `- <scenario title> -> <test file path>::<test function name>`. "
        "The runner checks every scenario is traced and every test exists."
    )
