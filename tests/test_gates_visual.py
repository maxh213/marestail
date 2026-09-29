from pathlib import Path
from typing import Any

import pytest

from marestail.config import Config
from marestail.gates.qa import visual
from marestail.gates.qa._visual_capture import Shot, Tree, TreeRun
from tests.conftest import gate_shape, make_context

BLOCK = "```visual\nroute: /donate.html\nselector: #widget\nsymptom: runs over the text\n```\n"
VISUAL = {"start": "serve", "viewports": {"desktop": "1440x900"}}
GEOMETRY: dict[str, Any] = {
    "box": {"x": 434, "y": 100, "width": 440, "height": 200},
    "scrollWidth": 1440,
    "clientWidth": 1440,
    "inside": {"selector": None, "box": None},
    "must_not_change": {},
    "overlaps": [],
}


def repo(tmp_path: Path, block: str | None = BLOCK) -> Path:
    if block is not None:
        (tmp_path / "qa").mkdir()
        (tmp_path / "qa" / "t.md").write_text(block)
    return tmp_path


def fake_capture(monkeypatch: pytest.MonkeyPatch, head: dict[str, Any], problems: list[str] | None = None) -> list[tuple[str, int]]:
    seen: list[tuple[str, int]] = []

    def trees(config: Config, spec: Any, sha: str, captures: int) -> tuple[TreeRun, TreeRun]:
        seen.append((sha, captures))
        base = TreeRun(Tree("base", "base", config.root), problems or [], {"desktop": Shot([GEOMETRY, GEOMETRY], None)})
        return base, TreeRun(Tree("head", "HEAD", config.root), [], {"desktop": Shot([head, head], None)})

    monkeypatch.setattr(visual.capture, "capture_trees", trees)
    monkeypatch.setattr(visual.capture, "tool_problems", list)
    monkeypatch.setattr(
        visual.worktree, "start_commit", lambda config, task: ("abc", "no recorded start commit for t; using git merge-base main HEAD")
    )
    return seen


def gate(root: Path, raw: dict[str, Any], task: str | None, monkeypatch: pytest.MonkeyPatch) -> tuple[str, bool, str, list[str]]:
    if task is None:
        monkeypatch.delenv("MARESTAIL_TASK", raising=False)
    else:
        monkeypatch.setenv("MARESTAIL_TASK", task)
    return gate_shape(visual.run_gate(make_context(root, raw)))


def test_gate_skips(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = repo(tmp_path, None)
    assert gate(root, {"visual": {"enabled": False}}, "t", monkeypatch) == ("visual", True, "skipped: [visual] enabled = false", [])
    assert gate(root, {"visual": VISUAL}, None, monkeypatch) == ("visual", True, "skipped: no task; set MARESTAIL_TASK", [])
    assert gate(root, {"visual": VISUAL}, "t", monkeypatch) == ("visual", True, "visual: no qa/t.md; skipped", [])


def test_gate_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = fake_capture(monkeypatch, GEOMETRY)
    assert gate(repo(tmp_path), {"visual": VISUAL}, "t", monkeypatch) == ("visual", True, "1 viewport, geometry holds", [])
    assert seen == [("abc", 2)]
    raw = {"visual": {**VISUAL, "viewports": {"desktop": "1440x900", "phone": "390x844@2 touch"}}}
    monkeypatch.setattr(visual.capture, "capture_trees", lambda config, spec, sha, captures: (two_views(config), two_views(config)))
    assert gate(tmp_path, raw, "t", monkeypatch)[2] == "2 viewports, geometry holds"


def two_views(config: Config) -> TreeRun:
    return TreeRun(Tree("x", "x", config.root), [], {"desktop": Shot([GEOMETRY], None), "phone": Shot([GEOMETRY], None)})


def test_gate_fails_with_symptom_last(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_capture(monkeypatch, {**GEOMETRY, "scrollWidth": 1842}, ["base: setup failed (exit 1)", "  installing"])
    assert gate(repo(tmp_path), {"visual": VISUAL}, "t", monkeypatch) == (
        "visual",
        False,
        "1 visual findings",
        ["base: setup failed (exit 1)", "  installing", "  symptom: runs over the text"],
    )


def test_gate_fails_without_symptom(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_capture(monkeypatch, {**GEOMETRY, "scrollWidth": 1842})
    root = repo(tmp_path, "```visual\nroute: /\nselector: #widget\n```\n")
    assert gate(root, {"visual": VISUAL}, "t", monkeypatch)[1:3] == (False, "1 visual findings")


def test_malformed_block_and_missing_tools_stop_before_capture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = fake_capture(monkeypatch, GEOMETRY)
    root = repo(tmp_path, "```visual\nroute: /\ncolour: red\n```\n")
    assert gate(root, {"visual": VISUAL}, "t", monkeypatch)[1:] == (
        False,
        "2 visual findings",
        ["qa/t.md visual block: missing selector", "qa/t.md visual block: unknown key colour"],
    )
    (root / "qa" / "t.md").write_text(BLOCK)
    monkeypatch.setattr(visual.capture, "tool_problems", lambda: ["node not found on PATH; run marestail install"])
    assert gate(root, {"visual": VISUAL}, "t", monkeypatch)[1:] == (
        False,
        "1 visual findings",
        ["node not found on PATH; run marestail install"],
    )
    assert seen == []


def test_capture_command_skips(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = repo(tmp_path, "no block\n")
    assert visual.capture_command(Config(root, {}), "t") == 0
    assert visual.capture_command(Config(root, {"visual": VISUAL}), "t") == 0
    assert capsys.readouterr().out == "skipped: no [visual] section in marestail.toml\nvisual: no block in qa/t.md; skipped\n"


def test_capture_command_prints_note_and_folders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen = fake_capture(monkeypatch, GEOMETRY)
    assert visual.capture_command(Config(repo(tmp_path), {"visual": VISUAL}), "t") == 0
    assert seen == [("abc", 1)]
    assert capsys.readouterr().out == (
        "no recorded start commit for t; using git merge-base main HEAD\n"
        "base desktop: .marestail/runs/t/visual/base/desktop\n"
        "head desktop: .marestail/runs/t/visual/head/desktop\n"
    )


def test_capture_command_without_note_and_with_problems(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_capture(monkeypatch, GEOMETRY, ["base: app exited with 3 before answering"])
    monkeypatch.setattr(visual.worktree, "start_commit", lambda config, task: ("abc", ""))
    assert visual.capture_command(Config(repo(tmp_path), {"visual": VISUAL}), "t") == 1
    assert capsys.readouterr().out.splitlines()[0] == "base: app exited with 3 before answering"


def test_capture_command_malformed_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = repo(tmp_path, "```visual\nroute: /\n```\n")
    assert visual.capture_command(Config(root, {"visual": VISUAL}), "t") == 2
    assert capsys.readouterr().out == "qa/t.md visual block: missing selector\n"
