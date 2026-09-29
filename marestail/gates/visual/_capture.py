import json
import re
import shutil
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from marestail import worktree
from marestail.config import Config
from marestail.gates import _serve
from marestail.gates.visual._model import Shot, Spec, Tree, TreeRun, Viewport
from marestail.gates.visual._spec import REPORTED, visual_dir
from marestail.shell import run, tail

_JS_DIR = Path(__file__).resolve().parent.parent.parent / "js"
_SCRIPT = _JS_DIR / "visual.mjs"
_TEMP_PREFIX = "marestail-visual-"
_LOG_TAIL = 10
_NODE_MISSING = "node not found on PATH; run marestail install"
_ERROR_LINE = re.compile(r"^[A-Za-z][\w.]*: \S")
_CHROMIUM_MISSING = "Playwright Chromium missing; run marestail install"
_CHECK_TIMEOUT = 120
_NODE_STARTUP_SECONDS = 60
_WAITS_PER_LOAD = 4
_TREES = ("base", "head")
_WEB_SCHEMES = ("http", "https")
_MARKUP_LIMIT = 200
_INDENT = "  "
_FRAME_KEYS = ("url", "width", "height", "status", "largest")
_Shooter = Callable[[Spec, Viewport, str, Path, int], Shot]


def tool_problems() -> list[str]:
    if shutil.which("node") is None:
        return [_NODE_MISSING]
    code, _ = run(["node", str(_SCRIPT), "--check"], cwd=_JS_DIR, timeout=_CHECK_TIMEOUT)
    return [] if code == 0 else [_CHROMIUM_MISSING]


def capture_trees(config: Config, spec: Spec, sha: str, captures: int) -> tuple[TreeRun, TreeRun]:
    folder = visual_dir(config, spec.task)
    for name in _TREES:
        shutil.rmtree(folder / name, ignore_errors=True)
    with _scratch(config.root) as path:
        base = _base_run(config.root, Tree("base", "base", path), sha, spec, folder, captures)
        head = _served(Tree("head", "HEAD", config.root), spec, spec.settings.port, folder, captures)
    return base, head


def capture_reported(config: Config, spec: Spec, sha: str) -> TreeRun:
    folder = visual_dir(config, spec.task)
    shutil.rmtree(folder / REPORTED, ignore_errors=True)
    if urlsplit(spec.block.route).scheme in _WEB_SCHEMES:
        tree = Tree(REPORTED, "base", config.root)
        return TreeRun(tree, [], _shots(spec, "", folder / REPORTED, 1, _reported_shot))
    with _scratch(config.root) as path:
        return _base_run(config.root, Tree(REPORTED, "base", path), sha, spec, folder, 1, _reported_shot)


@contextmanager
def _scratch(root: Path) -> Iterator[Path]:
    path = Path(tempfile.mkdtemp(prefix=_TEMP_PREFIX))
    try:
        yield path
    finally:
        worktree.remove(root, path)
        shutil.rmtree(path, ignore_errors=True)


def _base_run(root: Path, tree: Tree, sha: str, spec: Spec, folder: Path, captures: int, shoot: _Shooter | None = None) -> TreeRun:
    code, output = worktree.add(root, tree.path, sha)
    if code != 0:
        return TreeRun(tree, [f"base: git worktree add failed (exit {code})", *_detail(tail(output, _LOG_TAIL))])
    problems = _setup_problems(tree, spec, folder / tree.folder / "setup.log")
    if problems:
        return TreeRun(tree, problems)
    return _served(tree, spec, spec.settings.port + 1, folder, captures, shoot)


def _setup_problems(tree: Tree, spec: Spec, log_path: Path) -> list[str]:
    if not spec.settings.setup:
        return []
    code = _serve.run_logged(spec.settings.setup, tree.path, spec.settings.env, log_path, spec.settings.setup_timeout)
    if code == 0:
        return []
    first = f"base: setup failed (exit {code})" if code is not None else f"base: setup did not finish within {spec.settings.setup_timeout}s"
    return [first, *_log_detail(log_path)]


