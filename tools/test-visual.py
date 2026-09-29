#!/usr/bin/env python3
import json
import os
import re
import signal
import socket
import struct
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
STUB = HERE / "stub-claude"
JS = ROOT / "marestail" / "js"
SERVE = "python3 -m http.server $PORT --bind 127.0.0.1"
INDENT = " " * 7
DONATE = """<html><body style="margin:0">
<header id="top" style="height:60px">Top</header>
<main><div class="col" style="width:572px;margin:0 auto">
  <h1 style="margin:0;height:40px">Donate</h1>
  <div id="widget" style="width:440px;height:200px">w</div>
  <p id="text" style="margin:0">Page text</p>
</div></main>
</body></html>
"""
BLOCK = {
    "route": "/donate.html",
    "selector": "#widget",
    "scroll": "true",
    "wait": "#widget",
    "styles": "border-radius, overflow, width",
    "inside": ".col",
    "unchanged": "x-centre",
    "must_not_change": "header",
}
VISUAL = {
    "enabled": "true",
    "start": json.dumps(SERVE),
    "ready": '"/"',
    "port": "3400",
    "viewports": '{ desktop = "1440x900" }',
    "tolerance_px": "2",
}
WIDGET = 'style="width:440px;height:200px"'
WIDER = [
    "desktop: page scrolls sideways at HEAD, scrollWidth 1842px > clientWidth 1440px (base: scrollWidth 1440px)",
    "desktop: #widget is 1408px wide, .col is 572px (base: 440px)",
    "desktop: #widget x-centre moved 484px (base: 654px, HEAD: 1138px)",
]
RANDOM_WIDTH = '<script>document.getElementById("widget").style.width=(400+Math.floor(Math.random()*1000))+"px"</script>'
DRIFT = (
    '<script>setInterval(()=>{const w=document.getElementById("widget");'
    'w.style.marginLeft=(parseInt(w.style.marginLeft||0)+10)%100+"px"},50)</script>'
)
STOP_LINE = "qa got the same problems back 3 times in a row; the worker is not making progress, stopping for a human"


def expect(name: str, got: Any, wanted: Any) -> None:
    if got != wanted:
        raise SystemExit(f"{name}: {got!r} != {wanted!r}")


def expect_true(name: str, value: Any) -> None:
    if not value:
        raise SystemExit(f"{name}: {value!r}")


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def toml(visual: dict[str, str] | None, qa: str = "") -> str:
    head = f'[git]\nbase = "main"\n\n[qa]\ncmd = "true"\n{qa}'
    if visual is None:
        return head
    return head + "\n[visual]\n" + "".join(f"{key} = {value}\n" for key, value in visual.items())


def block_text(block: dict[str, str] | None) -> str:
    if block is None:
        return "# QA\n\n1. open the page\n"
    return "# QA\n\n```visual\n" + "".join(f"{key}: {value}\n" for key, value in block.items()) + "```\n"


def changed(**overrides: str | None) -> dict[str, str]:
    merged: dict[str, str | None] = {**VISUAL, **overrides}
    return {key: value for key, value in merged.items() if value is not None}


def block_with(**overrides: str | None) -> dict[str, str]:
    merged: dict[str, str | None] = {**BLOCK, **overrides}
    return {key: value for key, value in merged.items() if value is not None}


def fixture(
    folder: Path,
    head: Callable[[str], str] | None = None,
    visual: dict[str, str] | None = VISUAL,
    block: dict[str, str] | None = BLOCK,
    base: Callable[[str], str] | None = None,
) -> Path:
    root = folder / "repo"
    root.mkdir(parents=True)
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "test@marestail")
    git(root, "config", "user.name", "test")
    write(root, ".gitignore", ".marestail/\n")
    write(root, "index.html", "<html><body>home</body></html>\n")
    write(root, "donate.html", (base or str)(DONATE))
    write(root, "marestail.toml", toml(visual))
    write(root, "qa/t.md", block_text(block))
    git(root, "add", "-A")
    git(root, "commit", "-qm", "base")
    git(root, "checkout", "-q", "-b", "work")
    edit(root, head or (lambda text: text.replace("Page text", "Other text")))
    return root


