import json
from pathlib import Path
from typing import Any

from marestail.gates.qa import _visual_judge as judge
from marestail.gates.qa._visual_capture import Shot, Tree, TreeRun
from marestail.gates.qa._visual_spec import Block, Settings, Spec, Viewport

DESKTOP = Viewport("desktop", 1440, 900, 1, False)
PHONE = Viewport("phone", 390, 844, 2, True)
HEADER = {"x": 0, "y": 0, "width": 1440, "height": 60}


def make_spec(viewports: list[Viewport] | None = None, **block: Any) -> Spec:
    fields: dict[str, Any] = {
        "route": "/donate.html",
        "selector": "#widget",
        "scroll": True,
        "wait": "#widget",
        "styles": [],
        "inside": ".col",
        "unchanged": ["x-centre"],
        "must_not_change": ["header"],
        "symptom": "",
        **block,
    }
    settings = Settings("start", "", "/", 3400, {}, viewports or [DESKTOP], [], [], 2, 30, 900, 180)
    return Spec("t", Block(**fields), settings)


def box(x: int = 434, y: int = 100, width: int = 440, height: int = 200) -> dict[str, int]:
    return {"x": x, "y": y, "width": width, "height": height}


def geometry(**changes: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "box": box(),
        "scrollWidth": 1440,
        "clientWidth": 1440,
        "inside": {"selector": ".col", "box": box(434, 60, 572, 258)},
        "must_not_change": {"header": dict(HEADER)},
        "overlaps": [],
        "errors": [],
        "styles": {},
    }
    record.update(changes)
    return record


def tree_run(label: str, *shots: tuple[str, Shot], problems: list[str] | None = None) -> TreeRun:
    return TreeRun(Tree(label.lower(), label, Path(".")), problems or [], dict(shots))


def stable(record: dict[str, Any] | None = None) -> Shot:
    record = record or geometry()
    return Shot([record, json.loads(json.dumps(record))], None)


def judged(base: Shot, head: Shot, spec: Spec | None = None) -> list[str]:
    return judge.findings(tree_run("base", ("desktop", base)), tree_run("HEAD", ("desktop", head)), spec or make_spec())


def test_unchanged_layout_has_no_findings() -> None:
    assert judged(stable(), stable()) == []


def test_problems_stand_alone() -> None:
    base = tree_run("base", problems=["base: setup failed (exit 1)", "  installing"])
    head = tree_run("HEAD", ("desktop", Shot([], "not_found")), problems=["HEAD: app exited with 3 before answering"])
    assert judge.findings(base, head, make_spec()) == [
        "base: setup failed (exit 1)",
        "  installing",
        "HEAD: app exited with 3 before answering",
    ]


def test_wider_than_the_column() -> None:
    head = geometry(box=box(width=1408), scrollWidth=1842)
    assert judged(stable(), stable(head)) == [
        "desktop: page scrolls sideways at HEAD, scrollWidth 1842px > clientWidth 1440px (base: scrollWidth 1440px)",
        "desktop: #widget is 1408px wide, .col is 572px (base: 440px)",
        "desktop: #widget x-centre moved 484px (base: 654px, HEAD: 1138px)",
    ]


def test_sideways_scroll_already_at_base_is_not_new() -> None:
    wide = geometry(scrollWidth=1600)
    assert judged(stable(wide), stable(wide)) == []


def test_edges_outside_the_column() -> None:
    right = geometry(box=box(x=634))
    assert judged(stable(), stable(right)) == [
        "desktop: #widget right edge is 1074px, .col right edge is 1006px (base: 874px)",
        "desktop: #widget x-centre moved 200px (base: 654px, HEAD: 854px)",
    ]
    left = geometry(box=box(x=400))
    assert judged(stable(), stable(left), make_spec(unchanged=[])) == [
        "desktop: #widget left edge is 400px, .col left edge is 434px (base: 434px)"
    ]
    assert judged(stable(), stable(geometry(box=box(x=432))), make_spec(unchanged=[])) == []


def test_inside_rule_needs_inside() -> None:
    wide = geometry(box=box(width=1408), inside={"selector": None, "box": None})
    assert judged(stable(geometry(inside={"selector": None, "box": None})), stable(wide), make_spec(inside="", unchanged=[])) == []


