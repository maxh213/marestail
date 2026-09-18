from pathlib import Path

from marestail import practices


def test_no_guidance_folder_means_no_files(tmp_path: Path) -> None:
    assert practices.files(tmp_path) == []


def test_guidance_file_instead_of_folder_means_no_files(tmp_path: Path) -> None:
    (tmp_path / "guidance").write_text("x")
    assert practices.files(tmp_path) == []


def test_lists_markdown_guidance_sorted(tmp_path: Path) -> None:
    folder = tmp_path / "guidance"
    folder.mkdir()
    for name in ("ts.md", "cs.md", "notes.txt"):
        (folder / name).write_text("x")
    assert practices.files(tmp_path) == [folder / "cs.md", folder / "ts.md"]
