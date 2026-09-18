import re
from fnmatch import fnmatch

from marestail.config import Config

GATE_CONFIG = [
    "marestail.toml", "sonar-project.properties", "pyproject.toml", "**/setup.cfg", ".importlinter",
    "**/.coveragerc", "**/.dependency-cruiser.cjs", "**/stryker.config.*", "**/vite.config.*",
    "**/vitest.config.*", "**/eslint.config.*", "**/tsconfig*.json", "**/knip.json*", "**/knip.config.*",
    "mix.exs", "mix.lock", ".formatter.exs", ".credo.exs",
    "rebar.config", "rebar.lock", "**/*.app.src",
    "Gemfile", "Gemfile.lock", ".rubocop.yml", "**/.rubocop.yml", ".rspec", ".ruby-version", ".ruby-layers.json",
    "**/mutant.yml", "**/mutant.yaml",
    "**/jest.config.*", "**/babel.config.*", "**/.babelrc*", "**/next.config.*", "**/playwright.config.*",
    "**/*.csproj", "**/*.sln", "**/*.props", "**/*.targets", "**/NuGet.config", "**/nuget.config", "**/dotnet-tools.json",
    "**/Cargo.toml", "Cargo.lock", "**/clippy.toml", "**/.clippy.toml", "**/rustfmt.toml", "**/.rustfmt.toml",
    "rust-toolchain", "rust-toolchain.toml", "**/.cargo/config.toml", "**/.cargo/mutants.toml", ".rust-layers.json",
    "**/.editorconfig", "**/.globalconfig", "**/*.runsettings", "**/stryker-config.*", ".dotnet-layers.json",
    "**/pom.xml", "mvnw", "mvnw.cmd", "**/.mvn/**", "**/lombok.config", "**/pmd-ruleset.xml", ".java-layers.json",

    ".claude/settings.json", "CLAUDE.md", "AGENTS.md", "GEMINI.md", ".agents/hooks.json",
    ".grok/hooks/**", ".grok/config.toml",
    ".cursor/hooks.json", ".cursor/hooks/**", ".cursor/cli.json",
]
SPEC = ["features/**", "qa/**", "tasks/**", "perf/**", "PERFORMANCE.md", "guidance/**"]
CSPROJ_ADDITION = re.compile(r'^\+\s*<(PackageReference|InternalsVisibleTo) Include="[^"]+"(?: Version="[^"]+")? />\s*$')
CSPROJ_WRAPPER = re.compile(r"^\+\s*(<ItemGroup>|</ItemGroup>)?\s*$")
ALLOWED = {
    "specifier": ["features/**", "qa/**"],
    "architect": ["pyproject.toml", ".importlinter", "**/.dependency-cruiser.cjs", "mix.exs", ".ruby-layers.json", ".dotnet-layers.json", ".rust-layers.json", ".java-layers.json"],
}


def frozen_paths(config: Config, role: str, paths: list[str]) -> list[str]:
    frozen = config.get("freeze", "paths", GATE_CONFIG) + config.get("freeze", "spec", SPEC)
    allowed = {**ALLOWED, **config.get("freeze", "allow", {})}.get(role, [])
    return [path for path in paths if matches_any(path, frozen) and not matches_any(path, allowed)]


def tolerated(path: str, diff: str) -> bool:
    if not matches(path, "**/*.csproj"):
        return False
    changes = [line for line in diff.splitlines() if line[:1] in ("+", "-") and not line.startswith(("+++", "---"))]
    additions = [line for line in changes if CSPROJ_ADDITION.match(line)]
    return bool(additions) and all(CSPROJ_ADDITION.match(line) or CSPROJ_WRAPPER.match(line) for line in changes)


def matches_any(path: str, patterns: list[str]) -> bool:
    return any(matches(path, pattern) for pattern in patterns)


def matches(path: str, pattern: str) -> bool:
    if pattern.startswith("**/"):
        rest = pattern[3:]
        return fnmatch(path, rest) or fnmatch(path, "*/" + rest)
    if pattern.endswith("/**"):
        return path.startswith(pattern[:-3] + "/")
    return fnmatch(path, pattern)