def test_new_overlaps_only() -> None:
    base = geometry(overlaps=["div#old"])
    head = geometry(overlaps=["div#old", "p#text"])
    assert judged(stable(base), stable(head)) == ["desktop: #widget overlaps p#text at HEAD (base: no overlap)"]


def test_unchanged_measures_in_block_order() -> None:
    head = geometry(box=box(x=437, y=400, width=441, height=205))
    spec = make_spec(unchanged=["y-centre", "left", "right", "top", "bottom", "width", "height", "x-centre"], inside="")
    assert judged(stable(), stable(head), spec) == [
        "desktop: #widget y-centre moved 302.5px (base: 200px, HEAD: 502.5px)",
        "desktop: #widget left moved 3px (base: 434px, HEAD: 437px)",
        "desktop: #widget right moved 4px (base: 874px, HEAD: 878px)",
        "desktop: #widget top moved 300px (base: 100px, HEAD: 400px)",
        "desktop: #widget bottom moved 305px (base: 300px, HEAD: 605px)",
        "desktop: #widget height moved 5px (base: 200px, HEAD: 205px)",
        "desktop: #widget x-centre moved 3.5px (base: 654px, HEAD: 657.5px)",
    ]


def test_must_not_change_boxes() -> None:
    within = geometry(must_not_change={"header": {**HEADER, "height": 62}})
    assert judged(stable(), stable(within)) == []
    moved = geometry(must_not_change={"header": {**HEADER, "height": 90}})
    assert judged(stable(), stable(moved)) == ["desktop: must_not_change header changed (base: 0,0 1440x60; HEAD: 0,0 1440x90)"]


def test_missing_named_selectors_skip_only_their_rule() -> None:
    spec = make_spec(inside=".column", must_not_change=["header", "nav"])
    record = geometry(inside={"selector": ".column", "box": None}, must_not_change={"header": dict(HEADER), "nav": None})
    assert judged(stable(record), stable(record), spec) == [
        "desktop: inside .column not found at base",
        "desktop: inside .column not found at HEAD",
        "desktop: must_not_change nav not found at base",
        "desktop: must_not_change nav not found at HEAD",
    ]


def test_selector_missing_stands_alone() -> None:
    head = Shot([geometry(box=None, inside={"selector": ".col", "box": None})], "not_found")
    assert judged(stable(), head) == ["desktop: #widget not found at HEAD"]


def test_wait_settle_and_crash_findings() -> None:
    spec = make_spec(wait="#never")
    assert judged(Shot([geometry()], "wait"), Shot([geometry()], "wait"), spec) == [
        "desktop: wait #never never matched at base within 30s",
        "desktop: wait #never never matched at HEAD within 30s",
    ]
    assert judged(stable(), Shot([geometry(box=box(x=500))], "settle")) == ["desktop: #widget box did not settle at HEAD within 30s"]
    assert judged(Shot([], "browser gone"), stable()) == ["desktop: capture failed at base: browser gone"]


def test_unstable_stands_alone() -> None:
    first = geometry(box=box(width=500), scrollWidth=1842)
    second = geometry(box=box(width=900))
    assert judged(stable(), Shot([first, second], None)) == [
        "desktop: unstable at HEAD: two captures gave different geometry",
        f"  first: {json.dumps(first)}",
        f"  second: {json.dumps(second)}",
    ]


def test_stability_compares_within_tolerance() -> None:
    near = geometry(box=box(x=435), styles={"width": "441px"}, errors=["boom"])
    assert judged(stable(), Shot([geometry(), near], None)) == []
    for changed in (
        geometry(scrollWidth=1450),
        geometry(clientWidth=1400),
        geometry(overlaps=["p#text"]),
        geometry(inside={"selector": ".col", "box": None}),
        geometry(must_not_change={"header": {**HEADER, "y": 10}}),
    ):
        assert judged(stable(), Shot([geometry(), changed], None))[0] == "desktop: unstable at HEAD: two captures gave different geometry"


def test_one_capture_is_never_unstable() -> None:
    assert judged(Shot([geometry()], None), Shot([geometry()], None)) == []


def test_viewports_in_config_order() -> None:
    spec = make_spec([PHONE, DESKTOP])
    wide = stable(geometry(scrollWidth=1600))
    base = tree_run("base", ("desktop", stable()), ("phone", stable()))
    head = tree_run("HEAD", ("desktop", wide), ("phone", wide))
    assert [line.split(":")[0] for line in judge.findings(base, head, spec)] == ["phone", "desktop"]
