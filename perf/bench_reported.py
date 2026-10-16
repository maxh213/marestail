#!/usr/bin/env python3
import importlib
import json
import shutil
import tempfile
from pathlib import Path

import harness

root = harness.use_tree()

from marestail import pipeline, prompts, runner
from marestail.config import Config

TASK = "t"
TARGETS = (
    "visual.bug_report where",
    "visual.bug_report plain",
    "reported.reproduce_first plain",
    "reported.reproduce_first no visual",
    "reported.reproduce_first from coder",
    "visual.reported_section",
    "visual.reported_section blind",
    "reported.prompt_sections specifier",
    "reported.prompt_sections critic",
    "reported.prompt_sections coder",
    "reported.handoff_problems missing",
    "reported.handoff_problems present",
    "runner.bug_for specifier",
    "runner.bug_for critic",
    "prompts.worker_prompt reported",
    "prompts.judge_prompt reported",
    "_capture._markup_lines trimmed",
)
SECTION = {
    "enabled": True,
    "start": "python3 -m http.server $PORT",
    "port": 3400,
    "viewports": {"desktop": "1440x900", "phone": "390x844@2 touch"},
}
BUG_TASK = (
    "# t\nthe teal bar is wrong\n\n"
    "where: /donate\n"
    "symptom: the teal bar's top-left corner is round and its top-right corner is square\n"
    "selector: iframe#widget\n"
)
PLAIN_TASK = "# t\nthe top-right corner is square\n"
ANCESTORS = ['<body style="margin:0">', "<main>", '<div class="col block-full" style="width:572px;margin:0 auto">']
OBSERVED = (
    "# Specifier handoff\n## Observed\nThe frame is 440px wide and the form inside it is 425px.\n"
    "- radius on an iframe does not clip: does not hold, the left corner is round\n"
    "## For the coder\nnothing\n\nBy specifier.\n"
)
NO_OBSERVED = "# Specifier handoff\n## Assumptions\nnone\n\nBy specifier.\n"
FRAME = {
    "url": "https://donorbox.org/embed/campaign",
    "width": 440,
    "height": 200,
    "status": 200,
    "largest": {"name": "form#donation", "box": {"x": 0, "y": 0, "width": 425, "height": 180}},
}


def geometry() -> dict:
    return {
        "box": {"x": 434, "y": 100, "width": 440, "height": 200},
        "scrollWidth": 1440,
        "clientWidth": 1440,
        "scrollY": 0,
        "styles": {"border-radius": "16px", "overflow": "clip", "width": "440px", "height": "200px"},
    }


def markup() -> str:
    body = "\n".join(f"<p>{index}</p>" for index in range(300))
    return f'<iframe id="widget" src="{FRAME["url"]}" style="border-radius:16px">\n{body}\n</iframe>'


def seed_reported(folder: Path, views: list[str]) -> None:
    reported_dir = folder / ".marestail" / "runs" / TASK / "visual" / "reported"
    for view in views:
        shots = reported_dir / view
        shots.mkdir(parents=True)
        (shots / "viewport.png").write_bytes(b"\x89PNG")
        (shots / "element.png").write_bytes(b"\x89PNG")
        (shots / "geometry.json").write_text(json.dumps(geometry(), indent=2) + "\n")
        (shots / "markup.html").write_text("\n".join([*ANCESTORS, markup()])[:12000] + "\n")
        (shots / "frame.json").write_text(json.dumps(FRAME, indent=2) + "\n")


def module_or_none(name: str):
    try:
        return importlib.import_module(name)
    except ImportError:
        return None


reported = module_or_none("marestail.reported")
if reported is None or not hasattr(runner, "bug_for"):
    for target in TARGETS:
        harness.absent(target)
    raise SystemExit(0)

from marestail.gates import visual
from marestail.gates.visual import _capture

