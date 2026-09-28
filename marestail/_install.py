import json
import os
import shutil
import time
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from marestail.shell import ensure_dir, run

PERFORMANCE = "PERFORMANCE.md"
CONFIG = "marestail.toml"
SONAR = "sonar-project.properties"
DEPCRUISE = ".dependency-cruiser.cjs"
TSCONFIG = "tsconfig.json"
VITEST_CONFIG = "vitest.config.ts"
TOOLING = ".marestail/tooling"
HYPER_START = "# marestail (install --scope hyper)"
HYPER_END = "# end marestail"
CLAUDE_LOCAL = ".claude/settings.local.json"
AGY_HOOKS = ".agents/hooks.json"
GROK_HOOKS = ".grok/hooks/marestail-gate.json"
CURSOR_HOOKS = ".cursor/hooks.json"
TASKS_README = "tasks/README.md"
TS_GUIDANCE = "guidance/ts.md"
CS_GUIDANCE = "guidance/cs.md"
HYPER_EXCLUDES = [
    CONFIG,
    SONAR,
    "guidance/",
    "tasks/",
    "features/",
    "qa/",
    "perf/",
    PERFORMANCE,
    CLAUDE_LOCAL,
    AGY_HOOKS,
    GROK_HOOKS,
    CURSOR_HOOKS,
]
TRACK_CANDIDATES = [
    ".gitignore",
    "AGENTS.md",
    "CLAUDE.md",
    CONFIG,
    SONAR,
    TASKS_README,
    PERFORMANCE,
    TS_GUIDANCE,
    CS_GUIDANCE,
    CLAUDE_LOCAL,
    AGY_HOOKS,
    GROK_HOOKS,
    CURSOR_HOOKS,
]
NO_TOOLING = f'marestail.toml has no [ts] tooling; add tooling = "{TOOLING}" under [ts] so the gates use it'
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

__all__ = [
    "DECIDED",
    "GATE_MARKER",
    "GITIGNORE_GENERATED_LINES",
    "GITIGNORE_LINES",
    "TEMPLATES",
    "TRUSTED",
    "VERSION",
    "VERSION_DEFAULT",
    "apply_hooks",
    "done_message",
    "extend_gitignore",
    "generated_ignore",
    "install_hyper",
    "trust_grok_folder",
    "write_tree",
]


def write_tree(target: Path, hard: bool) -> None:
    copy_templates(target)
    write_agent_docs(target, hard)


def copy_templates(target: Path) -> None:
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


def apply_hooks(target: Path) -> None:
    merge_hook(target / ".claude" / "settings.json")
    merge_agy_hook(target / ".agents" / "hooks.json")
    merge_grok_hook(target / ".grok" / HOOKS / "marestail-gate.json")
    merge_cursor_hook(target / ".cursor" / "hooks.json")


def write_agent_docs(target: Path, hard: bool) -> None:
    if not hard:
        append_instructions(target / "CLAUDE.md")
        append_instructions(target / "AGENTS.md")


def generated_ignore(gitignore_generated: bool, hard: bool) -> list[str]:
    return GITIGNORE_GENERATED_LINES if gitignore_generated or hard else []


def done_message(target: Path, hard: bool) -> str:
    alone = "; left CLAUDE.md and AGENTS.md alone" if hard else ""
    return f"installed into {target}{alone}; edit marestail.toml and sonar-project.properties"


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
    if key not in data:
        return EMPTY_MAP
    found = data[key]
    return found if isinstance(found, dict) else EMPTY_MAP


def listed(data: dict[str, Any], key: str) -> list[Any]:
    if key not in data:
        return EMPTY_LIST
    found = data[key]
    return found if isinstance(found, list) else EMPTY_LIST


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

    def has_vitest(self) -> bool:
        return self.jest or self.has(VITEST_KINDS)

    def each_source(self, patterns: list[str]) -> str:
        return json_list([pattern.replace(SOURCE, source) for pattern in patterns for source in self.sources])


