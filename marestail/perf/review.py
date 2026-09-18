import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from marestail.config import Config
from marestail.perf import hygiene, results, settings, table, trees
from marestail.shell import run

CHANGED = ("degraded", "improved", "removed")
SETUP_NEEDED = "## Setup needed"


@dataclass(frozen=True)
class Review:
    problems: list[str]
    classified: list[results.Classified]
    used_db: bool


def review(config: Config, session: trees.Session, report: Path, verdict: str) -> Review:
    records = load_records(config)
    benches = bench_scripts(config)
    tree_names = [tree.name for tree in session.trees]
    policy = settings.policy(config)
    measurements, problems = results.compile_records(records, tree_names, benches, policy)
    classified = [results.classify(measurement, policy) for measurement in measurements]
    write_results(report, classified)
    problems += results.audit(classified, table.load(config.root).columns, report.read_text(), verdict, benches)
    problems += csharp_problems(config)
    problems += results.stale_problems(records, fingerprints(config, benches))
    return Review(problems, classified, used_db(records))


def fingerprints(config: Config, benches: list[str]) -> dict[str, str]:
    return {bench: hygiene.fingerprint(config.root, bench) for bench in benches}


def used_db(records: list[dict[str, Any]]) -> bool:
    return any(record["db"] for record in records)


def csharp_problems(config: Config) -> list[str]:
    projects = csharp_projects(config)
    folder = config.root / "perf"
    if not projects or not folder.is_dir():
        return []
    return [
        f"`{path.relative_to(config.root).as_posix()}` would compile into {', '.join(projects)}, because an SDK project at the repo root "
        "includes every .cs file below it; write this bench in another language"
        for path in sorted(folder.rglob("*.cs"))
    ]


def csharp_projects(config: Config) -> list[str]:
    return sorted(path.name for path in config.root.glob("*.csproj"))


def load_records(config: Config) -> list[dict[str, Any]]:
    path = trees.samples_file(config)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def bench_scripts(config: Config) -> list[str]:
    folder = config.root / "perf"
    if not folder.is_dir():
        return []
    return sorted(path.relative_to(config.root).as_posix() for path in folder.glob("bench_*") if path.is_file())


def write_results(report: Path, classified: list[results.Classified]) -> None:
    data = {"measurements": [asdict(item.measurement) | {"status": item.status, "change": item.change} for item in classified]}
    report.with_suffix(".results.json").write_text(json.dumps(data, indent=2) + "\n")


def record_table(config: Config, session: trees.Session, outcome: Review) -> None:
    if not outcome.classified:
        return
    table.write(config.root, snapshot_for(config, session, outcome))
    stage_table(config)


def snapshot_for(config: Config, session: trees.Session, outcome: Review) -> table.Snapshot:
    head = tree_named(session, "head")
    pre = tree_named(session, table.PRE_MARESTAIL)
    return table.Snapshot(
        task=session.task,
        commit=short(config, head.sha if head else "HEAD"),
        date=time.strftime("%Y-%m-%d"),
        rows=rows_cell(config, outcome.used_db),
        pre_commit=short(config, pre.sha) if pre else None,
        classified=outcome.classified,
    )


def stage_table(config: Config) -> None:
    ignored, _ = run(["git", "check-ignore", "-q", table.FILENAME], cwd=config.root)
    if ignored != 0:
        run(["git", "add", "--", table.FILENAME], cwd=config.root)


def tree_named(session: trees.Session, name: str) -> trees.Tree | None:
    return next((tree for tree in session.trees if tree.name == name), None)


def short(config: Config, sha: str) -> str:
    _, output = run(["git", "rev-parse", "--short", sha], cwd=config.root)
    return output.strip()


def rows_cell(config: Config, used_db: bool) -> str:
    return str(settings.effective_rows(config)[0]) if used_db else table.EMPTY


def changes_summary(outcome: Review, verdict_text: str, session: trees.Session) -> str:
    lines = ["## Performance changes", *database_lines(session), *change_lines(outcome), *setup_lines(verdict_text)]
    return "\n".join(lines)


def database_lines(session: trees.Session) -> list[str]:
    if not session.image:
        return []
    return [f"- Postgres image: {session.image} ({session.image_source})", f"- rows: {session.rows} ({session.rows_source})"]


def change_lines(outcome: Review) -> list[str]:
    return [f"- {item.status} {change_text(item)}`{item.measurement.column}`" for item in changed_items(outcome)] or ["- none"]


def changed_items(outcome: Review) -> list[results.Classified]:
    return [item for item in outcome.classified if item.status in CHANGED]


def setup_lines(verdict_text: str) -> list[str]:
    setup = setup_needed(verdict_text)
    return ["", "### Setup needed", setup] if setup else []


def change_text(item: results.Classified) -> str:
    return "" if item.change is None else f"{item.change:+.1f}% "


def setup_needed(text: str) -> str:
    if SETUP_NEEDED not in text:
        return ""
    return text.split(SETUP_NEEDED, 1)[1].split("\n## ", 1)[0].strip()
