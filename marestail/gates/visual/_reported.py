from pathlib import Path
from typing import Any

from marestail import worktree
from marestail.config import Config
from marestail.gates.visual import _capture as capture
from marestail.gates.visual import _judge as judge
from marestail.gates.visual import _spec as spec_module
from marestail.gates.visual._model import Report, Reproduction, Shot, Spec, TreeRun

_SHOTS = ("viewport.png", "element.png")
_BLIND = "The pictures could not be shown: {backend} cannot read images. Judge from the geometry and markup alone."
_NO_SELECTOR = "none: the task has no selector: line"
_NO_SRC = "The frame has no src; it was not loaded alone."
_ANSWERED_FROM = 400
_STATUS = "status"
_BASE = "base"
_Frame = dict[str, Any]


def reproduce(config: Config, report: Report) -> Reproduction | None:
    print(f"== reported ({report.where} at the start commit)")
    sha, _ = worktree.start_commit(config, report.task)
    run, reasons = _reported_run(config, report, sha)
    if run is None or reasons:
        print("\n".join(f"reported: {line}" for line in reasons))
        return None
    print("\n".join(_reported_folders(config, report, run)))
    return Reproduction(report, sha, list(run.shots), _frames_seen(run))


def _reported_run(config: Config, report: Report, sha: str) -> tuple[TreeRun | None, list[str]]:
    spec, problems = _checked(config, report)
    if spec is None:
        return None, problems
    run = capture.capture_reported(config, spec, sha)
    return run, run.problems or _shot_reasons(run, spec)


def _checked(config: Config, report: Report) -> tuple[Spec | None, list[str]]:
    spec, problems = spec_module.reported_spec(config, report)
    problems = problems or capture.tool_problems()
    return (None, problems) if problems else (spec, [])


def _shot_reasons(run: TreeRun, spec: Spec) -> list[str]:
    lines = [line for name, shot in run.shots.items() for line in _view_reasons(name, shot, spec)]
    return list(dict.fromkeys(lines))


def _view_reasons(name: str, shot: Shot, spec: Spec) -> list[str]:
    if shot.failure == _STATUS:
        return [f"{spec.block.route} answered {shot.details[_STATUS]}"]
    return judge.stop_findings(name, [(_BASE, shot)], spec)


def _reported_folders(config: Config, report: Report, run: TreeRun) -> list[str]:
    folder = spec_module.reported_dir(config, report.task)
    return [f"reported {name}: {(folder / name).relative_to(config.root)}" for name in run.shots]


def _frames_seen(run: TreeRun) -> dict[str, _Frame | None]:
    return {name: shot.details.get("frame") for name, shot in run.shots.items()}


def reported_section(config: Config, reproduction: Reproduction, blind_backend: str) -> str:
    report = reproduction.report
    folder = spec_module.reported_dir(config, report.task)
    views = reproduction.views
    return "\n".join(
        [
            f"Symptom: {report.symptom}",
            f"Where: {report.where} at {reproduction.sha}",
            *_pictures(config, folder, views, blind_backend),
            "## Geometry",
            *_fenced_files(folder, views, "geometry.json", "json"),
            *_markup(folder, views, report.selector),
            *_frames(reproduction.frames),
        ]
    )


def _pictures(config: Config, folder: Path, views: list[str], blind_backend: str) -> list[str]:
    if blind_backend:
        return [_BLIND.format(backend=blind_backend)]
    return ["## Pictures", *[f"- {path.relative_to(config.root)}" for path in _taken(folder, views)]]


def _taken(folder: Path, views: list[str]) -> list[Path]:
    return [folder / view / shot for view in views for shot in _SHOTS if (folder / view / shot).exists()]


def _fenced_files(folder: Path, views: list[str], name: str, language: str) -> list[str]:
    return [line for view in views for line in (f"### {view}", f"```{language}", (folder / view / name).read_text().strip(), "```")]


def _markup(folder: Path, views: list[str], selector: str) -> list[str]:
    if not selector:
        return ["## Markup", _NO_SELECTOR]
    return ["## Markup", *_fenced_files(folder, views, "markup.html", "html")]


def _frames(frames: dict[str, _Frame | None]) -> list[str]:
    shown = {view: frame for view, frame in frames.items() if frame is not None}
    if not shown:
        return []
    return ["## Frame", *_frame_blocks(shown)]


def _frame_blocks(shown: dict[str, _Frame]) -> list[str]:
    return [line for view, frame in shown.items() for line in (f"### {view}", *_frame_lines(frame))]


def _frame_lines(frame: _Frame) -> list[str]:
    if frame["url"] is None:
        return ["URL: none", _NO_SRC]
    return [f"URL: {frame['url']}", _load_problem(frame) or _measured(frame)]


def _load_problem(frame: _Frame) -> str:
    if "error" in frame:
        return f"Loaded alone: {frame['url']} could not be loaded: {frame['error']}"
    status = frame["status"]
    return f"Loaded alone: {frame['url']} answered {status}" if status is not None and status >= _ANSWERED_FROM else ""


def _measured(frame: _Frame) -> str:
    size = f"Loaded alone at {frame['width']}x{frame['height']}"
    largest = frame["largest"]
    if largest is None:
        return f"{size}, no visible element inside"
    box = largest["box"]
    return f"{size}, the largest visible element is {largest['name']} at {box['x']},{box['y']} {box['width']}x{box['height']}"
