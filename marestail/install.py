import json
import os
import shutil
import time
import tomllib
from pathlib import Path
from typing import Any

from marestail.shell import ensure_dir

PERFORMANCE = "PERFORMANCE.md"
CONFIG = "marestail.toml"
HOOKS = "hooks"
STOP = "Stop"
AGY_GATE = "marestail-gate"
TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
GITIGNORE_LINES = [
    ".marestail/",
    "mutants/",
    ".scannerwork/",
    ".venv/",
    ".coverage",
    "reports/mutation/",
    ".stryker-tmp/",
    "StrykerOutput/",
    ".sonarqube/",
    ".idea/",
    ".vscode/",
]
GITIGNORE_GENERATED_LINES = [
    "features/",
    "qa/",
    "tasks/",
    PERFORMANCE,
    "perf/",
    ".claude/settings.json",
    ".agents/hooks.json",
    ".grok/",
    ".cursor/hooks.json",
]
GATE_MARKER = "marestail gate"
VERSION = "version"
TRUSTED = "trusted"
DECIDED = "decided_at"
EMPTY_MAP: dict[str, Any] = {}
EMPTY_LIST: list[Any] = []
VERSION_DEFAULT = 1


def install(target: Path, gitignore_generated: bool = False) -> None:
    dotnet = uses_dotnet(target)
    copy_if_missing(TEMPLATES / CONFIG, target / CONFIG)
    if not dotnet:
        copy_if_missing(TEMPLATES / "sonar-project.properties", target / "sonar-project.properties")
    (target / "tasks").mkdir(exist_ok=True)
    copy_if_missing(TEMPLATES / "tasks-README.md", target / "tasks" / "README.md")
    copy_if_missing(TEMPLATES / PERFORMANCE, target / PERFORMANCE)
    (target / "guidance").mkdir(exist_ok=True)
    copy_if_missing(TEMPLATES / "guidance" / "ts.md", target / "guidance" / "ts.md")
    if uses_csharp(target):
        copy_if_missing(TEMPLATES / "guidance" / "cs.md", target / "guidance" / "cs.md")
    append_instructions(target / "CLAUDE.md")
    append_instructions(target / "AGENTS.md")
    merge_hook(target / ".claude" / "settings.json")
    merge_agy_hook(target / ".agents" / "hooks.json")
    merge_grok_hook(target / ".grok" / HOOKS / "marestail-gate.json")
    merge_cursor_hook(target / ".cursor" / "hooks.json")
    extend_gitignore(target / ".gitignore", GITIGNORE_GENERATED_LINES if gitignore_generated else [])
    trust_grok_folder(target)
    print(f"installed into {target}; edit marestail.toml and sonar-project.properties")


def uses_dotnet(target: Path) -> bool:
    config = target / CONFIG
    if not config.exists():
        return False
    with config.open("rb") as handle:
        return "dotnet" in tomllib.load(handle)


def uses_csharp(target: Path) -> bool:
    return next(target.rglob("*.csproj"), None) is not None


def copy_if_missing(source: Path, destination: Path) -> None:
    if not destination.exists():
        shutil.copy(source, destination)


def append_instructions(path: Path) -> None:
    snippet = (TEMPLATES / "CLAUDE.md").read_text()
    existing = path.read_text() if path.exists() else ""
    if GATE_MARKER not in existing:
        path.write_text(existing.rstrip() + ("\n\n" if existing else "") + snippet)


def read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text()) if path.exists() else default
    return data


def read_template(name: str) -> dict[str, Any]:
    template: dict[str, Any] = json.loads((TEMPLATES / name).read_text())
    return template


def write_json(path: Path, data: dict[str, Any]) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(data, indent=2) + "\n")


def mapping(data: dict[str, Any], key: str) -> dict[str, Any]:
    found = data.get(key, EMPTY_MAP)
    if not isinstance(found, dict):
        raise TypeError("map")
    return found


