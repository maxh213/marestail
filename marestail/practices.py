from pathlib import Path


def files(root: Path) -> list[Path]:
    return markdown(root / "guidance")


def pattern_files(root: Path) -> list[Path]:
    return markdown(root / "guidance" / "patterns")


def markdown(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(folder.glob("*.md"))
