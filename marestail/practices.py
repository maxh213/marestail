from pathlib import Path

GUIDANCE = "guidance"
PATTERNS = "patterns"


def files(root: Path) -> list[Path]:
    return markdown_files(root / GUIDANCE)


def pattern_files(root: Path) -> list[Path]:
    return markdown_files(root / GUIDANCE / PATTERNS)


def markdown_files(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(folder.glob("*.md"))
