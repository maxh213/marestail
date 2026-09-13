from pathlib import Path


def files(root: Path) -> list[Path]:
    folder = root / "guidance"
    if not folder.is_dir():
        return []
    return sorted(folder.glob("*.md"))