def _served(tree: Tree, spec: Spec, preferred: int, folder: Path, captures: int, shoot: _Shooter | None = None) -> TreeRun:
    settings = spec.settings
    log_path = folder / tree.folder / "app.log"
    with _serve.ready_app(settings.start, tree.path, preferred, settings.ready, settings.ready_timeout, settings.env, log_path) as (
        failure,
        port,
    ):
        if failure:
            return TreeRun(tree, [f"{tree.label}: {failure}", *_log_detail(log_path)])
        return TreeRun(tree, [], _shots(spec, f"http://localhost:{port}", folder / tree.folder, captures, shoot or _shoot))


def _shots(spec: Spec, origin: str, out: Path, captures: int, shoot: _Shooter) -> dict[str, Shot]:
    return {view.name: shoot(spec, view, origin, out / view.name, captures) for view in spec.settings.viewports}


def _log_detail(path: Path) -> list[str]:
    return _detail(tail(path.read_text(), _LOG_TAIL)) if path.exists() else []


def _detail(lines: list[str]) -> list[str]:
    return [f"  {line}" for line in lines]


def _shoot(spec: Spec, view: Viewport, origin: str, out: Path, captures: int) -> Shot:
    payload = json.dumps(_script_input(spec, view, origin + spec.block.route, out, captures))
    code, output = run(["node", str(_SCRIPT), "capture", payload], cwd=_JS_DIR, timeout=_node_timeout(spec, captures))
    if code != 0:
        return Shot([], _node_error(output, code))
    captured = json.loads(output.strip().splitlines()[-1])["captures"]
    return Shot([item["geometry"] for item in captured], captured[-1]["failure"])


def _reported_shot(spec: Spec, view: Viewport, origin: str, out: Path, captures: int) -> Shot:
    payload = json.dumps(_script_input(spec, view, origin + spec.block.route, out, captures))
    code, output = run(["node", str(_SCRIPT), REPORTED, payload], cwd=_JS_DIR, timeout=_node_timeout(spec, captures + 1))
    if code != 0:
        return Shot([], _node_error(output, code))
    taken = json.loads(output.strip().splitlines()[-1])
    _write_markup(out, taken.get("markup"))
    _write_frame(out, taken.get("frame"))
    return Shot([taken["geometry"]] if "geometry" in taken else [], taken["failure"], taken)


def _write_markup(out: Path, markup: dict[str, Any] | None) -> None:
    if markup:
        (out / "markup.html").write_text("\n".join(_markup_lines(markup["ancestors"], markup["html"])) + "\n")


def _markup_lines(ancestors: list[str], html: str) -> list[str]:
    opening = [_INDENT * depth + tag for depth, tag in enumerate(ancestors)]
    inner = _INDENT * len(ancestors)
    return _trimmed([*opening, *(inner + line for line in html.split("\n"))])


def _trimmed(lines: list[str]) -> list[str]:
    if len(lines) <= _MARKUP_LIMIT:
        return lines
    return [*lines[:_MARKUP_LIMIT], f"… {len(lines) - _MARKUP_LIMIT} more lines trimmed"]


def _write_frame(out: Path, frame: dict[str, Any] | None) -> None:
    if frame and frame["url"]:
        (out / "frame.json").write_text(json.dumps({key: frame[key] for key in _FRAME_KEYS}, indent=2) + "\n")


def _node_error(output: str, code: int) -> str:
    return next((line for line in output.splitlines() if _ERROR_LINE.match(line)), f"node exited with {code}")


def _node_timeout(spec: Spec, captures: int) -> int:
    loads = captures + 1
    return _NODE_STARTUP_SECONDS + spec.settings.capture_timeout * _WAITS_PER_LOAD * loads


def _script_input(spec: Spec, view: Viewport, url: str, out: Path, captures: int) -> dict[str, Any]:
    block = spec.block
    return {
        "url": url,
        "viewport": {"width": view.width, "height": view.height, "scale": view.scale, "touch": view.touch},
        "selector": block.selector or None,
        "scroll": block.scroll,
        "wait": block.wait,
        "query": {
            "selector": block.selector or None,
            "styles": block.styles,
            "inside": block.inside or None,
            "mustNotChange": block.must_not_change,
        },
        "hide": spec.settings.hide,
        "block": spec.settings.block,
        "timeout": spec.settings.capture_timeout * 1000,
        "captures": captures,
        "out": str(out),
    }
