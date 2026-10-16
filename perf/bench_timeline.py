#!/usr/bin/env python3
import shutil
import tempfile
from pathlib import Path

import harness

root = harness.use_tree()

TARGETS = (
    "timeline.utc_now",
    "timeline.verdict_text",
    "timeline.record",
    "runner.attempt_commits",
    "runner.attempt_files",
)

try:
    from marestail import timeline
    from marestail.report import Result
except ImportError:
    timeline = None

if timeline is None:
    for target in TARGETS:
        harness.absent(target)
else:
    from marestail import runner

    folder = Path(tempfile.mkdtemp())
    handoff = folder / "01-coder"
    handoff.write_text("coded the adder\n\nmore\n")
    gates = [Result("sonar", True, "ok", [], 0.1)]
    agent = {
        "backend": "claude",
        "model": "m",
        "effort": None,
        "account": None,
        "minutes": 0.1,
        "summary": "ok",
    }
    commits = [{"hash": "abc", "subject": "add src"}]
    files = ["src.py"]
    started = "2020-01-01T00:00:00.000Z"

    def clear_timeline() -> None:
        for name in ("timeline.json", "timeline.md"):
            path = folder / name
            if path.exists():
                path.unlink()

    def record_step() -> None:
        clear_timeline()
        timeline.record(
            folder,
            "t",
            "01-coder",
            "coder",
            1,
            started,
            gates,
            [],
            agent,
            None,
            commits,
            files,
            handoff,
        )

    try:
        harness.emit("timeline.utc_now", harness.measure(timeline.utc_now))
        harness.emit("timeline.verdict_text", harness.measure(lambda: timeline.verdict_text("BOUNCE", "specifier")))
        harness.emit("timeline.record", harness.measure(record_step))
        config = harness.make_config(root)
        before = runner.head(config)
        harness.emit("runner.attempt_commits", harness.measure(lambda: runner.attempt_commits(config, before)))
        harness.emit("runner.attempt_files", harness.measure(lambda: runner.attempt_files(config, before)))
    finally:
        shutil.rmtree(folder, ignore_errors=True)
