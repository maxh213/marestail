import json
import subprocess
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from marestail.config import Config
from marestail.gates.visual import _capture as capture
from marestail.gates.visual._model import Block, Settings, Shot, Spec, Tree, TreeRun, Viewport
from tests.conftest import FakeRun

DESKTOP = Viewport("desktop", 1440, 900, 1, False)
PHONE = Viewport("phone", 390, 844, 2, True)


def make_spec(setup: str = "", timeout: int = 900, viewports: list[Viewport] | None = None) -> Spec:
    block = Block("/donate.html", "#widget", True, "#widget", ["width"], ".col", ["x-centre"], ["header"], "")
    settings = Settings(
        "serve", setup, "/", 3400, {"CMS_URL": "https://cms"}, viewports or [DESKTOP], ["#cookie"], ["^x$"], 2, 3, timeout, 9
    )
    return Spec("t", block, settings)


def node_reply(*failures: str | None) -> tuple[int, str]:
    captures = [{"geometry": {"box": {"x": index}}, "failure": failure} for index, failure in enumerate(failures)]
    return 0, "warning\n" + json.dumps({"captures": captures}) + "\n"


class FakeServe:
    def __init__(self, failures: dict[str, str | None] | None = None) -> None:
        self.failures = failures or {}
        self.calls: list[tuple[Any, ...]] = []

    @contextmanager
    def __call__(
        self, start: str, cwd: Path, preferred: int, ready: str, seconds: int, env: dict[str, str], log: Path
    ) -> Iterator[tuple[str | None, int]]:
        self.calls.append((start, cwd, preferred, ready, seconds, env, log))
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("one\ntwo\n")
        yield self.failures.get(log.parent.name), preferred


@pytest.fixture
def sh_for_bash(monkeypatch: pytest.MonkeyPatch) -> None:
    real = subprocess.Popen

    def popen(args: list[str], *rest: Any, **options: Any) -> Any:
        return real(["sh", *args[1:]], *rest, **options)

    monkeypatch.setattr(subprocess, "Popen", popen)


@pytest.fixture
def worktrees(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Any]]:
    seen: list[tuple[str, Any]] = []
    monkeypatch.setattr(capture.worktree, "add", lambda root, path, sha: seen.append(("add", sha)) or (0, ""))
    monkeypatch.setattr(capture.worktree, "remove", lambda root, path: seen.append(("remove", path)))
    return seen


def test_visual_dir(tmp_path: Path) -> None:
    assert capture.visual_dir(Config(tmp_path, {}), "t") == tmp_path / ".marestail" / "runs" / "t" / "visual"


def test_tool_problems(monkeypatch: pytest.MonkeyPatch, fake_run: Callable[..., FakeRun]) -> None:
    monkeypatch.setattr(capture.shutil, "which", lambda name: None)
    assert capture.tool_problems() == ["node not found on PATH; run marestail install"]
    monkeypatch.setattr(capture.shutil, "which", lambda name: "/bin/node")
    fake = fake_run(capture, [(0, ""), (1, "Executable doesn't exist")])
    assert capture.tool_problems() == []
    assert capture.tool_problems() == ["Playwright Chromium missing; run marestail install"]
    assert fake.calls[0] == ["node", str(capture._SCRIPT), "--check"]
    assert fake.options[0]["cwd"] == capture._JS_DIR
    assert (capture._JS_DIR / "visual.mjs").exists()


def test_capture_trees_runs_base_then_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_run: Callable[..., FakeRun], worktrees: list[tuple[str, Any]]
) -> None:
    config = Config(tmp_path, {})
    stale = capture.visual_dir(config, "t") / "base" / "old.png"
    stale.parent.mkdir(parents=True)
    stale.write_text("x")
    serve = FakeServe()
    monkeypatch.setattr(capture._serve, "ready_app", serve)
    fake = fake_run(capture, lambda command: node_reply(None, None))
    base, head = capture.capture_trees(config, make_spec(viewports=[DESKTOP, PHONE]), "abc", 2)
    assert not stale.exists()
    assert [call[2] for call in serve.calls] == [3401, 3400]
    assert serve.calls[1][1] == tmp_path
    assert serve.calls[0][1] == base.tree.path
    assert serve.calls[0][5] == {"CMS_URL": "https://cms"}
    assert (serve.calls[0][0], serve.calls[0][3], serve.calls[0][4]) == ("serve", "/", 9)
    assert worktrees == [("add", "abc"), ("remove", base.tree.path)]
    assert not base.tree.path.exists()
    assert (base.tree.folder, base.tree.label, head.tree.folder, head.tree.label) == ("base", "base", "head", "HEAD")
    assert list(head.shots) == ["desktop", "phone"]
    assert head.shots["desktop"] == Shot([{"box": {"x": 0}}, {"box": {"x": 1}}], None)
    payload = json.loads(fake.calls[0][3])
    assert payload["url"] == "http://localhost:3401/donate.html"
    assert payload["out"] == str(capture.visual_dir(config, "t") / "base" / "desktop")
    assert json.loads(fake.calls[3][3])["out"] == str(capture.visual_dir(config, "t") / "head" / "phone")
    assert fake.options[0]["timeout"] == 60 + 3 * 4 * 3


