"""Thin adapter between screens.py's drawing calls and the real ili9341/
xpt2046 drivers -- the on-device half of the Canvas interface. sim/
mock_canvas.py implements the identical method set backed by PIL, so
screens.py runs unmodified against either.

Text comes in two kinds (see ui/fonts/__init__.py for the full type scale):

  * font=<Font>: a real bitmap face (Spleen), rendered through the vendored
    driver's draw_text()/draw_letter() with Font.get_letter() supplying
    RGB565 glyph cells. This is what all primary content uses -- a 12x24
    glyph drawn as 12x24 pixels of actual letterform detail.
  * font=None: MicroPython framebuf's built-in 8x8 (drivers/ili9341.py's
    draw_text8x8), for dense secondary text. The `scale` multiplier
    nearest-neighbour doubles it and is kept only as the layout floor and
    for legacy call sites; scaled 8x8 adds size without detail, which is
    exactly the blocky look the bitmap faces exist to replace.

At 1x an 8px glyph is about 1.4mm tall on this 2.8" panel -- fine for
footnotes, too small to eyeball a bech32 address against another device
with confidence; the 12x24 face is ~4mm and reads at arm's length.
"""
from framebuf import RGB565, FrameBuffer

from ..drivers.ili9341 import color565
from . import theme as th


try:  # MicroPython
    from time import sleep_ms as _sleep_ms
except ImportError:  # desktop CPython
    from time import sleep as _sleep

    def _sleep_ms(ms):
        _sleep(ms / 1000.0)


def _swap565(c):
    """framebuf stores RGB565 byte-swapped relative to what the panel wants --
    the vendored draw_text8x8 does this same swap for exactly this reason."""
    return ((c & 0xFF) << 8) | ((c & 0xFF00) >> 8)


