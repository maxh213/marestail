import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from marestail import worktree
from marestail.config import Config
from marestail.gates import _serve, visual
from marestail.gates.visual import _capture as capture
from marestail.gates.visual import _reported as reported
from marestail.gates.visual import _spec as spec_module
from marestail.gates.visual._model import Block, Report, Reproduction, Settings, Shot, Spec, Tree, TreeRun, Viewport
from tests.conftest import FakeRun
from tests.test_gates_visual_capture import FakeServe

DESKTOP = Viewport("desktop", 1440, 900, 1, False)
MOBILE = Viewport("mobile", 390, 844, 1, False)
TASK = "# Round the corners\nwhere: /donate.html\nsymptom: the corner is square\nselector: #widget\nwhere: /ignored\n"
REPORT = Report("t", "/donate.html", "the corner is square", "#widget")
FRAME = {
    "url": "http://localhost:3401/embed.html",
    "width": 440,
    "height": 200,
    "status": 200,
    "largest": {"name": "form#form", "box": {"x": 0, "y": 0, "width": 425, "height": 180}},
}
FRAME_LINE = "Loaded alone at 440x200, the largest visible element is form#form at 0,0 425x180"


@pytest.fixture
def worktrees(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Path]]:
    seen: list[tuple[str, Path]] = []

    def add(root: Path, path: Path, sha: str) -> tuple[int, str]:
        seen.append(("add", path))
        return 0, ""

    def remove(root: Path, path: Path) -> None:
        seen.append(("remove", path))

    monkeypatch.setattr(worktree, "add", add)
    monkeypatch.setattr(worktree, "remove", remove)
    return seen


def task_file(root: Path, text: str = TASK) -> Path:
    path = root / "tasks" / "t.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def config_with(root: Path, visual_section: dict[str, Any] | None = None) -> Config:
    raw: dict[str, Any] = {} if visual_section is None else {"visual": visual_section}
    return Config(root, raw)


def make_spec(route: str = "/donate.html", selector: str = "#widget", viewports: list[Viewport] | None = None) -> Spec:
    block = Block(route, selector, True, "", ["border-radius", "overflow", "width", "height"], "", [], [], "the corner is square")
    settings = Settings("serve", "", "/", 3400, {}, viewports or [DESKTOP], [], [], 2, 3, 9, 9)
    return Spec("t", block, settings)


def test_bug_report_reads_the_first_task_lines(tmp_path: Path) -> None:
    assert spec_module.bug_report(config_with(tmp_path, {}), task_file(tmp_path)) == REPORT
    without_selector = task_file(tmp_path, "where: /donate.html\nsymptom:  the corner is square \n")
    assert spec_module.bug_report(config_with(tmp_path, {}), without_selector) == Report("t", "/donate.html", "the corner is square", "")


def test_bug_report_needs_where_symptom_and_an_enabled_section(tmp_path: Path) -> None:
    path = task_file(tmp_path)
    assert spec_module.bug_report(config_with(tmp_path), path) is None
    assert spec_module.bug_report(config_with(tmp_path, {"enabled": False}), path) is None
    assert spec_module.bug_report(config_with(tmp_path, {}), task_file(tmp_path, "where: /donate.html\n")) is None
    assert spec_module.bug_report(config_with(tmp_path, {}), task_file(tmp_path, "symptom: square\nwhere: \n")) is None


def test_bug_report_reads_no_task_without_a_section(tmp_path: Path) -> None:
    assert spec_module.bug_report(config_with(tmp_path), tmp_path / "missing.md") is None


def test_reported_spec_pins_the_block(tmp_path: Path) -> None:
    config = config_with(tmp_path, {"start": "serve", "viewports": {"desktop": "1440x900"}})
    spec, problems = spec_module.reported_spec(config, REPORT)
    assert problems == []
    assert spec is not None
    assert spec.task == "t"
    assert spec.block == Block(
        "/donate.html", "#widget", True, "", ["border-radius", "overflow", "width", "height"], "", [], [], "the corner is square"
    )
    assert spec.settings.viewports == [DESKTOP]


