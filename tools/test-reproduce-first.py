#!/usr/bin/env python3
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CLI = ROOT / "marestail" / "cli.py"


def load(name: str, file: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VISUAL_TEST = load("visual_test", "test-visual.py")
JUDGE_TEST = load("visual_judge_test", "test-visual-judge.py")
expect, expect_true, git, write = VISUAL_TEST.expect, VISUAL_TEST.expect_true, VISUAL_TEST.git, VISUAL_TEST.write

WIDGET_DIV = '<div id="widget" style="width:440px;height:200px">w</div>'
IFRAME = '<iframe id="widget" src="/embed.html" style="width:440px;height:200px;border:0;border-radius:12px"></iframe>'
EMBED = '<html><body style="margin:0"><form id="form" style="width:425px;height:180px;background:teal">Give</form></body></html>\n'
SYMPTOM = "the teal bar's top-left corner is round and its top-right corner is square"
TASK_LINES = {
    "title": "# Round the teal bar's corners",
    "cause": "Radius on an iframe does not clip Donorbox's nested document. Make the widget fill the donate column.",
    "where": "where: /donate.html",
    "symptom": f"symptom: {SYMPTOM}",
    "selector": "selector: #widget",
}
DESKTOP = '{ desktop = "1440x900" }'
VISUAL = VISUAL_TEST.changed(viewports=DESKTOP)
OBSERVED_LINE = "- radius does not clip: does not hold, left corner round at 434,100"
STOP = "pipeline stopped before specifier: the bug could not be reproduced"
REPORTED = ".marestail/runs/t/visual/reported"
MARKUP = [
    '<body style="margin:0">',
    "  <main>",
    '    <div class="col block-full" style="width:572px;margin:0 auto">',
    f"      {IFRAME}",
]
GEOMETRY = {
    "viewport": {"width": 1440, "height": 900, "scale": 1, "touch": False},
    "selector": "#widget",
    "box": {"x": 434, "y": 100, "width": 440, "height": 200},
    "styles": {"border-radius": "12px", "overflow": "clip", "width": "440px", "height": "200px"},
    "scrollWidth": 1440,
    "clientWidth": 1440,
    "inside": {"selector": None, "box": None},
    "must_not_change": {},
    "overlaps": [],
    "errors": [],
    "scrollY": 0,
}
FRAME = {
    "url": "http://localhost:3401/embed.html",
    "width": 440,
    "height": 200,
    "status": 200,
    "largest": {"name": "form#form", "box": {"x": 0, "y": 0, "width": 425, "height": 180}},
}
FRAME_LINE = "Loaded alone at 440x200, the largest visible element is form#form at 0,0 425x180"
TO_CRITIC = ("--to", "critic", "--auto")
OBSERVED_PLAN = ["specify observed", "judge PASS"]


def framed(text: str) -> str:
    return text.replace(WIDGET_DIV, IFRAME).replace('class="col"', 'class="col block-full"')


def task_text(**overrides: str | None) -> str:
    merged: dict[str, str | None] = {**TASK_LINES, **overrides}
    return "".join(line + "\n" for line in merged.values() if line is not None)


BedBase: Any = JUDGE_TEST.Bed


class Bed(BedBase):  # type: ignore[misc]
    def __init__(
        self,
        folder: Path,
        visual: dict[str, str] | None = VISUAL,
        page: Callable[[str], str] = framed,
        task: str | None = None,
        embed: str = EMBED,
    ) -> None:
        self.folder = folder
        self.root = VISUAL_TEST.fixture(folder, visual=visual, block=None, base=page)
        write(self.root, "marestail.toml", JUDGE_TEST.toml(visual))
        write(self.root, "embed.html", embed)
        write(self.root, "tasks/t.md", task or task_text())
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "task")
        self.args = folder / "args"
        self.prompts = folder / "prompts"
        self.args.mkdir()
        self.prompts.mkdir()
        self.bins = self.wrappers()
        self.out = ""
        self.code = -1

    def reported(self, *parts: str) -> Path:
        return Path(self.root) / REPORTED / Path(*parts)

    def geometry(self, view: str = "desktop") -> Any:
        return json.loads(self.reported(view, "geometry.json").read_text())

    def markup(self, view: str = "desktop") -> list[str]:
        return self.reported(view, "markup.html").read_text().splitlines()

    def frame(self, view: str = "desktop") -> Any:
        return json.loads(self.reported(view, "frame.json").read_text())

    def prompt_count(self) -> int:
        return len(list(self.prompts.glob("*.txt")))


