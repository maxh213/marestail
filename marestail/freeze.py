import re
from fnmatch import fnmatch

from marestail.config import Config

GATE_CONFIG = [
    "marestail.toml",
    "sonar-project.properties",
    "pyproject.toml",
    "**/setup.cfg",
    ".importlinter",
    "**/.coveragerc",
    "**/.dependency-cruiser.cjs",
    "**/stryker.config.*",
    "**/vite.config.*",
    "**/vitest.config.*",
    "**/eslint.config.*",
    "**/tsconfig*.json",
    "**/knip.json*",
    "**/knip.config.*",
    "mix.exs",
    "mix.lock",
    ".formatter.exs",
    ".credo.exs",
    "rebar.config",
    "rebar.lock",
    "**/*.app.src",
    "Gemfile",
    "Gemfile.lock",
    ".rubocop.yml",
    "**/.rubocop.yml",
    ".rspec",
    ".ruby-version",
    ".ruby-layers.json",
    "**/mutant.yml",
    "**/mutant.yaml",
    "**/jest.config.*",
    "**/babel.config.*",
    "**/.babelrc*",
    "**/next.config.*",
    "**/*.csproj",
    "**/*.sln",
    "**/*.props",
    "**/*.targets",
    "**/NuGet.config",
    "**/nuget.config",
    "**/dotnet-tools.json",
    "**/Cargo.toml",
    "Cargo.lock",
    "**/clippy.toml",
    "**/.clippy.toml",
    "**/rustfmt.toml",
    "**/.rustfmt.toml",
    "rust-toolchain",
    "rust-toolchain.toml",
    "**/.cargo/config.toml",
    "**/.cargo/mutants.toml",
    ".rust-layers.json",
    "**/.editorconfig",
    "**/.globalconfig",
    "**/*.runsettings",
    "**/stryker-config.*",
    ".dotnet-layers.json",
    "**/pom.xml",
    "mvnw",
    "mvnw.cmd",
    "**/.mvn/**",
    "**/lombok.config",
    "**/pmd-ruleset.xml",
    ".java-layers.json",
    ".claude/settings.json",
    "CLAUDE.md",
    "AGENTS.md",
    "GEMINI.md",
    ".agents/hooks.json",
    ".grok/hooks/**",
    ".grok/config.toml",
    ".cursor/hooks.json",
    ".cursor/hooks/**",
    ".cursor/cli.json",
]
SPEC = ["features/**", "qa/**", "tasks/**", "perf/**", "PERFORMANCE.md", "guidance/**"]
CSPROJ_ADDITION = re.compile(r'^\+\s*<(PackageReference|InternalsVisibleTo) Include="[^"]+"(?: Version="[^"]+")? />\s*$')
CSPROJ_WRAPPERS = ("", "<ItemGroup>", "</ItemGroup>")
DIFF_HEADERS = ("+++", "---")
ALLOWED = {
    "specifier": ["features/**", "qa/**"],
    "architect": [
        "pyproject.toml",
        ".importlinter",
        "**/.dependency-cruiser.cjs",
        "mix.exs",
        ".ruby-layers.json",
        ".dotnet-layers.json",
        ".rust-layers.json",
        ".java-layers.json",
    ],
}


ROLE_ERROR = "role"


def require_role(role: object) -> str:
    if type(role) is not str:
        raise TypeError(ROLE_ERROR)
    return role


def frozen_paths(config: Config, role: str, paths: list[str]) -> list[str]:
    role = require_role(role)
    frozen = config.get("freeze", "paths", GATE_CONFIG) + config.get("freeze", "spec", SPEC)
    allowed = {**ALLOWED, **config.get("freeze", "allow", {})}.get(role, [])
    return [path for path in paths if matches_any(path, frozen) and not matches_any(path, allowed)]


def tolerated(path: str, diff: str) -> bool:
    return matches(path, "**/*.csproj") and only_csproj_additions(changed_lines(diff))


def only_csproj_additions(changes: list[str]) -> bool:
    return any(is_addition(line) for line in changes) and all(is_csproj_line(line) for line in changes)


def changed_lines(diff: str) -> list[str]:
    return [line for line in diff.splitlines() if line[:1] in ("+", "-") and not line.startswith(DIFF_HEADERS)]


def is_addition(line: str) -> bool:
    return CSPROJ_ADDITION.match(line) is not None


def is_csproj_line(line: str) -> bool:
    return is_addition(line) or is_wrapper(line)


def is_wrapper(line: str) -> bool:
    return line.startswith("+") and line[1:].strip() in CSPROJ_WRAPPERS


def matches_any(path: str, patterns: list[str]) -> bool:
    return any(matches(path, pattern) for pattern in patterns)


DOUBLE_STAR = "**/"
DIR_SUFFIX = "/**"
ANY_DIR = "*/"


def matches(path: str, pattern: str) -> bool:
    if pattern.startswith(DOUBLE_STAR):
        rest = pattern[len(DOUBLE_STAR) :]
        return fnmatch(path, rest) or fnmatch(path, ANY_DIR + rest)
    if pattern.endswith(DIR_SUFFIX):
        return path.startswith(pattern[: -len(DIR_SUFFIX)] + "/")
    return fnmatch(path, pattern)