def edit(root: Path, change: Callable[[str], str]) -> None:
    path = root / "donate.html"
    path.write_text(change(path.read_text()))
    git(root, "commit", "-qam", "change")


def widget(style: str) -> Callable[[str], str]:
    return lambda text: text.replace(WIDGET, f'style="{style}"')


def after_widget(html: str) -> Callable[[str], str]:
    return lambda text: text.replace(">w</div>\n", f">w</div>\n  {html}\n")


def set_config(root: Path, visual: dict[str, str] | None, qa: str = "") -> None:
    write(root, "marestail.toml", toml(visual, qa))


def set_block(root: Path, block: dict[str, str] | None) -> None:
    write(root, "qa/t.md", block_text(block))


def cli(root: Path, *args: str, env: dict[str, str] | None = None, task: str | None = "t") -> subprocess.CompletedProcess[str]:
    merged = {**os.environ, **(env or {})}
    merged.pop("MARESTAIL_TASK", None)
    if task:
        merged["MARESTAIL_TASK"] = task
    return subprocess.run([sys.executable, str(CLI), *args], cwd=root, capture_output=True, text=True, check=False, env=merged)


def listening(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def clean_afterwards(name: str, root: Path) -> None:
    expect(f"{name}-worktrees", len(git(root, "worktree", "list").splitlines()), 1)
    expect(f"{name}-ports", [listening(3400), listening(3401)], [False, False])
    expect(f"{name}-no-checkout", [str(path) for path in (root / ".marestail").rglob(".git")], [])


def visual_result(output: str) -> tuple[bool, str, list[str]]:
    lines = output.splitlines()
    index = next(i for i, line in enumerate(lines) if re.match(r"\[(ok  |FAIL)\] visual ", line))
    matched = re.match(r"\[(ok  |FAIL)\] visual\s+(.*)  \(\d+\.\ds\)$", lines[index])
    assert matched is not None
    entries = []
    for line in lines[index + 1 :]:
        if not line.startswith(INDENT):
            break
        entries.append(line[len(INDENT) :])
    return matched.group(1) == "ok  ", matched.group(2), entries


def gate(name: str, root: Path, env: dict[str, str] | None = None, task: str | None = "t") -> tuple[bool, str, list[str]]:
    completed = cli(root, "gate", "--tier", "qa", "--only", "visual", env=env, task=task)
    result = visual_result(completed.stdout + completed.stderr)
    expect(f"{name}-exit", completed.returncode, 0 if result[0] else 1)
    clean_afterwards(name, root)
    return result


def findings_of(entries: list[str]) -> list[str]:
    return [entry for entry in entries if not entry.startswith("  ")]


def only(name: str, root: Path, wanted: list[str]) -> list[str]:
    ok, summary, entries = gate(name, root)
    expect(f"{name}-ok", ok, False)
    expect(f"{name}-findings", findings_of(entries), wanted)
    expect(f"{name}-summary", summary, f"{len(wanted)} visual findings")
    return entries


def passes(name: str, root: Path) -> str:
    ok, summary, entries = gate(name, root)
    expect(f"{name}-passes", (ok, entries), (True, []))
    return summary


def run_dir(root: Path, *parts: str) -> Path:
    return root / ".marestail" / "runs" / "t" / "visual" / Path(*parts)


def geometry(root: Path, tree: str, view: str = "desktop") -> Any:
    return json.loads((run_dir(root, tree, view) / "geometry.json").read_text())


def get_count(root: Path, tree: str, route: str = "/donate.html") -> int:
    return sum(1 for line in (run_dir(root, tree) / "app.log").read_text().splitlines() if f"GET {route}" in line)


def png_size(path: Path) -> tuple[int, int]:
    width, height = struct.unpack(">II", path.read_bytes()[16:24])
    return width, height


def case_stays_put(folder: Path) -> None:
    root = fixture(folder, visual=changed(viewports='{ desktop = "1440x900", phone = "390x844@2 touch" }'))
    expect("put-summary", passes("put", root), "2 viewports, geometry holds")
    for tree in ("base", "head"):
        for view in ("desktop", "phone"):
            expect(
                f"put-files-{tree}-{view}",
                sorted(p.name for p in run_dir(root, tree, view).iterdir()),
                ["element.png", "geometry.json", "viewport.png"],
            )
        expect(f"put-loads-{tree}", get_count(root, tree), 6)
    head = geometry(root, "head")
    expect("put-box", head["box"], {"x": 434, "y": 100, "width": 440, "height": 200})
    expect("put-styles", head["styles"], {"border-radius": "0px", "overflow": "visible", "width": "440px"})
    expect("put-widths", (head["scrollWidth"], head["clientWidth"], head["overlaps"], head["scrollY"]), (1440, 1440, [], 0))
    expect(
        "put-inside",
        (head["inside"]["selector"], {k: v for k, v in head["inside"]["box"].items() if k != "height"}),
        (".col", {"x": 434, "y": 60, "width": 572}),
    )
    expect("put-viewport", head["viewport"], {"width": 1440, "height": 900, "scale": 1, "touch": False})
    expect("put-phone", geometry(root, "head", "phone")["viewport"], {"width": 390, "height": 844, "scale": 2, "touch": True})


def case_wider(folder: Path) -> None:
    root = fixture(folder, widget("width:1408px"))
    only("wider", root, WIDER)
    expect("wider-pictures", [(run_dir(root, tree, "desktop") / "element.png").exists() for tree in ("base", "head")], [True, True])


def case_symptom(folder: Path) -> None:
    root = fixture(folder, widget("width:1408px"), block=block_with(symptom="the widget runs over the page text"))
    entries = only("symptom", root, WIDER)
    expect("symptom-last", entries[-1], "  symptom: the widget runs over the page text")


def case_sideways(folder: Path) -> None:
    root = fixture(folder, lambda text: text.replace("</div></main>", '</div><div style="width:1600px">wide</div></main>'))
    only("sideways", root, ["desktop: page scrolls sideways at HEAD, scrollWidth 1600px > clientWidth 1440px (base: scrollWidth 1440px)"])


def case_overlap(folder: Path) -> None:
    root = fixture(folder, widget("width:440px;height:200px;margin-bottom:-120px"))
    only("overlap", root, ["desktop: #widget overlaps p#text at HEAD (base: no overlap)"])


def case_hidden(folder: Path) -> None:
    banner = '<div id="banner" style="position:absolute;top:0;left:0;width:100%;height:1000px">b</div>'
    root = fixture(folder, after_widget(banner))
    _, _, entries = gate("banner", root)
    expect_true("banner-finding", "desktop: #widget overlaps div#banner at HEAD (base: no overlap)" in entries)
    set_config(root, changed(hide='["#banner"]'))
    passes("banner-hidden", root)


def case_unchanged(folder: Path) -> None:
    root = fixture(folder, widget("width:440px;height:200px;margin-left:40px"))
    only("unchanged", root, ["desktop: #widget x-centre moved 40px (base: 654px, HEAD: 694px)"])


def case_must_not_change(folder: Path) -> None:
    root = fixture(folder, lambda text: text.replace('style="height:60px"', 'style="height:62px"'))
    passes("header-62", root)
    edit(root, lambda text: text.replace('style="height:62px"', 'style="height:90px"'))
    only("header-90", root, ["desktop: must_not_change header changed (base: 0,0 1440x60; HEAD: 0,0 1440x90)"])


def case_missing(folder: Path) -> None:
    root = fixture(folder, lambda text: text.replace('id="widget"', 'id="widget2"'))
    only("missing", root, ["desktop: #widget not found at HEAD"])
    expect("missing-box", geometry(root, "head")["box"], None)
    expect("missing-element", (run_dir(root, "head", "desktop") / "element.png").exists(), False)


def case_unstable(folder: Path) -> None:
    root = fixture(folder, after_widget(RANDOM_WIDTH))
    if not unstable_once(root):
        expect_true("unstable-rerun", unstable_once(root))


def unstable_once(root: Path) -> bool:
    ok, summary, entries = gate("unstable", root)
    if entries[:1] != ["desktop: unstable at HEAD: two captures gave different geometry"]:
        return False
    expect("unstable-summary", (ok, summary, len(entries)), (False, "1 visual findings", 3))
    expect("unstable-details", [entries[1][:10], entries[2][:11]], ["  first: {", "  second: {"])
    first, second = (json.loads(entry.split(": ", 1)[1])["box"]["width"] for entry in entries[1:])
    expect_true("unstable-widths", first != second)
    return True


def case_sticks_out(folder: Path) -> None:
    root = fixture(folder, widget("width:440px;height:200px;margin-left:200px"))
    only(
        "sticks",
        root,
        [
            "desktop: #widget right edge is 1074px, .col right edge is 1006px (base: 874px)",
            "desktop: #widget x-centre moved 200px (base: 654px, HEAD: 854px)",
        ],
    )


def case_aside(folder: Path) -> None:
    aside = '<main><p id="aside" style="position:absolute;top:150px;left:500px;margin:0">aside</p>'
    root = fixture(folder, lambda text: text.replace("<main>", aside))
    only("aside", root, ["desktop: #widget overlaps p#aside at HEAD (base: no overlap)"])


def case_tall(folder: Path) -> None:
    root = fixture(
        folder, widget("width:440px;height:200px;margin-top:300px"), block=block_with(unchanged="x-centre, y-centre"), base=spaced
    )
    wanted = ["desktop: #widget y-centre moved 300px (base: 2200px, HEAD: 2500px)"]
    only("tall", root, wanted)
    tall_geometry(root, lambda value: value > 1000)
    set_block(root, block_with(unchanged="x-centre, y-centre", scroll="false"))
    only("tall-top", root, wanted)
    tall_geometry(root, lambda value: value == 0)
    expect("tall-png", png_size(run_dir(root, "head", "desktop") / "element.png"), (488, 248))


def spaced(text: str) -> str:
    return text.replace("<main>", '<main><div style="height:2000px"></div>').replace(
        "</div></main>", '</div><div style="height:100px"></div></main>'
    )


def tall_geometry(root: Path, scrolled: Callable[[int], bool]) -> None:
    base, head = geometry(root, "base"), geometry(root, "head")
    header = {"header": {"x": 0, "y": 0, "width": 1440, "height": 60}}
    expect(
        "tall-base",
        (base["box"], base["inside"]["box"]["y"], base["must_not_change"]),
        ({"x": 434, "y": 2100, "width": 440, "height": 200}, 2060, header),
    )
    expect("tall-head", (head["box"]["y"], head["must_not_change"]), (2400, header))
    expect_true("tall-scroll", scrolled(base["scrollY"]) and scrolled(head["scrollY"]))


def case_named_missing(folder: Path) -> None:
    root = fixture(folder, block=block_with(inside=".column", must_not_change="header, nav"))
    only(
        "named",
        root,
        [
            "desktop: inside .column not found at base",
            "desktop: inside .column not found at HEAD",
            "desktop: must_not_change nav not found at base",
            "desktop: must_not_change nav not found at HEAD",
        ],
    )


def case_waits(folder: Path) -> None:
    root = fixture(folder, visual=changed(capture_timeout="2"), block=block_with(wait="#never"))
    only("wait", root, ["desktop: wait #never never matched at base within 2s", "desktop: wait #never never matched at HEAD within 2s"])
    set_block(root, BLOCK)
    git(root, "commit", "-qam", "block")
    edit(root, after_widget(DRIFT))
    entries = only("settle", root, ["desktop: #widget box did not settle at HEAD within 2s"])
    expect("settle-no-detail", len(entries), 1)
    expect(
        "settle-files", sorted(p.name for p in run_dir(root, "head", "desktop").iterdir()), ["element.png", "geometry.json", "viewport.png"]
    )
    expect_true("settle-box", geometry(root, "head")["box"] is not None)


def case_malformed(folder: Path) -> None:
    cases = [
        ("selector", block_with(selector=None), VISUAL, "qa/t.md visual block: missing selector"),
        ("colour", {**BLOCK, "colour": "red"}, VISUAL, "qa/t.md visual block: unknown key colour"),
        ("measure", block_with(unchanged="middle"), VISUAL, "qa/t.md visual block: unknown measure middle in unchanged"),
        (
            "viewport",
            BLOCK,
            changed(viewports='{ phone = "390by844" }'),
            '[visual] viewports: bad viewport phone = "390by844"; expected WxH[@scale][ touch]',
        ),
    ]
    root = fixture(folder)
    for name, block, visual, finding in cases:
        malformed_once(root, name, block, visual, finding)


def malformed_once(root: Path, name: str, block: dict[str, str], visual: dict[str, str], finding: str) -> None:
    set_block(root, block)
    set_config(root, visual)
    only(f"malformed-{name}", root, [finding])
    expect(f"malformed-{name}-nothing-started", run_dir(root).exists(), False)
    completed = cli(root, "visual", "capture", "t")
    expect(f"malformed-{name}-capture", (completed.returncode, completed.stdout), (2, finding + "\n"))


def case_tools_missing(folder: Path) -> None:
    root = fixture(folder)
    nonode = folder / "nonode"
    nonode.mkdir()
    for name in ("git", "bash", "sh"):
        (nonode / name).symlink_to(subprocess.run(["which", name], capture_output=True, text=True, check=True).stdout.strip())
    only_with("node", root, {"PATH": str(nonode)}, "node not found on PATH; run marestail install")
    completed = cli(root, "visual", "capture", "t", env={"PATH": str(nonode)})
    expect("node-capture", completed.returncode, 2)
    empty = folder / "browsers"
    empty.mkdir()
    only_with("chromium", root, {"PLAYWRIGHT_BROWSERS_PATH": str(empty)}, "Playwright Chromium missing; run marestail install")


def only_with(name: str, root: Path, env: dict[str, str], finding: str) -> None:
    ok, summary, entries = gate(name, root, env=env)
    expect(f"{name}-result", (ok, summary, entries), (False, "1 visual findings", [finding]))


def case_skips(folder: Path) -> None:
    root = fixture(folder, block=None)
    completed = cli(root, "visual", "capture", "t")
    expect("noblock-capture", (completed.returncode, completed.stdout), (0, "visual: no block in qa/t.md; skipped\n"))
    expect("noblock-gate", gate("noblock", root), (True, "visual: no block in qa/t.md; skipped", []))
    (root / "qa" / "t.md").unlink()
    expect("noqa-gate", gate("noqa", root), (True, "visual: no qa/t.md; skipped", []))
    expect("notask-gate", gate("notask", root, task=None), (True, "skipped: no task; set MARESTAIL_TASK", []))
    set_block(root, BLOCK)
    set_config(root, changed(enabled="false"))
    expect("disabled-gate", gate("disabled", root), (True, "skipped: [visual] enabled = false", []))
    expect("nothing-started", run_dir(root).exists(), False)


def case_page_error(folder: Path) -> None:
    root = fixture(folder, lambda text: text.replace("</body>", '<script>throw new Error("boom")</script></body>'))
    passes("boom", root)
    errors = geometry(root, "head")["errors"]
    expect("boom-errors", (len(errors), "boom" in errors[0]), (1, True))


def case_blocked(folder: Path) -> None:
    root = fixture(folder, after_widget('<script src="/tracker.js"></script>'), visual=changed(block='["*tracker*"]'))
    passes("blocked", root)
    expect("blocked-log", "/tracker.js" in (run_dir(root, "head") / "app.log").read_text(), False)
    expect("blocked-errors", geometry(root, "head")["errors"], [])
    set_config(root, changed(block='["*nomatch*"]'))
    gate("nomatch", root)
    expect("nomatch-log", "GET /tracker.js" in (run_dir(root, "head") / "app.log").read_text(), True)


def bindable(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        if sock.connect_ex(("127.0.0.1", port)) == 0:
            return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", port))
        return True


def wait_until_free(*ports: int) -> None:
    deadline = time.time() + 120
    while not all(free(port) for port in ports) and time.time() < deadline:
        time.sleep(1)
    expect_true("ports-free", all(free(port) for port in ports))


def free(port: int) -> bool:
    try:
        return bindable(port)
    except OSError:
        return False


def case_never_answers(folder: Path) -> None:
    wait_until_free(3400, 3401)
    root = fixture(folder, visual=changed(start='"sleep 60"', ready_timeout="2"))
    only(
        "never",
        root,
        ["base: app did not answer on http://localhost:3401/ within 2s", "HEAD: app did not answer on http://localhost:3400/ within 2s"],
    )
    set_config(root, changed(start='"echo dying; exit 3"'))
    entries = only("dying", root, ["base: app exited with 3 before answering", "HEAD: app exited with 3 before answering"])
    expect(
        "dying-details",
        entries,
        ["base: app exited with 3 before answering", "  dying", "HEAD: app exited with 3 before answering", "  dying"],
    )


def case_setup(folder: Path) -> None:
    root = fixture(folder, visual=changed(setup='"echo installing; exit 1"'))
    entries = only("setup", root, ["base: setup failed (exit 1)"])
    expect("setup-detail", entries, ["base: setup failed (exit 1)", "  installing"])
    expect("setup-log", "installing" in (run_dir(root, "base") / "setup.log").read_text(), True)
    expect(
        "setup-trees",
        [(run_dir(root, "head", "desktop") / "geometry.json").exists(), run_dir(root, "base", "desktop").exists()],
        [True, False],
    )
    set_config(root, changed(setup='"sleep 60"', setup_timeout="1"))
    only("setup-timeout", root, ["base: setup did not finish within 1s"])


def case_interrupt(folder: Path) -> None:
    root = fixture(folder, visual=changed(ready_timeout="60"))
    env = {**os.environ, "MARESTAIL_TASK": "t"}
    child = subprocess.Popen(
        [sys.executable, str(CLI), "gate", "--tier", "qa", "--only", "visual"],
        cwd=root,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    log = run_dir(root, "head") / "app.log"
    deadline = time.time() + 120
    while not log.exists() and time.time() < deadline:
        time.sleep(0.1)
    expect_true("interrupt-head-started", log.exists())
    child.send_signal(signal.SIGINT)
    child.wait(timeout=60)
    time.sleep(1)
    clean_afterwards("interrupt", root)


def case_env(folder: Path) -> None:
    root = fixture(folder)
    before = git(root, "rev-parse", "HEAD")
    set_config(root, changed(env='{ CMS_URL = "https://cms.example.test/graphql" }', start=json.dumps("echo $CMS_URL; " + SERVE)))
    passes("env", root)
    expect(
        "env-logs",
        ["https://cms.example.test/graphql" in (run_dir(root, tree) / "app.log").read_text() for tree in ("base", "head")],
        [True, True],
    )
    expect(
        "env-status",
        subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True).stdout,
        " M marestail.toml\n",
    )
    expect("env-head", git(root, "rev-parse", "HEAD"), before)
    expect("env-leaks", leaking(root), [])


def leaking(root: Path) -> list[str]:
    outside = [path for path in root.rglob("*") if path.is_file() and not {".marestail", ".git"} & set(path.relative_to(root).parts)]
    texts = [
        path
        for path in outside
        if path.name != "marestail.toml" and re.search("cms.example.test|localhost:34", path.read_text(errors="ignore"))
    ]
    return [str(path) for path in texts + [path for path in outside if path.suffix == ".png"]]


def case_qa_start(folder: Path) -> None:
    root = fixture(folder)
    set_config(root, changed(start=None), qa=f"start = {json.dumps(SERVE)}\n")
    passes("qa-start", root)


def case_hand_capture(folder: Path) -> None:
    root = fixture(folder, widget("width:1408px"))
    completed = cli(root, "visual", "capture", "t")
    expect(
        "hand-output",
        (completed.returncode, completed.stdout),
        (
            0,
            "no recorded start commit for t; using git merge-base main HEAD\n"
            "base desktop: .marestail/runs/t/visual/base/desktop\n"
            "head desktop: .marestail/runs/t/visual/head/desktop\n",
        ),
    )
    expect("hand-loads", get_count(root, "head"), 2)
    for tree in ("base", "head"):
        expect(
            f"hand-files-{tree}",
            sorted(p.name for p in run_dir(root, tree, "desktop").iterdir()),
            ["element.png", "geometry.json", "viewport.png"],
        )
    clean_afterwards("hand", root)


def case_no_section(folder: Path) -> None:
    root = fixture(folder, visual=None)
    completed = cli(root, "gate", "--tier", "qa")
    names = [line.split()[2] for line in completed.stdout.splitlines() if re.match(r"\[(ok  |FAIL)\]", line)]
    expect("nosection-gates", names, ["comments", "depth", "deadcode", "qa"])
    expect("nosection-npm", install_log(folder, root, "exit 0"), [])


def fake_tools(folder: Path, npx_tail: str) -> tuple[Path, Path]:
    bin_dir = folder / "fakebin"
    bin_dir.mkdir(exist_ok=True)
    log = folder / "tools.log"
    log.write_text("")
    for name, ending in (("npm", "exit 0"), ("npx", npx_tail)):
        script = write(bin_dir, name, f'#!/bin/sh\necho "{name} $* @ $(pwd)" >> {log}\n{ending}\n')
        script.chmod(0o755)
    return bin_dir, log


def install_log(folder: Path, target: Path, npx_tail: str) -> list[str]:
    completed = install(folder, target, npx_tail)
    expect("install-exit", completed.returncode, 0)
    return (folder / "tools.log").read_text().splitlines()


def install(folder: Path, target: Path, npx_tail: str) -> subprocess.CompletedProcess[str]:
    bin_dir, _ = fake_tools(folder, npx_tail)
    env = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "GROK_HOME": str(folder / "grok")}
    return cli(target, "install", str(target), env=env, task=None)


def case_install(folder: Path) -> None:
    target = folder / "target"
    target.mkdir()
    write(target, "marestail.toml", toml(VISUAL))
    expect("install-steps", install_log(folder, target, "exit 0"), [f"npm install @ {JS}", f"npx playwright install chromium @ {JS}"])
    expect("install-package", list(json.loads((JS / "package.json").read_text())["dependencies"]), ["playwright"])
    write(target, "marestail.toml", toml(changed(enabled="false")))
    expect("install-disabled", install_log(folder, target, "exit 0"), [])
    write(target, "marestail.toml", toml(VISUAL))
    completed = install(folder, target, "exit 1")
    expect(
        "install-npx-fails",
        (completed.returncode, completed.stdout.splitlines()[-1]),
        (1, "npx playwright install chromium failed (exit 1); everything else is installed"),
    )


def case_perf_unchanged(folder: Path) -> None:
    completed = subprocess.run([sys.executable, str(HERE / "test-perf.py")], capture_output=True, text=True, check=False)
    lines = [line for line in (completed.stdout + completed.stderr).splitlines() if line.strip()]
    expect("perf-result", (completed.returncode, lines[-1]), (1, "verdict-commit-files: '' != 'perf/bench_x.py'"))
    found = subprocess.run(
        ["grep", "-rn", '"worktree", "add"', str(ROOT / "marestail")], capture_output=True, text=True, check=False
    ).stdout
    expect("perf-one-add", len([line for line in found.splitlines() if "__pycache__" not in line]), 1)


def case_qa_worker(folder: Path) -> None:
    root = fixture(folder, widget("width:1408px"))
    write(root, "tasks/t.md", "# Widget\n")
    write(root, "features/t.feature", "Feature: t\n  Scenario: widget\n    Given x\n")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "task")
    write(root, ".marestail/runs/t/start-commit", git(root, "rev-parse", "main") + "\n")
    plan = write(folder, "plan.txt", "worker qa app\n" * 4)
    env = {"MARESTAIL_CLAUDE": str(STUB), "STUB_PLAN": str(plan), "PATH": f"{ROOT / 'bin'}:{os.environ['PATH']}"}
    completed = cli(root, "run", "tasks/t.md", "--from", "qa", "--to", "qa", "--auto", env=env, task=None)
    out = completed.stdout
    attempts = re.findall(r"^== qa \(.*\) attempt (\d+)$", out, re.MULTILINE)
    expect("worker-attempts", attempts, ["1", "2", "3"])
    expect("worker-fails", out.count("[FAIL] visual"), 3)
    expect_true("worker-stop", out.index(STOP_LINE) < out.index("pipeline stopped at qa"))
    expect("worker-end", ("pipeline complete" in out, completed.returncode), (False, 1))
    clean_afterwards("worker", root)


