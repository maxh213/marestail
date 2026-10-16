from pathlib import Path

_GUIDANCE = "guidance"
_PATTERNS = "patterns"


def files(root: Path) -> list[Path]:
    return _markdown(root / _GUIDANCE)


def pattern_files(root: Path) -> list[Path]:
    return _markdown(root / _GUIDANCE / _PATTERNS)


def _markdown(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(folder.glob("*.md"))
