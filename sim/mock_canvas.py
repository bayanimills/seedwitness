"""Desktop stand-in for HardwareCanvas (device/seedwitness/ui/canvas.py),
backed by PIL instead of the real ili9341/xpt2046 drivers. Implements the
identical method set so screens.py runs completely unmodified against either
-- screens.py never imports this file or the real one; both are handed to it
by whatever's driving the app (device/main.py for real hardware, this
package for desktop testing/screenshots).

Text fidelity: every glyph is rasterised from the SAME packed bitmap data
the device draws --

  * font=<Font>: the Spleen faces in device/seedwitness/ui/fonts/, the
    identical bytes the device blits via Font.get_letter().
  * font=None (built-in 8x8): sim/font_petme128_8x8.py, a generated copy of
    the exact glyph table compiled into MicroPython's framebuf (see
    tools/gen_fonts.py). This mock previously substituted a TrueType face
    at approximately the right size, which made every screenshot a lie
    about glyph shape and ink coverage -- the same class of sim-versus-
    hardware divergence that hid the black-box-behind-text bug.

There is no anti-aliasing anywhere because the hardware has none.

Also provides tap injection (tap_at) for scripted, deterministic screenshot
walkthroughs -- the CYD equivalent of the Pi build's synthetic Button presses.
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "device"))
from seedwitness.ui import theme as th  # noqa: E402

import font_petme128_8x8 as _petme  # noqa: E402  (sim-local generated module)


class MockCanvas:
    def __init__(self, width=th.WIDTH, height=th.HEIGHT):
        self.width = width
        self.height = height
        self.image = Image.new("RGB", (width, height), th.BG)
        self.draw = ImageDraw.Draw(self.image)
        self._pending_tap = None

    def fill(self, color):
        self.draw.rectangle([0, 0, self.width, self.height], fill=color)

    def fill_rect(self, x, y, w, h, color):
        self.draw.rectangle([x, y, x + w - 1, y + h - 1], fill=color)

    def rect(self, x, y, w, h, color):
        self.draw.rectangle([x, y, x + w - 1, y + h - 1], outline=color)

    def set_backlight(self, on):
        """No-op: there is no backlight to switch in the sim. Present so
        app.py can call it unconditionally and the two canvases keep the
        same surface."""

    def fill_circle(self, cx, cy, r, color):
        """Filled circle, via theme.circle_spans so this canvas and the
        other one produce the same pixels. Do not reimplement with a
        native primitive: see circle_spans for why."""
        for x, y, w in th.circle_spans(cx, cy, r):
            self.fill_rect(x, y, w, 1, color)

    def blit_mask(self, x, y, w, h, data, color, bg):
        """A packed 1-bit bitmap: set bits in `color`, clear bits in `bg`,
        always painting the full w x h window -- the same contract as
        HardwareCanvas.blit_mask. Which pixels are set comes from
        theme.mask_row_spans, the one shared definition, so the sim and the
        panel cannot drift; checked by the MASK golden-frame case."""
        if x < 0 or y < 0 or x + w > self.width or y + h > self.height:
            return  # same refusal as the hardware path
        self.fill_rect(x, y, w, h, bg)
        for yy in range(h):
            for sx, run in th.mask_row_spans(data, w, yy):
                self.fill_rect(x + sx, y + yy, run, 1, color)

    def hline(self, x, y, w, color):
        # Inclusive endpoint, matching rect() above and HardwareCanvas.hline()
        # -- see canvas.py for the hardware bug the old x + w form caused.
        self.draw.line([x, y, x + w - 1, y], fill=color)

    def text(self, x, y, s, color, bg=None, scale=1, font=None):
        # Same clamp as HardwareCanvas.text() (canvas.py), for true interface
        # parity: PIL would otherwise just silently clip off-canvas text,
        # which masked a real overflow bug during development (the real
        # ili9341 driver doesn't clip safely -- see canvas.py's docstring).
        cw = font.width if font is not None else th.CHAR_W * scale
        chh = font.height if font is not None else th.CHAR_H * scale
        max_chars = max(0, (self.width - x) // cw)
        s = s[:max_chars]
        if not s:
            return
        # Same vertical guard as the hardware paths: block() has no bounds
        # check, so canvas.py refuses to draw a line that would run off the
        # top or bottom rather than corrupt the panel. Refuse identically.
        if y < 0 or y + chh > self.height:
            return
        # Text is NEVER transparent on the device: both the built-in-font
        # path and Font.get_letter() blit the whole glyph cell, background
        # included, and that background defaults to black. Defaulting to
        # black here keeps the sim honest, so call sites that need to blend
        # have to say so.
        self.draw.rectangle([x, y, x + cw * len(s) - 1, y + chh - 1],
                            fill=(bg if bg is not None else th.BG))
        if font is not None:
            self._blit_font(x, y, s, color, font)
        else:
            self._blit_8x8(x, y, s, color, scale)

    def _blit_font(self, x, y, s, color, font):
        """Rasterise from the same packed rows the device blits. A char the
        face doesn't cover stays a blank cell -- matching Font.get_letter()."""
        rb = font.row_bytes
        shift = rb * 8 - 1
        pt = self.draw.point
        for i, ch in enumerate(s):
            g = font.rows(ch)
            if g is None:
                continue
            ox = x + i * font.width
            for yy in range(font.height):
                bits = int.from_bytes(g[yy * rb:(yy + 1) * rb], "big")
                if not bits:
                    continue
                for xx in range(font.width):
                    if (bits >> (shift - xx)) & 1:
                        pt((ox + xx, y + yy), fill=color)

    def _blit_8x8(self, x, y, s, color, scale):
        """MicroPython framebuf's own font, nearest-neighbour scaled exactly
        as HardwareCanvas._text_scaled() does. petme128 layout: 8 bytes per
        char, each byte one column, bit 0 = top pixel; chars outside 32..127
        render as char 127, matching framebuf's C fallback."""
        rect = self.draw.rectangle
        pt = self.draw.point
        for i, ch in enumerate(s):
            cp = ord(ch)
            if cp < _petme.FIRST or cp > _petme.LAST:
                cp = _petme.LAST
            o = (cp - _petme.FIRST) * 8
            cols = _petme.DATA[o:o + 8]
            ox = x + i * 8 * scale
            for cx in range(8):
                col = cols[cx]
                if not col:
                    continue
                for cy in range(8):
                    if (col >> cy) & 1:
                        px = ox + cx * scale
                        py = y + cy * scale
                        if scale == 1:
                            pt((px, py), fill=color)
                        else:
                            rect([px, py, px + scale - 1, py + scale - 1],
                                 fill=color)

    def present(self):
        pass

    # -- test/screenshot support, not part of the real Canvas interface --

    def tap_at(self, x, y):
        """Queue a synthetic tap; the next get_tap() call returns it once."""
        self._pending_tap = (x, y)

    # Screens that run a hold-to-confirm gesture read these. A synthetic press
    # is treated as held for as long as the gesture needs, and hold_ms=0 means
    # the sweep is still drawn (so screenshots show it) without sleeping
    # through a 50-roll ceremony.
    hold_ms = 0

    def touch_active(self):
        return True

    def wait_release(self):
        # True, matching HardwareCanvas's contract: a synthetic finger lifts
        # the instant it is asked to. Returning None here would read as "the
        # release timed out" to _main_loop's release-edge check and hang it.
        return True

    def get_press(self):
        return self.get_tap()

    def get_tap(self):
        point = self._pending_tap
        self._pending_tap = None
        return point

    def save(self, path):
        self.image.save(path)
