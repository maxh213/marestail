from fnmatch import fnmatch

from marestail.config import Config

GATE_CONFIG = [
    "marestail.toml", "sonar-project.properties", "pyproject.toml", "**/setup.cfg", ".importlinter",
    "**/.coveragerc", "**/.dependency-cruiser.cjs", "**/stryker.config.*", "**/vite.config.*",
    "**/vitest.config.*", "**/eslint.config.*", "**/tsconfig*.json", "**/knip.json*", "**/knip.config.*", ".claude/settings.json", "CLAUDE.md",
]
SPEC = ["features/**", "qa/**", "tasks/**"]
ALLOWED = {
    "specifier": ["features/**", "qa/**"],
    "architect": ["pyproject.toml", ".importlinter", "**/.dependency-cruiser.cjs"],
}


def frozen_paths(config: Config, role: str, paths: list[str]) -> list[str]:
    frozen = config.get("freeze", "paths", GATE_CONFIG) + config.get("freeze", "spec", SPEC)
    allowed = {**ALLOWED, **config.get("freeze", "allow", {})}.get(role, [])
    return [path for path in paths if matches_any(path, frozen) and not matches_any(path, allowed)]


def matches_any(path: str, patterns: list[str]) -> bool:
    return any(matches(path, pattern) for pattern in patterns)


def matches(path: str, pattern: str) -> bool:
    if pattern.startswith("**/"):
        rest = pattern[3:]
        return fnmatch(path, rest) or fnmatch(path, "*/" + rest)
    if pattern.endswith("/**"):
        return path.startswith(pattern[:-3] + "/")
    return fnmatch(path, pattern)
