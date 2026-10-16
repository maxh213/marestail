import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from marestail.shell import ensure_dir

TOOLING = ".marestail/tooling"
SONAR = "sonar-project.properties"
DEPCRUISE = ".dependency-cruiser.cjs"
TSCONFIG = "tsconfig.json"
VITEST_CONFIG = "vitest.config.ts"
TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
ESLINT_KINDS = ("eslint.config.*", ".eslintrc", ".eslintrc.js", ".eslintrc.cjs", ".eslintrc.json", ".eslintrc.yml", ".eslintrc.yaml")
VITEST_KINDS = ("vitest.config.*", "vite.config.*")
STRYKER_KINDS = ("stryker.conf.*", "stryker.config.*")
KNIP_KINDS = ("knip.json", "knip.jsonc", ".knip.json", ".knip.jsonc", "knip.ts", "knip.config.*")
SOURCE = "<S>"
SOURCE_FILES = ["<S>/**/*.{ts,tsx}"]
TEST_FILES = ["<S>/**/*.{test,spec}.{ts,tsx}"]
MUTATED_FILES = ["<S>/**/*.ts", "<S>/**/*.tsx", "!<S>/**/*.test.*", "!<S>/**/*.spec.*"]
ENTRY_FILES = ["<S>/index.{ts,tsx}", "<S>/main.{ts,tsx}"]
STRYKER_VERSION = "^9.0.0"
BASE_DEPENDENCIES = {
    "@stryker-mutator/core": STRYKER_VERSION,
    "dependency-cruiser": "^16.0.0",
    "eslint": "^9.0.0",
    "knip": "^5.0.0",
    "typescript": "^5.8.0",
    "typescript-eslint": "^8.0.0",
}
VITEST_DEPENDENCIES = {"@stryker-mutator/vitest-runner": STRYKER_VERSION, "@vitest/coverage-v8": "^3.2.0", "vitest": "^3.2.0"}
JEST_DEPENDENCIES = {"@stryker-mutator/jest-runner": STRYKER_VERSION}

__all__ = ["TOOLING", "Tooling", "write_tooling"]


@dataclass(frozen=True)
class Tooling:
    target: Path
    ts: dict[str, Any]

    @property
    def folder(self) -> Path:
        return self.target / TOOLING

    @property
    def ts_root(self) -> Path:
        return self.target / str(self.ts.get("root", "."))

    @property
    def sources(self) -> list[str]:
        return [str(source) for source in self.ts.get("sources") or [self.ts.get("source", "src")]]

    @property
    def jest(self) -> bool:
        return self.ts.get("runner") == "jest"

    @property
    def up(self) -> str:
        return os.path.relpath(self.ts_root, self.folder)

    @property
    def tsconfig_name(self) -> str:
        return str(self.ts.get("tsconfig", "tsconfig.app.json"))

    def has(self, patterns: tuple[str, ...]) -> bool:
        return any(next(self.ts_root.glob(pattern), None) is not None for pattern in patterns)

    def has_tsconfig(self) -> bool:
        return (self.ts_root / self.tsconfig_name).exists()

    def has_depcruise(self) -> bool:
        return (self.ts_root / str(self.ts.get("depcruise_config", DEPCRUISE))).exists()

    def has_vitest(self) -> bool:
        return self.jest or self.has(VITEST_KINDS)

    def each_source(self, patterns: list[str]) -> str:
        return json_list([pattern.replace(SOURCE, source) for pattern in patterns for source in self.sources])


def json_list(items: list[str]) -> str:
    return "[" + ", ".join(json.dumps(item) for item in items) + "]"


def fill(name: str, values: dict[str, str]) -> str:
    text = (TEMPLATES / "tooling" / name).read_text()
    for key, value in values.items():
        text = text.replace(f"@{key}@", value)
    return text


def package_json(tooling: Tooling) -> str:
    extra = JEST_DEPENDENCIES if tooling.jest else VITEST_DEPENDENCIES
    dependencies = dict(sorted({**BASE_DEPENDENCIES, **extra}.items()))
    return json.dumps({"name": "marestail-tooling", "private": True, "devDependencies": dependencies}, indent=2) + "\n"


def eslint_text(tooling: Tooling) -> str:
    return fill("lint.mjs", {"SOURCES": tooling.each_source(SOURCE_FILES)})


def type_setting(tooling: Tooling) -> str:
    if tooling.jest:
        return f'"typeRoots": {json_list([f"{tooling.up}/node_modules/@types"])}'
    return '"types": ["vitest/globals"]'


def tsconfig_text(tooling: Tooling) -> str:
    include = json_list([f"{tooling.up}/{source}" for source in tooling.sources])
    return fill("types.json", {"TYPES": type_setting(tooling), "INCLUDE": include})


def depcruise_text(tooling: Tooling) -> str:
    name = tooling.tsconfig_name if tooling.has_tsconfig() else str(tooling.folder / TSCONFIG)
    return (TEMPLATES / "dependency-cruiser.cjs").read_text().replace('fileName: "tsconfig.app.json"', f"fileName: {json.dumps(name)}")


def vitest_text(tooling: Tooling) -> str:
    return fill("vitest.ts", {"TESTS": tooling.each_source(TEST_FILES), "SOURCES": tooling.each_source(SOURCE_FILES)})


def runner_options(tooling: Tooling) -> str:
    if tooling.jest:
        return '  "jest": { "projectType": "custom" },\n'
    if tooling.has(VITEST_KINDS):
        return ""
    return f'  "vitest": {{ "configFile": {json.dumps(str(tooling.folder / VITEST_CONFIG))} }},\n'


def stryker_text(tooling: Tooling) -> str:
    runner = "jest" if tooling.jest else "vitest"
    return fill("mutation.json", {"RUNNER": runner, "OPTIONS": runner_options(tooling), "MUTATE": tooling.each_source(MUTATED_FILES)})


def knip_text(tooling: Tooling) -> str:
    return fill("deadcode.json", {"ENTRY": tooling.each_source(ENTRY_FILES), "SOURCES": tooling.each_source(SOURCE_FILES)})


def sonar_line(line: str) -> str:
    return line + ",.marestail/**" if line.startswith("sonar.exclusions=") else line


def sonar_text(_tooling: Tooling) -> str:
    return "\n".join(sonar_line(line) for line in (TEMPLATES / SONAR).read_text().splitlines()) + "\n"


def tooling_plan(tooling: Tooling) -> list[tuple[str, bool, Callable[[Tooling], str]]]:
    return [
        ("package.json", False, package_json),
        ("eslint.config.mjs", tooling.has(ESLINT_KINDS), eslint_text),
        (TSCONFIG, tooling.has_tsconfig(), tsconfig_text),
        (DEPCRUISE, tooling.has_depcruise(), depcruise_text),
        (VITEST_CONFIG, tooling.has_vitest(), vitest_text),
        ("stryker.config.json", tooling.has(STRYKER_KINDS), stryker_text),
        ("knip.json", tooling.has(KNIP_KINDS), knip_text),
        (SONAR, (tooling.target / SONAR).exists(), sonar_text),
    ]


def write_tooling(tooling: Tooling) -> None:
    ensure_dir(tooling.folder)
    for name, present, render in tooling_plan(tooling):
        if not present:
            (tooling.folder / name).write_text(render(tooling))