def section_of(prompt: str, title: str) -> list[str]:
    lines = prompt.splitlines()
    start = lines.index(f"# {title}")
    rest = lines[start + 1 :]
    end = next((index for index, line in enumerate(rest) if line.startswith("# ")), len(rest))
    return [line for line in rest[:end] if line.strip()]


def ordered(name: str, text: str, needles: list[str]) -> None:
    positions = [text.find(needle) for needle in needles]
    expect_true(f"{name}-present", all(position >= 0 for position in positions))
    expect(f"{name}-order", positions, sorted(positions))


def stopped(name: str, bed: Bed, reason: str) -> None:
    ordered(name, bed.out, [reason, STOP])
    expect(f"{name}-exit", bed.code, 1)
    expect(f"{name}-no-agent", bed.prompt_count(), 0)


def case_captured_before_specifier(folder: Path) -> None:
    bed = Bed(folder)
    start = git(bed.root, "rev-parse", "HEAD")
    VISUAL_TEST.wait_until_free(3401)
    bed.run(OBSERVED_PLAN, *TO_CRITIC)
    ordered(
        "stdout", bed.out, ["== reported (/donate.html at the start commit)", f"reported desktop: {REPORTED}/desktop", "== specifier ("]
    )
    started = (bed.prompts / "01.txt").stat().st_mtime
    for name in ("viewport.png", "element.png", "geometry.json", "markup.html", "frame.json"):
        expect_true(f"before-{name}", bed.reported("desktop", name).stat().st_mtime < started)
    expect("geometry", bed.geometry(), GEOMETRY)
    expect("markup", bed.markup(), MARKUP)
    expect("frame", bed.frame(), FRAME)
    prompt = bed.prompt(1)
    lines = prompt.splitlines()
    expect_true("reported-after-task", lines.index("# Task") < lines.index("# Reported") < lines.index("# Specification files"))
    reported = section_of(prompt, "Reported")
    for needle in (
        f"Symptom: {SYMPTOM}",
        f"- {REPORTED}/desktop/viewport.png",
        f"- {REPORTED}/desktop/element.png",
        MARKUP[2],
        "URL: http://localhost:3401/embed.html",
        FRAME_LINE,
    ):
        expect_true(f"prompt-{needle}", needle in reported)
    expect_true("prompt-where", f"Where: /donate.html at {start}" in reported)
    expect_true("prompt-ask", reported[-1].startswith("Write ## Observed in your handoff"))
    expect("exit", bed.code, 0)
    expect("clean", git(bed.root, "status", "--porcelain"), "")
    touched = git(bed.root, "log", "--name-only", "--format=", f"{start}..HEAD")
    expect("no-marestail-commit", [line for line in touched.splitlines() if line.startswith(".marestail/")], [])


def case_critic_sees_observed(folder: Path) -> None:
    bed = Bed(folder)
    bed.run(OBSERVED_PLAN, *TO_CRITIC)
    prompt = bed.prompt(2)
    expect_true("critic-first", prompt.startswith("You are the critic."))
    lines = prompt.splitlines()
    expect_true("critic-order", lines.index("# Task") < lines.index("# Reported") < lines.index("# Observed"))
    expect_true("critic-observed", OBSERVED_LINE in section_of(prompt, "Observed"))
    expect("critic-no-ask", [line for line in section_of(prompt, "Reported") if line.startswith("Write ## Observed")], [])
    expect_true("critic-frame", FRAME_LINE in section_of(prompt, "Reported"))


def case_observed_missing(folder: Path) -> None:
    bed = Bed(folder)
    bed.run(["specify", "specify observed", "judge PASS"], *TO_CRITIC, "--retries", "2")
    missing = "missing ## Observed section in .marestail/handoffs/t/01-specifier.md"
    expect_true("missing-line", missing in bed.out)
    retry = bed.prompt(2)
    expect_true("retry-specifier", retry.startswith("You are the specifier."))
    expect_true("retry-why", missing in section_of(retry, "Why the work came back to you"))
    expect("captured-once", bed.out.count("== reported ("), 1)
    expect("retry-exit", bed.code, 0)


