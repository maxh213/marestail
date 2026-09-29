import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from marestail import worktree
from marestail.config import Config
from marestail.gates import _serve
from marestail.gates.qa._visual_spec import Spec, Viewport
from marestail.shell import ensure_dir, run, tail

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


@dataclass(frozen=True)
class Tree:
    folder: str
    label: str
    path: Path


@dataclass
class Shot:
    geometries: list[dict[str, Any]]
    failure: str | None


@dataclass
class TreeRun:
    tree: Tree
    problems: list[str] = field(default_factory=list)
    shots: dict[str, Shot] = field(default_factory=dict)


def visual_dir(config: Config, task: str) -> Path:
    return config.work / "runs" / task / "visual"


def tool_problems() -> list[str]:
    if shutil.which("node") is None:
        return [_NODE_MISSING]
    code, _ = run(["node", str(_SCRIPT), "--check"], cwd=_JS_DIR, timeout=_CHECK_TIMEOUT)
    return [] if code == 0 else [_CHROMIUM_MISSING]


def capture_trees(config: Config, spec: Spec, sha: str, captures: int) -> tuple[TreeRun, TreeRun]:
    folder = visual_dir(config, spec.task)
    shutil.rmtree(folder, ignore_errors=True)
    path = Path(tempfile.mkdtemp(prefix=_TEMP_PREFIX))
    try:
        base = _base_run(config.root, Tree("base", "base", path), sha, spec, folder, captures)
        head = _served(Tree("head", "HEAD", config.root), spec, spec.settings.port, folder, captures)
    finally:
        worktree.remove(config.root, path)
        shutil.rmtree(path, ignore_errors=True)
    return base, head


def _base_run(root: Path, tree: Tree, sha: str, spec: Spec, folder: Path, captures: int) -> TreeRun:
    code, output = worktree.add(root, tree.path, sha)
    if code != 0:
        return TreeRun(tree, [f"base: git worktree add failed (exit {code})", *_detail(tail(output, _LOG_TAIL))])
    problems = _setup_problems(tree, spec, folder / tree.folder / "setup.log")
    if problems:
        return TreeRun(tree, problems)
    return _served(tree, spec, spec.settings.port + 1, folder, captures)


def _setup_problems(tree: Tree, spec: Spec, log_path: Path) -> list[str]:
    if not spec.settings.setup:
        return []
    code = _run_setup(tree.path, spec, log_path)
    if code == 0:
        return []
    first = f"base: setup failed (exit {code})" if code is not None else f"base: setup did not finish within {spec.settings.setup_timeout}s"
    return [first, *_log_detail(log_path)]


def _run_setup(cwd: Path, spec: Spec, log_path: Path) -> int | None:
    ensure_dir(log_path.parent)
    with log_path.open("w") as handle:
        process = subprocess.Popen(
            ["bash", "-lc", spec.settings.setup],
            cwd=cwd,
            env={**os.environ, **spec.settings.env},
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            return process.wait(timeout=spec.settings.setup_timeout)
        except subprocess.TimeoutExpired:
            return None
        finally:
            _serve.stop(process)


def _served(tree: Tree, spec: Spec, preferred: int, folder: Path, captures: int) -> TreeRun:
    settings = spec.settings
    log_path = folder / tree.folder / "app.log"
    with _serve.ready_app(settings.start, tree.path, preferred, settings.ready, settings.ready_timeout, settings.env, log_path) as (
        failure,
        port,
    ):
        if failure:
            return TreeRun(tree, [f"{tree.label}: {failure}", *_log_detail(log_path)])
        shots = {
            view.name: _shoot(spec, view, f"http://localhost:{port}", folder / tree.folder / view.name, captures)
            for view in settings.viewports
        }
        return TreeRun(tree, [], shots)


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
        "selector": block.selector,
        "scroll": block.scroll,
        "wait": block.wait,
        "query": {
            "selector": block.selector,
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
