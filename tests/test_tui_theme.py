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

    colors_on = True
    default_ok = True

    @staticmethod
    def has_colors() -> bool:
        return FakeCurses.colors_on

    @staticmethod
    def use_default_colors() -> None:
        if not FakeCurses.default_ok:
            raise curses.error("no")


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
    FakeCurses.colors_on = True
    FakeCurses.default_ok = True
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
    FakeCurses.default_ok = True
    FakeCurses.colors_on = False
    assert theme.init_theme().colors is False
    FakeCurses.colors_on = True

    def boom() -> theme.Theme:
        raise curses.error("no")

    monkeypatch.setattr(theme, "color_theme", boom)
    assert theme.init_theme().colors is False
    monkeypatch.setattr(theme, "color_theme", lambda: theme.mono_theme())
    FakeCurses.colors_on = True
    assert theme.init_theme().colors is False


def test_default_bg(monkeypatch: Any) -> None:
    monkeypatch.setattr(theme, "curses", FakeCurses)
    FakeCurses.default_ok = True
    assert theme.default_bg() == -1
    FakeCurses.default_ok = False
    assert theme.default_bg() == FakeCurses.COLOR_BLACK


def test_theme_dispatch_helpers(monkeypatch: Any) -> None:
    monkeypatch.setattr(theme, "curses", FakeCurses)
    mono = theme.mono_theme()
    assert theme.running_glyph("PASS") == theme.GLYPH_RUNNING
    assert theme.verdict_glyph("BOUNCE") == theme.GLYPH_BOUNCED
    assert theme.verdict_glyph("PASS") == theme.GLYPH_PASSED
    assert theme.verdict_glyph(None) == theme.GLYPH_DONE
    assert theme.running_attr(mono, None) == mono.worker
    assert theme.verdict_attr(mono, "BOUNCE") == mono.bounced
    assert theme.verdict_attr(mono, "PASS") == mono.passed
    assert theme.verdict_attr(mono, None) == mono.done
    assert theme.blank_vine(8) == ""
    assert theme.full_vine(5) == theme.vine(5)
    assert theme.rich_palette() == theme.RICH_PALETTE
    assert theme.basic_palette()[0] == FakeCurses.COLOR_YELLOW
    FakeCurses.default_ok = True
    assert theme.try_color().colors is True

    def boom() -> theme.Theme:
        raise curses.error("no")

    monkeypatch.setattr(theme, "color_theme", boom)
    assert theme.try_color().colors is False
