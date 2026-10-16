#!/usr/bin/env python3
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import harness

root = harness.use_tree()

from marestail import _install
from marestail.config import Config
from marestail.context import Context
from marestail.gates import _serve
from marestail.gates import configured_gates

VISUAL_TARGETS = (
    "visual.run_gate no block",
    "visual.run_gate malformed",
    "visual._spec.load",
    "visual._judge.findings holds",
    "visual._judge.findings wider",
    "marestail visual capture no section",
)

BLOCK = (
    "# QA\n\n```visual\n"
    "route: /donate\n"
    "selector: iframe[name=donorbox]\n"
    "scroll: true\n"
    "wait: iframe[src*=donorbox]\n"
    "styles: border-radius, overflow, width\n"
    "inside: .block-column\n"
    "unchanged: x-centre, y-centre\n"
    "must_not_change: header, main h1\n"
    "symptom: the teal bar is round on one side\n"
    "```\n"
)
BAD_BLOCK = "```visual\nroute: /donate\ncolour: teal\nunchanged: middle\n```\n"
SECTION = {
    "enabled": True,
    "start": "python3 -m http.server $PORT",
    "port": 3400,
    "env": {"CMS_URL": "https://cms.example.test/graphql"},
    "viewports": {"desktop": "1440x900", "phone": "390x844@2 touch", "tablet": "768x1024@1.5"},
    "hide": ["#CybotCookiebotDialog", "nextjs-portal"],
    "block": ["*googletagmanager*", "*tracker?.js", "*[!a]nalytics*"],
    "tolerance_px": 2,
}


def box(x: int, y: int, width: int, height: int) -> dict[str, int]:
    return {"x": x, "y": y, "width": width, "height": height}


def geometry(widget: dict[str, int], scroll_width: int, overlaps: list[str]) -> dict:
    return {
        "box": widget,
        "styles": {"width": f"{widget['width']}px"},
        "scrollWidth": scroll_width,
        "clientWidth": 1440,
        "inside": {"box": box(434, 100, 572, 400)},
        "must_not_change": {"header": box(0, 0, 1440, 60), "main h1": box(434, 60, 572, 40)},
        "overlaps": overlaps,
        "errors": [],
        "scrollY": 0,
    }


def with_section(folder: Path, section: dict | None) -> Config:
    raw = {"qa": {"start": "true"}}
    if section is not None:
        raw["visual"] = section
    return Config(root=folder, raw=raw)


folder = Path(tempfile.mkdtemp())
(folder / "qa").mkdir()
(folder / "qa" / "plain.md").write_text("# QA\n\nno block here\n" * 40)
(folder / "qa" / "block.md").write_text(BLOCK)
(folder / "qa" / "bad.md").write_text(BAD_BLOCK)
enabled_target = folder / "enabled"
enabled_target.mkdir()
(enabled_target / "marestail.toml").write_text('[visual]\nenabled = true\nport = 3400\n\n[qa]\nstart = "true"\n')
plain_target = folder / "plain"
plain_target.mkdir()
(plain_target / "marestail.toml").write_text('[qa]\nstart = "true"\n')
os.environ.pop("MARESTAIL_QA_CMD_TIMEOUT", None)

try:
    try:
        from marestail.gates import visual
        from marestail.gates.visual import _judge, _spec
        from marestail.gates.visual._model import Shot, Tree, TreeRun
    except ImportError:
        visual = None

    if visual is None:
        for target in VISUAL_TARGETS:
            harness.absent(target)
    else:
        config = with_section(folder, SECTION)
        os.environ["MARESTAIL_TASK"] = "plain"
        harness.emit("visual.run_gate no block", harness.measure(lambda: visual.run_gate(Context(config=config))))
        os.environ["MARESTAIL_TASK"] = "bad"
        harness.emit("visual.run_gate malformed", harness.measure(lambda: visual.run_gate(Context(config=config))))
        os.environ.pop("MARESTAIL_TASK", None)
        harness.emit("visual._spec.load", harness.measure(lambda: _spec.load(config, "block")))

        spec, _ = _spec.load(config, "block")
        views = [view.name for view in spec.settings.viewports]
        narrow = geometry(box(500, 120, 440, 300), 1440, [])
        wide = geometry(box(434, 120, 1408, 300), 1842, ["p#text"])

        def runs(head_geometry: dict) -> tuple:
            base = TreeRun(Tree("base", "base", folder), [], {name: Shot([narrow, narrow], None) for name in views})
            head = TreeRun(Tree("head", "HEAD", folder), [], {name: Shot([head_geometry, head_geometry], None) for name in views})
            return base, head

        holds = runs(narrow)
        wider = runs(wide)
        harness.emit("visual._judge.findings holds", harness.measure(lambda: _judge.findings(*holds, spec)))
        harness.emit("visual._judge.findings wider", harness.measure(lambda: _judge.findings(*wider, spec)))

        from marestail.cli import main

        def capture_no_section() -> None:
            previous = Path.cwd()
            os.chdir(plain_target)
            try:
                main(["visual", "capture", "t"])
            finally:
                os.chdir(previous)

        harness.emit("marestail visual capture no section", harness.measure(capture_no_section))

    try:
        from marestail import worktree

        start_commit = worktree.start_commit
    except ImportError:
        from marestail.perf import trees

        start_commit = trees.start_commit

    repo_config = harness.make_config(root)
    harness.emit("start_commit merge-base", harness.measure(lambda: start_commit(repo_config, "no-such-task-perf")))

    run_logged = getattr(_serve, "run_logged", None)
    if run_logged is None:
        harness.absent("_serve.run_logged")
    else:
        log_path = folder / "setup.log"
        harness.emit("_serve.run_logged", harness.measure(lambda: run_logged("true", folder, {"A": "1"}, log_path, 30)))

    enabled = getattr(_install, "visual_enabled", None)
    if enabled is None:
        harness.absent("_install.visual_enabled on")
        harness.absent("_install.visual_enabled off")
    else:
        harness.emit("_install.visual_enabled on", harness.measure(lambda: enabled(enabled_target)))
        harness.emit("_install.visual_enabled off", harness.measure(lambda: enabled(plain_target)))

    qa_config = with_section(folder, None)
    harness.emit("gates.configured_gates qa", harness.measure(lambda: configured_gates(qa_config, "qa", None)))
    visual_config = with_section(folder, SECTION)
    harness.emit("gates.configured_gates qa visual", harness.measure(lambda: configured_gates(visual_config, "qa", None)))

    cold = [sys.executable, "-c", "import sys; sys.path.insert(0, sys.argv[1]); from marestail.gates import registry; registry()", str(root)]

    def registry_cold() -> None:
        subprocess.run(cold, cwd=root, check=True, capture_output=True)

    harness.emit("gates.registry cold", harness.measure(registry_cold))
finally:
    os.environ.pop("MARESTAIL_TASK", None)
    shutil.rmtree(folder, ignore_errors=True)
