import json
import shutil
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
GITIGNORE_LINES = [".marestail/", "mutants/", ".scannerwork/", ".venv/", ".coverage", "reports/mutation/", ".stryker-tmp/", ".idea/", ".vscode/"]
CLAUDE_MARKER = "marestail gate"


def install(target: Path) -> None:
    copy_if_missing(TEMPLATES / "marestail.toml", target / "marestail.toml")
    copy_if_missing(TEMPLATES / "sonar-project.properties", target / "sonar-project.properties")
    (target / "tasks").mkdir(exist_ok=True)
    copy_if_missing(TEMPLATES / "tasks-README.md", target / "tasks" / "README.md")
    append_claude_md(target / "CLAUDE.md")
    merge_hook(target / ".claude" / "settings.json")
    extend_gitignore(target / ".gitignore")
    print(f"installed into {target}; edit marestail.toml and sonar-project.properties")


def copy_if_missing(source: Path, destination: Path) -> None:
    if not destination.exists():
        shutil.copy(source, destination)


def append_claude_md(path: Path) -> None:
    snippet = (TEMPLATES / "CLAUDE.md").read_text()
    existing = path.read_text() if path.exists() else ""
    if CLAUDE_MARKER not in existing:
        path.write_text(existing.rstrip() + ("\n\n" if existing else "") + snippet)


def merge_hook(path: Path) -> None:
    settings = json.loads(path.read_text()) if path.exists() else {}
    hook = json.loads((TEMPLATES / "stop-hook.json").read_text())
    stops = settings.setdefault("hooks", {}).setdefault("Stop", [])
    if not any(CLAUDE_MARKER in json.dumps(entry) for entry in stops):
        stops.append(hook)
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(settings, indent=2) + "\n")


def extend_gitignore(path: Path) -> None:
    existing = path.read_text().splitlines() if path.exists() else []
    missing = [line for line in GITIGNORE_LINES if line not in existing]
    if missing:
        header = [] if "# marestail" in existing else ["", "# marestail"]
        path.write_text("\n".join([*existing, *header, *missing]) + "\n")
