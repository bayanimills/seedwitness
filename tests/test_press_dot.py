"""The press dot: the loop's own touch-registered / liveness indicator.

Drawn by _main_loop (never by a screen) the moment a press is accepted,
wiped by that cycle's full redraw. A press with no dot means the panel or
calibration is at fault; a press with no dot on a lit, plausible-looking
screen means the loop is dead -- the hung-app-looks-healthy failure this
project keeps paying for.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "device"))
sys.path.insert(0, str(ROOT / "sim"))

from mock_canvas import MockCanvas  # noqa: E402

from seedwitness.ui import app as A  # noqa: E402
from seedwitness.ui import screens as S  # noqa: E402
from seedwitness.ui import theme as th  # noqa: E402

DOT_RECT = (th.WIDTH - A.PRESS_DOT_W - 1, S.HEADER_H - A.PRESS_DOT_H,
            A.PRESS_DOT_W, A.PRESS_DOT_H)


class _LoopDone(BaseException):
    pass


class RecordingCanvas(MockCanvas):
    hold_ms = 0

    def __init__(self, presses):
        super().__init__()
        self._presses = list(presses)
        self.ops = []  # ("rect", (x, y, w, h), color) | ("fill", None, color)

    def fill(self, color):
        self.ops.append(("fill", None, color))
        super().fill(color)

    def fill_rect(self, x, y, w, h, color):
        self.ops.append(("rect", (x, y, w, h), color))
        super().fill_rect(x, y, w, h, color)

    def touch_active(self):
        return True

    def wait_release(self):
        return True

    def get_press(self):
        if not self._presses:
            raise _LoopDone()
        return self._presses.pop(0)


class _Inert(S.Screen):
    buttons = ()

    def draw(self, app, canvas):
        canvas.fill(th.BG)


def _run(app, canvas):
    try:
        A._main_loop(app, canvas)
    except _LoopDone:
        pass


def _dot_indices(canvas):
    return [i for i, (op, rect, color) in enumerate(canvas.ops)
            if op == "rect" and rect == DOT_RECT and color == th.ACCENT]


def test_the_dot_lights_on_press_and_the_redraw_wipes_it():
    app = A.App()
    app.push(_Inert())
    canvas = RecordingCanvas([(5, 200)])  # a miss: hits no control
    _run(app, canvas)
    dots = _dot_indices(canvas)
    assert dots, "an accepted press never lit the dot"
    # the cycle must end with a full repaint AFTER the dot, which erases it
    fills = [i for i, (op, _, _) in enumerate(canvas.ops) if op == "fill"]
    assert any(i > dots[-1] for i in fills), (
        "nothing repainted over the dot after the press was handled")


def test_the_dot_lights_before_dispatch_not_after():
    """Feedback must not wait for the handler: a press that takes a while
    (hold-to-confirm, a slow action) should already show as registered."""
    order = []

    class Spy(_Inert):
        def handle_tap(self, app, canvas, x, y):
            order.append("dispatch")

    app = A.App()
    app.push(Spy())

    class DotSpyCanvas(RecordingCanvas):
        def fill_rect(self, x, y, w, h, color):
            if (x, y, w, h) == DOT_RECT:
                order.append("dot")
            super().fill_rect(x, y, w, h, color)

    _run(app, DotSpyCanvas([(5, 200)]))
    assert order and order[0] == "dot", "dot must light before dispatch"


def test_an_overlong_hold_keeps_the_dot_lit_and_douses_it_on_lift():
    """When the release outlives its bounded wait, the dot must be re-lit
    on top of the fresh post-tap frame (the redraw wiped it) and stay lit
    for the whole overlong hold -- it is the only signal that the device
    is alive and simply waiting for the finger to lift -- then go dark the
    moment the lift is seen."""
    app = A.App()
    app.push(_Inert())

    class HoldCanvas(RecordingCanvas):
        def __init__(self):
            super().__init__([(5, 200)])
            self.timeouts = 2

        def wait_release(self):
            if self.timeouts:
                self.timeouts -= 1
                return False
            return True

    canvas = HoldCanvas()
    _run(app, canvas)
    dots = _dot_indices(canvas)
    fills = [i for i, (op, _, _) in enumerate(canvas.ops) if op == "fill"]
    assert dots and fills
    assert dots[-1] > max(fills), (
        "the dot must be re-lit after the post-tap redraw while the touch "
        "is still down")
    offs = [i for i, (op, rect, color) in enumerate(canvas.ops)
            if op == "rect" and rect == DOT_RECT and color == th.BG]
    assert offs and offs[-1] > dots[-1], (
        "the dot must be painted back to background once the lift is seen")


def test_no_press_no_dot():
    """The dot is a press indicator, not an idle blinker: an idle loop must
    leave the panel untouched (the screen is being transcribed from)."""
    app = A.App()
    app.push(_Inert())
    canvas = RecordingCanvas([None, None, None])
    _run(app, canvas)
    assert _dot_indices(canvas) == []


def test_the_dot_slot_is_clear_on_every_screen():
    """The one top-right slot free everywhere: right of the clamped titles,
    below the corner Back button, inside the header band so any header
    repaint or full fill erases it."""
    x, y, w, h = DOT_RECT
    assert x + w <= th.WIDTH and y >= 0
    assert y + h <= S.HEADER_H, "dot must sit inside the header band"
    # titles are clamped to WIDTH - MARGIN; the dot starts at or right of it
    assert x >= th.WIDTH - S.MARGIN
    # the corner Back button (screens that have one): no overlap
    bx = th.WIDTH - S.Screen.BACK_INSET - S.Screen.BACK_W
    by = S.Screen.BACK_INSET
    bw, bh = S.Screen.BACK_W, S.Screen.BACK_H
    overlap_x = x < bx + bw and bx < x + w
    overlap_y = y < by + bh and by < y + h
    assert not (overlap_x and overlap_y), "dot overlaps the corner Back button"
