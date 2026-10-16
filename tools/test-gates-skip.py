#!/usr/bin/env python3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from marestail import gates
from marestail.cli import SKIP_REASON, skip_list
from marestail.config import Config
from marestail.report import Result, render


def expect(name, got, wanted):
    if got != wanted:
        print(f"FAIL {name}: got {got!r}, wanted {wanted!r}")
        sys.exit(1)


def config(raw):
    return Config(root=Path("/tmp/marestail-gates-skip-test"), raw=raw)


def refuses(name, raw):
    try:
        skip_list(config(raw))
    except SystemExit as error:
        expect(name, "[gates] skip" in str(error), True)
        return
    print(f"FAIL {name}: no error")
    sys.exit(1)


if __name__ == "__main__":
    expect("no-section", skip_list(config({})), set())
    expect("empty", skip_list(config({"gates": {"skip": []}})), set())
    expect("one", skip_list(config({"gates": {"skip": ["ts.mutation"]}})), {"ts.mutation"})
    expect("many", skip_list(config({"gates": {"skip": ["ts.mutation", "sonar"]}})), {"ts.mutation", "sonar"})
    refuses("unknown-name", {"gates": {"skip": ["ts.mutations"]}})
    refuses("not-a-list", {"gates": {"skip": "ts.mutation"}})
    refuses("not-strings", {"gates": {"skip": [1]}})
    full = {gate.name for gate in gates.select("full", None)}
    expect("full-has-mutation", "ts.mutation" in full, True)
    kept = full - skip_list(config({"gates": {"skip": ["ts.mutation"]}}))
    expect("skip-removes-only-that-gate", (("ts.mutation" in kept), ("ts.tests" in kept), ("sonar" in kept)), (False, True, True))
    report = render([Result.skipped("ts.mutation", SKIP_REASON)])
    expect("skipped-is-visible", "[ok  ] ts.mutation    skipped: listed in [gates] skip in marestail.toml" in report, True)
    expect("skipped-passes", report.endswith("GATE PASSED"), True)
    print("gates skip ok")
