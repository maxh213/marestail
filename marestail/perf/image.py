import re
from pathlib import Path

DEFAULT_IMAGE = "postgres:18"
COMPOSE_GLOBS = ("docker-compose*.yml", "docker-compose*.yaml", "compose*.yml", "compose*.yaml")
COMPOSE_IMAGE = re.compile(r"^\s*image:\s*[\"']?(postgres:[\w.-]+)", re.MULTILINE)
WORKFLOW_IMAGE = re.compile(r"(postgres:[\w.-]+)")
TOOL_VERSION = re.compile(r"^postgres\s+(\d+)", re.MULTILINE)
ALPINE = re.compile(r"-alpine[\w.]*$")


def resolve(root: Path, configured: str | None) -> tuple[str, str]:
    if configured:
        return configured, "marestail.toml"
    for path in sorted({path for pattern in COMPOSE_GLOBS for path in root.glob(pattern)}):
        found = COMPOSE_IMAGE.search(path.read_text())
        if found:
            return found.group(1), path.name
    workflows = root / ".github" / "workflows"
    for path in sorted([*workflows.glob("*.yml"), *workflows.glob("*.yaml")]):
        found = WORKFLOW_IMAGE.search(path.read_text())
        if found:
            return found.group(1), path.relative_to(root).as_posix()
    tool_versions = root / ".tool-versions"
    found = TOOL_VERSION.search(tool_versions.read_text()) if tool_versions.is_file() else None
    if found:
        return f"postgres:{found.group(1)}", ".tool-versions"
    return DEFAULT_IMAGE, "default"


def helper_image(image: str) -> str:
    return ALPINE.sub("", image)
