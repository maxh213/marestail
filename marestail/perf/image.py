import re
from collections.abc import Callable
from pathlib import Path

DEFAULT_IMAGE = "postgres:18"
COMPOSE_GLOBS = ("docker-compose*.yml", "docker-compose*.yaml", "compose*.yml", "compose*.yaml")
COMPOSE_IMAGE = re.compile(r"^\s*image:\s*[\"']?(postgres:[\w.-]+)", re.MULTILINE)
WORKFLOW_IMAGE = re.compile(r"(postgres:[\w.-]+)")
TOOL_VERSION = re.compile(r"^postgres\s+(\d+)", re.MULTILINE)
ALPINE = re.compile(r"-alpine[\w.]*$")
TOOL_VERSIONS = ".tool-versions"

Found = tuple[str, str] | None


def resolve(root: Path, configured: str | None) -> tuple[str, str]:
    if configured:
        return configured, "marestail.toml"
    return next((found for finder in FINDERS if (found := finder(root))), (DEFAULT_IMAGE, "default"))


def first_image(paths: list[Path], pattern: re.Pattern[str], label: Callable[[Path], str]) -> Found:
    for path in paths:
        found = pattern.search(path.read_text())
        if found:
            return found.group(1), label(path)
    return None


def compose_image(root: Path) -> Found:
    paths = sorted({path for pattern in COMPOSE_GLOBS for path in root.glob(pattern)})
    return first_image(paths, COMPOSE_IMAGE, lambda path: path.name)


def workflow_image(root: Path) -> Found:
    workflows = root / ".github" / "workflows"
    paths = sorted([*workflows.glob("*.yml"), *workflows.glob("*.yaml")])
    return first_image(paths, WORKFLOW_IMAGE, lambda path: path.relative_to(root).as_posix())


def tool_versions_image(root: Path) -> Found:
    path = root / TOOL_VERSIONS
    found = TOOL_VERSION.search(path.read_text()) if path.is_file() else None
    return (f"postgres:{found.group(1)}", TOOL_VERSIONS) if found else None


FINDERS: tuple[Callable[[Path], Found], ...] = (compose_image, workflow_image, tool_versions_image)


def helper_image(image: str) -> str:
    return ALPINE.sub("", image)