class HardwareCanvas:
    def __init__(self, display, touch):
        self.display = display
        self.touch = touch
        self.width = display.width
        self.height = display.height
        # Scratch buffers for scaled text, allocated once and grown on demand
        # rather than per call. Free RAM on this board is only ~26KB with the
        # UI and embit both imported, so repeatedly allocating a few KB per
        # string is a real fragmentation risk (Display.clear()'s own 5120-byte
        # line buffer has been seen to fail under exactly that pressure).
        self._src = None
        self._row = None

    # Rotate everything 180 degrees in software.
    #
    # The panel only addresses its full 320x240 area at MADCTL rotation=90,
    # which puts the USB/power edge at the BOTTOM -- so the cable runs into
    # the hand holding the device. rotation=270 is the orientation we want,
    # but this panel mishandles it: measured with an on-screen ruler from a
    # freshly reset board (single Display init, nothing else having touched
    # MADCTL), the X axis stops around 240 instead of 320 and the bottom 80
    # rows are never written at all, leaving stale pixels from earlier frames.
    # None of the four landscape MADCTL combinations gives both correct
    # geometry and upright text: the two mirrored ones render text reversed.
    #
    # So the display stays at the geometry that works and the drawing is
    # rotated instead. Every primitive maps (x, y) -> (W-1-x, H-1-y), and text
    # uses the vendored driver's own rotate=180 support, which rotates the
    # whole rendered string bitmap (not each glyph individually) and therefore
    # reads correctly the right way up.
    #
    # DISABLED. This existed to work around the landscape problem described
    # above, back when the UI was 320x240 and the only rotation that addressed
    # the full panel put the cable in the user's hands. The UI is now portrait
    # (240x320), the panel's native orientation, where rotation=0 and 180 are
    # both well behaved -- so a 180-degree turn is one constant in cyd.py and
    # needs no software rotation at all.
    #
    # Kept rather than deleted because it is the only correct way to flip if a
    # future panel misbehaves the same way, and getting it right is subtle:
    # the drawing flip and the touch flip must BOTH be applied or taps land
    # point-symmetric, and that failure is invisible until someone presses a
    # button. Setting this True re-enables both halves together.
    FLIP_180 = False

    def fill(self, color):
        self.display.clear(color565(*color))  # whole screen: flip is a no-op

    def fill_rect(self, x, y, w, h, color):
        if self.FLIP_180:
            x = self.width - x - w
            y = self.height - y - h
        self.display.fill_rectangle(x, y, w, h, color565(*color))

    def rect(self, x, y, w, h, color):
        if self.FLIP_180:
            x = self.width - x - w
            y = self.height - y - h
        self.display.draw_rectangle(x, y, w, h, color565(*color))

    def set_backlight(self, on):
        """Idle blanking. Kills the backlight rather than painting black:
        an unlit panel is unambiguous, costs one pin write, and needs no
        full-screen repaint (which is ~0.5s here and would itself look
        like a fault)."""
        # No getattr default: init_display always attaches this, so a missing
        # attribute means the display was constructed some other way and the
        # caller is about to believe it blanked a panel that is still lit.
        self.display.backlight.value(1 if on else 0)

    def fill_circle(self, cx, cy, r, color):
        """Filled circle, via theme.circle_spans so this canvas and the
        other one produce the same pixels. Do not reimplement with a
        native primitive: see circle_spans for why."""
        for x, y, w in th.circle_spans(cx, cy, r):
            self.fill_rect(x, y, w, 1, color)

    def blit_mask(self, x, y, w, h, data, color, bg):
        """A packed 1-bit bitmap (see theme.mask_row_spans for the layout):
        set bits in `color`, clear bits in `bg`. Always paints the full
        w x h window -- like text, a mask blit is never transparent here.

        Which pixels are set is decided by theme.mask_row_spans, shared with
        MockCanvas, so the two canvases cannot disagree about the shape;
        this method only chooses HOW to push the spans to the panel: one
        RGB565 row at a time through block(), which costs one driver call
        per row instead of one per span (the splash wordmark has ~30 spans
        per row, and at ~3ms per driver call that difference is a splash
        that appears in ~150ms rather than ~3s). Verified against the sim
        by the MASK golden-frame case.

        Allocation: two w*2-byte row buffers, transient. Drawn once at
        boot, where the heap is at its most coherent.

        No FLIP_180 support: the flag is disabled (see its note above) and
        this primitive draws only the boot splash; refuse loudly rather
        than render a wordmark mirror-imaged.
        """
        assert not self.FLIP_180, "blit_mask does not implement FLIP_180"
        if x < 0 or y < 0 or x + w > self.width or y + h > self.height:
            return  # block() has no bounds check; refuse like _text_scaled
        fg = color565(*color)
        bgc = color565(*bg)
        fg_pat = bytes((fg >> 8, fg & 0xFF))
        bg_row = bytes((bgc >> 8, bgc & 0xFF)) * w
        row = bytearray(w * 2)
        for yy in range(h):
            row[:] = bg_row
            for sx, run in th.mask_row_spans(data, w, yy):
                row[2 * sx:2 * (sx + run)] = fg_pat * run
            self.display.block(x, y + yy, x + w - 1, y + yy, row)

    def hline(self, x, y, w, color):
        # draw_line's endpoint is INCLUSIVE, so a w-pixel-wide line ends at
        # x + w - 1, not x + w. Passing x + w made every full-width call
        # (screens.py's header separator, drawn on every screen) fail
        # ili9341's is_off_grid check at x=320 on a 320px panel -- the driver
        # then skips the draw entirely and prints an error, so the line was
        # silently missing on real hardware. Invisible to the PIL-backed sim
        # and to CPython tests, which clip out-of-range coordinates instead.
        if self.FLIP_180:
            x = self.width - x - w
            y = self.height - 1 - y
        self.display.draw_line(x, y, x + w - 1, y, color565(*color))

    def text(self, x, y, s, color, bg=None, scale=1, font=None):
        # Defense in depth: the vendored draw_text8x8's own bounds check
        # (drivers/ili9341.py's is_off_grid call) only validates a fixed 8x8
        # box at (x, y) -- it ignores the actual rendered width of `s`. A
        # string that runs past the right edge isn't safely clipped, it
        # issues an out-of-range SET_COLUMN command straight to the panel
        # (undefined/vendor-dependent behavior, not a safe no-op). Every
        # call site is expected to fit on-screen already; this clamp exists
        # so a future one that doesn't fails safely (truncated text) instead
        # of risking display corruption.
        #
        # font=None is the built-in framebuf 8x8 (times `scale`); a Font
        # object (ui/fonts) selects a real bitmap face, rendered through the
        # vendored driver's own draw_text()/draw_letter() path with
        # Font.get_letter() supplying RGB565 cells. spacing=0 because Spleen
        # cells carry their own side bearings -- adding inter-letter pixels
        # would break the fixed-cell width every layout computes with.
        if font is not None:
            cw, chh = font.width, font.height
            max_chars = max(0, (self.width - x) // cw)
            s = s[:max_chars]
            if not s:
                return
            # same reason as the scaled path below: get_letter()'s cells go
            # out via block(), which performs no bounds check of its own
            if y < 0 or y + chh > self.height:
                return
            fg = color565(*color)
            bgc = color565(*bg) if bg else 0
            if self.FLIP_180:
                fx = self.width - x - cw * len(s)
                fy = self.height - y - chh
                self.display.draw_text(fx, fy, s, font, fg, background=bgc,
                                       rotate_180=True, spacing=0)
                return
            self.display.draw_text(x, y, s, font, fg, background=bgc,
                                   spacing=0)
            return
        max_chars = max(0, (self.width - x) // (th.CHAR_W * scale))
        s = s[:max_chars]
        if not s:
            return
        fg = color565(*color)
        bgc = color565(*bg) if bg else 0
        if scale == 1:
            if self.FLIP_180:
                # the string occupies CHAR_W*len(s) x CHAR_H from (x, y), so
                # its flipped top-left is measured from the far corner
                fx = self.width - x - th.CHAR_W * len(s)
                fy = self.height - y - th.CHAR_H
                self.display.draw_text8x8(fx, fy, s, fg, background=bgc,
                                          rotate=180)
                return
            self.display.draw_text8x8(x, y, s, fg, background=bgc)
            return
        # The scaled path writes via display.block(), which -- unlike
        # draw_sprite() -- performs NO bounds check and blindly sends whatever
        # it is given, so guard the vertical extent here. The horizontal
        # extent is already covered by the max_chars clamp above.
        if y < 0 or y + th.CHAR_H * scale > self.height:
            return
        self._text_scaled(x, y, s, fg, bgc, scale)

    def _text_scaled(self, x, y, s, fg, bgc, scale):
        """Nearest-neighbour pixel-double the built-in 8x8 font.

        Renders the whole string once at 1x into a framebuffer, then emits it
        `scale` source-rows at a time. Going row-wise rather than building the
        full scaled bitmap keeps peak allocation at a few hundred bytes per
        row instead of ~10KB for a full-width 2x line, which matters a lot
        given how little heap is left on this board.
        """
        n = len(s)
        w1 = th.CHAR_W * n              # source width in px
        need_src = w1 * th.CHAR_H * 2   # RGB565
        dw = w1 * scale                 # destination width in px
        need_row = dw * 2 * scale       # `scale` identical destination rows
        if self._src is None or len(self._src) < need_src:
            self._src = bytearray(need_src)
        if self._row is None or len(self._row) < need_row:
            self._row = bytearray(need_row)
        src, row = self._src, self._row

        fb = FrameBuffer(memoryview(src)[:need_src], w1, th.CHAR_H, RGB565)
        fb.fill(_swap565(bgc))
        fb.text(s, 0, 0, _swap565(fg))

        # 180-degree rotation is applied here rather than by rotating a
        # finished bitmap: read the source rows bottom-to-top and each row
        # right-to-left, and place the block measured from the far corner.
        # Same cost as the unflipped path, just reversed iteration.
        flip = self.FLIP_180
        ox = self.width - x - dw if flip else x
        oy = self.height - y - th.CHAR_H * scale if flip else y

        single = dw * 2  # bytes in one destination row
        for sy in range(th.CHAR_H):
            src_y = (th.CHAR_H - 1 - sy) if flip else sy
            base = src_y * w1 * 2
            o = 0
            rng = range(w1 - 1, -1, -1) if flip else range(w1)
            for sx in rng:
                hi = src[base + 2 * sx]
                lo = src[base + 2 * sx + 1]
                for _ in range(scale):
                    row[o] = hi
                    row[o + 1] = lo
                    o += 2
            # each source row becomes `scale` identical destination rows
            for r in range(1, scale):
                row[single * r:single * (r + 1)] = row[0:single]
            yy = oy + sy * scale
            # exact-size slice: block() writes every byte handed to it, and
            # `row` may be over-allocated from an earlier, longer string
            self.display.block(ox, yy, ox + dw - 1, yy + scale - 1,
                               memoryview(row)[:need_row])

    def present(self):
        pass  # ili9341 driver writes to the panel immediately; nothing to flush

    def _to_screen(self, point):
        if point is not None and self.FLIP_180:
            # The XPT2046 is a separate controller and knows nothing about how
            # this class draws; its calibration maps raw samples into the
            # panel's own frame. If every primitive above renders 180 degrees
            # rotated, a tap must be rotated the same way or it lands
            # point-symmetric about screen centre -- the top-left button
            # reported as bottom-right.
            return (self.width - 1 - point[0], self.height - 1 - point[1])
        return point

    def get_press(self):
        """Point of a stable touch, returned WHILE THE FINGER IS STILL DOWN.

        Unlike get_tap(), this does not wait for release, so a screen can
        respond during the press -- highlighting what is under the finger, or
        running a hold-to-confirm gesture. The caller is then responsible for
        wait_release() before accepting another press, or a resting finger
        re-triggers the same control.
        """
        return self._to_screen(self.touch.get_touch())

    def touch_active(self):
        """Is the panel being touched right now? Single unfiltered sample, no
        confidence averaging -- this is for 'is the finger still down', which
        needs to be cheap enough to poll inside an animation loop."""
        return self.touch.raw_touch() is not None

    # How long to wait for a finger to lift before giving up, and how many
    # consecutive quiet reads count as released.
    RELEASE_TIMEOUT_MS = 3000
    RELEASE_QUIET_READS = 3

    def wait_release(self):
        """Wait for the finger to lift. Bounded, and not a hot loop.

        This was `while self.touch.raw_touch() is not None: pass`, which is an
        unbounded busy-wait with no timeout and no sleep, and it is how this
        device froze. A resistive panel under a dragging or resting finger,
        electrical noise, or a glitch on the bit-banged touch bus can keep
        raw_touch() returning a point indefinitely. Nothing raises, so the
        crash handler never fires and the panel simply holds its last frame:
        indistinguishable from a working device, which is the failure mode
        this project keeps having to design against.

        Three changes, each fixing part of it:

        * a timeout, because no touch is worth wedging the device over. If it
          expires the app continues; the worst case is one extra press being
          absorbed, which is recoverable, unlike a freeze.
        * a sleep, because the old loop hammered a SoftSPI bus that is
          bit-banged in Python. That is both wasted CPU and extra traffic on
          the bus most likely to be glitching in the first place.
        * consecutive quiet reads, because a single spurious "released" on a
          noisy panel would end the wait early and let one physical touch fire
          two actions. Debouncing the release matters as much as debouncing
          the press.
        """
        quiet = 0
        waited = 0
        while waited < self.RELEASE_TIMEOUT_MS:
            if self.touch.raw_touch() is None:
                quiet += 1
                if quiet >= self.RELEASE_QUIET_READS:
                    return True
            else:
                quiet = 0
            _sleep_ms(10)
            waited += 10
        return False   # timed out: stuck or very slow release, carry on anyway

    def get_tap(self):
        """Stable touch, reported only once the finger lifts, so one physical
        tap always produces exactly one logical tap however long it is held."""
        point = self.get_press()
        if point is not None:
            self.wait_release()
        return point
