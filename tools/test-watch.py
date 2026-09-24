#!/usr/bin/env python3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from marestail.tui.collect import discover, latest_log


def expect(name: str, got: object, wanted: object) -> None:
    if got != wanted:
        raise SystemExit(f"{name}: {got!r} != {wanted!r}")


def nested_beds() -> None:
    with tempfile.TemporaryDirectory(prefix="marestail-watch-") as temp:
        root = Path(temp)
        (root / "top" / ".marestail").mkdir(parents=True)
        nested = root / "hermes"
        (nested / ".marestail").mkdir(parents=True)
        (root / "hermes" / "node_modules" / "pkg" / ".marestail").mkdir(parents=True)
        (root / "empty").mkdir()
        found = {path.resolve() for path in discover([root])}
        expect("top", root / "top" in found, True)
        expect("child", nested in found, True)
        expect("skips-deeper-node-modules", any("node_modules" in path.parts for path in found), False)
        expect("skips-empty", root / "empty" in found, False)


def overnight_only_log() -> None:
    with tempfile.TemporaryDirectory(prefix="marestail-watch-log-") as temp:
        root = Path(temp)
        runs = root / ".marestail" / "runs"
        runs.mkdir(parents=True)
        overnight = runs / "overnight-20200101T0000.log"
        overnight.write_text("== specifier (01-specifier) attempt 1\n")
        expect("overnight-only", latest_log(root), overnight)
        pipeline = runs / "000-green" / "pipeline.log"
        pipeline.parent.mkdir()
        pipeline.write_text("== coder (07-coder) attempt 1\n")
        expect("still-overnight", latest_log(root), overnight)
        expect("ignores-pipeline-log", latest_log(root) == pipeline, False)


if __name__ == "__main__":
    nested_beds()
    overnight_only_log()
    print("watch ok")
