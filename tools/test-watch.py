#!/usr/bin/env python3
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from marestail.tui.collect import discover, display_name, latest_log


def expect(name, got, wanted):
    if got != wanted:
        raise SystemExit(f"{name}: {got!r} != {wanted!r}")


def nested_beds():
    with tempfile.TemporaryDirectory(prefix="marestail-watch-") as temp:
        root = Path(temp)
        (root / "top" / ".marestail").mkdir(parents=True)
        nested = root / "hermes" / "doin_it"
        (nested / ".marestail").mkdir(parents=True)
        (root / "hermes" / "node_modules" / "pkg" / ".marestail").mkdir(parents=True)
        (root / "empty").mkdir()
        found = {p.resolve() for p in discover([root])}
        expect("top", root / "top" in found, True)
        expect("nested", nested in found, True)
        expect("skips-node-modules", any("node_modules" in p.parts for p in found), False)
        expect("skips-empty", root / "empty" in found, False)
        expect("display-nested", display_name(nested, [root]), "hermes/doin_it")
        expect("display-top", display_name(root / "top", [root]), "top")


def pipeline_log_wins():
    with tempfile.TemporaryDirectory(prefix="marestail-watch-log-") as temp:
        root = Path(temp)
        runs = root / ".marestail" / "runs"
        runs.mkdir(parents=True)
        overnight = runs / "overnight-20200101T0000.log"
        overnight.write_text("== specifier (01-specifier) attempt 1\n")
        os.utime(overnight, (1, 1))
        expect("overnight-only", latest_log(root), overnight)
        pipeline = runs / "000-green" / "pipeline.log"
        pipeline.parent.mkdir()
        pipeline.write_text("== coder (07-coder) attempt 1\n")
        expect("pipeline-newer", latest_log(root), pipeline)


if __name__ == "__main__":
    nested_beds()
    pipeline_log_wins()
    print("watch ok")
