from pathlib import Path

from marestail import audit
from marestail.config import Config
from marestail.pipeline import Judge, Worker

ROLES_DIR = Path(__file__).resolve().parent.parent / "roles"


def worker_prompt(config: Config, worker: Worker, task: Path, task_name: str, report: Path, feedback: str) -> str:
    parts = [
        role_text(worker.name),
        section("Task", task.read_text()),
        section("Specification files", spec_listing(config, task_name)),
        section("Handoffs so far", handoffs(config, task_name)),
        section("Finishing", finishing(config, worker, task_name, report)),
    ]
    if feedback:
        parts.append(section("Why the work came back to you", feedback))
    return "\n\n".join(parts)


def judge_prompt(config: Config, judge: Judge, task: Path, task_name: str, report: Path, gate_report: str) -> str:
    parts = [
        role_text(judge.name),
        section("Task", task.read_text()),
        section("Specification", spec_contents(config, task_name) if judge.name == "critic" else spec_listing(config, task_name)),
    ]
    if gate_report:
        parts.append(section("Gate report", gate_report))
    parts += [
        section("Handoffs so far", handoffs(config, task_name)),
        section("Verdict", verdict_instructions(report)),
    ]
    return "\n\n".join(parts)


def role_text(name: str) -> str:
    return (ROLES_DIR / f"{name}.md").read_text().strip()


def section(title: str, body: str) -> str:
    return f"# {title}\n{body.strip()}"


def spec_listing(config: Config, task_name: str) -> str:
    files = audit.feature_files(config, task_name) + qa_files(config, task_name)
    return "\n".join(str(f.relative_to(config.root)) for f in files) or "none yet"


def spec_contents(config: Config, task_name: str) -> str:
    files = audit.feature_files(config, task_name) + qa_files(config, task_name)
    return "\n\n".join(f"## {f.relative_to(config.root)}\n{f.read_text().strip()}" for f in files) or "none yet"


def qa_files(config: Config, task_name: str) -> list[Path]:
    folder = config.root / "qa"
    files = sorted(folder.glob("*.md")) if folder.exists() else []
    related = [f for f in files if f.stem in task_name or task_name.endswith(f.stem)]
    return related or files


def handoffs(config: Config, task_name: str) -> str:
    folder = config.work / "handoffs" / task_name
    files = sorted(folder.glob("*.md")) if folder.exists() else []
    return "\n\n".join(f"## {f.stem}\n{f.read_text().strip()}" for f in files) or "none"


def finishing(config: Config, worker: Worker, task_name: str, report: Path) -> str:
    steps = []
    if worker.audit:
        steps.append(audit.instructions(config, task_name))
    if worker.tier:
        steps.append(f"Run `marestail gate --tier {worker.tier}` and keep working until it prints GATE PASSED.")
    steps.append(f"Commit everything with a message ending in `By {worker.name}.`")
    steps.append(
        f"Write {report.relative_to(config.root)}: what you did, what is left, what the next role must know. "
        "Under 40 lines, plus the audit section if one is required."
    )
    steps.append("Never change gate configuration, the feature files, or the QA procedure; the runner rejects such commits.")
    return "\n".join(f"{i}. {step}" for i, step in enumerate(steps, start=1))


def verdict_instructions(report: Path) -> str:
    return (
        f"Write your verdict to {report} and nothing else. Do not edit any other file; the runner discards other edits.\n"
        "First line: `VERDICT: PASS` or `VERDICT: BOUNCE`. Then numbered findings, each naming the file, scenario or "
        "step concerned and the fix required. Under 40 lines."
    )
