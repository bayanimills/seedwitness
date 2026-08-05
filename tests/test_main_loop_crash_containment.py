"""The main loop must contain a crash anywhere in its cycle, draw included.

The third field freeze (holding a letter key on Verify Seed) was a
MemoryError raised by the full-screen redraw at the END of the loop cycle:
a held finger makes every cycle burn wait_release's whole timeout in
allocation-heavy polling, nothing in the loop ever collected, and after a
few cycles the ili9341 clear()'s 3840-byte line buffer could not be
allocated. That draw sat OUTSIDE the crash handler, so instead of the
CrashScreen the app unwound out of main.py entirely: panel frozen on its
last frame, seed cache intact, live REPL on USB, and no diag breadcrumb.

These tests drive the REAL loop (_main_loop) with a scripted canvas -- the
previous two freezes survived repeated REPL-driven repros precisely because
those bypassed the loop.
"""
import os
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "device"))
sys.path.insert(0, str(ROOT / "sim"))

from mock_canvas import MockCanvas  # noqa: E402

from seedwitness import diag  # noqa: E402
from seedwitness.ui import app as A  # noqa: E402
from seedwitness.ui import screens as S  # noqa: E402


class _LoopDone(BaseException):
    """Deliberately NOT an Exception: escapes every guard in _main_loop, so a
    scripted canvas can end the otherwise-infinite loop."""


class ScriptedCanvas(MockCanvas):
    """MockCanvas plus the touch API _main_loop drives: serves a fixed list
    of presses, then ends the loop."""

    hold_ms = 0  # hold-to-confirm commits instantly, as everywhere in tests

    def __init__(self, presses):
        super().__init__()
        self._presses = list(presses)

    def get_press(self):
        if not self._presses:
            raise _LoopDone()
        return self._presses.pop(0)

    def touch_active(self):
        return True

    def wait_release(self):
        return True


class _Inert(S.Screen):
    """A screen with no buttons: a press changes nothing, so each loop cycle
    is pure dispatch + redraw."""
    buttons = ()

    def draw(self, app, canvas):
        canvas.fill((0, 0, 0))


class _BoomOnSecondDraw(_Inert):
    """Draws fine at boot, then OOMs on the first post-tap redraw -- the
    reproduced freeze, minus the three cycles of garbage it took to get
    there on the real board."""

    def __init__(self):
        self.calls = 0

    def draw(self, app, canvas):
        self.calls += 1
        if self.calls == 2:
            raise MemoryError("memory allocation failed, allocating 3840 bytes")
        super().draw(app, canvas)


def _fresh_diag():
    try:
        os.remove(diag._PATH)
    except OSError:
        pass


def test_a_draw_crash_reaches_the_crash_screen_not_the_repl():
    """The regression that froze the device: before the fix, this exact
    MemoryError propagated straight out of _main_loop (and on hardware out
    of main.py, to a dead panel and a live REPL)."""
    _fresh_diag()
    app = A.App()
    app.push(_BoomOnSecondDraw())
    canvas = ScriptedCanvas([(5, 5)])
    try:
        A._main_loop(app, canvas)
    except _LoopDone:
        pass
    assert type(app.screen).__name__ == "CrashScreen", (
        "a crash in draw() must show the CrashScreen, not unwind the loop")


def test_a_draw_crash_leaves_a_diag_breadcrumb():
    """The field report had no crash line in diag.txt because the escape
    path never ran record_crash. Contained crashes must record."""
    _fresh_diag()
    app = A.App()
    app.push(_BoomOnSecondDraw())
    try:
        A._main_loop(app, ScriptedCanvas([(5, 5)]))
    except _LoopDone:
        pass
    assert diag.read().get("crash", "").startswith("MemoryError")


def test_an_undrawable_crash_screen_resets_instead_of_wedging(monkeypatch):
    """If even the CrashScreen cannot draw (heap truly gone), the loop must
    reset the board -- RAM cleared, working home screen -- rather than
    unwind to a frozen panel with the seed still in memory."""
    resets = []

    def fake_reset():
        resets.append(True)
        raise _LoopDone()

    monkeypatch.setattr(A, "_hard_reset", fake_reset)

    class EveryDrawFails(ScriptedCanvas):
        def __init__(self, presses):
            super().__init__(presses)
            self._fills = 0

        def fill(self, color):
            self._fills += 1
            if self._fills > 1:  # boot draw succeeds, every redraw OOMs
                raise MemoryError(
                    "memory allocation failed, allocating 3840 bytes")
            super().fill(color)

    app = A.App()
    app.push(_Inert())
    try:
        A._main_loop(app, EveryDrawFails([(5, 5)]))
    except _LoopDone:
        pass
    assert resets, "two consecutive draw failures must fail safe via reset"


def test_the_loop_collects_garbage_every_cycle(monkeypatch):
    """The root cause of the freeze was cross-cycle garbage accumulation:
    nothing in the loop collected, so a held key OOM'd the board in three
    cycles (free RAM measured 23120 -> 18512 -> 9840 -> MemoryError). The
    per-cycle collect is load-bearing; losing it brings the freeze back."""
    calls = []
    monkeypatch.setattr(A, "gc", types.SimpleNamespace(collect=lambda: calls.append(1)))
    app = A.App()
    app.push(_Inert())
    try:
        A._main_loop(app, ScriptedCanvas([(5, 5)] * 3))
    except _LoopDone:
        pass
    assert len(calls) >= 3, "gc.collect must run on every interaction cycle"
