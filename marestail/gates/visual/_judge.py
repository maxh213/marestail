import json
from collections.abc import Callable
from typing import Any

from marestail.gates.visual._model import Block, Shot, Spec, TreeRun

_NOT_FOUND = "not_found"
_WAIT = "wait"
_SETTLE = "settle"
_KNOWN = (_NOT_FOUND, _WAIT, _SETTLE)
_BOX_KEYS = ("x", "y", "width", "height")
_Box = dict[str, int]
_Geometry = dict[str, Any]
_Trees = list[tuple[str, Shot]]
_MEASURES: dict[str, Callable[[_Box], float]] = {
    "x-centre": lambda box: box["x"] + box["width"] / 2,
    "y-centre": lambda box: box["y"] + box["height"] / 2,
    "left": lambda box: box["x"],
    "right": lambda box: box["x"] + box["width"],
    "top": lambda box: box["y"],
    "bottom": lambda box: box["y"] + box["height"],
    "width": lambda box: box["width"],
    "height": lambda box: box["height"],
}


def findings(base: TreeRun, head: TreeRun, spec: Spec) -> list[str]:
    problems = base.problems + head.problems
    return problems or _all_views(base, head, spec)


def _all_views(base: TreeRun, head: TreeRun, spec: Spec) -> list[str]:
    views = [view.name for view in spec.settings.viewports]
    return [line for name in views for line in _viewport_findings(name, [("base", base.shots[name]), ("HEAD", head.shots[name])], spec)]


def _viewport_findings(name: str, trees: _Trees, spec: Spec) -> list[str]:
    early = _stop_findings(name, trees, spec)
    if _stopped(trees):
        return early
    return early + (_unstable_findings(name, trees, spec) or _compare(name, trees[0][1].geometries[0], trees[1][1].geometries[0], spec))


def _stopped(trees: _Trees) -> bool:
    return any(shot.failure for _, shot in trees)


def _unstable_findings(name: str, trees: _Trees, spec: Spec) -> list[str]:
    return [line for label, shot in trees for line in _unstable_lines(name, label, shot.geometries, spec.settings.tolerance)]


def _stop_findings(name: str, trees: _Trees, spec: Spec) -> list[str]:
    rules = (_selector_missing, _inside_missing, _kept_missing, _wait_failed, _settle_failed, _crashed)
    return [f"{name}: {line}" for rule in rules for line in rule(trees, spec)]


def _selector_missing(trees: _Trees, spec: Spec) -> list[str]:
    return [f"{spec.block.selector} not found at {label}" for label in _failed_as(trees, _NOT_FOUND)]


def _inside_missing(trees: _Trees, spec: Spec) -> list[str]:
    if not spec.block.inside:
        return []
    return [f"inside {spec.block.inside} not found at {label}" for label in _lacking(trees, "inside", "box")]


def _kept_missing(trees: _Trees, spec: Spec) -> list[str]:
    return [
        f"must_not_change {sel} not found at {label}"
        for sel in spec.block.must_not_change
        for label in _lacking(trees, "must_not_change", sel)
    ]


def _wait_failed(trees: _Trees, spec: Spec) -> list[str]:
    return [
        f"wait {spec.block.wait} never matched at {label} within {spec.settings.capture_timeout}s" for label in _failed_as(trees, _WAIT)
    ]


def _settle_failed(trees: _Trees, spec: Spec) -> list[str]:
    return [
        f"{spec.block.selector} box did not settle at {label} within {spec.settings.capture_timeout}s"
        for label in _failed_as(trees, _SETTLE)
    ]


def _crashed(trees: _Trees, spec: Spec) -> list[str]:
    return [f"capture failed at {label}: {shot.failure}" for label, shot in trees if shot.failure not in (None, *_KNOWN)]


def _failed_as(trees: _Trees, kind: str) -> list[str]:
    return [label for label, shot in trees if shot.failure == kind]


def _lacking(trees: _Trees, key: str, inner: str) -> list[str]:
    return [label for label, shot in trees if _lacks(shot, key, inner)]


def _lacks(shot: Shot, key: str, inner: str) -> bool:
    return not shot.failure and shot.geometries[0][key].get(inner) is None


def _unstable_lines(name: str, label: str, geometries: list[_Geometry], tolerance: int) -> list[str]:
    if len(geometries) < 2 or _same(geometries[0], geometries[1], tolerance):
        return []
    first, second = geometries[:2]
    return [
        f"{name}: unstable at {label}: two captures gave different geometry",
        f"  first: {json.dumps(first)}",
        f"  second: {json.dumps(second)}",
    ]


