import contextlib
import json
import shutil
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from marestail.config import Config
from marestail.perf import settings, table
from marestail.shell import run, tail

NO_PRE_MARESTAIL = "no commit before marestail.toml; skipping the pre-marestail row"


@dataclass(frozen=True)
class Tree:
    name: str
    sha: str
    path: Path


@dataclass
class Session:
    task: str
    trees: list[Tree] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def work(config: Config) -> Path:
    folder = config.work / "perf"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def trees_file(config: Config) -> Path:
    return work(config) / "trees.json"


def samples_file(config: Config) -> Path:
    return work(config) / "samples.jsonl"


def start_file(config: Config, task: str) -> Path:
    return config.work / "runs" / task / "start-commit"


def head(config: Config) -> str:
    _, output = run(["git", "rev-parse", "HEAD"], cwd=config.root)
    return output.strip()


def record_start(config: Config, task: str) -> None:
    path = start_file(config, task)
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(head(config) + "\n")


def start_commit(config: Config, task: str) -> tuple[str, str]:
    path = start_file(config, task)
    if path.exists():
        return path.read_text().strip(), ""
    base = config.get("git", "base", "origin/master")
    code, output = run(["git", "merge-base", base, "HEAD"], cwd=config.root)
    if code == 0:
        return output.strip(), f"no recorded start commit for {task}; using git merge-base {base} HEAD"
    return head(config), f"no recorded start commit for {task} and no merge-base with {base}; using HEAD"


def archive_start(config: Config, task: str, destination: Path | None) -> None:
    path = start_file(config, task)
    if not path.exists():
        return
    if destination is None:
        path.unlink()
        return
    shutil.move(str(path), str(destination / path.name))


def pre_marestail_commit(config: Config) -> tuple[str | None, str]:
    if table.load(config.root).rows:
        return None, ""
    _, output = run(["git", "log", "--diff-filter=A", "--reverse", "--format=%H", "--", "marestail.toml"], cwd=config.root)
    added = output.split()
    if not added:
        return None, NO_PRE_MARESTAIL
    code, parent = run(["git", "rev-parse", "--verify", "--quiet", f"{added[0]}^"], cwd=config.root)
    return (parent.strip(), "") if code == 0 else (None, NO_PRE_MARESTAIL)


@contextlib.contextmanager
def measuring(config: Config, task: str) -> Iterator[Session]:
    session = Session(task)
    try:
        populate(config, session)
        yield session
    finally:
        close(config, session)


def populate(config: Config, session: Session) -> None:
    start, start_note = start_commit(config, session.task)
    pre, pre_note = pre_marestail_commit(config)
    session.notes += [note for note in (start_note, pre_note) if note]
    for note in session.notes:
        print(f"   {note}")
    add_tree(config, session, "baseline", start)
    session.trees.append(Tree("head", head(config), config.root))
    if pre:
        add_tree(config, session, "pre-marestail", pre)
    write_trees(config, session)
    samples_file(config).write_text("")


def add_tree(config: Config, session: Session, name: str, sha: str) -> None:
    path = Path(tempfile.mkdtemp(prefix="marestail-perf-"))
    session.trees.append(Tree(name, sha, path))
    code, output = run(["git", "worktree", "add", "--detach", str(path), sha], cwd=config.root)
    if code != 0:
        raise RuntimeError(f"git worktree add for the {name} tree at {sha} failed: {' '.join(tail(output, 5))}")
    setup = config.get("perf", "setup")
    if not setup:
        return
    code, output = run(["bash", "-lc", setup], cwd=path, timeout=3600)
    if code != 0:
        session.notes.append(f"[perf] setup failed in the {name} tree (exit {code}): {' | '.join(tail(output, 10))}")


def write_trees(config: Config, session: Session) -> None:
    data = {
        "task": session.task,
        "trees": [{"tree": tree.name, "sha": tree.sha, "path": str(tree.path)} for tree in session.trees],
    }
    trees_file(config).write_text(json.dumps(data, indent=2) + "\n")


def close(config: Config, session: Session) -> None:
    for tree in session.trees:
        if tree.name == "head":
            continue
        run(["git", "worktree", "remove", "--force", str(tree.path)], cwd=config.root)
        shutil.rmtree(tree.path, ignore_errors=True)
    run(["git", "worktree", "prune"], cwd=config.root)
    trees_file(config).unlink(missing_ok=True)


def prompt_section(config: Config, session: Session) -> str:
    lines = [f"- {tree.name}: {tree.sha} at {tree.path}" for tree in session.trees]
    existing = table.load(config.root).columns
    lines += [
        f"- threshold_percent: {settings.threshold_percent(config):g}",
        f"- min_runs: {settings.min_runs(config)}",
        "- existing columns every run must re-measure: " + (", ".join(f"`{column}`" for column in existing) or "none"),
    ]
    lines += [f"- note: {note}" for note in session.notes]
    return "\n".join(lines)
