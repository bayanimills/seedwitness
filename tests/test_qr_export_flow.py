"""The seed QR must be impossible to reach by accident.

A seed QR on screen is a machine-readable broadcast of the entire wallet, so
DEVPLAN 4.1 gates it three ways: an explicit warning screen, hold-to-reveal
rather than tap, and an auto-blank timeout. These tests hold the screen to
all three -- most importantly that a plain tap (a press withdrawn before the
sweep closes) never draws a single QR module, and that the QR is never
pushed onto the navigation stack where Back or a redraw could resurrect it.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "device"))
sys.path.insert(0, str(ROOT / "sim"))

from mock_canvas import MockCanvas  # noqa: E402

from seedwitness.ui import screens as S  # noqa: E402
from seedwitness.ui import theme as th  # noqa: E402
from seedwitness.ui.app import App  # noqa: E402

MNEMONIC = (
    "abandon abandon abandon abandon abandon abandon abandon abandon "
    "abandon abandon abandon about"
)
MNEMONIC24 = ("abandon " * 23 + "art").strip()


class RecordingCanvas(MockCanvas):
    """Records draw calls in order, so a test can ask not just whether the
    QR appeared but what happened after it."""

    def __init__(self, hold_for=None):
        super().__init__()
        self.events = []
        self.hold_for = hold_for
        self.polls = 0

    def fill(self, color):
        self.events.append(("fill", color))
        return super().fill(color)

    def fill_rect(self, x, y, w, h, color):
        self.events.append(("rect", x, y, w, h, color))
        return super().fill_rect(x, y, w, h, color)

    def text(self, x, y, s, color, bg=None, scale=1, font=None):
        self.events.append(("text", x, y, s, scale, font))
        return super().text(x, y, s, color, bg=bg, scale=scale, font=font)

    def touch_active(self):
        if self.hold_for is None:
            return True
        self.polls += 1
        return self.polls <= self.hold_for


def _panel_events(canvas):
    """The big light square the QR sits on: nothing else on this device
    draws a large white filled rect."""
    return [e for e in canvas.events
            if e[0] == "rect" and e[5] == th.FG and e[3] >= 100 and e[3] == e[4]]


def _centre(b):
    return b.x + b.w // 2, b.y + b.h // 2


def _export_screen(mnemonic=MNEMONIC):
    app = App()
    app.last_mnemonic = mnemonic
    screen = S.QRExportScreen(mnemonic)
    app.push(screen)
    return app, screen


# ---------------------------------------------------------------------------
# The gate

def test_plain_tap_never_reveals_the_qr():
    """A press withdrawn before the sweep closes must not draw one module.
    This is the bypass test: tap-instead-of-hold is exactly how a QR would
    leak in front of the wrong camera."""
    for label in ("Compact QR", "Standard QR"):
        app, screen = _export_screen()
        canvas = RecordingCanvas(hold_for=1)    # finger lifts immediately
        b = next(b for b in screen.buttons if b.label == label)
        screen.handle_tap(app, canvas, *_centre(b))
        assert not _panel_events(canvas), "%s revealed on a plain tap" % label


def test_held_press_reveals_the_qr():
    app, screen = _export_screen()
    canvas = RecordingCanvas()                  # held for the whole sweep
    screen.handle_tap(app, canvas, *_centre(screen.compact_btn))
    assert _panel_events(canvas), "a full hold never drew the QR"


def test_reveal_pushes_nothing_onto_the_stack():
    """The QR is drawn inside the tap and blanked before it returns. If it
    lived on the stack, Back or any redraw could resurrect it without the
    hold gate."""
    app, screen = _export_screen()
    depth = len(app.stack)
    screen.handle_tap(app, RecordingCanvas(), *_centre(screen.compact_btn))
    assert app.screen is screen
    assert len(app.stack) == depth


def test_qr_is_blanked_after_reveal():
    """Auto-blank: once the reveal returns, the warning screen has been
    repainted over the QR -- a full background fill after the panel."""
    app, screen = _export_screen()
    canvas = RecordingCanvas()
    screen.handle_tap(app, canvas, *_centre(screen.compact_btn))
    panel_i = canvas.events.index(_panel_events(canvas)[0])
    later_fills = [i for i, e in enumerate(canvas.events)
                   if e[0] == "fill" and e[1] == th.BG and i > panel_i]
    assert later_fills, "nothing blanked the QR after it was revealed"


def test_back_is_a_plain_tap_and_leaves():
    """Leaving must always be easier than revealing: the corner Back needs
    no hold even though everything else on this screen does."""
    app, screen = _export_screen()
    depth = len(app.stack)
    canvas = RecordingCanvas(hold_for=1)        # would fail any hold gate
    screen.handle_tap(app, canvas, th.WIDTH - 10, 10)
    assert len(app.stack) < depth
    assert not _panel_events(canvas)


def test_reveal_hold_is_the_longest_in_the_app():
    """Revealing the finished wallet must take a longer deliberate hold
    than anything else, including a dice roll."""
    assert S.QRExportScreen.SWEEP_MS > S.RollEntryScreen.SWEEP_MS
    assert S.QRExportScreen.SWEEP_MS > S.MenuScreen.SWEEP_MS


def test_auto_blank_timeout_is_short():
    """Long enough to aim a camera at it, far too short to walk away from."""
    assert 5000 <= S.QRExportScreen.SHOW_MS <= 30000


def test_warning_screen_says_there_is_no_scan_in_path():
    """The board has no camera; a QR feature otherwise implies import
    exists. The warning screen must close that expectation off."""
    app, screen = _export_screen()
    canvas = RecordingCanvas()
    screen.draw(app, canvas)
    drawn = " ".join(e[3] for e in canvas.events if e[0] == "text")
    assert "no camera" in drawn.lower()
    assert "camera" in drawn.lower() and "steal" in drawn.lower(), \
        "the warning itself is missing"


# ---------------------------------------------------------------------------
# The entry point

def test_ceremony_complete_offers_export():
    screen = S.GenerateCompleteScreen(MNEMONIC)
    labels = [b.label for b in screen.buttons]
    assert "Export QR" in labels


def test_export_entry_carries_the_ceremony_mnemonic():
    app = App()
    app.push(S.GenerateCompleteScreen(MNEMONIC))
    b = next(b for b in app.screen.buttons if b.label == "Export QR")
    b.on_tap(app)
    assert type(app.screen).__name__ == "QRExportScreen"
    assert app.screen.mnemonic == MNEMONIC


# ---------------------------------------------------------------------------
# The revealed frame itself

def _frame(mnemonic, compact):
    from seedwitness import mnemonic as mn
    from seedwitness import qr
    app, screen = _export_screen(mnemonic)
    if compact:
        size, mat = qr.compact_seedqr(mn.mnemonic_to_entropy(mnemonic))
    else:
        size, mat = qr.standard_seedqr(mn.word_indices(mnemonic))
    canvas = RecordingCanvas()
    screen._draw_qr_frame(canvas, size, mat, compact)
    return screen, size, mat, canvas


def test_qr_frame_keeps_the_mandatory_quiet_zone():
    """Four light modules minimum on every side. A QR drawn edge to edge
    often will not scan; the quiet zone is part of the symbol."""
    for mnemonic, compact in ((MNEMONIC, True), (MNEMONIC24, True),
                              (MNEMONIC, False), (MNEMONIC24, False)):
        screen, size, mat, canvas = _frame(mnemonic, compact)
        panel = _panel_events(canvas)[0]
        _, px0, py0, pw, ph, _ = panel
        px = pw // (size + 2 * screen.QUIET)
        assert px >= 4, "modules under 4px will not scan reliably"
        assert px0 >= 0 and px0 + pw <= th.WIDTH
        assert py0 >= 0 and py0 + ph <= th.HEIGHT
        # every dark module drawn after the panel must sit at least QUIET
        # modules inside it
        for e in canvas.events[canvas.events.index(panel) + 1:]:
            if e[0] == "rect" and e[5] == th.BG and e[2] >= py0 and \
                    e[2] < py0 + ph and e[1] >= px0:
                assert e[1] >= px0 + screen.QUIET * px
                assert e[1] + e[3] <= px0 + pw - screen.QUIET * px
                assert e[2] >= py0 + screen.QUIET * px
                assert e[2] + e[4] <= py0 + ph - screen.QUIET * px


def test_qr_frame_draws_exactly_the_matrix():
    """Reconstruct the module grid from the recorded fill_rects and require
    it to equal the encoder's matrix. Between the encoder round-trip tests
    and this, screen-to-scanner is covered end to end."""
    for compact in (True, False):
        screen, size, mat, canvas = _frame(MNEMONIC, compact)
        panel = _panel_events(canvas)[0]
        _, px0, py0, pw, _, _ = panel
        px = pw // (size + 2 * screen.QUIET)
        ox = px0 + screen.QUIET * px
        oy = py0 + screen.QUIET * px
        got = bytearray(size * size)
        for e in canvas.events[canvas.events.index(panel) + 1:]:
            if e[0] != "rect" or e[5] != th.BG:
                continue
            x, y, w, h = e[1], e[2], e[3], e[4]
            if not (ox <= x and oy <= y and y + h <= oy + size * px):
                continue
            if h != px or (x - ox) % px or (y - oy) % px or w % px:
                continue
            r = (y - oy) // px
            for c in range((x - ox) // px, (x - ox) // px + w // px):
                got[r * size + c] = 1
        assert bytes(got) == bytes(mat), \
            "the drawn modules differ from the encoded matrix"


def test_the_actual_screen_pixels_decode_to_the_wallet():
    """The end of the chain: render the reveal frame through the sim canvas
    -- the same pixels the panel shows -- and hand the whole screen image to
    an independent decoder. This catches anything between the matrix and
    the glass: module size, quiet zone, colours, stray overlays."""
    import zxingcpp
    from seedwitness import mnemonic as mn
    from seedwitness import qr

    for mnemonic in (MNEMONIC, MNEMONIC24):
        entropy = mn.mnemonic_to_entropy(mnemonic)
        app, screen = _export_screen(mnemonic)
        canvas = MockCanvas()
        size, mat = qr.compact_seedqr(entropy)
        screen._draw_qr_frame(canvas, size, mat, compact=True)
        # a phone camera sees the panel bigger than 240px; give the decoder
        # the same benefit
        img = canvas.image.resize((canvas.width * 3, canvas.height * 3))
        res = zxingcpp.read_barcode(img)
        assert res is not None, "the on-screen frame did not decode"
        assert res.bytes == entropy

        size, mat = qr.standard_seedqr(mn.word_indices(mnemonic))
        screen._draw_qr_frame(canvas, size, mat, compact=False)
        img = canvas.image.resize((canvas.width * 3, canvas.height * 3))
        res = zxingcpp.read_barcode(img)
        assert res is not None
        assert res.text == "".join("%04d" % i for i in mn.word_indices(mnemonic))


def test_qr_frame_text_fits_the_screen():
    for compact in (True, False):
        _, _, _, canvas = _frame(MNEMONIC24, compact)
        for e in canvas.events:
            if e[0] != "text":
                continue
            _, x, y, s, scale, font = e
            cell = font.width if font is not None else th.CHAR_W * scale
            assert x + len(s) * cell <= th.WIDTH, "clipped: %r" % s
