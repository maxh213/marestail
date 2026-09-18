from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from marestail.changes import base_exists, changed_files, changed_lines, file_lines
from marestail.config import Config

CHANGED = "changed"
DEFAULT_BASE = "origin/master"


@dataclass(frozen=True)
class MutationScope:
    mode: str
    files: list[str] | None = None
    note: str = ""


def matches(path: str, relative: Path, suffixes: tuple[str, ...]) -> bool:
    return path.endswith(suffixes) and Path(path).is_relative_to(relative)


def scoped_files(files: list[str]) -> MutationScope:
    return MutationScope("scoped", files) if files else MutationScope("skip", [])


@dataclass
class Context:
    config: Config
    scope_changed: bool = False
    changed: set[str] = field(default_factory=set)
    focus: set[str] = field(default_factory=set)
    changed_lines_map: dict[str, set[int]] = field(default_factory=dict)
    hard: bool = False

    @property
    def root(self) -> Path:
        return self.config.root

    @property
    def work(self) -> Path:
        return self.config.work

    @property
    def scoped(self) -> bool:
        return self.scope_changed or bool(self.focus)

    @property
    def scope_name(self) -> str:
        if self.hard:
            return "hard"
        return CHANGED if self.scoped else "all"

    def section_root(self, section: str) -> Path:
        return Path(self.root, self.config.get(section, "root", "."))

    def python(self, key: str, default: Any = None) -> Any:
        return self.config.get("python", key, default)

    def ts(self, key: str, default: Any = None) -> Any:
        return self.config.get("ts", key, default)

    def elixir(self, key: str, default: Any = None) -> Any:
        return self.config.get("elixir", key, default)

    def ruby(self, key: str, default: Any = None) -> Any:
        return self.config.get("ruby", key, default)

    def python_bin(self, tool: str) -> str:
        venv = self.root / self.python("venv", ".venv")
        return str(venv / "bin" / tool)

    def python_root(self) -> Path:
        return self.section_root("python")

    def ts_root(self) -> Path:
        return self.section_root("ts")

    def elixir_root(self) -> Path:
        return self.section_root("elixir")

    def ruby_root(self) -> Path:
        return self.section_root("ruby")

    def dotnet(self, key: str, default: Any = None) -> Any:
        return self.config.get("dotnet", key, default)

    def dotnet_root(self) -> Path:
        return self.section_root("dotnet")

    def erlang(self, key: str, default: Any = None) -> Any:
        return self.config.get("erlang", key, default)

    def erlang_root(self) -> Path:
        return self.section_root("erlang")

    def rust(self, key: str, default: Any = None) -> Any:
        return self.config.get("rust", key, default)

    def rust_root(self) -> Path:
        return self.section_root("rust")

    def java(self, key: str, default: Any = None) -> Any:
        return self.config.get("java", key, default)

    def java_root(self) -> Path:
        return self.section_root("java")

    def changed_under(self, folder: Path, suffixes: tuple[str, ...]) -> list[str]:
        relative = folder.relative_to(self.root)
        paths = {path for path in self.changed if matches(path, relative, suffixes)}
        paths.update(self.focus_under(relative, suffixes))
        return sorted(paths)

    def focus_under(self, relative: Path, suffixes: tuple[str, ...]) -> set[str]:
        found: set[str] = set()
        for entry in self.focus:
            found.update(self.focus_entry(entry, relative, suffixes))
        return found

    def focus_entry(self, entry: str, relative: Path, suffixes: tuple[str, ...]) -> set[str]:
        target = self.root / entry
        if target.is_file():
            return {entry} if matches(entry, relative, suffixes) else set()
        if target.is_dir():
            return self.files_below(target, relative, suffixes)
        return set()

    def files_below(self, target: Path, relative: Path, suffixes: tuple[str, ...]) -> set[str]:
        return {path for path in self.files_in(target) if matches(path, relative, suffixes)}

    def files_in(self, target: Path) -> list[str]:
        return [path.relative_to(self.root).as_posix() for path in target.rglob("*") if path.is_file()]

    def in_focus(self, path: str) -> bool:
        return any(path == entry or Path(path).is_relative_to(entry) for entry in self.focus)

    def in_scope(self, path: str) -> bool:
        if not self.scoped:
            return True
        return path in self.changed or self.in_focus(path)

    def gated_lines(self, path: str) -> set[int] | None:
        if not self.scoped:
            return None
        if self.in_focus(path):
            return file_lines(self.root / path) or set()
        return self.changed_lines_map.get(path)

    def scope_summary(self) -> str:
        if not self.scoped:
            return "all"
        if self.hard:
            return "hard: " + ", ".join(sorted(self.focus))
        return self.changed_summary()

    def changed_summary(self) -> str:
        total = sum(len(lines) for lines in self.changed_lines_map.values())
        summary = f"changed ({len(self.changed)} files, {total} lines)"
        if self.focus:
            summary += " + focus: " + ", ".join(sorted(self.focus))
        return summary

    def global_note(self, summary: str) -> str:
        if not self.scoped:
            return summary
        return f"{summary} (global gate — scope: {self.scope_name})"

    def mutation_files(self, lang_key: str, root: Path, suffixes: tuple[str, ...]) -> MutationScope:
        setting = self.config.get(lang_key, "mutation_scope", CHANGED)
        if setting not in (CHANGED, "all"):
            return MutationScope("error", note=f'[{lang_key}] mutation_scope must be "changed" or "all", got {setting!r}')
        if self.scoped:
            return scoped_files(self.changed_under(root, suffixes))
        if setting == "all":
            return MutationScope("full")
        return self.branch_mutation_files(root, suffixes)

    def branch_mutation_files(self, root: Path, suffixes: tuple[str, ...]) -> MutationScope:
        base = self.config.get("git", "base", DEFAULT_BASE)
        if not base_exists(self.root, base):
            return MutationScope("full", note=f"(no base {base}; full run)")
        relative = root.relative_to(self.root)
        return scoped_files(sorted(path for path in changed_files(self.root, base) if matches(path, relative, suffixes)))


def hard_context(config: Config, focused: set[str]) -> Context:
    if not focused:
        raise SystemExit("--scope hard needs at least one focus path: pass --focus or set [focus] paths in marestail.toml")
    return Context(config=config, scope_changed=True, focus=focused, hard=True)


def diff_context(config: Config, scoped: bool, focused: set[str]) -> Context:
    if not scoped:
        return Context(config=config, focus=focused)
    base = config.get("git", "base", DEFAULT_BASE)
    changed = changed_files(config.root, base)
    lines = changed_lines(config.root, base)
    return Context(config=config, scope_changed=True, changed=changed, focus=focused, changed_lines_map=lines)


def build(config: Config, scope_changed: bool, focus: set[str] | None = None, hard: bool = False) -> Context:
    focused = focus or set()
    if hard:
        return hard_context(config, focused)
    return diff_context(config, scope_changed or bool(focused), focused)
