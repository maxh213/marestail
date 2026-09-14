from dataclasses import dataclass, field
from pathlib import Path

from marestail.changes import base_exists, changed_files, changed_lines, file_lines
from marestail.config import Config


@dataclass(frozen=True)
class MutationScope:
    mode: str
    files: list[str] | None = None
    note: str = ""


@dataclass
class Context:
    config: Config
    scope_changed: bool = False
    changed: set[str] = field(default_factory=set)
    focus: set[str] = field(default_factory=set)
    changed_lines_map: dict[str, set[int]] = field(default_factory=dict)

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
        return "changed" if self.scoped else "all"

    def python(self, key: str, default=None):
        return self.config.get("python", key, default)

    def ts(self, key: str, default=None):
        return self.config.get("ts", key, default)

    def elixir(self, key: str, default=None):
        return self.config.get("elixir", key, default)

    def ruby(self, key: str, default=None):
        return self.config.get("ruby", key, default)

    def python_bin(self, tool: str) -> str:
        venv = self.root / self.python("venv", ".venv")
        return str(venv / "bin" / tool)

    def python_root(self) -> Path:
        return self.root / self.python("root", ".")

    def ts_root(self) -> Path:
        return self.root / self.ts("root", ".")

    def elixir_root(self) -> Path:
        return self.root / self.elixir("root", ".")

    def ruby_root(self) -> Path:
        return self.root / self.ruby("root", ".")

    def dotnet(self, key: str, default=None):
        return self.config.get("dotnet", key, default)

    def dotnet_root(self) -> Path:
        return self.root / self.dotnet("root", ".")

    def erlang(self, key: str, default=None):
        return self.config.get("erlang", key, default)

    def erlang_root(self) -> Path:
        return self.root / self.erlang("root", ".")

    def rust(self, key: str, default=None):
        return self.config.get("rust", key, default)

    def rust_root(self) -> Path:
        return self.root / self.rust("root", ".")

    def java(self, key: str, default=None):
        return self.config.get("java", key, default)

    def java_root(self) -> Path:
        return self.root / self.java("root", ".")

    def changed_under(self, folder: Path, suffixes: tuple[str, ...]) -> list[str]:
        relative = folder.relative_to(self.root)
        paths = {
            path
            for path in self.changed
            if path.endswith(suffixes) and Path(path).is_relative_to(relative)
        }
        paths.update(self.focus_under(relative, suffixes))
        return sorted(paths)

    def focus_under(self, relative: Path, suffixes: tuple[str, ...]) -> set[str]:
        found: set[str] = set()
        for entry in self.focus:
            target = self.root / entry
            if target.is_file() and entry.endswith(suffixes) and Path(entry).is_relative_to(relative):
                found.add(entry)
            elif target.is_dir():
                found.update(
                    path.relative_to(self.root).as_posix()
                    for path in target.rglob("*")
                    if path.is_file()
                    and path.name.endswith(suffixes)
                    and path.relative_to(self.root).is_relative_to(relative)
                )
        return found

    def in_focus(self, path: str) -> bool:
        return any(path == entry or Path(path).is_relative_to(entry) for entry in self.focus)

    def in_scope(self, path: str) -> bool:
        if not self.scoped:
            return True
        return path in self.changed or self.in_focus(path)

    def changed_line_set(self, path: str) -> set[int]:
        return self.changed_lines_map.get(path, set())

    def gated_lines(self, path: str) -> set[int] | None:
        if not self.scoped:
            return None
        if self.in_focus(path):
            return file_lines(self.root / path) or set()
        return self.changed_lines_map.get(path)

    def scope_summary(self) -> str:
        if not self.scoped:
            return "all"
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
        setting = self.config.get(lang_key, "mutation_scope", "changed")
        if setting not in ("changed", "all"):
            return MutationScope("error", note=f"[{lang_key}] mutation_scope must be \"changed\" or \"all\", got {setting!r}")
        if self.scoped:
            files = self.changed_under(root, suffixes)
            return MutationScope("scoped", files) if files else MutationScope("skip", [])
        if setting == "all":
            return MutationScope("full")
        base = self.config.get("git", "base", "origin/master")
        if not base_exists(self.root, base):
            return MutationScope("full", note=f"(no base {base}; full run)")
        relative = root.relative_to(self.root)
        files = sorted(
            path
            for path in changed_files(self.root, base)
            if path.endswith(suffixes) and Path(path).is_relative_to(relative)
        )
        return MutationScope("scoped", files) if files else MutationScope("skip", [])


def build(config: Config, scope_changed: bool, focus: set[str] | None = None) -> Context:
    base = config.get("git", "base", "origin/master")
    focused = focus or set()
    scoped = scope_changed or bool(focused)
    changed = changed_files(config.root, base) if scoped else set()
    lines = changed_lines(config.root, base) if scoped else {}
    return Context(config=config, scope_changed=scoped, changed=changed, focus=focused, changed_lines_map=lines)
