#!/usr/bin/env python3
import json
import os
import shutil
import tempfile
from pathlib import Path

import harness

root = harness.use_tree()

from marestail import pipeline, ran_against, runner
from marestail.config import Config

TASK = "t"
VISUAL_TARGETS = (
    "pipeline.steps visual",
    "pipeline.names visual",
    "pipeline.find visual",
    "pipeline.window visual",
    "pipeline.window hyper visual",
    "visual.judge_skip block",
    "visual.judge_skip no qa",
    "visual.judge_section",
    "visual.judge_section blind",
    "runner.skip_reason visual",
    "runner.gate_tier visual",
    "runner.review_for visual",
    "runner.built_judge_prompt visual",
    "runner.gated_verdict visual",
    "runner.commit label blind",
    "runner.judging_with",
    "runner.ending_for unseen",
    "ran_against.finish unseen",
    "runner.ask_visual stub claude",
    "runner.ask_visual fallback",
)
BLOCK = (
    "# QA\n\n```visual\n"
    "route: /donate\n"
    "selector: iframe[name=donorbox]\n"
    "unchanged: x-centre, y-centre\n"
    "symptom: the top-right corner is square\n"
    "```\n"
)
SECTION = {
    "enabled": True,
    "start": "python3 -m http.server $PORT",
    "port": 3400,
    "viewports": {"desktop": "1440x900", "phone": "390x844@2 touch"},
}
VERDICT = "VERDICT: BOUNCE specifier\n1. the corner is still square\n"
STUB = "#!/bin/sh\ncat >/dev/null\nprintf '%s\\n' '{\"type\":\"result\",\"result\":\"ok\"}'\n"
LIMITED_STUB = (
    "#!/bin/sh\ncat >/dev/null\n"
    "case \"$*\" in *claude-fable-5-1*) printf '%s\\n' '{\"is_error\":true,\"result\":\"rate limit exceeded\"}';; "
    "*) printf '%s\\n' '{\"type\":\"result\",\"result\":\"ok\"}';; esac\n"
)


def geometry(width: int, scroll_width: int) -> dict:
    return {
        "box": {"x": 434, "y": 120, "width": width, "height": 300},
        "scrollWidth": scroll_width,
        "overlaps": ["p#text"] if width > 1000 else [],
    }


def seed_pictures(folder: Path) -> None:
    visual = folder / ".marestail" / "runs" / TASK / "visual"
    for tree, width in (("base", 440), ("head", 1408)):
        for view in ("desktop", "phone"):
            shots = visual / tree / view
            shots.mkdir(parents=True)
            (shots / "element.png").write_bytes(b"\x89PNG")
            (shots / "viewport.png").write_bytes(b"\x89PNG")
            (shots / "geometry.json").write_text(json.dumps(geometry(width, 1440 + width)))


def run_state(config: Config, task: Path, agent: str) -> runner.Run:
    state = runner.Run(config=config, task=task, model="m", retries=1, agent=agent)
    state.handoffs.mkdir(parents=True, exist_ok=True)
    return state


folder = Path(tempfile.mkdtemp())
stub_dir = Path(tempfile.mkdtemp())
try:
    (folder / "tasks").mkdir()
    (folder / "qa").mkdir()
    task = folder / "tasks" / f"{TASK}.md"
    task.write_text("# t\nthe top-right corner is square\n")
    (folder / "qa" / f"{TASK}.md").write_text(BLOCK)
    config = Config(root=folder, raw={"git": {"base": "HEAD"}, "visual": SECTION})
    report = folder / ".marestail" / "report.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(VERDICT)

    stub = stub_dir / "claude"
    stub.write_text(STUB)
    stub.chmod(0o755)
    os.environ["MARESTAIL_CLAUDE"] = str(stub)
    os.environ.pop("MARESTAIL_AGENT", None)
    plain = run_state(config, task, "claude")
    harness.emit("runner.invoke stub claude", harness.measure(lambda: runner.invoke(plain, "01-hardener", "prompt\n")))

    visual_judge = getattr(runner, "ask_visual", None)
    if visual_judge is None:
        for target in VISUAL_TARGETS:
            harness.absent(target)
    else:
        from marestail.gates import visual

        seed_pictures(folder)
        judge = pipeline.find("visual", None, True)
        state = run_state(config, task, "claude")
        blind = run_state(config, task, "kilo")
        blind.blind = True
        blind.unseen = True
        gate = ("", True, [])
        texts = ("", "", "")
        steps = pipeline.steps(None, True)

        harness.emit("pipeline.steps visual", harness.measure(lambda: pipeline.steps(None, True)))
        harness.emit("pipeline.names visual", harness.measure(lambda: pipeline.names(None, True)))
        harness.emit("pipeline.find visual", harness.measure(lambda: pipeline.find("visual", None, True)))
        harness.emit("pipeline.window visual", harness.measure(lambda: pipeline.window("coder", "qa", None, True)))
        harness.emit("pipeline.window hyper visual", harness.measure(lambda: pipeline.window("coder", "qa", "hyper", True)))
        harness.emit("visual.judge_skip block", harness.measure(lambda: visual.judge_skip(config, TASK)))
        harness.emit("visual.judge_skip no qa", harness.measure(lambda: visual.judge_skip(config, "absent")))
        harness.emit("visual.judge_section", harness.measure(lambda: visual.judge_section(config, TASK, "")))
        harness.emit("visual.judge_section blind", harness.measure(lambda: visual.judge_section(config, TASK, "kilo")))
        harness.emit("runner.skip_reason visual", harness.measure(lambda: runner.skip_reason(state, judge)))
        harness.emit("runner.gate_tier visual", harness.measure(lambda: runner.gate_tier("visual")))
        harness.emit("runner.review_for visual", harness.measure(lambda: runner.review_for(state, judge)))
        harness.emit(
            "runner.built_judge_prompt visual",
            harness.measure(lambda: runner.built_judge_prompt(state, judge, report, texts)),
        )
        harness.emit(
            "runner.gated_verdict visual",
            harness.measure(lambda: runner.gated_verdict(judge, report, gate, ("BOUNCE", "hardener"))),
        )
        harness.emit(
            "runner.commit label blind",
            harness.measure(lambda: (runner.shown_verdict(blind, "PASS"), runner.judge_label(blind))),
        )

        def swap() -> None:
            with runner.judging_with(state, "claude-fable-5-1"):
                runner.agent_label(state)

        harness.emit("runner.judging_with", harness.measure(swap))
        harness.emit("runner.ending_for unseen", harness.measure(lambda: runner.ending_for(blind, [pipeline.find("hardener")])))
        harness.emit("ran_against.finish unseen", harness.measure(lambda: ran_against.finish("app", False)))

        def build() -> str:
            return runner.built_judge_prompt(state, judge, report, texts)

        harness.emit("runner.ask_visual stub claude", harness.measure(lambda: runner.ask_visual(state, "02-visual", build)))
        stub.write_text(LIMITED_STUB)
        harness.emit("runner.ask_visual fallback", harness.measure(lambda: runner.ask_visual(state, "02-visual", build)))
finally:
    os.environ.pop("MARESTAIL_CLAUDE", None)
    shutil.rmtree(folder, ignore_errors=True)
    shutil.rmtree(stub_dir, ignore_errors=True)
