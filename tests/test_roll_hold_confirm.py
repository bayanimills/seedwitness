"""A roll must not be recorded unless the finger stayed put.

A 12-word ceremony is 50 rolls, and a wrong value silently corrupts the
entropy: nothing downstream can detect it, the seed is simply not the one the
dice produced. So the value buttons commit on a held press, not a brush, and
a press withdrawn part-way must leave the session untouched.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "device"))
sys.path.insert(0, str(ROOT / "sim"))

from mock_canvas import MockCanvas  # noqa: E402

from seedwitness import entropy as ent  # noqa: E402
from seedwitness.ui import screens as S  # noqa: E402
from seedwitness.ui.app import App  # noqa: E402


class HeldCanvas(MockCanvas):
    """Reports the finger as lifting after `hold_for` polls."""

    def __init__(self, hold_for=99):
        super().__init__()
        self.hold_for = hold_for
        self.polls = 0

    def touch_active(self):
        self.polls += 1
        return self.polls <= self.hold_for


def _screen():
    return S.RollEntryScreen(ent.RollSession(ent.SOURCES["D6"], 12))


def _centre(b):
    return b.x + b.w // 2, b.y + b.h // 2


def test_held_press_records_the_roll():
    screen = _screen()
    app = App()
    canvas = HeldCanvas()          # never lifts
    x, y = _centre(screen.buttons[2])   # the "3" button
    screen.handle_tap(app, canvas, x, y)
    assert screen.session.rolls == [3]


def test_press_withdrawn_early_records_nothing():
    screen = _screen()
    app = App()
    canvas = HeldCanvas(hold_for=2)     # lifts after two polls
    x, y = _centre(screen.buttons[2])
    screen.handle_tap(app, canvas, x, y)
    assert screen.session.rolls == []


def test_cancelling_leaves_the_button_usable():
    """A withdrawn press must not wedge anything -- holding it properly next
    time still counts."""
    screen = _screen()
    app = App()
    x, y = _centre(screen.buttons[0])
    screen.handle_tap(app, HeldCanvas(hold_for=1), x, y)
    assert screen.session.rolls == []
    screen.handle_tap(app, HeldCanvas(), x, y)
    assert screen.session.rolls == [1]


def test_tapping_outside_any_button_does_nothing():
    screen = _screen()
    app = App()
    canvas = HeldCanvas()
    screen.handle_tap(app, canvas, 2, S.RollEntryScreen.GRID_TOP - 4)
    assert screen.session.rolls == []


def test_sweep_stays_inside_the_button():
    """The dial is drawn with raw fill_rects rather than through a clipping
    helper, so an off-by-one would paint over a neighbouring button."""
    screen = _screen()
    b = screen.buttons[0]
    rects = []

    class RecordingCanvas(MockCanvas):
        def fill_rect(self, x, y, w, h, color):
            rects.append((x, y, w, h))
            return super().fill_rect(x, y, w, h, color)

    canvas = RecordingCanvas()
    for i in range(1, S.RollEntryScreen.SWEEP_STEPS + 1):
        screen._draw_sweep(canvas, b, i / S.RollEntryScreen.SWEEP_STEPS)
    for x, y, w, h in rects:
        assert x >= b.x and y >= b.y
        assert x + w <= b.x + b.w, "sweep runs past the right edge"
        assert y + h <= b.y + b.h, "sweep runs past the bottom edge"


def test_full_sweep_covers_the_whole_perimeter():
    """At 100% the dial must actually close, not stop short."""
    screen = _screen()
    b = screen.buttons[0]
    painted = []

    class RecordingCanvas(MockCanvas):
        def fill_rect(self, x, y, w, h, color):
            painted.append((x, y, w, h))
            return super().fill_rect(x, y, w, h, color)

    screen._draw_sweep(RecordingCanvas(), b, 1.0)
    t = S.RollEntryScreen.SWEEP_T
    # one run along each of the four edges
    assert any(w == b.w and y == b.y for x, y, w, h in painted), "top edge missing"
    assert any(h == b.h and x == b.x + b.w - t for x, y, w, h in painted), "right edge missing"
    assert any(w == b.w and y == b.y + b.h - t for x, y, w, h in painted), "bottom edge missing"
    assert any(h == b.h and x == b.x for x, y, w, h in painted), "left edge missing"


# --- menus confirm on a hold too, but a brief one -------------------------

def test_menu_press_held_navigates():
    app = App()
    canvas = HeldCanvas()
    home = app.screen
    b = home.buttons[0]                      # "Generate New Seed"
    home.handle_tap(app, canvas, *_centre(b))
    assert type(app.screen).__name__ == "MethodSelectScreen"


def test_menu_press_withdrawn_early_does_not_navigate():
    app = App()
    canvas = HeldCanvas(hold_for=1)          # lifts almost immediately
    home = app.screen
    before = type(app.screen).__name__
    home.handle_tap(app, canvas, *_centre(home.buttons[0]))
    assert type(app.screen).__name__ == before


def test_menu_dwell_is_shorter_than_a_roll():
    """A wrong menu tap is a wrong turn you back out of; a wrong roll is
    unrecoverable. The dwells should reflect that, not be uniform."""
    assert 0 < S.MenuScreen.SWEEP_MS < S.RollEntryScreen.SWEEP_MS


def test_typing_dwell_is_the_shortest_in_the_app():
    """Keys confirm on a hold too -- a 34px key is narrower than the finger
    pressing it, so a mis-hit is easy and invisible. But a word is up to 8
    presses and a seed is 12 or 24 words, so this dwell must stay the
    briefest anywhere in the app."""
    assert 0 < S.WordEntryScreen.SWEEP_MS < S.MenuScreen.SWEEP_MS
    assert S.WordEntryScreen.SWEEP_MS < S.RollEntryScreen.SWEEP_MS


def test_letter_press_shows_a_magnified_preview():
    """The feedback must land where the hand is NOT: a fingertip covers a
    34px key completely, so anything drawn on the key is unreadable."""
    screen = S.WordEntryScreen(12)
    key = next(b for b in screen.buttons if b.label == "a")
    drawn = []

    class RecordingCanvas(MockCanvas):
        def text(self, x, y, s, color, bg=None, scale=1, font=None):
            drawn.append((y, s, scale, font))
            return super().text(x, y, s, color, bg=bg, scale=scale, font=font)

    screen._press_preview(RecordingCanvas(), key)
    # "magnified" = drawn in a face at least 3x the base glyph height
    big = [d for d in drawn
           if (d[3].height if d[3] else 8 * d[2]) >= 24]
    assert big, "no magnified preview drawn"
    assert big[0][1] == "a"
    # and it must be above the keyboard, not on the key
    assert big[0][0] < key.y, "preview drawn under the finger"


def test_wide_action_keys_get_no_preview():
    """Back and Cancel are wide and self-evident; a magnified 'Cancel' would
    just be noise."""
    screen = S.WordEntryScreen(12)
    cancel = next(b for b in screen.buttons if b.label == "Cancel")
    drawn = []

    class RecordingCanvas(MockCanvas):
        def text(self, x, y, s, color, bg=None, scale=1, font=None):
            drawn.append(s)
            return super().text(x, y, s, color, bg=bg, scale=scale, font=font)

    screen._press_preview(RecordingCanvas(), cancel)
    assert drawn == []


def test_screens_default_to_instant():
    assert S.Screen.SWEEP_MS == 0


# --- the "locked in" colour change ----------------------------------------

class ColourCanvas(HeldCanvas):
    """Records the fill colour of every rectangle drawn."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fills = []
        self.sized_fills = []

    def fill_rect(self, x, y, w, h, color):
        self.fills.append(color)
        # Size matters now that a button can paint ON TOP of its own
        # background: die pips go down as 1px-tall spans through fill_circle,
        # so the LAST fill is a pip, not the button. Recording geometry lets a
        # test ask about the button itself rather than about whatever happened
        # to be drawn most recently.
        self.sized_fills.append((w, h, color))
        return super().fill_rect(x, y, w, h, color)


