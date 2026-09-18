from pathlib import Path

from marestail import audit
from marestail.config import Config
from marestail.pipeline import Judge, Worker

ROLES_DIR = Path(__file__).resolve().parent.parent / "roles"
WORKER_SCOPE = (
    "This run has a hard scope: {paths}. Make the task's change inside these paths. Outside them, make only the smallest "
    "supporting edits the change cannot work without, such as a caller, an import or a test; refactor, rename, reformat and "
    "clean up nothing outside them, even where your role asks for it. The gates measure only these paths."
)
JUDGE_SCOPE = (
    "This run has a hard scope: {paths}. The gate report covers only these paths, and code outside them is not the task's to "
    "improve. Outside them, bounce only when the diff goes beyond the smallest supporting edits the change needs, such as a "
    "refactor, a rename or a cleanup, naming the file and line."
)


def worker_prompt(
    config: Config,
    worker: Worker,
    task: Path,
    task_name: str,
    report: Path,
    feedback: str,
    label: str = "",
    gate_flags: str = "",
    hard_focus: set[str] | None = None,
) -> str:
    parts = [
        role_text(worker.name),
        section("Task", task.read_text()),
        *hard_scope(hard_focus, WORKER_SCOPE),
        section("Specification files", spec_listing(config, task_name)),
        section("Handoffs so far", handoffs(config, task_name)),
        section("Finishing", finishing(config, worker, task_name, report, label, gate_flags)),
    ]
    if feedback:
        parts.append(section("Why the work came back to you", feedback))
    return "\n\n".join(parts)


def judge_prompt(
    config: Config,
    judge: Judge,
    task: Path,
    task_name: str,
    report: Path,
    gate_report: str,
    trees: str = "",
    feedback: str = "",
    hard_focus: set[str] | None = None,
) -> str:
    parts = [
        role_text(judge.name),
        section("Task", task.read_text()),
        *hard_scope(hard_focus, JUDGE_SCOPE),
        section("Specification", spec_contents(config, task_name) if judge.name == "critic" else spec_listing(config, task_name)),
    ]
    if gate_report:
        parts.append(section("Gate report", gate_report))
    if trees:
        parts.append(section("Trees", trees))
    parts.append(section("Handoffs so far", handoffs(config, task_name)))
    if judge.name == "perf":
        parts.append(
            section(
                "Benches frozen",
                (
                    "`perf/` is frozen in this phase and the runner has already taken every missing sample; any edit you make to `perf/` "
                    "is discarded. If a bench is missing or broken, write `VERDICT: AUTHOR` as your first line to return to the authoring "
                    "phase. Otherwise analyse the measurements and write your verdict."
                ),
            )
        )
    parts.append(section("Verdict", verdict_instructions(report, judge)))
    if feedback:
        parts.append(section("Why your verdict was rejected", feedback))
    return "\n\n".join(parts)


def role_text(name: str) -> str:
    return (ROLES_DIR / f"{name}.md").read_text().strip()


def section(title: str, body: str) -> str:
    return f"# {title}\n{body.strip()}"


def hard_scope(focus: set[str] | None, template: str) -> list[str]:
    if not focus:
        return []
    return [section("Scope", template.format(paths=", ".join(f"`{path}`" for path in sorted(focus))))]


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
    if files:
        return "\n\n".join(f"## {f.stem}\n{f.read_text().strip()}" for f in files)
    return handoffs_from_history(config) or "none"


def handoffs_from_history(config: Config) -> str:
    from marestail.shell import run

    base = config.get("git", "base", "origin/master")
    _, log = run(["git", "log", "--reverse", "--format=## %s%n%b%n", f"{base}..HEAD"], cwd=config.root)
    entries = [entry for entry in log.split("## ") if "By " in entry]
    return "\n".join(f"## {entry.strip()}" for entry in entries)


def finishing(config: Config, worker: Worker, task_name: str, report: Path, label: str = "", gate_flags: str = "") -> str:
    steps = []
    if worker.audit:
        steps.append(audit.instructions(config, task_name))
    if worker.tier:
        steps.append(f"Run `marestail gate --tier {worker.tier}{gate_flags}` and keep working until it prints GATE PASSED.")
    opening = f"starting with `[{label}] ` and " if label else ""
    steps.append(
        f"Commit tracked changes with a message {opening}ending in `By {worker.name}.` "
        "Do not use `git add -f` or `--force`. Paths ignored by `.gitignore` stay untracked on disk; "
        "the next role still reads them. The runner drops any gitignored path you force-add."
    )
    steps.append(
        f"Write {report.relative_to(config.root)}: what you did, what is left, what the next role must know. "
        "Under 40 lines, plus the audit section if one is required."
    )
    steps.append(
        "Do not change gate configuration, the feature files, or the QA procedure; the runner reverts such changes. "
        "If you believe one is needed, say so under `## Config change` in the handoff with the reason; a human "
        "sees it after the run. Then find a way within the current configuration." + csproj_note(config)
    )
    return "\n".join(f"{i}. {step}" for i, step in enumerate(steps, start=1))


def csproj_note(config: Config) -> str:
    if config.section("dotnet") is None:
        return ""
    return (
        ' The one frozen edit that is kept: adding `<PackageReference Include="..." Version="..." />` or '
        '`<InternalsVisibleTo Include="..." />` lines to a `.csproj`. Any other csproj change, including removing or '
        "changing a line, is reverted."
    )


def verdict_instructions(report: Path, judge: Judge) -> str:
    return (
        f"Write your verdict to {report} and {allowed_edits(judge)}; the runner discards other edits.\n"
        f"{bounce_choices(judge)} Then numbered findings, each naming "
        "the file, scenario or step concerned and the fix required. Under 40 lines."
    )


def allowed_edits(judge: Judge) -> str:
    if not judge.writes:
        return "nothing else. Do not edit any other file"
    patterns = ", ".join(f"`{pattern}`" for pattern in judge.writes)
    return f"edit nothing else except files matching {patterns}"


def bounce_choices(judge: Judge) -> str:
    if judge.pinned_bounce:
        return f"First line: `VERDICT: PASS`, or `VERDICT: BOUNCE` to send the work back to the {judge.bounce_to}."
    return (
        "First line: `VERDICT: PASS`, or `VERDICT: BOUNCE` to send the work back to the usual role, or "
        "`VERDICT: BOUNCE <role>` to send it to a different one, for example `VERDICT: BOUNCE specifier` when the "
        "defect is in the feature file or the QA procedure rather than the code."
    )


def perf_author_prompt(config: Config, task: Path, task_name: str, trees: str, note: Path, feedback: str = "") -> str:
    parts = [
        role_text("perf"),
        section("Task", task.read_text()),
        section("Specification", spec_listing(config, task_name)),
        section("Handoffs so far", handoffs(config, task_name)),
    ]
    if trees:
        parts.append(section("Trees", trees))
    parts.append(
        section(
            "Authoring",
            (
                "This is the authoring phase: `perf/` is editable now and frozen afterwards. Look at the diff since the task's start "
                "commit and make sure every endpoint and function the task added or changed is measured by a `perf/bench_*` script, "
                "extending or creating benches as needed and keeping every existing target. Do not take samples: the runner measures "
                "between the phases. If the benches already cover the diff, change nothing. Write one short paragraph on what you "
                f"changed (or `nothing`) to {note}. Edit nothing outside `perf/`."
            ),
        )
    )
    if feedback:
        parts.append(section("Earlier feedback", feedback))
    return "\n\n".join(parts)
