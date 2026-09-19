import curses
from typing import Any, ClassVar

from marestail.tui import theme


class FakeCurses:
    A_BOLD = 1
    A_NORMAL = 0
    A_DIM = 2
    A_REVERSE = 4
    COLOR_BLACK = 0
    COLOR_YELLOW = 3
    COLOR_GREEN = 2
    COLOR_MAGENTA = 5
    COLOR_WHITE = 7
    COLOR_RED = 1
    COLORS = 8
    error = curses.error
    pairs: ClassVar[dict[int, tuple[int, int]]] = {}

    @staticmethod
    def color_pair(index: int) -> int:
        return index * 10

    @staticmethod
    def init_pair(index: int, fg: int, bg: int) -> None:
        FakeCurses.pairs[index] = (fg, bg)

    @staticmethod
    def start_color() -> None:
        return None

    @staticmethod
    def has_colors() -> bool:
        return True

    @staticmethod
    def use_default_colors() -> None:
        return None


def test_mono_and_glyphs() -> None:
    mono = theme.mono_theme()
    assert mono.colors is False
    assert theme.step_glyph("running", None) == theme.GLYPH_RUNNING
    assert theme.step_glyph("done", "BOUNCE") == theme.GLYPH_BOUNCED
    assert theme.step_glyph("done", "PASS") == theme.GLYPH_PASSED
    assert theme.step_glyph("done", None) == theme.GLYPH_DONE
    assert theme.step_attr(mono, "running", None) == mono.worker
    assert theme.step_attr(mono, "done", "BOUNCE") == mono.bounced
    assert theme.step_attr(mono, "done", "PASS") == mono.passed
    assert theme.step_attr(mono, "done", None) == mono.done
    assert theme.role_attr(mono, "critic") == mono.judge
    assert theme.role_attr(mono, "coder") == mono.worker
    assert theme.vine(0) == ""
    assert len(theme.vine(5)) == 5


def test_color_theme(monkeypatch: Any) -> None:
    monkeypatch.setattr(theme, "curses", FakeCurses)
    FakeCurses.COLORS = 8
    colored = theme.color_theme()
    assert colored.colors is True
    FakeCurses.COLORS = 256
    rich = theme.color_theme()
    assert rich.colors is True
    assert theme.color_palette(True)[0] == 178
    assert theme.color_palette(False)[0] == FakeCurses.COLOR_YELLOW


def test_init_theme_paths(monkeypatch: Any) -> None:
    monkeypatch.setattr(theme, "curses", FakeCurses)
    FakeCurses.has_colors = staticmethod(lambda: False)
    assert theme.init_theme().colors is False
    FakeCurses.has_colors = staticmethod(lambda: True)

    def boom() -> theme.Theme:
        raise curses.error("no")

    monkeypatch.setattr(theme, "color_theme", boom)
    assert theme.init_theme().colors is False
    monkeypatch.setattr(theme, "color_theme", lambda: theme.mono_theme())
    FakeCurses.has_colors = staticmethod(lambda: True)
    assert theme.init_theme().colors is False


def test_default_bg(monkeypatch: Any) -> None:
    monkeypatch.setattr(theme, "curses", FakeCurses)
    assert theme.default_bg() == -1

    def boom() -> None:
        raise curses.error("no")

    FakeCurses.use_default_colors = staticmethod(boom)
    assert theme.default_bg() == FakeCurses.COLOR_BLACK