def _same(one: _Geometry, other: _Geometry, tolerance: int) -> bool:
    boxes = zip(_compared_boxes(one), _compared_boxes(other), strict=True)
    return _same_widths(one, other, tolerance) and one["overlaps"] == other["overlaps"] and all(_near(a, b, tolerance) for a, b in boxes)


def _same_widths(one: _Geometry, other: _Geometry, tolerance: int) -> bool:
    return all(abs(one[key] - other[key]) <= tolerance for key in ("scrollWidth", "clientWidth"))


def _compared_boxes(geometry: _Geometry) -> list[_Box | None]:
    kept = geometry["must_not_change"]
    return [geometry["box"], geometry["inside"]["box"], *[kept[key] for key in sorted(kept)]]


def _near(one: _Box | None, other: _Box | None, tolerance: int) -> bool:
    if one is None or other is None:
        return one is other
    return all(abs(one[key] - other[key]) <= tolerance for key in _BOX_KEYS)


def _compare(name: str, base: _Geometry, head: _Geometry, spec: Spec) -> list[str]:
    block = spec.block
    tolerance = spec.settings.tolerance
    return [
        *_scroll_lines(name, base, head),
        *_inside_lines(name, block, base, head, tolerance),
        *_overlap_lines(name, block, base, head),
        *_unchanged_lines(name, block, base["box"], head["box"], tolerance),
        *_kept_lines(name, block, base, head, tolerance),
    ]


def _scroll_lines(name: str, base: _Geometry, head: _Geometry) -> list[str]:
    if head["scrollWidth"] <= head["clientWidth"] or base["scrollWidth"] > base["clientWidth"]:
        return []
    return [
        f"{name}: page scrolls sideways at HEAD, scrollWidth {head['scrollWidth']}px > clientWidth {head['clientWidth']}px "
        f"(base: scrollWidth {base['scrollWidth']}px)"
    ]


def _inside_lines(name: str, block: Block, base: _Geometry, head: _Geometry, tolerance: int) -> list[str]:
    outer = head["inside"]["box"]
    if not block.inside or outer is None:
        return []
    box = head["box"]
    if box["width"] > outer["width"] + tolerance:
        return [f"{name}: {block.selector} is {box['width']}px wide, {block.inside} is {outer['width']}px (base: {base['box']['width']}px)"]
    return _edge_lines(name, block, base["box"], box, outer, tolerance)


def _edge_lines(name: str, block: Block, base: _Box, box: _Box, outer: _Box, tolerance: int) -> list[str]:
    left, right = _MEASURES["left"], _MEASURES["right"]
    overshoots = [("left", left, left(outer) - left(box)), ("right", right, right(box) - right(outer))]
    return [
        f"{name}: {block.selector} {side} edge is {_px(edge(box))}px, {block.inside} {side} edge is {_px(edge(outer))}px (base: {_px(edge(base))}px)"
        for side, edge, overshoot in overshoots
        if overshoot > tolerance
    ]


def _overlap_lines(name: str, block: Block, base: _Geometry, head: _Geometry) -> list[str]:
    fresh = [other for other in head["overlaps"] if other not in base["overlaps"]]
    return [f"{name}: {block.selector} overlaps {other} at HEAD (base: no overlap)" for other in fresh]


def _unchanged_lines(name: str, block: Block, base: _Box, head: _Box, tolerance: int) -> list[str]:
    moved = [(measure, _MEASURES[measure](base), _MEASURES[measure](head)) for measure in block.unchanged]
    return [
        f"{name}: {block.selector} {measure} moved {_px(abs(now - was))}px (base: {_px(was)}px, HEAD: {_px(now)}px)"
        for measure, was, now in moved
        if abs(now - was) > tolerance
    ]


def _kept_lines(name: str, block: Block, base: _Geometry, head: _Geometry, tolerance: int) -> list[str]:
    pairs = [(sel, base["must_not_change"][sel], head["must_not_change"][sel]) for sel in block.must_not_change]
    return [
        f"{name}: must_not_change {sel} changed (base: {_described(was)}; HEAD: {_described(now)})"
        for sel, was, now in pairs
        if _moved_box(was, now, tolerance)
    ]


def _moved_box(was: _Box | None, now: _Box | None, tolerance: int) -> bool:
    return was is not None and now is not None and not _near(was, now, tolerance)


def _described(box: _Box) -> str:
    return f"{box['x']},{box['y']} {box['width']}x{box['height']}"


def _px(value: float) -> str:
    return f"{value:.10g}"