def test_locked_button_is_filled_with_the_accent():
    """Committing flips the button to a solid fill. A colour change is the
    unambiguous 'it counted' signal -- a closed ring alone is easy to miss."""
    from seedwitness.ui import theme as th
    b = S.TapButton(0, 0, 100, 40, "x", lambda a: None)
    canvas = ColourCanvas()
    b.draw(canvas, locked=True)
    assert th.ACCENT in canvas.fills


def test_unlocked_button_is_not_accent_filled():
    from seedwitness.ui import theme as th
    b = S.TapButton(0, 0, 100, 40, "x", lambda a: None)
    canvas = ColourCanvas()
    b.draw(canvas)
    assert canvas.fills and canvas.fills[0] == th.BUTTON_BG


def test_a_held_press_paints_the_locked_state_before_acting():
    from seedwitness.ui import theme as th
    screen = _screen()
    app = App()
    canvas = ColourCanvas()
    screen.handle_tap(app, canvas, *_centre(screen.buttons[0]))
    assert screen.session.rolls == [1]
    assert th.ACCENT in canvas.fills


def test_a_cancelled_press_never_paints_locked():
    """A withdrawn press must not flash the committed colour -- that would
    say the roll counted when it did not."""
    from seedwitness.ui import theme as th
    screen = _screen()
    app = App()
    canvas = ColourCanvas(hold_for=1)
    canvas.fills = []
    screen.handle_tap(app, canvas, *_centre(screen.buttons[0]))
    assert screen.session.rolls == []
    # The sweep itself draws accent strokes, so assert on the final state of
    # the BUTTON: its last full-size fill must be the normal background.
    # Asserting on the last fill of any size used to work and no longer does,
    # because a D6 button now paints pips over its own background.
    btn = screen.buttons[0]
    button_fills = [c for w, h, c in canvas.sized_fills
                    if w == btn.w and h == btn.h]
    assert button_fills, "the button was never repainted"
    assert button_fills[-1] == th.BUTTON_BG, "cancelled button not restored"
