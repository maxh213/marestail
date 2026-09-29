import re
from pathlib import Path
from typing import Any

from marestail.config import Config
from marestail.gates.visual._model import Block, Settings, Spec, Viewport

SECTION = "visual"
_KEYS = ("route", "selector", "scroll", "wait", "styles", "inside", "unchanged", "must_not_change", "symptom")
_REQUIRED = ("route", "selector")
_MEASURES = ("x-centre", "y-centre", "left", "right", "top", "bottom", "width", "height")
_FENCES = ("```", "~~~")
_VIEWPORT = re.compile(r"^(\d+)x(\d+)(?:@(\d+(?:\.\d+)?))?( touch)?$")
_WILDCARDS = {"*": "[\\s\\S]*", "?": "[\\s\\S]"}
_DEFAULT_PORT = 3400
_DEFAULT_READY = "/"
_DEFAULT_READY_TIMEOUT = 180
_DEFAULT_TOLERANCE = 2
_DEFAULT_CAPTURE_TIMEOUT = 30
_DEFAULT_SETUP_TIMEOUT = 900


def _qa_file(config: Config, task: str) -> Path:
    return config.root / "qa" / f"{task}.md"


def skip_reason(config: Config, task: str | None) -> str | None:
    if config.get(SECTION, "enabled", True) is False:
        return "skipped: [visual] enabled = false"
    if not task:
        return "skipped: no task; set MARESTAIL_TASK"
    return _missing_block(config, task)


def _missing_block(config: Config, task: str) -> str | None:
    path = _qa_file(config, task)
    if not path.exists():
        return f"visual: no qa/{task}.md; skipped"
    return None if _block_lines(path.read_text()) is not None else f"visual: no block in qa/{task}.md; skipped"


def _block_lines(text: str) -> list[str] | None:
    lines = text.splitlines()
    starts = [index for index, line in enumerate(lines) if _opens_block(line)]
    if not starts:
        return None
    body = lines[starts[0] + 1 :]
    return body[: _closing(body)]


def _opens_block(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith(_FENCES) and stripped[3:].strip() == SECTION


def _closing(body: list[str]) -> int:
    return next((index for index, line in enumerate(body) if line.strip().startswith(_FENCES)), len(body))


def load(config: Config, task: str) -> tuple[Spec | None, list[str]]:
    pairs = _block_pairs(_block_lines(_qa_file(config, task).read_text()) or [])
    viewports, bad = _parse_viewports(config.get(SECTION, "viewports", {}))
    problems = _block_problems(task, pairs) + bad
    if problems:
        return None, problems
    return Spec(task, _make_block(dict(pairs)), _make_settings(config, viewports)), []


def _block_pairs(lines: list[str]) -> list[tuple[str, str]]:
    split = [line.partition(":") for line in lines if line.strip()]
    return [(key.strip(), value.strip()) for key, _, value in split]


def _listed(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def _block_problems(task: str, pairs: list[tuple[str, str]]) -> list[str]:
    prefix = f"qa/{task}.md visual block:"
    keys = [key for key, _ in pairs]
    return _missing_keys(prefix, keys) + _unknown_keys(prefix, keys) + _unknown_measures(prefix, dict(pairs).get("unchanged", ""))


def _missing_keys(prefix: str, keys: list[str]) -> list[str]:
    return [f"{prefix} missing {key}" for key in _REQUIRED if key not in keys]


def _unknown_keys(prefix: str, keys: list[str]) -> list[str]:
    return [f"{prefix} unknown key {key}" for key in keys if key not in _KEYS]


def _unknown_measures(prefix: str, unchanged: str) -> list[str]:
    return [f"{prefix} unknown measure {name} in unchanged" for name in _listed(unchanged) if name not in _MEASURES]


def _parse_viewports(raw: dict[str, Any]) -> tuple[list[Viewport], list[str]]:
    parsed = [(str(name), str(value), _VIEWPORT.match(str(value))) for name, value in raw.items()]
    return _good_viewports(parsed), _bad_viewports(parsed)


def _good_viewports(parsed: list[tuple[str, str, re.Match[str] | None]]) -> list[Viewport]:
    return [_make_viewport(name, match) for name, _, match in parsed if match]


def _bad_viewports(parsed: list[tuple[str, str, re.Match[str] | None]]) -> list[str]:
    return [
        f'[visual] viewports: bad viewport {name} = "{value}"; expected WxH[@scale][ touch]' for name, value, match in parsed if not match
    ]


def _make_viewport(name: str, match: re.Match[str]) -> Viewport:
    width, height, scale, touch = match.groups()
    number = float(scale or 1)
    return Viewport(name, int(width), int(height), int(number) if number.is_integer() else number, bool(touch))


def _make_block(values: dict[str, str]) -> Block:
    return Block(
        route=values["route"],
        selector=values["selector"],
        scroll=values.get("scroll", "false").lower() == "true",
        wait=values.get("wait", ""),
        styles=_listed(values.get("styles", "")),
        inside=values.get("inside", ""),
        unchanged=_listed(values.get("unchanged", "")),
        must_not_change=_listed(values.get("must_not_change", "")),
        symptom=values.get("symptom", ""),
    )


def _make_settings(config: Config, viewports: list[Viewport]) -> Settings:
    section = config.section(SECTION) or {}
    return Settings(
        start=_start_command(config, section),
        setup=str(section.get("setup", "")),
        ready=str(section.get("ready", _DEFAULT_READY)),
        port=int(section.get("port", _DEFAULT_PORT)),
        env=_strings(section.get("env", {})),
        viewports=viewports,
        hide=[str(item) for item in section.get("hide", [])],
        block=[_glob_regex(str(item)) for item in section.get("block", [])],
        tolerance=int(section.get("tolerance_px", _DEFAULT_TOLERANCE)),
        capture_timeout=int(section.get("capture_timeout", _DEFAULT_CAPTURE_TIMEOUT)),
        setup_timeout=int(section.get("setup_timeout", _DEFAULT_SETUP_TIMEOUT)),
        ready_timeout=_ready_timeout(config, section),
    )


def _start_command(config: Config, section: dict[str, Any]) -> str:
    return str(section.get("start") or config.get("qa", "start", ""))


def _strings(raw: dict[str, Any]) -> dict[str, str]:
    return {str(key): str(value) for key, value in raw.items()}


def _ready_timeout(config: Config, section: dict[str, Any]) -> int:
    return int(section.get("ready_timeout") or config.get("qa", "ready_timeout", _DEFAULT_READY_TIMEOUT))


def _glob_regex(pattern: str) -> str:
    parts = []
    index = 0
    while index < len(pattern):
        part, index = _glob_part(pattern, index)
        parts.append(part)
    return "^" + "".join(parts) + "$"


def _glob_part(pattern: str, index: int) -> tuple[str, int]:
    char = pattern[index]
    if char in _WILDCARDS:
        return _WILDCARDS[char], index + 1
    if char == "[":
        return _bracket(pattern, index)
    return re.escape(char), index + 1


def _bracket(pattern: str, index: int) -> tuple[str, int]:
    end = pattern.find("]", index + 2)
    if end < 0:
        return re.escape("["), index + 1
    body = pattern[index + 1 : end]
    return "[" + ("^" + body[1:] if body.startswith("!") else body) + "]", end + 1