def case_route_404(folder: Path) -> None:
    bed = Bed(folder, task=task_text(where="where: /nope.html"))
    before = git(bed.root, "rev-parse", "HEAD")
    bed.run(OBSERVED_PLAN, *TO_CRITIC)
    stopped("nope", bed, "reported: /nope.html answered 404")
    expect("nope-commits", git(bed.root, "rev-parse", "HEAD"), before)


def no_node_path(folder: Path) -> str:
    nonode = folder / "nonode-bin"
    nonode.mkdir()
    for name in ("git", "bash", "sh"):
        (nonode / name).symlink_to(subprocess.run(["which", name], capture_output=True, text=True, check=True).stdout.strip())
    return str(nonode)


def case_other_failures(folder: Path) -> None:
    gone = Bed(
        folder / "gone", visual=VISUAL_TEST.changed(viewports=DESKTOP, capture_timeout="3"), task=task_text(selector="selector: #gone")
    )
    gone.run(OBSERVED_PLAN, *TO_CRITIC)
    stopped("gone", gone, "reported: desktop: #gone not found at base")
    exits = Bed(folder / "exit", visual=VISUAL_TEST.changed(viewports=DESKTOP, start='"exit 7"'))
    exits.run(OBSERVED_PLAN, *TO_CRITIC)
    stopped("exit", exits, "reported: base: app exited with 7 before answering")
    nonode = Bed(folder / "nonode")
    nonode.run(OBSERVED_PLAN, *TO_CRITIC, env={"PATH": no_node_path(folder)})
    stopped("nonode", nonode, "reported: node not found on PATH; run marestail install")


def case_no_selector(folder: Path) -> None:
    bed = Bed(folder, task=task_text(selector=None))
    bed.run(OBSERVED_PLAN, *TO_CRITIC)
    present = {
        name: bed.reported("desktop", name).exists()
        for name in ("viewport.png", "geometry.json", "element.png", "markup.html", "frame.json")
    }
    expect("files", present, {"viewport.png": True, "geometry.json": True, "element.png": False, "markup.html": False, "frame.json": False})
    wanted = {**GEOMETRY, "selector": None, "box": None, "styles": {}}
    expect("geometry", bed.geometry(), wanted)
    prompt = bed.prompt(1)
    expect_true("no-selector-line", "none: the task has no selector: line" in prompt and "## Frame" not in prompt)
    expect("exit", bed.code, 0)


def case_largest(folder: Path) -> None:
    decoys = (
        '<iframe id="pixel" src="/embed.html" style="position:absolute;top:0;left:0;width:1px;height:1px;border:0"></iframe>'
        '<iframe id="ghost" src="/embed.html" style="display:none"></iframe>'
    )
    bed = Bed(folder, page=lambda text: framed(text).replace("<main>", "<main>" + decoys), task=task_text(selector="selector: iframe"))
    bed.run(OBSERVED_PLAN, *TO_CRITIC)
    expect("largest-box", bed.geometry()["box"], GEOMETRY["box"])
    markup = bed.markup()
    expect_true("largest-markup", markup[-1].startswith('      <iframe id="widget"'))
    expect_true("largest-no-decoy", not any("pixel" in line or "ghost" in line for line in markup))
    expect("largest-exit", bed.code, 0)


def case_all_hidden(folder: Path) -> None:
    bed = Bed(folder, page=lambda text: framed(text).replace("border-radius:12px", "border-radius:12px;display:none"))
    bed.run(OBSERVED_PLAN, *TO_CRITIC)
    stopped("hidden", bed, "reported: desktop: #widget not visible at base")


