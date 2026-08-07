"""Pin the factual claims the device makes about itself.

Three of these were wrong at once, and all three were the kind of wrong that
matters on a device whose entire pitch is that its statements can be checked:

  1. The crash screen said "Nothing was written to flash." The crash handler
     calls diag.record_crash() seven lines before that screen is built, which
     writes /diag.txt. The breadcrumb is deliberately secret-free, so the
     honest sentence is about secrets, not about flash.
  2. The same screen said a seed had been "cleared". SECURITY.md is explicit
     that references are dropped and objects are not overwritten, and that
     garbage collection is not secure zeroisation. "Cleared" promises the
     stronger thing on the one screen a user reads to find out what
     protection they just got.
  3. The checksum screen quoted "15 of every 16" for every seed length. That
     is the 12-word figure. A 24-word seed carries 8 checksum bits, not 4,
     and catches 255 of 256.

None of these were pinned by a test, which is why they drifted.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "device"))
sys.path.insert(0, str(ROOT / "sim"))

from mock_canvas import MockCanvas  # noqa: E402
from seedwitness import mnemonic as mn  # noqa: E402
from seedwitness.ui import fonts, theme as th  # noqa: E402
from seedwitness.ui import flow_generate as FG  # noqa: E402
from seedwitness.ui import screens as S  # noqa: E402
from seedwitness.ui.app import App  # noqa: E402


class Recording(MockCanvas):
    def __init__(self):
        super().__init__()
        self.drawn = []

    def text(self, x, y, s, color, bg=None, scale=1, font=None):
        self.drawn.append((x, str(s), font))
        return super().text(x, y, s, color, bg=bg, scale=scale, font=font)


def _render(screen):
    c = Recording()
    screen.draw(App(), c)
    return c


def _text(canvas):
    return " ".join(s for _, s, _ in canvas.drawn)


def test_the_crash_screen_does_not_claim_flash_was_untouched():
    """diag.record_crash() writes /diag.txt on this exact path."""
    body = _text(_render(S.CrashScreen(ValueError("x"), True)))
    assert "Nothing was written to flash" not in body
    assert "No secret was written" in body


def test_the_crash_screen_does_not_promise_zeroisation():
    """SECURITY.md: references are dropped, memory is not overwritten."""
    body = _text(_render(S.CrashScreen(ValueError("x"), True)))
    assert "has been cleared" not in body
    assert "dropped" in body


def test_the_uncleared_crash_path_still_says_power_off():
    """The dangerous branch must keep its instruction."""
    body = _text(_render(S.CrashScreen(ValueError("x"), False)))
    assert "could NOT be cleared" in body
    assert "Power the device off" in body


def test_the_checksum_figure_matches_the_seed_length():
    for entropy_bytes, expected in ((16, "15 of every 16"),
                                    (32, "255 of every 256")):
        m = mn.entropy_to_mnemonic(bytes(entropy_bytes))
        body = _text(_render(FG.ChecksumWordScreen(m)))
        assert expected in body, (entropy_bytes, body)


def test_no_line_on_these_screens_runs_off_the_panel():
    """Two strings were drawn one pixel past the right edge and silently lost
    their last character. Measured from the recorded draw calls, not assumed:
    both screens sit outside tests/test_no_text_clipping.py's sweep."""
    m = mn.entropy_to_mnemonic(bytes(16))
    for name, screen in (("crash-cleared", S.CrashScreen(ValueError("x"), True)),
                         ("crash-uncleared", S.CrashScreen(ValueError("x"), False)),
                         ("checksum", FG.ChecksumWordScreen(m))):
        for x, s, font in _render(screen).drawn:
            width = (font.width if font is not None else th.CHAR_W)
            right = x + len(s) * width
            assert right <= th.WIDTH, (
                "%s: %r ends at %dpx on a %dpx panel" % (name, s, right, th.WIDTH))
