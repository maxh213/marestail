import json
from pathlib import Path
from typing import Any

from marestail.config import Config
from marestail.gates.visual import pictures

BLOCK = "# QA\n\n```visual\nroute: /donate.html\nselector: #widget\nsymptom: the widget is 440px wide\n```\n"
VIEWPORTS = {"desktop": "1440x900", "phone": "390x844@2 touch"}
HEADER = ["## Geometry", "| viewport | measure | base | HEAD |", "|---|---|---|---|"]


def config_for(root: Path, block: str | None = BLOCK) -> Config:
    if block is not None:
        (root / "qa").mkdir(exist_ok=True)
        (root / "qa" / "t.md").write_text(block)
    return Config(root=root, raw={"visual": {"viewports": VIEWPORTS}})


def shoot(root: Path, tree: str, view: str, record: dict[str, Any] | None, shots: tuple[str, ...] = ("element", "viewport")) -> None:
    folder = root / ".marestail" / "runs" / "t" / "visual" / tree / view
    folder.mkdir(parents=True, exist_ok=True)
    for shot in shots:
        (folder / f"{shot}.png").write_bytes(b"png")
    if record is not None:
        (folder / "geometry.json").write_text(json.dumps(record))


def record(x: float = 434, overlaps: list[str] | None = None, box: bool = True) -> dict[str, Any]:
    return {
        "box": {"x": x, "y": 100, "width": 440, "height": 200} if box else None,
        "scrollWidth": 1440,
        "overlaps": overlaps or [],
    }


def test_skip_reason_names_the_missing_file_or_block(tmp_path: Path) -> None:
    config = config_for(tmp_path, None)
    assert pictures.skip_reason(config, "t") == "visual: no qa/t.md; skipping"
    config_for(tmp_path, "# QA\n\n1. open\n")
    assert pictures.skip_reason(config, "t") == "visual: no block in qa/t.md; skipping"
    config_for(tmp_path)
    assert pictures.skip_reason(config, "t") == ""


def test_section_lists_every_picture_and_the_geometry(tmp_path: Path) -> None:
    config = config_for(tmp_path)
    for tree in ("base", "head"):
        shoot(tmp_path, tree, "desktop", record(overlaps=["#a", "#b"] if tree == "head" else None))
    shoot(tmp_path, "base", "phone", record(x=0.5), ("element",))
    shoot(tmp_path, "head", "phone", record(box=False))
    lines = pictures.section(config, "t", "").splitlines()
    assert lines[:2] == ["Symptom: the widget is 440px wide", "## Pictures"]
    assert lines[2:10] == [
        "- .marestail/runs/t/visual/base/desktop/element.png",
        "- .marestail/runs/t/visual/base/desktop/viewport.png",
        "- .marestail/runs/t/visual/head/desktop/element.png",
        "- .marestail/runs/t/visual/head/desktop/viewport.png",
        "- .marestail/runs/t/visual/base/phone/element.png",
        "- .marestail/runs/t/visual/base/phone/viewport.png (missing)",
        "- .marestail/runs/t/visual/head/phone/element.png",
        "- .marestail/runs/t/visual/head/phone/viewport.png",
    ]
    assert lines[10:] == [
        *HEADER,
        "| desktop | x | 434 | 434 |",
        "| desktop | y | 100 | 100 |",
        "| desktop | width | 440 | 440 |",
        "| desktop | height | 200 | 200 |",
        "| desktop | scrollWidth | 1440 | 1440 |",
        "| desktop | overlaps | none | #a, #b |",
        "| phone | x | 0.5 | missing |",
        "| phone | y | 100 | missing |",
        "| phone | width | 440 | missing |",
        "| phone | height | 200 | missing |",
        "| phone | scrollWidth | 1440 | 1440 |",
        "| phone | overlaps | none | none |",
    ]


def test_section_without_pictures_keeps_the_geometry_alone(tmp_path: Path) -> None:
    config = config_for(tmp_path, "# QA\n\n```visual\nroute: /\nselector: #w\n```\n")
    shoot(tmp_path, "head", "desktop", record())
    section = pictures.section(config, "t", "kilo")
    lines = section.splitlines()
    assert lines[:2] == [
        "Symptom: none given in qa/t.md",
        "The pictures could not be shown: kilo cannot read images. Judge from the geometry table alone.",
    ]
    assert "| desktop | x | missing | 434 |" in lines
    assert "| phone | overlaps | missing | missing |" in lines
    assert ".png" not in section


def test_section_for_an_unreadable_block_points_at_the_gate(tmp_path: Path) -> None:
    config = config_for(tmp_path, "# QA\n\n```visual\nroute: /\n```\n")
    assert pictures.section(config, "t", "") == "The visual block in qa/t.md could not be read; the gate report says why."
