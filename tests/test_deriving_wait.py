"""The ten-minute wait, made survivable: motion so a working device cannot
be mistaken for a hung one, a time estimate measured from the observed
PBKDF2 rate rather than assumed, and the all-types caption that used to
dangle under the percentage for the whole stretch.

Everything here drives DerivingScreen.update_progress the way the device
does -- through repeated (done, total) callbacks -- with the clock faked so
the observed rate is exactly what the test says it is. Desktop CPython's
native pbkdf2_hmac has no progress hook, so the real flow tests (single-type
versus all-types rendering) assert on what gets painted, not on timing.
"""
import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "device"))
sys.path.insert(0, str(ROOT / "sim"))

from mock_canvas import MockCanvas  # noqa: E402

from seedwitness import derive as d  # noqa: E402
from seedwitness.ui import flow_address as fa  # noqa: E402
from seedwitness.ui import screens as S  # noqa: E402
from seedwitness.ui import theme as th  # noqa: E402
from seedwitness.ui.app import App  # noqa: E402

M = ("abandon abandon abandon abandon abandon abandon abandon abandon "
     "abandon abandon abandon about")

TOTAL = 2048          # PBKDF2 rounds, matching the real stretch
STEP = 16             # callback cadence, matching mp_shims/pbkdf2.py
RATE_MS = 280         # ~573s per stretch: the board's measured ballpark

ETA_RE = re.compile(r"^(about (\d+) min left|nearly done)$")


class RecordingCanvas(MockCanvas):
    """MockCanvas that also logs every text call and every dot-sized
    fill_rect on the dot row, so tests can assert on what was painted and
    in what order."""

    def __init__(self):
        super().__init__()
        self.texts = []
        self.dots = []

    def text(self, x, y, s, color, bg=None, scale=1, font=None):
        self.texts.append((x, y, s, color))
        super().text(x, y, s, color, bg=bg, scale=scale, font=font)

    def fill_rect(self, x, y, w, h, color):
        ds = fa.DerivingScreen.DOT_S
        if (w, h) == (ds, ds) and y == fa.DerivingScreen.DOT_Y - ds // 2:
            self.dots.append((x, y, color))
        super().fill_rect(x, y, w, h, color)


class FakeClock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now


def _eta_texts(canvas):
    out = []
    for _, y, s, _ in canvas.texts:
        if y == fa.DerivingScreen.ETA_Y:
            out.append(s.strip())
    return out


def _run_stretch(canvas, clock, screen, round_ms=RATE_MS, jitter=None):
    """Feed the callback sequence the device shim produces: every STEP
    rounds, then the final (TOTAL, TOTAL). jitter, if given, is a function
    of the callback index returning that interval's per-round ms."""
    screen.draw(None, canvas)
    last = 0
    i = 0
    for done in range(0, TOTAL, STEP):
        ms = round_ms if jitter is None else jitter(i)
        clock.now += (done - last) * ms
        last = done
        i += 1
        screen.update_progress(canvas, done, TOTAL)
    clock.now += (TOTAL - last) * round_ms
    screen.update_progress(canvas, TOTAL, TOTAL)


def test_no_estimate_until_five_percent_of_the_stretch_is_observed(monkeypatch):
    """An ETA computed from the first one percent swings wildly and teaches
    the user to distrust the number; nothing is shown until 5% has been
    timed (about half a minute on the board)."""
    clock = FakeClock()
    monkeypatch.setattr(fa, "ticks_ms", clock)
    canvas = RecordingCanvas()
    screen = fa.DerivingScreen()
    screen.draw(None, canvas)
    threshold = TOTAL * screen.ETA_MIN_PERCENT // 100
    for done in range(0, threshold, STEP):
        clock.now = done * RATE_MS
        screen.update_progress(canvas, done, TOTAL)
    assert _eta_texts(canvas) == [], "estimate shown before the 5% threshold"
    clock.now = (threshold + STEP) * RATE_MS
    screen.update_progress(canvas, threshold + STEP, TOTAL)
    assert _eta_texts(canvas), "estimate still withheld after the threshold"