folder = Path(tempfile.mkdtemp())
try:
    (folder / "tasks").mkdir()
    bug_task = folder / "tasks" / f"{TASK}.md"
    bug_task.write_text(BUG_TASK)
    plain_task = folder / "tasks" / "plain.md"
    plain_task.write_text(PLAIN_TASK)
    config = Config(root=folder, raw={"git": {"base": "HEAD"}, "visual": SECTION})
    no_visual = Config(root=folder, raw={"git": {"base": "HEAD"}})
    views = list(SECTION["viewports"])
    seed_reported(folder, views)

    report = visual.bug_report(config, bug_task)
    reproduction = visual.Reproduction(report, "fff474e", views, {view: dict(FRAME) for view in views})

    state = runner.Run(config=config, task=bug_task, model="m", retries=1, agent="claude")
    state.handoffs.mkdir(parents=True, exist_ok=True)
    state.reproduction = reproduction
    (state.handoffs / "01-specifier.md").write_text(OBSERVED)
    with_observed = state.handoffs / "01-specifier.md"
    without_observed = state.handoffs / "03-specifier.md"
    without_observed.write_text(NO_OBSERVED)
    handoffs = state.handoffs

    names = [step.name for step in pipeline.steps(None, True)]
    from_coder = [step.name for step in pipeline.window("coder", "qa", None, True)]
    specifier = pipeline.find("specifier", None, True)
    critic = pipeline.find("critic", None, True)
    bug = reported.prompt_sections(config, reproduction, "specifier", handoffs, "")
    critic_bug = reported.prompt_sections(config, reproduction, "critic", handoffs, "")
    verdict = folder / ".marestail" / "report.md"
    verdict.write_text("VERDICT: PASS\n")
    lines = markup().split("\n")

    harness.emit("visual.bug_report where", harness.measure(lambda: visual.bug_report(config, bug_task)))
    harness.emit("visual.bug_report plain", harness.measure(lambda: visual.bug_report(config, plain_task)))
    harness.emit("reported.reproduce_first plain", harness.measure(lambda: reported.reproduce_first(config, plain_task, names)))
    harness.emit("reported.reproduce_first no visual", harness.measure(lambda: reported.reproduce_first(no_visual, bug_task, names)))
    harness.emit("reported.reproduce_first from coder", harness.measure(lambda: reported.reproduce_first(config, bug_task, from_coder)))
    harness.emit("visual.reported_section", harness.measure(lambda: visual.reported_section(config, reproduction, "")))
    harness.emit("visual.reported_section blind", harness.measure(lambda: visual.reported_section(config, reproduction, "kilo")))
    harness.emit(
        "reported.prompt_sections specifier",
        harness.measure(lambda: reported.prompt_sections(config, reproduction, "specifier", handoffs, "")),
    )
    harness.emit(
        "reported.prompt_sections critic",
        harness.measure(lambda: reported.prompt_sections(config, reproduction, "critic", handoffs, "")),
    )
    harness.emit(
        "reported.prompt_sections coder",
        harness.measure(lambda: reported.prompt_sections(config, reproduction, "coder", handoffs, "")),
    )
    harness.emit(
        "reported.handoff_problems missing",
        harness.measure(lambda: reported.handoff_problems(config, reproduction, "specifier", without_observed)),
    )
    harness.emit(
        "reported.handoff_problems present",
        harness.measure(lambda: reported.handoff_problems(config, reproduction, "specifier", with_observed)),
    )
    harness.emit("runner.bug_for specifier", harness.measure(lambda: runner.bug_for(state, "specifier")))
    harness.emit("runner.bug_for critic", harness.measure(lambda: runner.bug_for(state, "critic")))
    harness.emit(
        "prompts.worker_prompt reported",
        harness.measure(lambda: prompts.worker_prompt(config, specifier, bug_task, TASK, with_observed, "", bug=bug)),
    )
    harness.emit(
        "prompts.judge_prompt reported",
        harness.measure(lambda: prompts.judge_prompt(config, critic, bug_task, TASK, verdict, "", bug=critic_bug)),
    )
    harness.emit("_capture._markup_lines trimmed", harness.measure(lambda: _capture._markup_lines(ANCESTORS, "\n".join(lines))))
finally:
    shutil.rmtree(folder, ignore_errors=True)
