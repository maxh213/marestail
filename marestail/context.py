from dataclasses import dataclass, field
from pathlib import Path

from marestail.changes import changed_files
from marestail.config import Config


@dataclass
class Context:
    config: Config
    scope_changed: bool = False
    changed: set[str] = field(default_factory=set)

    @property
    def root(self) -> Path:
        return self.config.root

    @property
    def work(self) -> Path:
        return self.config.work

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


    def changed_under(self, folder: Path, suffixes: tuple[str, ...]) -> list[str]:
        relative = folder.relative_to(self.root)
        return sorted(
            path
            for path in self.changed
            if path.endswith(suffixes) and Path(path).is_relative_to(relative)
        )


def build(config: Config, scope_changed: bool) -> Context:
    base = config.get("git", "base", "origin/master")
    changed = changed_files(config.root, base) if scope_changed else set()
    return Context(config=config, scope_changed=scope_changed, changed=changed)
