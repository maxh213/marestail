import shutil
from pathlib import Path

from marestail.shell import ensure_dir

_TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
_GUIDANCE = "guidance"
_PATTERNS = "patterns"


def copy(target: Path) -> None:
    folder = target / _GUIDANCE
    folder.mkdir(exist_ok=True)
    for language in _languages(target):
        _copy_language(folder, language)


def uses_csharp(target: Path) -> bool:
    return _anywhere(target, "*.csproj")


def _languages(target: Path) -> tuple[str, ...]:
    return ("ts", *(name for name, present in _markers(target) if present))


def _markers(target: Path) -> tuple[tuple[str, bool], ...]:
    return (
        ("cs", uses_csharp(target)),
        ("rb", _uses_ruby(target)),
        ("ex", _uses_elixir(target)),
        ("er", _uses_erlang(target)),
        ("go", _uses_go(target)),
    )


def _copy_language(folder: Path, language: str) -> None:
    name = f"{language}.md"
    _copy(_template(name), folder / name)
    _copy(_template(_PATTERNS, name), folder / _PATTERNS / name)


def _template(*parts: str) -> Path:
    return _TEMPLATES.joinpath(_GUIDANCE, *parts)


def _copy(source: Path, destination: Path) -> None:
    if destination.exists():
        return
    ensure_dir(destination.parent)
    shutil.copy(source, destination)


def _uses_ruby(target: Path) -> bool:
    return _at_root(target, "Gemfile")


def _uses_elixir(target: Path) -> bool:
    return _at_root(target, "mix.exs")


def _uses_go(target: Path) -> bool:
    return _at_root(target, "go.mod")


def _uses_erlang(target: Path) -> bool:
    return _anywhere(target, "*.erl")


def _anywhere(target: Path, pattern: str) -> bool:
    return next(target.rglob(pattern), None) is not None


def _at_root(target: Path, name: str) -> bool:
    return (target / name).is_file()