def install_hyper(target: Path) -> int:
    exclude = exclude_file(target)
    if exclude is None:
        print(f"marestail install --scope hyper needs a git repository: {target}")
        return 1
    tracked = tracked_paths(target)
    write_hyper_tree(target, tracked)
    skipped = apply_local_hooks(target, tracked)
    ts = ts_section(target)
    write_tooling(Tooling(target, ts))
    extend_exclude(target / exclude)
    trust_grok_folder(target)
    print_notes(tracked, skipped, ts)
    return npm_outcome(target, exclude)


def exclude_file(target: Path) -> str | None:
    code, output = run(["git", "rev-parse", "--git-path", "info/exclude"], cwd=target, timeout=60)
    return output.strip() if code == 0 else None


def tracked_paths(target: Path) -> set[str]:
    _, output = run(["git", "ls-files", "-z", "--", *TRACK_CANDIDATES], cwd=target, timeout=60)
    return {path for path in output.split("\0") if path}


def hyper_config() -> str:
    return (TEMPLATES / CONFIG).read_text().replace("\n[ts]\n", f'\n[ts]\ntooling = "{TOOLING}"\n', 1)


def hyper_files(target: Path) -> dict[str, str]:
    files = {
        CONFIG: hyper_config(),
        TASKS_README: (TEMPLATES / "tasks-README.md").read_text(),
        PERFORMANCE: (TEMPLATES / PERFORMANCE).read_text(),
        TS_GUIDANCE: (TEMPLATES / TS_GUIDANCE).read_text(),
    }
    if uses_csharp(target):
        files[CS_GUIDANCE] = (TEMPLATES / CS_GUIDANCE).read_text()
    return files


def write_hyper_tree(target: Path, tracked: set[str]) -> None:
    for name, text in hyper_files(target).items():
        if name not in tracked:
            write_if_missing(target / name, text)


def write_if_missing(path: Path, text: str) -> None:
    if not path.exists():
        ensure_dir(path.parent)
        path.write_text(text)


Hook = tuple[str, str, Callable[[Path], None]]


def local_hooks() -> list[Hook]:
    return [
        ("agy", AGY_HOOKS, merge_agy_hook),
        ("claude", CLAUDE_LOCAL, merge_hook),
        ("cursor", CURSOR_HOOKS, merge_cursor_hook),
        ("grok", GROK_HOOKS, merge_grok_hook),
    ]


def apply_local_hooks(target: Path, tracked: set[str]) -> list[str]:
    return [line for hook in local_hooks() for line in local_hook(target, hook, tracked)]


def local_hook(target: Path, hook: Hook, tracked: set[str]) -> list[str]:
    backend, name, merge = hook
    if name in tracked:
        return [f"no Stop hook for {backend}: {name} is tracked"]
    merge(target / name)
    return []


def ts_section(target: Path) -> dict[str, Any]:
    with (target / CONFIG).open("rb") as handle:
        ts = tomllib.load(handle).get("ts")
    return ts if isinstance(ts, dict) else {}


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
        (DEPCRUISE, (tooling.ts_root / str(tooling.ts.get("depcruise_config", DEPCRUISE))).exists(), depcruise_text),
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


def extend_exclude(path: Path) -> None:
    existing = path.read_text() if path.exists() else ""
    if HYPER_START in existing.splitlines():
        return
    ensure_dir(path.parent)
    block = [HYPER_START, *GITIGNORE_LINES, *HYPER_EXCLUDES, HYPER_END]
    path.write_text(ended(existing) + "\n".join(block) + "\n")


def ended(text: str) -> str:
    return text if not text or text.endswith("\n") else text + "\n"


def print_notes(tracked: set[str], skipped: list[str], ts: dict[str, Any]) -> None:
    alone = [f"left tracked files alone: {', '.join(sorted(tracked))}"] if tracked else []
    missing = [] if "tooling" in ts else [NO_TOOLING]
    for line in [*alone, *skipped, *missing]:
        print(line)


def npm_outcome(target: Path, exclude: str) -> int:
    code, output = run(["npm", "install", "--prefix", TOOLING], cwd=target, timeout=1800)
    (target / TOOLING / "npm.log").write_text(output)
    if code != 0:
        print(f"npm install --prefix {TOOLING} failed (exit {code}); everything else is installed, see {TOOLING}/npm.log")
        return 1
    print(f"installed into {target} with --scope hyper; nothing to commit, see {exclude}")
    return 0
