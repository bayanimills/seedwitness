"""Every long or costly thing this device does must have a way back.

Three separate places had no exit, and all three failed the same way: the
device was working correctly and the user had no move left. Fifty rolls with
no undo and a Back that discarded them on a brush; a ten-minute derivation
with no cancel; and, on the other side of the same coin, screens showing the
seed that never timed out at all while the QR of that same seed hid itself
after twenty seconds.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "device"))
sys.path.insert(0, str(ROOT / "sim"))

from mock_canvas import MockCanvas  # noqa: E402

from seedwitness import entropy as ent  # noqa: E402
from seedwitness.ui import app as A  # noqa: E402
from seedwitness.ui import screens as S  # noqa: E402
from seedwitness.ui.app import App  # noqa: E402

# None of the behaviour below is implemented. This file is a specification
# written 2026-08-09 ahead of the code, and the code never followed: undo a
# roll, erase a letter, cancel a derivation, idle sleep with warning, and a
# brush that must not end the ceremony. It is kept so the intent is not lost.
#
# Self-healing sentinel: implement RollSession.undo_roll and the spec runs.
pytestmark = pytest.mark.skipif(
    not hasattr(ent.RollSession, "undo_roll"),
    reason="recoverable-waits feature not implemented; spec retained",
)


class HeldCanvas(MockCanvas):
    """Reports the finger as lifting after `hold_for` polls."""

    def __init__(self, hold_for=99):
        super().__init__()
        self.hold_for = hold_for
        self.polls = 0

    def touch_active(self):
        self.polls += 1
        return self.polls <= self.hold_for


def _roll_screen(rolls=0):
    from seedwitness.ui.flow_generate import RollEntryScreen
    session = ent.RollSession(ent.SOURCES["D6"], 12)
    for i in range(rolls):
        session.add_roll((i % 6) + 1)
    return RollEntryScreen(session)


def _centre(b):
    return b.x + b.w // 2, b.y + b.h // 2


# --------------------------------------------------------------------------
# Undo


def test_undo_roll_removes_only_the_last_one():
    s = ent.RollSession(ent.SOURCES["D6"], 12)
    for v in (4, 2, 6):
        s.add_roll(v)
    assert s.undo_roll() is True
    assert s.rolls == [4, 2]


def test_undo_on_an_empty_session_is_a_no_op():
    """Not an error: the button is hidden when there is nothing to remove,
    and a session that reports False rather than raising keeps a stray call
    from reaching the crash screen."""
    s = ent.RollSession(ent.SOURCES["D6"], 12)
    assert s.undo_roll() is False
    assert s.rolls == []


def test_undone_rolls_leave_no_trace_in_the_entropy():
    """A session with a roll added and undone must be indistinguishable from
    one that never saw it -- otherwise undo would be a lie about what the
    dice decided."""
    a = ent.RollSession(ent.SOURCES["D6"], 12)
    b = ent.RollSession(ent.SOURCES["D6"], 12)
    for v in (1, 5, 3):
        a.add_roll(v)
        b.add_roll(v)
    a.add_roll(6)
    a.undo_roll()
    assert a.roll_string() == b.roll_string()
    assert a.live_preview_hash() == b.live_preview_hash()


def test_undo_button_appears_only_once_there_is_something_to_undo():
    assert _roll_screen(rolls=0)._undo_button() is not None  # constructible
    screen = _roll_screen(rolls=3)
    app, canvas = App(), HeldCanvas()
    screen.handle_tap(app, canvas, *_centre(screen._undo_button()))
    assert len(screen.session.rolls) == 2
    # with no rolls the same coordinates must not reach the handler
    empty = _roll_screen(rolls=0)
    empty.handle_tap(app, HeldCanvas(), *_centre(empty._undo_button()))
    assert empty.session.rolls == []


def test_undo_is_held_like_every_other_control_on_the_screen():
    """The roll screen's rule is that everything holds. A brush must not
    silently drop a roll the user believes they recorded."""
    screen = _roll_screen(rolls=3)
    screen.handle_tap(App(), HeldCanvas(hold_for=1),
                      *_centre(screen._undo_button()))
    assert len(screen.session.rolls) == 3


# --------------------------------------------------------------------------
# Back, on the one screen where leaving is expensive


def test_back_holds_once_rolls_exist():
    """Back discards up to 49 rolls of physical dice work. Everywhere else in
    the app it is a plain tap, and it stays one here until there is something
    to lose."""
    screen = _roll_screen(rolls=12)
    app = App()
    app.push(screen)
    depth = len(app.stack)
    screen.handle_tap(app, HeldCanvas(hold_for=1),
                      *_centre(screen.back_button(app)))
    assert len(app.stack) == depth, "a brush must not end the ceremony"
    screen.handle_tap(app, HeldCanvas(), *_centre(screen.back_button(app)))
    assert len(app.stack) == depth - 1


def test_back_stays_a_plain_tap_with_nothing_rolled():
    screen = _roll_screen(rolls=0)
    app = App()
    app.push(screen)
    depth = len(app.stack)
    screen.handle_tap(app, HeldCanvas(hold_for=1),
                      *_centre(screen.back_button(app)))
    assert len(app.stack) == depth - 1


# --------------------------------------------------------------------------
# Idle windows


def _idle_for(screen):
    app = App()
    app.push(screen)
    return A._idle_limits(app)


def test_menus_keep_the_hour():
    assert _idle_for(S.HomeScreen()) == (A.SLEEP_AFTER_MS, A.SLEEP_WARN_MS)


def test_screens_showing_the_seed_time_out_in_minutes():
    from seedwitness.ui.flow_generate import ChecksumWordScreen, WordListScreen

    M = ("abandon abandon abandon abandon abandon abandon "
         "abandon abandon abandon abandon abandon about")
    for screen in (WordListScreen(M, 0), ChecksumWordScreen(M)):
        after, warn = _idle_for(screen)
        assert after == S.Screen.SECRET_IDLE_MS
        assert warn == S.Screen.SECRET_WARN_MS
        assert after < A.SLEEP_AFTER_MS


def test_the_warning_can_never_outlast_the_window_it_counts_down():
    """A screen that asked for a countdown longer than its own timeout would
    put the notice up permanently and never blank."""

    class Greedy(S.Screen):
        IDLE_MS = 30_000
        IDLE_WARN_MS = 5 * 60 * 1000

    after, warn = _idle_for(Greedy())
    assert warn <= after


# --------------------------------------------------------------------------
# Cancelling a derivation


def test_cancel_needs_the_panel_to_be_seen_clear_first():
    """Screen._dispatch fires on_tap while the finger that pressed Derive is
    still down, so an unarmed check would cancel the derivation the user just
    asked for, on its very first progress callback."""
    from seedwitness.ui.flow_address import DerivingScreen

    waiting = DerivingScreen()
    never_lifts = HeldCanvas()
    for _ in range(5):
        waiting._check_cancel(never_lifts)   # must not raise


def test_a_held_touch_after_release_cancels():
    from seedwitness.ui.flow_address import DeriveCancelled, DerivingScreen

    waiting = DerivingScreen()

    class Sequence(MockCanvas):
        """Clear once (arming the gesture), then held down for good."""

        def __init__(self):
            super().__init__()
            self.n = 0

        def touch_active(self):
            self.n += 1
            return self.n > 1

    canvas = Sequence()
    waiting._check_cancel(canvas)            # clear: arms
    try:
        waiting._check_cancel(canvas)        # held: cancels
    except DeriveCancelled:
        pass
    else:
        raise AssertionError("a held touch after release must cancel")


def test_a_touch_released_inside_the_window_is_not_a_cancel():
    from seedwitness.ui.flow_address import DerivingScreen

    waiting = DerivingScreen()
    waiting._cancel_armed = True
    waiting._check_cancel(HeldCanvas(hold_for=2))   # lifts almost at once


# --------------------------------------------------------------------------
# Labels that follow state


def test_the_erase_key_names_what_it_would_actually_remove():
    from seedwitness.ui.flow_verify import WordEntryScreen

    screen = WordEntryScreen(12)
    canvas = MockCanvas()
    app = App()

    screen.draw(app, canvas)
    assert screen.erase_button.label == WordEntryScreen.ERASE_LETTER

    screen.prefix = "aba"
    screen.draw(app, canvas)
    assert screen.erase_button.label == WordEntryScreen.ERASE_LETTER

    # a confirmed word behind an empty prefix: the key now throws the WORD
    # away, which is what "<- Back" used to do without saying so
    screen.prefix = ""
    screen.words = ["abandon"]
    screen.draw(app, canvas)
    assert screen.erase_button.label == WordEntryScreen.ERASE_WORD