def test_reported_spec_reports_bad_viewports(tmp_path: Path) -> None:
    spec, problems = spec_module.reported_spec(config_with(tmp_path, {"viewports": {"desktop": "wide"}}), REPORT)
    assert spec is None
    assert problems == ['[visual] viewports: bad viewport desktop = "wide"; expected WxH[@scale][ touch]']


def test_reported_dir(tmp_path: Path) -> None:
    assert spec_module.reported_dir(Config(tmp_path, {}), "t") == tmp_path / ".marestail" / "runs" / "t" / "visual" / "reported"


def reply(**taken: Any) -> tuple[int, str]:
    return 0, "noise\n" + json.dumps(taken) + "\n"


def test_reported_shot_writes_markup_and_frame(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    markup = {"ancestors": ['<body style="margin:0">', "<main>"], "html": '<iframe id="widget">\n</iframe>'}
    fake = fake_run(capture, [reply(status=200, failure=None, geometry={"box": 1}, markup=markup, frame={**FRAME, "error": "x"})])
    shot = capture._reported_shot(make_spec(), DESKTOP, "http://localhost:3401", tmp_path, 1)
    assert shot.geometries == [{"box": 1}]
    assert shot.failure is None
    assert shot.details["frame"]["error"] == "x"
    assert (tmp_path / "markup.html").read_text() == '<body style="margin:0">\n  <main>\n    <iframe id="widget">\n    </iframe>\n'
    assert json.loads((tmp_path / "frame.json").read_text()) == FRAME
    assert fake.calls[0][:3] == ["node", str(capture._SCRIPT), "reported"]
    assert json.loads(fake.calls[0][3])["url"] == "http://localhost:3401/donate.html"
    assert fake.options[0]["timeout"] == capture._node_timeout(make_spec(), 2)


def test_reported_shot_without_geometry_or_records(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake_run(capture, [reply(status=404, failure="status"), reply(status=200, failure=None, geometry={}, markup=None, frame={"url": None})])
    assert capture._reported_shot(make_spec(), DESKTOP, "", tmp_path, 1) == Shot([], "status", {"status": 404, "failure": "status"})
    assert capture._reported_shot(make_spec(), DESKTOP, "", tmp_path, 1).geometries == [{}]
    assert list(tmp_path.iterdir()) == []


def test_reported_shot_reports_node_failures(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake_run(capture, [(1, "")])
    assert capture._reported_shot(make_spec(), DESKTOP, "", tmp_path, 1) == Shot([], "node exited with 1")


def test_markup_lines_indent_and_trim() -> None:
    assert capture._markup_lines(["<body>"], "<p>\nx\n</p>") == ["<body>", "  <p>", "  x", "  </p>"]
    long = capture._markup_lines([], "\n".join(str(number) for number in range(205)))
    assert len(long) == 201
    assert long[-2:] == ["199", "… 5 more lines trimmed"]
    assert len(capture._markup_lines([], "\n".join("x" * 200))) == 200


def test_script_input_sends_no_selector_as_null(tmp_path: Path) -> None:
    payload = capture._script_input(make_spec(selector=""), DESKTOP, "http://x", tmp_path, 1)
    assert (payload["selector"], payload["query"]["selector"]) == (None, None)


def test_capture_trees_keeps_the_reported_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, worktrees: list[Any]) -> None:
    monkeypatch.setattr(capture, "_base_run", lambda *args: TreeRun(Tree("base", "base", tmp_path)))
    monkeypatch.setattr(capture, "_served", lambda *args: TreeRun(Tree("head", "HEAD", tmp_path)))
    folder = spec_module.visual_dir(Config(tmp_path, {}), "t")
    for name in ("base", "head", "reported"):
        (folder / name).mkdir(parents=True)
    capture.capture_trees(Config(tmp_path, {}), make_spec(), "abc", 1)
    assert sorted(path.name for path in folder.iterdir()) == ["reported"]


def test_capture_reported_starts_the_base_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, worktrees: list[Any], fake_run: Callable[..., FakeRun]
) -> None:
    serve = FakeServe()
    monkeypatch.setattr(_serve, "ready_app", serve)
    fake = fake_run(capture, [reply(status=200, failure=None, geometry={}, markup=None, frame=None)])
    stale = spec_module.reported_dir(Config(tmp_path, {}), "t") / "old"
    stale.mkdir(parents=True)
    run = capture.capture_reported(Config(tmp_path, {}), make_spec(), "abc")
    assert run.problems == []
    assert list(run.shots) == ["desktop"]
    assert not stale.exists()
    assert serve.calls[0][2] == 3401
    assert serve.calls[0][6] == spec_module.reported_dir(Config(tmp_path, {}), "t") / "app.log"
    assert json.loads(fake.calls[0][3])["url"] == "http://localhost:3401/donate.html"
    assert [kind for kind, _ in worktrees] == ["add", "remove"]


def test_capture_reported_loads_a_full_url_as_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, worktrees: list[Any], fake_run: Callable[..., FakeRun]
) -> None:
    serve = FakeServe()
    monkeypatch.setattr(_serve, "ready_app", serve)
    fake = fake_run(capture, [reply(status=200, failure=None, geometry={}, markup=None, frame=None)])
    run = capture.capture_reported(Config(tmp_path, {}), make_spec(route="https://example.org/donate"), "abc")
    assert (run.tree.folder, run.tree.label, run.problems) == ("reported", "base", [])
    assert json.loads(fake.calls[0][3])["url"] == "https://example.org/donate"
    assert (serve.calls, worktrees) == ([], [])


