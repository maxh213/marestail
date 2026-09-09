import json
import os
import shutil
import time
import tomllib
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
GITIGNORE_LINES = [".marestail/", "mutants/", ".scannerwork/", ".venv/", ".coverage", "reports/mutation/", ".stryker-tmp/", ".idea/", ".vscode/"]
GATE_MARKER = "marestail gate"


def install(target: Path) -> None:
    copy_if_missing(TEMPLATES / "marestail.toml", target / "marestail.toml")
    copy_if_missing(TEMPLATES / "sonar-project.properties", target / "sonar-project.properties")
    (target / "tasks").mkdir(exist_ok=True)
    copy_if_missing(TEMPLATES / "tasks-README.md", target / "tasks" / "README.md")
    append_instructions(target / "CLAUDE.md")
    append_instructions(target / "AGENTS.md")
    merge_hook(target / ".claude" / "settings.json")
    merge_agy_hook(target / ".agents" / "hooks.json")
    merge_grok_hook(target / ".grok" / "hooks" / "marestail-gate.json")
    extend_gitignore(target / ".gitignore")
    trust_grok_folder(target)
    print(f"installed into {target}; edit marestail.toml and sonar-project.properties")


def copy_if_missing(source: Path, destination: Path) -> None:
    if not destination.exists():
        shutil.copy(source, destination)


def append_instructions(path: Path) -> None:
    snippet = (TEMPLATES / "CLAUDE.md").read_text()
    existing = path.read_text() if path.exists() else ""
    if GATE_MARKER not in existing:
        path.write_text(existing.rstrip() + ("\n\n" if existing else "") + snippet)


def merge_hook(path: Path) -> None:
    settings = json.loads(path.read_text()) if path.exists() else {}
    hook = json.loads((TEMPLATES / "stop-hook.json").read_text())
    stops = settings.setdefault("hooks", {}).setdefault("Stop", [])
    if not any(GATE_MARKER in json.dumps(entry) for entry in stops):
        stops.append(hook)
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(settings, indent=2) + "\n")


def merge_agy_hook(path: Path) -> None:
    settings = json.loads(path.read_text()) if path.exists() else {}
    template = json.loads((TEMPLATES / "agy-hooks.json").read_text())
    gate_hook = settings.setdefault("marestail-gate", {})
    stops = gate_hook.setdefault("Stop", [])
    if not any(GATE_MARKER in json.dumps(entry) for entry in stops):
        stops.extend(template.get("marestail-gate", {}).get("Stop", []))
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(settings, indent=2) + "\n")


def merge_grok_hook(path: Path) -> None:
    settings = json.loads(path.read_text()) if path.exists() else {}
    template = json.loads((TEMPLATES / "grok-hooks.json").read_text())
    stops = settings.setdefault("hooks", {}).setdefault("Stop", [])
    if not any(GATE_MARKER in json.dumps(entry) for entry in stops):
        stops.extend(template.get("hooks", {}).get("Stop", []))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2) + "\n")


def trust_grok_folder(root: Path) -> None:
    home = Path(os.environ.get("GROK_HOME", Path.home() / ".grok"))
    store = home / "trusted_folders.toml"
    key = str(root.resolve())
    folders: dict = {}
    if store.exists():
        folders = tomllib.loads(store.read_text()).get("folders") or {}
    entry = folders.get(key)
    if isinstance(entry, dict) and entry.get("trusted"):
        return
    folders[key] = {"trusted": True, "decided_at": int(time.time())}
    lines = []
    for path, meta in folders.items():
        trusted = meta.get("trusted", True) if isinstance(meta, dict) else True
        decided = meta.get("decided_at", int(time.time())) if isinstance(meta, dict) else int(time.time())
        lines += [f"[folders.{json.dumps(path)}]", f"trusted = {str(bool(trusted)).lower()}", f"decided_at = {int(decided)}", ""]
    try:
        store.parent.mkdir(parents=True, exist_ok=True)
        store.write_text("\n".join(lines))
        store.chmod(0o600)
    except OSError as error:
        print(f"could not trust {key} for grok hooks: {error}")
        return
    print(f"trusted {key} for grok project hooks")


def extend_gitignore(path: Path) -> None:
    existing = path.read_text().splitlines() if path.exists() else []
    missing = [line for line in GITIGNORE_LINES if line not in existing]
    if missing:
        header = [] if "# marestail" in existing else ["", "# marestail"]
        path.write_text("\n".join([*existing, *header, *missing]) + "\n")