def case_trimmed(folder: Path) -> None:
    paragraphs = "".join(f"<p>{number}</p>\n" for number in range(1, 301))
    bed = Bed(folder, page=lambda text: framed(text).replace(IFRAME, f'<div id="widget">\n{paragraphs}</div>'))
    bed.run(OBSERVED_PLAN, *TO_CRITIC)
    markup = bed.markup()
    wanted = [*MARKUP[:3], '      <div id="widget">', *[f"      <p>{number}</p>" for number in range(1, 197)], "… 105 more lines trimmed"]
    expect("trimmed", markup, wanted)
    prompt = bed.prompt(1)
    expect_true("trimmed-prompt", "… 105 more lines trimmed" in prompt and "<p>197</p>" not in prompt)


def frame_section(bed: Bed) -> list[str]:
    reported = section_of(bed.prompt(1), "Reported")
    start = reported.index("## Frame")
    end = next((index for index in range(start + 1, len(reported)) if reported[index].startswith("## ")), len(reported))
    return [line for line in reported[start + 1 : end] if not line.startswith("Write ## Observed")]


def case_frame_problems(folder: Path) -> None:
    cases: list[tuple[str, Callable[[str], str], str, str]] = [
        (
            "missing",
            lambda text: framed(text).replace('src="/embed.html"', 'src="/missing.html"'),
            EMBED,
            "Loaded alone: http://localhost:3401/missing.html answered 404",
        ),
        (
            "invisible",
            framed,
            EMBED.replace("background:teal", "background:teal;display:none"),
            "Loaded alone at 440x200, no visible element inside",
        ),
        ("nosrc", lambda text: framed(text).replace(' src="/embed.html"', ""), EMBED, "The frame has no src; it was not loaded alone."),
    ]
    for name, page, embed, line in cases:
        bed = Bed(folder / name, page=page, embed=embed)
        VISUAL_TEST.wait_until_free(3401)
        bed.run(OBSERVED_PLAN, *TO_CRITIC)
        expect(f"{name}-exit", bed.code, 0)
        section = frame_section(bed)
        expect_true(f"{name}-line", section[0] == "### desktop" and line in section)


def case_per_viewport(folder: Path) -> None:
    visual = VISUAL_TEST.changed(viewports='{ desktop = "1440x900", mobile = "390x844" }')
    bed = Bed(
        folder,
        visual=visual,
        page=lambda text: framed(text).replace("width:440px;height:200px;border:0", "width:min(440px, 100vw - 40px);height:200px;border:0"),
    )
    VISUAL_TEST.wait_until_free(3401)
    bed.run(OBSERVED_PLAN, *TO_CRITIC)
    expect("widths", (bed.frame()["width"], bed.frame("mobile")["width"]), (440, 350))
    expect(
        "frames",
        frame_section(bed),
        [
            "### desktop",
            "URL: http://localhost:3401/embed.html",
            FRAME_LINE,
            "### mobile",
            "URL: http://localhost:3401/embed.html",
            "Loaded alone at 350x200, the largest visible element is form#form at 0,0 425x180",
        ],
    )
    expect("mobile-box", bed.geometry("mobile")["box"], {"x": 0, "y": 100, "width": 350, "height": 200})


def case_full_url(folder: Path) -> None:
    bed = Bed(
        folder,
        visual=VISUAL_TEST.changed(viewports=DESKTOP, start='"exit 7"'),
        task=task_text(where="where: http://127.0.0.1:3500/donate.html"),
    )
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", "3500", "--bind", "127.0.0.1"],
        cwd=bed.root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        while not VISUAL_TEST.listening(3500):
            time.sleep(0.1)
        bed.run(OBSERVED_PLAN, *TO_CRITIC)
    finally:
        server.terminate()
        server.wait()
    expect_true("url-header", "== reported (http://127.0.0.1:3500/donate.html at the start commit)" in bed.out)
    expect("url-exit", bed.code, 0)
    expect("url-no-app", bed.reported("app.log").exists(), False)


def case_blind(folder: Path) -> None:
    bed = Bed(folder)
    bed.run(OBSERVED_PLAN, *TO_CRITIC, "--agent", "kilo", "--model", "x")
    prompt = bed.prompt(1)
    blind = "The pictures could not be shown: kilo cannot read images. Judge from the geometry and markup alone."
    expect_true("blind-line", blind in prompt and '"width": 440' in prompt and "form#form at 0,0 425x180" in prompt)
    expect_true("blind-no-png", ".png" not in prompt)