def listed(data: dict[str, Any], key: str) -> list[Any]:
    found = data.get(key, EMPTY_LIST)
    if not isinstance(found, list):
        raise TypeError("list")
    return found


def add_template_stops(settings: dict[str, Any], template: dict[str, Any], key: str, stop: str) -> None:
    stops = settings.setdefault(key, {}).setdefault(stop, [])
    if not any(GATE_MARKER in json.dumps(entry) for entry in stops):
        stops.extend(listed(mapping(template, key), stop))


def merge_template_hook(path: Path, template: dict[str, Any], key: str) -> None:
    settings = read_json(path, {})
    add_template_stops(settings, template, key, STOP)
    write_json(path, settings)


def merge_hook(path: Path) -> None:
    merge_template_hook(path, {HOOKS: {STOP: [read_template("stop-hook.json")]}}, HOOKS)


def merge_agy_hook(path: Path) -> None:
    merge_template_hook(path, read_template("agy-hooks.json"), AGY_GATE)


def merge_grok_hook(path: Path) -> None:
    merge_template_hook(path, read_template("grok-hooks.json"), HOOKS)


def mapping_default(data: dict[str, Any], key: str, default: Any) -> Any:
    if type(key) is not str:
        raise TypeError("key")
    if key not in data:
        return default
    return data[key]


def merge_cursor_hook(path: Path) -> None:
    settings = read_json(path, {"version": 1, HOOKS: {}})
    template = read_template("cursor-hooks.json")
    settings.setdefault(VERSION, mapping_default(template, VERSION, VERSION_DEFAULT))
    add_template_stops(settings, template, HOOKS, "stop")
    write_json(path, settings)


def trust_grok_folder(root: Path) -> None:
    store = grok_home() / "trusted_folders.toml"
    key = str(root.resolve())
    folders = trusted_folders(store)
    if is_trusted(folders.get(key)):
        return
    folders[key] = trust_entry()
    save_trusted_folders(store, key, folders)


def trust_entry() -> dict[str, Any]:
    return {TRUSTED: True, DECIDED: int(time.time())}


def grok_home() -> Path:
    return Path(os.environ.get("GROK_HOME", Path.home() / ".grok"))


def trusted_folders(store: Path) -> dict[str, Any]:
    if not store.exists():
        return {}
    folders: dict[str, Any] = tomllib.loads(store.read_text()).get("folders") or {}
    return folders


def is_trusted(entry: Any) -> bool:
    return isinstance(entry, dict) and bool(entry.get("trusted"))


def folder_lines(path: str, meta: Any) -> list[str]:
    fields = meta if isinstance(meta, dict) else {}
    trusted = fields.get(TRUSTED, True)
    decided = fields.get(DECIDED, int(time.time()))
    return [f"[folders.{json.dumps(path)}]", f"trusted = {str(bool(trusted)).lower()}", f"decided_at = {int(decided)}", ""]


def save_trusted_folders(store: Path, key: str, folders: dict[str, Any]) -> None:
    lines = [line for path, meta in folders.items() for line in folder_lines(path, meta)]
    try:
        ensure_dir(store.parent)
        store.write_text("\n".join(lines))
        store.chmod(0o600)
    except OSError as error:
        print(f"could not trust {key} for grok hooks: {error}")
        return
    print(f"trusted {key} for grok project hooks")


def extend_gitignore(path: Path, extra: list[str] | None = None) -> None:
    existing = path.read_text().splitlines() if path.exists() else []
    missing = missing_lines(existing, extra or [])
    if missing:
        path.write_text("\n".join([*existing, *gitignore_header(existing), *missing]) + "\n")


def missing_lines(existing: list[str], extra: list[str]) -> list[str]:
    return [line for line in [*GITIGNORE_LINES, *extra] if line not in existing]


def gitignore_header(existing: list[str]) -> list[str]:
    return [] if "# marestail" in existing else ["", "# marestail"]