def reproduction(frames: dict[str, dict[str, Any] | None], selector: str = "#widget") -> Reproduction:
    views = list(frames)
    return Reproduction(Report("t", "/donate.html", "square", selector), "abc123", views, frames)


def captured(root: Path, views: list[str], element: bool = True) -> None:
    folder = spec_module.reported_dir(Config(root, {}), "t")
    for view in views:
        (folder / view).mkdir(parents=True)
        (folder / view / "viewport.png").write_bytes(b"")
        (folder / view / "geometry.json").write_text('{"box": null}\n')
        if element:
            (folder / view / "element.png").write_bytes(b"")
            (folder / view / "markup.html").write_text("<body>\n  <p>x</p>\n")


def test_reported_section_lists_everything(tmp_path: Path) -> None:
    captured(tmp_path, ["desktop"])
    text = reported.reported_section(Config(tmp_path, {}), reproduction({"desktop": FRAME}), "")
    assert text.splitlines() == [
        "Symptom: square",
        "Where: /donate.html at abc123",
        "## Pictures",
        "- .marestail/runs/t/visual/reported/desktop/viewport.png",
        "- .marestail/runs/t/visual/reported/desktop/element.png",
        "## Geometry",
        "### desktop",
        "```json",
        '{"box": null}',
        "```",
        "## Markup",
        "### desktop",
        "```html",
        "<body>",
        "  <p>x</p>",
        "```",
        "## Frame",
        "### desktop",
        "URL: http://localhost:3401/embed.html",
        FRAME_LINE,
    ]


def test_reported_section_without_selector_or_eyes(tmp_path: Path) -> None:
    captured(tmp_path, ["desktop"], element=False)
    text = reported.reported_section(Config(tmp_path, {}), reproduction({"desktop": None}, selector=""), "kilo")
    lines = text.splitlines()
    assert lines[2] == "The pictures could not be shown: kilo cannot read images. Judge from the geometry and markup alone."
    assert lines[-2:] == ["## Markup", "none: the task has no selector: line"]
    assert ".png" not in text


