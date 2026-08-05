"""A finger that stays down must fire its control exactly once.

Bounding canvas.wait_release fixed the original freeze but created a
repeat: the wait now returns False after its 3s timeout with the finger
still on the glass, and the loop fell straight through to get_press, which
read the SAME unreleased touch as a brand-new press. Observed live on the
device and reproduced on the board: a finger held on the word-entry 'b'
key typed another 'b' every ~3.4 seconds ("bbbbbbbb" on the user's panel).
On RollEntryScreen the identical mechanics commit the same roll twice,
which silently corrupts the ceremony's entropy -- nothing downstream can
detect it.

The two failure modes constrain each other and both must hold at once:
a held finger must never wedge the device (the original freeze), and must
never fire the same control more than once (this regression). These tests
drive the REAL loop (_main_loop) with one modelled finger that stays down
through several full wait_release timeouts.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "device"))
sys.path.insert(0, str(ROOT / "sim"))

from mock_canvas import MockCanvas  # noqa: E402

from seedwitness import entropy as ent  # noqa: E402
from seedwitness.ui import app as A  # noqa: E402
from seedwitness.ui import screens as S  # noqa: E402


class _LoopDone(BaseException):
    """Deliberately NOT an Exception: escapes _main_loop's guards to end
    the otherwise-infinite loop once the scripted finger has lifted."""


class HeldFingerCanvas(MockCanvas):
    """One physical finger, glued to `point` through `timeouts` full
    wait_release timeouts (3 seconds each on hardware), then lifted.

    This is the state machine of a real held touch: get_press keeps
    resolving the same point while the finger is down, wait_release
    returns False (timed out, still down) `timeouts` times before the
    release actually happens, and a few empty polls after the lift end
    the loop."""

    hold_ms = 0  # hold-to-confirm sweeps complete instantly, as in all tests

    def __init__(self, point, timeouts, empty_polls=4):
        super().__init__()
        self.point = point
        self.timeouts = timeouts
        self.down = True
        self.empty_polls = empty_polls

    def get_press(self):
        if self.down:
            return self.point
        self.empty_polls -= 1
        if self.empty_polls < 0:
            raise _LoopDone()
        return None

    def touch_active(self):
        return self.down

    def wait_release(self):
        if self.timeouts > 0:
            self.timeouts -= 1
            return False        # bounded wait expired, finger still down
        self.down = False
        return True


def _centre(b):
    return b.x + b.w // 2, b.y + b.h // 2


def _run(app, canvas):
    try:
        A._main_loop(app, canvas)
    except _LoopDone:
        pass


def test_a_held_letter_key_types_exactly_one_letter():
    """The regression as reported: held 'b' filled the screen with
    'bbbbbbbb', one per wait_release timeout."""
    app = A.App()
    screen = S.WordEntryScreen(12)
    app.push(screen)
    key = next(b for b in screen.buttons if b.label == "b")
    _run(app, HeldFingerCanvas(_centre(key), timeouts=3))
    assert screen.prefix == "b", (
        "a single held press appended %r; the still-held touch re-fired "
        "as a new press after a wait_release timeout" % screen.prefix)


def test_a_held_die_commits_exactly_one_roll():
    """The serious instance: a doubled roll silently corrupts the entropy
    and cannot be detected afterwards."""
    app = A.App()
    screen = S.RollEntryScreen(ent.RollSession(ent.SOURCES["D6"], 12))
    app.push(screen)
    _run(app, HeldFingerCanvas(_centre(screen.buttons[2]), timeouts=3))
    assert screen.session.rolls == [3], (
        "one held press produced rolls %r" % screen.session.rolls)


def test_release_within_the_first_wait_still_fires_once():
    """The ordinary tap: no timeout involved, exactly one action."""
    app = A.App()
    screen = S.WordEntryScreen(12)
    app.push(screen)
    key = next(b for b in screen.buttons if b.label == "b")
    _run(app, HeldFingerCanvas(_centre(key), timeouts=0))
    assert screen.prefix == "b"


def test_a_held_menu_item_navigates_once_and_redraws_during_the_hold():
    """The fourth field report: long-press on a Verify Seed menu item froze
    the device. Two causes, both pinned here. The release-edge guard was an
    unbounded inner loop, so the main loop stopped turning for as long as
    the touch persisted; and the redraw ran only after the release, so the
    panel kept showing the PRE-tap screen for the whole hold -- a menu's
    170ms sweep completes almost immediately, leaving the longest dead
    window of any control in the app. The fixed loop must fire the action
    exactly once, put the new screen on the panel BEFORE waiting for the
    release, and still be alive afterwards."""
    app = A.App()
    home = app.screen
    verify = next(b for b in home.buttons
                  if "Verify" in b.label and "Build" not in b.label)
    events = []

    class Recording(HeldFingerCanvas):
        def fill(self, color):
            events.append("fill")
            super().fill(color)

        def wait_release(self):
            events.append("wait")
            return super().wait_release()

    _run(app, Recording(_centre(verify), timeouts=3))
    # fired exactly once, however long the hold
    assert len(app.stack) == 2, "the held menu item pushed %d screens" % (
        len(app.stack) - 1)
    assert type(app.screen).__name__ != "HomeScreen"
    # the pushed screen's redraw (second full fill: first is the boot draw)
    # must land before the loop first waits for the release
    fills = [i for i, e in enumerate(events) if e == "fill"]
    first_wait = events.index("wait")
    assert len(fills) >= 2 and fills[1] < first_wait, (
        "the post-tap redraw must not wait for the finger to lift: %r"
        % events)


def test_the_loop_survives_the_hold_and_keeps_accepting_input():
    """The other half of the constraint: requiring a release edge must not
    reintroduce the original freeze. After the finger finally lifts, the
    loop must still be alive and dispatch the next, separate tap."""
    app = A.App()
    screen = S.WordEntryScreen(12)
    app.push(screen)
    key_b = next(b for b in screen.buttons if b.label == "b")

    class TwoTouches(HeldFingerCanvas):
        """A long stuck hold on 'b', then a clean second tap on 'a'."""

        def __init__(self):
            super().__init__(_centre(key_b), timeouts=5)
            self.second_tap_pending = True

        def get_press(self):
            if self.down:
                return self.point
            if self.second_tap_pending:
                self.second_tap_pending = False
                # a fresh physical touch: finger down again, releasing
                # cleanly within the first wait this time
                self.down = True
                self.timeouts = 0
                key_a = next(b for b in screen.buttons if b.label == "a")
                return _centre(key_a)
            raise _LoopDone()

    _run(app, TwoTouches())
    assert screen.prefix == "ba", (
        "expected the held 'b' once then a clean 'a', got %r" % screen.prefix)
