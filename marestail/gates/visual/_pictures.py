import json
from pathlib import Path
from typing import Any

from marestail.config import Config
from marestail.gates.visual import _spec as spec_module
from marestail.gates.visual._spec import visual_dir

_TREES = ("base", "head")
_SHOTS = ("element.png", "viewport.png")
_BOX_MEASURES = ("x", "y", "width", "height")
_MEASURES = (*_BOX_MEASURES, "scrollWidth", "overlaps")
_MISSING = "missing"
_TABLE_HEAD = ["## Geometry", "| viewport | measure | base | HEAD |", "|---|---|---|---|"]
_UNREADABLE = "The visual block in qa/{task}.md could not be read; the gate report says why."
_BLIND = "The pictures could not be shown: {backend} cannot read images. Judge from the geometry table alone."
_Record = dict[str, Any] | None


def judge_skip(config: Config, task: str) -> str:
    problem = spec_module.block_problem(config, task)
    return f"visual: {problem}; skipping" if problem else ""


def judge_section(config: Config, task: str, blind_backend: str) -> str:
    spec, _ = spec_module.load(config, task)
    if spec is None:
        return _UNREADABLE.format(task=task)
    folder = visual_dir(config, task)
    views = [view.name for view in spec.settings.viewports]
    symptom = spec.block.symptom or f"none given in qa/{task}.md"
    return "\n".join([f"Symptom: {symptom}", *_pictures(config, folder, views, blind_backend), *_geometry(folder, views)])


def _pictures(config: Config, folder: Path, views: list[str], blind_backend: str) -> list[str]:
    if blind_backend:
        return [_BLIND.format(backend=blind_backend)]
    return ["## Pictures", *[_picture_line(config, path) for view in views for path in _shots(folder, view)]]


def _shots(folder: Path, view: str) -> list[Path]:
    return [folder / tree / view / shot for tree in _TREES for shot in _SHOTS]


def _picture_line(config: Config, path: Path) -> str:
    line = f"- {path.relative_to(config.root)}"
    return line if path.exists() else f"{line} (missing)"


def _geometry(folder: Path, views: list[str]) -> list[str]:
    return [*_TABLE_HEAD, *[row for view in views for row in _rows(view, _record(folder / "base" / view), _record(folder / "head" / view))]]


def _record(folder: Path) -> _Record:
    path = folder / "geometry.json"
    return json.loads(path.read_text()) if path.exists() else None


def _rows(view: str, base: _Record, head: _Record) -> list[str]:
    return [f"| {view} | {measure} | {_cell(base, measure)} | {_cell(head, measure)} |" for measure in _MEASURES]


def _cell(record: _Record, measure: str) -> str:
    if record is None:
        return _MISSING
    if measure in _BOX_MEASURES:
        return _box_cell(record.get("box"), measure)
    return _page_cell(record, measure)


def _box_cell(box: dict[str, float] | None, measure: str) -> str:
    return _MISSING if box is None else _number(box[measure])


def _page_cell(record: dict[str, Any], measure: str) -> str:
    if measure == "scrollWidth":
        return _number(record["scrollWidth"])
    return ", ".join(record["overlaps"]) or "none"


def _number(value: float) -> str:
    return f"{value:.10g}"