def test_estimate_is_whole_minutes_rounded_up_never_seconds(monkeypatch):
    """'about 9 min left' is useful; '8m 47s' implies precision the device
    does not have. Rounding is UP: told ten and done in nine is pleasant,
    the reverse is where the plug gets pulled."""
    clock = FakeClock()
    monkeypatch.setattr(fa, "ticks_ms", clock)
    canvas = RecordingCanvas()
    _run_stretch(canvas, clock, fa.DerivingScreen())
    etas = _eta_texts(canvas)
    assert etas, "no estimate ever shown"
    for s in etas:
        assert ETA_RE.match(s), "unexpected estimate wording: %r" % s
    # 280ms/round: 9.03 true minutes remain at the first estimate, so an
    # honest round-up says 10
    first = ETA_RE.match(etas[0])
    assert first.group(2) == "10", "first estimate was %r" % etas[0]


def test_estimate_only_counts_down_with_a_steady_clock(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(fa, "ticks_ms", clock)
    canvas = RecordingCanvas()
    _run_stretch(canvas, clock, fa.DerivingScreen())
    minutes = []
    seen_nearly_done = False
    for s in _eta_texts(canvas):
        m = ETA_RE.match(s)
        if m.group(2) is not None:
            assert not seen_nearly_done, "a number reappeared after 'nearly done'"
            minutes.append(int(m.group(2)))
        else:
            seen_nearly_done = True
    assert minutes == sorted(minutes, reverse=True), (
        "estimate went back up: %r" % minutes)
    assert seen_nearly_done


def test_estimate_does_not_wobble_under_a_jittery_clock(monkeypatch):
    """Per-callback timing on the board is not perfectly uniform (gc pauses,
    the display write itself). +/-20% jitter must not make the displayed
    minutes bounce."""
    clock = FakeClock()
    monkeypatch.setattr(fa, "ticks_ms", clock)
    canvas = RecordingCanvas()
    _run_stretch(canvas, clock, fa.DerivingScreen(),
                 jitter=lambda i: RATE_MS + (56 if i % 2 else -56))
    minutes = [int(m.group(2)) for m in map(ETA_RE.match, _eta_texts(canvas))
               if m and m.group(2) is not None]
    assert minutes == sorted(minutes, reverse=True), (
        "estimate wobbled: %r" % minutes)


def test_the_tail_says_nearly_done_never_one_minute_left(monkeypatch):
    """A countdown that sits at '1 min left' while the tail runs long reads
    as a lie the user can check. The last step is 'nearly done', which also
    honestly covers the EC math after the stretch."""
    clock = FakeClock()
    monkeypatch.setattr(fa, "ticks_ms", clock)
    canvas = RecordingCanvas()
    _run_stretch(canvas, clock, fa.DerivingScreen())
    etas = _eta_texts(canvas)
    assert "about 1 min left" not in etas
    assert etas[-1] == "nearly done"


def test_final_callback_latches_and_later_ones_paint_nothing(monkeypatch):
    """All-types cache hits fire progress(1, 1) after the stretch; if those
    repainted, they would draw over the arrival list."""
    clock = FakeClock()
    monkeypatch.setattr(fa, "ticks_ms", clock)
    canvas = RecordingCanvas()
    screen = fa.DerivingScreen()
    _run_stretch(canvas, clock, screen)
    texts, dots = len(canvas.texts), len(canvas.dots)
    screen.update_progress(canvas, 1, 1)
    screen.update_progress(canvas, TOTAL, TOTAL)
    assert (len(canvas.texts), len(canvas.dots)) == (texts, dots)


def test_the_lit_dot_cycles_one_step_per_update(monkeypatch):
    """The motion element: three dots, one lit, stepping right one place per
    progress callback. Slow and quiet, but visibly alive."""
    clock = FakeClock()
    monkeypatch.setattr(fa, "ticks_ms", clock)
    canvas = RecordingCanvas()
    screen = fa.DerivingScreen()
    screen.draw(None, canvas)
    # the static frame paints all three dots dim, none lit
    assert len(canvas.dots) == 3
    assert all(c[2] == th.BUTTON_BORDER for c in canvas.dots)
    for done in range(0, STEP * 7, STEP):
        clock.now = done * RATE_MS
        screen.update_progress(canvas, done, TOTAL)
    lit = [c[0] for c in canvas.dots if c[2] == th.ACCENT]
    ds = fa.DerivingScreen.DOT_S
    x0 = th.WIDTH // 2 - fa.DerivingScreen.DOT_GAP - ds // 2
    expected = [x0 + fa.DerivingScreen.DOT_GAP * (i % 3) for i in range(7)]
    assert lit == expected, "lit dot did not cycle left-to-right: %r" % lit


def test_single_type_path_never_shows_the_all_types_caption():
    """'each type is quick:' belongs to the all-types arrival list; on the
    single-type derive there is no list and no caption."""
    d.clear_seed_cache()
    app, canvas = App(), RecordingCanvas()
    app.push(S.DerivationPathScreen(M))
    b = next(x for x in app.screen.buttons if "BIP84" in x.label)
    app.handle_tap(canvas, b.x + b.w // 2, b.y + b.h // 2)
    app.handle_tap(canvas, app.screen.derive_button.x + 5,
                   app.screen.derive_button.y + 5)
    assert type(app.screen).__name__ == "AddressDisplayScreen"
    assert not any("each type is quick" in t[2] for t in canvas.texts)
    d.clear_seed_cache()


def test_all_types_caption_appears_once_with_the_arrivals():
    """On the all-types path the caption still exists -- but exactly once,
    and only when the four per-type rows actually start landing, not
    dangling under the percentage for the whole stretch."""
    d.clear_seed_cache()
    app, canvas = App(), RecordingCanvas()
    app.push(S.DerivationPathScreen(M))
    b = next(x for x in app.screen.buttons if "All Types" in x.label)
    app.handle_tap(canvas, b.x + b.w // 2, b.y + b.h // 2)
    app.handle_tap(canvas, app.screen.derive_button.x + 5,
                   app.screen.derive_button.y + 5)
    assert type(app.screen).__name__ == "AllAddressesScreen"
    captions = [i for i, t in enumerate(canvas.texts)
                if "each type is quick" in t[2]]
    assert len(captions) == 1
    rows = [i for i, t in enumerate(canvas.texts)
            if t[2].startswith("BIP") and t[3] == th.GOOD]
    assert len(rows) == 4
    assert captions[0] < min(rows), "caption painted after the arrival rows"
    d.clear_seed_cache()


def test_device_shim_reports_at_least_every_sixteen_rounds():
    """The motion above can only move when the PBKDF2 loop calls back, so
    the shim's cadence IS the animation's frame interval: 16 rounds is
    ~4.5s on the board. Loaded by file path so mp_shims/ never lands on
    sys.path (see tests/test_mp_shims.py for why that rule exists)."""
    spec = importlib.util.spec_from_file_location(
        "_pbkdf2_shim_cadence", ROOT / "device" / "mp_shims" / "pbkdf2.py")
    shim = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(shim)
    seen = []
    shim.progress_cb = lambda done, total: seen.append((done, total))
    shim.pbkdf2_hmac("sha512", b"password", b"salt", 64, 32)
    dones = [x[0] for x in seen]
    assert dones == sorted(dones)
    assert seen[-1] == (64, 64)
    gaps = [b - a for a, b in zip(dones, dones[1:])]
    assert gaps and max(gaps) <= 16, (
        "progress cadence regressed: gaps %r" % gaps)