def test_frame_lines_for_every_outcome() -> None:
    url = FRAME["url"]
    assert reported._frame_lines({"url": None}) == ["URL: none", "The frame has no src; it was not loaded alone."]
    assert reported._frame_lines({**FRAME, "status": 404, "largest": None})[1] == f"Loaded alone: {url} answered 404"
    failed = {**FRAME, "status": None, "largest": None, "error": "page.goto: net::ERR_CONNECTION_REFUSED"}
    assert reported._frame_lines(failed)[1] == f"Loaded alone: {url} could not be loaded: page.goto: net::ERR_CONNECTION_REFUSED"
    assert reported._frame_lines({**FRAME, "largest": None})[1] == "Loaded alone at 440x200, no visible element inside"
    assert reported._frame_lines({**FRAME, "status": None})[1] == FRAME_LINE


def test_frames_per_viewport_skip_non_frames() -> None:
    assert reported._frames({"desktop": None}) == []
    assert reported._frames({"desktop": FRAME, "phone": None, "mobile": {**FRAME, "width": 350}}) == [
        "## Frame",
        "### desktop",
        "URL: http://localhost:3401/embed.html",
        FRAME_LINE,
        "### mobile",
        "URL: http://localhost:3401/embed.html",
        FRAME_LINE.replace("440x200", "350x200"),
    ]


@pytest.fixture
def reproducing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    seen: dict[str, Any] = {
        "tools": [],
        "run": TreeRun(Tree("reported", "base", tmp_path), [], {"desktop": Shot([{}], None, {"frame": FRAME})}),
    }
    monkeypatch.setattr(worktree, "start_commit", lambda config, task: ("abc", "note"))
    monkeypatch.setattr(visual.capture, "tool_problems", lambda: seen["tools"])
    monkeypatch.setattr(visual.capture, "capture_reported", lambda config, spec, sha: seen["run"])
    return seen


def test_reproduce_prints_where_the_capture_went(tmp_path: Path, reproducing: dict[str, Any], capsys: Any) -> None:
    config = config_with(tmp_path, {"viewports": {"desktop": "1440x900"}})
    assert visual.reproduce(config, REPORT) == Reproduction(REPORT, "abc", ["desktop"], {"desktop": FRAME})
    assert capsys.readouterr().out.splitlines() == [
        "== reported (/donate.html at the start commit)",
        "reported desktop: .marestail/runs/t/visual/reported/desktop",
    ]


def test_reproduce_stops_on_tools_and_bad_config(tmp_path: Path, reproducing: dict[str, Any], capsys: Any) -> None:
    reproducing["tools"] = ["node not found on PATH; run marestail install"]
    assert visual.reproduce(config_with(tmp_path, {"viewports": {"desktop": "1440x900"}}), REPORT) is None
    assert visual.reproduce(config_with(tmp_path, {"viewports": {"desktop": "big"}}), REPORT) is None
    assert capsys.readouterr().out.splitlines()[1::2] == [
        "reported: node not found on PATH; run marestail install",
        'reported: [visual] viewports: bad viewport desktop = "big"; expected WxH[@scale][ touch]',
    ]


def test_reproduce_stops_on_app_and_capture_failures(tmp_path: Path, reproducing: dict[str, Any], capsys: Any) -> None:
    config = config_with(tmp_path, {"viewports": {"desktop": "1440x900", "mobile": "390x844"}})
    reproducing["run"] = TreeRun(Tree("reported", "base", tmp_path), ["base: app exited with 7 before answering"])
    assert visual.reproduce(config, REPORT) is None
    shots = {"desktop": Shot([], "status", {"status": 404}), "mobile": Shot([], "status", {"status": 404})}
    reproducing["run"] = TreeRun(Tree("reported", "base", tmp_path), [], shots)
    assert visual.reproduce(config, REPORT) is None
    shots = {"desktop": Shot([], "not_visible"), "mobile": Shot([], "not_found")}
    reproducing["run"] = TreeRun(Tree("reported", "base", tmp_path), [], shots)
    assert visual.reproduce(config, REPORT) is None
    assert [line for line in capsys.readouterr().out.splitlines() if not line.startswith("==")] == [
        "reported: base: app exited with 7 before answering",
        "reported: /donate.html answered 404",
        "reported: desktop: #widget not visible at base",
        "reported: mobile: #widget not found at base",
    ]