def case_roles_and_readme(_folder: Path) -> None:
    expect_true(
        "role-specifier",
        "When the task changes what a user sees, add a fenced visual block to qa/<task>.md."
        in (ROOT / "roles" / "specifier.md").read_text(),
    )
    expect_true(
        "role-critic",
        "Bounce a spec that changes what a user sees and has no visual block in qa/<task>.md."
        in (ROOT / "roles" / "critic.md").read_text(),
    )
    readme = (ROOT / "README.md").read_text()
    for needle in (
        "`[visual]` keys",
        "`tolerance_px`",
        "`must_not_change`",
        "`symptom`",
        ".marestail/runs/<task>/visual/<tree>/<viewport>/",
    ):
        expect_true(f"readme-{needle}", needle in readme)


CASES = [
    case_roles_and_readme,
    case_skips,
    case_malformed,
    case_tools_missing,
    case_install,
    case_no_section,
    case_stays_put,
    case_wider,
    case_symptom,
    case_sideways,
    case_overlap,
    case_hidden,
    case_unchanged,
    case_must_not_change,
    case_missing,
    case_unstable,
    case_sticks_out,
    case_aside,
    case_tall,
    case_named_missing,
    case_waits,
    case_page_error,
    case_blocked,
    case_never_answers,
    case_setup,
    case_interrupt,
    case_env,
    case_qa_start,
    case_hand_capture,
    case_qa_worker,
    case_perf_unchanged,
]


def main() -> None:
    wanted = set(sys.argv[1:])
    for case in CASES:
        if wanted and case.__name__ not in wanted:
            continue
        with tempfile.TemporaryDirectory(prefix="marestail-visual-test-") as folder:
            started = time.time()
            case(Path(folder))
            print(f"{case.__name__} ok ({time.time() - started:.0f}s)", flush=True)
    print("visual ok")


if __name__ == "__main__":
    main()
