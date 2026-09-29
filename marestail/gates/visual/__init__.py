import os
import time
from pathlib import Path
from typing import Any

from marestail import worktree
from marestail.config import Config
from marestail.context import Context
from marestail.gates.visual import _capture as capture
from marestail.gates.visual import _judge as judge
from marestail.gates.visual import _spec as spec_module
from marestail.gates.visual._model import Report, Reproduction, Shot, Spec, TreeRun
from marestail.gates.visual._pictures import judge_section, judge_skip
from marestail.gates.visual._reported import reported_section
from marestail.gates.visual._spec import bug_report
from marestail.report import Result, elapsed

__all__ = ["Reproduction", "bug_report", "capture_command", "judge_section", "judge_skip", "reported_section", "reproduce", "run_gate"]

_GATE = "visual"
_TASK_ENV = "MARESTAIL_TASK"
_GATE_CAPTURES = 2
_HAND_CAPTURES = 1
_STATUS = "status"
_BASE = "base"


def run_gate(ctx: Context) -> Result:
    started = time.time()
    task = os.environ.get(_TASK_ENV, "")
    skip = spec_module.skip_reason(ctx.config, task)
    if skip is not None:
        return Result(_GATE, True, skip)
    findings, verdict = _gate_findings(ctx.config, task)
    return Result(_GATE, not findings, verdict, findings, elapsed(started))


def _gate_findings(config: Config, task: str) -> tuple[list[str], str]:
    spec, problems = _checked(spec_module.load(config, task))
    if spec is None:
        return problems, _failed_verdict(problems)
    sha, _ = worktree.start_commit(config, task)
    base, head = capture.capture_trees(config, spec, sha, _GATE_CAPTURES)
    findings = _with_symptom(judge.findings(base, head, spec), spec)
    return findings, _verdict(findings, len(spec.settings.viewports))


def _checked(loaded: tuple[Spec | None, list[str]]) -> tuple[Spec | None, list[str]]:
    spec, problems = loaded
    if spec is None:
        return None, problems
    tools = capture.tool_problems()
    return (None, tools) if tools else (spec, [])


def _with_symptom(findings: list[str], spec: Spec) -> list[str]:
    if not findings or not spec.block.symptom:
        return findings
    return [*findings, f"  symptom: {spec.block.symptom}"]


def _verdict(findings: list[str], count: int) -> str:
    if findings:
        return _failed_verdict(findings)
    return f"{count} viewport{_plural(count)}, geometry holds"


def _failed_verdict(findings: list[str]) -> str:
    return f"{_finding_count(findings)} visual findings"


def _finding_count(lines: list[str]) -> int:
    return sum(1 for line in lines if not line.startswith("  "))


def _plural(count: int) -> str:
    return "" if count == 1 else "s"


def capture_command(config: Config, task: str) -> int:
    if config.section(spec_module.SECTION) is None:
        print("skipped: no [visual] section in marestail.toml")
        return 0
    skip = spec_module.skip_reason(config, task)
    if skip:
        print(skip)
        return 0
    return _hand_capture(config, task)


def _hand_capture(config: Config, task: str) -> int:
    spec, problems = _checked(spec_module.load(config, task))
    if spec is None:
        print("\n".join(problems))
        return 2
    sha, note = worktree.start_commit(config, task)
    if note:
        print(note)
    base, head = capture.capture_trees(config, spec, sha, _HAND_CAPTURES)
    return _report_capture(config, spec, [base, head])


def _report_capture(config: Config, spec: Spec, runs: list[TreeRun]) -> int:
    problems = [line for run in runs for line in run.problems]
    print("\n".join(problems + _folder_lines(config, spec, runs)))
    return 1 if problems else 0


def _folder_lines(config: Config, spec: Spec, runs: list[TreeRun]) -> list[str]:
    root = spec_module.visual_dir(config, spec.task)
    folders = [(run.tree.folder, name) for run in runs for name in run.shots]
    return [f"{folder} {name}: {_shown_path(config, root / folder / name)}" for folder, name in folders]


def _shown_path(config: Config, path: Path) -> str:
    return str(path.relative_to(config.root))


def reproduce(config: Config, report: Report) -> Reproduction | None:
    print(f"== reported ({report.where} at the start commit)")
    sha, _ = worktree.start_commit(config, report.task)
    run, reasons = _reported_run(config, report, sha)
    if run is None or reasons:
        print("\n".join(f"reported: {line}" for line in reasons))
        return None
    print("\n".join(_reported_folders(config, report, run)))
    return Reproduction(report, sha, list(run.shots), _frames(run))


def _reported_folders(config: Config, report: Report, run: TreeRun) -> list[str]:
    folder = spec_module.reported_dir(config, report.task)
    return [f"reported {name}: {_shown_path(config, folder / name)}" for name in run.shots]


def _frames(run: TreeRun) -> dict[str, dict[str, Any] | None]:
    return {name: shot.details.get("frame") for name, shot in run.shots.items()}


def _reported_run(config: Config, report: Report, sha: str) -> tuple[TreeRun | None, list[str]]:
    spec, problems = _checked(spec_module.reported_spec(config, report))
    if spec is None:
        return None, problems
    run = capture.capture_reported(config, spec, sha)
    return run, run.problems or _shot_reasons(run, spec)


def _shot_reasons(run: TreeRun, spec: Spec) -> list[str]:
    lines = [line for name, shot in run.shots.items() for line in _view_reasons(name, shot, spec)]
    return list(dict.fromkeys(lines))


def _view_reasons(name: str, shot: Shot, spec: Spec) -> list[str]:
    if shot.failure == _STATUS:
        return [f"{spec.block.route} answered {shot.details[_STATUS]}"]
    return judge.stop_findings(name, [(_BASE, shot)], spec)