def case_gate_keeps(folder: Path) -> None:
    bed = Bed(folder)
    bed.run(OBSERVED_PLAN, *TO_CRITIC)
    VISUAL_TEST.set_block(bed.root, {**VISUAL_TEST.BLOCK, "inside": "", "must_not_change": "", "unchanged": ""})
    git(bed.root, "commit", "-qam", "block")
    completed = VISUAL_TEST.cli(bed.root, "gate", "--tier", "qa", "--only", "visual")
    expect_true("gate-ran", "[ok  ] visual" in completed.stdout or "[FAIL] visual" in completed.stdout)
    expect_true("gate-captured", (bed.root / ".marestail/runs/t/visual/base/desktop/viewport.png").exists())
    expect_true("gate-keeps", bed.reported("desktop", "viewport.png").exists())


def case_outside_rule(folder: Path) -> None:
    setups: list[tuple[str, dict[str, str] | None, str]] = [
        ("no-where", VISUAL, task_text(where=None)),
        ("no-symptom", VISUAL, task_text(symptom=None)),
        ("no-section", None, task_text()),
        ("disabled", VISUAL_TEST.changed(viewports=DESKTOP, enabled="false"), task_text()),
    ]
    for name, visual, task in setups:
        bed = Bed(folder / name, visual=visual, task=task)
        bed.run(["specify", "judge PASS"], *TO_CRITIC)
        expect(f"{name}-quiet", [line for line in bed.out.splitlines() if line.startswith(("== reported", "reported"))], [])
        prompts = [bed.prompt(1), bed.prompt(2)]
        expect(
            f"{name}-sections",
            [("# Reported" in text.splitlines(), "# Observed" in text.splitlines()) for text in prompts],
            [(False, False), (False, False)],
        )
        expect(f"{name}-accepted", (bed.invocations(), bed.code), (["specifier", "critic"], 0))
        expect(f"{name}-nothing", (bed.root / REPORTED).exists(), False)


def case_from_coder(folder: Path) -> None:
    bed = Bed(folder)
    bed.run(["code"], "--from", "coder", "--to", "coder", "--auto", "--retries", "1")
    expect("coder-quiet", [line for line in bed.out.splitlines() if line.startswith("== reported")], [])
    expect("coder-nothing", (bed.root / REPORTED).exists(), False)


def case_roles_and_docs(_folder: Path) -> None:
    critic = (
        "Bounce a spec whose cause is not supported by the capture, or whose scenarios model the page differently from the captured markup."
    )
    expect_true("critic", critic in (ROOT / "roles" / "critic.md").read_text())
    specifier = "A spec that models a layout takes it from the captured markup in # Reported."
    expect_true("specifier", specifier in (ROOT / "roles" / "specifier.md").read_text())
    for readme in ("tasks/README.md", "templates/tasks-README.md"):
        text = (ROOT / readme).read_text()
        expect_true(readme, TASK_LINES["where"] in text and TASK_LINES["symptom"] in text and "A bug task should carry" in text)
    pipeline = (ROOT / "README.md").read_text().split("## Pipeline", 1)[1].split("\n## ", 1)[0]
    expect_true("readme", "reproduced first" in pipeline)


CASES = [
    case_roles_and_docs,
    case_captured_before_specifier,
    case_critic_sees_observed,
    case_observed_missing,
    case_route_404,
    case_other_failures,
    case_no_selector,
    case_largest,
    case_all_hidden,
    case_trimmed,
    case_frame_problems,
    case_per_viewport,
    case_full_url,
    case_blind,
    case_gate_keeps,
    case_outside_rule,
    case_from_coder,
]


def main() -> None:
    wanted = set(sys.argv[1:])
    for case in CASES:
        if wanted and case.__name__ not in wanted:
            continue
        with tempfile.TemporaryDirectory(prefix="marestail-reproduce-test-") as folder:
            started = time.time()
            case(Path(folder))
            print(f"{case.__name__} ok ({time.time() - started:.0f}s)", flush=True)
    print("reproduce first ok")


if __name__ == "__main__":
    os.environ.pop("MARESTAIL_TASK", None)
    main()
