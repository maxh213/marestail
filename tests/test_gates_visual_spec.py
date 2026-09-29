import re
from pathlib import Path
from typing import Any

import pytest

from marestail.config import Config
from marestail.gates.qa import _visual_spec as spec_module
from marestail.gates.qa._visual_spec import Block, Viewport

BLOCK = """# QA

```visual
route: /donate
selector: iframe[name=donorbox]
scroll: true
wait: iframe[src*=donorbox]
styles: border-radius, overflow, width
inside: .block-column
unchanged: x-centre
must_not_change: header, main h1
symptom: the corner is round
```

```visual
route: /ignored
```
"""


def config_with(root: Path, visual: dict[str, Any] | None = None, qa: dict[str, Any] | None = None) -> Config:
    raw: dict[str, Any] = {"qa": qa or {}}
    if visual is not None:
        raw["visual"] = visual
    return Config(root=root, raw=raw)


def write_qa(root: Path, text: str, task: str = "t") -> None:
    (root / "qa").mkdir(exist_ok=True)
    (root / "qa" / f"{task}.md").write_text(text)


def test_skip_reason_in_order(tmp_path: Path) -> None:
    assert spec_module.skip_reason(config_with(tmp_path, {"enabled": False}), None) == "skipped: [visual] enabled = false"
    config = config_with(tmp_path, {"enabled": True})
    assert spec_module.skip_reason(config, None) == "skipped: no task; set MARESTAIL_TASK"
    assert spec_module.skip_reason(config, "") == "skipped: no task; set MARESTAIL_TASK"
    assert spec_module.skip_reason(config, "t") == "visual: no qa/t.md; skipped"
    write_qa(tmp_path, "no block here\n```python\nx\n```\n")
    assert spec_module.skip_reason(config, "t") == "visual: no block in qa/t.md; skipped"
    write_qa(tmp_path, BLOCK)
    assert spec_module.skip_reason(config, "t") is None
    assert spec_module.skip_reason(config_with(tmp_path, {}), "t") is None


def test_block_lines_takes_the_first_visual_fence() -> None:
    assert spec_module._block_lines(BLOCK) == [
        "route: /donate",
        "selector: iframe[name=donorbox]",
        "scroll: true",
        "wait: iframe[src*=donorbox]",
        "styles: border-radius, overflow, width",
        "inside: .block-column",
        "unchanged: x-centre",
        "must_not_change: header, main h1",
        "symptom: the corner is round",
    ]
    assert spec_module._block_lines("~~~ visual\nroute: /\n") == ["route: /"]
    assert spec_module._block_lines("```visualise\nroute: /\n```\n") is None


def test_load_reads_block_and_settings(tmp_path: Path) -> None:
    write_qa(tmp_path, BLOCK)
    visual = {
        "setup": "yarn",
        "port": 3500,
        "env": {"CMS_URL": "https://cms", "N": 1},
        "viewports": {"desktop": "1440x900", "phone": "390x844@2 touch", "retina": "800x600@1.5"},
        "hide": ["#cookie"],
        "block": ["*gtm*"],
        "tolerance_px": 3,
        "capture_timeout": 5,
        "setup_timeout": 7,
        "ready_timeout": 9,
        "start": "yarn dev",
    }
    spec, problems = spec_module.load(config_with(tmp_path, visual), "t")
    assert problems == []
    assert spec is not None
    assert spec.task == "t"
    assert spec.block == Block(
        route="/donate",
        selector="iframe[name=donorbox]",
        scroll=True,
        wait="iframe[src*=donorbox]",
        styles=["border-radius", "overflow", "width"],
        inside=".block-column",
        unchanged=["x-centre"],
        must_not_change=["header", "main h1"],
        symptom="the corner is round",
    )
    settings = spec.settings
    assert (settings.start, settings.setup, settings.ready, settings.port) == ("yarn dev", "yarn", "/", 3500)
    assert settings.env == {"CMS_URL": "https://cms", "N": "1"}
    assert settings.viewports == [
        Viewport("desktop", 1440, 900, 1, False),
        Viewport("phone", 390, 844, 2, True),
        Viewport("retina", 800, 600, 1.5, False),
    ]
    assert type(settings.viewports[0].scale) is int
    assert settings.hide == ["#cookie"]
    assert settings.block == ["^[\\s\\S]*gtm[\\s\\S]*$"]
    assert (settings.tolerance, settings.capture_timeout, settings.setup_timeout, settings.ready_timeout) == (3, 5, 7, 9)


def test_load_defaults(tmp_path: Path) -> None:
    write_qa(tmp_path, "```visual\nroute: /\nselector: #w\n```\n")
    spec, _ = spec_module.load(config_with(tmp_path, {}, {"start": "qa start", "ready_timeout": 44}), "t")
    assert spec is not None
    assert spec.block == Block("/", "#w", False, "", [], "", [], [], "")
    settings = spec.settings
    assert (settings.start, settings.setup, settings.ready, settings.port, settings.env) == ("qa start", "", "/", 3400, {})
    assert (settings.viewports, settings.hide, settings.block) == ([], [], [])
    assert (settings.tolerance, settings.capture_timeout, settings.setup_timeout, settings.ready_timeout) == (2, 30, 900, 44)
    spec, _ = spec_module.load(config_with(tmp_path, {}), "t")
    assert spec is not None
    assert (spec.settings.start, spec.settings.ready_timeout) == ("", 180)


def test_load_reports_problems_in_order(tmp_path: Path) -> None:
    write_qa(tmp_path, "```visual\ncolour: red\nunchanged: middle, width, centre\nnonsense\n\nsize: 3\n```\n")
    config = config_with(tmp_path, {"viewports": {"phone": "390by844", "desktop": "1440x900", "tv": "1x2@x"}})
    assert spec_module.load(config, "t") == (
        None,
        [
            "qa/t.md visual block: missing route",
            "qa/t.md visual block: missing selector",
            "qa/t.md visual block: unknown key colour",
            "qa/t.md visual block: unknown key nonsense",
            "qa/t.md visual block: unknown key size",
            "qa/t.md visual block: unknown measure middle in unchanged",
            "qa/t.md visual block: unknown measure centre in unchanged",
            '[visual] viewports: bad viewport phone = "390by844"; expected WxH[@scale][ touch]',
            '[visual] viewports: bad viewport tv = "1x2@x"; expected WxH[@scale][ touch]',
        ],
    )


def test_selector_with_colons_keeps_its_value(tmp_path: Path) -> None:
    write_qa(tmp_path, "```visual\nroute: /\nselector: a:hover\nscroll: TRUE\n```\n")
    spec, _ = spec_module.load(config_with(tmp_path, {}), "t")
    assert spec is not None
    assert (spec.block.selector, spec.block.scroll) == ("a:hover", True)


@pytest.mark.parametrize(
    ("pattern", "url", "matches"),
    [
        ("*tracker*", "http://localhost:3400/tracker.js", True),
        ("*googletagmanager*", "https://www.googletagmanager.com/gtm.js", True),
        ("*nomatch*", "http://localhost:3400/tracker.js", False),
        ("http://a/?.js", "http://a/b.js", True),
        ("http://a/?.js", "http://a/bc.js", False),
        ("*[ab].js", "http://x/a.js", True),
        ("*[!ab].js", "http://x/a.js", False),
        ("*[!ab].js", "http://x/c.js", True),
        ("*[x", "http://a/[x", True),
        ("a.b", "axb", False),
    ],
)
def test_glob_regex_follows_fnmatchcase(pattern: str, url: str, matches: bool) -> None:
    assert bool(re.search(spec_module._glob_regex(pattern), url)) is matches
