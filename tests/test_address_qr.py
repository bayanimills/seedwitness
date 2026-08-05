"""The address QR must be trivially reachable, and the seed QR must not be.

A receive address is public -- it is the thing you hand a payer -- so its QR
opens on a plain tap, sits on the navigation stack like any other screen,
and stays up until dismissed. The seed QR keeps its warning screen, 900ms
hold and auto-blank. These tests pin BOTH directions: the gate stays where
the secret is, and only there. A gate on the address QR would be security
theatre that trains users to hold through warnings; a missing gate on the
seed QR is a lost wallet.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "device"))
sys.path.insert(0, str(ROOT / "sim"))

import pytest  # noqa: E402
import zxingcpp  # noqa: E402
from mock_canvas import MockCanvas  # noqa: E402

from seedwitness.ui import fonts  # noqa: E402
from seedwitness.ui import screens as S  # noqa: E402
from seedwitness.ui import theme as th  # noqa: E402
from seedwitness.ui.app import App  # noqa: E402

MNEMONIC = (
    "abandon abandon abandon abandon abandon abandon abandon abandon "
    "abandon abandon abandon about"
)

# The published first receive addresses for the canonical test mnemonic --
# one per script type, covering both encodings: bech32 (lowercase, wants
# alphanumeric mode) and base58 (mixed case, must stay byte mode).
ADDRESSES = {
    44: "1LqBGSKuX5yYUonjxT5qGfpUsXKYYWeabA",
    49: "37VucYSaXLCAsxYyAPfbSi9eh4iEcbShgf",
    84: "bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu",
    86: "bc1p5cyxnuxmeuwuvkwfem96lqzszd02n6xdcjrs20cac6yqjjwudpxqkedrcr",
}


class RecordingCanvas(MockCanvas):
    def __init__(self, hold_for=None):
        super().__init__()
        self.events = []
        self.hold_for = hold_for
        self.polls = 0

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


def _panel(canvas):
    """The big light square a QR sits on."""
    panels = [e for e in canvas.events
              if e[0] == "rect" and e[5] == th.FG and e[3] >= 100 and e[3] == e[4]]
    return panels[0] if panels else None


def _bip(n):
    return next(t for t in S.drv.PATH_TEMPLATES if t.bip == n)


def _display_screen(bip=84):
    app = App()
    screen = S.AddressDisplayScreen(MNEMONIC, _bip(bip), 0,
                                    "m/%dh/0h/0h/0/0" % bip, ADDRESSES[bip])
    app.push(screen)
    return app, screen


# ---------------------------------------------------------------------------
# Not gated: a plain tap opens it, and it stays until dismissed

def test_a_plain_tap_opens_the_address_qr():
    """A press with the finger lifted immediately -- the exact gesture the
    seed QR must reject -- opens the address QR. If this ever needs a hold,
    the routine action has been dressed in the secret's ceremony."""
    app, screen = _display_screen()
    canvas = RecordingCanvas(hold_for=0)     # finger already up
    b = next(b for b in screen.buttons if b.label == "QR")
    screen.handle_tap(app, canvas, b.x + b.w // 2, b.y + b.h // 2)
    assert type(app.screen).__name__ == "AddressQRScreen"


def test_address_qr_lives_on_the_stack_and_stays_until_dismissed():
    """Unlike the seed QR (drawn inside the tap, blanked before it returns),
    the address QR is an ordinary pushed screen: a redraw shows the same
    symbol again, and only Back removes it."""
    app, screen = _display_screen()
    canvas = RecordingCanvas()
    b = next(b for b in screen.buttons if b.label == "QR")
    screen.handle_tap(app, canvas, b.x + b.w // 2, b.y + b.h // 2)
    qr_screen = app.screen
    for _ in range(2):                      # still there on every repaint
        canvas = RecordingCanvas()
        qr_screen.draw(app, canvas)
        assert _panel(canvas) is not None
    assert not hasattr(qr_screen, "SHOW_MS"), "an auto-blank crept in"
    app.pop()
    assert app.screen is screen


def test_gating_contrast_is_pinned():
    """The whole point in one assertion each: address and xpub QR screens
    act on plain taps, the seed QR keeps the longest hold in the app."""
    assert S.AddressDisplayScreen.SWEEP_MS == 0
    assert S.AddressQRScreen.SWEEP_MS == 0
    assert S.AccountQRScreen.SWEEP_MS == 0
    assert S.QRExportScreen.SWEEP_MS >= 900


# ---------------------------------------------------------------------------
# The symbol itself

@pytest.mark.parametrize("bip", [44, 49, 84, 86])
def test_the_screen_pixels_decode_to_the_address(bip):
    """Render the whole screen through the sim canvas and hand it to the
    independent decoder. bech32 is encoded uppercase (alphanumeric mode,
    case-insensitive by construction); base58 must come back byte-exact."""
    addr = ADDRESSES[bip]
    screen = S.AddressQRScreen(addr, "m/%dh/0h/0h/0/0" % bip)
    canvas = MockCanvas()
    screen.draw(App(), canvas)
    img = canvas.image.resize((canvas.width * 3, canvas.height * 3))
    res = zxingcpp.read_barcode(img)
    assert res is not None, "the on-screen frame did not decode"
    if addr == addr.lower():
        assert res.text == addr.upper()
    else:
        assert res.text == addr


@pytest.mark.parametrize("bip", [44, 49, 84, 86])
def test_symbol_fits_the_panel_with_quiet_zone_and_scannable_modules(bip):
    screen = S.AddressQRScreen(ADDRESSES[bip], "m/%dh/0h/0h/0/0" % bip)
    canvas = RecordingCanvas()
    screen.draw(App(), canvas)
    panel = _panel(canvas)
    assert panel is not None
    _, x0, y0, side, _, _ = panel
    px = side // (screen.size + 2 * screen.QUIET)
    assert px >= 4, "modules under 4px will not scan reliably"
    assert x0 >= 0 and x0 + side <= th.WIDTH
    assert y0 >= 0 and y0 + side <= th.HEIGHT


@pytest.mark.parametrize("bip", [44, 49, 84, 86])
def test_the_address_text_stays_on_screen_with_the_qr(bip):
    """Comparing the text against the wallet is the actual job; the QR is a
    convenience on top. The full address must render on the QR screen, in
    the same 4-char groups as the comparison screen."""
    addr = ADDRESSES[bip]
    screen = S.AddressQRScreen(addr, "m/%dh/0h/0h/0/0" % bip)
    canvas = RecordingCanvas()
    screen.draw(App(), canvas)
    chunks = [e[3] for e in canvas.events
              if e[0] == "text" and e[5] is S.AddressQRScreen.ADDR_FONT]
    assert addr in "".join(chunks), "address not rendered in full beside the QR"


@pytest.mark.parametrize("bip", [44, 49, 84, 86])
def test_nothing_on_the_qr_screen_clips(bip):
    screen = S.AddressQRScreen(ADDRESSES[bip], "m/%dh/0h/0h/0/0" % bip)
    canvas = RecordingCanvas()
    screen.draw(App(), canvas)
    for e in canvas.events:
        if e[0] != "text":
            continue
        _, x, y, s, scale, font = e
        cell = font.width if font is not None else th.CHAR_W * scale
        cell_h = font.height if font is not None else th.CHAR_H * scale
        assert x + len(s) * cell <= th.WIDTH, "clipped right: %r" % s
        assert y + cell_h <= th.HEIGHT, "clipped bottom: %r" % s


def test_display_screen_keeps_its_grouped_text_view():
    """Adding the QR button must not cost the text view its job: the full
    address still renders on the display screen in alternating groups."""
    app, screen = _display_screen(86)       # the longest address
    canvas = RecordingCanvas()
    screen.draw(app, canvas)
    # Filter by position rather than face: a 62-character taproot address
    # legitimately steps down to a smaller face so it fits above the buttons
    # instead of being drawn under them and truncated. Pinning the face here
    # would make this test blind to the whole address body.
    button_top = min([b.y for b in screen.buttons] or [320])
    chunks = [e[3] for e in canvas.events
              if e[0] == "text" and S.HEADER_H + 40 < e[2] < button_top]
    assert ADDRESSES[86] in "".join(chunks)
