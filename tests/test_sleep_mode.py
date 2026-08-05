"""Sleep mode: the hour-idle threshold, the five-minute countdown that makes
it preventable, the session clearing at sleep entry, the swallowed wake
touch, and the drifting Zzz that proves the loop is still turning.

History this feature must not repeat: idle blanking was built at ~30s and
removed the same day, because a panel that blanks mid-transcription is a
control users defeat by never letting the device idle. The shipped timings
are therefore load-bearing and asserted here: an hour of no touch, announced
by a five-minute full-frame countdown that any touch cancels.

These tests drive the REAL loop (_main_loop) with a scripted canvas that
owns the clock -- the pattern of test_main_loop_crash_containment, because
every freeze this project has had lived only inside the real loop.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "device"))
sys.path.insert(0, str(ROOT / "sim"))

from mock_canvas import MockCanvas  # noqa: E402

from seedwitness import derive  # noqa: E402
from seedwitness.ui import app as A  # noqa: E402
from seedwitness.ui import screens as S  # noqa: E402

MIN = 60 * 1000


class _LoopDone(BaseException):
    """Deliberately NOT an Exception: escapes every guard in _main_loop, so
    a scripted canvas can end the otherwise-infinite loop."""


class TimedCanvas(MockCanvas):
    """Feeds _main_loop a scripted list of (time_ms, press) events and owns
    the clock the loop reads (_run points A._ticks_ms at .now). A None press
    is an idle tick -- on hardware, get_press returning on the touch
    controller's ~2s timeout. Records every draw primitive so tests can
    assert what was painted and, as important, what was not."""

    hold_ms = 0

    def __init__(self, events):
        super().__init__()
        self.events = list(events)
        self.now = 0
        self.calls = []

    def get_press(self):
        if not self.events:
            raise _LoopDone()
        t, press = self.events.pop(0)
        self.now = t
        return press

    def fill(self, color):
        self.calls.append(("fill",))
        super().fill(color)

    def fill_rect(self, x, y, w, h, color):
        self.calls.append(("fill_rect", x, y, w, h))
        super().fill_rect(x, y, w, h, color)

    def text(self, x, y, s, color, bg=None, scale=1, font=None):
        self.calls.append(("text", x, y, s))
        super().text(x, y, s, color, bg=bg, scale=scale, font=font)

    def touch_active(self):
        return True

    def wait_release(self):
        return True


def _run(app, canvas, monkeypatch):
    monkeypatch.setattr(A, "_ticks_ms", lambda: canvas.now)
    try:
        A._main_loop(app, canvas)
    except _LoopDone:
        pass


def _texts(canvas):
    return [c[3] for c in canvas.calls if c[0] == "text"]


def _fills(canvas):
    return sum(1 for c in canvas.calls if c[0] == "fill")


def test_the_shipped_timings_are_the_owners():
    """One hour to sleep, announced by a five-minute countdown, Zzz every
    five seconds. NOT the 30 seconds that got the last attempt removed --
    anyone shortening these must reread that commit (ea98531) first."""
    assert A.SLEEP_AFTER_MS == 60 * 60 * 1000
    assert A.SLEEP_WARN_MS == 5 * 60 * 1000
    assert A.ZZZ_STEP_MS == 5000


def test_no_countdown_before_55_minutes(monkeypatch):
    canvas = TimedCanvas([(30 * MIN, None), (55 * MIN - 1, None)])
    _run(A.App(), canvas, monkeypatch)
    assert "sleeping in" not in _texts(canvas)


def test_the_countdown_appears_at_55_minutes_and_says_what_sleep_does(monkeypatch):
    canvas = TimedCanvas([(55 * MIN, None)])
    _run(A.App(), canvas, monkeypatch)
    texts = _texts(canvas)
    assert "sleeping in" in texts
    assert "5m 00s" in texts
    assert "touch to stay awake" in texts
    # honesty requirement: the countdown must warn that sleep ends the
    # session, because that is the consequence a touch here prevents
    assert any("clears any seed" in t for t in texts), texts


def test_the_countdown_ticks_without_repainting_the_frame(monkeypatch):
    canvas = TimedCanvas([(55 * MIN, None), (55 * MIN + 2000, None),
                          (55 * MIN + 4000, None)])
    _run(A.App(), canvas, monkeypatch)
    # exactly two full repaints ever: the boot draw and the countdown frame;
    # each later tick repaints only the remaining-time figure
    assert _fills(canvas) == 2
    texts = _texts(canvas)
    assert "4m 58s" in texts and "4m 56s" in texts


def test_a_touch_cancels_the_countdown_without_dispatch_or_clearing(monkeypatch):
    """The touch that prevents sleep must do nothing else: not activate the
    control under the finger (the user is aiming at a notice, not at the
    screen it covered) and not cost the session it exists to save."""
    app = A.App()
    app.last_mnemonic = "kept"
    b = app.screen.buttons[0]                 # "Roll a Seed"
    press = (b.x + b.w // 2, b.y + b.h // 2)
    canvas = TimedCanvas([(56 * MIN, None), (56 * MIN + 4000, press),
                          (56 * MIN + 8000, None)])
    _run(app, canvas, monkeypatch)
    assert len(app.stack) == 1, "the cancelling touch dispatched a control"
    assert type(app.screen).__name__ == "HomeScreen"
    assert app.last_mnemonic == "kept", "cancelling the countdown must not clear"
    # boot, countdown frame, and the restored screen after the cancel
    assert _fills(canvas) == 3
    # and the countdown is gone for good until idleness accrues again: the
    # tick 4s after the cancel must not repaint it
    assert _texts(canvas).count("sleeping in") == 1


def test_entering_sleep_clears_the_session_like_going_home(monkeypatch):
    """After an hour unattended the ceremony is over or abandoned, and the
    device must not still hold a seed for whoever picks it up next. Sleep
    ends the session exactly as Done/Home does."""
    cleared = []
    monkeypatch.setattr(derive, "clear_seed_cache", lambda: cleared.append(1))
    app = A.App()
    app.last_mnemonic = "secret words"
    app.passphrase = "secret passphrase"
    app.demo = True
    app.push(S.WordEntryScreen(12))
    canvas = TimedCanvas([(60 * MIN, None), (60 * MIN + 2000, None)])
    _run(app, canvas, monkeypatch)
    assert app.last_mnemonic is None
    assert app.passphrase == ""
    assert app.demo is False
    assert cleared, "the cached PBKDF2 seed must be dropped at sleep entry"
    assert len(app.stack) == 1 and type(app.screen).__name__ == "HomeScreen"
    # and the sleeping frame says why the ceremony is gone
    texts = _texts(canvas)
    assert "Zzz" in texts
    assert "session cleared" in texts
    assert "touch to wake" in texts


def test_the_wake_touch_is_swallowed_not_dispatched(monkeypatch):
    """The user is reaching for a dark panel and cannot see what they are
    about to press -- same rule as the splash tap. The press lands exactly
    on 'Roll a Seed'; waking must not start a ceremony."""
    app = A.App()
    b = app.screen.buttons[0]
    press = (b.x + b.w // 2, b.y + b.h // 2)
    canvas = TimedCanvas([(60 * MIN, None), (60 * MIN + 2000, None),
                          (61 * MIN, press), (61 * MIN + 2000, None)])
    _run(app, canvas, monkeypatch)
    assert len(app.stack) == 1, "the wake touch dispatched a control"
    assert type(app.screen).__name__ == "HomeScreen"
    # boot, countdown, sleep entry, and the wake redraw of the home screen
    assert _fills(canvas) == 4
    # idleness restarted at the wake: the tick 2s later must not re-sleep
    assert _texts(canvas).count("Zzz") == 1


def test_the_zzz_drifts_by_partial_repaint_only(monkeypatch):
    """The moving Zzz is the liveness signal (a lit static frame over a dead
    program is this project's recurring failure) and the burn-in relief. It
    must actually move, and it must move cheaply: erase the old block, paint
    the new one, never a full-screen repaint (~0.5s on this panel)."""
    t0 = 60 * MIN + 2000                       # sleep entry tick
    canvas = TimedCanvas([(60 * MIN, None), (t0, None)] +
                         [(t0 + i * 5000, None) for i in (1, 2, 3)])
    _run(A.App(), canvas, monkeypatch)
    zzz = [(c[1], c[2]) for c in canvas.calls
           if c[0] == "text" and c[3] == "Zzz"]
    assert len(zzz) == 4, "expected the entry paint plus three 5s moves"
    assert len(set(zzz)) >= 3, "the Zzz position must change, not sit still"
    for a, b in zip(zzz, zzz[1:]):
        assert a != b, "consecutive positions may never repeat"
    # full repaints: boot, countdown frame, sleep entry -- and NOT ONE MORE.
    # every move after that is an erase rectangle plus the new block
    assert _fills(canvas) == 3
    erases = [(c[1], c[2]) for c in canvas.calls
              if c[0] == "fill_rect" and (c[3], c[4]) == (A._ZZZ_W, A._ZZZ_H)]
    assert erases == zzz[:-1], (
        "each move must erase exactly the block it replaces: %r vs %r"
        % (erases, zzz[:-1]))


def test_the_zzz_block_always_fits_the_panel(monkeypatch):
    """The position derivation must never place the block where text would
    be refused by the canvas bounds guard (an off-screen Zzz is a silent
    liveness outage). Walk many LCG steps and check every one."""
    from seedwitness.ui import theme as th
    z = ((12345 | 1), -1, -1, 0)
    canvas = TimedCanvas([])
    for _ in range(500):
        z = A._zzz_step(canvas, z, 0)
        _, x, y, _ = z
        assert 0 <= x <= th.WIDTH - A._ZZZ_W
        assert 0 <= y <= th.HEIGHT - A._ZZZ_H
    # and everything queued for drawing really was drawn (nothing refused)
    assert sum(1 for c in canvas.calls if c[0] == "text") == 3 * 500