def test_capture_trees_removes_the_worktree_on_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, worktrees: list[tuple[str, Any]]
) -> None:
    def interrupted(*args: Any) -> Any:
        raise KeyboardInterrupt

    monkeypatch.setattr(capture._serve, "ready_app", interrupted)
    config = Config(tmp_path, {})
    spec = make_spec()
    with pytest.raises(KeyboardInterrupt):
        capture.capture_trees(config, spec, "abc", 1)
    assert [kind for kind, _ in worktrees] == ["add", "remove"]
    assert not worktrees[1][1].exists()


def test_worktree_failure_is_a_base_problem(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(capture.worktree, "add", lambda root, path, sha: (128, "fatal: bad\nrevision"))
    run = capture._base_run(tmp_path, Tree("base", "base", tmp_path / "wt"), "abc", make_spec(), tmp_path, 1)
    assert run.problems == ["base: git worktree add failed (exit 128)", "  fatal: bad", "  revision"]


@pytest.mark.usefixtures("sh_for_bash")
def test_setup_runs_in_the_worktree_and_reports_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(capture.worktree, "add", lambda root, path, sha: (0, ""))
    tree = Tree("base", "base", tmp_path)
    spec = make_spec(setup="pwd; echo $CMS_URL; echo installing; exit 1")
    run = capture._base_run(tmp_path, tree, "abc", spec, tmp_path / "visual", 1)
    assert run.problems == ["base: setup failed (exit 1)", f"  {tmp_path}", "  https://cms", "  installing"]
    assert run.shots == {}
    assert (tmp_path / "visual" / "base" / "setup.log").read_text().endswith("installing\n")


@pytest.mark.usefixtures("sh_for_bash")
def test_setup_timeout(tmp_path: Path) -> None:
    spec = make_spec(setup="echo started; sleep 30", timeout=1)
    assert capture._setup_problems(Tree("base", "base", tmp_path), spec, tmp_path / "setup.log") == [
        "base: setup did not finish within 1s",
        "  started",
    ]


@pytest.mark.usefixtures("sh_for_bash")
def test_passing_setup_serves_the_base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_run: Callable[..., FakeRun]) -> None:
    monkeypatch.setattr(capture.worktree, "add", lambda root, path, sha: (0, ""))
    monkeypatch.setattr(capture._serve, "ready_app", FakeServe())
    fake_run(capture, lambda command: node_reply(None))
    run = capture._base_run(tmp_path, Tree("base", "base", tmp_path), "abc", make_spec(setup="true"), tmp_path / "v", 1)
    assert run.problems == []
    assert list(run.shots) == ["desktop"]


def test_no_setup_has_no_problems(tmp_path: Path) -> None:
    assert capture._setup_problems(Tree("base", "base", tmp_path), make_spec(), tmp_path / "setup.log") == []
    assert not (tmp_path / "setup.log").exists()


def test_app_failure_carries_the_log_tail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(capture._serve, "ready_app", FakeServe({"head": "app exited with 3 before answering"}))
    run = capture._served(Tree("head", "HEAD", tmp_path), make_spec(), 3400, tmp_path, 1)
    assert run == TreeRun(Tree("head", "HEAD", tmp_path), ["HEAD: app exited with 3 before answering", "  one", "  two"], {})


def test_log_detail_without_a_log(tmp_path: Path) -> None:
    assert capture._log_detail(tmp_path / "none.log") == []


def test_shoot_reports_node_failures(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    trace = "node:internal/run_main:123\n    throw\npage.screenshot: Protocol error (Page.captureScreenshot)\n  name: 'Error'\n"
    fake_run(capture, [(1, trace), (1, "")])
    assert capture._shoot(make_spec(), DESKTOP, "http://x", tmp_path, 1) == Shot(
        [], "page.screenshot: Protocol error (Page.captureScreenshot)"
    )
    assert capture._shoot(make_spec(), DESKTOP, "http://x", tmp_path, 1) == Shot([], "node exited with 1")


def test_shoot_keeps_the_last_failure(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake_run(capture, [node_reply("not_found")])
    assert capture._shoot(make_spec(), DESKTOP, "http://x", tmp_path, 2) == Shot([{"box": {"x": 0}}], "not_found")


def test_script_input(tmp_path: Path) -> None:
    assert capture._script_input(make_spec(), PHONE, "http://x/donate.html", tmp_path, 2) == {
        "url": "http://x/donate.html",
        "viewport": {"width": 390, "height": 844, "scale": 2, "touch": True},
        "selector": "#widget",
        "scroll": True,
        "wait": "#widget",
        "query": {"selector": "#widget", "styles": ["width"], "inside": ".col", "mustNotChange": ["header"]},
        "hide": ["#cookie"],
        "block": ["^x$"],
        "timeout": 3000,
        "captures": 2,
        "out": str(tmp_path),
    }
    spec = make_spec()
    bare = Spec("t", Block("/", "#w", False, "", [], "", [], [], ""), spec.settings)
    assert capture._script_input(bare, DESKTOP, "http://x/", tmp_path, 1)["query"]["inside"] is None
